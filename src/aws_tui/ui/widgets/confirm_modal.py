"""ConfirmModal screen bound to :class:`ConfirmationVM`.

Centered modal with title + body lines + confirm/cancel buttons.
``Enter`` accepts (confirm), ``Esc`` cancels.
"""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Container, Horizontal
from textual.screen import ModalScreen
from textual.widgets import Button, Static
from vmx import Message, MessageHub

from aws_tui.vm.chrome.confirm_vm import ConfirmationVM, ConfirmRequest


class ConfirmModal(ModalScreen[bool]):
    """Yes/no modal driven by the VM's RelayCommands."""

    BINDINGS = [  # noqa: RUF012
        ("escape", "cancel", "Cancel"),
        ("enter", "confirm", "Confirm"),
    ]

    def __init__(
        self,
        vm: ConfirmationVM,
        request: ConfirmRequest,
        *,
        hub: MessageHub[Message],
    ) -> None:
        super().__init__()
        self._vm: ConfirmationVM = vm
        self._request: ConfirmRequest = request
        self._hub: MessageHub[Message] = hub
        if request.danger:
            self.add_class("-danger")

    @property
    def vm(self) -> ConfirmationVM:
        return self._vm

    @property
    def request(self) -> ConfirmRequest:
        return self._request

    def compose(self) -> ComposeResult:
        with Container():
            yield Static(self._request.title, classes="modal-title")
            for line in self._request.body_lines:
                yield Static(line, classes="modal-body")
            with Horizontal(classes="modal-footer"):
                yield Button(self._request.cancel_label, id="cancel-btn")
                yield Button(self._request.confirm_label, id="confirm-btn", variant="error")

    def action_cancel(self) -> None:
        self._vm.cancel_command.execute()
        self.dismiss(False)

    def action_confirm(self) -> None:
        self._vm.confirm_command.execute()
        self.dismiss(True)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "confirm-btn":
            self.action_confirm()
        elif event.button.id == "cancel-btn":
            self.action_cancel()


__all__ = ["ConfirmModal"]
