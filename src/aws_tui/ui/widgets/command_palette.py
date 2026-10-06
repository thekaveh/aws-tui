"""CommandPalette modal screen bound to :class:`CommandPaletteVM`.

Renders as a centered overlay: a prompt-line input on top of a vertical
list of palette items. ``Up`` / ``Down`` move the selection; ``Enter``
executes; ``Esc`` closes.
"""

from __future__ import annotations

from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Input, Static
from vmx import Message, MessageHub

from aws_tui.ui.widgets._subscriber import HubSubscriberMixin
from aws_tui.vm.chrome.action_catalog import (
    ActionPresentation,
    format_effective_keys,
    literal_display,
    scope_label,
)
from aws_tui.vm.chrome.command_palette_vm import CommandPaletteVM, PaletteEntry


class CommandPaletteItem(Static):
    """Single row inside the command palette list."""

    action_id: str
    presentation: ActionPresentation

    def __init__(
        self,
        entry: PaletteEntry,
        *,
        active_service_id: str | None = None,
        is_selected: bool = False,
    ) -> None:
        self.action_id = entry.id
        self.presentation = entry
        classes = "palette-item" + (" -selected" if is_selected else "")
        text = Text(format_effective_keys(entry.effective_keys))
        text.append("  ")
        text.append(literal_display(entry.label))
        text.append("  " + scope_label(entry.service_ids, active_service_id), style="dim")
        super().__init__(text, classes=classes)


class CommandPalette(HubSubscriberMixin, ModalScreen[None]):
    """Modal palette screen."""

    DEFAULT_CSS = """
    CommandPalette .palette-list {
        overflow-y: auto;
    }
    """

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

    async def on_mount(self) -> None:
        await self._rebuild_list()
        self.subscribe_to_vm(
            hub=self._hub,
            vm=self._vm,
            property_names=("filtered_entries", "selected_index"),
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
        command = self._vm.execute_selected_command
        if not command.can_execute():
            return
        command.execute()
        if not self._vm.is_open:
            self.dismiss(None)

    def action_move_up(self) -> None:
        self._vm.move_selection_command.execute(-1)

    def action_move_down(self) -> None:
        self._vm.move_selection_command.execute(1)

    # ── Internal ────────────────────────────────────────────────────────────

    def _on_vm_property_changed(self, property_name: str) -> None:
        if property_name in {"filtered_entries", "selected_index"}:
            self.call_after_refresh(self._rebuild_list)

    async def _rebuild_list(self) -> None:
        try:
            container = self.query_one("#palette-list", Vertical)
        except Exception:
            return
        await container.remove_children()
        entries = self._vm.filtered_entries
        selected = self._vm.selected_index
        if entries:
            await container.mount(
                *(
                    CommandPaletteItem(
                        entry,
                        active_service_id=self._vm.active_service_id,
                        is_selected=(idx == selected),
                    )
                    for idx, entry in enumerate(entries)
                )
            )
        self.call_after_refresh(self._scroll_selected_into_view)

    def _scroll_selected_into_view(self) -> None:
        if self.is_mounted:
            selected = next(iter(self.query(".palette-item.-selected")), None)
            if selected is not None:
                selected.scroll_visible(animate=False, immediate=True)


__all__ = ["CommandPalette", "CommandPaletteItem"]
