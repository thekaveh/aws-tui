from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Generic, TypeVar

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
    NamedQuery,
    NamedQuerySummary,
    PreparedStatement,
    PreparedStatementSummary,
)
from aws_tui.vm.athena._errors import map_provider_error, map_unexpected_error
from aws_tui.vm.file_manager.pane_vm import PaneState

_SAVED_ERROR = "Athena saved query request failed"
_PREPARED_ERROR = "Athena prepared statement request failed"

T = TypeVar("T")


class SavedQueryKind(StrEnum):
    NAMED = "named"
    PREPARED = "prepared"


@dataclass(eq=False)
class _SavedWorker(Generic[T]):
    generation: int
    workgroup: str
    tasks: set[asyncio.Task[Any]] = field(default_factory=set, repr=False)
    named_query_details: dict[str, NamedQuery] = field(
        default_factory=dict,
        repr=False,
    )
    retired: bool = False
    pager: TokenPagedComposition[T, str] = field(init=False, repr=False)
    load_more_command: AsyncRelayCommand = field(init=False, repr=False)


class AthenaSavedVM:
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
        self._context_generation = 0
        self._named_generation = 0
        self._prepared_generation = 0
        self._detail_generation = 0
        self._selected_kind: SavedQueryKind | None = None
        self._selected_query_id: str | None = None
        self._selected_named_query: NamedQuery | None = None
        self._selected_prepared_statement: PreparedStatement | None = None
        self._named_state = PaneState.EMPTY
        self._prepared_state = PaneState.EMPTY
        self._detail_state = PaneState.EMPTY
        self._named_error_text: str | None = None
        self._prepared_error_text: str | None = None
        self._detail_error_text: str | None = None
        self._is_loading_more_named_queries = False
        self._is_loading_more_prepared_statements = False
        self._detail_tasks: set[asyncio.Task[Any]] = set()
        self._on_property_changed: Subject[str] = Subject()
        self._inner: ComponentVMOf[None] = (
            ComponentVMOf[None]
            .builder()
            .name("athena.saved")
            .model(None)
            .services(hub, dispatcher)
            .build()
        )
        self._workers: set[_SavedWorker[Any]] = set()
        self._named_worker = self._make_named_worker()
        self._prepared_worker = self._make_prepared_worker()
        self._named_pager = self._named_worker.pager
        self._prepared_pager = self._prepared_worker.pager

    @property
    def workgroup(self) -> str:
        return self._workgroup

    @property
    def named_queries(self) -> tuple[NamedQuerySummary, ...]:
        return tuple(self._named_pager.items)

    @property
    def prepared_statements(self) -> tuple[PreparedStatementSummary, ...]:
        return tuple(self._prepared_pager.items)

    @property
    def has_more_named_queries(self) -> bool:
        return bool(self._workgroup) and self._named_pager.current_token is not None

    @property
    def has_more_prepared_statements(self) -> bool:
        return bool(self._workgroup) and self._prepared_pager.current_token is not None

    @property
    def selected_kind(self) -> SavedQueryKind | None:
        return self._selected_kind

    @property
    def selected_query_id(self) -> str | None:
        return self._selected_query_id

    @property
    def selected_named_query(self) -> NamedQuery | None:
        return self._selected_named_query

    @property
    def selected_prepared_statement(self) -> PreparedStatement | None:
        return self._selected_prepared_statement

    @property
    def named_state(self) -> PaneState:
        return self._named_state

    @property
    def prepared_state(self) -> PaneState:
        return self._prepared_state

    @property
    def detail_state(self) -> PaneState:
        return self._detail_state

    @property
    def named_error_text(self) -> str | None:
        return self._named_error_text

    @property
    def prepared_error_text(self) -> str | None:
        return self._prepared_error_text

    @property
    def detail_error_text(self) -> str | None:
        return self._detail_error_text

    @property
    def is_loading_more_named_queries(self) -> bool:
        return self._is_loading_more_named_queries

    @property
    def is_loading_more_prepared_statements(self) -> bool:
        return self._is_loading_more_prepared_statements

    @property
    def load_more_named_command(self) -> AsyncRelayCommand:
        return self._named_worker.load_more_command

    @property
    def load_more_prepared_command(self) -> AsyncRelayCommand:
        return self._prepared_worker.load_more_command

    @property
    def status(self) -> ConstructionStatus:
        return self._inner.status

    @property
    def on_property_changed(self) -> rx.Observable[str]:
        return self._on_property_changed

    def construct(self) -> None:
        self._inner.construct()

    async def setup(self) -> None:
        if self._disposed or self._shutdown_started:
            return
        await asyncio.gather(
            self.refresh_named_queries(),
            self.refresh_prepared_statements(),
        )

    async def refresh_named_queries(self) -> None:
        if self._disposed or self._shutdown_started:
            return
        self._named_generation += 1
        worker = self._replace_named_worker()
        self._clear_selection()
        self._named_error_text = None
        self._set_named_state(PaneState.LOADING if self._workgroup else PaneState.EMPTY)
        self._notify_saved()
        if not self._workgroup:
            return
        await self._run_named_pager(worker, refresh=True)

    async def refresh_prepared_statements(self) -> None:
        if self._disposed or self._shutdown_started:
            return
        self._prepared_generation += 1
        worker = self._replace_prepared_worker()
        self._clear_selection()
        self._prepared_error_text = None
        self._set_prepared_state(PaneState.LOADING if self._workgroup else PaneState.EMPTY)
        self._notify_saved()
        if not self._workgroup:
            return
        await self._run_prepared_pager(worker, refresh=True)

    async def load_more_named_queries(self) -> None:
        worker = self._named_worker
        if self._can_load_more_named(worker):
            self._set_loading_more_named(True)
            try:
                await self._run_named_pager(worker, refresh=False)
            finally:
                self._set_loading_more_named(False)

    async def load_more_prepared_statements(self) -> None:
        worker = self._prepared_worker
        if self._can_load_more_prepared(worker):
            self._set_loading_more_prepared(True)
            try:
                await self._run_prepared_pager(worker, refresh=False)
            finally:
                self._set_loading_more_prepared(False)

    async def select_named_query(self, query_id: str) -> None:
        if self._disposed or self._shutdown_started:
            return
        query = next(
            (candidate for candidate in self.named_queries if candidate.query_id == query_id),
            None,
        )
        detail = self._named_worker.named_query_details.get(query_id)
        if (
            query is None
            or detail is None
            or detail.workgroup != self._workgroup
            or _named_query_summary(detail) != query
        ):
            return
        self._detail_generation += 1
        self._selected_kind = SavedQueryKind.NAMED
        self._selected_query_id = query_id
        self._selected_named_query = detail
        self._selected_prepared_statement = None
        self._detail_error_text = None
        self._detail_state = PaneState.IDLE
        self._notify_selection()

    async def select_prepared_statement(self, name: str) -> None:
        if self._disposed or self._shutdown_started:
            return
        if not any(summary.name == name for summary in self.prepared_statements):
            return
        self._detail_generation += 1
        generation = self._detail_generation
        context_generation = self._context_generation
        workgroup = self._workgroup
        self._selected_kind = SavedQueryKind.PREPARED
        self._selected_query_id = name
        self._selected_named_query = None
        self._selected_prepared_statement = None
        self._detail_error_text = None
        self._detail_state = PaneState.LOADING
        self._notify_selection()
        task = asyncio.create_task(self._client.get_prepared_statement(name, workgroup))
        self._detail_tasks.add(task)
        try:
            detail = await task
        except asyncio.CancelledError:
            if not self._is_current_detail(generation, context_generation, name):
                return
            raise
        except ProviderError as exc:
            if self._is_current_detail(generation, context_generation, name):
                self._detail_state, self._detail_error_text = map_provider_error(
                    exc,
                    fallback=_PREPARED_ERROR,
                )
                self._notify("detail_state")
                self._notify("detail_error_text")
            return
        except Exception:
            if self._is_current_detail(generation, context_generation, name):
                self._detail_state, self._detail_error_text = map_unexpected_error(
                    fallback=_PREPARED_ERROR,
                )
                self._notify("detail_state")
                self._notify("detail_error_text")
            return
        finally:
            self._detail_tasks.discard(task)
        if not self._is_current_detail(generation, context_generation, name):
            return
        if detail.name != name or detail.workgroup != workgroup:
            self._detail_state = PaneState.ERROR
            self._detail_error_text = _PREPARED_ERROR
            self._notify("detail_state")
            self._notify("detail_error_text")
            return
        self._selected_prepared_statement = detail
        self._detail_state = PaneState.IDLE
        self._notify("selected_prepared_statement")
        self._notify("detail_state")

    def selected_sql(self) -> str | None:
        if self._selected_kind is SavedQueryKind.NAMED:
            query = self._selected_named_query
            return None if query is None else query.query_string
        if self._selected_kind is SavedQueryKind.PREPARED:
            statement = self._selected_prepared_statement
            return None if statement is None else statement.query_statement
        return None

    def replace_workgroup(self, workgroup: str) -> None:
        if self._disposed or self._shutdown_started:
            return
        self._context_generation += 1
        self._named_generation += 1
        self._prepared_generation += 1
        self._detail_generation += 1
        self._cancel_detail_tasks()
        self._workgroup = workgroup
        self._replace_named_worker()
        self._replace_prepared_worker()
        self._clear_selection()
        self._named_error_text = None
        self._prepared_error_text = None
        self._detail_error_text = None
        self._named_state = PaneState.EMPTY
        self._prepared_state = PaneState.EMPTY
        self._detail_state = PaneState.EMPTY
        self._notify_all()

    async def shutdown(self) -> None:
        if self._shutdown_complete:
            return
        self._shutdown_started = True
        self._context_generation += 1
        self._named_generation += 1
        self._prepared_generation += 1
        self._detail_generation += 1
        self._cancel_detail_tasks()
        self._workgroup = ""
        named = self._replace_named_worker()
        prepared = self._replace_prepared_worker()
        self._clear_selection()
        self._named_error_text = None
        self._prepared_error_text = None
        self._detail_error_text = None
        self._named_state = PaneState.EMPTY
        self._prepared_state = PaneState.EMPTY
        self._detail_state = PaneState.EMPTY
        self._notify_all()
        for worker in tuple(self._workers):
            self._retire_worker(worker)
        await self._drain_tasks()
        self._named_worker = named
        self._prepared_worker = prepared
        self._named_pager = named.pager
        self._prepared_pager = prepared.pager
        self._shutdown_complete = True

    def dispose(self) -> None:
        if self._disposed:
            return
        self._disposed = True
        self._context_generation += 1
        self._detail_generation += 1
        self._cancel_detail_tasks()
        for worker in tuple(self._workers):
            self._retire_worker(worker)
        self._on_property_changed.on_completed()
        self._on_property_changed.dispose()
        self._inner.dispose()

    async def _run_named_pager(
        self,
        worker: _SavedWorker[NamedQuerySummary],
        *,
        refresh: bool,
    ) -> None:
        task = self._track_current_task(worker)
        try:
            command = worker.pager.refresh_command if refresh else worker.pager.load_more_command
            await command.execute_async()
        except ProviderError as exc:
            if self._is_current_named(worker):
                self._named_state, self._named_error_text = map_provider_error(
                    exc,
                    fallback=_SAVED_ERROR,
                )
                self._notify("named_state")
                self._notify("named_error_text")
            return
        except Exception:
            if self._is_current_named(worker):
                self._named_state, self._named_error_text = map_unexpected_error(
                    fallback=_SAVED_ERROR,
                )
                self._notify("named_state")
                self._notify("named_error_text")
            return
        finally:
            self._untrack_task(worker, task)
        if self._is_current_named(worker):
            self._named_error_text = None
            self._notify("named_queries")
            self._notify("has_more_named_queries")
            self._notify("named_error_text")
            self._set_named_state(PaneState.IDLE if self.named_queries else PaneState.EMPTY)

    async def _run_prepared_pager(
        self,
        worker: _SavedWorker[PreparedStatementSummary],
        *,
        refresh: bool,
    ) -> None:
        task = self._track_current_task(worker)
        try:
            command = worker.pager.refresh_command if refresh else worker.pager.load_more_command
            await command.execute_async()
        except ProviderError as exc:
            if self._is_current_prepared(worker):
                self._prepared_state, self._prepared_error_text = map_provider_error(
                    exc,
                    fallback=_PREPARED_ERROR,
                )
                self._notify("prepared_state")
                self._notify("prepared_error_text")
            return
        except Exception:
            if self._is_current_prepared(worker):
                self._prepared_state, self._prepared_error_text = map_unexpected_error(
                    fallback=_PREPARED_ERROR,
                )
                self._notify("prepared_state")
                self._notify("prepared_error_text")
            return
        finally:
            self._untrack_task(worker, task)
        if self._is_current_prepared(worker):
            self._prepared_error_text = None
            self._notify("prepared_statements")
            self._notify("has_more_prepared_statements")
            self._notify("prepared_error_text")
            self._set_prepared_state(
                PaneState.IDLE if self.prepared_statements else PaneState.EMPTY
            )

    def _make_named_worker(self) -> _SavedWorker[NamedQuerySummary]:
        generation = self._named_generation
        context_generation = self._context_generation
        workgroup = self._workgroup
        worker: _SavedWorker[NamedQuerySummary] = _SavedWorker(generation, workgroup)

        async def fetch(token: str | None) -> tuple[list[NamedQuerySummary], str | None]:
            if not workgroup:
                return [], None
            ids, next_token = await self._client.list_named_queries_page(
                workgroup,
                start_token=token,
            )
            if not self._is_current_named(worker, context_generation):
                return [], None
            details = await self._client.get_named_queries(list(ids))
            if not self._is_current_named(worker, context_generation):
                return [], None
            by_id = {query.query_id: query for query in details}
            if (
                len(by_id) != len(ids)
                or any(query_id not in by_id for query_id in ids)
                or any(query.workgroup != workgroup for query in details)
            ):
                raise ValueError("Athena named query response identity mismatch")
            worker.named_query_details.update(by_id)
            return [_named_query_summary(by_id[query_id]) for query_id in ids], next_token

        worker.pager = TokenPagedComposition(fetch)
        worker.load_more_command = (
            AsyncRelayCommand.builder()
            .predicate(lambda: self._can_load_more_named(worker))
            .triggers(self._on_property_changed)
            .task(lambda: self._run_named_pager(worker, refresh=False))
            .build()
        )
        self._workers.add(worker)
        return worker

    def _make_prepared_worker(self) -> _SavedWorker[PreparedStatementSummary]:
        generation = self._prepared_generation
        context_generation = self._context_generation
        workgroup = self._workgroup
        worker: _SavedWorker[PreparedStatementSummary] = _SavedWorker(
            generation,
            workgroup,
        )

        async def fetch(
            token: str | None,
        ) -> tuple[list[PreparedStatementSummary], str | None]:
            if not workgroup:
                return [], None
            rows, next_token = await self._client.list_prepared_statements_page(
                workgroup,
                start_token=token,
            )
            if not self._is_current_prepared(worker, context_generation):
                return [], None
            return rows, next_token

        worker.pager = TokenPagedComposition(fetch)
        worker.load_more_command = (
            AsyncRelayCommand.builder()
            .predicate(lambda: self._can_load_more_prepared(worker))
            .triggers(self._on_property_changed)
            .task(lambda: self._run_prepared_pager(worker, refresh=False))
            .build()
        )
        self._workers.add(worker)
        return worker

    def _replace_named_worker(self) -> _SavedWorker[NamedQuerySummary]:
        old_worker = self._named_worker
        worker = self._make_named_worker()
        self._named_worker = worker
        self._named_pager = worker.pager
        self._retire_worker(old_worker)
        return worker

    def _replace_prepared_worker(self) -> _SavedWorker[PreparedStatementSummary]:
        old_worker = self._prepared_worker
        worker = self._make_prepared_worker()
        self._prepared_worker = worker
        self._prepared_pager = worker.pager
        self._retire_worker(old_worker)
        return worker

    def _retire_worker(self, worker: _SavedWorker[Any]) -> None:
        if worker.retired:
            return
        worker.retired = True
        worker.load_more_command.dispose()
        worker.pager.dispose()
        if (
            not worker.tasks
            and worker is not self._named_worker
            and worker is not self._prepared_worker
        ):
            self._workers.discard(worker)

    def _track_current_task(
        self,
        worker: _SavedWorker[Any],
    ) -> asyncio.Task[Any] | None:
        task = asyncio.current_task()
        if task is not None:
            worker.tasks.add(task)
        return task

    def _untrack_task(
        self,
        worker: _SavedWorker[Any],
        task: asyncio.Task[Any] | None,
    ) -> None:
        if task is not None:
            worker.tasks.discard(task)
        if (
            worker.retired
            and not worker.tasks
            and worker is not self._named_worker
            and worker is not self._prepared_worker
        ):
            self._workers.discard(worker)

    async def _drain_tasks(self) -> None:
        current = asyncio.current_task()
        while True:
            tasks = {
                *(task for worker in self._workers for task in worker.tasks),
                *self._detail_tasks,
            }
            tasks = {task for task in tasks if task is not current and not task.done()}
            if not tasks:
                return
            await asyncio.gather(*tasks, return_exceptions=True)

    def _cancel_detail_tasks(self) -> None:
        for task in tuple(self._detail_tasks):
            if not task.done():
                task.cancel()

    def _can_load_more_named(self, worker: _SavedWorker[NamedQuerySummary]) -> bool:
        return (
            self._is_current_named(worker)
            and bool(worker.workgroup)
            and worker.pager.current_token is not None
        )

    def _can_load_more_prepared(
        self,
        worker: _SavedWorker[PreparedStatementSummary],
    ) -> bool:
        return (
            self._is_current_prepared(worker)
            and bool(worker.workgroup)
            and worker.pager.current_token is not None
        )

    def _is_current_named(
        self,
        worker: _SavedWorker[NamedQuerySummary],
        context_generation: int | None = None,
    ) -> bool:
        return (
            worker is self._named_worker
            and worker.generation == self._named_generation
            and (context_generation is None or context_generation == self._context_generation)
            and not worker.retired
            and not self._disposed
            and not self._shutdown_started
        )

    def _is_current_prepared(
        self,
        worker: _SavedWorker[PreparedStatementSummary],
        context_generation: int | None = None,
    ) -> bool:
        return (
            worker is self._prepared_worker
            and worker.generation == self._prepared_generation
            and (context_generation is None or context_generation == self._context_generation)
            and not worker.retired
            and not self._disposed
            and not self._shutdown_started
        )

    def _is_current_detail(
        self,
        generation: int,
        context_generation: int,
        name: str,
    ) -> bool:
        return (
            generation == self._detail_generation
            and context_generation == self._context_generation
            and self._selected_kind is SavedQueryKind.PREPARED
            and self._selected_query_id == name
            and not self._disposed
            and not self._shutdown_started
        )

    def _clear_selection(self) -> None:
        self._detail_generation += 1
        self._selected_kind = None
        self._selected_query_id = None
        self._selected_named_query = None
        self._selected_prepared_statement = None
        self._detail_state = PaneState.EMPTY
        self._detail_error_text = None

    def _notify_saved(self) -> None:
        for property_name in (
            "named_queries",
            "prepared_statements",
            "has_more_named_queries",
            "has_more_prepared_statements",
            "named_state",
            "prepared_state",
        ):
            self._notify(property_name)
        self._notify_selection()

    def _notify_selection(self) -> None:
        for property_name in (
            "selected_kind",
            "selected_query_id",
            "selected_named_query",
            "selected_prepared_statement",
            "detail_state",
            "detail_error_text",
        ):
            self._notify(property_name)

    def _notify_all(self) -> None:
        self._notify("workgroup")
        self._notify_saved()
        self._notify("named_error_text")
        self._notify("prepared_error_text")

    def _set_named_state(self, state: PaneState) -> None:
        if self._named_state == state:
            return
        self._named_state = state
        self._notify("named_state")

    def _set_prepared_state(self, state: PaneState) -> None:
        if self._prepared_state == state:
            return
        self._prepared_state = state
        self._notify("prepared_state")

    def _set_loading_more_named(self, value: bool) -> None:
        if self._is_loading_more_named_queries == value:
            return
        self._is_loading_more_named_queries = value
        self._notify("is_loading_more_named_queries")

    def _set_loading_more_prepared(self, value: bool) -> None:
        if self._is_loading_more_prepared_statements == value:
            return
        self._is_loading_more_prepared_statements = value
        self._notify("is_loading_more_prepared_statements")

    def _notify(self, property_name: str) -> None:
        if self._disposed:
            return
        self._hub.send(
            PropertyChangedMessage.create(
                self,
                "athena.saved",
                property_name,
            )
        )
        self._on_property_changed.on_next(property_name)


def _named_query_summary(query: NamedQuery) -> NamedQuerySummary:
    return NamedQuerySummary(
        query_id=query.query_id,
        name=query.name,
        description=query.description,
        database=query.database,
        workgroup=query.workgroup,
    )


__all__ = ["AthenaSavedVM", "SavedQueryKind"]
