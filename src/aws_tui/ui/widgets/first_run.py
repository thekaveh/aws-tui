"""Actionable first-run presentation, driven only by discovery snapshots."""

from __future__ import annotations

from pathlib import Path
from typing import ClassVar

from rich.text import Text
from textual.app import ComposeResult
from textual.containers import VerticalScroll
from textual.dom import DOMNode
from textual.events import Click, Key
from textual.message import Message as TextualMessage
from textual.widget import Widget
from textual.widgets import Input, Static
from vmx import Message, MessageHub

from aws_tui.ui.widgets.modal_button import ModalButton
from aws_tui.ui.widgets.settings.connection_form import (
    ConnectionFormCancelled,
    ConnectionFormInline,
)
from aws_tui.vm.connection_discovery import ConnectionDiscoveryDisplay, ConnectionDisplay

# App/theme styles outrank widget defaults. Share the scoped structural override
# with the production App and its chrome-shaped snapshot harness.
FIRST_RUN_FORM_CSS = """
FirstRunView ConnectionFormInline Input { height: 2; border-top: none; margin-bottom: 0; }
"""

INVALID_CONFIGURATION = "Invalid configuration. Fix the configuration file, then Retry discovery."
NO_CONNECTIONS = "No AWS profiles or S3-compatible connections found. Add a connection or set up an AWS profile, then Retry discovery."
PROBE_FAILED = "Credential probe failed. Refresh credentials outside aws-tui, then select the connection again."


class ConnectionChoice(Widget, can_focus=True):
    """A plain name/origin row. Focus alone never selects a connection."""

    DEFAULT_CSS: ClassVar[str] = """
    ConnectionChoice { height: 2; width: 1fr; padding: 0 1; }
    ConnectionChoice:focus { color: $accent; text-style: underline; }
    """

    def __init__(self, *, connection: ConnectionDisplay) -> None:
        super().__init__()
        self.connection_name = connection.name
        self._source = connection.source

    def render(self) -> Text:
        name = Text(self.connection_name.replace("\r", r"\r").replace("\n", r"\n"))
        if self.content_size.width:
            name.truncate(self.content_size.width, overflow="ellipsis")
        name.append("\n")
        name.append(self._source)
        return name

    def on_key(self, event: Key) -> None:
        if event.key in {"enter", "space"} and isinstance(self.parent, FirstRunConnectionList):
            event.stop()
            self.parent.post_message(
                FirstRunConnectionList.ConnectionSelected(self.connection_name)
            )


class FirstRunConnectionList(VerticalScroll):
    """Keyboard-scrollable setup and connection choices for the optional rail."""

    DEFAULT_CSS: ClassVar[str] = """
    FirstRunConnectionList { height: 1fr; width: 1fr; }
    FirstRunConnectionList > ModalButton { width: 1fr; min-width: 0; margin: 0; padding: 0; }
    """

    class SetupRequested(TextualMessage):
        """Return to connection setup, including when discovery is empty."""

    class ConnectionSelected(TextualMessage):
        def __init__(self, name: str) -> None:
            super().__init__()
            self.name = name

    def __init__(self, *, snapshot: ConnectionDiscoveryDisplay) -> None:
        super().__init__()
        self._connections = snapshot.connections

    def compose(self) -> ComposeResult:
        yield ModalButton("Connection setup", button_id="first-run-setup")
        for connection in self._connections:
            yield ConnectionChoice(connection=connection)

    def activate_focused(self) -> bool:
        focused = self.app.focused
        if focused is None or self not in focused.ancestors_with_self:
            return False
        if isinstance(focused, ConnectionChoice):
            if not focused.is_disabled:
                self.post_message(self.ConnectionSelected(focused.connection_name))
        elif isinstance(focused, ModalButton):
            focused.press()
        return True

    def on_key(self, event: Key) -> None:
        if event.key not in {"up", "down", "k", "j"}:
            return
        event.stop()
        self._move_focus(-1 if event.key in {"up", "k"} else 1)

    def action_cursor_up(self) -> None:
        self._move_focus(-1)

    def action_cursor_down(self) -> None:
        self._move_focus(1)

    def _move_focus(self, step: int) -> None:
        controls = list(self.query("ModalButton, ConnectionChoice"))
        focused = self.app.focused
        if focused not in controls:
            return
        index = min(max(controls.index(focused) + step, 0), len(controls) - 1)
        controls[index].focus()

    def on_click(self, event: Click) -> None:
        node: DOMNode | None = event.widget
        while node is not None and node is not self:
            if isinstance(node, ConnectionChoice):
                event.stop()
                if not node.is_disabled:
                    self.post_message(self.ConnectionSelected(node.connection_name))
                return
            if isinstance(node, ModalButton):
                event.stop()
                if not node.is_disabled:
                    self.post_message(self.SetupRequested())
                return
            node = node.parent


