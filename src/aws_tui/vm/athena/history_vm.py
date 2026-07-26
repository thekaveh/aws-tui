from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

import reactivex as rx
from reactivex.subject import Subject
from vmx import (
    AsyncRelayCommand,
    ComponentVMOf,
    Message,
    MessageHub,
    PropertyChangedMessage,
)
from vmx.collections.token_paged_composition import TokenPagedComposition
from vmx.lifecycle.status import ConstructionStatus
from vmx.services.dispatcher import Dispatcher

from aws_tui.domain.filesystem import ProviderError
from aws_tui.domain.query import (
    QueryExecutionDetail,
    QueryExecutionRef,
    QueryExecutionSummary,
)
from aws_tui.vm.athena._errors import map_provider_error, map_unexpected_error
from aws_tui.vm.file_manager.pane_vm import PaneState

_HISTORY_ERROR = "Athena history request failed"


@dataclass(eq=False)
class _HistoryWorker:
    generation: int
    workgroup: str
    tasks: set[asyncio.Task[Any]] = field(default_factory=set, repr=False)
    details: dict[str, QueryExecutionDetail] = field(default_factory=dict, repr=False)
    retired: bool = False
    pager: TokenPagedComposition[QueryExecutionSummary, str] = field(
        init=False,
        repr=False,
    )
    load_more_command: AsyncRelayCommand = field(init=False, repr=False)


class AthenaHistoryVM:
    def __init__(
        self,
        *,
        client: Any,
        workgroup: str,
        hub: MessageHub[Message],
        dispatcher: Dispatcher,
    ) -> None:
        self._client = client
        self._hub = hub
        self._workgroup = workgroup
        self._disposed = False
        self._shutdown_started = False
        self._shutdown_complete = False
        self._generation = 0
        self._selected_execution_id: str | None = None
        self._detail: QueryExecutionDetail | None = None
        self._state = PaneState.EMPTY
        self._error_text: str | None = None
        self._on_property_changed: Subject[str] = Subject()
        self._inner: ComponentVMOf[None] = (
            ComponentVMOf[None]
            .builder()
            .name("athena.history")
            .model(None)
            .services(hub, dispatcher)
            .build()
        )
        self._workers: set[_HistoryWorker] = set()
        self._worker = self._make_worker(workgroup, self._generation)
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
    def load_more_command(self) -> AsyncRelayCommand:
        return self._worker.load_more_command

    @property
    def status(self) -> ConstructionStatus:
        return self._inner.status

    @property
    def on_property_changed(self) -> rx.Observable[str]:
        return self._on_property_changed

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
        await self._run_pager(worker, refresh=False)

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

    def replace_workgroup(self, workgroup: str) -> None:
        if self._disposed or self._shutdown_started:
            return
        self._generation += 1
        self._workgroup = workgroup
        self._replace_worker(workgroup, self._generation)
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
        except Exception:
            if self._is_current(worker):
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
        self._notify("items")
        self._notify("has_more")
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

        worker.pager = TokenPagedComposition(fetch)
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
        tasks = [
            asyncio.create_task(self._client.get_query_execution(ref.execution_id)) for ref in refs
        ]
        if not tasks:
            return []
        worker.tasks.update(tasks)
        try:
            done, _ = await asyncio.wait(
                tasks,
                return_when=asyncio.FIRST_EXCEPTION,
            )
            failed = next(
                (task for task in done if not task.cancelled() and task.exception() is not None),
                None,
            )
            if failed is not None:
                for task in tasks:
                    if not task.done():
                        task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            if failed is not None:
                error = failed.exception()
                assert error is not None
                raise error
            return [task.result() for task in tasks]
        except BaseException:
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            raise
        finally:
            worker.tasks.difference_update(tasks)

    def _replace_worker(self, workgroup: str, generation: int) -> _HistoryWorker:
        old_worker = self._worker
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

    def _notify(self, property_name: str) -> None:
        if self._disposed:
            return
        self._hub.send(
            PropertyChangedMessage.create(
                self,
                "athena.history",
                property_name,
            )
        )
        self._on_property_changed.on_next(property_name)


__all__ = ["AthenaHistoryVM"]
