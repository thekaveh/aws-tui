"""Top-level Textual application — composes RootVM + chrome + content host.

This is the real composition that replaces the M0 hello-world placeholder.
The actual layer wiring lives in :mod:`aws_tui.composition` so this module
stays focused on the Textual side (compose, mounting, action handlers).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import sys
from collections import deque
from datetime import UTC, datetime
from typing import ClassVar

from textual.app import App, ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Container, Horizontal
from textual.widgets import Static

from aws_tui.composition import AppContext, build_app_context
from aws_tui.infra.connection_resolver import Connection
from aws_tui.infra.crash_dump import CrashDump
from aws_tui.ui.actions import ActionRegistry
from aws_tui.ui.bindings import BindingResolver
from aws_tui.ui.widgets.brand_banner import BrandBanner
from aws_tui.ui.widgets.confirm_modal import ConfirmModal
from aws_tui.ui.widgets.crash_modal import CrashModal
from aws_tui.ui.widgets.dual_pane import DualPane
from aws_tui.ui.widgets.help_modal import HelpModal
from aws_tui.ui.widgets.hint_legend import HintLegend
from aws_tui.ui.widgets.services_hamburger import ServicesHamburger
from aws_tui.ui.widgets.services_menu import (
    ServicesMenu,
)
from aws_tui.ui.widgets.theme_picker_modal import ThemePickerModal
from aws_tui.ui.widgets.toast import ToastStack
from aws_tui.ui.widgets.transfers_overlay import TransfersOverlay
from aws_tui.version import __version__
from aws_tui.vm.chrome.confirm_vm import ConfirmPath, ConfirmRequest
from aws_tui.vm.chrome.crash_vm import CrashChoice, CrashReport, CrashVM
from aws_tui.vm.chrome.theme_picker_vm import ThemePickerVM
from aws_tui.vm.chrome.toast_vm import ToastLevel, ToastModel
from aws_tui.vm.messages import ThemeChangedMessage

_ACTION_RING_SIZE = 100


def _build_swap_candidates(
    ctx: AppContext,
) -> tuple[list[tuple[str, str | Connection]], list[str]]:
    """Build the (label, payload) ring for ``action_swap_source``,
    filtering out connections in ``ctx.unreachable_connections``.

    Returns ``(candidates, skipped_names)`` where ``skipped_names`` is
    the list of TOML section names / profile names that were filtered
    out (used by ``_raise_skip_toast`` to inform the user).
    """
    from aws_tui.services.s3.service import _format_pane_title

    candidates: list[tuple[str, str | Connection]] = [("local", "local")]
    skipped: list[str] = []
    for conn in ctx.connection_resolver.list():
        if (conn.kind, conn.name) in ctx.unreachable_connections:
            skipped.append(conn.name)
            continue
        candidates.append((_format_pane_title(conn), conn))
    return candidates, skipped


def _raise_skip_toast(ctx: AppContext, skipped: list[str]) -> None:
    """Raise a one-line INFO toast naming the skipped connections.

    No-op if ``skipped`` is empty.
    """
    if not skipped:
        return
    text = f"Skipped unreachable: {', '.join(skipped)}"
    toast_id = f"swap-skip-{','.join(skipped)}"
    ctx.root_vm.chrome.toast_stack.raise_toast(
        ToastModel(
            id=toast_id,
            text=text,
            level=ToastLevel.INFO,
            sticky=False,
            timeout_seconds=3.0,
            action_label=None,
            action_action=None,
        )
    )


def _join_path(base: str, name: str) -> str:
    """Append ``name`` to ``base`` with a single ``/`` separator. Used
    only by the copy confirm modal to surface source/destination paths."""
    if not base:
        return name
    if base.endswith("/"):
        return f"{base}{name}"
    return f"{base}/{name}"


class AwsTuiApp(App[None]):
    """The aws-tui Textual application.

    Composition root, real version. Constructor accepts an optional
    :class:`AppContext` so tests / E2E journeys can inject pre-wired
    state instead of touching ``~/.config/aws-tui``.
    """

    TITLE = "aws-tui"
    SUB_TITLE = f"v{__version__}"

    # Declare the notifications layer so ToastStack floats above the
    # main layout instead of consuming flow space.
    CSS = """
    Screen {
        layers: base notifications;
    }
    """

    # Minimum-viable input router (input-router-deferred from M6). The
    # `[keybindings]` overlay in config.toml is parsed by `KeymapStore` but
    # the action→Textual handler indirection isn't wired yet; until it is,
    # the bindings here drive the most essential navigation actions
    # directly. Spec §4.2 documents the full set.
    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("q", "quit", "Quit", show=True),
        Binding("ctrl+c", "quit", "Quit", show=False),
        # priority=True puts these *ahead* of Textual's Screen-level defaults
        # for focus rotation (Tab/Shift+Tab/arrows). Without priority, Screen
        # consumes the key for its built-in focus traversal before the App
        # handler ever fires — that's the "Tab does nothing" symptom.
        Binding("tab", "switch_focus", "Switch pane", show=True, priority=True),
        Binding("shift+tab", "switch_focus", "Switch pane", show=False, priority=True),
        Binding("up,k", "move_up", "↑", show=True, priority=True),
        Binding("down,j", "move_down", "↓", show=True, priority=True),
        Binding("enter", "descend", "Open", show=True, priority=True),
        Binding("backspace", "ascend", "Up", show=True, priority=True),
        Binding("left", "modal_left_or_ascend", "←", show=False, priority=True),
        Binding("right", "modal_right", "→", show=False, priority=True),
        Binding("r", "refresh", "Refresh", show=True, priority=True),
        Binding("question_mark", "help", "Help", show=True, priority=True),
        Binding("colon", "help", "Cmd", show=True, priority=True),
        Binding("t", "themes", "Themes", show=True, priority=True),
        Binding("T", "cycle_theme", "Cycle theme", show=True, priority=True),
        Binding("c", "copy", "Copy", show=True, priority=True),
        Binding("d", "delete", "Delete", show=True, priority=True),
        Binding("m", "toggle_services", "Menu", show=True, priority=True),
        Binding("S", "swap_source", "Swap source", show=True, priority=True),
        Binding("shift+up", "mark_up", "Mark ↑", show=False, priority=True),
        Binding("shift+down", "mark_down", "Mark ↓", show=False, priority=True),
    ]

    def __init__(self, context: AppContext | None = None) -> None:
        super().__init__()
        self._app_ctx = context if context is not None else build_app_context()
        # Theme CSS is injected as the App's stylesheet (see on_mount).
        self._actions = ActionRegistry()
        self._resolver = BindingResolver(
            keymap=self._app_ctx.keymap_store,
            actions=self._actions,
        )
        # Register handlers for the action ids the BindingResolver advertises.
        self._actions.register("app.quit", self._handle_quit)
        # Action ring buffer feeds the crash dump per spec §7.10. Each entry
        # is a short ISO-timestamped action id string; we keep the most
        # recent ``_ACTION_RING_SIZE`` to bound memory.
        self._action_ring: deque[str] = deque(maxlen=_ACTION_RING_SIZE)
        self._last_action_id: str | None = None
        # Populated by ``_handle_exception`` when Textual surfaces an
        # unhandled exception so ``main()`` can print the dump path and
        # re-raise after the app has torn down.
        self._crash_report: CrashReport | None = None

    @property
    def app_ctx(self) -> AppContext:
        return self._app_ctx

    def compose(self) -> ComposeResult:
        # StatusBar is no longer mounted — profile/region/auth indicator
        # now live in the left pane's border (title shows the live path,
        # subtitle shows the connection identity). Bookkeeping VMs still
        # exist in RootVM.chrome so hub subscribers stay wired up; only
        # the widget is dropped.
        ctx = self._app_ctx
        yield BrandBanner(
            theme_name=ctx.initial_theme,
            hub=ctx.hub,
            id="brand-banner",
        )
        with Horizontal(id="main-area"):
            yield ServicesHamburger(id="services-hamburger")
            yield ServicesMenu(ctx.root_vm.services_menu, hub=ctx.hub, id="services-menu")
            yield Container(id="content-host")
        yield HintLegend(ctx.root_vm.chrome.hint_legend, hub=ctx.hub, id="hint-legend")
        yield ToastStack(ctx.root_vm.chrome.toast_stack, hub=ctx.hub, id="toast-stack")
        yield TransfersOverlay(ctx.transfers_vm, hub=ctx.hub, id="transfers-overlay")

    async def on_mount(self) -> None:
        ctx = self._app_ctx
        # Construct the VM tree.
        ctx.root_vm.construct()
        ctx.transfers_vm.construct()
        ctx.confirm_vm.construct()
        ctx.quick_look_vm.construct()
        ctx.command_palette_vm.construct()

        self._apply_initial_theme()

        initial_conn = self._resolve_initial_connection()
        if initial_conn is not None:
            auth_state = ctx.aws_session.probe_token(initial_conn).state
            await ctx.root_vm.switch_connection_with(initial_conn, auth_state)
            with contextlib.suppress(Exception):
                await ctx.root_vm.switch_service("s3")
            self._mount_initial_service_view()
        else:
            self._mount_no_connection_placeholder()

    # ── on_mount helpers ───────────────────────────────────────────────────

    def _apply_initial_theme(self) -> None:
        """Layer the active theme `.tcss` on top of Textual's defaults."""
        ctx = self._app_ctx
        try:
            theme_css = ctx.theme_store.load(ctx.initial_theme)
            self.stylesheet.add_source(theme_css)
            self.stylesheet.parse()
            self.stylesheet.update(self)
        except Exception:
            ctx.log_sink.error("theme.load.failed", name=ctx.initial_theme)

    def _resolve_initial_connection(self) -> Connection | None:
        """Pick the initial connection in this order:

        1. ``[defaults].connection`` from config.toml, if it resolves.
        2. ``$AWS_PROFILE`` if exported — matches the AWS CLI's resolution
           order so the TUI lands on the same identity a user gets from
           ``aws s3 ls`` in the same shell. This is the SSO-recovery path
           for users whose ``[default]`` profile has no creds but whose
           working profile is the env var.
        3. The first auto-discovered profile (legacy fallback).
        4. ``None`` — the no-connection placeholder branch.
        """
        ctx = self._app_ctx
        try:
            cfg = ctx.config_store.load()
        except Exception:
            cfg = None
        connections = ctx.connection_resolver.list()
        initial_conn = None
        if cfg is not None and cfg.defaults.connection:
            initial_conn = next(
                (c for c in connections if c.name == cfg.defaults.connection),
                None,
            )
        if initial_conn is None:
            env_profile = (os.environ.get("AWS_PROFILE") or "").strip()
            if env_profile:
                initial_conn = next(
                    (c for c in connections if c.profile == env_profile),
                    None,
                )
        if initial_conn is None and connections:
            initial_conn = connections[0]
        return initial_conn

    def _mount_initial_service_view(self) -> None:
        """Mount the current service's view widget into the content host.

        ``switch_service`` updates the VM tree; the View layer has to follow
        explicitly — Textual won't infer that from VMx state.
        """
        ctx = self._app_ctx
        try:
            current_vm = ctx.root_vm.content_host.current
            if current_vm is not None:
                host = self.query_one("#content-host", Container)
                host.remove_children()
                host.mount(DualPane(current_vm, hub=ctx.hub, id="content-dual-pane"))
        except Exception:
            ctx.log_sink.error("app.mount_service_view.failed", service_id="s3")

    def _mount_no_connection_placeholder(self) -> None:
        """Render a clear "configure one and relaunch" message when no
        AWS / S3-compatible connection resolves at startup.
        """
        with contextlib.suppress(Exception):
            host = self.query_one("#content-host", Container)
            host.mount(
                Static(
                    "\n  No AWS profile or S3-compatible connection found.\n\n"
                    "  To get started, do ONE of the following and relaunch:\n\n"
                    "    1. Run [b]aws configure[/]                      (interactive AWS keys setup)\n"
                    "    2. Run [b]aws configure sso[/]                  (interactive SSO setup)\n"
                    "    3. Edit [b]~/.config/aws-tui/config.toml[/]     (add an AWS or S3-compatible connection)\n\n"
                    "  See [b]docs/connections.md[/] in the repo for the [b][connections.<name>][/] schema and\n"
                    "  vendor quirks (MinIO, R2, B2, Wasabi).\n\n"
                    "  Press [b]q[/] to quit.",
                    id="content-placeholder",
                    classes="content-placeholder",
                    markup=True,
                )
            )

    # ── Action handlers ────────────────────────────────────────────────────

    async def action_app_quit(self) -> None:
        await self._aws_tui_shutdown()
        self.exit()

    def _handle_quit(self) -> None:
        self.exit()

    def _dual_pane(self) -> object | None:
        """Return the currently-hosted ``DualPaneVM`` (or None)."""
        return self._app_ctx.root_vm.content_host.current

    def action_switch_focus(self) -> None:
        dual = self._dual_pane()
        if dual is None:
            return
        cmd = getattr(dual, "switch_focus_command", None)
        if cmd is not None:
            cmd.execute()

    def _forward_to_modal(self, *action_names: str) -> bool:
        """When a modal is active, try each ``action_name`` on the active
        screen and run the first that exists. Used to work around
        Textual dispatching App-level priority bindings BEFORE modal
        ones — without forwarding, things like ↑/↓/Enter in our modals
        would never reach the modal's own handlers."""
        if len(self.screen_stack) <= 1:
            return False
        for name in action_names:
            forward = getattr(self.screen, name, None)
            if forward is not None:
                forward()
                return True
        return False

    def action_move_up(self) -> None:
        if self._forward_to_modal("action_move_up"):
            return
        self._move_cursor(-1)

    def action_move_down(self) -> None:
        if self._forward_to_modal("action_move_down"):
            return
        self._move_cursor(1)

    def _move_cursor(self, delta: int) -> None:
        dual = self._dual_pane()
        if dual is None:
            return
        pane = getattr(dual, "focused_pane", None)
        if pane is None:
            return
        cmd = getattr(pane, "move_cursor_command", None)
        if cmd is not None:
            cmd.execute(delta)

    async def action_descend(self) -> None:
        # Forward Enter to the active modal first. Most of our modals
        # treat Enter as confirm/apply (ConfirmModal.action_confirm,
        # ThemePickerModal.action_apply). Without this, App's
        # priority=True enter binding always wins and Enter never
        # reaches the modal's handler. ``commit_focused`` is the
        # confirm-modal handler that commits whichever button has
        # arrow-key focus — checked first so it wins over the plain
        # ``confirm`` fallback.
        if self._forward_to_modal("action_commit_focused", "action_confirm", "action_apply"):
            return
        dual = self._dual_pane()
        if dual is None:
            return
        pane = getattr(dual, "focused_pane", None)
        if pane is None:
            return
        target = pane.selected_entry
        if target is None:
            return
        # ".." synthetic entry — ascend to parent.
        if target.entry.name == "..":
            if not pane.path.is_root:
                await pane.navigate_to(pane.path.parent())
            return
        # Descend only into directories; files trigger Quick Look later.
        if str(target.entry.kind) == "directory":
            await pane.navigate_to(pane.path.join(target.entry.name))

    async def action_ascend(self) -> None:
        # Forward Backspace to the active modal as a cancel-by-key
        # gesture (esc still works too).
        if self._forward_to_modal("action_cancel", "action_close", "action_dismiss"):
            return
        dual = self._dual_pane()
        if dual is None:
            return
        pane = getattr(dual, "focused_pane", None)
        if pane is None or pane.path.is_root:
            return
        await pane.navigate_to(pane.path.parent())

    async def action_modal_left_or_ascend(self) -> None:
        # In a modal: Left moves arrow-key focus to the previous footer
        # button (or whatever the modal exposes as ``action_focus_prev``).
        # Outside any modal: behaves like ``ascend`` so file-pane
        # navigation is unchanged.
        if self._forward_to_modal("action_focus_prev"):
            return
        await self.action_ascend()

    def action_modal_right(self) -> None:
        # In a modal: Right moves arrow-key focus to the next footer
        # button. Outside any modal: no-op (panes don't currently bind
        # Right to anything).
        self._forward_to_modal("action_focus_next")

    async def action_refresh(self) -> None:
        dual = self._dual_pane()
        if dual is None:
            return
        pane = getattr(dual, "focused_pane", None)
        if pane is not None:
            await pane.refresh()

    async def action_help(self) -> None:
        """Show the help overlay (also bound to ``:``). The theme picker
        is a separate modal — press ``t`` (or use the help modal's
        Themes link)."""
        await self.push_screen(HelpModal())

    async def action_copy(self) -> None:
        """Copy the focused pane's marked entries (or the cursor row if
        none are marked) into the *other* pane. Pops a confirm modal
        showing source → destination paths first; only proceeds on
        explicit confirm."""
        dual = self._dual_pane()
        if dual is None:
            return
        src_pane = getattr(dual, "focused_pane", None)
        dst_pane = getattr(dual, "other_pane", None)
        if src_pane is None or dst_pane is None:
            return

        targets = list(src_pane.marked_entries)
        # Fall back to the cursor row if nothing is multi-selected.
        used_cursor_fallback = not targets
        if used_cursor_fallback:
            selected = src_pane.selected_entry
            if selected is not None and not selected.is_parent_link:
                targets = [selected]
        if not targets:
            return

        src_base = src_pane.viewmodel.border_title
        dst_base = dst_pane.viewmodel.border_title
        names_preview = (
            targets[0].entry.name
            if len(targets) == 1
            else f"{len(targets)} items ({targets[0].entry.name}, …)"
        )
        items_summary = "1 item" if len(targets) == 1 else f"{len(targets)} items"
        request = ConfirmRequest(
            title=f"Copy {items_summary}?",
            paths=(
                ConfirmPath(label="From", path=_join_path(src_base, names_preview)),
                ConfirmPath(label="To", path=_join_path(dst_base, names_preview)),
            ),
            confirm_label="Copy",
            cancel_label="Cancel",
        )

        ctx = self._app_ctx
        modal = ConfirmModal(ctx.confirm_vm, request, hub=ctx.hub)

        # Why ``push_screen`` with a callback instead of
        # ``push_screen_wait``: the latter requires a Textual worker
        # context, which actions invoked through bindings don't have —
        # calling it raised ``NoActiveWorker`` and popped the crash
        # modal. Schedule the actual copy as a worker after the user
        # decides.
        def _after_decision(decision: bool | None) -> None:
            if not decision:
                return
            self.run_worker(
                self._run_copy(dual, list(targets), used_cursor_fallback),
                exclusive=False,
            )

        self.push_screen(modal, _after_decision)

    async def _run_copy(
        self,
        dual: object,
        targets: list[object],
        used_cursor_fallback: bool,
    ) -> None:
        """Run ``DualPaneVM.copy_across`` from a worker. Errors are
        toasted, never re-raised."""
        ctx = self._app_ctx
        copy_across = getattr(dual, "copy_across", None)
        if copy_across is None:
            return
        if used_cursor_fallback:
            for entry in targets:
                entry.set_marked(True)  # type: ignore[attr-defined]
        try:
            try:
                await copy_across()
            except Exception as exc:
                ctx.log_sink.error("copy.failed", error=str(exc))
                self.notify(f"Copy failed: {exc}", severity="error", timeout=8)
        finally:
            if used_cursor_fallback:
                for entry in targets:
                    entry.set_marked(False)  # type: ignore[attr-defined]

    async def action_delete(self) -> None:
        """Delete the focused pane's marked entries (or the cursor row if
        none are marked). Pops a danger-styled confirm modal first."""
        dual = self._dual_pane()
        if dual is None:
            return
        src_pane = getattr(dual, "focused_pane", None)
        if src_pane is None:
            return

        targets = list(src_pane.marked_entries)
        used_cursor_fallback = not targets
        if used_cursor_fallback:
            selected = src_pane.selected_entry
            if selected is not None and not selected.is_parent_link:
                targets = [selected]
        if not targets:
            return

        ctx = self._app_ctx
        base = src_pane.viewmodel.border_title
        names_preview = (
            targets[0].entry.name
            if len(targets) == 1
            else f"{len(targets)} items ({targets[0].entry.name}, …)"
        )
        items_summary = "1 item" if len(targets) == 1 else f"{len(targets)} items"
        request = ConfirmRequest(
            title=f"Delete {items_summary}?",
            paths=(ConfirmPath(label="Target", path=_join_path(base, names_preview)),),
            body_lines=("This cannot be undone.",),
            confirm_label="Delete",
            cancel_label="Cancel",
            danger=True,
        )
        modal = ConfirmModal(ctx.confirm_vm, request, hub=ctx.hub)

        # Same worker-deferral as action_copy — bindings don't run in a
        # worker, so push_screen_wait would raise NoActiveWorker and
        # crash the app. Push, then kick off the delete in a worker.
        def _after_decision(decision: bool | None) -> None:
            if not decision:
                return
            self.run_worker(
                self._run_delete(dual, list(targets), used_cursor_fallback),
                exclusive=False,
            )

        self.push_screen(modal, _after_decision)

    async def _run_delete(
        self,
        dual: object,
        targets: list[object],
        used_cursor_fallback: bool,
    ) -> None:
        """Mirror of :meth:`_run_copy` for the delete path."""
        ctx = self._app_ctx
        delete_in_focused = getattr(dual, "delete_in_focused", None)
        if delete_in_focused is None:
            return
        if used_cursor_fallback:
            for entry in targets:
                entry.set_marked(True)  # type: ignore[attr-defined]
        try:
            try:
                await delete_in_focused()
            except Exception as exc:
                ctx.log_sink.error("delete.failed", error=str(exc))
                self.notify(f"Delete failed: {exc}", severity="error", timeout=8)
        finally:
            if used_cursor_fallback:
                for entry in targets:
                    entry.set_marked(False)  # type: ignore[attr-defined]

    def action_toggle_services(self) -> None:
        """Collapse/expand the left services rail."""
        for menu in self.query(ServicesMenu):
            menu.toggle_collapsed()

    def action_cycle_theme(self) -> None:
        """Cycle to the next theme without opening the picker modal —
        bound to ``Shift+T`` so the footer chip is reachable too.

        Uses the canonical :class:`ThemePickerVM` (same VMx model the
        modal flow uses) to determine the next theme, then raises a
        top-right toast through :class:`ToastStackVM` so the
        notification overlay layer (not Textual's built-in bottom-
        center notify) handles placement + theme conformance.
        """
        ctx = self._app_ctx
        picker = ThemePickerVM(
            themes=ctx.theme_store.BUILTIN_NAMES,
            active_theme=ctx.initial_theme,
            on_pick=self.switch_theme,
            on_preview=self.switch_theme,
            hub=ctx.hub,
            dispatcher=ctx.dispatcher,
        )
        picker.construct()
        try:
            nxt = picker.next_theme()
            picker.pick_theme_command.execute(nxt)
        finally:
            self.call_after_refresh(picker.dispose)
        self._raise_theme_changed_toast(nxt)

    def action_mark_up(self) -> None:
        self._extend_selection(-1)

    def action_mark_down(self) -> None:
        self._extend_selection(1)

    def _extend_selection(self, delta: int) -> None:
        """Shift+arrow handler: TOGGLE the row we are leaving, then move.

        Rule (from the user — see PR comments): the only row whose
        mark changes is the row the cursor is *moving away from*. The
        target row is never touched, and we never modify both rows in
        the same press. This gives clean, predictable semantics:

        - Walking down through an unmarked range with Shift+Down marks
          each row as you leave it.
        - Walking back up through a marked range with Shift+Up unmarks
          each row as you leave it.
        - On a row whose mark you want flipped, point at it and press
          Shift+Arrow — the row toggles, cursor moves on.

        Cursor still moves even when the move would land out of range
        of the entries list (handled by ``move_cursor_command``'s own
        clamp), but the toggle only happens when ``cur`` is a real
        row (always true here since we got it from ``cursor_index``)."""
        dual = self._dual_pane()
        if dual is None:
            return
        pane = getattr(dual, "focused_pane", None)
        if pane is None:
            return
        cur = pane.cursor_index
        entries = pane.filtered_entries
        if not (0 <= cur < len(entries)):
            return
        mark = getattr(pane, "mark_at", None)
        if mark is None:
            return
        mark(cur, marked=not entries[cur].is_marked)
        move = getattr(pane, "move_cursor_command", None)
        if move is not None:
            move.execute(delta)

    async def action_swap_source(self) -> None:
        """Cycle the focused pane through every available source.

        The cycle is built from ``ConnectionResolver.list()`` (TOML +
        auto-discovered AWS profiles) plus the local filesystem. Each
        press of ``Shift+S`` advances to the next candidate, wrapping
        at the end, so the user can spin through ``aws s3 · default ·
        us-east-1`` → ``s3-compatible · minio-local · localhost:64093``
        → ``local`` → ``aws s3 · default · …`` without opening the
        connection picker.

        The current position is found by matching the focused pane's
        identity label against each candidate's computed label."""
        ctx = self._app_ctx
        dual = self._dual_pane()
        if dual is None:
            return
        focused = getattr(dual, "focused_pane", None)
        if focused is None:
            return
        try:
            from aws_tui.domain.local_fs import LocalFS
            from aws_tui.domain.s3_fs import S3FS
            from aws_tui.services.s3.service import _aioboto3_session_for
        except Exception:
            return

        _LOCAL_LABEL = "local"
        candidates, skipped = _build_swap_candidates(ctx)
        _raise_skip_toast(ctx, skipped)
        if len(candidates) <= 1:
            # Only local — either no connections configured, or every
            # configured connection has been observed unreachable.
            if skipped:
                self.notify(
                    "All connections unreachable — staying on local.",
                    severity="warning",
                )
            else:
                self.notify(
                    "No connections configured — can't swap source.",
                    severity="warning",
                )
            return

        current_label = focused.identity_label or _LOCAL_LABEL
        try:
            idx = next(i for i, (label, _) in enumerate(candidates) if label == current_label)
        except StopIteration:
            idx = -1  # current label unknown → start of ring on next++
        next_label, payload = candidates[(idx + 1) % len(candidates)]

        new_provider: object
        new_protocol: str
        if payload == "local":
            new_provider = LocalFS()
            new_protocol = ""
        else:
            assert not isinstance(payload, str)  # narrows payload to Connection
            conn = payload
            session = _aioboto3_session_for(conn)
            new_provider = S3FS(
                session=session,
                bucket=None,
                endpoint_url=conn.endpoint_url,
                force_path_style=conn.force_path_style,
            )
            new_protocol = "s3:"

        swap = getattr(focused, "swap_provider", None)
        if swap is None:
            return
        ctx.log_sink.info("pane.swap_source", to=next_label)
        await swap(new_provider, identity_label=next_label, path_protocol=new_protocol)

    async def action_themes(self) -> None:
        """Open the keyboard-navigable theme picker modal."""
        ctx = self._app_ctx

        def _pick_with_toast(name: str) -> None:
            self.switch_theme(name)
            self._raise_theme_changed_toast(name)

        picker = ThemePickerVM(
            themes=ctx.theme_store.BUILTIN_NAMES,
            active_theme=ctx.initial_theme,
            on_pick=_pick_with_toast,
            on_preview=self.switch_theme,
            hub=ctx.hub,
            dispatcher=ctx.dispatcher,
        )
        picker.construct()
        modal = ThemePickerModal(picker=picker, hub=ctx.hub)
        try:
            await self.push_screen(modal)
        finally:
            self.call_after_refresh(picker.dispose)

    def _raise_theme_changed_toast(self, theme_name: str) -> None:
        """Raise a top-right, theme-conformant toast announcing the
        switch. Routed through :class:`ToastStackVM` (notifications
        overlay layer) rather than ``self.notify()`` (Textual's
        built-in bottom-center notify, which wrecks the footer)."""
        ctx = self._app_ctx
        ctx.root_vm.chrome.toast_stack.raise_toast(
            ToastModel(
                id=f"theme-changed-{theme_name}",
                text=f"Theme changed to: [{theme_name}]",
                level=ToastLevel.INFO,
                sticky=False,
                timeout_seconds=2.0,
                action_label=None,
                action_action=None,
            )
        )

    # Stable read_from key for the aws-tui theme source — re-using it on
    # every ``add_source`` call means subsequent theme swaps REPLACE the
    # source instead of stacking (the old code accumulated one source
    # per swap, which is wasteful and can leak cached rules).
    _THEME_SOURCE_KEY: ClassVar[tuple[str, str]] = ("aws_tui", "active-theme.tcss")

    def switch_theme(self, name: str) -> None:
        """Runtime theme swap.

        Mirrors Textual's own ``_watch_theme`` flow:

        1. Replace the theme tcss source via a stable ``read_from`` key so
           sources don't accumulate.
        2. Call ``refresh_css(animate=False)`` — that one call re-parses
           the stylesheet, re-resolves variables, and applies styles to
           every screen in the stack (current + background). It's the
           same API Textual uses internally for its theme reactive.
        3. Publish a ThemeChangedMessage on the hub so VMx-bound widgets
           that bake colors into Python (BrandBanner) can swap their
           per-theme palette without us reaching in by widget type.
        """
        ctx = self._app_ctx
        try:
            theme_css = ctx.theme_store.load(name)
        except Exception:
            ctx.log_sink.error("theme.load.failed", name=name)
            return

        # 1. Replace, don't accumulate.
        self.stylesheet.add_source(theme_css, read_from=self._THEME_SOURCE_KEY)

        # 2. Use Textual's own theme-refresh pipeline. This is the API
        # ``_watch_theme`` itself uses — it covers reparse, variable
        # re-resolution, and layout refresh across all mounted screens.
        self._invalidate_css()
        self.refresh_css(animate=False)

        ctx.initial_theme = name

        # 3. Broadcast for Python-side palettes (e.g. the banner).
        ctx.hub.send(ThemeChangedMessage(name=name))

    # ── Crash handling ─────────────────────────────────────────────────────

    def record_action(self, action_id: str) -> None:
        """Record an action id in the ring buffer and track it as the latest.

        Called by the input router / action invokers so the crash modal can
        decide whether ``continue`` is safe and the dump can include the
        last 100 user actions per spec §7.10.
        """
        ts = datetime.now(UTC).isoformat()
        self._action_ring.append(f"{ts} {action_id}")
        self._last_action_id = action_id

    @property
    def last_action_id(self) -> str | None:
        return self._last_action_id

    def _build_crash_report(self, exc: BaseException) -> CrashReport:
        """Write the dump and assemble the matching :class:`CrashReport`.

        Side effects: a new file under ``~/.cache/aws-tui/crash/`` and an
        ``ERROR``-level log line tagged ``crash.captured``. Always
        succeeds (falls back to a side-channel path if the write fails).
        """
        ctx = self._app_ctx
        dump = CrashDump(base_dir=ctx.log_sink.path.parent.parent / "crash")
        log_path = ctx.log_sink.path
        try:
            dump_path = dump.write(
                exc=exc,
                log_path=log_path,
                action_ring=list(self._action_ring),
            )
        except Exception:
            dump_path = log_path.parent / "crash-fallback.txt"
        last_id = self._last_action_id
        report = CrashReport(
            timestamp=datetime.now(UTC),
            exception_type=type(exc).__name__,
            exception_message=str(exc) or repr(exc),
            traceback_short=CrashDump.short_traceback(exc),
            dump_path=dump_path,
            can_continue=CrashReport.is_safe_to_continue(last_id),
            last_action_id=last_id,
        )
        with contextlib.suppress(Exception):
            logging.getLogger("aws_tui").error(
                "crash.captured",
                extra={
                    "json_fields": {
                        "exception_type": report.exception_type,
                        "dump_path": str(report.dump_path),
                        "last_action_id": report.last_action_id,
                    }
                },
            )
        return report

    def _handle_exception(self, error: Exception) -> None:
        """Override Textual's fatal handler to write a crash dump first.

        We still defer to the upstream behavior (which sets ``_return_code``
        and tears down) — the dump and report are the only thing we add
        before the app exits.
        """
        try:
            self._crash_report = self._build_crash_report(error)
        finally:
            super()._handle_exception(error)

    async def show_crash_modal(self, report: CrashReport) -> CrashChoice:
        """Push the crash modal for ``report`` and await the user's choice.

        Public so tests and recovery flows can drive the modal without
        also having to raise an exception. The in-app crash path
        (``_handle_exception``) does not currently call this — see the
        ``deferred-from-m6`` note on ``record_action``/crash-modal
        push_screen wiring.
        """
        ctx = self._app_ctx
        crash_vm = CrashVM(report, hub=ctx.hub, dispatcher=ctx.dispatcher)
        crash_vm.construct()
        try:
            ask_task = asyncio.create_task(crash_vm.ask())
            await self.push_screen(CrashModal(crash_vm, hub=ctx.hub))
            return await ask_task
        finally:
            crash_vm.dispose()

    @property
    def crash_report(self) -> CrashReport | None:
        """The last crash report captured via ``_handle_exception``."""
        return self._crash_report

    async def _aws_tui_shutdown(self) -> None:
        """Graceful shutdown per spec sec 5.4.

        Renamed away from ``_shutdown`` to avoid colliding with the
        internal ``App._shutdown`` lifecycle hook on Textual.
        """
        ctx = self._app_ctx
        with contextlib.suppress(Exception):
            ctx.transfers_vm.cancel_all_command.execute()
        with contextlib.suppress(Exception):
            await ctx.aws_session.aclose_all_clients()
        with contextlib.suppress(Exception):
            ctx.log_sink.flush()
            ctx.log_sink.close()
        with contextlib.suppress(Exception):
            ctx.command_palette_vm.dispose()
            ctx.quick_look_vm.dispose()
            ctx.confirm_vm.dispose()
            ctx.transfers_vm.dispose()
            ctx.root_vm.dispose()


