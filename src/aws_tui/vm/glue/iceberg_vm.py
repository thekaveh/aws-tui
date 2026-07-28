"""Glue-owned Iceberg metadata state backed by bounded Athena inspection."""

from __future__ import annotations

import asyncio
import contextlib
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Literal, Protocol, TypeAlias, cast

import reactivex as rx
from vmx import ComponentVMOf, Message, MessageHub, PropertyChangedMessage
from vmx.lifecycle.status import ConstructionStatus
from vmx.services.dispatcher import Dispatcher

from aws_tui.domain.data_catalog import TableFormat, TableRef
from aws_tui.domain.filesystem import (
    AuthRequiredError,
    PermissionDeniedError,
    ProviderError,
    ProviderUnreachableError,
    ThrottledError,
)
from aws_tui.domain.glue import LakeFormationPermissionError
from aws_tui.domain.iceberg import (
    IcebergDataFile,
    IcebergHistoryEntry,
    IcebergManifest,
    IcebergPartition,
    IcebergReference,
    IcebergSnapshot,
)
from aws_tui.vm.athena._observable import ObserverSafeSubject, send_value_free
from aws_tui.vm.file_manager.pane_vm import PaneState
from aws_tui.vm.messages import OpenAthenaTableRequest

IcebergView: TypeAlias = Literal[
    "snapshots",
    "history",
    "manifests",
    "files",
    "partitions",
    "refs",
]
IcebergRow: TypeAlias = (
    IcebergSnapshot
    | IcebergHistoryEntry
    | IcebergManifest
    | IcebergDataFile
    | IcebergPartition
    | IcebergReference
)

_VIEWS: tuple[IcebergView, ...] = (
    "snapshots",
    "history",
    "manifests",
    "files",
    "partitions",
    "refs",
)
_VIEW_SET = frozenset(_VIEWS)
_ROW_TYPES: dict[IcebergView, type[IcebergRow]] = {
    "snapshots": IcebergSnapshot,
    "history": IcebergHistoryEntry,
    "manifests": IcebergManifest,
    "files": IcebergDataFile,
    "partitions": IcebergPartition,
    "refs": IcebergReference,
}
_ROW_LIMITS: dict[IcebergView, int] = {
    "snapshots": 100,
    "history": 100,
    "manifests": 500,
    "files": 1000,
    "partitions": 500,
    "refs": 100,
}


class IcebergInspectorProtocol(Protocol):
    async def list_snapshots(self, table_ref: TableRef) -> tuple[IcebergSnapshot, ...]: ...

    async def list_history(self, table_ref: TableRef) -> tuple[IcebergHistoryEntry, ...]: ...

    async def list_manifests(self, table_ref: TableRef) -> tuple[IcebergManifest, ...]: ...

    async def list_files(self, table_ref: TableRef) -> tuple[IcebergDataFile, ...]: ...

    async def list_partitions(self, table_ref: TableRef) -> tuple[IcebergPartition, ...]: ...

    async def list_refs(self, table_ref: TableRef) -> tuple[IcebergReference, ...]: ...


class IcebergInspectionUnavailableError(ProviderError):
    """Iceberg inspection has no valid Athena execution context."""


class UnavailableIcebergInspector:
    """Typed inspector used when Glue cannot resolve an Athena workgroup."""

    def __init__(self, message: str = "Athena workgroup unavailable") -> None:
        self._message = message

    async def _unavailable(self) -> tuple[Any, ...]:
        raise IcebergInspectionUnavailableError(self._message)

    async def list_snapshots(self, _table_ref: TableRef) -> tuple[IcebergSnapshot, ...]:
        return cast(tuple[IcebergSnapshot, ...], await self._unavailable())

    async def list_history(self, _table_ref: TableRef) -> tuple[IcebergHistoryEntry, ...]:
        return cast(tuple[IcebergHistoryEntry, ...], await self._unavailable())

    async def list_manifests(self, _table_ref: TableRef) -> tuple[IcebergManifest, ...]:
        return cast(tuple[IcebergManifest, ...], await self._unavailable())

    async def list_files(self, _table_ref: TableRef) -> tuple[IcebergDataFile, ...]:
        return cast(tuple[IcebergDataFile, ...], await self._unavailable())

    async def list_partitions(self, _table_ref: TableRef) -> tuple[IcebergPartition, ...]:
        return cast(tuple[IcebergPartition, ...], await self._unavailable())

    async def list_refs(self, _table_ref: TableRef) -> tuple[IcebergReference, ...]:
        return cast(tuple[IcebergReference, ...], await self._unavailable())


