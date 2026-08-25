"""One-row command hints bound to :class:`HintLegendVM`."""

from __future__ import annotations

from collections import Counter

from rich.cells import cell_len
from textual.app import ComposeResult
from textual.containers import Horizontal
from textual.events import Resize
from textual.widget import Widget
from textual.widgets import Static
from vmx import Message, MessageHub

from aws_tui.ui.widgets._subscriber import HubSubscriberMixin
from aws_tui.vm.chrome.hint_legend_vm import HintAction, HintLegendVM


def _action_width(action: HintAction) -> int:
    return cell_len(f"[{action.key_label}] {action.action_label}") + 1


def _fit_actions(actions: tuple[HintAction, ...], width: int) -> tuple[HintAction, ...]:
    """Return the source-ordered action hints that fit in one row."""
    regular = tuple(action for action in actions if not action.overflow_only)
    if sum(_action_width(action) for action in regular) <= width:
        return regular

    visible = list(actions)
    protected_ids = {"app.command_palette", "app.quit"}

    while sum(_action_width(action) for action in visible) > width:
        tab_counts = Counter(action.key_label.casefold() for action in visible)
        removable = [
            (index, action)
            for index, action in enumerate(visible)
            if action.action_id not in protected_ids
        ]
        if not removable:
            break
        index, _action = min(
            removable,
            key=lambda item: (
                0
                if item[1].key_label.casefold() == "tab"
                and tab_counts[item[1].key_label.casefold()] > 1
                else 1,
                -item[1].priority,
                0 if not item[1].enabled else 1,
                -item[0],
            ),
        )
        visible.pop(index)

    return tuple(visible)


class _HintChip(Horizontal):
    """Content-sized, non-focusable presentation for one hint action."""

    can_focus = False

    def __init__(self, action: HintAction) -> None:
        disabled_suffix = " -disabled" if not action.enabled else ""
        key = Static(
            f"[{action.key_label}]",
            classes=f"hint-key{disabled_suffix}",
            markup=False,
        )
        label = Static(
            action.action_label,
            classes=f"hint-label{disabled_suffix}",
        )
        super().__init__(key, label, classes="hint-chip")
        self.action = action
        self._key = key
        self._label = label
        self.tooltip = action.tooltip

    def retire(self) -> None:
        """Remove this chip from layout and semantic queries before pruning."""
        self.display = False
        self.remove_class("hint-chip")
        self.tooltip = None
        self._key.update("")
        self._label.update("")


class HintLegend(HubSubscriberMixin, Widget):
    """Bottom command legend that always renders as one compact row."""

    DEFAULT_CSS = """
    HintLegend {
        height: 3;
        min-height: 3;
        margin: 0 1 1 1;
        border-title-align: left;
    }
    HintLegend > #hint-strip {
        width: 1fr;
        height: 1;
        layout: horizontal;
        overflow: hidden hidden;
    }
    HintLegend .hint-chip {
        width: auto;
        min-width: 0;
        height: 1;
        margin-right: 1;
    }
    HintLegend .hint-key {
        width: auto;
        height: 1;
        text-style: bold;
    }
    HintLegend .hint-key.-disabled {
        text-style: dim;
    }
    HintLegend .hint-label {
        width: auto;
        height: 1;
        padding-left: 1;
    }
    HintLegend .hint-label.-disabled {
        text-style: dim;
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
        self._vm = vm
        self._hub = hub
        self._rebuild_generation = 0
        self._rebuild_scheduled = False
        self.border_title = "Commands"

    @property
    def vm(self) -> HintLegendVM:
        return self._vm

    def compose(self) -> ComposeResult:
        yield Horizontal(id="hint-strip")

    def on_mount(self) -> None:
        self.subscribe_to_vm(
            hub=self._hub,
            vm=self._vm,
            property_names=("actions",),
            on_property_changed=self._on_vm_property_changed,
        )
        self._request_rebuild()

    def on_unmount(self) -> None:
        self._rebuild_generation += 1
        self._rebuild_scheduled = False
        self.unsubscribe_from_vm()

    def on_resize(self, _event: Resize) -> None:
        self._request_rebuild()

    def _on_vm_property_changed(self, property_name: str) -> None:
        if property_name == "actions":
            self._request_rebuild()

    def _all_actions(self) -> tuple[HintAction, ...]:
        return tuple(self._vm.actions) + tuple(self._vm.global_actions)

    def _request_rebuild(self) -> None:
        self._rebuild_generation += 1
        self._schedule_rebuild()

    def _schedule_rebuild(self) -> None:
        if self._rebuild_scheduled or not self.is_attached:
            return
        self._rebuild_scheduled = self.call_after_refresh(self._rebuild_chips)

    async def _rebuild_chips(self) -> None:
        if not self.is_attached:
            self._rebuild_scheduled = False
            return
        started_generation = self._rebuild_generation
        replacement_completed = False
        try:
            strip = self.query_one("#hint-strip", Horizontal)
            actions = _fit_actions(self._all_actions(), self.content_region.width)
            old_chips = tuple(child for child in strip.children if isinstance(child, _HintChip))
            with self.app.batch_update():
                mounted = strip.mount(*(_HintChip(action) for action in actions))
                for chip in old_chips:
                    chip.retire()
                removed = strip.remove_children(old_chips)

            await mounted
            if not self.is_attached:
                return
            await removed
            replacement_completed = True
        finally:
            self._rebuild_scheduled = False
            if (
                self.is_attached
                and replacement_completed
                and started_generation != self._rebuild_generation
            ):
                self._schedule_rebuild()


__all__ = ["HintLegend"]
