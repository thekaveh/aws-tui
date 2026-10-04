"""Literal, read-only properties and full selected-value inspection."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from functools import partial
from typing import ClassVar

from reactivex.abc import DisposableBase
from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Horizontal, Vertical
from textual.events import Click
from textual.screen import ModalScreen
from textual.widgets import DataTable, Static, TextArea

from aws_tui.ui.widgets._worker import DeferredWorkerMixin
from aws_tui.ui.widgets.modal_button import ModalButton
from aws_tui.vm.file_manager.s3_object_details_vm import (
    ObjectDetailField,
    S3ObjectDetailsState,
    S3ObjectDetailsVM,
)


class S3ObjectDetailsModal(DeferredWorkerMixin, ModalScreen[None]):
    """A compact property table with a wrapping, lossless value viewer."""

    DEFAULT_CSS = """
    S3ObjectDetailsModal { align: center middle; }
    S3ObjectDetailsModal > Vertical { width: 104; max-width: 95%; height: 90%; padding: 1 2; border: solid $accent; background: $surface; }
    S3ObjectDetailsModal .details-title { height: auto; max-height: 2; text-style: bold; }
    S3ObjectDetailsModal .details-status { height: 1; }
    S3ObjectDetailsModal DataTable { height: 2fr; min-height: 4; }
    S3ObjectDetailsModal TextArea { height: 1fr; min-height: 3; border: solid $secondary; }
    S3ObjectDetailsModal TextArea:focus { border: solid $accent; }
    S3ObjectDetailsModal .details-value-label { height: 1; text-style: bold; }
    S3ObjectDetailsModal .modal-footer { height: 3; align: center middle; }
    """
    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("escape", "close", "Close"),
        Binding("ctrl+c", "copy", "Copy full value", priority=True),
    ]

    def __init__(
        self, vm: S3ObjectDetailsVM, *, copy_value: Callable[[str], Awaitable[None]]
    ) -> None:
        super().__init__()
        self._vm = vm
        self._copy_value = copy_value
        self._subscription: DisposableBase | None = None
        self._source_subscription: DisposableBase | None = None
        self._generation: int | None = None
        self._dismiss_requested = False

    @property
    def vm(self) -> S3ObjectDetailsVM:
        return self._vm

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Static(
                self.vm.title, id="s3-details-title", classes="details-title", markup=False
            )
            yield Static("Loading…", id="s3-details-status", classes="details-status", markup=False)
            yield DataTable(id="s3-details-fields", cursor_type="row", zebra_stripes=True)
            yield Static(
                "Full value",
                id="s3-details-value-label",
                classes="details-value-label",
                markup=False,
            )
            yield TextArea(id="s3-details-value", read_only=True, soft_wrap=True)
            with Horizontal(classes="modal-footer"):
                yield ModalButton("Copy · Ctrl+C", button_id="copy", disabled=True)
                yield ModalButton("Close · Esc", button_id="close")

    def on_mount(self) -> None:
        if self._dismiss_requested or self.vm.state is S3ObjectDetailsState.CLOSED:
            return
        table = self.query_one(DataTable)
        table.add_columns("Field", "Value")
        self._subscription = self.vm.on_property_changed.subscribe(self._changed)
        self._redraw()
        self.call_after_refresh(self._schedule_load)
        table.focus()

    def _changed(self, prop: str) -> None:
        if not self.is_mounted or self._dismiss_requested:
            return
        self._redraw()
        if self.vm.state is S3ObjectDetailsState.CLOSED:
            self.call_after_refresh(self.action_close)
        elif prop == "request_generation":
            self._schedule_load()

    def _schedule_load(self) -> None:
        generation = self.vm.request_generation
        if self.vm.state is not S3ObjectDetailsState.LOADING or generation == self._generation:
            return
        self._generation = generation
        self._run_lifecycle_worker(
            partial(self.vm.load_revision, generation), group="s3-details-read", exit_on_error=False
        )

    def _redraw(self) -> None:
        table = self.query_one(DataTable)
        cursor = table.cursor_row
        table.clear()
        self.query_one("#s3-details-title", Static).update(self.vm.title)
        state = self.vm.state
        self.query_one("#s3-details-status", Static).update(
            {
                S3ObjectDetailsState.LOADING: "Loading…",
                S3ObjectDetailsState.READY: "Read-only · values reported by S3",
                S3ObjectDetailsState.UNAVAILABLE: "Unavailable · select a current S3 object",
                S3ObjectDetailsState.ERROR: "Details unavailable",
                S3ObjectDetailsState.CLOSED: "Closed",
            }[state]
        )
        if state in {S3ObjectDetailsState.READY, S3ObjectDetailsState.ERROR}:
            for detail in self.vm.fields:
                # Rich Text bypasses markup parsing, including in table cells.
                table.add_row(Text(detail.label), Text(detail.value), key=detail.label)
            if table.row_count:
                table.move_cursor(row=min(cursor, table.row_count - 1))
        self._show_value()

    def on_data_table_row_highlighted(self, _event: DataTable.RowHighlighted) -> None:
        self._show_value()

    def _current_field(self) -> ObjectDetailField | None:
        if self.vm.state not in {S3ObjectDetailsState.READY, S3ObjectDetailsState.ERROR}:
            return None
        row = self.query_one(DataTable).cursor_row
        return self.vm.fields[row] if 0 <= row < len(self.vm.fields) else None

    def _show_value(self) -> None:
        field = self._current_field()
        self.query_one(TextArea).load_text(field.value if field is not None else "")
        self.query_one("#s3-details-value-label", Static).update(
            f"Full value · {field.label}" if field is not None else "Full value"
        )
        button = next(button for button in self.query(ModalButton) if button.button_id == "copy")
        button.disabled = field is None or field.copy_value is None
        button.can_focus = not button.disabled

    def action_copy(self) -> None:
        field = self._current_field()
        if not self._dismiss_requested and field is not None and field.copy_value is not None:
            self._run_lifecycle_worker(
                partial(self._copy_current, self.vm.request_generation, field),
                group="s3-details-copy",
                exit_on_error=False,
            )

    async def _copy_current(self, generation: int, field: ObjectDetailField) -> None:
        if (
            not self._dismiss_requested
            and generation == self.vm.request_generation
            and self._current_field() is field
            and field.copy_value is not None
        ):
            await self._copy_value(field.copy_value)

    def action_move_up(self) -> None:
        self.query_one(DataTable).action_cursor_up()

    def action_move_down(self) -> None:
        self.query_one(DataTable).action_cursor_down()

    def action_descend(self) -> None:
        if isinstance(self.focused, ModalButton):
            self.focused.press()

    def action_focus_next(self) -> None:
        self.focus_next()

    def action_focus_prev(self) -> None:
        self.focus_previous()

    def on_click(self, event: Click) -> None:
        if isinstance(event.widget, ModalButton):
            event.stop()
            if event.widget.button_id == "copy":
                self.action_copy()
            else:
                self.action_close()

    def action_close(self) -> None:
        if self._dismiss_requested:
            return
        self._dismiss_requested = True
        self.vm.close()
        if self.is_mounted:
            self._redraw()
        if self._source_subscription is not None:
            self._source_subscription.dispose()
            self._source_subscription = None
        if self in self.app.screen_stack:
            self.dismiss()

    async def on_unmount(self) -> None:
        self._dismiss_requested = True
        if self._subscription is not None:
            self._subscription.dispose()
            self._subscription = None
        if self._source_subscription is not None:
            self._source_subscription.dispose()
            self._source_subscription = None
        self.vm.close()
        self.workers.cancel_group(self, "s3-details-read")
        self.workers.cancel_group(self, "s3-details-copy")
        await self.vm.shutdown()
        self.vm.dispose()
