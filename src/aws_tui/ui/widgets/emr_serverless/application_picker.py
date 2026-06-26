"""ApplicationPicker — top-strip dropdown for the EMR page.

Trigger button + layered OptionList. Pressing `a` (page-level
binding) calls ``toggle_open``; clicking the trigger does the same.
Selecting a row in the OptionList commits via ``vm.select(app_id)``
and closes the popover. Esc cancels."""

from __future__ import annotations

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
    ApplicationPicker > OptionList {
        layer: dropdown;
        width: 40;
        max-height: 16;
        offset: 0 3;
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
        # Any click bubbles up via ``self`` so toggling is convenient
        # — the OptionList rows have their own click → action_commit
        # via the option-selected message handler below.
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

    def _trigger_label(self) -> str:
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
        return f"🔥 {match.name} {glyph}{match.state.value}"

    def _build_options(self) -> list[Option]:
        return [
            Option(
                prompt=f"🔥 {a.name} {_APP_STATE_GLYPH.get(a.state, '?')}{a.state.value}",
                id=a.id,
            )
            for a in self._vm.applications
        ]


__all__ = ["ApplicationPicker"]
