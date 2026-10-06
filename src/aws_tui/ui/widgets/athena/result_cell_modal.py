"""Read-only inspection of an original loaded result value."""

from __future__ import annotations

from typing import ClassVar

from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Horizontal, Vertical
from textual.events import Click
from textual.screen import ModalScreen
from textual.widgets import Static, TextArea

from aws_tui.ui.widgets.modal_button import ModalButton


class AthenaResultCellModal(ModalScreen[None]):
    DEFAULT_CSS: ClassVar[str] = """
    AthenaResultCellModal { align: center middle; }
    AthenaResultCellModal > Vertical {
        width: 92; max-width: 94%; height: 85%; padding: 1 2;
    }
    AthenaResultCellModal #athena-cell-metadata { height: auto; max-height: 3; }
    AthenaResultCellModal TextArea { height: 1fr; margin: 1 0; }
    AthenaResultCellModal Horizontal { height: 3; align-horizontal: right; }
    """
    BINDINGS: ClassVar[list[BindingType]] = [Binding("escape", "close", "Close")]

    def __init__(self, value: str | None, *, row: int, column: int, name: str) -> None:
        super().__init__()
        status = "null" if value is None else "empty string" if value == "" else "string"
        self._metadata = Static(
            f"Loaded row {row + 1} · column {column + 1} · {status}\n{name}",
            id="athena-cell-metadata",
            markup=False,
        )
        self._body = TextArea(
            "" if value is None else value,
            id="athena-cell-value",
            read_only=True,
            soft_wrap=True,
        )
        self._invalidated = False

    def compose(self) -> ComposeResult:
        with Vertical(classes="modal-frame"):
            yield Static("Athena cell · loaded results", classes="modal-title", markup=False)
            yield self._metadata
            yield self._body
            with Horizontal(classes="modal-footer"):
                yield ModalButton("Close", button_id="close")

    def on_mount(self) -> None:
        self._body.focus()

    def action_close(self) -> None:
        self.dismiss(None)

    def action_commit_focused(self) -> None:
        if isinstance(self.focused, ModalButton):
            self.focused.press()

    def on_click(self, event: Click) -> None:
        if isinstance(event.widget, ModalButton):
            self.action_close()

    def invalidate(self) -> None:
        self._invalidated = True
        self._body.clear()
        self._metadata.update("Result is no longer available")
        if self.is_attached and self.app.screen is self:
            self.dismiss(None)

    def on_screen_resume(self) -> None:
        if self._invalidated and self.is_attached and self.app.screen is self:
            self.dismiss(None)
