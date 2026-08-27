from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any, Literal

import reactivex as rx
from vmx import (
    AsyncRelayCommand,
    ComponentVMOf,
    Message,
    MessageHub,
    PropertyChangedMessage,
)
from vmx.lifecycle.status import ConstructionStatus
from vmx.services.dispatcher import Dispatcher

from aws_tui.domain.filesystem import ProviderError
from aws_tui.domain.query import (
    QueryContext,
    QueryExecutionDetail,
    QueryExecutionRef,
    QueryExecutionSummary,
    QueryState,
)
from aws_tui.domain.s3_uri import parse_s3_uri
from aws_tui.vm._observable import ObserverSafeSubject, send_value_free
from aws_tui.vm.athena._domain_validation import (
    optional_exact_string,
    optional_non_empty_exact_string,
    valid_query_context,
    valid_query_execution_detail,
    valid_query_execution_summary,
)
from aws_tui.vm.athena._errors import map_provider_error, map_unexpected_error
from aws_tui.vm.athena._pager_compat import SnapshotTokenPager, seed_token_pager
from aws_tui.vm.file_manager.pane_vm import PaneState
from aws_tui.vm.messages import OpenS3LocationRequest
from aws_tui.vm.service_diagnostics import report_unexpected_service_error

_HISTORY_ERROR = "Athena history request failed"


@dataclass(frozen=True, slots=True)
class AthenaHistorySnapshot:
    context: QueryContext = field(repr=False)
    items: tuple[QueryExecutionSummary, ...] = field(repr=False)
    details: tuple[QueryExecutionDetail, ...] = field(repr=False)
    next_token: str | None = field(repr=False)
    selected_execution_id: str | None = field(repr=False)
    state: PaneState = field(repr=False)
    error_text: str | None = field(repr=False)


@dataclass(eq=False)
class _HistoryWorker:
    generation: int
    workgroup: str
    tasks: set[asyncio.Task[Any]] = field(default_factory=set, repr=False)
    details: dict[str, QueryExecutionDetail] = field(default_factory=dict, repr=False)
    retired: bool = False
    pager: SnapshotTokenPager[QueryExecutionSummary, str] = field(
        init=False,
        repr=False,
    )
    load_more_command: AsyncRelayCommand = field(init=False, repr=False)


