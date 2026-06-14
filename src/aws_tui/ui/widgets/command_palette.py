"""CommandPalette modal screen bound to :class:`CommandPaletteVM`.

Renders as a centered overlay: a prompt-line input on top of a vertical
list of palette items. ``Up`` / ``Down`` move the selection; ``Enter``
executes; ``Esc`` closes.
"""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Input, Static
from vmx import Message, MessageHub

from aws_tui.ui.widgets._subscriber import HubSubscriberMixin
from aws_tui.vm.chrome.command_palette_vm import CommandPaletteVM


class CommandPaletteItem(Static):
    """Single row inside the command palette list."""

    def __init__(
        self,
        label: str,
        category: str,
        *,
        is_selected: bool = False,
    ) -> None:
        classes = "palette-item"
        if is_selected:
            classes += " -selected"
        super().__init__(self._format(label, category), classes=classes)
        self._label = label
        self._category = category

    @staticmethod
    def _format(label: str, category: str) -> str:
        return f"{label}    {category}"


class CommandPalette(HubSubscriberMixin, ModalScreen[None]):
    """Modal palette screen."""

    BINDINGS = [  # noqa: RUF012 - Textual expects a class-level mutable
        ("escape", "close", "Close"),
        ("enter", "execute", "Execute"),
        ("up", "move_up", "Up"),
        ("down", "move_down", "Down"),
    ]

    def __init__(
        self,
        vm: CommandPaletteVM,
        *,
        hub: MessageHub[Message],
    ) -> None:
        super().__init__()
        self._vm: CommandPaletteVM = vm
        self._hub: MessageHub[Message] = hub

    @property
    def vm(self) -> CommandPaletteVM:
        return self._vm

    def compose(self) -> ComposeResult:
        with Vertical(id="palette-container"):
            yield Static(":", classes="palette-prompt")
            yield Input(placeholder="type a command...", id="palette-input")
            yield Vertical(id="palette-list", classes="palette-list")

    def on_mount(self) -> None:
        self._rebuild_list()
        self.subscribe_to_vm(
            hub=self._hub,
            vm=self._vm,
            on_property_changed=self._on_vm_property_changed,
        )
        self.query_one("#palette-input", Input).focus()

    def on_unmount(self) -> None:
        self.unsubscribe_from_vm()

    def on_input_changed(self, event: Input.Changed) -> None:
        self._vm.filter_text = event.value

    # ── Actions ─────────────────────────────────────────────────────────────

    def action_close(self) -> None:
        self._vm.close_command.execute()
        self.dismiss(None)

    def action_execute(self) -> None:
        self._vm.execute_selected_command.execute()
        self.dismiss(None)

    def action_move_up(self) -> None:
        self._vm.move_selection_command.execute(-1)

    def action_move_down(self) -> None:
        self._vm.move_selection_command.execute(1)

    # ── Internal ────────────────────────────────────────────────────────────

    def _on_vm_property_changed(self, property_name: str) -> None:
        if property_name in {"filtered_entries", "selected_index"}:
            self.call_after_refresh(self._rebuild_list)

    def _rebuild_list(self) -> None:
        try:
            container = self.query_one("#palette-list", Vertical)
        except Exception:
            return
        for child in list(container.children):
            child.remove()
        entries = self._vm.filtered_entries
        selected = self._vm.selected_index
        for idx, entry in enumerate(entries):
            container.mount(
                CommandPaletteItem(entry.label, entry.category, is_selected=(idx == selected))
            )


__all__ = ["CommandPalette", "CommandPaletteItem"]