@dataclass(slots=True)
class _MetadataPane:
    rows: tuple[IcebergRow, ...] = field(default_factory=tuple)
    visible_count: int = 0
    state: PaneState = PaneState.EMPTY
    error_text: str | None = None
    loaded: bool = False
    generation: int = 0
    stable_rows: tuple[IcebergRow, ...] = field(default_factory=tuple)
    stable_visible_count: int = 0
    stable_state: PaneState = PaneState.EMPTY
    stable_error_text: str | None = None
    stable_loaded: bool = False
    stable_selected_snapshot_id: int | None = None

    @property
    def visible_rows(self) -> tuple[IcebergRow, ...]:
        return self.rows[: self.visible_count]

    @property
    def has_more(self) -> bool:
        return self.visible_count < len(self.rows)


class GlueIcebergVM:
    """Own independent, on-demand metadata panes for one selected Iceberg table."""

    def __init__(
        self,
        *,
        inspector: IcebergInspectorProtocol,
        hub: MessageHub[Message],
        dispatcher: Dispatcher,
        page_size: int = 50,
    ) -> None:
        if type(page_size) is not int or page_size <= 0:
            raise ValueError("Iceberg metadata page size must be positive")
        self._inspector = inspector
        self._hub = hub
        self._page_size = page_size
        self._disposed = False
        self._shutdown_started = False
        self._shutdown_complete = False
        self._lifecycle_lock = asyncio.Lock()
        self._metadata_tasks: set[asyncio.Task[tuple[Any, ...]]] = set()
        self._binding_generation = 0
        self._table_ref: TableRef | None = None
        self._active_view: IcebergView = "snapshots"
        self._selected_snapshot_id: int | None = None
        self._panes = {view: _MetadataPane() for view in _VIEWS}
        self._on_property_changed = ObserverSafeSubject[str]()
        self._inner: ComponentVMOf[None] = (
            ComponentVMOf[None]
            .builder()
            .name("glue.iceberg")
            .model(None)
            .services(hub, dispatcher)
            .build()
        )

    @property
    def status(self) -> ConstructionStatus:
        return self._inner.status

    @property
    def on_property_changed(self) -> rx.Observable[str]:
        return self._on_property_changed.observable

    @property
    def available(self) -> bool:
        return not self._disposed and not self._shutdown_started and self._table_ref is not None

    @property
    def table_ref(self) -> TableRef | None:
        return self._table_ref

    @property
    def active_view(self) -> IcebergView:
        return self._active_view

    @property
    def state(self) -> PaneState:
        return self._panes[self._active_view].state

    @property
    def error_text(self) -> str | None:
        return self._panes[self._active_view].error_text

    @property
    def items(self) -> tuple[IcebergRow, ...]:
        return self._panes[self._active_view].visible_rows

    @property
    def has_more(self) -> bool:
        return self._panes[self._active_view].has_more

    @property
    def selected_snapshot_id(self) -> int | None:
        return self._selected_snapshot_id

    @property
    def can_time_travel_in_athena(self) -> bool:
        pane = self._panes["snapshots"]
        snapshot_id = self._selected_snapshot_id
        return (
            self.available
            and self._active_view == "snapshots"
            and pane.state is PaneState.IDLE
            and type(snapshot_id) is int
            and any(
                row.snapshot_id == snapshot_id
                for row in cast(tuple[IcebergSnapshot, ...], pane.visible_rows)
            )
        )

    @property
    def snapshots(self) -> tuple[IcebergSnapshot, ...]:
        return cast(tuple[IcebergSnapshot, ...], self._panes["snapshots"].visible_rows)

    @property
    def history(self) -> tuple[IcebergHistoryEntry, ...]:
        return cast(tuple[IcebergHistoryEntry, ...], self._panes["history"].visible_rows)

    @property
    def manifests(self) -> tuple[IcebergManifest, ...]:
        return cast(tuple[IcebergManifest, ...], self._panes["manifests"].visible_rows)

    @property
    def files(self) -> tuple[IcebergDataFile, ...]:
        return cast(tuple[IcebergDataFile, ...], self._panes["files"].visible_rows)

    @property
    def partitions(self) -> tuple[IcebergPartition, ...]:
        return cast(tuple[IcebergPartition, ...], self._panes["partitions"].visible_rows)

    @property
    def refs(self) -> tuple[IcebergReference, ...]:
        return cast(tuple[IcebergReference, ...], self._panes["refs"].visible_rows)

    def state_for(self, view: IcebergView) -> PaneState:
        return self._pane(view).state

    def error_text_for(self, view: IcebergView) -> str | None:
        return self._pane(view).error_text

    def has_more_for(self, view: IcebergView) -> bool:
        return self._pane(view).has_more

    def construct(self) -> None:
        if not self._disposed:
            self._inner.construct()

    def dispose(self) -> None:
        if self._disposed:
            return
        self._disposed = True
        self._shutdown_started = True
        self._invalidate_binding(notify=False)
        self._cancel_metadata_tasks()
        try:
            self._on_property_changed.on_completed()
        finally:
            try:
                self._on_property_changed.dispose()
            finally:
                self._inner.dispose()

    async def bind_table(
        self,
        table_ref: TableRef | None,
        *,
        table_format: TableFormat | None = None,
    ) -> None:
        """Replace the table and clear every pane without issuing provider calls."""
        if self._disposed or self._shutdown_started:
            return
        if (
            table_ref is not None
            and table_format is TableFormat.ICEBERG
            and _valid_table_ref(table_ref)
        ):
            await self._replace_table(table_ref)
            return
        await self._replace_table(None)

    async def clear_table(self) -> None:
        """Invalidate metadata and drain provider work during a parent reset."""
        if not self._disposed and not self._shutdown_started:
            await self._replace_table(None)

    async def shutdown(self) -> None:
        async with self._lifecycle_lock:
            if self._disposed or self._shutdown_complete:
                return
            self._shutdown_started = True
            self._invalidate_binding()
            await self._cancel_and_drain_metadata_tasks()
            self._shutdown_complete = True

    async def _replace_table(self, table_ref: TableRef | None) -> None:
        async with self._lifecycle_lock:
            if self._disposed or self._shutdown_started:
                return
            self._invalidate_binding()
            await self._cancel_and_drain_metadata_tasks()
            if self._disposed or self._shutdown_started:
                return
            self._table_ref = table_ref
            self._notify_all()

    def _invalidate_binding(self, *, notify: bool = True) -> None:
        self._binding_generation += 1
        self._table_ref = None
        self._selected_snapshot_id = None
        for view, pane in self._panes.items():
            pane.generation += 1
            pane.rows = ()
            pane.visible_count = 0
            pane.state = PaneState.EMPTY
            pane.error_text = None
            pane.loaded = False
            self._remember_stable(view, pane)
        if notify:
            self._notify_all()

    def _cancel_metadata_tasks(self) -> tuple[asyncio.Task[tuple[Any, ...]], ...]:
        tasks = tuple(self._metadata_tasks)
        for task in tasks:
            if not task.done():
                task.cancel()
        return tasks

    async def _cancel_and_drain_metadata_tasks(self) -> None:
        tasks = self._cancel_metadata_tasks()
        current_task = asyncio.current_task()
        cancellation_count = current_task.cancelling() if current_task is not None else 0
        cancelled = False
        for task in tasks:
            while not task.done():
                try:
                    await asyncio.shield(task)
                except asyncio.CancelledError:
                    current_count = current_task.cancelling() if current_task is not None else 0
                    if current_count > cancellation_count:
                        cancelled = True
                        cancellation_count = current_count
            if task.cancelled():
                continue
            with contextlib.suppress(Exception):
                task.result()
        if cancelled:
            raise asyncio.CancelledError

    async def select_view(self, view: IcebergView) -> bool:
        pane = self._pane(view)
        if not self.available:
            return False
        changed = view != self._active_view
        self._active_view = view
        if changed:
            if view != "snapshots" and self._selected_snapshot_id is not None:
                self._selected_snapshot_id = None
                self._remember_stable("snapshots", self._panes["snapshots"])
                self._notify("selected_snapshot_id")
            self._notify("active_view")
            self._notify_active()
        if pane.loaded:
            return True
        return await self._load(view)

    async def retry(self) -> bool:
        if not self.available:
            return False
        return await self._load(self._active_view)

    async def load_more(self) -> bool:
        if not self.available:
            return False
        pane = self._panes[self._active_view]
        if not pane.has_more or pane.state not in {PaneState.IDLE, PaneState.EMPTY}:
            return False
        pane.visible_count = min(len(pane.rows), pane.visible_count + self._page_size)
        self._remember_stable(self._active_view, pane)
        self._notify_view(self._active_view)
        return True

    def select_snapshot(self, snapshot_id: int) -> bool:
        pane = self._panes["snapshots"]
        snapshots = cast(tuple[IcebergSnapshot, ...], pane.visible_rows)
        if (
            not self.available
            or self._active_view != "snapshots"
            or pane.state is not PaneState.IDLE
            or type(snapshot_id) is not int
            or not any(row.snapshot_id == snapshot_id for row in snapshots)
        ):
            return False
        if self._selected_snapshot_id != snapshot_id:
            self._selected_snapshot_id = snapshot_id
            self._remember_stable("snapshots", pane)
            self._notify("selected_snapshot_id")
        return True

    def time_travel_in_athena(self) -> bool:
        table_ref = self._table_ref
        snapshot_id = self._selected_snapshot_id
        if not self.can_time_travel_in_athena or table_ref is None or type(snapshot_id) is not int:
            return False
        send_value_free(
            self._hub,
            OpenAthenaTableRequest(table_ref=table_ref, snapshot_id=snapshot_id),
        )
        return True

    async def _load(self, view: IcebergView) -> bool:
        table_ref = self._table_ref
        if self._disposed or table_ref is None:
            return False
        pane = self._panes[view]
        pane.generation += 1
        request_generation = pane.generation
        binding_generation = self._binding_generation
        caller_task = asyncio.current_task()
        try:
            pane.state = PaneState.LOADING
            pane.error_text = None
            self._notify_view(view)
            metadata_task = asyncio.create_task(self._loader(view)(table_ref))
            self._metadata_tasks.add(metadata_task)
            metadata_task.add_done_callback(self._metadata_task_done)
            rows = await metadata_task
            if caller_task is not None and caller_task.cancelling():
                raise asyncio.CancelledError
            normalized = self._normalize_rows(view, rows)
        except asyncio.CancelledError:
            if self._is_current(view, request_generation, binding_generation, table_ref):
                selection_changed = self._restore_stable(view, pane)
                if selection_changed:
                    self._notify("selected_snapshot_id")
                self._notify_view(view)
            if caller_task is not None and caller_task.cancelling():
                raise
            return False
        except ProviderError as exc:
            if not self._is_current(view, request_generation, binding_generation, table_ref):
                return False
            pane.state, pane.error_text = _map_iceberg_provider_error(exc)
            pane.loaded = False
            self._remember_stable(view, pane)
            self._notify_view(view)
            return False
        except Exception as exc:
            if not self._is_current(view, request_generation, binding_generation, table_ref):
                return False
            del exc
            pane.state = PaneState.ERROR
            pane.error_text = "Iceberg metadata request failed"
            pane.loaded = False
            self._remember_stable(view, pane)
            self._notify_view(view)
            return False
        if not self._is_current(view, request_generation, binding_generation, table_ref):
            return False
        pane.rows = normalized
        pane.visible_count = min(len(normalized), self._page_size)
        pane.state = PaneState.IDLE if normalized else PaneState.EMPTY
        pane.error_text = None
        pane.loaded = True
        snapshots = cast(tuple[IcebergSnapshot, ...], pane.visible_rows)
        selection_changed = False
        if view == "snapshots" and not any(
            row.snapshot_id == self._selected_snapshot_id for row in snapshots
        ):
            selection_changed = self._selected_snapshot_id is not None
            self._selected_snapshot_id = None
        self._remember_stable(view, pane)
        if selection_changed:
            self._notify("selected_snapshot_id")
        self._notify_view(view)
        return True

    def _metadata_task_done(self, task: asyncio.Task[tuple[Any, ...]]) -> None:
        self._metadata_tasks.discard(task)
        if task.cancelled():
            return
        with contextlib.suppress(Exception):
            task.exception()

    def _remember_stable(self, view: IcebergView, pane: _MetadataPane) -> None:
        if pane.state is PaneState.LOADING:
            return
        pane.stable_rows = pane.rows
        pane.stable_visible_count = pane.visible_count
        pane.stable_state = pane.state
        pane.stable_error_text = pane.error_text
        pane.stable_loaded = pane.loaded
        if view == "snapshots":
            pane.stable_selected_snapshot_id = self._selected_snapshot_id

    def _restore_stable(self, view: IcebergView, pane: _MetadataPane) -> bool:
        pane.rows = pane.stable_rows
        pane.visible_count = pane.stable_visible_count
        pane.state = pane.stable_state
        pane.error_text = pane.stable_error_text
        pane.loaded = pane.stable_loaded
        if view != "snapshots":
            return False
        changed = self._selected_snapshot_id != pane.stable_selected_snapshot_id
        self._selected_snapshot_id = pane.stable_selected_snapshot_id
        return changed

    def _loader(self, view: IcebergView) -> Any:
        return getattr(self._inspector, f"list_{view}")

    def _normalize_rows(
        self,
        view: IcebergView,
        rows: object,
    ) -> tuple[IcebergRow, ...]:
        if type(rows) is not tuple:
            raise ProviderError("Iceberg metadata response is invalid")
        expected = _ROW_TYPES[view]
        if len(rows) > _ROW_LIMITS[view] or any(
            type(row) is not expected or not _valid_row(view, row) for row in rows
        ):
            raise ProviderError("Iceberg metadata response is invalid")
        typed = cast(tuple[IcebergRow, ...], rows)
        identities = tuple(_row_identity(view, row) for row in typed)
        if len(set(identities)) != len(identities):
            raise ProviderError("Iceberg metadata response contains duplicate rows")
        if view == "snapshots":
            return tuple(
                sorted(
                    cast(tuple[IcebergSnapshot, ...], typed),
                    key=lambda row: (row.committed_at, row.snapshot_id),
                    reverse=True,
                )
            )
        if view == "history":
            return tuple(
                sorted(
                    cast(tuple[IcebergHistoryEntry, ...], typed),
                    key=lambda row: (row.made_current_at, row.snapshot_id),
                    reverse=True,
                )
            )
        return typed

    def _is_current(
        self,
        view: IcebergView,
        request_generation: int,
        binding_generation: int,
        table_ref: TableRef,
    ) -> bool:
        return (
            not self._disposed
            and self._binding_generation == binding_generation
            and self._table_ref == table_ref
            and self._panes[view].generation == request_generation
        )

    def _pane(self, view: IcebergView) -> _MetadataPane:
        if type(view) is not str or view not in _VIEW_SET:
            raise ValueError(f"unknown Iceberg metadata view: {view}")
        return self._panes[view]

    def _notify_view(self, view: IcebergView) -> None:
        self._notify(view)
        if view == self._active_view:
            self._notify_active()

    def _notify_active(self) -> None:
        for property_name in ("items", "state", "error_text", "has_more"):
            self._notify(property_name)

    def _notify_all(self) -> None:
        for property_name in (
            "available",
            "table_ref",
            "snapshots",
            "history",
            "manifests",
            "files",
            "partitions",
            "refs",
            "selected_snapshot_id",
        ):
            self._notify(property_name)
        self._notify_active()

    def _notify(self, property_name: str) -> None:
        if self._disposed:
            return
        send_value_free(
            self._hub,
            PropertyChangedMessage.create(self, "glue.iceberg", property_name),
        )
        self._on_property_changed.on_next(property_name)


