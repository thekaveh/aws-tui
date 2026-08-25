"""Screen-overlaid option list with explicit dismissal events."""

from __future__ import annotations

from typing import ClassVar

from textual.binding import Binding, BindingType
from textual.events import Blur
from textual.message import Message
from textual.widgets import OptionList


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


__all__ = ["OverlayOptionList"]
