"""Small local SQL prompts; none of these dialogs can execute a query."""

from __future__ import annotations

from typing import ClassVar

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Vertical
from textual.dom import DOMNode
from textual.message import Message
from textual.screen import ModalScreen
from textual.widgets import Input, OptionList, Static
from textual.widgets.option_list import Option
from vmx import Message as HubMessage
from vmx import MessageHub
from vmx.services.dispatcher import Dispatcher

from aws_tui.ui.widgets.confirm_modal import TextualDialogService
from aws_tui.vm.chrome.confirm_vm import ConfirmationVM, ConfirmRequest


async def ask_sql_confirmation(
    host: DOMNode, *, hub: MessageHub[HubMessage], dispatcher: Dispatcher, message: str
) -> bool:
    confirmation = ConfirmationVM(hub=hub, dispatcher=dispatcher)
    confirmation.construct()
    try:
        return await confirmation.ask(
            ConfirmRequest(title=message, confirm_label="Replace"),
            dialog_service=TextualDialogService(host.app, confirmation, hub=hub),
        )
    finally:
        confirmation.dispose()


class _SqlPrompt(ModalScreen[str | None]):
    def __init__(self) -> None:
        super().__init__()
        self._invalidated = False

    def invalidate(self) -> None:
        self._invalidated = True
        if self.is_attached and self.app.screen is self:
            self.dismiss(None)

    def on_screen_resume(self) -> None:
        if self._invalidated and self.is_attached and self.app.screen is self:
            self.dismiss(None)


class SqlChoiceModal(_SqlPrompt):
    BINDINGS: ClassVar[list[BindingType]] = [Binding("escape", "cancel", "Cancel")]
    DEFAULT_CSS: ClassVar[str] = """
    SqlChoiceModal { align: center middle; background: $background 60%; }
    SqlChoiceModal > Vertical { width: 76; max-width: 96%; height: auto; max-height: 90%; padding: 1 2; }
    SqlChoiceModal Static { height: auto; }
    SqlChoiceModal OptionList { height: auto; max-height: 14; min-height: 2; }
    """

    def __init__(
        self, title: str, choices: tuple[tuple[str, str], ...], *, detail: str = ""
    ) -> None:
        super().__init__()
        self._title, self._choices, self._detail = title, choices, detail

    def compose(self) -> ComposeResult:
        with Vertical(classes="modal-frame"):
            yield Static(self._title, id="athena-sql-title", classes="modal-title", markup=False)
            yield Static(self._detail, markup=False)
            yield OptionList(
                *(Option(Text(label), id=identity) for identity, label in self._choices),
                id="athena-sql-choices",
            )
            yield Static("Enter chooses · Esc cancels", markup=False)

    def on_mount(self) -> None:
        self.query_one(OptionList).focus()

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        event.stop()
        if not self._invalidated:
            self.dismiss(event.option.id)

    def action_commit_focused(self) -> None:
        self.query_one(OptionList).action_select()

    def action_cancel(self) -> None:
        self.dismiss(None)


class _PrivateSqlInput(Input):
    def post_message(self, message: Message) -> bool:
        if isinstance(message, (Input.Changed, Input.Submitted, Input.Blurred)):
            message.value = ""
        return super().post_message(message)


class SqlValueModal(_SqlPrompt):
    BINDINGS: ClassVar[list[BindingType]] = [Binding("escape", "cancel", "Cancel")]
    DEFAULT_CSS: ClassVar[str] = """
    SqlValueModal { align: center middle; background: $background 60%; }
    SqlValueModal > Vertical { width: 76; max-width: 96%; height: auto; padding: 1 2; }
    SqlValueModal Static { height: auto; }
    SqlValueModal Input { margin: 1 0; }
    """

    def __init__(self, title: str, *, value: str = "", detail: str = "") -> None:
        super().__init__()
        self._title, self._detail = title, detail
        self._field = _PrivateSqlInput(value, id="athena-sql-value", max_length=4096)

    def compose(self) -> ComposeResult:
        with Vertical(classes="modal-frame"):
            yield Static(self._title, classes="modal-title", markup=False)
            yield Static(self._detail, markup=False)
            yield self._field
            yield Static("Enter applies · Esc cancels", markup=False)

    def on_mount(self) -> None:
        self._field.focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        event.stop()
        self.action_commit_focused()

    def action_commit_focused(self) -> None:
        if not self._invalidated and self._field.value:
            self.dismiss(self._field.value)

    def invalidate(self) -> None:
        self._field.value = ""
        super().invalidate()

    def action_cancel(self) -> None:
        self.dismiss(None)
