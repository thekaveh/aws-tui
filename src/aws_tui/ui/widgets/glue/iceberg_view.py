from __future__ import annotations

from collections.abc import Awaitable, Callable
from functools import partial
from typing import ClassVar, cast

from reactivex.abc import DisposableBase
from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Horizontal
from textual.events import Click
from textual.message import Message as TextualMessage
from textual.widget import Widget
from textual.widgets import Button, DataTable, Static
from textual.worker import Worker

from aws_tui.domain.iceberg import (
    IcebergDataFile,
    IcebergHistoryEntry,
    IcebergManifest,
    IcebergPartition,
    IcebergReference,
    IcebergSnapshot,
)
from aws_tui.ui.widgets.glue.detail_rows import display_time, display_value, state_placeholder
from aws_tui.vm.file_manager.pane_vm import PaneState
from aws_tui.vm.glue.iceberg_vm import GlueIcebergVM, IcebergRow, IcebergView

_VIEW_ORDER: tuple[IcebergView, ...] = (
    "snapshots",
    "history",
    "manifests",
    "files",
    "partitions",
    "refs",
)
_VIEW_LABELS: dict[IcebergView, str] = {
    "snapshots": "Snaps",
    "history": "Hist",
    "manifests": "Mnfst",
    "files": "Files",
    "partitions": "Parts",
    "refs": "Refs",
}


class _IcebergTab(Static, can_focus=True):
    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("enter,space", "select", "Select", show=False),
    ]

    class Selected(TextualMessage):
        def __init__(self, view: IcebergView) -> None:
            super().__init__()
            self.view = view

    def __init__(self, view: IcebergView) -> None:
        super().__init__(
            _VIEW_LABELS[view],
            id=f"glue-iceberg-tab-{view}",
            classes="glue-iceberg-tab",
            markup=False,
        )
        self.view = view
        self.tooltip = f"Show Iceberg {view}"

    def on_click(self, _event: Click) -> None:
        self.focus()
        self.action_select()

    def action_select(self) -> None:
        self.post_message(self.Selected(self.view))


