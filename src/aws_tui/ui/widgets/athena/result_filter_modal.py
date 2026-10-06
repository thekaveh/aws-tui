"""Literal substring filter for already loaded Athena rows."""

from __future__ import annotations

from typing import ClassVar

from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Horizontal, Vertical
from textual.events import Click
from textual.message import Message
from textual.screen import ModalScreen
from textual.widgets import Input, Static

from aws_tui.ui.widgets.modal_button import ModalButton


class _PrivateFilterInput(Input):
    """Keep authoritative text in the field, out of outgoing event diagnostics."""

    def post_message(self, message: Message) -> bool:
        if isinstance(message, (Input.Changed, Input.Submitted, Input.Blurred)):
            message.value = ""
        return super().post_message(message)


class AthenaResultFilterModal(ModalScreen[str | None]):
    """None cancels; a string applies (the empty string clears)."""

    DEFAULT_CSS: ClassVar[str] = """
    AthenaResultFilterModal { align: center middle; }
    AthenaResultFilterModal > Vertical {
        width: 76; max-width: 94%; height: auto; max-height: 90%; padding: 1 2;
    }
    AthenaResultFilterModal Static { height: auto; }
    AthenaResultFilterModal Input { margin: 1 0; }
    AthenaResultFilterModal Horizontal { height: 3; align-horizontal: right; }
    """
    BINDINGS: ClassVar[list[BindingType]] = [Binding("escape", "cancel", "Cancel")]

    def __init__(self, current: str) -> None:
        super().__init__()
        self._field = _PrivateFilterInput(current, id="athena-result-filter")
        self._invalidated = False

    def compose(self) -> ComposeResult:
        with Vertical(classes="modal-frame"):
            yield Static("Filter loaded Athena results", classes="modal-title", markup=False)
            yield Static("Case-insensitive literal substring · loaded rows only", markup=False)
            yield self._field
            with Horizontal(classes="modal-footer"):
                yield ModalButton("Cancel", button_id="cancel")
                yield ModalButton("Clear", button_id="clear")
                yield ModalButton("Apply", button_id="apply", classes="-primary")

    def on_mount(self) -> None:
        self._field.focus()

    def action_cancel(self) -> None:
        self.dismiss(None)

    def action_apply(self) -> None:
        if not self._invalidated:
            self.dismiss(self._field.value)

    def action_commit_focused(self) -> None:
        if isinstance(self.focused, Input):
            self.action_apply()
        elif isinstance(self.focused, ModalButton):
            self.focused.press()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        event.stop()
        if self.focused is self._field:
            self.action_apply()

    def on_click(self, event: Click) -> None:
        if not isinstance(event.widget, ModalButton):
            return
        if event.widget.button_id == "apply":
            self.action_apply()
        elif event.widget.button_id == "clear" and not self._invalidated:
            self.dismiss("")
        else:
            self.action_cancel()

    def invalidate(self) -> None:
        self._invalidated = True
        self._field.value = ""
        if self.is_attached and self.app.screen is self:
            self.dismiss(None)

    def on_screen_resume(self) -> None:
        if self._invalidated and self.is_attached and self.app.screen is self:
            self.dismiss(None)
