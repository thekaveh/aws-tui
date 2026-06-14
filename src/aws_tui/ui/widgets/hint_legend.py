"""HintLegend widget — bottom contextual hint row bound to :class:`HintLegendVM`.

Each chip is rendered as ``<key in accent> <label in dim>`` with two spaces
between chips. The widget subscribes to ``PropertyChangedMessage`` on the
hub for the VM's ``actions`` property and re-renders on change.
"""

from __future__ import annotations

from rich.text import Text
from textual.widget import Widget
from vmx import Message, MessageHub

from aws_tui.ui.widgets._subscriber import HubSubscriberMixin
from aws_tui.vm.chrome.hint_legend_vm import HintLegendVM


class HintLegend(HubSubscriberMixin, Widget):
    """Bottom hint-legend row."""

    DEFAULT_CSS = """
    HintLegend {
        height: 1;
    }
    """

    def __init__(
        self,
        vm: HintLegendVM,
        *,
        hub: MessageHub[Message],
        id: str | None = None,
        classes: str | None = None,
    ) -> None:
        super().__init__(id=id, classes=classes)
        self._vm: HintLegendVM = vm
        self._hub: MessageHub[Message] = hub

    @property
    def vm(self) -> HintLegendVM:
        return self._vm

    def render(self) -> Text:
        text = Text()
        chips = self._vm.actions
        if not chips:
            return text
        for index, chip in enumerate(chips):
            if index > 0:
                text.append("  ", style="dim")
            text.append(chip.key_label, style="bold cyan")
            text.append(" ")
            text.append(chip.action_label, style="dim")
        return text

    def on_mount(self) -> None:
        self.subscribe_to_vm(
            hub=self._hub,
            vm=self._vm,
            on_property_changed=self._on_vm_property_changed,
        )

    def on_unmount(self) -> None:
        self.unsubscribe_from_vm()

    def _on_vm_property_changed(self, property_name: str) -> None:
        if property_name == "actions":
            self.refresh()


__all__ = ["HintLegend"]