def _map_iceberg_provider_error(exc: ProviderError) -> tuple[PaneState, str]:
    if isinstance(exc, AuthRequiredError):
        return PaneState.AUTH_REQUIRED, "AWS authentication is required"
    if isinstance(exc, LakeFormationPermissionError | PermissionDeniedError):
        return PaneState.FORBIDDEN, "Iceberg metadata access denied"
    if isinstance(exc, ProviderUnreachableError):
        return PaneState.UNREACHABLE, "Iceberg metadata service unavailable"
    if isinstance(exc, ThrottledError):
        return PaneState.ERROR, "Iceberg metadata request throttled"
    if isinstance(exc, IcebergInspectionUnavailableError):
        return PaneState.ERROR, "Athena workgroup unavailable"
    return PaneState.ERROR, "Iceberg metadata request failed"


def _valid_table_ref(value: object) -> bool:
    return type(value) is TableRef and all(
        type(field_value) is str and bool(field_value.strip())
        for field_value in (
            value.catalog_name,
            value.database_name,
            value.table_name,
            value.connection_name,
            value.region,
        )
    )


def _valid_string(value: object, *, optional: bool = False) -> bool:
    return (optional and value is None) or (type(value) is str and bool(value.strip()))


def _valid_int(value: object, *, optional: bool = False) -> bool:
    return (optional and value is None) or (type(value) is int and value >= 0)