class GlueIcebergView(Widget):
    DEFAULT_CSS: ClassVar[str] = """
    GlueIcebergView {
        width: 1fr;
        height: 3fr;
        min-height: 8;
        layout: grid;
        grid-size: 1 4;
        grid-rows: 1 1 1fr 3;
        grid-columns: 1fr;
        border-title-align: left;
    }
    GlueIcebergView > #glue-iceberg-tabs {
        width: 1fr;
        height: 1;
        layout: horizontal;
    }
    GlueIcebergView .glue-iceberg-tab {
        width: 1fr;
        height: 1;
        content-align: center middle;
        text-overflow: ellipsis;
    }
    GlueIcebergView > #glue-iceberg-status {
        width: 1fr;
        height: 1;
        padding: 0 1;
        text-overflow: ellipsis;
    }
    GlueIcebergView > #glue-iceberg-table {
        width: 1fr;
        height: 1fr;
        scrollbar-size: 1 1;
    }
    GlueIcebergView > #glue-iceberg-controls {
        width: 1fr;
        height: 3;
        layout: horizontal;
    }
    GlueIcebergView #glue-iceberg-footer {
        width: 1fr;
        height: 1;
        padding: 1 1 0 1;
        text-align: right;
        text-overflow: ellipsis;
    }
    GlueIcebergView #glue-iceberg-more,
    GlueIcebergView #glue-iceberg-retry,
    GlueIcebergView #glue-iceberg-time-travel {
        width: 5;
        min-width: 5;
        height: 3;
        margin: 0 0 0 1;
    }
    """

    def __init__(self, vm: GlueIcebergVM, *, id: str | None = None) -> None:
        super().__init__(id=id, classes="glue-pane glue-iceberg-view")
        self._vm = vm
        self._sub: DisposableBase | None = None
        self._suppress_highlight = False

    def compose(self) -> ComposeResult:
        with Horizontal(id="glue-iceberg-tabs"):
            for view in _VIEW_ORDER:
                yield _IcebergTab(view)
        yield Static("", id="glue-iceberg-status", markup=False)
        yield DataTable(
            id="glue-iceberg-table",
            cursor_type="row",
            zebra_stripes=True,
            header_height=1,
        )
        with Horizontal(id="glue-iceberg-controls"):
            yield Static("", id="glue-iceberg-footer", markup=False)
            yield Button(
                "↓",
                id="glue-iceberg-more",
                compact=True,
                flat=True,
                tooltip="Load more Iceberg metadata",
            )
            yield Button(
                "↻",
                id="glue-iceberg-retry",
                compact=True,
                flat=True,
                tooltip="Retry Iceberg metadata",
            )
            yield Button(
                "↗",
                id="glue-iceberg-time-travel",
                compact=True,
                flat=True,
                tooltip="Open selected snapshot in Athena",
            )

    def on_mount(self) -> None:
        self.border_title = "Iceberg metadata"
        self._refresh()
        self._sub = self._vm.on_property_changed.subscribe(on_next=self._on_vm_changed)

    def on_unmount(self) -> None:
        if self._sub is not None:
            self._sub.dispose()
            self._sub = None

    def focus_targets(self) -> tuple[Widget, ...]:
        """Return the complete enabled Iceberg interaction surface."""
        if not self.display:
            return ()
        candidates = (
            *(self.query_one(f"#glue-iceberg-tab-{view}", _IcebergTab) for view in _VIEW_ORDER),
            self.query_one("#glue-iceberg-table", DataTable),
            self.query_one("#glue-iceberg-more", Button),
            self.query_one("#glue-iceberg-retry", Button),
            self.query_one("#glue-iceberg-time-travel", Button),
        )
        return tuple(
            widget
            for widget in candidates
            if widget.display and not widget.disabled and widget.can_focus
        )

    def on__iceberg_tab_selected(self, event: _IcebergTab.Selected) -> None:
        self._run_lifecycle_worker(
            partial(self._vm.select_view, event.view),
            group="glue-iceberg-select-view",
        )

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        if (
            self._suppress_highlight
            or event.cursor_row != event.data_table.cursor_row
            or self._vm.active_view != "snapshots"
            or event.cursor_row >= len(self._vm.snapshots)
        ):
            return
        self._vm.select_snapshot(self._vm.snapshots[event.cursor_row].snapshot_id)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "glue-iceberg-more":
            self._run_lifecycle_worker(
                self._vm.load_more,
                group="glue-iceberg-load-more",
            )
        elif event.button.id == "glue-iceberg-retry":
            self._run_lifecycle_worker(
                self._vm.retry,
                group="glue-iceberg-retry",
            )
        elif event.button.id == "glue-iceberg-time-travel":
            self._time_travel_selected()

    def _run_lifecycle_worker(
        self,
        work: Callable[[], Awaitable[bool]],
        *,
        group: str,
    ) -> Worker[bool]:
        async def deferred() -> bool:
            return await work()

        return self.run_worker(deferred, exclusive=True, group=group)

    def _on_vm_changed(self, _property_name: str) -> None:
        self.call_after_refresh(self._refresh)

    def _refresh(self) -> None:
        try:
            table = self.query_one("#glue-iceberg-table", DataTable)
            status = self.query_one("#glue-iceberg-status", Static)
            footer = self.query_one("#glue-iceberg-footer", Static)
            more = self.query_one("#glue-iceberg-more", Button)
            retry = self.query_one("#glue-iceberg-retry", Button)
            time_travel = self.query_one("#glue-iceberg-time-travel", Button)
        except Exception:
            return
        self.display = self._vm.available
        if not self._vm.available:
            return
        for view in _VIEW_ORDER:
            self.query_one(f"#glue-iceberg-tab-{view}", _IcebergTab).set_class(
                view == self._vm.active_view,
                "-active",
            )
        selected_snapshot_id = self._vm.selected_snapshot_id
        self._suppress_highlight = True
        try:
            table.clear(columns=True)
            columns = _columns(self._vm.active_view)
            for index, column in enumerate(columns):
                table.add_column(column, key=f"glue-iceberg-column-{index}")
            for index, row in enumerate(self._vm.items):
                row_key = (
                    f"iceberg-snapshot-{cast(IcebergSnapshot, row).snapshot_id}"
                    if self._vm.active_view == "snapshots"
                    else f"iceberg-row-{index}"
                )
                table.add_row(
                    *(Text(cell, no_wrap=True) for cell in _cells(self._vm.active_view, row)),
                    key=row_key,
                )
            if self._vm.active_view == "snapshots" and self._vm.snapshots:
                selected_index = next(
                    (
                        index
                        for index, row in enumerate(self._vm.snapshots)
                        if row.snapshot_id == selected_snapshot_id
                    ),
                    0,
                )
                selected_snapshot_id = self._vm.snapshots[selected_index].snapshot_id
                table.move_cursor(row=selected_index)
                self._vm.select_snapshot(selected_snapshot_id)
        finally:
            self.call_after_refresh(self._enable_highlight)
        placeholder = state_placeholder(
            self._vm.state,
            error_text=self._vm.error_text,
            empty_text=f"No Iceberg {self._vm.active_view}",
        )
        status.update(
            placeholder[0]
            if placeholder is not None and self._vm.state is not PaneState.IDLE
            else ""
        )
        status.set_class(self._vm.state is PaneState.FORBIDDEN, "-warning")
        status.set_class(self._vm.state is PaneState.ERROR, "-error")
        suffix = " · more available" if self._vm.has_more else ""
        footer.update(f"{len(self._vm.items)} rows{suffix}")
        more.disabled = not self._vm.has_more or self._vm.state is PaneState.LOADING
        retry.display = self._vm.state in {
            PaneState.AUTH_REQUIRED,
            PaneState.FORBIDDEN,
            PaneState.UNREACHABLE,
            PaneState.ERROR,
        }
        retry.disabled = self._vm.state is PaneState.LOADING
        time_travel.disabled = not self._vm.can_time_travel_in_athena

    def _enable_highlight(self) -> None:
        self._suppress_highlight = False

    def _time_travel_selected(self) -> None:
        if self._vm.can_time_travel_in_athena:
            self._vm.time_travel_in_athena()


