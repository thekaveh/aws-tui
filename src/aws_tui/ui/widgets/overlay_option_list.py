"""Screen-overlaid option list with explicit dismissal events."""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar

from textual.binding import Binding, BindingType
from textual.events import Blur
from textual.message import Message
from textual.widget import Widget
from textual.widgets import OptionList


@dataclass(slots=True)
class PickerFocusIntent:
    """Monotonic guard for picker focus callbacks deferred by Textual."""

    epoch: int = 0

    def advance(self) -> int:
        self.epoch += 1
        return self.epoch

    def is_current(self, epoch: int) -> bool:
        return epoch == self.epoch


@dataclass(slots=True)
class PickerOpenIntent:
    """Page-owned newest-open intent for deferred picker reconciliation."""

    epoch: int = 0
    desired: Widget | None = None

    def observe(self, picker: Widget, is_open: bool) -> int:
        if is_open:
            self.desired = picker
        elif self.desired is picker:
            self.desired = None
        self.epoch += 1
        return self.epoch

    def cancel(self) -> None:
        self.desired = None
        self.epoch += 1

    def is_current(self, epoch: int) -> bool:
        return epoch == self.epoch


class OverlayOptionList(OptionList):
    """Option list that asks its owner to dismiss on Escape or focus loss."""

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("escape", "dismiss", "Close selector", show=False, priority=True),
    ]

    class Dismissed(Message):
        def __init__(self, *, lost_focus: bool) -> None:
            super().__init__()
            self.lost_focus = lost_focus

    def action_dismiss(self) -> None:
        self.post_message(self.Dismissed(lost_focus=False))

    def on_blur(self, _event: Blur) -> None:
        if self.display:
            self.post_message(self.Dismissed(lost_focus=True))


__all__ = ["OverlayOptionList", "PickerFocusIntent", "PickerOpenIntent"]
