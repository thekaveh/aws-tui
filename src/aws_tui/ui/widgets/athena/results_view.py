from __future__ import annotations

from typing import ClassVar, Protocol, cast

from reactivex.abc import DisposableBase
from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Horizontal
from textual.message import Message
from textual.widget import Widget
from textual.widgets import Button, DataTable, Static

from aws_tui.ui.widgets._focus_guard import is_on_active_screen
from aws_tui.ui.widgets._worker import DeferredWorkerMixin
from aws_tui.ui.widgets.athena.load_more_button import AthenaLoadMoreButton
from aws_tui.ui.widgets.athena.result_cell_modal import AthenaResultCellModal
from aws_tui.ui.widgets.athena.result_filter_modal import AthenaResultFilterModal
from aws_tui.ui.widgets.glue.detail_rows import state_placeholder
from aws_tui.vm.athena.page_vm import AthenaPageVM
from aws_tui.vm.athena.result_projection import serialize_cell, serialize_row
from aws_tui.vm.file_manager.pane_vm import PaneState


class _ClipboardApp(Protocol):
    def copy_value(self, value: str, label: str) -> None: ...


class _AthenaResultTable(DataTable[Text | None]):
    """Stamp highlights when posted, before asynchronous table rebuilds."""

    projection_revision = 0
    execution_generation = -1
    rebuilding = False

    def post_message(self, message: Message) -> bool:
        # Selection uses indexed keys only; do not let Textual's event repr
        # carry a rendered result value into diagnostics.
        if isinstance(message, (DataTable.CellHighlighted, DataTable.CellSelected)):
            message.__dict__["value"] = None
        if isinstance(message, DataTable.CellHighlighted):
            message.__dict__.update(
                athena_revision=self.projection_revision,
                athena_generation=self.execution_generation,
                athena_rebuilding=self.rebuilding,
            )
        return super().post_message(message)


