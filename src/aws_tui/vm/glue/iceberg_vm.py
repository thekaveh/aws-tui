"""Glue-owned Iceberg metadata state backed by bounded Athena inspection."""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from functools import partial
from typing import Any, Literal, Protocol, TypeAlias, cast

from vmx import (
    NULL_MESSAGE_HUB,
    AsyncResourceError,
    AsyncResourceErrorWithValue,
    AsyncResourceLoadingWithValue,
    AsyncResourceReady,
    AsyncResourceRetention,
    AsyncResourceStatus,
    AsyncResourceVM,
    Dispatcher,
    Message,
    MessageHub,
)

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
    IcebergCoverage,
    IcebergDataFile,
    IcebergHistoryEntry,
    IcebergInspection,
    IcebergManifest,
    IcebergPartition,
    IcebergQueryPlan,
    IcebergReference,
    IcebergSnapshot,
    metadata_query_plan,
)
from aws_tui.domain.iceberg_interpretation import (
    ObservationSummary,
    SnapshotComparison,
    SnapshotExplanation,
    compare_snapshots,
    current_observation,
    explain_snapshot,
)
from aws_tui.domain.mutation_guard import PROTECTED_MESSAGE, ConnectionProtectedError
from aws_tui.infra.duckdb import DuckDbPort, NativeDuckDb
from aws_tui.vm._component import IsolatedComponentVM
from aws_tui.vm._observable import send_value_free
from aws_tui.vm.file_manager.pane_vm import PaneState
from aws_tui.vm.glue.iceberg_preview_vm import IcebergPreviewVM
from aws_tui.vm.messages import OpenAthenaTableRequest
from aws_tui.vm.service_diagnostics import report_unexpected_service_error

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
    async def list_snapshots(
        self, table_ref: TableRef
    ) -> IcebergInspection[IcebergSnapshot] | tuple[IcebergSnapshot, ...]: ...

    async def list_history(
        self, table_ref: TableRef
    ) -> IcebergInspection[IcebergHistoryEntry] | tuple[IcebergHistoryEntry, ...]: ...

    async def list_manifests(
        self, table_ref: TableRef
    ) -> IcebergInspection[IcebergManifest] | tuple[IcebergManifest, ...]: ...

    async def list_files(
        self, table_ref: TableRef
    ) -> IcebergInspection[IcebergDataFile] | tuple[IcebergDataFile, ...]: ...

    async def list_partitions(
        self, table_ref: TableRef
    ) -> IcebergInspection[IcebergPartition] | tuple[IcebergPartition, ...]: ...

    async def list_refs(
        self, table_ref: TableRef
    ) -> IcebergInspection[IcebergReference] | tuple[IcebergReference, ...]: ...


class IcebergInspectionUnavailableError(ProviderError):
    """Iceberg inspection has no valid Athena execution context."""


class UnavailableIcebergInspector:
    """Typed inspector used when Glue cannot resolve an Athena workgroup."""

    def __init__(self, message: str = "Athena workgroup unavailable") -> None:
        self._message = message

    async def _unavailable(self) -> tuple[Any, ...]:
        raise IcebergInspectionUnavailableError(self._message)

    async def list_snapshots(
        self, _table_ref: TableRef
    ) -> IcebergInspection[IcebergSnapshot] | tuple[IcebergSnapshot, ...]:
        return cast(tuple[IcebergSnapshot, ...], await self._unavailable())

    async def list_history(
        self, _table_ref: TableRef
    ) -> IcebergInspection[IcebergHistoryEntry] | tuple[IcebergHistoryEntry, ...]:
        return cast(tuple[IcebergHistoryEntry, ...], await self._unavailable())

    async def list_manifests(
        self, _table_ref: TableRef
    ) -> IcebergInspection[IcebergManifest] | tuple[IcebergManifest, ...]:
        return cast(tuple[IcebergManifest, ...], await self._unavailable())

    async def list_files(
        self, _table_ref: TableRef
    ) -> IcebergInspection[IcebergDataFile] | tuple[IcebergDataFile, ...]:
        return cast(tuple[IcebergDataFile, ...], await self._unavailable())

    async def list_partitions(
        self, _table_ref: TableRef
    ) -> IcebergInspection[IcebergPartition] | tuple[IcebergPartition, ...]:
        return cast(tuple[IcebergPartition, ...], await self._unavailable())

    async def list_refs(
        self, _table_ref: TableRef
    ) -> IcebergInspection[IcebergReference] | tuple[IcebergReference, ...]:
        return cast(tuple[IcebergReference, ...], await self._unavailable())