class FirstRunView(VerticalScroll):
    """Local setup guidance and the existing validated connection form."""

    DEFAULT_CSS: ClassVar[str] = """
    FirstRunView { width: 1fr; height: 1fr; padding: 1 2; }
    FirstRunView > Static { height: auto; margin-bottom: 1; }
    FirstRunView > ModalButton { width: auto; margin: 0 0 1 0; }
    """

    class RetryRequested(TextualMessage):
        """Refresh local metadata without opening any source."""

    def __init__(
        self, config_path: Path, hub: MessageHub[Message], id: str = "content-first-run"
    ) -> None:
        super().__init__(id=id)
        self._config_path = config_path
        self._hub = hub
        self._status = NO_CONNECTIONS
        self._busy = False

    def compose(self) -> ComposeResult:
        yield Static("Connection setup", markup=False)
        yield Static(self._status, id="first-run-status", markup=False)
        yield Static(
            f"Application configuration: {self._config_path}\nDiscovery reads local configuration. Select a connection to open it.\nConnection guide: https://thekaveh.github.io/aws-tui/connections/",
            markup=False,
        )
        yield ModalButton("Add S3-compatible connection", button_id="first-run-add")
        yield ModalButton("AWS profile setup", button_id="first-run-aws")
        yield ModalButton("Retry discovery", button_id="first-run-retry")
        yield Static(
            "Set up credentials outside aws-tui with aws configure or aws configure sso.\nThen choose Retry discovery. These commands are instructions; aws-tui does not run them.",
            id="first-run-aws-guidance",
            markup=False,
        )
        yield Static(
            "Save and open saves this connection and explicitly selects it to open S3.",
            id="first-run-save-guidance",
            markup=False,
        )
        yield ConnectionFormInline(hub=self._hub, submit_label="Save and open")

    def on_mount(self) -> None:
        self.query_one("#first-run-aws-guidance").display = False
        self.query_one("#first-run-save-guidance").display = False

    def show_discovery(self, snapshot: ConnectionDiscoveryDisplay) -> None:
        if snapshot.invalid_sources:
            self._status = INVALID_CONFIGURATION
        elif not snapshot.connections:
            self._status = NO_CONNECTIONS
        else:
            self._status = "Select a connection to open it, or add a connection below."
        self.query_one("#first-run-status", Static).update(self._status)

    def show_error(self, text: str) -> None:
        self._status = text
        self.query_one("#first-run-status", Static).update(text)

    def set_busy(self, busy: bool) -> None:
        self._busy = busy
        self.query_one(ConnectionFormInline).disabled = busy
        for button in self.query(ModalButton):
            if button.button_id.startswith("first-run-"):
                button.disabled = busy

    def focus_default(self) -> None:
        if self._busy:
            return
        form = self.query_one(ConnectionFormInline)
        if form.has_class("-open"):
            controls = [i for i in form.query(Input) if not i.disabled]
            if controls:
                controls[0].focus()
            return
        self.query(ModalButton).first().focus()

    def cycle_focus(self, *, reverse: bool = False) -> bool:
        form = self.query_one(ConnectionFormInline)
        if form.cycle_focus(reverse=reverse):
            return True
        focused = self.app.focused
        if focused is None or not (
            self in focused.ancestors_with_self
            or any(isinstance(node, FirstRunConnectionList) for node in focused.ancestors_with_self)
        ):
            return False
        # Native screen traversal includes both the content actions and rail choices.
        if reverse:
            self.screen.focus_previous()
        else:
            self.screen.focus_next()
        return True

    def activate_focused(self) -> bool:
        focused = self.app.focused
        if focused is None:
            return self._busy
        if self not in focused.ancestors_with_self:
            return False
        if self._busy:
            return True
        if isinstance(focused, ModalButton):
            focused.press()
            return True
        if isinstance(focused, Input):
            return self.query_one(ConnectionFormInline).cycle_focus()
        return False

    def on_click(self, event: Click) -> None:
        node: DOMNode | None = event.widget
        while node is not None and node is not self:
            if isinstance(node, ModalButton) and node.button_id.startswith("first-run-"):
                event.stop()
                if self._busy or node.is_disabled:
                    return
                if node.button_id == "first-run-add":
                    self.query_one("#first-run-save-guidance").display = True
                    self.query_one(ConnectionFormInline).open_for_add()
                elif node.button_id == "first-run-aws":
                    self.query_one("#first-run-aws-guidance").display = True
                elif node.button_id == "first-run-retry":
                    self.post_message(self.RetryRequested())
                return
            node = node.parent

    def on_connection_form_cancelled(self, event: ConnectionFormCancelled) -> None:
        self.query_one("#first-run-save-guidance").display = False
        self.query_one("#first-run-status", Static).update(self._status)
        self.focus_default()
