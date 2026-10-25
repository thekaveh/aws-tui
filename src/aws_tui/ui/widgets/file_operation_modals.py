"""Collect file-operation intent; providers and mutation workers live elsewhere."""

from __future__ import annotations

from typing import ClassVar

from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.events import Click
from textual.screen import ModalScreen
from textual.widgets import Input, Static

from aws_tui.domain.filesystem import ProviderError
from aws_tui.ui.widgets.modal_button import ModalButton
from aws_tui.vm.file_manager.file_names import require_entry_name


def _button(label: str, identity: str, *, disabled: bool = False) -> ModalButton:
    button = ModalButton(label, button_id=identity, disabled=disabled)
    button.id = f"file-operation-{identity}"
    return button


class _FileOperationModal(ModalScreen[str | None]):
    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("escape", "cancel", "Cancel"),
        Binding("enter", "commit_focused", "Choose"),
        Binding("tab", "focus_next", show=False),
        Binding("shift+tab", "focus_prev", show=False),
    ]
    DEFAULT_CSS = """
    _FileOperationModal { align: center middle; }
    _FileOperationModal > Vertical {
        width: 76; max-width: 95%; height: auto; max-height: 90%;
        border: solid $accent; background: $surface; padding: 1 2;
    }
    _FileOperationModal Static { height: auto; }
    _FileOperationModal .modal-title { text-style: bold; }
    _FileOperationModal .modal-footer { height: 3; align: center middle; }
    _FileOperationModal .modal-footer > ModalButton {
        height: 3; min-width: 10; width: 1fr; margin: 0; padding: 0 1;
    }
    """

    def action_cancel(self) -> None:
        self.dismiss(None)

    def action_focus_next(self) -> None:
        self.focus_next()

    def action_focus_prev(self) -> None:
        self.focus_previous()

    def action_commit_focused(self) -> None:
        focused = self.focused
        if isinstance(focused, ModalButton):
            focused.press()

    def on_click(self, event: Click) -> None:
        if isinstance(event.widget, ModalButton) and not event.widget.disabled:
            event.stop()
            self._choose(event.widget.button_id)

    def _choose(self, identity: str) -> None:
        if identity == "cancel":
            self.action_cancel()
        else:
            self.dismiss(identity)


class FileNameModal(_FileOperationModal):
    """Return one validated basename with its exact user-entered spelling."""

    AUTO_FOCUS = "#file-name-input"
    DEFAULT_CSS = """
    FileNameModal .file-name-location { height: auto; max-height: 5; }
    FileNameModal Input { margin: 1 0; }
    FileNameModal #file-name-error { min-height: 1; color: $error; }
    """

    def __init__(self, *, title: str, location: str, initial: str = "") -> None:
        super().__init__()
        self._title = title
        self._location = location
        self._initial = initial

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Static(self._title, classes="modal-title", markup=False)
            with VerticalScroll(classes="file-name-location"):
                yield Static(self._location, markup=False)
            yield Input(self._initial, id="file-name-input")
            yield Static("", id="file-name-error", markup=False)
            with Horizontal(classes="modal-footer"):
                yield _button("Review", "review")
                yield _button("Cancel", "cancel")

    def on_input_submitted(self, event: Input.Submitted) -> None:
        event.stop()
        self._submit()

    def action_commit_focused(self) -> None:
        if isinstance(self.focused, Input):
            self._submit()
        else:
            super().action_commit_focused()

    def _choose(self, identity: str) -> None:
        if identity == "review":
            self._submit()
        else:
            super()._choose(identity)

    def _submit(self) -> None:
        field = self.query_one(Input)
        try:
            require_entry_name(field.value)
        except ProviderError as error:
            self.query_one("#file-name-error", Static).update(str(error))
            field.focus()
            return
        self.dismiss(field.value)


class MoveReviewModal(_FileOperationModal):
    """Review captured endpoints and return Move, single-entry Rename, or Cancel."""

    AUTO_FOCUS = "#file-operation-move"
    DEFAULT_CSS = """
    MoveReviewModal > Vertical { height: 90%; }
    MoveReviewModal #file-move-paths { height: 1fr; }
    MoveReviewModal #file-move-review { height: auto; }
    """

    def __init__(
        self,
        *,
        source_identity: str,
        destination_identity: str,
        source_paths: tuple[str, ...],
        destination_path: str,
    ) -> None:
        super().__init__()
        self._source_identity = source_identity
        self._destination_identity = destination_identity
        self._source_paths = tuple(source_paths)
        self._destination_path = destination_path

    def compose(self) -> ComposeResult:
        count = len(self._source_paths)
        lines = [
            f"{count} {'item' if count == 1 else 'items'}",
            f"Source connection: {self._source_identity}",
            "Source paths:",
            *self._source_paths,
            f"Destination connection: {self._destination_identity}",
            f"Destination directory: {self._destination_path}",
            "Conflict policy: ERROR — stop on conflict; never overwrite.",
        ]
        with Vertical():
            yield Static("Review move", classes="modal-title", markup=False)
            with VerticalScroll(id="file-move-paths"):
                yield Static("\n".join(lines), id="file-move-review", markup=False)
            with Horizontal(classes="modal-footer"):
                yield _button("Move", "move")
                yield _button("Rename", "rename", disabled=count != 1)
                yield _button("Cancel", "cancel")


__all__ = ["FileNameModal", "MoveReviewModal"]
