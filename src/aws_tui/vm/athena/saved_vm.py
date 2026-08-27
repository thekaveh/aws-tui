from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Generic, TypeVar

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
    NamedQuery,
    NamedQuerySummary,
    PreparedStatement,
    PreparedStatementSummary,
)
from aws_tui.vm._observable import ObserverSafeSubject, send_value_free
from aws_tui.vm.athena._domain_validation import (
    optional_exact_string,
    optional_non_empty_exact_string,
    valid_named_query,
    valid_named_query_summary,
    valid_prepared_statement,
    valid_prepared_statement_summary,
)
from aws_tui.vm.athena._errors import map_provider_error, map_unexpected_error
from aws_tui.vm.athena._pager_compat import (
    PagerCollectionLimitError,
    SnapshotTokenPager,
    seed_token_pager,
)
from aws_tui.vm.file_manager.pane_vm import PaneState
from aws_tui.vm.service_diagnostics import report_unexpected_service_error

_SAVED_ERROR = "Athena saved query request failed"
_PREPARED_ERROR = "Athena prepared statement request failed"
_MAX_SAVED_ITEMS = 1_000

T = TypeVar("T")


class SavedQueryKind(StrEnum):
    NAMED = "named"
    PREPARED = "prepared"


@dataclass(frozen=True, slots=True)
class AthenaSavedSnapshot:
    workgroup: str = field(repr=False)
    named_queries: tuple[NamedQuerySummary, ...] = field(repr=False)
    named_query_details: tuple[NamedQuery, ...] = field(repr=False)
    named_next_token: str | None = field(repr=False)
    prepared_statements: tuple[PreparedStatementSummary, ...] = field(repr=False)
    prepared_next_token: str | None = field(repr=False)
    selected_kind: SavedQueryKind | None = field(repr=False)
    selected_query_id: str | None = field(repr=False)
    selected_named_query: NamedQuery | None = field(repr=False)
    selected_prepared_statement: PreparedStatement | None = field(repr=False)
    named_state: PaneState = field(repr=False)
    prepared_state: PaneState = field(repr=False)
    detail_state: PaneState = field(repr=False)
    named_error_text: str | None = field(repr=False)
    prepared_error_text: str | None = field(repr=False)
    detail_error_text: str | None = field(repr=False)


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
    pager: SnapshotTokenPager[T, str] = field(init=False, repr=False)
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
        self._loading_more_named_worker: _SavedWorker[NamedQuerySummary] | None = None
        self._loading_more_prepared_worker: _SavedWorker[PreparedStatementSummary] | None = None
        self._detail_tasks: set[asyncio.Task[Any]] = set()
        self._on_property_changed = ObserverSafeSubject[str]()
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
        return bool(self._workgroup) and self._named_pager.has_more

    @property
    def has_more_prepared_statements(self) -> bool:
        return bool(self._workgroup) and self._prepared_pager.has_more

    @property
    def named_limit_reached(self) -> bool:
        return self._named_pager.limit_reached

    @property
    def prepared_limit_reached(self) -> bool:
        return self._prepared_pager.limit_reached

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
        return self._on_property_changed.observable

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
            self._begin_loading_more_named(worker)
            try:
                await self._run_named_pager(worker, refresh=False)
            finally:
                self._finish_loading_more_named(worker)

    async def load_more_prepared_statements(self) -> None:
        worker = self._prepared_worker
        if self._can_load_more_prepared(worker):
            self._begin_loading_more_prepared(worker)
            try:
                await self._run_prepared_pager(worker, refresh=False)
            finally:
                self._finish_loading_more_prepared(worker)

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
        except Exception as exc:
            if self._is_current_detail(generation, context_generation, name):
                report_unexpected_service_error(
                    self._hub,
                    service="athena",
                    operation="get_prepared_statement",
                    error=exc,
                )
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

    def export_snapshot(self) -> AthenaSavedSnapshot:
        if (
            self._disposed
            or self._shutdown_started
            or self._named_state is PaneState.LOADING
            or self._prepared_state is PaneState.LOADING
            or self._detail_state is PaneState.LOADING
            or self._is_loading_more_named_queries
            or self._is_loading_more_prepared_statements
            or self._detail_tasks
            or any(not task.done() for worker in self._workers for task in worker.tasks)
        ):
            raise ValueError("Athena saved queries are busy")
        details = tuple(
            self._named_worker.named_query_details[row.query_id]
            for row in self.named_queries
            if row.query_id in self._named_worker.named_query_details
        )
        snapshot = AthenaSavedSnapshot(
            workgroup=self._workgroup,
            named_queries=self.named_queries,
            named_query_details=details,
            named_next_token=self._named_pager.current_token,
            prepared_statements=self.prepared_statements,
            prepared_next_token=self._prepared_pager.current_token,
            selected_kind=self._selected_kind,
            selected_query_id=self._selected_query_id,
            selected_named_query=self._selected_named_query,
            selected_prepared_statement=self._selected_prepared_statement,
            named_state=self._named_state,
            prepared_state=self._prepared_state,
            detail_state=self._detail_state,
            named_error_text=self._named_error_text,
            prepared_error_text=self._prepared_error_text,
            detail_error_text=self._detail_error_text,
        )
        if not self.snapshot_is_valid(snapshot):
            raise ValueError("Athena saved query snapshot is invalid")
        return snapshot

    def restore_snapshot(self, snapshot: AthenaSavedSnapshot) -> None:
        if self._disposed or self._shutdown_started:
            raise ValueError("Athena saved queries are unavailable")
        if not self.snapshot_is_valid(snapshot):
            raise ValueError("Athena saved query snapshot is invalid")
        self._install_snapshot(snapshot)
        self._notify_snapshot_restored()

    def _install_snapshot(self, snapshot: AthenaSavedSnapshot) -> None:
        self._context_generation += 1
        self._named_generation += 1
        self._prepared_generation += 1
        self._detail_generation += 1
        self._workgroup = snapshot.workgroup
        named = self._replace_named_worker()
        prepared = self._replace_prepared_worker()
        named.named_query_details.update(
            {detail.query_id: detail for detail in snapshot.named_query_details}
        )
        seed_token_pager(named.pager, snapshot.named_queries, snapshot.named_next_token)
        seed_token_pager(
            prepared.pager,
            snapshot.prepared_statements,
            snapshot.prepared_next_token,
        )
        self._selected_kind = snapshot.selected_kind
        self._selected_query_id = snapshot.selected_query_id
        self._selected_named_query = snapshot.selected_named_query
        self._selected_prepared_statement = snapshot.selected_prepared_statement
        self._named_state = snapshot.named_state
        self._prepared_state = snapshot.prepared_state
        self._detail_state = snapshot.detail_state
        self._named_error_text = snapshot.named_error_text
        self._prepared_error_text = snapshot.prepared_error_text
        self._detail_error_text = snapshot.detail_error_text
        self._is_loading_more_named_queries = False
        self._is_loading_more_prepared_statements = False

    def _notify_snapshot_restored(self) -> None:
        self._notify_all()

    @staticmethod
    def snapshot_is_valid(snapshot: object) -> bool:
        if (
            type(snapshot) is not AthenaSavedSnapshot
            or type(snapshot.workgroup) is not str
            or type(snapshot.named_queries) is not tuple
            or type(snapshot.named_query_details) is not tuple
            or type(snapshot.prepared_statements) is not tuple
            or len(snapshot.named_queries) > _MAX_SAVED_ITEMS
            or len(snapshot.prepared_statements) > _MAX_SAVED_ITEMS
            or not optional_non_empty_exact_string(snapshot.named_next_token)
            or not optional_non_empty_exact_string(snapshot.prepared_next_token)
            or (
                snapshot.selected_kind is not None
                and type(snapshot.selected_kind) is not SavedQueryKind
            )
            or not optional_exact_string(snapshot.selected_query_id)
            or not all(
                type(state) is PaneState and state is not PaneState.LOADING
                for state in (
                    snapshot.named_state,
                    snapshot.prepared_state,
                    snapshot.detail_state,
                )
            )
            or not all(
                optional_exact_string(value)
                for value in (
                    snapshot.named_error_text,
                    snapshot.prepared_error_text,
                    snapshot.detail_error_text,
                )
            )
            or not all(valid_named_query_summary(row) for row in snapshot.named_queries)
            or not all(valid_named_query(row) for row in snapshot.named_query_details)
            or not all(
                valid_prepared_statement_summary(row) for row in snapshot.prepared_statements
            )
        ):
            return False
        named_ids = tuple(row.query_id for row in snapshot.named_queries)
        prepared_ids = tuple(row.name for row in snapshot.prepared_statements)
        details = {detail.query_id: detail for detail in snapshot.named_query_details}
        if (
            len(set(named_ids)) != len(named_ids)
            or len(set(prepared_ids)) != len(prepared_ids)
            or len(details) != len(snapshot.named_query_details)
            or set(named_ids) != set(details)
        ):
            return False
        if any(
            detail.workgroup != snapshot.workgroup or _named_query_summary(detail) != row
            for row in snapshot.named_queries
            if (detail := details.get(row.query_id)) is not None
        ) or any(row.query_id not in details for row in snapshot.named_queries):
            return False
        if any(row.name == "" for row in snapshot.prepared_statements):
            return False
        if snapshot.named_next_token is not None and not snapshot.named_queries:
            return False
        if snapshot.prepared_next_token is not None and not snapshot.prepared_statements:
            return False
        if (snapshot.selected_kind is None) != (snapshot.selected_query_id is None):
            return False
        if snapshot.selected_kind is SavedQueryKind.NAMED:
            named_detail = snapshot.selected_named_query
            if (
                type(named_detail) is not NamedQuery
                or not valid_named_query(named_detail)
                or named_detail.query_id != snapshot.selected_query_id
                or details.get(named_detail.query_id) != named_detail
                or snapshot.selected_prepared_statement is not None
                or snapshot.detail_state is not PaneState.IDLE
            ):
                return False
        elif snapshot.selected_kind is SavedQueryKind.PREPARED:
            prepared_detail = snapshot.selected_prepared_statement
            if snapshot.selected_named_query is not None or not any(
                row.name == snapshot.selected_query_id for row in snapshot.prepared_statements
            ):
                return False
            if snapshot.detail_state is PaneState.IDLE:
                if (
                    type(prepared_detail) is not PreparedStatement
                    or not valid_prepared_statement(prepared_detail)
                    or prepared_detail.name != snapshot.selected_query_id
                    or prepared_detail.workgroup != snapshot.workgroup
                ):
                    return False
            elif (
                snapshot.detail_state
                not in {
                    PaneState.AUTH_REQUIRED,
                    PaneState.FORBIDDEN,
                    PaneState.UNREACHABLE,
                    PaneState.ERROR,
                }
                or prepared_detail is not None
            ):
                return False
        elif (
            snapshot.selected_named_query is not None
            or snapshot.selected_prepared_statement is not None
            or snapshot.detail_state is not PaneState.EMPTY
            or snapshot.detail_error_text is not None
        ):
            return False
        for state, error in (
            (snapshot.named_state, snapshot.named_error_text),
            (snapshot.prepared_state, snapshot.prepared_error_text),
            (snapshot.detail_state, snapshot.detail_error_text),
        ):
            if state in {
                PaneState.AUTH_REQUIRED,
                PaneState.FORBIDDEN,
                PaneState.UNREACHABLE,
                PaneState.ERROR,
            }:
                if not error:
                    return False
            elif error is not None:
                return False
        if snapshot.named_state is PaneState.EMPTY and (
            snapshot.named_queries
            or snapshot.named_query_details
            or snapshot.named_next_token is not None
        ):
            return False
        if snapshot.named_state is PaneState.IDLE and not snapshot.named_queries:
            return False
        if snapshot.prepared_state is PaneState.EMPTY and (
            snapshot.prepared_statements or snapshot.prepared_next_token is not None
        ):
            return False
        return not (snapshot.prepared_state is PaneState.IDLE and not snapshot.prepared_statements)

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
        except PagerCollectionLimitError:
            if self._is_current_named(worker):
                accepted_ids = {row.query_id for row in worker.pager.items}
                worker.named_query_details = {
                    query_id: detail
                    for query_id, detail in worker.named_query_details.items()
                    if query_id in accepted_ids
                }
                self._named_error_text = None
                self._notify("named_queries")
                self._notify("has_more_named_queries")
                self._notify("named_error_text")
                self._set_named_state(PaneState.IDLE if self.named_queries else PaneState.EMPTY)
            return
        except ProviderError as exc:
            if self._is_current_named(worker):
                self._named_state, self._named_error_text = map_provider_error(
                    exc,
                    fallback=_SAVED_ERROR,
                )
                self._notify("named_state")
                self._notify("named_error_text")
            return
        except Exception as exc:
            if self._is_current_named(worker):
                report_unexpected_service_error(
                    self._hub, service="athena", operation="list_named_queries", error=exc
                )
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
        except PagerCollectionLimitError:
            if self._is_current_prepared(worker):
                self._prepared_error_text = None
                self._notify("prepared_statements")
                self._notify("has_more_prepared_statements")
                self._notify("prepared_error_text")
                self._set_prepared_state(
                    PaneState.IDLE if self.prepared_statements else PaneState.EMPTY
                )
            return
        except ProviderError as exc:
            if self._is_current_prepared(worker):
                self._prepared_state, self._prepared_error_text = map_provider_error(
                    exc,
                    fallback=_PREPARED_ERROR,
                )
                self._notify("prepared_state")
                self._notify("prepared_error_text")
            return
        except Exception as exc:
            if self._is_current_prepared(worker):
                report_unexpected_service_error(
                    self._hub,
                    service="athena",
                    operation="list_prepared_statements",
                    error=exc,
                )
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

        worker.pager = SnapshotTokenPager(fetch, max_items=_MAX_SAVED_ITEMS)
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

        worker.pager = SnapshotTokenPager(fetch, max_items=_MAX_SAVED_ITEMS)
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
        self._finish_loading_more_named(old_worker)
        worker = self._make_named_worker()
        self._named_worker = worker
        self._named_pager = worker.pager
        self._retire_worker(old_worker)
        return worker

    def _replace_prepared_worker(self) -> _SavedWorker[PreparedStatementSummary]:
        old_worker = self._prepared_worker
        self._finish_loading_more_prepared(old_worker)
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
        return self._is_current_named(worker) and bool(worker.workgroup) and worker.pager.has_more

    def _can_load_more_prepared(
        self,
        worker: _SavedWorker[PreparedStatementSummary],
    ) -> bool:
        return (
            self._is_current_prepared(worker) and bool(worker.workgroup) and worker.pager.has_more
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

    def _begin_loading_more_named(
        self,
        worker: _SavedWorker[NamedQuerySummary],
    ) -> None:
        self._loading_more_named_worker = worker
        if not self._is_loading_more_named_queries:
            self._is_loading_more_named_queries = True
            self._notify("is_loading_more_named_queries")

    def _finish_loading_more_named(
        self,
        worker: _SavedWorker[NamedQuerySummary],
    ) -> None:
        if self._loading_more_named_worker is not worker:
            return
        self._loading_more_named_worker = None
        if self._is_loading_more_named_queries:
            self._is_loading_more_named_queries = False
            self._notify("is_loading_more_named_queries")

    def _begin_loading_more_prepared(
        self,
        worker: _SavedWorker[PreparedStatementSummary],
    ) -> None:
        self._loading_more_prepared_worker = worker
        if not self._is_loading_more_prepared_statements:
            self._is_loading_more_prepared_statements = True
            self._notify("is_loading_more_prepared_statements")

    def _finish_loading_more_prepared(
        self,
        worker: _SavedWorker[PreparedStatementSummary],
    ) -> None:
        if self._loading_more_prepared_worker is not worker:
            return
        self._loading_more_prepared_worker = None
        if self._is_loading_more_prepared_statements:
            self._is_loading_more_prepared_statements = False
            self._notify("is_loading_more_prepared_statements")

    def _notify(self, property_name: str) -> None:
        if self._disposed:
            return
        send_value_free(
            self._hub,
            PropertyChangedMessage.create(
                self,
                "athena.saved",
                property_name,
            ),
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


__all__ = ["AthenaSavedSnapshot", "AthenaSavedVM", "SavedQueryKind"]