@dataclass(frozen=True, slots=True)
class _MetadataPage:
    """The settled value of one metadata view. Its resource owns it."""

    rows: tuple[IcebergRow, ...]
    coverage: IcebergCoverage
    observed_at: datetime


@dataclass(slots=True, eq=False)
class _MetadataRequest:
    """One accepted read: the facts it was admitted under, and its page."""

    table_ref: TableRef
    binding_generation: int
    presenter: Callable[[IcebergQueryPlan], Awaitable[bool]] | None
    plan_lease: object | None
    plan_required: bool
    retired: bool = False
    page: _MetadataPage | None = None
    task: asyncio.Task[Any] | None = None


class _MetadataPane:
    """One view: an AsyncResourceVM that owns its page, plus the paging count.

    Every state field is read from the resource, so a cancelled or refused
    read restores the previous state without a copy of it kept here.
    ``visible_count`` is the only value the resource does not own: it is the
    local paging position, set when a new page arrives and moved by
    ``load_more``.
    """

    def __init__(self, resource: AsyncResourceVM[_MetadataPage], subscription: Any) -> None:
        self.resource = resource
        self.subscription = subscription
        self.visible_count = 0
        self.applied: _MetadataPage | None = None

    @property
    def page(self) -> _MetadataPage | None:
        return _resource_value(self.resource.state)

    @property
    def rows(self) -> tuple[IcebergRow, ...]:
        page = self.page
        return page.rows if page is not None else ()

    @property
    def coverage(self) -> IcebergCoverage | None:
        page = self.page
        return page.coverage if page is not None else None

    @property
    def observed_at(self) -> datetime | None:
        page = self.page
        return page.observed_at if page is not None else None

    @property
    def state(self) -> PaneState:
        status = self.resource.state.status
        if status is AsyncResourceStatus.IDLE:
            return PaneState.EMPTY
        if status is AsyncResourceStatus.LOADING:
            return PaneState.LOADING
        if status is AsyncResourceStatus.READY:
            return PaneState.IDLE if self.rows else PaneState.EMPTY
        error = _resource_error(self.resource.state)
        return _map_iceberg_provider_error(error)[0] if error is not None else PaneState.ERROR

    @property
    def error_text(self) -> str | None:
        if self.resource.state.status is not AsyncResourceStatus.ERROR:
            return None
        error = _resource_error(self.resource.state)
        if error is None:
            return "Iceberg metadata request failed"
        return _map_iceberg_provider_error(error)[1]

    @property
    def loaded(self) -> bool:
        return self.resource.state.status is AsyncResourceStatus.READY

    @property
    def visible_rows(self) -> tuple[IcebergRow, ...]:
        return self.rows[: self.visible_count]

    @property
    def has_more(self) -> bool:
        return self.visible_count < len(self.rows)


