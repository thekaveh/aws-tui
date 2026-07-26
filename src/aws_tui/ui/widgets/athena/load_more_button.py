from __future__ import annotations

from typing import ClassVar

from textual.widgets import Button

from aws_tui.vm.file_manager.pane_vm import PaneState


class AthenaLoadMoreButton(Button):
    DEFAULT_CSS: ClassVar[str] = """
    AthenaLoadMoreButton {
        width: 5;
        min-width: 5;
        height: 3;
        margin: 0;
    }
    """

    def __init__(self, *, id: str, tooltip: str) -> None:
        super().__init__(
            "↓",
            id=id,
            classes="athena-load-more",
            compact=True,
            flat=True,
            tooltip=tooltip,
        )
        self._default_tooltip = tooltip
        self.display = False

    def sync(
        self,
        *,
        has_more: bool,
        busy: bool,
        state: PaneState,
        error_text: str | None,
    ) -> None:
        self.display = has_more or busy
        self.disabled = not has_more or busy
        self.label = "…" if busy else "↓"
        self.tooltip = error_text or self._default_tooltip
        self.set_class(state is PaneState.FORBIDDEN, "-warning")
        self.set_class(state is PaneState.ERROR, "-error")


__all__ = ["AthenaLoadMoreButton"]
