from __future__ import annotations

from collections.abc import Awaitable, Callable
from functools import partial
from inspect import isawaitable
from typing import ClassVar, Literal, TypeAlias, cast

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
from aws_tui.ui.actions import ActionDispatcher
from aws_tui.ui.widgets._worker import DeferredWorkerMixin
from aws_tui.ui.widgets.glue.detail_rows import display_time, display_value, state_placeholder
from aws_tui.vm.file_manager.pane_vm import PaneState
from aws_tui.vm.glue.iceberg_vm import GlueIcebergVM, IcebergRow, IcebergView

# The pane's own display selector. ``GlueIcebergVM.active_view`` only knows
# about the six metadata panes -- "preview" is a widget-local display mode
# layered on top, since the Peek pane binds to a wholly separate child VM
# (``IcebergPreviewVM``) that the metadata VM has no notion of selecting.
_TabView: TypeAlias = IcebergView | Literal["preview"]

# ``_VIEW_ORDER[0]`` typed narrowly, for the "Peek became unavailable" fallback
# in ``_refresh`` -- ``select_view`` only accepts ``IcebergView``, not the
# wider ``_TabView`` indexing ``_VIEW_ORDER`` would otherwise produce.
_FIRST_METADATA_VIEW: IcebergView = "snapshots"

_VIEW_ORDER: tuple[_TabView, ...] = (
    "snapshots",
    "history",
    "manifests",
    "files",
    "partitions",
    "refs",
    "preview",
)
_VIEW_LABELS: dict[_TabView, str] = {
    "snapshots": "Snaps",
    "history": "Hist",
    "manifests": "Mnfst",
    "files": "Files",
    "partitions": "Parts",
    "refs": "Refs",
    "preview": "Peek",
}


class _IcebergTab(Static, can_focus=True):
    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("enter,space", "select", "Select", show=False),
    ]

    class Selected(TextualMessage):
        def __init__(self, view: _TabView) -> None:
            super().__init__()
            self.view = view

    def __init__(self, view: _TabView) -> None:
        super().__init__(
            _VIEW_LABELS[view],
            id=f"glue-iceberg-tab-{view}",
            classes="glue-iceberg-tab",
            markup=False,
        )
        self.view = view
        self.tooltip = (
            "Peek at table rows via DuckDB" if view == "preview" else f"Show Iceberg {view}"
        )

    def on_click(self, _event: Click) -> None:
        self.focus()
        self.action_select()

    def action_select(self) -> None:
        self.post_message(self.Selected(self.view))