class GlueIcebergVM(IsolatedComponentVM):
    """Own independent, on-demand metadata panes for one selected Iceberg table."""

    def __init__(
        self,
        *,
        inspector: IcebergInspectorProtocol,
        hub: MessageHub[Message],
        dispatcher: Dispatcher,
        page_size: int = 50,
        aws_profile: str | None = None,
        duckdb_port: DuckDbPort | None = None,
    ) -> None:
        if type(page_size) is not int or page_size <= 0:
            raise ValueError("Iceberg metadata page size must be positive")
        super().__init__(name="glue.iceberg", hub=hub, dispatcher=dispatcher)
        self._inspector = inspector
        self._hub: MessageHub[Message] = (
            hub  # keeps the narrow type report_unexpected_service_error needs
        )
        self._dispatcher = dispatcher
        self._page_size = page_size
        self._aws_profile = aws_profile
        self._shutdown_started = False
        self._shutdown_complete = False
        self._drain_lock = asyncio.Lock()
        self._metadata_tasks: set[asyncio.Task[Any]] = set()
        self._metadata_load_drain_count = 0
        self._silent_depth = 0
        self._binding_mutation_epoch = 0
        self._binding_generation = 0
        self._table_ref: TableRef | None = None
        self._active_view: IcebergView = "snapshots"
        self._selected_snapshot_id: int | None = None
        self._comparison_snapshot_id: int | None = None
        self._plan_presenter: Callable[[IcebergQueryPlan], Awaitable[bool]] | None = None
        self._plan_lease: object | None = None
        self._plan_required = False
        self._requests: dict[IcebergView, _MetadataRequest] = {}
        self._panes: dict[IcebergView, _MetadataPane] = {
            view: self._new_pane(view) for view in _VIEWS
        }
        self._preview: IcebergPreviewVM = IcebergPreviewVM(
            port=duckdb_port or NativeDuckDb(),
            hub=hub,
            dispatcher=dispatcher,
        )
        self._own(self._preview)

    @property
    def available(self) -> bool:
        return (
            not self._is_disposed() and not self._shutdown_started and self._table_ref is not None
        )

    @property
    def table_ref(self) -> TableRef | None:
        return self._table_ref

    @property
    def preview(self) -> IcebergPreviewVM:
        return self._preview

    @property
    def owns_active_work(self) -> bool:
        """Track accepted reads independently of current table/presentation state."""
        return (
            self._preview.owns_active_work
            or any(not task.done() for task in self._metadata_tasks)
            # A resource can be Loading before its operation task has started.
            or any(
                pane.resource.state.status is AsyncResourceStatus.LOADING
                for pane in self._panes.values()
            )
        )

    @property
    def active_view(self) -> IcebergView:
        return self._active_view

    @property
    def coverage(self) -> IcebergCoverage | None:
        return self._panes[self._active_view].coverage

    @property
    def fetched_count(self) -> int:
        return len(self._panes[self._active_view].rows)

    def coverage_for(self, view: IcebergView) -> IcebergCoverage | None:
        return self._pane(view).coverage

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
    def comparison_snapshot_id(self) -> int | None:
        return self._comparison_snapshot_id

    @property
    def selected_snapshot_detail(self) -> SnapshotExplanation | None:
        if not self.available:
            return None
        row = next((r for r in self.snapshots if r.snapshot_id == self._selected_snapshot_id), None)
        return explain_snapshot(row) if row is not None else None

    @property
    def snapshot_comparison(self) -> SnapshotComparison | None:
        if not self.available or self._table_ref is None:
            return None
        snapshots = cast(tuple[IcebergSnapshot, ...], self._panes["snapshots"].rows)
        a = next((r for r in snapshots if r.snapshot_id == self._comparison_snapshot_id), None)
        b = next((r for r in snapshots if r.snapshot_id == self._selected_snapshot_id), None)
        if a is None or b is None:
            return None
        return compare_snapshots(
            self._table_ref,
            a,
            b,
            snapshots=snapshots,
            history=cast(tuple[IcebergHistoryEntry, ...], self._panes["history"].rows),
        )

    def observed_at_for(self, view: IcebergView) -> datetime | None:
        return self._pane(view).observed_at

    def observation_for(self, view: Literal["files", "partitions"]) -> ObservationSummary | None:
        if not self.available or self._table_ref is None:
            return None
        pane = self._pane(view)
        if pane.observed_at is None:
            return None
        return current_observation(
            self._table_ref,
            view,
            cast(tuple[IcebergDataFile, ...] | tuple[IcebergPartition, ...], pane.rows),
            pane.coverage,
            pane.observed_at,
        )

    def set_plan_presenter(
        self,
        presenter: Callable[[IcebergQueryPlan], Awaitable[bool]],
    ) -> Callable[[], None]:
        """Install the mounted view's render acknowledgement, without a read.

        Headless inspection remains usable without a renderer. A mounted UI
        installs this port before user-triggered loads and acknowledges only
        after the bounded read plan was actually painted.
        """
        if self._is_disposed() or self._shutdown_started:
            return lambda: None
        lease = object()
        self._plan_required = True
        self._plan_lease = lease
        self._plan_presenter = presenter

        def release() -> None:
            if self._plan_lease is lease:
                self._plan_presenter = None
                self._plan_lease = None

        return release

    def pin_comparison_snapshot(self) -> bool:
        if not self.can_time_travel_in_athena:
            return False
        self._comparison_snapshot_id = self._selected_snapshot_id
        self._notify("comparison_snapshot_id")
        return True

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
    def observed_file_content_codes(self) -> tuple[int, ...]:
        """Raw codes across the complete fetched sample, before local paging."""
        return tuple(
            sorted({cast(IcebergDataFile, row).content for row in self._panes["files"].rows})
        )

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
        if not self._is_disposed():
            super().construct()
            self._preview.construct()
            for pane in self._panes.values():
                pane.resource.construct()

    def _on_dispose(self) -> None:
        self._plan_presenter = None
        self._plan_lease = None
        self._shutdown_started = True
        self._binding_mutation_epoch += 1
        self._invalidate_binding(notify=False)
        self._cancel_metadata_tasks()

    async def bind_table(
        self,
        table_ref: TableRef | None,
        *,
        table_format: TableFormat | None = None,
        location: str | None = None,
    ) -> None:
        """Replace the table and clear every pane without issuing provider calls."""
        if self._is_disposed() or self._shutdown_started:
            return
        self._binding_mutation_epoch += 1
        binding_mutation_epoch = self._binding_mutation_epoch
        bound = (
            table_ref is not None
            and table_format is TableFormat.ICEBERG
            and _valid_table_ref(table_ref)
        )
        self._preview.bind(
            location if bound else None,
            profile=self._aws_profile,
            region=table_ref.region if table_ref is not None else "",
        )
        if bound:
            assert table_ref is not None
            await self._replace_table(table_ref, binding_mutation_epoch)
            return
        await self._replace_table(None, binding_mutation_epoch)

    def clear_table(self) -> None:
        """Synchronously invalidate metadata during a parent selection reset."""
        if self._is_disposed() or self._shutdown_started:
            return
        self._binding_mutation_epoch += 1
        self._invalidate_binding()
        self._cancel_metadata_tasks()
        self._preview.bind(None, profile=self._aws_profile, region="")

    async def clear_table_and_drain(self) -> None:
        """Invalidate metadata and durably drain provider work."""
        if not self._is_disposed() and not self._shutdown_started:
            self._binding_mutation_epoch += 1
            binding_mutation_epoch = self._binding_mutation_epoch
        else:
            binding_mutation_epoch = None
        async with self._drain_lock:
            if self._shutdown_complete:
                return
            if (
                binding_mutation_epoch is not None
                and binding_mutation_epoch == self._binding_mutation_epoch
                and not self._is_disposed()
                and not self._shutdown_started
            ):
                self._invalidate_binding()
            await self._cancel_and_drain_metadata_tasks()
            await self._preview.cancel()

    async def cancel_metadata_loads_and_drain_silently(self) -> None:
        """Supersede provider loads without changing or publishing the binding."""
        has_drain_lease = not self._is_disposed() and not self._shutdown_started
        if has_drain_lease:
            self._metadata_load_drain_count += 1
            self._invalidate_metadata_loads_silently()
        try:
            async with self._drain_lock:
                if self._shutdown_complete:
                    return
                await self._cancel_and_drain_metadata_tasks()
                await self._preview.cancel()
        finally:
            if has_drain_lease:
                self._metadata_load_drain_count -= 1

    def begin_shutdown(self) -> None:
        if self._shutdown_started or self._shutdown_complete:
            return
        self._shutdown_started = True
        self._binding_mutation_epoch += 1
        if not self._is_disposed():
            self._invalidate_binding()
        self._cancel_metadata_tasks()
        self._preview.bind(None, profile=self._aws_profile, region="")

    async def shutdown(self) -> None:
        self.begin_shutdown()
        async with self._drain_lock:
            if self._shutdown_complete:
                return
            await self._cancel_and_drain_metadata_tasks()
            await self._preview.cancel()
            self._shutdown_complete = True

    async def _replace_table(
        self,
        table_ref: TableRef | None,
        binding_mutation_epoch: int,
    ) -> None:
        async with self._drain_lock:
            if (
                self._is_disposed()
                or self._shutdown_started
                or self._binding_mutation_epoch != binding_mutation_epoch
            ):
                return
            binding_generation = self._invalidate_binding()
            self._cancel_metadata_tasks()
            cancelled = False
            try:
                await asyncio.sleep(0)
            except asyncio.CancelledError:
                cancelled = True
            try:
                await self._cancel_and_drain_metadata_tasks()
            except asyncio.CancelledError:
                cancelled = True
            if cancelled:
                raise asyncio.CancelledError
            if (
                self._is_disposed()
                or self._shutdown_started
                or self._binding_mutation_epoch != binding_mutation_epoch
                or self._binding_generation != binding_generation
            ):
                return
            self._table_ref = table_ref
            self._notify_all()

    def _invalidate_binding(self, *, notify: bool = True) -> int:
        self._binding_generation += 1
        binding_generation = self._binding_generation
        self._table_ref = None
        self._selected_snapshot_id = None
        self._comparison_snapshot_id = None
        self._reset_panes()
        if notify:
            self._notify_all()
        return binding_generation

    def _invalidate_metadata_loads_silently(self) -> None:
        """Settle every loading pane at its baseline, publishing nothing."""
        self._silent_depth += 1
        try:
            for pane in self._panes.values():
                if pane.resource.state.status is AsyncResourceStatus.LOADING:
                    pane.resource.cancel()
        finally:
            self._silent_depth -= 1

    def _cancel_metadata_tasks(self) -> tuple[asyncio.Task[Any], ...]:
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
                except Exception:
                    # A read that failed while drained is read below; it must not strand the rest.
                    pass
            if task.cancelled():
                self._metadata_tasks.discard(task)
                continue
            with contextlib.suppress(Exception):
                task.result()
            self._metadata_tasks.discard(task)
        if cancelled:
            raise asyncio.CancelledError

    async def select_view(self, view: IcebergView) -> bool:
        pane = self._pane(view)
        if not self.available:
            return False
        if not pane.loaded and not self._metadata_load_is_admitted():
            return False
        changed = view != self._active_view
        self._active_view = view
        if changed:
            if view != "snapshots" and self._selected_snapshot_id is not None:
                self._selected_snapshot_id = None
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
        if not self._metadata_load_is_admitted() or table_ref is None:
            return False
        # Capture admission before the resource starts. Once a UI renderer has
        # owned this VM, losing it can never restore the headless fallback.
        presenter, plan_lease = self._plan_presenter, self._plan_lease
        plan_required = self._plan_required
        if plan_required and presenter is None:
            return False
        if not self._metadata_load_is_admitted():
            return False
        resource = self._panes[view].resource
        request = _MetadataRequest(
            table_ref=table_ref,
            binding_generation=self._binding_generation,
            presenter=presenter,
            plan_lease=plan_lease,
            plan_required=plan_required,
        )
        self._requests[view] = request
        try:
            if resource.state.status is AsyncResourceStatus.IDLE:
                await resource.load()
            else:
                await resource.reload()
        except asyncio.CancelledError:
            # Retire before forwarding cancellation: a renderer that suppresses
            # its own cancellation cannot revive this read.
            request.retired = True
            await _settle_provider(request)
            raise
        # VMx returns as soon as an operation is cancelled. A provider that
        # ignores cancellation is still running, and this caller owns it until
        # it drains, so wait for it here.
        await _settle_provider(request)
        state = resource.state
        return isinstance(state, AsyncResourceReady) and state.value is request.page

    async def _inspect(self, view: IcebergView) -> _MetadataPage:
        """The resource loader: present the plan, then run one bounded read.

        VMx runs this inside the resource's operation task. A refused or
        superseded read ends by cancellation, which drains expect. VMx settles
        the resource on that path, so nothing is published.
        """
        request = self._requests.get(view)
        if request is None:
            raise asyncio.CancelledError
        task = asyncio.current_task()
        request.task = task
        if task is not None:
            self._metadata_tasks.add(task)
            task.add_done_callback(self._metadata_task_done)
        table_ref = request.table_ref
        try:
            # VMx publishes Loading after it creates this task. An eager task
            # factory (Textual sets one) would run this body first, so yield
            # once: the Loading state publishes before any presenter runs.
            await asyncio.sleep(0)
            if not self._request_is_current(view, request):
                raise asyncio.CancelledError
            presented: object = True
            if not self._plan_owner_is_current(request):
                presented = False
            elif request.presenter is not None:
                presented = await request.presenter(metadata_query_plan(table_ref, view))
                if not self._request_is_current(view, request):
                    raise asyncio.CancelledError
            if presented is not True or not self._plan_owner_is_current(request):
                # Refused before any provider call: settle at the baseline.
                self._panes[view].resource.cancel()
                raise asyncio.CancelledError
            rows = await self._loader(view)(table_ref)
            if not self._request_is_current(view, request):
                raise asyncio.CancelledError
            normalized, coverage = self._normalize_inspection(view, table_ref, rows)
        except asyncio.CancelledError:
            if self._request_is_current(view, request):
                # The provider cancelled itself. VMx 3.23.0 would leave the
                # resource Loading, so settle it at its baseline here.
                self._panes[view].resource.cancel()
            raise
        except Exception as exc:
            if self._request_is_current(view, request) and not isinstance(exc, ProviderError):
                report_unexpected_service_error(
                    self._hub,
                    service="glue",
                    operation=f"inspect_iceberg_{view}",
                    error=exc,
                    source=table_ref.connection_name,
                    region=table_ref.region,
                )
            raise
        page = _MetadataPage(normalized, coverage, _now())
        request.page = page
        return page

    def _request_is_current(self, view: IcebergView, request: _MetadataRequest) -> bool:
        task = asyncio.current_task()
        if request.retired or (task is not None and task.cancelling()):
            return False
        return (
            self._requests.get(view) is request
            and self._metadata_load_is_admitted()
            and self._binding_generation == request.binding_generation
            and self._table_ref == request.table_ref
        )

    def _plan_owner_is_current(self, request: _MetadataRequest) -> bool:
        return (
            self._plan_lease is request.plan_lease
            and self._plan_presenter is request.presenter
            and self._plan_required == request.plan_required
        )

    def _metadata_task_done(self, task: asyncio.Task[Any]) -> None:
        self._metadata_tasks.discard(task)
        if task.cancelled():
            return
        with contextlib.suppress(Exception):
            task.exception()

    def _new_pane(self, view: IcebergView) -> _MetadataPane:
        resource: AsyncResourceVM[_MetadataPage] = AsyncResourceVM[_MetadataPage](
            name=f"glue.iceberg.{view}",
            loader=partial(self._inspect, view),
            # The resource never reaches the shared hub. The VM publishes the
            # pane property names itself, from one place.
            hub=NULL_MESSAGE_HUB,
            dispatcher=self._dispatcher,
            retention=AsyncResourceRetention.RETAIN_PREVIOUS,
        )
        subscription = resource.property_changed.subscribe(
            on_next=lambda name: self._on_pane_property(view, resource, name)
        )
        return _MetadataPane(resource, subscription)

    @staticmethod
    def _retire_pane(pane: _MetadataPane) -> None:
        pane.subscription.dispose()
        pane.resource.dispose()

    def _reset_panes(self) -> None:
        """Clear every pane for a new binding.

        An idle resource holds no value and runs no operation, so it is kept.
        A pane that holds a value or a running read is replaced, because
        AsyncResourceVM has no reset: cancel would restore the old table's
        rows. Retirement disposes the old resource, which cancels its read.
        """
        for view, pane in list(self._panes.items()):
            if not self._is_disposed() and pane.resource.state.status is AsyncResourceStatus.IDLE:
                pane.visible_count = 0
                pane.applied = None
                continue
            self._retire_pane(pane)
            fresh = self._new_pane(view)
            if self._is_disposed():
                self._retire_pane(fresh)
            elif self.is_constructed:
                fresh.resource.construct()
            self._panes[view] = fresh

    def _on_pane_property(
        self,
        view: IcebergView,
        resource: AsyncResourceVM[_MetadataPage],
        property_name: str,
    ) -> None:
        """Fan the resource's one "state" change out to the pane property names."""
        if property_name != "state" or self._silent_depth:
            return
        pane = self._panes[view]
        if pane.resource is not resource:
            return
        selection_changed = False
        comparison_changed = False
        page = pane.page
        if page is not None and page is not pane.applied:
            # A new page: reset the paging position and the snapshot selection.
            # A rollback to the same page leaves both alone.
            pane.applied = page
            pane.visible_count = min(len(page.rows), self._page_size)
            if view == "snapshots":
                # Check the whole page, as the comparison pin does: a row beyond the visible
                # rows is still on the page and keeps its selection.
                if not any(
                    cast(IcebergSnapshot, row).snapshot_id == self._selected_snapshot_id
                    for row in page.rows
                ):
                    selection_changed = self._selected_snapshot_id is not None
                    self._selected_snapshot_id = None
                if not any(
                    cast(IcebergSnapshot, row).snapshot_id == self._comparison_snapshot_id
                    for row in page.rows
                ):
                    comparison_changed = self._comparison_snapshot_id is not None
                    self._comparison_snapshot_id = None
        if comparison_changed:
            self._notify("comparison_snapshot_id")
        if selection_changed:
            self._notify("selected_snapshot_id")
        self._notify_view(view)

    def _loader(self, view: IcebergView) -> Any:
        return getattr(self._inspector, f"list_{view}")

    def _normalize_inspection(
        self,
        view: IcebergView,
        table_ref: TableRef,
        result: object,
    ) -> tuple[tuple[IcebergRow, ...], IcebergCoverage]:
        if type(result) is IcebergInspection:
            coverage = result.coverage
            rows = result.rows
            if (
                type(coverage) is not IcebergCoverage
                or type(coverage.status) is not str
                or coverage.status not in {"complete", "truncated", "unknown"}
                or type(coverage.row_limit) is not int
                or coverage.row_limit != _ROW_LIMITS[view]
                or type(coverage.collection) is not str
                or coverage.collection != view
                or not _valid_table_ref(coverage.table_ref)
                or coverage.table_ref != table_ref
                or type(rows) is not tuple
                or (coverage.status == "truncated" and len(rows) != coverage.row_limit)
            ):
                raise ProviderError("Iceberg metadata coverage is invalid")
        else:
            coverage = IcebergCoverage("unknown", _ROW_LIMITS[view], view, table_ref)
            return self._normalize_rows(view, result), coverage
        return self._normalize_rows(view, rows), coverage

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

    def _metadata_load_is_admitted(self) -> bool:
        return (
            not self._is_disposed()
            and not self._shutdown_started
            and self._metadata_load_drain_count == 0
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
        for property_name in (
            "items",
            "state",
            "error_text",
            "has_more",
            "coverage",
            "fetched_count",
        ):
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
        self._publish(property_name)


def _now() -> datetime:
    """Observation clock. A module-level seam so a rendering fixture can pin it."""
    return datetime.now(UTC)


async def _settle_provider(request: _MetadataRequest) -> None:
    """Wait until the request's loader task has finished, whatever it did."""
    task = request.task
    while task is not None and not task.done():
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await asyncio.shield(task)


def _resource_value(state: object) -> _MetadataPage | None:
    if isinstance(
        state, AsyncResourceReady | AsyncResourceLoadingWithValue | AsyncResourceErrorWithValue
    ):
        return cast(_MetadataPage, state.value)
    return None


def _resource_error(state: object) -> BaseException | None:
    if isinstance(state, AsyncResourceError | AsyncResourceErrorWithValue):
        return state.error
    return None


def _map_iceberg_provider_error(exc: BaseException) -> tuple[PaneState, str]:
    if isinstance(exc, ConnectionProtectedError):
        return PaneState.FORBIDDEN, PROTECTED_MESSAGE
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
    unique_keys: bool = True,
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
    return not unique_keys or len(keys) == len(set(keys))


def _valid_row(view: IcebergView, row: IcebergRow) -> bool:
    if view == "snapshots":
        snapshot = cast(IcebergSnapshot, row)
        return (
            _valid_datetime(snapshot.committed_at)
            and _valid_int(snapshot.snapshot_id)
            and _valid_int(snapshot.parent_id, optional=True)
            and snapshot.operation in {"append", "delete", "overwrite", "replace"}
            and _valid_string(snapshot.manifest_list)
            and _valid_pairs(snapshot.summary, unique_keys=False)
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
            _valid_int(data_file.content)
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
