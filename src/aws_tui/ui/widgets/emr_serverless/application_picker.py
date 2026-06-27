"""ApplicationPicker — top-strip application selector for the EMR page.

Inline-expanding dropdown. The picker uses ``height: auto`` so it
grows to wrap whatever children are visible: just the trigger row
when closed, trigger + OptionList when open. The parent
``emr-app-box`` is ``height: auto, min-height: 3`` so it grows
in lockstep; the sibling ``JobRunsPane`` (``height: 1fr``)
shrinks to make room.

Why not a floating overlay: the prior layered-overlay approaches
(PR #83 declaring ``dropdown`` on Screen, PR #85 mounting the
OptionList directly to the Screen) both broke the popover —
layers are z-order only (don't escape parent clipping in PR #83)
and Screen-mount put the popover after Screen's vertical-flow
children (so it ended up below the Commands pane in PR #85).
The inline-expanding pattern is simpler and reliable: the
OptionList stays a normal child of the picker, no layers, no
absolute positioning, no cross-widget message routing.
"""

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
    """Top-strip application selector — inline-expanding."""

    DEFAULT_CSS: ClassVar[str] = """
    ApplicationPicker {
        width: 1fr;
        height: auto;
        min-height: 3;
        layout: vertical;
    }
    /* The trigger row is always 3 cells tall (matches the apps-box
       minimum). Wrapped in a Horizontal so its width takes the full
       picker; the Static fills that Horizontal. */
    ApplicationPicker > Horizontal {
        width: 1fr;
        height: 3;
    }
    ApplicationPicker > Horizontal > .app-trigger {
        width: 1fr;
        height: 3;
        padding: 0 1;
        content-align: left middle;
        text-style: bold;
    }
    /* OptionList is collapsed by default; ``-open`` flips display
       to block AND the picker's ``height: auto`` grows to wrap the
       newly-visible OptionList. Parent ``emr-app-box`` grows in
       lockstep (its ``height: auto, min-height: 3`` lets it expand
       up to the column's available space; the sibling JobRunsPane
       with ``height: 1fr`` shrinks to make room). */
    ApplicationPicker > OptionList {
        width: 1fr;
        height: auto;
        max-height: 16;
        display: none;
    }
    ApplicationPicker.-open > OptionList {
        display: block;
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

    def compose(self) -> ComposeResult:
        # Trigger row + OptionList both as children of the picker.
        # The OptionList is hidden by default via ``display: none``
        # and revealed when the picker gains the ``-open`` class.
        with Horizontal():
            yield Static(self._trigger_label(), classes="app-trigger")
        yield OptionList(*self._build_options(), id="app-options")

    def on_mount(self) -> None:
        self._sub = self._hub.messages.subscribe(on_next=self._on_hub_message)

    def on_unmount(self) -> None:
        if self._sub is not None:
            self._sub.dispose()
            self._sub = None

    # ── Public API ──────────────────────────────────────────────────────────

    def toggle_open(self) -> None:
        if "-open" in self.classes:
            self.remove_class("-open")
        else:
            self.add_class("-open")
            # Focus the dropdown so arrow keys / Enter / Esc are
            # routed there immediately. ``call_after_refresh`` waits
            # for the layout-pass that the ``-open`` class triggered
            # so the OptionList is laid out and focusable.
            self.call_after_refresh(self._focus_dropdown)
            self._refresh_options()

    def action_close(self) -> None:
        self.remove_class("-open")

    def action_commit(self) -> None:
        try:
            opts = self.query_one("#app-options", OptionList)
        except Exception:
            return
        if opts.highlighted is None:
            return
        opt = opts.get_option_at_index(opts.highlighted)
        if opt.id is not None:
            self._vm.select(opt.id)
        self.remove_class("-open")

    # ── Internal ────────────────────────────────────────────────────────────

    def on_click(self, event: Click) -> None:
        # Click on the trigger row toggles open/closed. Click on a
        # row inside the dropdown is handled by Textual's OptionList
        # which posts ``OptionSelected`` — see the handler below.
        if event.widget is not None and getattr(event.widget, "id", None) == "app-options":
            return
        self.toggle_open()

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        if event.option.id is not None:
            self._vm.select(event.option.id)
        self.remove_class("-open")

    def _on_hub_message(self, msg: object) -> None:
        if not isinstance(msg, PropertyChangedMessage):
            return
        if msg.property_name not in {"applications", "selected_id", "state"}:
            return
        self.call_after_refresh(self._refresh_trigger)
        self.call_after_refresh(self._refresh_options)

    def _refresh_trigger(self) -> None:
        try:
            trigger = self.query_one(".app-trigger", Static)
        except Exception:
            return
        trigger.update(self._trigger_label())

    def _refresh_options(self) -> None:
        try:
            opts = self.query_one("#app-options", OptionList)
        except Exception:
            return
        opts.clear_options()
        for opt in self._build_options():
            opts.add_option(opt)

    def _focus_dropdown(self) -> None:
        with contextlib.suppress(Exception):
            opts = self.query_one("#app-options", OptionList)
            opts.focus()

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