def main() -> None:
    """Run the Textual app with unhandled-exception capture.

    Invoked by the ``aws-tui`` console script and ``python -m aws_tui``.
    If the app surfaces an unhandled exception, ``_handle_exception``
    writes a crash dump under ``~/.cache/aws-tui/crash/`` and the
    saved :class:`CrashReport` is printed here before the exception is
    re-raised so the user knows where the dump landed.
    """
    app = AwsTuiApp()
    try:
        app.run()
    except BaseException as exc:
        report = app.crash_report
        if report is None:
            report = app._build_crash_report(exc)
        # Print to stderr (after Textual has restored the terminal).
        print(
            "\naws-tui crashed.\n"
            f"  {report.exception_type}: {report.exception_message}\n"
            f"  dump: {report.dump_path}\n",
            file=sys.stderr,
        )
        raise
    else:
        # Normal exit; crash report would be set only if `_handle_exception`
        # fired and Textual swallowed the exception (it does this when
        # rendering a fatal panel).
        report = app.crash_report
        if report is not None:
            print(
                "\naws-tui crashed.\n"
                f"  {report.exception_type}: {report.exception_message}\n"
                f"  dump: {report.dump_path}\n",
                file=sys.stderr,
            )


if __name__ == "__main__":
    main()


__all__ = ["AwsTuiApp", "main"]
