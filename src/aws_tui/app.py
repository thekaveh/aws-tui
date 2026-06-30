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
from typing import TYPE_CHECKING, ClassVar

from reactivex.abc import DisposableBase

if TYPE_CHECKING:
    from aws_tui.vm.file_manager.pane_vm import PaneState

from textual.app import App, ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Container, Horizontal
from textual.widgets import OptionList, Static

from aws_tui.composition import AppContext, build_app_context
from aws_tui.infra.aws_session import TokenState
from aws_tui.infra.connection_resolver import Connection
from aws_tui.infra.crash_dump import CrashDump
from aws_tui.ui import notifications
from aws_tui.ui.actions import ActionRegistry
from aws_tui.ui.bindings import BindingResolver
from aws_tui.ui.widgets.brand_banner import BrandBanner
from aws_tui.ui.widgets.confirm_modal import ConfirmModal
from aws_tui.ui.widgets.crash_modal import CrashModal
from aws_tui.ui.widgets.dual_pane import DualPane
from aws_tui.ui.widgets.emr_serverless.page import EmrServerlessPage
from aws_tui.ui.widgets.help_modal import HelpModal
from aws_tui.ui.widgets.hint_legend import HintLegend
from aws_tui.ui.widgets.nav_menu import NavMenu
from aws_tui.ui.widgets.settings_view import SettingsView
from aws_tui.ui.widgets.theme_picker_modal import ThemePickerModal
from aws_tui.ui.widgets.toast import ToastStack
from aws_tui.ui.widgets.transfers_overlay import TransfersOverlay
from aws_tui.version import __version__
from aws_tui.vm.chrome.confirm_vm import ConfirmPath, ConfirmRequest
from aws_tui.vm.chrome.crash_vm import CrashChoice, CrashReport, CrashVM
from aws_tui.vm.chrome.theme_picker_vm import ThemePickerVM
from aws_tui.vm.messages import ConnectionListChangedMessage, ThemeChangedMessage
from aws_tui.vm.nav_menu_vm import SETTINGS_NAV_ID

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

    No-op if ``skipped`` is empty. The toast id is built from a SORTED
    join so it is stable regardless of the iteration order of
    ``ConnectionResolver.list()``.
    """
    if not skipped:
        return
    # Keep displayed text in original (config-file) order; sort only the id.
    notifications.advise(
        ctx.root_vm.chrome.toast_stack,
        subject="Source",
        message=f"skipped unreachable {', '.join(skipped)}",
        toast_id=f"swap-skip-{','.join(sorted(skipped))}",
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
    #
    # ``#main-area`` and ``#content-host`` need explicit ``1fr`` sizing
    # so the Horizontal layout allocates the remaining width to the
    # content host after the always-visible NavMenu takes its fixed
    # 10-cell width (post-PR-#94 / #97 — single mode, no
    # collapse/expand, sized to fit the longest 3-letter service
    # label + the ``▌`` ribbon + per-row padding + borders). Without
    # this, the DualPane mounted inside renders at zero width and the
    # user sees a blank screen at startup. The pre-#94 standalone
    # ServicesHamburger widget + the toggle/collapse modes were both
    # dropped in the always-visible nav rework.
    CSS = """
    Screen {
        /* ``dropdown`` is declared between ``base`` and
           ``notifications`` so the EMR application picker's
           OptionList renders as a true overlay above the page
           body (not embedded in the LEFT column's flow, which
           was the long-standing "There's no dropdown!" bug —
           prior comments in ``application_picker.py`` already
           identified Screen as the missing piece). ``notifications``
           stays the top layer so toasts overlay everything,
           including any open picker. */
        layers: base dropdown notifications;
    }
    #main-area {
        height: 1fr;
        width: 1fr;
        /* Match HintLegend's ``margin: 0 1 1 1`` and BrandBanner's
           (override below) horizontal margin so all three pieces of
           top-level chrome line up on the same x-axis. User
           feedback: "I want the top border pane … the bottom border
           pane … and all that is in between to have the similar
           length and margins from the left and right edges of the
           screen". */
        margin: 0 1;
    }
    BrandBanner {
        margin: 1 1 0 1;
    }
    #content-host {
        height: 1fr;
        width: 1fr;
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
        Binding("comma", "open_settings", "Settings", show=True, priority=True),
        Binding("c", "copy", "Copy", show=True, priority=True),
        Binding("d", "delete", "Delete", show=True, priority=True),
        # ``m`` (toggle nav rail expand/collapse) was dropped in the
        # nav-as-pane rework — the rail is always visible at a single
        # fixed width and shows TEXT labels (no icons), so there is
        # nothing to toggle.
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
        self._pane_state_sub: DisposableBase | None = None
        self._connection_list_sub: DisposableBase | None = None
        self._nav_selection_sub: DisposableBase | None = None
        self._cursor_sub: DisposableBase | None = None
        # True while ``on_mount`` is driving the initial service mount —
        # gates ``_on_nav_selection_changed`` so the seed selected_id
        # change doesn't spawn a duplicate mount worker that races the
        # on_mount direct mount (blank-screen-at-startup regression).
        self._boot_in_flight: bool = False
        # Set to the active connection key while ``_initial_mount_worker``
        # is waiting for the LEFT pane to reach a terminal state for
        # that connection. ``_on_pane_state_changed`` resolves
        # ``_attempt_future`` when an UNREACHABLE / AUTH_REQUIRED /
        # FORBIDDEN / IDLE / EMPTY transition arrives for that key.
        # Filtering by key keeps stale messages from a just-disposed
        # PaneVM (the prior failed attempt) from resolving the future
        # for the next attempt.
        self._attempt_conn_key: tuple[str, str] | None = None
        self._attempt_future: asyncio.Future[str] | None = None
        # Sticky for the session once the boot chain ran out of
        # candidates and we mounted local-on-both-panes. Subsequent
        # Settings → S3 toggles route through
        # ``_mount_local_only_dual_pane`` instead of rebuilding the
        # S3 DualPane (which would re-attempt the same failed
        # connections and force the user back into the same
        # 60s-then-error pane the chain already negotiated past).
        # The user can still press ``r`` on a pane or ``Shift+S``
        # to re-bind to a specific connection — those are the
        # explicit recovery paths.
        self._chain_resolved_to_local: bool = False
        self._chain_initial_conn: Connection | None = None
        # Tracks the last frozenset of skipped connection names shown in a
        # skip-toast so repeated Shift+S presses don't stack duplicate toasts.
        self._last_skip_toast_set: frozenset[str] | None = None

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
            demo=ctx.demo,
            id="brand-banner",
        )
        with Horizontal(id="main-area"):
            # NavMenu mounts as the always-visible left rail. Post-
            # PR-#94 there is no hamburger / collapse / expand mode —
            # the rail is a fixed-width pane that joins the Tab cycle
            # like any other.
            yield NavMenu(
                vm=ctx.root_vm.services_menu,
                hub=ctx.hub,
                focus_coordinator=ctx.focus_coordinator,
                id="nav-menu",
            )
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
        ctx.s3_connections_vm.construct()
        # NOTE: SettingsVM is intentionally NOT constructed here. It's
        # now built fresh per mount in ``_mount_settings_view`` because
        # ContentHostVM disposes its hosted VM on swap-out, and a
        # singleton would crash on the second Settings switch with
        # ``StatusTransitionError('Cannot construct from state Disposed.')``.

        self._apply_initial_theme()

        # Subscribe to PaneVM state transitions BEFORE switch_service so the
        # initial UNREACHABLE state-change (if the connection is offline) is
        # not missed (Bug 2: initial mount UNREACHABLE silently missed).
        self._pane_state_sub = ctx.hub.messages.subscribe(on_next=self._on_hub_message_pane_state)
        self._connection_list_sub = ctx.hub.messages.subscribe(
            on_next=self._on_connection_list_changed
        )
        self._nav_selection_sub = ctx.hub.messages.subscribe(on_next=self._on_nav_selection_changed)
        # Subscribe to pane cursor / entries changes so the HintLegend can
        # mark ``pane.copy`` / ``pane.delete`` disabled when the cursor sits
        # on the ``..`` parent row. User feedback: "if the selected item is
        # the '..' representing the parent folder, I shouldn't be able to
        # invoke copy or delete commands and I expect them greyed out".
        self._cursor_sub = ctx.hub.messages.subscribe(on_next=self._on_hub_message_cursor)

        initial_conn = self._resolve_initial_connection()
        if initial_conn is not None:
            auth_state = ctx.aws_session.probe_token(initial_conn).state
            await ctx.root_vm.switch_connection_with(initial_conn, auth_state)
            # If the resolved AWS connection's SSO token is EXPIRED at
            # Run the initial DualPane build in a background worker
            # so on_mount returns immediately and Textual paints the
            # chrome (banner, nav rail, hint legend, etc.) without
            # waiting for boto3 — see the worker docstring for the
            # blank-screen-on-launch history.
            #
            # AWS+EXPIRED is NOT routed to a special "auth required"
            # placeholder anymore (PR #55 originally did that). User
            # feedback after PR #59: an error-message-instead-of-panes
            # is a worse UX than panes-with-graceful-fallback. The
            # worker checks the auth state itself and, when EXPIRED,
            # skips the S3 build entirely (which would otherwise
            # block 15s on boto3 trying to refresh the SSO token) and
            # builds a LocalFS-only DualPane directly with a toast
            # telling the user how to recover.
            self._boot_in_flight = True
            self.run_worker(
                self._initial_mount_worker(initial_conn=initial_conn, auth_state=auth_state),
                exclusive=True,
                group="content-mount",
            )
        else:
            self._mount_no_connection_placeholder()

        # Drop Textual's automatic first-focus pass. By default Textual
        # focuses the first focusable widget on mount, which on the S3
        # screen is the NavMenu's services OptionList — the user then
        # has to press Tab once just to MOVE focus off the rail before
        # the second Tab actually toggles LEFT ↔ RIGHT. User report:
        # "the menu box comes focused / selected when the app is
        # launched". Same ``set_focus(None)`` trick the snapshot
        # fixtures already use (``_UnfocusedMixin``); deferred via
        # ``call_after_refresh`` so it runs AFTER Textual's first
        # focus pass instead of being silently undone by it.
        self.call_after_refresh(lambda: self.set_focus(None))

        if ctx.demo:
            # Spec: one-shot Advisory toast on mount so the user
            # learns the in-session contract on first run. The
            # persistent banner subtitle keeps reminding them
            # afterwards.
            notifications.advise(
                ctx.root_vm.chrome.toast_stack,
                subject="Source",
                message="Demo mode active — try every feature; nothing persists",
            )

    async def _initial_mount_worker(
        self, *, initial_conn: Connection, auth_state: TokenState
    ) -> None:
        """Walk the configured-connections chain, narrating each step
        via toasts, until one succeeds OR all fail (→ local-only).

        Boot UX history. v0.7 single-shot: try the resolved initial
        connection; on terminal-bad pane state, swap LEFT to LocalFS
        with a one-line toast. The user complaint after PR #69:
        "since it takes such a long time loading the proper content
        on the left side on app launch when it goes through all the
        fallbacks, I think we'd better show toast notifications
        when each source is about to be tried. And if that source
        turns out not available, we show another toast that it's
        not or that it failed, and then show another one about
        trying the next option and so on". So: build an ORDERED
        chain (initial first, then the rest in resolver order),
        and for each candidate raise a "▸ Trying <name>…" sticky
        INFO toast, attempt, dismiss it on outcome, raise a success
        SUCCESS or failure WARNING outcome toast. On chain
        exhaustion, raise a WARNING fallback toast and mount
        LocalFS-on-both-panes.

        AWS+(EXPIRED|MISSING) is short-circuited via offline
        ``probe_token`` so we don't burn the 15s SSO-refresh wait
        on a candidate we already know has no working session;
        s3-compatible and AWS+CONNECTED candidates are attempted
        live via ``switch_service("s3")`` and the LEFT pane's
        terminal state (IDLE / EMPTY = success; UNREACHABLE /
        AUTH_REQUIRED / FORBIDDEN = failure) signals the outcome
        through ``_attempt_future`` (resolved by
        ``_on_pane_state_changed`` for the matching connection key).

        ``auth_state`` is kept as a parameter for back-compat
        (caller in ``on_mount`` already probed the initial
        connection); we re-probe each AWS candidate in the chain
        instead of relying on the value because the chain may
        include connections the caller never probed.
        """
        _ = auth_state  # caller probed `initial_conn`; chain re-probes each AWS candidate
        ctx = self._app_ctx
        try:
            chain = self._build_attempt_chain(initial_conn)
            ctx.log_sink.info(
                "app.boot_chain.start",
                length=len(chain),
                initial=initial_conn.name,
            )
            for index, conn in enumerate(chain, start=1):
                # Defer the "▸ Trying X…" toast so a fast happy boot
                # (e.g. silent SSO succeeds in <500ms) stays silent.
                # The deferred task self-cancels if the attempt
                # resolves before the grace window. We then ONLY
                # raise the success toast if the pre-attempt toast
                # was visible — otherwise the success is implicit
                # (panes populated) and a redundant success toast
                # would just flash by.
                pre_attempt_visible = False
                pre_attempt_task = asyncio.create_task(
                    self._raise_attempt_toast_after_grace(conn, index=index, total=len(chain))
                )
                try:
                    outcome = await self._try_connection(conn)
                finally:
                    pre_attempt_task.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await pre_attempt_task
                    # If the grace window elapsed and the toast was
                    # actually raised, dismiss it before emitting the
                    # outcome toast.
                    if pre_attempt_task.done() and not pre_attempt_task.cancelled():
                        pre_attempt_visible = bool(pre_attempt_task.result())
                if pre_attempt_visible:
                    self._dismiss_attempt_toast(conn)
                if outcome == "ok":
                    if pre_attempt_visible:
                        self._raise_success_toast(conn)
                    ctx.log_sink.info(
                        "app.boot_chain.success",
                        kind=conn.kind,
                        name=conn.name,
                        attempt=index,
                    )
                    return
                self._mark_connection_unreachable(conn.kind, conn.name)
                self._raise_failure_toast(conn, outcome)
                ctx.log_sink.info(
                    "app.boot_chain.attempt_failed",
                    kind=conn.kind,
                    name=conn.name,
                    attempt=index,
                    outcome=outcome,
                )
            # Chain exhausted — mount local on both panes and
            # remember the decision so a subsequent Settings → S3
            # toggle reproduces the local-on-both state instead of
            # walking the chain again (which would just replay the
            # same failures the user already saw).
            self._raise_local_fallback_toast()
            self._chain_resolved_to_local = True
            self._chain_initial_conn = initial_conn
            await self._mount_local_only_dual_pane(
                initial_conn=initial_conn,
                reason="chain-exhausted",
            )
            ctx.log_sink.info("app.boot_chain.local_fallback", initial=initial_conn.name)
        finally:
            self._boot_in_flight = False

    def _build_attempt_chain(self, initial: Connection) -> list[Connection]:
        """Order the resolver's connection list with ``initial`` first.

        Per-launch ``unreachable_connections`` starts empty — nothing
        to skip on a cold start. If a previous attempt in this same
        boot marked a candidate unreachable, it's still in the chain
        but the pre-flight ``probe_token`` (for AWS) or the live
        attempt outcome will surface the same failure again, which
        is fine — the chain decides termination, not a static skip
        list. We DO de-duplicate so the initial isn't tried twice
        when it also appears in the resolver list.
        """
        ctx = self._app_ctx
        try:
            others = ctx.connection_resolver.list()
        except Exception as exc:
            ctx.log_sink.error(
                "app.boot_chain.resolver_failed",
                error=str(exc),
                error_type=type(exc).__name__,
            )
            others = []
        chain: list[Connection] = [initial]
        seen = {(initial.kind, initial.name)}
        for conn in others:
            key = (conn.kind, conn.name)
            if key in seen:
                continue
            chain.append(conn)
            seen.add(key)
        return chain

    async def _try_connection(self, conn: Connection) -> str:
        """Attempt to mount ``conn`` as the LEFT pane's source.

        Returns one of: ``"ok"``, ``"aws-sso-expired"``,
        ``"aws-no-creds"``, ``"unreachable"``, ``"auth-required"``,
        ``"forbidden"``, ``"timeout"``, ``"error"``. The chain
        narrator maps these into toast text.
        """
        ctx = self._app_ctx
        if conn.kind == "aws" and not ctx.demo:
            try:
                state = ctx.aws_session.probe_token(conn).state
            except Exception as exc:
                ctx.log_sink.error(
                    "app.boot_chain.probe_failed",
                    name=conn.name,
                    error=str(exc),
                    error_type=type(exc).__name__,
                )
                return "error"
            if state is TokenState.EXPIRED:
                return "aws-sso-expired"
            if state is TokenState.MISSING:
                return "aws-no-creds"
        # ``get_event_loop`` from inside a coroutine is deprecated in
        # 3.12+. ``_try_connection`` is always awaited from a worker,
        # so the running loop is the right and stable handle.
        loop = asyncio.get_running_loop()
        self._attempt_future = loop.create_future()
        self._attempt_conn_key = (conn.kind, conn.name)
        try:
            try:
                await ctx.root_vm.switch_connection_with(conn, TokenState.CONNECTED)
                await ctx.root_vm.switch_service("s3")
            except Exception as exc:
                ctx.log_sink.error(
                    "app.boot_chain.switch_failed",
                    name=conn.name,
                    error=str(exc),
                    error_type=type(exc).__name__,
                )
                return "error"
            self._mount_initial_service_view()
            try:
                return await asyncio.wait_for(self._attempt_future, timeout=90.0)
            except TimeoutError:
                return "timeout"
        finally:
            self._attempt_future = None
            self._attempt_conn_key = None

    # ── Boot-chain narration (toasts) ──────────────────────────────────────

    @staticmethod
    def _attempt_toast_id(conn: Connection) -> str:
        # Must match the ``progress`` helper's id shape:
        # ``f"progress-{key}"``.
        return f"progress-boot-attempt-{conn.kind}-{conn.name}"

    @staticmethod
    def _outcome_toast_id(conn: Connection) -> str:
        return f"boot-outcome-{conn.kind}-{conn.name}"

    # Grace window before the "▸ Trying X…" toast appears, so a fast
    # happy boot (silent SSO succeeds in well under this) stays
    # silent. The slow-boot UX the user actually asked for narration
    # of is well above this threshold (a single boto SSO refresh or
    # an unreachable s3 endpoint takes seconds, not hundreds of ms).
    _ATTEMPT_TOAST_GRACE_SECONDS: ClassVar[float] = 0.5

    async def _raise_attempt_toast_after_grace(
        self, conn: Connection, *, index: int, total: int
    ) -> bool:
        """Raise the pre-attempt toast after a grace window.

        Returns True if the toast was raised (caller will dismiss
        it on outcome), False otherwise. The outer caller cancels
        this task when the attempt's outcome arrives — a cancel
        before the grace window elapses means no toast was raised,
        so success is silent.
        """
        await asyncio.sleep(self._ATTEMPT_TOAST_GRACE_SECONDS)
        ctx = self._app_ctx
        posted = False
        try:
            notifications.progress(
                ctx.root_vm.chrome.toast_stack,
                key=f"boot-attempt-{conn.kind}-{conn.name}",
                subject="Connection",
                message=(
                    f"trying {conn.name} ({self._friendly_kind(conn.kind)}) — {index}/{total}"
                ),
            )
            posted = True
        except Exception:
            # Toast stack disposed / full / otherwise unhappy. Caller
            # should NOT later try to dismiss a toast that was never
            # raised — return ``False`` to telegraph that.
            posted = False
        return posted

    def _dismiss_attempt_toast(self, conn: Connection) -> None:
        with contextlib.suppress(Exception):
            self._app_ctx.root_vm.chrome.toast_stack.dismiss(self._attempt_toast_id(conn))

    def _raise_success_toast(self, conn: Connection) -> None:
        ctx = self._app_ctx
        with contextlib.suppress(Exception):
            notifications.success(
                ctx.root_vm.chrome.toast_stack,
                subject="Connection",
                message=f"{conn.name} connected",
                toast_id=self._outcome_toast_id(conn),
            )

    def _raise_failure_toast(self, conn: Connection, outcome: str) -> None:
        ctx = self._app_ctx
        reason = self._friendly_outcome(outcome)
        with contextlib.suppress(Exception):
            notifications.advise(
                ctx.root_vm.chrome.toast_stack,
                subject="Source",
                message=f"{conn.name} {reason}",
                action="trying next",
                toast_id=self._outcome_toast_id(conn),
            )

    def _raise_local_fallback_toast(self) -> None:
        ctx = self._app_ctx
        with contextlib.suppress(Exception):
            notifications.advise(
                ctx.root_vm.chrome.toast_stack,
                subject="Fallback",
                message="all sources unavailable, both panes set to local",
                action="press r in a pane to retry",
                toast_id="boot-fallback-local",
            )

    @staticmethod
    def _friendly_kind(kind: str) -> str:
        return {"aws": "AWS", "s3-compatible": "S3-compatible"}.get(kind, kind)

    @staticmethod
    def _friendly_outcome(outcome: str) -> str:
        return {
            "aws-sso-expired": "SSO token expired",
            "aws-no-creds": "no AWS credentials",
            "unreachable": "endpoint unreachable",
            "auth-required": "authentication required",
            "forbidden": "access denied",
            "timeout": "timed out",
            "error": "errored",
        }.get(outcome, outcome)

    async def _mount_local_only_dual_pane(self, *, initial_conn: Connection, reason: str) -> None:
        """Mount a DualPane with ``LocalFS`` on BOTH panes, bypassing
        ``S3Service.build_vm`` so we never construct an S3FS provider
        that would block 15s on boto3.

        Used by the AWS+EXPIRED proactive-fallback path. The
        ``initial_conn`` is still in ``ctx.unreachable_connections``
        so ``Shift+S`` skips it until the user runs
        ``aws sso login --profile X`` and relaunches (or until the
        ``r`` retry path clears the mark).
        """
        from aws_tui.domain.local_fs import LocalFS
        from aws_tui.vm.file_manager.dual_pane_vm import DualPaneVM
        from aws_tui.vm.file_manager.pane_vm import PaneVM

        ctx = self._app_ctx
        # Mark the connection unreachable so the swap-source ring
        # skips it (consistent with the reactive auto-fallback path).
        self._mark_connection_unreachable(initial_conn.kind, initial_conn.name)

        # Reach into the S3Service for its local_root configuration —
        # tests inject a custom root via this field, production
        # defaults to None (current working dir). Without this the
        # LocalFS would always start at CWD even in tests.
        s3_service = ctx.registry.get("s3")
        local_root = getattr(s3_service, "_local_root", None)

        def _make_local() -> LocalFS:
            return LocalFS(root=local_root) if local_root else LocalFS()

        left = PaneVM(
            provider=_make_local(),
            hub=ctx.hub,
            dispatcher=ctx.dispatcher,
            id_prefix="pane.local",
            identity_label="local",
            path_protocol="",
            connection_key=None,
        )
        right = PaneVM(
            provider=_make_local(),
            hub=ctx.hub,
            dispatcher=ctx.dispatcher,
            id_prefix="pane.local",
            identity_label="local",
            path_protocol="",
            connection_key=None,
        )
        # Defensive: keep S3Service satisfied even if it inspects
        # local_root later.
        _ = s3_service
        # Pull the transfer journal from the S3Service the same way
        # build_vm does so cross-pane copy/move journals stay
        # consistent across the fallback.
        journal = getattr(s3_service, "_journal", None)
        if journal is None:
            # Shouldn't happen — S3Service always has a journal — but
            # bail cleanly if it does.
            ctx.log_sink.error("app.local_only_mount.missing_journal")
            return
        dual = DualPaneVM(
            left=left,
            right=right,
            hub=ctx.hub,
            dispatcher=ctx.dispatcher,
            transfer_journal=journal,
        )
        try:
            # ContentHostVM.set_content is idempotent on a matching
            # ``service_id`` — it returns early if ``current_id`` is
            # already the same. When the boot chain falls back to
            # local-on-both AFTER an s3-compatible attempt failed,
            # ``current_id`` is already "s3" (the failed attempt's
            # service id) and the idempotent check would silently skip
            # adopting the new local DualPaneVM, leaving the host
            # bound to the failed S3FS panes. User-visible symptom:
            # "upon launching the app, neither pane shows any content
            # when neither aws s3 nor s3-compatible are available.
            # Only when I browse the settings and come back to S3, it
            # shows the local source." (The toggle path worked because
            # ``current_id`` flipped to ``"settings"`` in between,
            # invalidating the idempotent skip.) Force the swap by
            # clearing first.
            await ctx.root_vm.content_host.set_content(None, service_id=None)
            await ctx.root_vm.content_host.set_content(dual, service_id="s3")
        except Exception as exc:
            ctx.log_sink.error(
                "app.local_only_mount.set_content_failed",
                error=str(exc),
                error_type=type(exc).__name__,
            )
            return
        # Mount the widget.
        try:
            host = self.query_one("#content-host", Container)
            await host.remove_children()
            await host.mount(DualPane(dual, hub=ctx.hub, id="content-dual-pane"))
        except Exception as exc:
            ctx.log_sink.error(
                "app.local_only_mount.mount_failed",
                error=str(exc),
                error_type=type(exc).__name__,
            )

        # Recovery-hint toast. Originally sticky so the user could read
        # the recovery command at their own pace; the user reported the
        # sticky behavior felt intrusive ("remains there which is not
        # something I want, I want it to disappear after a count down"),
        # so switched to a non-sticky toast with a generous 12-second
        # timeout. That's long enough to read a one-line command,
        # short enough not to crowd subsequent toasts.
        profile = initial_conn.profile or initial_conn.name
        message, action = {
            "aws-sso-expired": (
                f"SSO expired for [b]{profile}[/], both panes set to local",
                f"refresh with [b]aws sso login --profile {profile}[/] and relaunch",
            ),
            "aws-no-creds": (
                f"no session for [b]{profile}[/], both panes set to local",
                f"run [b]aws sso login --profile {profile}[/] (or set $AWS_PROFILE) and relaunch",
            ),
        }.get(
            reason,
            (f"{initial_conn.name} unavailable, both panes set to local", None),
        )
        # ``chain-exhausted`` callers (``_initial_mount_worker``)
        # already raised the boot-chain's own local-fallback toast;
        # don't double-up. Other callers (legacy direct invocation)
        # still get the per-reason recovery hint.
        if reason != "chain-exhausted":
            notifications.advise(
                ctx.root_vm.chrome.toast_stack,
                subject="Auth",
                message=message,
                action=action,
                toast_id=f"initial-fallback-{reason}-{initial_conn.name}",
            )
        ctx.log_sink.info(
            "app.local_only_mount.success",
            reason=reason,
            kind=initial_conn.kind,
            name=initial_conn.name,
        )

    # ── on_mount helpers ───────────────────────────────────────────────────

    def _apply_initial_theme(self) -> None:
        """Layer the active theme `.tcss` on top of Textual's defaults.

        Uses the same ``read_from=self._THEME_SOURCE_KEY`` keyed
        source that ``switch_theme`` uses, so the first runtime theme
        swap REPLACES this initial source instead of stacking on top
        of an anonymous one (which would leave a dead entry the
        stylesheet would still re-parse on every refresh).
        """
        ctx = self._app_ctx
        try:
            theme_css = ctx.theme_store.load(ctx.initial_theme)
            self.stylesheet.add_source(theme_css, read_from=self._THEME_SOURCE_KEY)
            self.stylesheet.parse()
            self.stylesheet.update(self)
        except Exception as exc:
            ctx.log_sink.error(
                "app.theme.load_failed",
                name=ctx.initial_theme,
                error=str(exc),
                error_type=type(exc).__name__,
            )

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
        except Exception as exc:
            ctx.log_sink.error(
                "app.config_load.failed",
                error=str(exc),
                error_type=type(exc).__name__,
            )
            cfg = None
        # ``connection_resolver.list()`` calls ``_explicit_connections()``
        # which calls ``config_store.load()`` AGAIN — without this guard,
        # a malformed ~/.config/aws-tui/config.toml propagates the second
        # parse failure up through ``on_mount``, crashing the app with a
        # raw Python traceback before any UI lands. The first ``load()``
        # above is already guarded, but the resolver's internal load was
        # not. Fall back to an empty list so the boot chain reaches the
        # no-connection placeholder branch and the user gets a usable UI
        # + the ``app.config_load.failed`` log line for diagnostics.
        try:
            connections = ctx.connection_resolver.list()
        except Exception as exc:
            ctx.log_sink.error(
                "app.connection_resolver_list.failed",
                error=str(exc),
                error_type=type(exc).__name__,
            )
            connections = []
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

        Connection attribution is now carried by ``PaneVM.current_connection_key``
        (set by ``S3Service.build_vm``), so no per-pane tracker is needed here.
        """
        ctx = self._app_ctx
        _svc_id: str | None = None
        try:
            current_vm = ctx.root_vm.content_host.current
            _svc_id = ctx.root_vm.content_host.current_id
            if current_vm is not None:
                host = self.query_one("#content-host", Container)
                host.remove_children()
                if _svc_id == "emr-serverless":
                    host.mount(
                        EmrServerlessPage(
                            current_vm,
                            hub=ctx.hub,
                            focus_coordinator=ctx.focus_coordinator,
                            id="content-emr-page",
                        )
                    )
                else:
                    host.mount(DualPane(current_vm, hub=ctx.hub, id="content-dual-pane"))
        except Exception as exc:
            ctx.log_sink.error(
                "app.mount_service_view.failed",
                service_id=_svc_id or "unknown",
                error=str(exc),
                error_type=type(exc).__name__,
            )
            # Surface the failure so the user gets *some* explanation
            # instead of a blank screen. Suppressed because the toast
            # stack may not be mounted yet during startup; the log
            # entry above is the durable record.
            with contextlib.suppress(Exception):
                notifications.error(
                    ctx.root_vm.chrome.toast_stack,
                    subject="Mount",
                    message=f"Service view failed: {type(exc).__name__}",
                    action="see ~/.cache/aws-tui/log/aws-tui.log",
                    toast_id="mount-service-failed",
                )

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

    async def action_quit(self) -> None:
        """Override Textual's built-in ``action_quit`` so every exit path
        (the ``q`` and ``ctrl+c`` BINDINGS above, plus Textual's own
        SIGINT handler, plus the action-registry ``app.quit`` bridge
        once the input-router lands) flows through the async shutdown
        defined at :meth:`_aws_tui_shutdown`.

        Previously ``BINDINGS`` mapped ``q`` / ``ctrl+c`` to the bare
        ``"quit"`` action which Textual resolved to its sync
        :meth:`App.action_quit` (just ``self.exit()``), bypassing
        :meth:`_aws_tui_shutdown` entirely — aioboto3 client closures,
        in-flight transfer cancellation, log-sink flush, and VM
        disposal all leaked at exit. The async coroutine
        :meth:`action_app_quit` was wired but never invoked because no
        binding routed to it.
        """
        await self._aws_tui_shutdown()
        self.exit()

    # Retained for the deferred BindingResolver's ``app.quit`` bridge
    # so user-defined ``[keybindings].app.quit`` entries reach the
    # same shutdown path. Production today goes through ``action_quit``
    # above; this alias becomes load-bearing when the input-router
    # ships.
    async def action_app_quit(self) -> None:
        await self.action_quit()

    def _handle_quit(self) -> None:
        # Synchronous fallback for the BindingResolver bridge that
        # cannot await. Schedule the async path on the event loop
        # so cleanup still runs instead of being silently dropped.
        self.run_worker(self.action_quit(), exclusive=True, group="shutdown")

    def _dual_pane(self) -> object | None:
        """Return the currently-hosted ``DualPaneVM`` (or None).

        Returns None when the content host is showing a non-file-manager
        view (e.g. SettingsView).
        """
        from aws_tui.vm.file_manager.dual_pane_vm import DualPaneVM

        current = self._app_ctx.root_vm.content_host.current
        return current if isinstance(current, DualPaneVM) else None

    def _emr_page(self) -> EmrServerlessPage | None:
        """Return the currently-mounted :class:`EmrServerlessPage`
        widget, or None when EMR isn't the active service.

        Mirror of :meth:`_dual_pane` for the EMR page so the global
        priority bindings (``up``/``down``/``enter``/``r``) can route
        keystrokes to the EMR LEFT pane the same way they route to
        the S3 file pane via ``dual.focused_pane``.
        """
        with contextlib.suppress(Exception):
            return self.query_one("#content-emr-page", EmrServerlessPage)
        return None

    def _emr_active_pane(self, emr_page: EmrServerlessPage) -> object | None:
        """Return whichever EMR pane (LEFT or RIGHT) should receive
        the next cursor/refresh action.

        Logic: if Textual focus is currently inside one of the two
        focusable panes (JobRunsPane LEFT or JobRunLogsPane RIGHT),
        return that pane. Otherwise default to LEFT — the S3 page's
        analogue is ``dual.focused_pane`` defaulting to LEFT before
        the user has Tabbed; mirroring that gives the same "arrow keys
        work immediately after clicking ⚡ in the rail" UX as S3.

        Note: the detail pane is non-focusable and never returned.
        """
        focused = self.focused
        if focused is not None:
            for pane in (emr_page.left_pane, emr_page.right_pane):
                if pane is not None and (focused is pane or pane in focused.ancestors_with_self):
                    return pane
        return emr_page.left_pane

    def action_switch_focus(self) -> None:
        """Tab cycle. NavMenu is a regular slot — user feedback
        (post-PR-#93): "I also want the menu pane be treated like
        any other pane in the app, which mean tab switching should
        allow for it being among the switchable panes to be
        selected / focused".

        Slot order:

        - **S3** (3-slot): NAV → LEFT → RIGHT
        - **EMR** (4-slot): NAV → LEFT → DETAIL → LOGS — delegated
          to :meth:`EmrServerlessPage.action_cycle_panes_forward`.
        - **Settings-only** (no DualPane mounted): NAV ↔ Content
          (the previous 2-way preserved so keyboard users can leave
          the Settings page without a mouse).

        Pane widgets decline Textual focus by design (they use a CSS
        ``-focused`` class toggled by ``DualPaneVM``); the NavMenu
        DOES accept Textual focus (it's an OptionList container).
        We bridge the two: ``self.set_focus(nav-list)`` for the NAV
        slot, then ``self.set_focus(None)`` when moving to a pane
        slot so Textual's focus chain stops chasing the OptionList.
        """
        # EMR page owns its own 4-slot Tab cycle. Delegate
        # immediately so the App-level priority binding never falls
        # through to the S3 / Settings branches below.
        with contextlib.suppress(Exception):
            emr_page = self.query_one("#content-emr-page", EmrServerlessPage)
            emr_page.action_cycle_panes_forward()
            return

        try:
            nav = self.query_one("#nav-menu", NavMenu)
        except Exception:
            nav = None
        dual = self._dual_pane()

        if dual is None:
            # Settings-only screen — 2-way cycle between content
            # host and NavMenu so keyboard users can leave the
            # Settings page without a mouse.
            if nav is not None:
                if self._nav_has_focus():
                    with contextlib.suppress(Exception):
                        self.set_focus(None)
                else:
                    with contextlib.suppress(Exception):
                        self._focus_active_nav_list(nav)
            return

        # S3 screen — 3-slot ring: NAV → LEFT → RIGHT → NAV.
        # NavMenu is the only slot that accepts Textual focus; the
        # two file panes don't (they decline focus by design and
        # display a CSS ``-focused`` class via DualPaneVM). We
        # derive the current slot from the combination of "does
        # Textual focus sit on the NavMenu" + ``DualPaneVM.focused``.
        switch_focus = getattr(dual, "switch_focus_command", None)
        if nav is not None and self._nav_has_focus():
            # NAV → LEFT. Drop NavMenu focus first so Textual's
            # focus chain stops chasing the OptionList, then nudge
            # DualPaneVM onto LEFT (toggle only if currently RIGHT
            # — ``switch_focus_command`` is a flip, no set-LEFT
            # primitive exists on the VM).
            with contextlib.suppress(Exception):
                self.set_focus(None)
            focused_pane = getattr(dual, "focused", None)
            focused_name = getattr(focused_pane, "value", None)
            if focused_name != "left" and switch_focus is not None:
                switch_focus.execute()
            return
        focused_pane = getattr(dual, "focused", None)
        focused_name = getattr(focused_pane, "value", None)
        if focused_name == "left":
            # LEFT → RIGHT (flip the VM's focused pane).
            if switch_focus is not None:
                switch_focus.execute()
            return
        # RIGHT → NAV (default branch — also catches the boot-time
        # "no pane focused yet" state).
        if nav is not None:
            with contextlib.suppress(Exception):
                self._focus_active_nav_list(nav)

    def focus_active_service_pane(self) -> None:
        """Move focus to the currently-active service's default pane.

        Called by :meth:`NavMenu.action_commit` when ENTER is pressed
        on a service row — the user's explicit intent to "enter" the
        service. Per-service mapping:

        - ``s3`` → :class:`DualPane.focus_left_pane` (Panes decline
          Textual focus by design; the visual indicator is driven by
          ``DualPaneVM.focused`` via CSS, so we drop App focus and
          ensure the VM is on LEFT).
        - ``emr-serverless`` → :class:`JobRunsPane` (the focusable
          LEFT pane of the EMR page).
        - ``settings`` → first :class:`CollapsibleTitle` of the
          Settings view.

        For service swaps (target ≠ current selected), the new page
        may not be mounted yet when this fires; each branch's
        ``query_one`` silently no-ops in that case, and the
        destination page's own ``on_mount`` auto-focus is the safety
        net (gated on "is NavMenu still focused?", which NavMenu
        clears before calling here).
        """
        current_id = self._app_ctx.root_vm.content_host.current_id
        if current_id == "s3":
            with contextlib.suppress(Exception):
                dual_widget = self.query_one("#content-dual-pane", DualPane)
                dual_widget.focus_left_pane()
            return
        if current_id == "emr-serverless":
            with contextlib.suppress(Exception):
                emr_page = self.query_one("#content-emr-page", EmrServerlessPage)
                left = emr_page.left_pane
                if left is not None:
                    left.focus()
            return
        if current_id == SETTINGS_NAV_ID:
            with contextlib.suppress(Exception):
                settings = self.query_one(SettingsView)
                settings.focus_default()
            return

    def _focus_active_nav_list(self, nav: NavMenu) -> None:
        """Hand Textual focus to the NavMenu.

        Post-PR-#94 rework: NavMenu is itself the focusable widget
        (no more internal OptionLists). Lands focus directly so
        arrow keys reach :meth:`NavMenu.action_cursor_up` /
        ``action_cursor_down``. Name kept (``_focus_active_nav_list``)
        for backward compatibility with the call sites the App and
        the EMR page already use.
        """
        with contextlib.suppress(Exception):
            nav.focus()

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
        # EMR page: if the application picker dropdown is open, Up/
        # Down navigate the picker's OptionList rather than the
        # pane cursor. Without this, the App's ``priority=True`` Up/
        # Down binding steals the keystroke and moves the runs-pane
        # cursor instead — user feedback: "When the list of
        # applications is expanded, I can't use arrow keys and enter
        # to choose a new app". Same hijack-pattern PR #77/#78 used
        # for the nav-rail.
        picker_opts = self._open_emr_picker_optionlist()
        if picker_opts is not None:
            if delta < 0:
                picker_opts.action_cursor_up()
            else:
                picker_opts.action_cursor_down()
            return
        # If Textual focus is in the NavMenu's OptionList, Up/Down
        # should navigate THAT list, not the pane cursor. Textual's
        # ``priority=True`` on the App binding steals the keystroke
        # before the OptionList sees it; we manually forward the
        # cursor action so OptionList navigation still works while
        # nav is the active Tab-cycle slot.
        if self._nav_has_focus():
            # NavMenu owns its own cursor; ``priority=True`` on the
            # App-level Up/Down binding would otherwise steal the
            # keystroke before NavMenu's binding fires. Manual
            # forward keeps the master-detail switch happening on
            # every arrow press.
            try:
                nav = self.query_one("#nav-menu", NavMenu)
            except Exception:
                return
            if delta < 0:
                nav.action_cursor_up()
            else:
                nav.action_cursor_down()
            return
        # EMR page: forward Up/Down to the active EMR pane's cursor
        # or scroll action. Mirrors the S3 path's ``dual.focused_pane``
        # lookup. LEFT pane uses cursor actions; RIGHT pane (logs) uses
        # scroll actions. ``getattr(..., None)`` swallows the key cleanly
        # when the pane doesn't support the action.
        emr_page = self._emr_page()
        if emr_page is not None:
            emr_pane = self._emr_active_pane(emr_page)
            if emr_pane is not None:
                # Try cursor action first (LEFT pane), then scroll action (RIGHT pane).
                action = getattr(
                    emr_pane,
                    "action_cursor_up" if delta < 0 else "action_cursor_down",
                    None,
                )
                if action is not None:
                    action()
                    return
                # Fallback to scroll action for logs pane.
                action = getattr(
                    emr_pane,
                    "action_scroll_up" if delta < 0 else "action_scroll_down",
                    None,
                )
                if action is not None:
                    action()
            return
        dual = self._dual_pane()
        if dual is None:
            return
        pane = getattr(dual, "focused_pane", None)
        if pane is None:
            return
        cmd = getattr(pane, "move_cursor_command", None)
        if cmd is not None:
            cmd.execute(delta)

    def _open_emr_picker_optionlist(self) -> OptionList | None:
        """Return the EMR application picker's OptionList if and only
        if the picker dropdown is open. Used by the priority-binding
        forwards (``_move_cursor`` / ``action_descend``) so Up/Down/
        Enter route to the dropdown when it's expanded.
        """
        emr_page = self._emr_page()
        if emr_page is None:
            return None
        picker = getattr(emr_page, "_picker", None)
        if picker is None:
            return None
        if "-open" not in picker.classes:
            return None
        try:
            opts: OptionList = picker.query_one("#app-options", OptionList)
        except Exception:
            return None
        return opts

    def _nav_has_focus(self) -> bool:
        """True if Textual focus is currently on the NavMenu.

        Post-PR-#94 rework the NavMenu is itself the focusable
        widget — no internal OptionLists. The descendant check
        survives in case any future child widget grabs focus
        (none currently does), but the common case is
        ``self.focused is nav``.

        Used to gate priority bindings (Up/Down/Enter) so they
        don't steal keystrokes from the NavMenu's cursor
        navigation when the user has Tab-cycled focus to the
        rail.
        """
        try:
            nav = self.query_one("#nav-menu", NavMenu)
        except Exception:
            return False
        focused = self.focused
        if focused is None:
            return False
        return focused is nav or nav in focused.ancestors_with_self

    def _focused_optionlist(self) -> OptionList | None:
        """Vestigial — kept so the EMR application picker's
        dropdown OptionList resolver path (``_open_emr_picker_optionlist``)
        is the only OptionList we still need to address. NavMenu
        no longer hosts an OptionList post-PR-#94; returns None for
        the nav case."""
        return None

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
        # EMR page: if the application picker dropdown is open,
        # Enter commits the highlighted row — drive the picker's
        # ``action_commit`` directly so it fires the
        # ``ApplicationCommitted`` message + cascades through
        # ``page_vm.select_application`` (the JobRuns + Detail
        # panes refresh in lockstep). Same priority-binding-hijack
        # rationale as the Up/Down forward in ``_move_cursor``.
        picker_opts = self._open_emr_picker_optionlist()
        if picker_opts is not None:
            emr_page = self._emr_page()
            if emr_page is not None and emr_page._picker is not None:
                emr_page._picker.action_commit()
            return
        # If Textual focus is in the NavMenu, forward Enter to its
        # own commit action (re-fires the switch on the
        # currently-highlighted row). Post-PR-#94 NavMenu is the
        # focusable widget directly, no OptionList layer.
        if self._nav_has_focus():
            try:
                nav = self.query_one("#nav-menu", NavMenu)
            except Exception:
                return
            nav.action_commit()
            return
        # EMR page: Enter on the LEFT pane commits the focused row
        # (posts ``JobRunsPane.RunSelected`` → page VM forwards to
        # ``select_job_run``). Enter on the RIGHT pane (logs) triggers
        # ``action_load`` to fetch logs. The ``getattr(..., None)``
        # guard mirrors the S3 path.
        emr_page = self._emr_page()
        if emr_page is not None:
            emr_pane = self._emr_active_pane(emr_page)
            if emr_pane is not None:
                # Try commit action first (LEFT pane), then load action (RIGHT pane).
                commit = getattr(emr_pane, "action_commit_selection", None)
                if commit is not None:
                    commit()
                    return
                # Fallback to load action for logs pane.
                load = getattr(emr_pane, "action_load", None)
                if load is not None:
                    load()
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
        # EMR page: Backspace is currently a deliberate no-op (the
        # page is a 2-slot master-detail with no hierarchical
        # navigation), but we short-circuit here so the keystroke
        # isn't silently consumed by ``_dual_pane()``-style
        # fallthrough. Symmetric with ``action_descend``'s EMR
        # branch (the user reaches the EMR page through the rail,
        # not by navigating up out of it).
        if self._emr_page() is not None:
            return
        dual = self._dual_pane()
        if dual is None:
            return
        pane = getattr(dual, "focused_pane", None)
        if pane is None or pane.path.is_root:
            return
        await pane.navigate_to(pane.path.parent())

    async def action_modal_left_or_ascend(self) -> None:
        """In modals, move focus to the previous button; in panes, ascend to parent."""
        # In a modal: Left moves arrow-key focus to the previous footer
        # button (or whatever the modal exposes as ``action_focus_prev``).
        # Outside any modal: behaves like ``ascend`` so file-pane
        # navigation is unchanged.
        if self._forward_to_modal("action_focus_prev"):
            return
        await self.action_ascend()

    def action_modal_right(self) -> None:
        """In modals, move focus to the next button. No-op in panes."""
        # In a modal: Right moves arrow-key focus to the next footer
        # button. Outside any modal: no-op (panes don't currently bind
        # Right to anything).
        self._forward_to_modal("action_focus_next")

    async def action_refresh(self) -> None:
        # EMR page: ``r`` forwards to whichever pane currently holds
        # Textual focus (LEFT or RIGHT). LEFT pane (JobRunsPane) posts
        # ``RefreshRequested`` which the page widget routes to
        # ``page_vm.refresh_focused("runs")``. RIGHT pane (JobRunLogsPane)
        # calls ``action_reload`` to refresh/reload logs.
        emr_page = self._emr_page()
        if emr_page is not None:
            emr_pane = self._emr_active_pane(emr_page)
            if emr_pane is not None:
                # Try refresh request first (LEFT pane).
                refresh = getattr(emr_pane, "action_request_refresh", None)
                if refresh is not None:
                    refresh()
                    return
                # Fallback to reload action for logs pane.
                reload = getattr(emr_pane, "action_reload", None)
                if reload is not None:
                    reload()
            return
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
        explicit confirm.

        EMR-page hijack: when the EMR Serverless page is the active
        content (not S3), ``c`` reroutes to the page widget's own
        ``action_clone_selected_run`` — same priority-binding-hijack
        pattern PR #77 used for Tab and PR #78 used for Up/Down/Enter/r.
        Without this short-circuit the App-level priority binding
        swallows ``c`` before the page widget's binding can run.
        """
        emr_page = self._emr_page()
        if emr_page is not None:
            await emr_page.action_clone_selected_run()
            return
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
            # Serialize against the delete worker — both mutate the
            # focused pane's mark state and the shared transfer journal.
            # Without exclusive+group, a user pressing `c` then `d` in
            # quick succession can interleave the two flows on the same
            # DualPaneVM._journal / pane entries (see Textual worker race
            # rule: shared-state workers must use exclusive=True,
            # group="<name>").
            self.run_worker(
                self._run_copy(dual, list(targets), used_cursor_fallback),
                exclusive=True,
                group="transfer-ops",
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
            await copy_across()
        except Exception as exc:
            ctx.log_sink.error(
                "app.copy.failed",
                error=str(exc),
                error_type=type(exc).__name__,
                file_count=len(targets),
            )
            # User feedback: "when a copy or delete fails, I see an
            # error box shown ON the command section at the bottom
            # which then disappears. I want all such errors be
            # handled gracefully and be shown as toast notifications
            # on top right with their proper icons and emojis". The
            # ``notifications.error`` helper routes through the
            # canonical ``ToastStack`` (top-right, ✖ glyph,
            # ``$danger`` colour, 30-s auto-dismiss); Textual's
            # bare ``self.notify`` paints over the Commands strip
            # and steals the eye there.
            notifications.error(
                ctx.root_vm.chrome.toast_stack,
                toast_id="copy-failed",
                subject="Transfer",
                message=f"copy failed: {exc}",
            )
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
            # Shares the "transfer-ops" group with copy so the two flows
            # can't interleave on the focused pane's mark state.
            self.run_worker(
                self._run_delete(dual, list(targets), used_cursor_fallback),
                exclusive=True,
                group="transfer-ops",
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
            await delete_in_focused()
        except Exception as exc:
            ctx.log_sink.error(
                "app.delete.failed",
                error=str(exc),
                error_type=type(exc).__name__,
                file_count=len(targets),
            )
            # See action_copy for the rationale — same Commands-strip
            # paint-over problem when using bare ``self.notify``.
            notifications.error(
                ctx.root_vm.chrome.toast_stack,
                toast_id="delete-failed",
                subject="Transfer",
                message=f"delete failed: {exc}",
            )
        finally:
            if used_cursor_fallback:
                for entry in targets:
                    entry.set_marked(False)  # type: ignore[attr-defined]

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
        identity label against each candidate's computed label.

        On the EMR page (no DualPaneVM hosted), ``Shift+S`` cycles
        to the NEXT application (wraps at end) — user feedback:
        "shift + s … doesn't result in an actual app switching",
        they want an actual switch, not just opening the picker.
        The picker is still openable explicitly via ``a``.
        """
        emr = self._emr_page()
        if emr is not None:
            emr.action_cycle_application_forward()
            return
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
        # Bug 4 fix: deduplicate skip toasts — only raise when the skipped
        # set changes (avoid stacking N identical toasts on repeated Shift+S).
        skipped_fs = frozenset(skipped)
        if skipped_fs != self._last_skip_toast_set:
            _raise_skip_toast(ctx, skipped)
            self._last_skip_toast_set = skipped_fs if skipped else None
        if len(candidates) <= 1:
            # Only local — either no connections configured, or every
            # configured connection has been observed unreachable.
            if skipped:
                notifications.advise(
                    ctx.root_vm.chrome.toast_stack,
                    toast_id="swap-source-unreachable",
                    subject="Source",
                    message="all connections unreachable — staying on local",
                )
            else:
                notifications.advise(
                    ctx.root_vm.chrome.toast_stack,
                    toast_id="swap-source-empty",
                    subject="Source",
                    message="no connections configured — can't swap source",
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
        # Compute the connection_key passed to swap_provider so it is stored
        # atomically BEFORE _reload() fires — eliminating the attribution race
        # (Bug 1) where _on_hub_message_pane_state would read a stale key.
        new_conn_key: tuple[str, str] | None
        if payload == "local":
            new_conn_key = None
        else:
            assert not isinstance(payload, str)  # narrows payload to Connection
            new_conn_key = (payload.kind, payload.name)
        ctx.log_sink.info("pane.swap_source", to=next_label)
        await swap(
            new_provider,
            identity_label=next_label,
            path_protocol=new_protocol,
            connection_key=new_conn_key,
        )

    def action_open_settings(self) -> None:
        """Select the Settings entry in the nav menu (programmatic equivalent
        of clicking it). Bound to ``,`` (comma)."""
        self._app_ctx.root_vm.services_menu.switch_service_command.execute(SETTINGS_NAV_ID)

    async def _rebind_pane_to_local(self, pane: object) -> None:
        """Rebind a pane to the local filesystem provider.

        Mirrors the local-branch of ``action_swap_source``.
        """
        from aws_tui.domain.local_fs import LocalFS

        swap = getattr(pane, "swap_provider", None)
        if swap is None:
            return
        await swap(
            LocalFS(),
            identity_label="local",
            path_protocol="",
            connection_key=None,
        )

    async def _rebind_pane_to_connection(self, pane: object, conn: object) -> None:
        """Rebind a pane to an S3FS provider for ``conn``.

        Mirrors the remote-branch of ``action_swap_source``. ``conn`` is
        typed as ``object`` here to avoid a circular import with
        ``infra.connection_resolver``; runtime attribute access is safe
        (Connection is the only thing the resolver returns).
        """
        from aws_tui.domain.s3_fs import S3FS
        from aws_tui.services.s3.service import _aioboto3_session_for, _format_pane_title

        swap = getattr(pane, "swap_provider", None)
        if swap is None:
            return
        session = _aioboto3_session_for(conn)  # type: ignore[arg-type]
        provider = S3FS(
            session=session,
            bucket=None,
            endpoint_url=conn.endpoint_url,  # type: ignore[attr-defined]
            force_path_style=conn.force_path_style,  # type: ignore[attr-defined]
        )
        await swap(
            provider,
            identity_label=_format_pane_title(conn),  # type: ignore[arg-type]
            path_protocol="s3:",
            connection_key=(conn.kind, conn.name),  # type: ignore[attr-defined]
        )

    async def action_themes(self) -> None:
        """Open the keyboard-navigable theme picker modal.

        Idempotent — if a ThemePickerModal is already on the screen
        stack (user pressed ``t`` more than once), this is a no-op.
        Previously each ``t`` press stacked another picker on top,
        requiring N ``Esc`` presses to clear N stacked modals.
        """
        # ``self.screen_stack`` is ordered: the active screen is last.
        # Walk it and bail if any layer is already a ThemePickerModal.
        for screen in self.screen_stack:
            if isinstance(screen, ThemePickerModal):
                return

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

        # ``push_screen`` returns AwaitMount which resolves on MOUNT,
        # not dismiss — the dedicated wait-for-dismiss is
        # ``push_screen_wait`` (or the callback form). The prior
        # ``finally`` ran the moment the modal APPEARED, scheduling
        # picker.dispose on the next tick. Every subsequent user
        # action (cursor preview, Enter apply, Esc rollback) then
        # called pick_theme_command.execute / preview_command.execute
        # on already-disposed RelayCommands and mutated already-
        # disposed ThemeOptionVM._inner (whose _trigger_disposed=True
        # silently swallows property-change subjects). Use the
        # callback form so dispose runs on DISMISSAL.
        def _on_picker_dismiss(_result: None) -> None:
            self.call_after_refresh(picker.dispose)

        self.push_screen(modal, _on_picker_dismiss)

    def _raise_theme_changed_toast(self, theme_name: str) -> None:
        """Routed through :class:`ToastStackVM` (notifications overlay
        layer) rather than ``self.notify()`` (Textual's built-in
        bottom-center notify, which wrecks the footer)."""
        ctx = self._app_ctx
        notifications.announce(
            ctx.root_vm.chrome.toast_stack,
            subject="Theme",
            message=f"switched to {theme_name}",
            toast_id=f"theme-changed-{theme_name}",
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
        except Exception as exc:
            ctx.log_sink.error(
                "app.theme.load_failed",
                name=name,
                error=str(exc),
                error_type=type(exc).__name__,
            )
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

    # ── Connection-reachability tracking ───────────────────────────────────

    def _mark_connection_unreachable(self, kind: str, name: str) -> None:
        """Add ``(kind, name)`` to the unreachable set so subsequent
        Shift+S cycles skip this connection. Idempotent.
        """
        self._app_ctx.unreachable_connections.add((kind, name))
        self._app_ctx.log_sink.info("connection.unreachable.mark", kind=kind, name=name)

    def _clear_connection_unreachable(self, kind: str, name: str) -> None:
        """Remove ``(kind, name)`` from the unreachable set so it
        re-enters the swap-source ring. Idempotent.
        """
        self._app_ctx.unreachable_connections.discard((kind, name))
        self._app_ctx.log_sink.info("connection.unreachable.clear", kind=kind, name=name)

    def _on_pane_state_changed(
        self,
        *,
        kind: str,
        name: str,
        new_state: PaneState,
    ) -> None:
        """Hub-subscriber dispatch. When an active pane's state hits
        UNREACHABLE, mark its connection. When it transitions to
        IDLE / EMPTY from UNREACHABLE, clear the mark.

        Also resolves ``_attempt_future`` for the boot-time chain
        narrator when the LEFT pane reaches a terminal state for the
        currently-attempting connection key. ``_initial_mount_worker``
        is awaiting that future to decide success vs. failure vs.
        move-to-next-candidate. Filtering by ``_attempt_conn_key``
        keeps stale messages from a prior disposed PaneVM from
        resolving the next attempt's future.
        """
        from aws_tui.vm.file_manager.pane_vm import PaneState

        was_marked = (kind, name) in self._app_ctx.unreachable_connections
        if new_state is PaneState.UNREACHABLE:
            self._mark_connection_unreachable(kind, name)
        elif was_marked and new_state in (PaneState.IDLE, PaneState.EMPTY):
            self._clear_connection_unreachable(kind, name)
            # ``r`` retry (or any post-boot rebind) that actually
            # reached IDLE/EMPTY means the user has a working live
            # connection again — drop the chain-fallback memo so
            # Settings → S3 toggles return to the real DualPane
            # instead of staying pinned to local-on-both.
            self._chain_resolved_to_local = False

        # Boot-chain attempt narrator: resolve the awaited future on
        # first terminal state for the matching connection key. The
        # chain owns the user-facing fallback decision (no reactive
        # auto-fallback anymore — the chain walks every candidate
        # explicitly and falls back to local on exhaustion).
        if (
            self._attempt_future is not None
            and not self._attempt_future.done()
            and self._attempt_conn_key == (kind, name)
        ):
            outcome: str | None = None
            if new_state in (PaneState.IDLE, PaneState.EMPTY):
                outcome = "ok"
            elif new_state is PaneState.UNREACHABLE:
                outcome = "unreachable"
            elif new_state is PaneState.AUTH_REQUIRED:
                outcome = "auth-required"
            elif new_state is PaneState.FORBIDDEN:
                outcome = "forbidden"
            elif new_state is PaneState.ERROR:
                # Generic ``ProviderError`` — without this branch the
                # boot chain stalls the full 90-s ``wait_for`` budget
                # instead of moving to "errored — trying next". The
                # friendly map at ``_friendly_outcome`` already carries
                # an ``"error"`` key.
                outcome = "error"
            if outcome is not None:
                self._attempt_future.set_result(outcome)

    def _on_connection_list_changed(self, msg: object) -> None:
        """Hub subscriber: drop deleted connection names from the
        unreachable set AND reload any pane bound to a changed connection."""
        if not isinstance(msg, ConnectionListChangedMessage):
            return
        if msg.change == "deleted":
            for name in msg.names:
                self._app_ctx.unreachable_connections.discard(("s3-compatible", name))
        # Schedule pane reload for affected connections (immediate, not
        # deferred). Skip on 'added' — new connections aren't bound yet.
        if msg.change == "added":
            return
        self.run_worker(
            self._reload_panes_for(msg.names, deleted=(msg.change == "deleted")),
            exclusive=True,
            group="settings-reload",
        )

    async def _reload_panes_for(self, names: tuple[str, ...], *, deleted: bool) -> None:
        """Walk both panes; rebind any pane bound to a connection in ``names``."""
        dual = self._dual_pane()
        if dual is None:
            return
        for pane in (dual.left, dual.right):  # type: ignore[attr-defined]
            key = pane.current_connection_key
            if key is None:
                continue
            _, pane_name = key
            if pane_name not in names:
                continue
            if deleted:
                await self._rebind_pane_to_local(pane)
            else:
                try:
                    conn = self._app_ctx.connection_resolver.resolve(pane_name)
                except Exception:
                    await self._rebind_pane_to_local(pane)
                else:
                    await self._rebind_pane_to_connection(pane, conn)

    def _on_hub_message_pane_state(self, msg: object) -> None:
        """Hub subscriber: route PaneVM state changes to the reachability set.

        Reads ``PaneVM.current_connection_key`` directly — the key is stored
        atomically inside ``swap_provider`` BEFORE ``_reload()`` runs, so
        there is no race between the state-change notification and the key
        update (Bug 1 fix). The subscription is established BEFORE
        ``switch_service`` in ``on_mount`` so the initial UNREACHABLE
        state-change is never missed (Bug 2 fix).
        """
        from vmx import PropertyChangedMessage

        from aws_tui.vm.file_manager.pane_vm import PaneVM

        if not isinstance(msg, PropertyChangedMessage):
            return
        if msg.property_name != "state":
            return
        if not isinstance(msg.sender_object, PaneVM):
            return
        sender_vm = msg.sender_object
        key = sender_vm.current_connection_key
        if key is None:
            return  # local pane — never tracked
        self._on_pane_state_changed(kind=key[0], name=key[1], new_state=sender_vm.state)

    def _on_hub_message_cursor(self, msg: object) -> None:
        """Hub subscriber: when ANY PaneVM emits a cursor / entries
        change, recompute which Commands chips are disabled based on
        the focused pane's cursor target. Today's only rule: the
        ``..`` parent row disables ``pane.copy`` and ``pane.delete``
        (no source to copy/delete — the parent reference is
        navigation-only). EMR panes are out of scope; their cursor
        target is a job-run, all chips stay enabled.
        """
        from vmx import PropertyChangedMessage

        from aws_tui.vm.file_manager.pane_vm import PaneVM

        if not isinstance(msg, PropertyChangedMessage):
            return
        if msg.property_name not in {"cursor_index", "viewmodel", "entries"}:
            return
        if not isinstance(msg.sender_object, PaneVM):
            return
        self._recompute_hint_disables()

    def _recompute_hint_disables(self) -> None:
        """Push a fresh disabled-action set to the HintLegendVM based
        on the focused pane's current cursor target. Safe to call at
        any time — no-ops on EMR / Settings (no DualPaneVM mounted).
        """
        dual = self._dual_pane()
        if dual is None:
            # No file-pane context — leave whatever the EMR / Settings
            # service set is the source of truth. Don't add disables.
            self._app_ctx.root_vm.chrome.hint_legend.set_disabled_actions(frozenset())
            return
        pane = getattr(dual, "focused_pane", None)
        if pane is None:
            self._app_ctx.root_vm.chrome.hint_legend.set_disabled_actions(frozenset())
            return
        cursor_idx = getattr(pane, "cursor_index", 0)
        entries = getattr(pane, "filtered_entries", ()) or getattr(pane, "entries", ())
        target = entries[cursor_idx] if 0 <= cursor_idx < len(entries) else None
        target_name = getattr(getattr(target, "entry", target), "name", None)
        if target_name == "..":
            self._app_ctx.root_vm.chrome.hint_legend.set_disabled_actions(
                frozenset({"pane.copy", "pane.delete"})
            )
        else:
            self._app_ctx.root_vm.chrome.hint_legend.set_disabled_actions(frozenset())

    def _on_nav_selection_changed(self, msg: object) -> None:
        """Hub subscriber: route NavMenuVM selected_id changes to the content host.

        When "settings" is selected, calls ``ContentHostVM.set_content`` with
        ``settings_vm`` and mounts ``SettingsView`` in the content host.
        For any other service id, rebuilds the DualPane-based S3 view.
        """
        from vmx import PropertyChangedMessage

        from aws_tui.vm.nav_menu_vm import NavMenuVM

        if not isinstance(msg, PropertyChangedMessage):
            return
        if msg.property_name != "selected_id":
            return
        if not isinstance(msg.sender_object, NavMenuVM):
            return
        # Skip the seed selected_id change that on_mount fires while
        # priming the initial service. on_mount drives that mount
        # synchronously via _mount_initial_service_view; if we ALSO
        # spawn a mount worker here, the two race against the same
        # #content-host and one silently clobbers the other → blank
        # screen at startup.
        if self._boot_in_flight:
            return
        ctx = self._app_ctx
        selected = ctx.root_vm.services_menu.selected_id
        if selected is None:
            return
        # Push the active service id down to the HintLegend so the
        # bottom Commands pane re-renders its service-specific chips
        # (S3 → copy/delete/swap-src; EMR → switch-app/refresh; …).
        # The legend's right-hand globals (themes/help/quit) are
        # independent of this.
        ctx.root_vm.chrome.hint_legend.set_current_service(selected)
        # Serialize the two mount workers via an exclusive worker
        # group so a rapid Settings → S3 → Settings toggle can't race
        # against itself. Without exclusivity, the awaited
        # ``switch_service`` inside ``_mount_service_view`` would
        # interleave with a later ``_mount_settings_view`` task: the
        # service worker would resume after the settings worker
        # replaced ContentHost.current, read the now-SettingsVM, and
        # try to wrap it in a DualPane — which then crashes with
        # ``AttributeError: 'SettingsVM' object has no attribute 'left'``
        # (observed on Windows py3.11 in CI; rarer but possible
        # everywhere). ``exclusive=True`` + a shared group name makes
        # Textual cancel any in-flight worker in the group before
        # starting the new one.
        if selected == SETTINGS_NAV_ID:
            self.run_worker(self._mount_settings_view(), exclusive=True, group="content-mount")
        else:
            # Re-use the S3 content if it's already hosted; switch_service
            # is idempotent on the same service_id.
            self.run_worker(
                self._mount_service_view(selected), exclusive=True, group="content-mount"
            )

    async def _mount_settings_view(self) -> None:
        """Swap the content host to show SettingsView.

        Build a fresh :class:`SettingsVM` per mount. The previous
        singleton pattern crashed on the second Settings switch:
        ``ContentHostVM.set_content`` calls ``vm.dispose()`` on the
        outgoing VM and ``vm.construct()`` on the incoming one, so
        after the first Settings → S3 swap the singleton was in
        ``Disposed`` state and re-mounting raised
        ``StatusTransitionError('Cannot construct from state Disposed.')``.
        ``SettingsVM.dispose()`` does NOT cascade to its
        :class:`S3ConnectionsVM` child (per ``SettingsVM.dispose``),
        so the shared connection list/selection state survives across
        rebuilds — only the thin ComponentVM wrapper is recreated.
        """
        from aws_tui.vm.settings.settings_vm import SettingsVM

        ctx = self._app_ctx
        settings_vm = SettingsVM(s3=ctx.s3_connections_vm, hub=ctx.hub, dispatcher=ctx.dispatcher)
        try:
            await ctx.root_vm.content_host.set_content(settings_vm, service_id=SETTINGS_NAV_ID)
        except Exception as exc:
            ctx.log_sink.error(
                "app.mount_settings_view.set_content_failed",
                error=str(exc),
                error_type=type(exc).__name__,
            )
            return
        try:
            host = self.query_one("#content-host", Container)
            # ``await`` both the remove and the mount so a cancelled
            # worker (e.g. user toggling Settings ↔ S3 rapidly) can't
            # leave the host with a half-attached widget. Without the
            # awaits, ``mount`` returns an AwaitMount that's never
            # consumed — if the worker is cancelled between remove and
            # mount completion, the pending mount still fires after the
            # next worker's remove_children, leaving BOTH widgets in
            # the DOM.
            await host.remove_children()
            await host.mount(
                SettingsView(
                    vm=settings_vm,
                    hub=ctx.hub,
                    focus_coordinator=ctx.focus_coordinator,
                )
            )
        except Exception as exc:
            ctx.log_sink.error(
                "app.mount_settings_view.mount_failed",
                error=str(exc),
                error_type=type(exc).__name__,
            )

    async def _mount_service_view(self, service_id: str) -> None:
        """Swap the content host to show the DualPane for ``service_id``.

        Always runs the full ``switch_service`` + ``remove_children``
        + ``mount`` sequence. An earlier ``current_id == service_id``
        early-return shortcut was meant to skip a no-op rebuild on a
        same-service reselect, but it raced the Settings ↔ S3 toggle:
        if the user clicked S3 → Settings → S3 quickly enough, the
        Settings worker could be cancelled mid-``set_content`` before
        ``current_id`` flipped to ``"settings"``, leaving
        ``current_id == "s3"`` while the DOM held a partially-mounted
        ``SettingsView``. The next ``_mount_service_view("s3")`` would
        then hit the shortcut, see ``current_id == "s3"``, check
        ``host.query(DualPane)`` (which returned nothing because
        SettingsView was in the DOM), and re-mount using whatever
        ``content_host.current`` was — possibly the disposed DualPaneVM
        from the original mount, possibly the SettingsVM, possibly
        nothing — and the user saw "S3 selected but Settings still on
        screen". ``switch_service`` is idempotent on a matching
        ``service_id``, so always running it is cheap; always tearing
        the host children down and re-mounting forces the DOM into
        agreement with the VM regardless of which intermediate state
        a cancelled worker may have left behind.
        """
        ctx = self._app_ctx
        # Honor the boot-chain's local-fallback decision on
        # Settings → S3 toggle. Without this, re-mounting the s3
        # DualPane re-attempts every chain-failed connection
        # (60s+ wait per unreachable s3 endpoint) and lands on the
        # same UNREACHABLE error pane the chain just navigated past.
        # The session-sticky flag is cleared by ``r`` retries on a
        # pane that actually succeed (see ``_on_pane_state_changed``).
        if (
            service_id == "s3"
            and self._chain_resolved_to_local
            and self._chain_initial_conn is not None
        ):
            await self._mount_local_only_dual_pane(
                initial_conn=self._chain_initial_conn,
                reason="chain-exhausted",
            )
            return
        try:
            await ctx.root_vm.switch_service(service_id)
        except Exception as exc:
            ctx.log_sink.error(
                "app.mount_service_view.switch_service_failed",
                service_id=service_id,
                error=str(exc),
                error_type=type(exc).__name__,
            )
            return
        try:
            host = self.query_one("#content-host", Container)
            current_vm = ctx.root_vm.content_host.current
            if current_vm is not None:
                await host.remove_children()
                if service_id == "emr-serverless":
                    await host.mount(
                        EmrServerlessPage(
                            current_vm,
                            hub=ctx.hub,
                            focus_coordinator=ctx.focus_coordinator,
                            id="content-emr-page",
                        )
                    )
                else:
                    await host.mount(DualPane(current_vm, hub=ctx.hub, id="content-dual-pane"))
        except Exception as exc:
            ctx.log_sink.error(
                "app.mount_service_view.mount_failed",
                service_id=service_id,
                error=str(exc),
                error_type=type(exc).__name__,
            )

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
        ask_task: asyncio.Task[CrashChoice] | None = None
        try:
            ask_task = asyncio.create_task(crash_vm.ask())
            await self.push_screen(CrashModal(crash_vm, hub=ctx.hub))
            return await ask_task
        finally:
            # Cancel + drain ask_task on the cancellation path BEFORE
            # disposing the VM. Without this, an outer cancellation at
            # ``push_screen`` or ``ask_task`` raises CancelledError —
            # crash_vm.dispose() runs but ask_task is never explicitly
            # cancelled nor awaited. CrashVM.dispose happens to resolve
            # the future via set_result(QUIT) today, but the coupling
            # is fragile (a future refactor switching to ``cancel()``
            # would orphan ask_task), and a race window exists where
            # the outer cancel lands BEFORE ask_task creates the
            # future — then dispose's short-circuit leaves ask_task
            # raising a "modal disposed" RuntimeError nobody awaits,
            # triggering asyncio's "never retrieved" warning. Same
            # R38 family.
            if ask_task is not None and not ask_task.done():
                ask_task.cancel()
                with contextlib.suppress(Exception, asyncio.CancelledError):
                    await ask_task
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
        # Include asyncio.CancelledError in the suppress — without it
        # an in-flight CancelledError (a BaseException, not an
        # Exception) on the aclose_all_clients await would cascade
        # past every subsequent cleanup step below this block,
        # skipping log_sink.flush()/.close() + the four reactive
        # subscription disposes. Shutdown must NOT be cancellable
        # mid-stream.
        with contextlib.suppress(Exception, asyncio.CancelledError):
            await ctx.aws_session.aclose_all_clients()
        with contextlib.suppress(Exception):
            ctx.log_sink.flush()
            ctx.log_sink.close()
        with contextlib.suppress(Exception):
            if self._pane_state_sub is not None:
                self._pane_state_sub.dispose()
                self._pane_state_sub = None
        with contextlib.suppress(Exception):
            if self._connection_list_sub is not None:
                self._connection_list_sub.dispose()
                self._connection_list_sub = None
        with contextlib.suppress(Exception):
            if self._nav_selection_sub is not None:
                self._nav_selection_sub.dispose()
                self._nav_selection_sub = None
        with contextlib.suppress(Exception):
            if self._cursor_sub is not None:
                self._cursor_sub.dispose()
                self._cursor_sub = None
        with contextlib.suppress(Exception):
            # Currently-hosted SettingsVM (if any) is disposed by the
            # ContentHostVM tree teardown via ``root_vm.shutdown()``.
            ctx.s3_connections_vm.dispose()
        # One suppress PER dispose — bundling them under a single
        # suppress lets the first raise short-circuit every later
        # call (most notably the FocusCoordinatorVM cleanup the
        # comment below specifically defends against).
        with contextlib.suppress(Exception):
            ctx.command_palette_vm.dispose()
        with contextlib.suppress(Exception):
            ctx.quick_look_vm.dispose()
        with contextlib.suppress(Exception):
            ctx.confirm_vm.dispose()
        with contextlib.suppress(Exception):
            ctx.transfers_vm.dispose()
        with contextlib.suppress(Exception):
            ctx.root_vm.dispose()
        with contextlib.suppress(Exception):
            # FocusCoordinatorVM lives on the AppContext top-level,
            # not under root_vm, so root_vm.dispose() doesn't reach
            # it. Without an explicit call the inner ComponentVM +
            # Subject leak on every shutdown.
            ctx.focus_coordinator.dispose()
        with contextlib.suppress(Exception):
            # In demo mode, cancel any in-flight clone state-machine tasks
            # so asyncio doesn't emit "Task was destroyed but it is pending"
            # warnings on exit.  The InMemoryEmr singleton is shared across
            # connection switches within the same AppContext, so we dispose
            # it here (on app shutdown) rather than in EmrServerlessPageVM.
            if ctx.demo_emr is not None:
                ctx.demo_emr.dispose()


def main() -> None:
    """Run the Textual app with unhandled-exception capture.

    Invoked by the ``aws-tui`` console script and ``python -m aws_tui``.
    If the app surfaces an unhandled exception, ``_handle_exception``
    writes a crash dump under ``~/.cache/aws-tui/crash/`` and the
    saved :class:`CrashReport` is printed here before the exception is
    re-raised so the user knows where the dump landed.

    Recognises one CLI flag: ``--version`` prints the version + demo
    status and exits without launching the UI.
    """
    from aws_tui.demo import is_demo_mode_enabled

    demo = is_demo_mode_enabled()

    if "--version" in sys.argv:
        status = "enabled" if demo else "disabled"
        # Match the pip convention: ``project-name 0.8.0``.
        print(f"aws-tui {__version__} (demo: {status})")
        return

    app = AwsTuiApp(context=build_app_context(demo=demo))
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