def _columns(view: IcebergView) -> tuple[str, ...]:
    return {
        "snapshots": ("Committed", "Snapshot", "Parent", "Operation"),
        "history": ("Current at", "Snapshot", "Parent", "Ancestor"),
        "manifests": ("Path", "Bytes", "Spec", "Snapshot", "Added", "Existing", "Deleted"),
        "files": ("Path", "Format", "Spec", "Records", "Bytes", "Content"),
        "partitions": ("Partition", "Records", "Files", "Bytes", "Snapshot"),
        "refs": ("Name", "Type", "Snapshot", "Ref age", "Keep", "Snapshot age"),
    }[view]


def _cells(view: IcebergView, item: IcebergRow) -> tuple[str, ...]:
    if view == "snapshots":
        snapshot = cast(IcebergSnapshot, item)
        return (
            display_time(snapshot.committed_at),
            str(snapshot.snapshot_id),
            display_value(snapshot.parent_id),
            snapshot.operation,
        )
    if view == "history":
        history = cast(IcebergHistoryEntry, item)
        return (
            display_time(history.made_current_at),
            str(history.snapshot_id),
            display_value(history.parent_id),
            display_value(history.is_current_ancestor),
        )
    if view == "manifests":
        manifest = cast(IcebergManifest, item)
        return (
            manifest.path,
            str(manifest.length),
            str(manifest.partition_spec_id),
            str(manifest.added_snapshot_id),
            str(manifest.added_data_files_count),
            str(manifest.existing_data_files_count),
            str(manifest.deleted_data_files_count),
        )
    if view == "files":
        data_file = cast(IcebergDataFile, item)
        return (
            data_file.file_path,
            data_file.file_format,
            str(data_file.spec_id),
            str(data_file.record_count),
            str(data_file.file_size_in_bytes),
            str(data_file.content),
        )
    if view == "partitions":
        partition = cast(IcebergPartition, item)
        values = " / ".join(f"{name}={display_value(value)}" for name, value in partition.values)
        return (
            values,
            str(partition.record_count),
            str(partition.file_count),
            str(partition.total_data_file_size_in_bytes),
            display_value(partition.last_updated_snapshot_id),
        )
    reference = cast(IcebergReference, item)
    return (
        reference.name,
        reference.ref_type,
        str(reference.snapshot_id),
        display_value(reference.max_reference_age_in_ms),
        display_value(reference.min_snapshots_to_keep),
        display_value(reference.max_snapshot_age_in_ms),
    )


__all__ = ["GlueIcebergView"]