def _valid_positive_int(value: object, *, optional: bool = False) -> bool:
    return (optional and value is None) or (type(value) is int and value > 0)


def _valid_datetime(value: object, *, optional: bool = False) -> bool:
    if optional and value is None:
        return True
    if type(value) is not datetime:
        return False
    try:
        offset = value.utcoffset()
    except Exception:
        return False
    return type(offset) is timedelta and -timedelta(days=1) < offset < timedelta(days=1)


def _valid_pairs(
    value: object,
    *,
    optional_values: bool = False,
) -> bool:
    if type(value) is not tuple:
        return False
    keys: list[str] = []
    for pair in value:
        if type(pair) is not tuple or len(pair) != 2:
            return False
        key, item = pair
        if not _valid_string(key) or not ((item is None and optional_values) or type(item) is str):
            return False
        keys.append(cast(str, key))
    return len(keys) == len(set(keys))


def _valid_row(view: IcebergView, row: IcebergRow) -> bool:
    if view == "snapshots":
        snapshot = cast(IcebergSnapshot, row)
        return (
            _valid_datetime(snapshot.committed_at)
            and _valid_int(snapshot.snapshot_id)
            and _valid_int(snapshot.parent_id, optional=True)
            and snapshot.operation in {"append", "delete", "overwrite", "replace"}
            and _valid_string(snapshot.manifest_list)
            and _valid_pairs(snapshot.summary)
        )
    if view == "history":
        history = cast(IcebergHistoryEntry, row)
        return (
            _valid_datetime(history.made_current_at)
            and _valid_int(history.snapshot_id)
            and _valid_int(history.parent_id, optional=True)
            and type(history.is_current_ancestor) is bool
        )
    if view == "manifests":
        manifest = cast(IcebergManifest, row)
        return (
            _valid_string(manifest.path)
            and _valid_int(manifest.length)
            and _valid_int(manifest.partition_spec_id)
            and _valid_int(manifest.added_snapshot_id)
            and _valid_int(manifest.added_data_files_count)
            and _valid_int(manifest.existing_data_files_count)
            and _valid_int(manifest.deleted_data_files_count)
            and _valid_string(manifest.partition_summaries, optional=True)
        )
    if view == "files":
        data_file = cast(IcebergDataFile, row)
        return (
            data_file.content in {0, 1, 2}
            and type(data_file.content) is int
            and _valid_string(data_file.file_path)
            and data_file.file_format in {"AVRO", "ORC", "PARQUET"}
            and _valid_int(data_file.spec_id)
            and _valid_string(data_file.partition, optional=True)
            and _valid_int(data_file.record_count)
            and _valid_int(data_file.file_size_in_bytes)
            and _valid_string(data_file.equality_ids, optional=True)
            and _valid_int(data_file.sort_order_id, optional=True)
        )
    if view == "partitions":
        partition = cast(IcebergPartition, row)
        return (
            _valid_pairs(partition.values, optional_values=True)
            and _valid_int(partition.record_count)
            and _valid_int(partition.file_count)
            and _valid_int(partition.total_data_file_size_in_bytes)
            and _valid_int(partition.position_delete_record_count, optional=True)
            and _valid_int(partition.position_delete_file_count, optional=True)
            and _valid_int(partition.equality_delete_record_count, optional=True)
            and _valid_int(partition.equality_delete_file_count, optional=True)
            and _valid_datetime(partition.last_updated_at, optional=True)
            and _valid_int(partition.last_updated_snapshot_id, optional=True)
        )
    reference = cast(IcebergReference, row)
    common_valid = (
        _valid_string(reference.name)
        and reference.ref_type in {"BRANCH", "TAG"}
        and _valid_int(reference.snapshot_id)
        and _valid_positive_int(reference.max_reference_age_in_ms, optional=True)
    )
    if not common_valid:
        return False
    if reference.ref_type == "TAG":
        return reference.min_snapshots_to_keep is None and reference.max_snapshot_age_in_ms is None
    return _valid_positive_int(
        reference.min_snapshots_to_keep, optional=True
    ) and _valid_positive_int(reference.max_snapshot_age_in_ms, optional=True)


def _row_identity(view: IcebergView, row: IcebergRow) -> object:
    if view == "snapshots":
        return cast(IcebergSnapshot, row).snapshot_id
    if view == "history":
        history = cast(IcebergHistoryEntry, row)
        return history.made_current_at, history.snapshot_id
    if view == "manifests":
        return cast(IcebergManifest, row).path
    if view == "files":
        return cast(IcebergDataFile, row).file_path
    if view == "partitions":
        return cast(IcebergPartition, row).values
    return cast(IcebergReference, row).name


__all__ = [
    "GlueIcebergVM",
    "IcebergInspectionUnavailableError",
    "IcebergInspectorProtocol",
    "IcebergRow",
    "IcebergView",
    "UnavailableIcebergInspector",
]