class AthenaHistoryVM:
    def __init__(
        self,
        *,
        client: Any,
        context: QueryContext,
        hub: MessageHub[Message],
        dispatcher: Dispatcher,
    ) -> None:
        self._client = client
        self._hub = hub
        self._context = context
        self._workgroup = context.workgroup
        self._disposed = False
        self._shutdown_started = False
        self._shutdown_complete = False
        self._generation = 0
        self._selected_execution_id: str | None = None
        self._detail: QueryExecutionDetail | None = None
        self._state = PaneState.EMPTY
        self._error_text: str | None = None
        self._is_loading_more = False
        self._loading_more_worker: _HistoryWorker | None = None
        self._on_property_changed = ObserverSafeSubject[str]()
        self._inner: ComponentVMOf[None] = (
            ComponentVMOf[None]
            .builder()
            .name("athena.history")
            .model(None)
            .services(hub, dispatcher)
            .build()
        )
        self._workers: set[_HistoryWorker] = set()
        self._worker = self._make_worker(context.workgroup, self._generation)
        self._pager = self._worker.pager

    @property
    def workgroup(self) -> str:
        return self._workgroup

    @property
    def items(self) -> tuple[QueryExecutionSummary, ...]:
        return tuple(self._pager.items)

    @property
    def has_more(self) -> bool:
        return bool(self._workgroup) and self._pager.current_token is not None

    @property
    def selected_execution_id(self) -> str | None:
        return self._selected_execution_id

    @property
    def detail(self) -> QueryExecutionDetail | None:
        return self._detail

    @property
    def state(self) -> PaneState:
        return self._state

    @property
    def error_text(self) -> str | None:
        return self._error_text

    @property
    def is_loading_more(self) -> bool:
        return self._is_loading_more

    @property
    def load_more_command(self) -> AsyncRelayCommand:
        return self._worker.load_more_command

    @property
    def status(self) -> ConstructionStatus:
        return self._inner.status

    @property
    def on_property_changed(self) -> rx.Observable[str]:
        return self._on_property_changed.observable

    def construct(self) -> None:
        self._inner.construct()

    async def setup(self) -> None:
        await self.refresh()

    async def refresh(self) -> None:
        if self._disposed or self._shutdown_started:
            return
        self._generation += 1
        worker = self._replace_worker(self._workgroup, self._generation)
        self._clear_selection()
        self._error_text = None
        self._set_state(PaneState.LOADING if self._workgroup else PaneState.EMPTY)
        self._notify_all()
        if not self._workgroup:
            return
        await self._run_pager(worker, refresh=True)

    async def load_more(self) -> None:
        worker = self._worker
        if not self._can_load_more(worker):
            return
        self._begin_loading_more(worker)
        try:
            await self._run_pager(worker, refresh=False)
        finally:
            self._finish_loading_more(worker)

    async def select_execution(self, execution_id: str) -> None:
        if self._disposed or self._shutdown_started:
            return
        summary = next(
            (row for row in self.items if row.ref.execution_id == execution_id),
            None,
        )
        if summary is None:
            return
        detail = self._worker.details.get(execution_id)
        if detail is None or detail.summary != summary:
            return
        if self._selected_execution_id == execution_id and self._detail == detail:
            return
        self._selected_execution_id = execution_id
        self._detail = detail
        self._notify("selected_execution_id")
        self._notify("detail")

    def export_snapshot(self) -> AthenaHistorySnapshot:
        if (
            self._disposed
            or self._shutdown_started
            or self._state is PaneState.LOADING
            or self._is_loading_more
            or any(not task.done() for worker in self._workers for task in worker.tasks)
        ):
            raise ValueError("Athena history is busy")
        details = tuple(
            self._worker.details[row.ref.execution_id]
            for row in self.items
            if row.ref.execution_id in self._worker.details
        )
        snapshot = AthenaHistorySnapshot(
            context=self._context,
            items=self.items,
            details=details,
            next_token=self._pager.current_token,
            selected_execution_id=self._selected_execution_id,
            state=self._state,
            error_text=self._error_text,
        )
        if not self.snapshot_is_valid(snapshot):
            raise ValueError("Athena history snapshot is invalid")
        return snapshot

    def restore_snapshot(self, snapshot: AthenaHistorySnapshot) -> None:
        if self._disposed or self._shutdown_started:
            raise ValueError("Athena history is unavailable")
        if not self.snapshot_is_valid(snapshot):
            raise ValueError("Athena history snapshot is invalid")
        self._install_snapshot(snapshot)
        self._notify_snapshot_restored()

    def _install_snapshot(self, snapshot: AthenaHistorySnapshot) -> None:
        self._generation += 1
        self._context = snapshot.context
        self._workgroup = snapshot.context.workgroup
        worker = self._replace_worker(self._workgroup, self._generation)
        worker.details.update(
            {detail.summary.ref.execution_id: detail for detail in snapshot.details}
        )
        seed_token_pager(worker.pager, snapshot.items, snapshot.next_token)
        self._selected_execution_id = snapshot.selected_execution_id
        self._detail = (
            None
            if snapshot.selected_execution_id is None
            else worker.details[snapshot.selected_execution_id]
        )
        self._state = snapshot.state
        self._error_text = snapshot.error_text
        self._is_loading_more = False

    def _notify_snapshot_restored(self) -> None:
        self._notify_all()
        self._notify("is_loading_more")

    @staticmethod
    def snapshot_is_valid(snapshot: object) -> bool:
        if (
            type(snapshot) is not AthenaHistorySnapshot
            or not valid_query_context(snapshot.context)
            or type(snapshot.items) is not tuple
            or type(snapshot.details) is not tuple
            or not optional_non_empty_exact_string(snapshot.next_token)
            or not optional_exact_string(snapshot.selected_execution_id)
            or type(snapshot.state) is not PaneState
            or snapshot.state is PaneState.LOADING
            or not optional_exact_string(snapshot.error_text)
            or not all(valid_query_execution_summary(item) for item in snapshot.items)
            or not all(valid_query_execution_detail(detail) for detail in snapshot.details)
        ):
            return False
        item_ids = tuple(item.ref.execution_id for item in snapshot.items)
        details = {detail.summary.ref.execution_id: detail for detail in snapshot.details}
        if (
            len(set(item_ids)) != len(item_ids)
            or len(details) != len(snapshot.details)
            or set(item_ids) != set(details)
        ):
            return False
        for item in snapshot.items:
            detail = details.get(item.ref.execution_id)
            if (
                detail is None
                or detail.summary != item
                or item.ref.connection_name != snapshot.context.connection_name
                or item.ref.region != snapshot.context.region
                or item.ref.workgroup != snapshot.context.workgroup
            ):
                return False
        if (
            snapshot.selected_execution_id is not None
            and snapshot.selected_execution_id not in details
        ):
            return False
        if snapshot.next_token is not None and not snapshot.items:
            return False
        if snapshot.state is PaneState.EMPTY:
            return not (
                snapshot.items
                or snapshot.details
                or snapshot.next_token is not None
                or snapshot.selected_execution_id is not None
                or snapshot.error_text is not None
            )
        if snapshot.state is PaneState.IDLE:
            return bool(snapshot.items) and snapshot.error_text is None
        if snapshot.state in {
            PaneState.AUTH_REQUIRED,
            PaneState.FORBIDDEN,
            PaneState.UNREACHABLE,
            PaneState.ERROR,
        }:
            return bool(snapshot.error_text)
        return False

    def open_s3_location(
        self,
        *,
        preferred_pane: Literal["left", "right"] = "left",
    ) -> bool:
        """Publish the selected execution's authoritative result location."""
        detail = self._detail
        if (
            self._disposed
            or self._shutdown_started
            or detail is None
            or detail.summary.state is not QueryState.SUCCEEDED
            or detail.summary.ref.execution_id != self._selected_execution_id
            or not _execution_identity_belongs_to(detail, self._context)
            or parse_s3_uri(detail.output_location) is None
        ):
            return False
        ref = detail.summary.ref
        assert detail.output_location is not None
        self._hub.send(
            OpenS3LocationRequest(
                connection_name=ref.connection_name,
                region=ref.region,
                uri=detail.output_location,
                preferred_pane=preferred_pane,
                reveal_object=True,
            )
        )
        return True

    def replace_context(self, context: QueryContext) -> None:
        if self._disposed or self._shutdown_started:
            return
        self._generation += 1
        self._context = context
        self._workgroup = context.workgroup
        self._replace_worker(context.workgroup, self._generation)
        self._clear_selection()
        self._error_text = None
        self._state = PaneState.EMPTY
        self._notify_all()

    async def shutdown(self) -> None:
        if self._shutdown_complete:
            return
        self._shutdown_started = True
        self._generation += 1
        self._workgroup = ""
        current = self._replace_worker("", self._generation)
        self._clear_selection()
        self._error_text = None
        self._state = PaneState.EMPTY
        self._notify_all()
        for worker in tuple(self._workers):
            self._retire_worker(worker)
        await self._drain_workers()
        self._worker = current
        self._pager = current.pager
        self._shutdown_complete = True

    def dispose(self) -> None:
        if self._disposed:
            return
        self._disposed = True
        self._generation += 1
        for worker in tuple(self._workers):
            self._retire_worker(worker)
        self._on_property_changed.on_completed()
        self._on_property_changed.dispose()
        self._inner.dispose()

    async def _run_pager(self, worker: _HistoryWorker, *, refresh: bool) -> None:
        task = self._track_current_task(worker)
        try:
            command = worker.pager.refresh_command if refresh else worker.pager.load_more_command
            await command.execute_async()
        except ProviderError as exc:
            if self._is_current(worker):
                self._state, self._error_text = map_provider_error(
                    exc,
                    fallback=_HISTORY_ERROR,
                )
                self._notify("state")
                self._notify("error_text")
            return
        except Exception as exc:
            if self._is_current(worker):
                report_unexpected_service_error(
                    self._hub,
                    service="athena",
                    operation="list_query_executions",
                    error=exc,
                )
                self._state, self._error_text = map_unexpected_error(
                    fallback=_HISTORY_ERROR,
                )
                self._notify("state")
                self._notify("error_text")
            return
        finally:
            self._untrack_task(worker, task)
        if not self._is_current(worker):
            return
        self._error_text = None
        self._notify("items")
        self._notify("has_more")
        self._notify("error_text")
        self._set_state(PaneState.IDLE if self.items else PaneState.EMPTY)

    def _make_worker(self, workgroup: str, generation: int) -> _HistoryWorker:
        worker = _HistoryWorker(generation, workgroup)

        async def fetch(
            token: str | None,
        ) -> tuple[list[QueryExecutionSummary], str | None]:
            if not workgroup:
                return [], None
            refs, next_token = await self._client.list_query_executions_page(
                workgroup,
                start_token=token,
            )
            if not self._is_current(worker):
                return [], None
            details = await self._hydrate_details(worker, refs)
            if not self._is_current(worker):
                return [], None
            for ref, detail in zip(refs, details, strict=True):
                if detail.summary.ref != ref or ref.workgroup != workgroup:
                    raise ValueError("Athena history response identity mismatch")
                worker.details[ref.execution_id] = detail
            return [detail.summary for detail in details], next_token

        worker.pager = SnapshotTokenPager(fetch)
        worker.load_more_command = (
            AsyncRelayCommand.builder()
            .predicate(lambda: self._can_load_more(worker))
            .triggers(self._on_property_changed)
            .task(lambda: self._run_pager(worker, refresh=False))
            .build()
        )
        self._workers.add(worker)
        return worker

    async def _hydrate_details(
        self,
        worker: _HistoryWorker,
        refs: list[QueryExecutionRef],
    ) -> list[QueryExecutionDetail]:
        del worker
        return list(await self._client.get_query_executions([ref.execution_id for ref in refs]))

    def _replace_worker(self, workgroup: str, generation: int) -> _HistoryWorker:
        old_worker = self._worker
        self._finish_loading_more(old_worker)
        worker = self._make_worker(workgroup, generation)
        self._worker = worker
        self._pager = worker.pager
        self._retire_worker(old_worker)
        return worker

    def _retire_worker(self, worker: _HistoryWorker) -> None:
        if worker.retired:
            return
        worker.retired = True
        worker.load_more_command.dispose()
        worker.pager.dispose()
        if not worker.tasks and worker is not self._worker:
            self._workers.discard(worker)

    def _track_current_task(self, worker: _HistoryWorker) -> asyncio.Task[Any] | None:
        task = asyncio.current_task()
        if task is not None:
            worker.tasks.add(task)
        return task

    def _untrack_task(
        self,
        worker: _HistoryWorker,
        task: asyncio.Task[Any] | None,
    ) -> None:
        if task is not None:
            worker.tasks.discard(task)
        if worker.retired and not worker.tasks and worker is not self._worker:
            self._workers.discard(worker)

    async def _drain_workers(self) -> None:
        current = asyncio.current_task()
        while True:
            tasks = {
                task
                for worker in self._workers
                for task in worker.tasks
                if task is not current and not task.done()
            }
            if not tasks:
                return
            await asyncio.gather(*tasks, return_exceptions=True)

    def _can_load_more(self, worker: _HistoryWorker) -> bool:
        return (
            self._is_current(worker)
            and bool(worker.workgroup)
            and worker.pager.current_token is not None
        )

    def _is_current(self, worker: _HistoryWorker) -> bool:
        return (
            worker is self._worker
            and worker.generation == self._generation
            and not worker.retired
            and not self._disposed
            and not self._shutdown_started
        )

    def _clear_selection(self) -> None:
        self._selected_execution_id = None
        self._detail = None

    def _notify_all(self) -> None:
        for property_name in (
            "workgroup",
            "items",
            "has_more",
            "selected_execution_id",
            "detail",
            "state",
            "error_text",
        ):
            self._notify(property_name)

    def _set_state(self, state: PaneState) -> None:
        if self._state == state:
            return
        self._state = state
        self._notify("state")

    def _begin_loading_more(self, worker: _HistoryWorker) -> None:
        self._loading_more_worker = worker
        if not self._is_loading_more:
            self._is_loading_more = True
            self._notify("is_loading_more")

    def _finish_loading_more(self, worker: _HistoryWorker) -> None:
        if self._loading_more_worker is not worker:
            return
        self._loading_more_worker = None
        if self._is_loading_more:
            self._is_loading_more = False
            self._notify("is_loading_more")

    def _notify(self, property_name: str) -> None:
        if self._disposed:
            return
        send_value_free(
            self._hub,
            PropertyChangedMessage.create(
                self,
                "athena.history",
                property_name,
            ),
        )
        self._on_property_changed.on_next(property_name)


__all__ = ["AthenaHistorySnapshot", "AthenaHistoryVM"]


def _execution_identity_belongs_to(
    detail: QueryExecutionDetail,
    expected: QueryContext,
) -> bool:
    ref = detail.summary.ref
    return (
        ref.connection_name == detail.context.connection_name
        and ref.region == detail.context.region
        and ref.workgroup == detail.context.workgroup
        and detail.context == expected
    )
