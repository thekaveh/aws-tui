"""SettingsModal — themed settings overlay with sidebar nav."""

from __future__ import annotations

from textual import on
from textual.app import ComposeResult
from textual.containers import Container, Horizontal, Vertical
from textual.events import Click
from textual.screen import ModalScreen
from textual.widgets import ListItem, ListView, Static
from vmx import Message, MessageHub

from aws_tui.ui.widgets.modal_button import ModalButton
from aws_tui.ui.widgets.settings._placeholder_panel import _PlaceholderPanel
from aws_tui.ui.widgets.settings.s3_connections_panel import S3ConnectionsPanel
from aws_tui.vm.settings.settings_vm import SettingsVM

_SECTION_LABELS: dict[str, str] = {
    "connections": "Connections",
    "themes": "Themes",
    "keymap": "Keymap",
}


class SettingsModal(ModalScreen[None]):
    """Modal that hosts the App Settings shell.

    Sidebar entries:
      ▸ Connections        — active (sub-project A)
        Themes (soon)      — disabled until sub-project B lands
        Keymap (soon)      — disabled until sub-project C lands
    """

    BINDINGS = [  # noqa: RUF012
        ("escape", "close", "Close"),
    ]

    def __init__(self, *, vm: SettingsVM, hub: MessageHub[Message]) -> None:
        super().__init__()
        self._vm: SettingsVM = vm
        self._hub: MessageHub[Message] = hub

    @property
    def vm(self) -> SettingsVM:
        return self._vm

    def compose(self) -> ComposeResult:
        with Container(id="settings-frame"):
            yield Static("Settings", id="settings-title")
            with Horizontal(id="settings-content"):
                with Vertical(id="settings-sidebar"):
                    yield self._build_sidebar()
                with Vertical(id="settings-body"):
                    yield self._build_body()
            with Horizontal(id="settings-footer"):
                yield ModalButton("close", button_id="settings-close-btn")

    def _build_sidebar(self) -> ListView:
        items: list[ListItem] = []
        for section_id in self._vm.SECTIONS:
            label = _SECTION_LABELS[section_id]
            suffix = "" if section_id in self._vm.ENABLED else " (soon)"
            item = ListItem(Static(f"{label}{suffix}"), id=f"section-{section_id}")
            if section_id not in self._vm.ENABLED:
                item.disabled = True
                item.add_class("-disabled")
            items.append(item)
        view = ListView(*items, id="section-list")
        # Initial cursor = active section
        try:
            view.index = self._vm.SECTIONS.index(self._vm.active_section)
        except ValueError:
            view.index = 0
        return view

    def _build_body(self) -> S3ConnectionsPanel | _PlaceholderPanel:
        section = self._vm.active_section
        if section == "connections":
            return S3ConnectionsPanel(vm=self._vm.s3, hub=self._hub)
        return _PlaceholderPanel(section_id=section)

    @on(ListView.Highlighted)
    def _on_section_highlighted(self, event: ListView.Highlighted) -> None:
        if event.item is None or event.item.disabled:
            return
        item_id = event.item.id or ""
        section_id = item_id.removeprefix("section-")
        if section_id not in self._vm.SECTIONS:
            return
        if section_id == self._vm.active_section:
            return
        self._vm.change_section(section_id)
        self.run_worker(self._swap_body(), exclusive=True)

    async def _swap_body(self) -> None:
        body = self.query_one("#settings-body", Vertical)
        await body.remove_children()
        await body.mount(self._build_body())

    def on_click(self, event: Click) -> None:
        """Handle close button clicks.

        ModalButton is a Static subclass, not a Button — it emits Click
        events rather than Button.Pressed. Walk up the widget tree to find
        the ModalButton and dispatch by button_id.
        """
        node: object | None = event.widget if hasattr(event, "widget") else None
        while node is not None:
            if isinstance(node, ModalButton):
                if node.button_id == "settings-close-btn":
                    event.stop()
                    self.action_close()
                return
            node = getattr(node, "parent", None)

    def action_close(self) -> None:
        self.dismiss()


__all__ = ["SettingsModal"]