class GlueIcebergView(DeferredWorkerMixin, Widget):
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
        self._preview_sub: DisposableBase | None = None
        # Widget-local display selector -- see the ``_TabView`` comment above.
        self._preview_active = False
        self._suppress_highlight = False
        self._refresh_pending = False
        self._table_snapshot: object | None = None

    def compose(self) -> ComposeResult:
        with Horizontal(id="glue-iceberg-tabs"):
            # Composed unconditionally, like its six siblings: ``compose()``
            # runs once at mount, but ``preview.available`` can become True
            # *after* mount (the common case -- landing on a Hive table
            # first, then navigating to an Iceberg one). A tab only created
            # when ``available`` happens to be true at mount time is a
            # feature some users could never reach. Visibility is instead
            # driven live, in ``_refresh``, exactly like ``self.display =
            # self._vm.available`` already drives the whole widget.
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
        # The Peek pane binds directly to its own child VM's Observable --
        # never filtered from the shared hub, never pushed in by the parent.
        self._preview_sub = self._vm.preview.on_property_changed.subscribe(
            on_next=self._on_preview_changed
        )

    def on_unmount(self) -> None:
        if self._sub is not None:
            self._sub.dispose()
            self._sub = None
        if self._preview_sub is not None:
            self._preview_sub.dispose()
            self._preview_sub = None

    def focus_targets(self) -> tuple[Widget, ...]:
        """Return the complete enabled Iceberg interaction surface."""
        if not self.display:
            return ()
        candidates = (
            *self.query(_IcebergTab),
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

    def activate_focused(self, focused: Widget) -> bool:
        """Activate a supported focused control through its public action."""
        ancestors = set(focused.ancestors_with_self)
        target = next(
            (candidate for candidate in self.focus_targets() if candidate in ancestors),
            None,
        )
        if isinstance(target, _IcebergTab):
            target.action_select()
            return True
        if isinstance(target, Button):
            target.press()
            return True
        return False

    def on__iceberg_tab_selected(self, event: _IcebergTab.Selected) -> None:
        view = event.view
        if view == "preview":
            self._preview_active = True
            # Load-once-then-cache, exactly like the six sibling panes'
            # ``select_view``: a re-click on an already-loaded Peek tab is a
            # no-op here too. Recovery from an error goes through the retry
            # button (see ``on_button_pressed``), not a silent re-run.
            if self._vm.preview.state is PaneState.EMPTY:
                self._run_lifecycle_worker(
                    self._vm.preview.load,
                    group="glue-iceberg-preview",
                )
            self._schedule_refresh()
            return
        self._preview_active = False
        self._run_lifecycle_worker(
            partial(self._vm.select_view, view),
            group="glue-iceberg-select-view",
        )
        self._schedule_refresh()

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        if (
            self._suppress_highlight
            or self._preview_active
            or event.cursor_row != event.data_table.cursor_row
            or self._vm.active_view != "snapshots"
            or event.cursor_row >= len(self._vm.snapshots)
        ):
            return
        self._vm.select_snapshot(self._vm.snapshots[event.cursor_row].snapshot_id)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "glue-iceberg-more":
            if self._preview_active:
                # The Peek pane's honest load-more: a real second DuckDB
                # scan at the next row-limit step, unlike the sibling panes'
                # local-window widen.
                self._run_lifecycle_worker(
                    self._vm.preview.load_more,
                    group="glue-iceberg-preview",
                )
            else:
                self._run_lifecycle_worker(
                    self._vm.load_more,
                    group="glue-iceberg-load-more",
                )
        elif event.button.id == "glue-iceberg-retry":
            if self._preview_active:
                self._run_lifecycle_worker(
                    partial(self._vm.preview.load, self._vm.preview.snapshot_id),
                    group="glue-iceberg-preview",
                )
            else:
                self._run_lifecycle_worker(
                    self._vm.retry,
                    group="glue-iceberg-retry",
                )
        elif event.button.id == "glue-iceberg-time-travel":
            self._time_travel_selected()

    def _on_vm_changed(self, _property_name: str) -> None:
        self._schedule_refresh()

    def _on_preview_changed(self, _property_name: str) -> None:
        self._schedule_refresh()

    def _schedule_refresh(self) -> None:
        if self._refresh_pending:
            return
        self._refresh_pending = True
        self.call_after_refresh(self._flush_refresh)

    def _flush_refresh(self) -> None:
        self._refresh_pending = False
        self._refresh()

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
        if self._preview_active and not self._vm.preview.available:
            # Peek was active and just stopped being available (e.g. the
            # user navigated to a different Iceberg table with no usable S3
            # location). Never leave the pane sitting on a tab that is about
            # to be hidden -- fall back to the first tab, exactly like the
            # widget would show on first mount.
            self._preview_active = False
            self._run_lifecycle_worker(
                partial(self._vm.select_view, _FIRST_METADATA_VIEW),
                group="glue-iceberg-select-view",
            )
        active = self._current_view()
        for tab in self.query(_IcebergTab):
            tab.set_class(tab.view == active, "-active")
            if tab.view == "preview":
                tab.display = self._vm.preview.available
        selected_snapshot_id = self._vm.selected_snapshot_id
        self._suppress_highlight = True
        try:
            if active == "preview":
                preview = self._vm.preview
                table_snapshot: object = ("preview", preview.columns, preview.rows)
                if table_snapshot != self._table_snapshot:
                    self._table_snapshot = table_snapshot
                    table.clear(columns=True)
                    for index, column in enumerate(preview.columns):
                        table.add_column(column, key=f"glue-iceberg-preview-column-{index}")
                    for index, preview_row in enumerate(preview.rows):
                        table.add_row(
                            *(
                                Text(
                                    "NULL" if value is None else value,
                                    style="dim italic" if value is None else "",
                                    no_wrap=True,
                                )
                                for value in preview_row
                            ),
                            key=f"iceberg-preview-row-{index}",
                        )
            else:
                table_snapshot = (active, self._vm.items)
                if table_snapshot != self._table_snapshot:
                    self._table_snapshot = table_snapshot
                    table.clear(columns=True)
                    columns = _columns(active)
                    for index, column in enumerate(columns):
                        table.add_column(column, key=f"glue-iceberg-column-{index}")
                    for index, row in enumerate(self._vm.items):
                        row_key = (
                            f"iceberg-snapshot-{cast(IcebergSnapshot, row).snapshot_id}"
                            if active == "snapshots"
                            else f"iceberg-row-{index}"
                        )
                        table.add_row(
                            *(Text(cell, no_wrap=True) for cell in _cells(active, row)),
                            key=row_key,
                        )
                if active == "snapshots" and self._vm.snapshots:
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
        if active == "preview":
            preview = self._vm.preview
            placeholder = state_placeholder(
                preview.state,
                error_text=preview.error_text,
                empty_text="No preview rows",
            )
            status.update(
                placeholder[0]
                if placeholder is not None and preview.state is not PaneState.IDLE
                else ""
            )
            status.set_class(preview.state is PaneState.FORBIDDEN, "-warning")
            status.set_class(preview.state is PaneState.ERROR, "-error")
            snapshot_suffix = (
                f" · snapshot {preview.snapshot_id}" if preview.snapshot_id is not None else ""
            )
            footer.update(f"{len(preview.rows)} rows · limit {preview.limit}{snapshot_suffix}")
            more.disabled = not preview.has_more
            retry.display = preview.state in {
                PaneState.AUTH_REQUIRED,
                PaneState.FORBIDDEN,
                PaneState.ERROR,
            }
            retry.disabled = preview.state is PaneState.LOADING
        else:
            placeholder = state_placeholder(
                self._vm.state,
                error_text=self._vm.error_text,
                empty_text=f"No Iceberg {active}",
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

    def _current_view(self) -> _TabView:
        return "preview" if self._preview_active else self._vm.active_view

    def _enable_highlight(self) -> None:
        self._suppress_highlight = False

    def _time_travel_selected(self) -> None:
        self._run_dispatch_worker(
            lambda: cast(ActionDispatcher, self.app).action_dispatch("glue.time_travel_in_athena"),
            group="glue-iceberg-time-travel",
        )

    def _run_dispatch_worker(
        self,
        dispatch: Callable[[], Awaitable[None] | None],
        *,
        group: str,
    ) -> Worker[None]:
        async def deferred() -> None:
            result = dispatch()
            if isawaitable(result):
                await result

        return self.run_worker(deferred, exclusive=True, group=group)


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
