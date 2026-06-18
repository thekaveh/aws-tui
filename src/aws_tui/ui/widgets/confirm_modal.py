r"""ConfirmModal screen bound to :class:`ConfirmationVM`.

Custom-styled, bound-box modal with title + body lines + confirm/cancel
"buttons". The buttons are Static widgets (not :class:`Button`) because
the Textual default Button ships with heavy built-in CSS (ansi colors,
``\$border-blurred``, etc.) that fights theme overrides and makes the
labels overflow on narrow widths. Static + a small CSS class gives us
predictable layout + clean theme adoption.

Enter accepts (confirm), Esc cancels. The App-level priority bindings
for arrows + enter are forwarded to ``action_confirm`` / ``action_cancel``
from ``AwsTuiApp`` (see ``_forward_to_modal``).
"""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Container, Horizontal
from textual.events import Click
from textual.screen import ModalScreen
from textual.widgets import Static
from vmx import Message, MessageHub

from aws_tui.ui.widgets.modal_button import ModalButton as _ModalButton
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
            for path_entry in self._request.paths:
                yield Static(path_entry.label, classes="modal-path-label")
                yield Static(path_entry.path, classes="modal-path-value")
            for line in self._request.body_lines:
                yield Static(line, classes="modal-body")
            with Horizontal(classes="modal-footer"):
                yield _ModalButton(
                    f"  {self._request.cancel_label}  ",
                    button_id="cancel",
                )
                primary_classes = "-danger" if self._request.danger else "-primary"
                yield _ModalButton(
                    f"  {self._request.confirm_label}  ",
                    button_id="confirm",
                    classes=primary_classes,
                )

    def action_cancel(self) -> None:
        self._vm.cancel_command.execute()
        self.dismiss(False)

    def action_confirm(self) -> None:
        self._vm.confirm_command.execute()
        self.dismiss(True)

    def on_click(self, event: Click) -> None:
        # Walk up from the click target to find the button (if any).
        node: object | None = event.widget if hasattr(event, "widget") else None
        while node is not None:
            if isinstance(node, _ModalButton):
                if node.button_id == "confirm":
                    self.action_confirm()
                else:
                    self.action_cancel()
                return
            node = getattr(node, "parent", None)


__all__ = ["ConfirmModal"]
