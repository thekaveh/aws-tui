"""ApplicationPicker — top-strip dropdown for the EMR page.

Trigger button + layered OptionList. Pressing `a` (page-level
binding) calls ``toggle_open``; clicking the trigger does the same.
Selecting a row in the OptionList commits via ``vm.select(app_id)``
and closes the popover. Esc cancels."""

from __future__ import annotations

import contextlib
from typing import ClassVar

from reactivex.abc import DisposableBase
from textual.app import ComposeResult
from textual.binding import BindingType
from textual.containers import Horizontal
from textual.events import Click
from textual.widget import Widget
from textual.widgets import OptionList, Static
from textual.widgets.option_list import Option
from vmx import Message, MessageHub, PropertyChangedMessage

from aws_tui.domain.emr_serverless import ApplicationState
from aws_tui.vm.emr_serverless.applications_vm import ApplicationsVM

_APP_STATE_GLYPH: dict[ApplicationState, str] = {
    ApplicationState.CREATING: "◌",
    ApplicationState.CREATED: "○",
    ApplicationState.STARTING: "◐",
    ApplicationState.STARTED: "●",
    ApplicationState.STOPPING: "◑",
    ApplicationState.STOPPED: "○",
    ApplicationState.TERMINATED: "✗",
}


class ApplicationPicker(Widget):
    """Top-strip application selector.

    Visually a trigger button (closed) that swaps to an OptionList
    when opened. Theming is in the per-theme ``.tcss``; this widget
    owns only structural rules."""

    DEFAULT_CSS: ClassVar[str] = """
    ApplicationPicker {
        width: 1fr;
        height: 1fr;
        layout: horizontal;
    }
    ApplicationPicker > .app-trigger {
        width: 1fr;
        height: 1fr;
        padding: 0 1;
        content-align: left middle;
        text-style: bold;
    }
    /* The OptionList is mounted to the Screen on open (not to
       this widget) so it escapes the bordered ``emr-app-box``'s
       3-row clip. ``layer: dropdown`` lifts it above the body
       widgets; explicit screen-space ``offset`` is set in
       ``_open_dropdown`` based on the picker's region.
       Background / border come from per-theme tcss so the
       dropdown picks up the active accent at runtime. */
    #emr-app-picker-dropdown {
        layer: dropdown;
        width: 50;
        max-height: 16;
    }
    """

    BINDINGS: ClassVar[list[BindingType]] = [
        ("escape", "close", "Close"),
        ("enter", "commit", "Pick"),
    ]

    def __init__(
        self,
        vm: ApplicationsVM,
        *,
        hub: MessageHub[Message],
        id: str | None = None,
        classes: str | None = None,
    ) -> None:
        super().__init__(id=id, classes=classes)
        self._vm: ApplicationsVM = vm
        self._hub: MessageHub[Message] = hub
        self._sub: DisposableBase | None = None
        # The OptionList lives outside this widget's compose so it
        # can be mounted to the Screen on open (and unmounted on
        # close) — that's the only way to escape the bordered
        # ``emr-app-box``'s 3-row clip region. See ``_open_dropdown``.
        self._dropdown: OptionList | None = None

    def compose(self) -> ComposeResult:
        # Trigger only. The OptionList overlay is owned by
        # ``_open_dropdown`` / ``_close_dropdown`` (mounted to
        # ``self.app.screen`` on open). Wrapping the trigger in a
        # Horizontal preserves the original layout the page CSS +
        # snapshot baselines were authored against.
        with Horizontal():
            yield Static(self._trigger_label(), classes="app-trigger")

    def on_mount(self) -> None:
        self._sub = self._hub.messages.subscribe(on_next=self._on_hub_message)

    def on_unmount(self) -> None:
        self._close_dropdown()
        if self._sub is not None:
            self._sub.dispose()
            self._sub = None

    # ── Public API ──────────────────────────────────────────────────────────

    def toggle_open(self) -> None:
        if "-open" in self.classes:
            self._close_dropdown()
        else:
            self._open_dropdown()

    def action_close(self) -> None:
        self._close_dropdown()

    def action_commit(self) -> None:
        if self._dropdown is None:
            return
        opts = self._dropdown
        if opts.highlighted is None:
            return
        opt = opts.get_option_at_index(opts.highlighted)
        if opt.id is not None:
            self._vm.select(opt.id)
        self._close_dropdown()

    # ── Internal ────────────────────────────────────────────────────────────

    def on_click(self, event: Click) -> None:
        # Any click bubbles up via ``self`` so toggling is convenient
        # — the OptionList rows post their own ``OptionSelected``
        # which routes through the on_option_list_* handler below.
        if (
            event.widget is not None
            and getattr(event.widget, "id", None) == "emr-app-picker-dropdown"
        ):
            return
        self.toggle_open()

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        if event.option.id is not None:
            self._vm.select(event.option.id)
        self._close_dropdown()

    def _on_hub_message(self, msg: object) -> None:
        if not isinstance(msg, PropertyChangedMessage):
            return
        if msg.property_name not in {"applications", "selected_id", "state"}:
            return
        self.call_after_refresh(self._refresh_trigger)
        self.call_after_refresh(self._refresh_dropdown_options)

    def _refresh_trigger(self) -> None:
        try:
            trigger = self.query_one(".app-trigger", Static)
        except Exception:
            return
        trigger.update(self._trigger_label())

    def _refresh_dropdown_options(self) -> None:
        if self._dropdown is None:
            return
        self._dropdown.clear_options()
        for opt in self._build_options():
            self._dropdown.add_option(opt)

    def _open_dropdown(self) -> None:
        """Mount the OptionList to the Screen and position it just
        below the picker's bordered box.

        Screen-mounting (rather than yielding the OptionList as a
        child of this widget) escapes the bordered apps box's 3-row
        clip — the prior in-flow + ``layer: dropdown`` approach
        from PR #83 still had the popover clipped by the parent's
        overflow rect, so the user saw nothing. ``layer: dropdown``
        is z-order only; it doesn't escape clipping."""
        if self._dropdown is not None:
            return
        self.add_class("-open")
        opt_list = OptionList(*self._build_options(), id="emr-app-picker-dropdown")
        self._dropdown = opt_list
        try:
            self.app.screen.mount(opt_list)
        except Exception:
            # Screen unavailable (test harness without ``run_test`` or
            # mid-shutdown) — fall back to silent no-op.
            self._dropdown = None
            self.remove_class("-open")
            return
        # Anchor below the picker's bordered apps box. ``self.region``
        # is the picker's on-screen rect; the box border adds 1 row
        # below us, so y + height + 1.
        with contextlib.suppress(Exception):
            region = self.region
            opt_list.styles.offset = (region.x, region.y + region.height + 1)
        with contextlib.suppress(Exception):
            opt_list.focus()

    def _close_dropdown(self) -> None:
        self.remove_class("-open")
        if self._dropdown is None:
            return
        with contextlib.suppress(Exception):
            self._dropdown.remove()
        self._dropdown = None

    def _trigger_label(self) -> str:
        """Render the trigger row.

        Format: ``🔥  <name>  ·  <glyph> <STATE>``. The em-dot
        separator + double-spaces give the state pill room to
        breathe (user feedback: "the way STOPPED is being displayed
        right in front of the current app is not formatted enough"
        — pre-fix it was ``🔥 etl-pipeline-1 ●STOPPED`` with the
        glyph and the state name jammed together)."""
        apps = self._vm.applications
        sid = self._vm.selected_id
        if not apps:
            return "(no application)"
        if sid is None:
            return "(select application)"
        match = next((a for a in apps if a.id == sid), None)
        if match is None:
            return "(select application)"
        glyph = _APP_STATE_GLYPH.get(match.state, "?")
        return f"🔥  {match.name}  ·  {glyph} {match.state.value}"

    def _build_options(self) -> list[Option]:
        return [
            Option(
                prompt=(f"🔥  {a.name}  ·  {_APP_STATE_GLYPH.get(a.state, '?')} {a.state.value}"),
                id=a.id,
            )
            for a in self._vm.applications
        ]


__all__ = ["ApplicationPicker"]