class AthenaResultsView(DeferredWorkerMixin, Widget):
    DEFAULT_CSS: ClassVar[str] = """
    AthenaResultsView {
        height: 1fr;
        layout: grid;
        grid-size: 1 3;
        grid-rows: 3 1fr 3;
        grid-columns: 1fr;
    }
    AthenaResultsView > #athena-results-summary,
    AthenaResultsView #athena-results-footer {
        width: 1fr;
        height: 3;
        padding: 0 1;
        text-overflow: ellipsis;
    }
    AthenaResultsView > #athena-results-summary {
        border-title-align: left;
    }
    AthenaResultsView #athena-results-footer {
        text-align: right;
    }
    AthenaResultsView > #athena-results-controls {
        width: 1fr;
        height: 3;
        layout: horizontal;
    }
    AthenaResultsView > DataTable {
        width: 1fr;
        height: 1fr;
        scrollbar-size: 1 1;
    }
    """

    def __init__(self, vm: AthenaPageVM, *, id: str | None = None) -> None:
        super().__init__(id=id, classes="athena-service-view")
        self._page_vm = vm
        self._vm = vm.results
        self._sub: DisposableBase | None = None
        self._refresh_pending = False
        self._table_snapshot: object | None = None
        self._overlay: AthenaResultCellModal | AthenaResultFilterModal | None = None
        self._overlay_generation: int | None = None

    def compose(self) -> ComposeResult:
        yield Static("", id="athena-results-summary", markup=False)
        yield _AthenaResultTable(
            id="athena-results-table",
            cursor_type="cell",
            zebra_stripes=True,
            header_height=2,
        )
        with Horizontal(id="athena-results-controls"):
            yield Static("", id="athena-results-footer", markup=False)
            yield AthenaLoadMoreButton(
                id="athena-more-results",
                tooltip="Load more result rows",
            )

    def on_mount(self) -> None:
        self.query_one("#athena-results-summary").border_title = "query status"
        self.query_one("#athena-results-table", DataTable).border_title = "query results"
        self._refresh()
        self.app.screen_change_signal.subscribe(self, self._on_screen_change)
        self._sub = self._vm.on_property_changed.subscribe(
            on_next=self._on_vm_changed, on_completed=self._invalidate_overlay
        )

    def on_unmount(self) -> None:
        self.app.screen_change_signal.unsubscribe(self)
        self._invalidate_overlay()
        if self._sub is not None:
            self._sub.dispose()
            self._sub = None

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "athena-more-results":
            self.dispatch_load_more()

    def dispatch_load_more(self) -> None:
        self._run_lifecycle_worker(
            self._vm.load_more,
            group="athena-more-results",
        )

    def _on_vm_changed(self, _property_name: str) -> None:
        if self._overlay_generation != self._vm.projection_generation:
            self._invalidate_overlay()
        if self._refresh_pending:
            return
        self._refresh_pending = True
        self.call_after_refresh(self._flush_refresh)

    def _flush_refresh(self) -> None:
        self._refresh_pending = False
        self._refresh()

    def _refresh(self) -> None:
        if not self._can_act():
            return
        table = self.query_one("#athena-results-table", _AthenaResultTable)
        status = self.query_one("#athena-results-summary", Static)
        footer = self.query_one("#athena-results-footer", Static)
        load_more = self.query_one("#athena-more-results", AthenaLoadMoreButton)
        if not all(widget.is_mounted for widget in (table, status, footer, load_more)):
            return
        table.border_title = "query results"
        status.border_title = "query status"
        indices = self._vm.visible_row_indices
        table_snapshot = (
            table,
            self._vm.projection_generation,
            self._vm.columns,
            indices,
            self._vm.visible_rows,
        )
        table.rebuilding = True
        try:
            if table_snapshot != self._table_snapshot:
                table.projection_revision += 1
                table.execution_generation = self._vm.projection_generation
                table.clear(columns=True)
                for index, column in enumerate(self._vm.columns):
                    table.add_column(
                        Text(f"{column.name}\n{column.type_name}", no_wrap=True),
                        key=f"athena-result-column-{index}",
                    )
                for original_row, row in zip(indices, self._vm.visible_rows, strict=True):
                    table.add_row(
                        *(
                            Text(
                                "NULL" if cell is None else '""' if cell == "" else cell,
                                style="dim italic" if cell is None else "",
                                no_wrap=True,
                            )
                            for cell in row
                        ),
                        key=str(original_row),
                    )
                self._table_snapshot = table_snapshot
            selection = self._vm.selection
            if selection is not None:
                selected_row, selected_column = selection
                table.move_cursor(
                    row=indices.index(selected_row), column=selected_column, animate=False
                )
        finally:
            table.rebuilding = False
        placeholder = state_placeholder(
            self._vm.state,
            error_text=self._vm.error_text,
            empty_text="No results loaded",
        )
        if placeholder is None or self._vm.state is PaneState.IDLE:
            execution = self._vm.execution_id or "No execution"
            status.update(f"Execution {execution}")
        else:
            status.update(placeholder[0])
        status.set_class(self._vm.state is PaneState.FORBIDDEN, "-warning")
        status.set_class(self._vm.state is PaneState.ERROR, "-error")
        suffix = (
            " · safety limit"
            if self._vm.limit_reached
            else " · more available"
            if self._vm.has_more
            else ""
        )
        footer.update(
            f"{len(self._vm.visible_rows)} visible / {len(self._vm.rows)} loaded · local{suffix}"
        )
        load_more.sync(
            has_more=self._vm.has_more,
            busy=self._vm.is_loading_more,
            state=self._vm.state,
            error_text=self._vm.error_text,
            limit_reached=self._vm.limit_reached,
        )

    def _can_act(self) -> bool:
        return (
            self.is_running
            and self.is_attached
            and self.display
            and self._controls_are_live()
            and self._page_vm.active_view == "results"
            and is_on_active_screen(self)
        )

    def _controls_are_live(self) -> bool:
        controls = tuple(
            self.query(
                "#athena-results-table, #athena-results-summary, "
                "#athena-results-footer, #athena-more-results"
            )
        )
        return len(controls) == 4 and all(
            control.is_attached and control.is_running and control.is_mounted
            for control in controls
        )

    def on_hide(self) -> None:
        self._invalidate_overlay()

    def on_show(self) -> None:
        self._on_vm_changed("visible")

    def _on_screen_change(self, _screen: object) -> None:
        self._on_vm_changed("screen")

    def on_data_table_cell_highlighted(self, event: DataTable.CellHighlighted) -> None:
        table = event.data_table
        if (
            not self._can_act()
            or not isinstance(table, _AthenaResultTable)
            or not table.is_mounted
            or table not in self.children
            or getattr(event, "athena_rebuilding", True)
            or getattr(event, "athena_revision", None) != table.projection_revision
            or getattr(event, "athena_generation", None) != self._vm.projection_generation
            or event.coordinate != table.cursor_coordinate
            or not table.is_valid_coordinate(event.coordinate)
            or event.cell_key != table.coordinate_to_cell_key(event.coordinate)
        ):
            return
        indices = self._vm.visible_row_indices
        row, column = event.coordinate
        if (
            not 0 <= row < len(indices)
            or event.cell_key.row_key.value != str(indices[row])
            or event.cell_key.column_key.value != f"athena-result-column-{column}"
        ):
            return
        self._vm.select_cell(row, column, generation=table.execution_generation)

    def _invalidate_overlay(self) -> None:
        overlay = self._overlay
        if overlay is not None:
            overlay.invalidate()
        self._overlay = None
        self._overlay_generation = None

    def _restore_table(self, generation: int, selection: tuple[int, int] | None) -> None:
        if not self._can_act() or generation != self._vm.projection_generation:
            return
        indices = self._vm.visible_row_indices
        if selection is not None and selection[0] in indices:
            self._vm.select_cell(indices.index(selection[0]), selection[1], generation=generation)
        self._refresh()
        table = self.query_one("#athena-results-table", DataTable)
        if table.is_mounted:
            table.focus()

    def action_inspect_cell(self) -> None:
        selection = self._vm.selection
        if not self._can_act() or selection is None:
            return
        row, column = selection
        generation = self._vm.projection_generation
        modal = AthenaResultCellModal(
            self._vm.selected_cell,
            row=row,
            column=column,
            name=self._vm.columns[column].name,
        )
        self._overlay = modal
        self._overlay_generation = generation

        def closed(_result: None) -> None:
            if self._overlay is modal:
                self._overlay = None
                self._overlay_generation = None
            self.call_after_refresh(self._restore_table, generation, selection)

        self.app.push_screen(modal, closed)

    def action_filter_results(self) -> None:
        if not self._can_act():
            return
        generation = self._vm.projection_generation
        modal = AthenaResultFilterModal(self._vm.filter_text)
        self._overlay = modal
        self._overlay_generation = generation

        def closed(result: str | None) -> None:
            if self._overlay is modal:
                self._overlay = None
                self._overlay_generation = None
            if not self._can_act() or generation != self._vm.projection_generation:
                return
            if result is not None:
                self._vm.set_filter(result)
            self.call_after_refresh(self._restore_table, generation, self._vm.selection)

        self.app.push_screen(modal, closed)

    def action_copy_cell(self) -> None:
        if self._can_act() and self._vm.selection is not None:
            cast(_ClipboardApp, self.app).copy_value(
                serialize_cell(self._vm.selected_cell), "Athena cell"
            )

    def action_copy_row(self) -> None:
        row = self._vm.selected_row
        if self._can_act() and row is not None:
            cast(_ClipboardApp, self.app).copy_value(serialize_row(row), "Athena row")

    def action_sort_results(self) -> None:
        selection = self._vm.selection
        if not self._can_act() or selection is None:
            return
        column = selection[1]
        if self._vm.sort_column != column:
            self._vm.set_sort(column)
        elif self._vm.sort_direction == "ascending":
            self._vm.set_sort(column, "descending")
        else:
            self._vm.set_sort(None)

    def action_reset_results(self) -> None:
        if self._can_act():
            self._vm.reset_projection()


__all__ = ["AthenaResultsView"]
