from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Generic, Literal, TypeAlias, TypeVar

import anyio
import reactivex as rx
from reactivex.subject import Subject
from vmx import ComponentVMOf, Message, MessageHub, PropertyChangedMessage
from vmx.collections.token_paged_composition import TokenPagedComposition
from vmx.lifecycle.status import ConstructionStatus
from vmx.services.dispatcher import Dispatcher

from aws_tui.domain.athena import (
    AthenaCatalogSummary,
    AthenaWorkgroupDetail,
    AthenaWorkgroupSummary,
)
from aws_tui.domain.data_catalog import DatabaseSummary
from aws_tui.domain.filesystem import ProviderError
from aws_tui.domain.query import QueryContext
from aws_tui.domain.sql_policy import ReadOnlySqlPolicy
from aws_tui.infra.connection_resolver import Connection
from aws_tui.vm.athena._errors import map_provider_error, map_unexpected_error
from aws_tui.vm.athena.history_vm import AthenaHistoryVM
from aws_tui.vm.athena.query_vm import AthenaQueryVM
from aws_tui.vm.athena.results_vm import AthenaResultsVM
from aws_tui.vm.athena.saved_vm import AthenaSavedVM
from aws_tui.vm.file_manager.pane_vm import PaneState
from aws_tui.vm.service_source_vm import (
    SelectionScope,
    ServiceSelectionStore,
    ServiceSourceContext,
)

AthenaView: TypeAlias = Literal["query", "history", "results", "saved"]
Sleep = Callable[[float], Awaitable[None]]
T = TypeVar("T")

_VIEWS = frozenset({"query", "history", "results", "saved"})
_CONTEXT_ERROR = "Athena context request failed"
_WORKGROUP_DETAIL_ERROR = "Athena workgroup request failed"


@dataclass(eq=False)
class _PageWorker(Generic[T]):
    generation: int
    pager: TokenPagedComposition[T, str] = field(init=False, repr=False)


class AthenaPageVM:
    def __init__(
        self,
        *,
        client: Any,
        policy: ReadOnlySqlPolicy,
        connection: Connection,
        hub: MessageHub[Message],
        dispatcher: Dispatcher,
        selection_store: ServiceSelectionStore | None = None,
        sleep: Sleep = anyio.sleep,
    ) -> None:
        self._client = client
        self._connection = connection
        self._hub = hub
        self._source = ServiceSourceContext.from_connection(connection)
        self._selection_scope = SelectionScope(
            "athena",
            connection.name,
            connection.region,
        )
        self._selection_store = selection_store or ServiceSelectionStore()
        self._disposed = False
        self._shutdown_started = False
        self._shutdown_complete = False
        self._active_view: AthenaView = "query"
        self._loaded_views: set[AthenaView] = set()
        self._lifecycle_generation = 0
        self._context_generation = 0
        self._workgroup_generation = 0
        self._catalog_generation = 0
        self._database_generation = 0
        self._setup_lock = asyncio.Lock()
        self._shutdown_lock = asyncio.Lock()
        self._page_tasks: set[asyncio.Task[Any]] = set()
        self._context = QueryContext(
            connection.name,
            connection.region,
            "",
            "",
            "",
        )
        self._workgroups_state = PaneState.EMPTY
        self._catalogs_state = PaneState.EMPTY
        self._databases_state = PaneState.EMPTY
        self._workgroups_error_text: str | None = None
        self._catalogs_error_text: str | None = None
        self._databases_error_text: str | None = None
        self._workgroup_detail: AthenaWorkgroupDetail | None = None
        self._workgroup_detail_state = PaneState.EMPTY
        self._workgroup_detail_error_text: str | None = None
        self._is_loading_more_workgroups = False
        self._is_loading_more_catalogs = False
        self._is_loading_more_databases = False
        self._on_property_changed: Subject[str] = Subject()
        self._inner: ComponentVMOf[None] = (
            ComponentVMOf[None]
            .builder()
            .name("athena.page")
            .model(None)
            .services(hub, dispatcher)
            .build()
        )
        self._workgroup_worker = self._make_workgroup_worker()
        self._catalog_worker = self._make_catalog_worker("")
        self._database_worker = self._make_database_worker("", "")
        self._workgroup_pager = self._workgroup_worker.pager
        self._catalog_pager = self._catalog_worker.pager
        self._database_pager = self._database_worker.pager
        self.query = AthenaQueryVM(
            client=client,
            policy=policy,
            context=self._context,
            hub=hub,
            dispatcher=dispatcher,
            sleep=sleep,
        )
        self.history = AthenaHistoryVM(
            client=client,
            workgroup="",
            hub=hub,
            dispatcher=dispatcher,
        )
        self.saved = AthenaSavedVM(
            client=client,
            workgroup="",
            hub=hub,
            dispatcher=dispatcher,
        )

    @property
    def connection(self) -> Connection:
        return self._connection

    @property
    def source(self) -> ServiceSourceContext:
        return self._source

    @property
    def client(self) -> Any:
        return self._client

    @property
    def context(self) -> QueryContext:
        return self._context

    @property
    def active_view(self) -> AthenaView:
        return self._active_view

    @property
    def results(self) -> AthenaResultsVM:
        return self.query.results

    @property
    def workgroups(self) -> tuple[AthenaWorkgroupSummary, ...]:
        return tuple(self._workgroup_pager.items)

    @property
    def catalogs(self) -> tuple[AthenaCatalogSummary, ...]:
        return tuple(self._catalog_pager.items)

    @property
    def databases(self) -> tuple[DatabaseSummary, ...]:
        return tuple(self._database_pager.items)

    @property
    def workgroup_detail(self) -> AthenaWorkgroupDetail | None:
        return self._workgroup_detail

    @property
    def workgroup_detail_state(self) -> PaneState:
        return self._workgroup_detail_state

    @property
    def workgroup_detail_error_text(self) -> str | None:
        return self._workgroup_detail_error_text

    @property
    def has_more_workgroups(self) -> bool:
        return self._workgroup_pager.current_token is not None

    @property
    def has_more_catalogs(self) -> bool:
        return self._catalog_pager.current_token is not None

    @property
    def has_more_databases(self) -> bool:
        return self._database_pager.current_token is not None

    @property
    def is_loading_more_workgroups(self) -> bool:
        return self._is_loading_more_workgroups

    @property
    def is_loading_more_catalogs(self) -> bool:
        return self._is_loading_more_catalogs

    @property
    def is_loading_more_databases(self) -> bool:
        return self._is_loading_more_databases

    @property
    def workgroups_state(self) -> PaneState:
        return self._workgroups_state

    @property
    def catalogs_state(self) -> PaneState:
        return self._catalogs_state

    @property
    def databases_state(self) -> PaneState:
        return self._databases_state

    @property
    def workgroups_error_text(self) -> str | None:
        return self._workgroups_error_text

    @property
    def catalogs_error_text(self) -> str | None:
        return self._catalogs_error_text

    @property
    def databases_error_text(self) -> str | None:
        return self._databases_error_text

    @property
    def status(self) -> ConstructionStatus:
        return self._inner.status

    @property
    def on_property_changed(self) -> rx.Observable[str]:
        return self._on_property_changed

    def construct(self) -> None:
        if not self._is_alive():
            return
        self._inner.construct()
        self.query.construct()
        self.history.construct()
        self.saved.construct()

    async def setup(self) -> None:
        async with self._setup_lock:
            if self._disposed or self._shutdown_started:
                return
            stored_view = self._selection_store.get(
                self._selection_scope,
                "active_view",
            )
            if stored_view in _VIEWS:
                self._active_view = stored_view  # type: ignore[assignment]
            elif stored_view is not None:
                self._selection_store.discard(
                    self._selection_scope,
                    "active_view",
                )
            await self.refresh_workgroups()
            if not self._is_alive():
                return
            if self._workgroups_state not in {PaneState.IDLE, PaneState.EMPTY}:
                return
            if self._workgroups_state is PaneState.EMPTY:
                self._clear_context_store()
                return
            stored_workgroup = self._selection_store.get(
                self._selection_scope,
                "workgroup",
            )
            workgroup_names = tuple(row.name for row in self.workgroups)
            workgroup = (
                stored_workgroup if stored_workgroup in workgroup_names else workgroup_names[0]
            )
            stored_catalog = self._selection_store.get(
                self._selection_scope,
                "catalog",
            )
            stored_database = self._selection_store.get(
                self._selection_scope,
                "database",
            )
            await self._select_workgroup(
                workgroup,
                preferred_catalog=stored_catalog,
                preferred_database=stored_database,
                setup_active=False,
            )
            if self._is_alive():
                await self._setup_view(
                    self._active_view,
                    self._context_generation,
                )

    async def select_view(self, view: AthenaView) -> None:
        if not self._is_alive():
            return
        if view not in _VIEWS:
            raise ValueError(f"unknown Athena view: {view}")
        changed = view != self._active_view
        self._active_view = view
        self._selection_store.set(self._selection_scope, "active_view", view)
        if changed:
            self._notify("active_view")
        await self._setup_view(view, self._context_generation)

    async def select_workgroup(self, workgroup: str) -> None:
        if not self._is_alive():
            return
        if not any(row.name == workgroup for row in self.workgroups):
            return
        await self._select_workgroup(
            workgroup,
            preferred_catalog=self._selection_store.get(
                self._selection_scope,
                "catalog",
            ),
            preferred_database=self._selection_store.get(
                self._selection_scope,
                "database",
            ),
            setup_active=True,
        )

    async def select_catalog(self, catalog: str) -> None:
        if not self._is_alive():
            return
        if not any(row.name == catalog for row in self.catalogs):
            return
        await self._select_catalog(
            catalog,
            preferred_database=self._selection_store.get(
                self._selection_scope,
                "database",
            ),
            setup_active=True,
        )

    async def select_database(self, database: str) -> None:
        if not self._is_alive():
            return
        if not any(row.ref.database_name == database for row in self.databases):
            return
        generation = self._begin_context_change(
            self._context.workgroup,
            self._context.catalog,
            database,
            clear_catalogs=False,
            clear_databases=False,
        )
        self._selection_store.set(self._selection_scope, "database", database)
        await self.query.set_context(self._context)
        if self._is_current_context(generation):
            await self._setup_view(self._active_view, generation)

    async def load_more_workgroups(self) -> None:
        if not self._is_alive():
            return
        worker = self._workgroup_worker
        if self.has_more_workgroups and self._is_current_workgroup(worker):
            self._set_loading_more("workgroups", True)
            try:
                await self._run_page_command(
                    worker.pager.load_more_command.execute_async,
                    worker,
                    "workgroups",
                )
            finally:
                self._set_loading_more("workgroups", False)

    async def load_more_catalogs(self) -> None:
        if not self._is_alive():
            return
        worker = self._catalog_worker
        if self.has_more_catalogs and self._is_current_catalog(worker):
            self._set_loading_more("catalogs", True)
            try:
                await self._run_page_command(
                    worker.pager.load_more_command.execute_async,
                    worker,
                    "catalogs",
                )
            finally:
                self._set_loading_more("catalogs", False)

    async def load_more_databases(self) -> None:
        if not self._is_alive():
            return
        worker = self._database_worker
        if self.has_more_databases and self._is_current_database(worker):
            self._set_loading_more("databases", True)
            try:
                await self._run_page_command(
                    worker.pager.load_more_command.execute_async,
                    worker,
                    "databases",
                )
            finally:
                self._set_loading_more("databases", False)

    async def select_history_execution(self, execution_id: str) -> None:
        if not self._is_alive():
            return
        await self.history.select_execution(execution_id)
        if self.history.selected_execution_id == execution_id:
            self._selection_store.set(
                self._selection_scope,
                "history_execution_id",
                execution_id,
            )

    async def select_named_query(self, query_id: str) -> None:
        if not self._is_alive():
            return
        lifecycle_generation = self._lifecycle_generation
        context_generation = self._context_generation
        await self.saved.select_named_query(query_id)
        if lifecycle_generation != self._lifecycle_generation or not self._is_current_context(
            context_generation
        ):
            return
        if self.saved.selected_query_id == query_id:
            self._selection_store.set(
                self._selection_scope,
                "saved_query_id",
                query_id,
            )

    async def select_prepared_statement(self, name: str) -> None:
        if not self._is_alive():
            return
        lifecycle_generation = self._lifecycle_generation
        context_generation = self._context_generation
        await self.saved.select_prepared_statement(name)
        if lifecycle_generation != self._lifecycle_generation or not self._is_current_context(
            context_generation
        ):
            return
        if self.saved.selected_query_id == name:
            self._selection_store.set(
                self._selection_scope,
                "saved_query_id",
                name,
            )

    async def open_saved_in_editor(self) -> None:
        if not self._is_alive():
            return
        sql = self.saved.selected_sql()
        if sql is None:
            return
        selected_id = self.saved.selected_query_id
        if selected_id is not None:
            self._selection_store.set(
                self._selection_scope,
                "saved_query_id",
                selected_id,
            )
        self.query.set_sql(sql)
        await self.select_view("query")

    async def open_history_results(self) -> None:
        if not self._is_alive():
            return
        execution_id = self.history.selected_execution_id
        if execution_id is None:
            return
        context_generation = self._context_generation
        task = asyncio.current_task()
        await self.results.load(execution_id)
        if (
            (task is not None and task.cancelling())
            or not self._is_current_context(context_generation)
            or self.history.selected_execution_id != execution_id
        ):
            return
        await self.select_view("results")

    async def refresh_workgroups(self) -> None:
        if not self._is_alive():
            return
        self._workgroup_generation += 1
        worker = self._replace_workgroup_worker()
        self._workgroups_error_text = None
        self._workgroups_state = PaneState.LOADING
        self._notify_context_lists()
        await self._run_page_command(
            worker.pager.refresh_command.execute_async,
            worker,
            "workgroups",
        )

    async def refresh_query_context(self) -> None:
        if not self._is_alive():
            return
        workgroup = self._context.workgroup
        await self.refresh_workgroups()
        if (
            not self._is_alive()
            or self._workgroups_state not in {PaneState.IDLE, PaneState.EMPTY}
            or self._workgroup_detail_state is PaneState.IDLE
            or workgroup not in {row.name for row in self.workgroups}
        ):
            return
        await self._select_workgroup(
            workgroup,
            preferred_catalog=self._selection_store.get(
                self._selection_scope,
                "catalog",
            ),
            preferred_database=self._selection_store.get(
                self._selection_scope,
                "database",
            ),
            setup_active=True,
        )

    async def shutdown(self) -> None:
        async with self._shutdown_lock:
            if self._disposed or self._shutdown_complete:
                return
            self._shutdown_started = True
            self._lifecycle_generation += 1
            self._context_generation += 1
            self._workgroup_generation += 1
            self._catalog_generation += 1
            self._database_generation += 1
            self._context = QueryContext(
                self._connection.name,
                self._connection.region,
                "",
                "",
                "",
            )
            self._workgroup_pager.dispose()
            self._catalog_pager.dispose()
            self._database_pager.dispose()
            self._workgroups_state = PaneState.EMPTY
            self._catalogs_state = PaneState.EMPTY
            self._databases_state = PaneState.EMPTY
            self._workgroup_detail = None
            self._workgroup_detail_state = PaneState.EMPTY
            self._workgroup_detail_error_text = None
            self._notify("context")
            self._notify_context_lists()
            self._notify_workgroup_detail()
            await asyncio.gather(
                self.query.shutdown(),
                self.history.shutdown(),
                self.saved.shutdown(),
            )
            await self._drain_page_tasks()
            self._shutdown_complete = True

    def dispose(self) -> None:
        if self._disposed:
            return
        self._disposed = True
        self._lifecycle_generation += 1
        self._context_generation += 1
        self._workgroup_pager.dispose()
        self._catalog_pager.dispose()
        self._database_pager.dispose()
        self.saved.dispose()
        self.history.dispose()
        self.query.dispose()
        self._on_property_changed.on_completed()
        self._on_property_changed.dispose()
        self._inner.dispose()

    async def _select_workgroup(
        self,
        workgroup: str,
        *,
        preferred_catalog: str | None,
        preferred_database: str | None,
        setup_active: bool,
    ) -> None:
        generation = self._begin_context_change(
            workgroup,
            "",
            "",
            clear_catalogs=True,
            clear_databases=True,
        )
        self._selection_store.set(self._selection_scope, "workgroup", workgroup)
        await self.query.set_context(self._context)
        if not self._is_current_context(generation):
            return
        if not await self._load_workgroup_detail(workgroup, generation):
            return
        await self._refresh_catalogs(generation)
        if not self._is_current_context(generation):
            return
        if self._catalogs_state not in {PaneState.IDLE, PaneState.EMPTY}:
            return
        catalog_names = tuple(row.name for row in self.catalogs)
        catalog = (
            preferred_catalog
            if preferred_catalog in catalog_names
            else next(iter(catalog_names), None)
        )
        if catalog is None:
            self._selection_store.discard(self._selection_scope, "catalog")
            self._selection_store.discard(self._selection_scope, "database")
            return
        await self._select_catalog(
            catalog,
            preferred_database=preferred_database,
            setup_active=setup_active,
        )

    async def _select_catalog(
        self,
        catalog: str,
        *,
        preferred_database: str | None,
        setup_active: bool,
    ) -> None:
        generation = self._begin_context_change(
            self._context.workgroup,
            catalog,
            "",
            clear_catalogs=False,
            clear_databases=True,
        )
        self._selection_store.set(self._selection_scope, "catalog", catalog)
        await self.query.set_context(self._context)
        if not self._is_current_context(generation):
            return
        await self._refresh_databases(generation)
        if not self._is_current_context(generation):
            return
        if self._databases_state not in {PaneState.IDLE, PaneState.EMPTY}:
            return
        database_names = tuple(row.ref.database_name for row in self.databases)
        database = (
            preferred_database
            if preferred_database in database_names
            else next(iter(database_names), None)
        )
        if database is None:
            self._selection_store.discard(self._selection_scope, "database")
            return
        final_generation = self._begin_context_change(
            self._context.workgroup,
            catalog,
            database,
            clear_catalogs=False,
            clear_databases=False,
        )
        self._selection_store.set(self._selection_scope, "database", database)
        await self.query.set_context(self._context)
        if setup_active and self._is_current_context(final_generation):
            await self._setup_view(self._active_view, final_generation)

    def _begin_context_change(
        self,
        workgroup: str,
        catalog: str,
        database: str,
        *,
        clear_catalogs: bool,
        clear_databases: bool,
    ) -> int:
        self._context_generation += 1
        generation = self._context_generation
        self._context = QueryContext(
            self._connection.name,
            self._connection.region,
            workgroup,
            catalog,
            database,
        )
        self.results.clear()
        self.history.replace_workgroup(workgroup)
        self.saved.replace_workgroup(workgroup)
        self._loaded_views.discard("history")
        self._loaded_views.discard("saved")
        if clear_catalogs:
            self._catalog_generation += 1
            self._replace_catalog_worker(workgroup)
            self._catalogs_state = PaneState.EMPTY
            self._catalogs_error_text = None
        if clear_databases:
            self._database_generation += 1
            self._replace_database_worker(workgroup, catalog)
            self._databases_state = PaneState.EMPTY
            self._databases_error_text = None
        self._notify("context")
        self._notify_context_lists()
        return generation

    async def _load_workgroup_detail(
        self,
        workgroup: str,
        context_generation: int,
    ) -> bool:
        self._workgroup_detail = None
        self._workgroup_detail_state = PaneState.LOADING
        self._workgroup_detail_error_text = None
        self._notify_workgroup_detail()
        task = asyncio.current_task()
        if task is not None:
            self._page_tasks.add(task)
        try:
            detail = await self._client.get_workgroup(workgroup)
            if detail.summary.name != workgroup:
                raise ValueError("Athena workgroup response identity mismatch")
        except ProviderError as exc:
            if self._is_current_context(context_generation):
                state, error_text = map_provider_error(
                    exc,
                    fallback=_WORKGROUP_DETAIL_ERROR,
                )
                self._workgroup_detail_state = state
                self._workgroup_detail_error_text = error_text
                self._notify_workgroup_detail()
            return False
        except Exception:
            if self._is_current_context(context_generation):
                state, error_text = map_unexpected_error(
                    fallback=_WORKGROUP_DETAIL_ERROR,
                )
                self._workgroup_detail_state = state
                self._workgroup_detail_error_text = error_text
                self._notify_workgroup_detail()
            return False
        finally:
            if task is not None:
                self._page_tasks.discard(task)
        if not self._is_current_context(context_generation):
            return False
        self._workgroup_detail = detail
        self._workgroup_detail_state = PaneState.IDLE
        self._workgroup_detail_error_text = None
        self._notify_workgroup_detail()
        return True

    async def _refresh_catalogs(self, context_generation: int) -> None:
        worker = self._catalog_worker
        self._catalogs_error_text = None
        self._catalogs_state = PaneState.LOADING
        self._notify_context_lists()
        await self._run_page_command(
            worker.pager.refresh_command.execute_async,
            worker,
            "catalogs",
        )
        if not self._is_current_context(context_generation):
            return

    async def _refresh_databases(self, context_generation: int) -> None:
        worker = self._database_worker
        self._databases_error_text = None
        self._databases_state = PaneState.LOADING
        self._notify_context_lists()
        await self._run_page_command(
            worker.pager.refresh_command.execute_async,
            worker,
            "databases",
        )
        if not self._is_current_context(context_generation):
            return

    async def _setup_view(self, view: AthenaView, generation: int) -> None:
        if (
            not self._is_current_context(generation)
            or view in self._loaded_views
            or view in {"query", "results"}
        ):
            return
        if view == "history":
            await self.history.setup()
            if not self._is_current_context(generation):
                return
            await self._restore_history_selection()
        else:
            await self.saved.setup()
            if not self._is_current_context(generation):
                return
            await self._restore_saved_selection()
        if self._is_current_context(generation):
            self._loaded_views.add(view)

    async def _restore_history_selection(self) -> None:
        stored_id = self._selection_store.get(
            self._selection_scope,
            "history_execution_id",
        )
        ids = tuple(row.ref.execution_id for row in self.history.items)
        selected_id = stored_id if stored_id in ids else next(iter(ids), None)
        if selected_id is None:
            self._selection_store.discard(
                self._selection_scope,
                "history_execution_id",
            )
            return
        await self.history.select_execution(selected_id)
        if self.history.selected_execution_id == selected_id:
            self._selection_store.set(
                self._selection_scope,
                "history_execution_id",
                selected_id,
            )

    async def _restore_saved_selection(self) -> None:
        stored_id = self._selection_store.get(
            self._selection_scope,
            "saved_query_id",
        )
        named_ids = tuple(query.query_id for query in self.saved.named_queries)
        prepared_names = tuple(summary.name for summary in self.saved.prepared_statements)
        if stored_id in named_ids:
            await self.saved.select_named_query(stored_id)
        elif stored_id in prepared_names:
            await self.saved.select_prepared_statement(stored_id)
        elif named_ids:
            await self.saved.select_named_query(named_ids[0])
        elif prepared_names:
            await self.saved.select_prepared_statement(prepared_names[0])
        selected_id = self.saved.selected_query_id
        if selected_id is None:
            self._selection_store.discard(
                self._selection_scope,
                "saved_query_id",
            )
        else:
            self._selection_store.set(
                self._selection_scope,
                "saved_query_id",
                selected_id,
            )

    async def _run_page_command(
        self,
        command: Callable[[], Awaitable[None]],
        worker: _PageWorker[Any],
        kind: Literal["workgroups", "catalogs", "databases"],
    ) -> None:
        task = asyncio.current_task()
        if task is not None:
            self._page_tasks.add(task)
        try:
            await command()
        except ProviderError as exc:
            if self._is_current_worker(worker, kind):
                state, error_text = map_provider_error(
                    exc,
                    fallback=_CONTEXT_ERROR,
                )
                self._set_list_error(kind, state, error_text)
            return
        except Exception:
            if self._is_current_worker(worker, kind):
                state, error_text = map_unexpected_error(
                    fallback=_CONTEXT_ERROR,
                )
                self._set_list_error(kind, state, error_text)
            return
        finally:
            if task is not None:
                self._page_tasks.discard(task)
        if not self._is_current_worker(worker, kind):
            return
        items = {
            "workgroups": self.workgroups,
            "catalogs": self.catalogs,
            "databases": self.databases,
        }[kind]
        self._set_list_state(kind, PaneState.IDLE if items else PaneState.EMPTY)
        self._notify_context_lists()

    def _make_workgroup_worker(self) -> _PageWorker[AthenaWorkgroupSummary]:
        generation = self._workgroup_generation
        worker: _PageWorker[AthenaWorkgroupSummary] = _PageWorker(generation)

        async def fetch(
            token: str | None,
        ) -> tuple[list[AthenaWorkgroupSummary], str | None]:
            rows, next_token = await self._client.list_workgroups_page(
                start_token=token,
            )
            if not self._is_current_workgroup(worker):
                return [], None
            return rows, next_token

        worker.pager = TokenPagedComposition(fetch)
        return worker

    def _make_catalog_worker(
        self,
        workgroup: str,
    ) -> _PageWorker[AthenaCatalogSummary]:
        generation = self._catalog_generation
        worker: _PageWorker[AthenaCatalogSummary] = _PageWorker(generation)

        async def fetch(
            token: str | None,
        ) -> tuple[list[AthenaCatalogSummary], str | None]:
            if not workgroup:
                return [], None
            rows, next_token = await self._client.list_catalogs_page(
                workgroup=workgroup,
                start_token=token,
            )
            if not self._is_current_catalog(worker):
                return [], None
            return rows, next_token

        worker.pager = TokenPagedComposition(fetch)
        return worker

    def _make_database_worker(
        self,
        workgroup: str,
        catalog: str,
    ) -> _PageWorker[DatabaseSummary]:
        generation = self._database_generation
        worker: _PageWorker[DatabaseSummary] = _PageWorker(generation)

        async def fetch(
            token: str | None,
        ) -> tuple[list[DatabaseSummary], str | None]:
            if not workgroup or not catalog:
                return [], None
            rows, next_token = await self._client.list_databases_page(
                catalog,
                workgroup=workgroup,
                start_token=token,
            )
            if not self._is_current_database(worker):
                return [], None
            for row in rows:
                if (
                    row.ref.connection_name != self._connection.name
                    or row.ref.region != self._connection.region
                    or row.ref.catalog_name != catalog
                ):
                    raise ValueError("Athena database response identity mismatch")
            return rows, next_token

        worker.pager = TokenPagedComposition(fetch)
        return worker

    def _replace_workgroup_worker(self) -> _PageWorker[AthenaWorkgroupSummary]:
        old_worker = self._workgroup_worker
        worker = self._make_workgroup_worker()
        self._workgroup_worker = worker
        self._workgroup_pager = worker.pager
        old_worker.pager.dispose()
        return worker

    def _replace_catalog_worker(
        self,
        workgroup: str,
    ) -> _PageWorker[AthenaCatalogSummary]:
        old_worker = self._catalog_worker
        worker = self._make_catalog_worker(workgroup)
        self._catalog_worker = worker
        self._catalog_pager = worker.pager
        old_worker.pager.dispose()
        return worker

    def _replace_database_worker(
        self,
        workgroup: str,
        catalog: str,
    ) -> _PageWorker[DatabaseSummary]:
        old_worker = self._database_worker
        worker = self._make_database_worker(workgroup, catalog)
        self._database_worker = worker
        self._database_pager = worker.pager
        old_worker.pager.dispose()
        return worker

    def _is_current_workgroup(
        self,
        worker: _PageWorker[AthenaWorkgroupSummary],
    ) -> bool:
        return (
            worker is self._workgroup_worker
            and worker.generation == self._workgroup_generation
            and self._is_alive()
        )

    def _is_current_catalog(
        self,
        worker: _PageWorker[AthenaCatalogSummary],
    ) -> bool:
        return (
            worker is self._catalog_worker
            and worker.generation == self._catalog_generation
            and self._is_alive()
        )

    def _is_current_database(
        self,
        worker: _PageWorker[DatabaseSummary],
    ) -> bool:
        return (
            worker is self._database_worker
            and worker.generation == self._database_generation
            and self._is_alive()
        )

    def _is_current_worker(
        self,
        worker: _PageWorker[Any],
        kind: Literal["workgroups", "catalogs", "databases"],
    ) -> bool:
        if kind == "workgroups":
            return worker is self._workgroup_worker and self._is_alive()
        if kind == "catalogs":
            return worker is self._catalog_worker and self._is_alive()
        return worker is self._database_worker and self._is_alive()

    def _is_current_context(self, generation: int) -> bool:
        return generation == self._context_generation and self._is_alive()

    def _is_alive(self) -> bool:
        return not self._disposed and not self._shutdown_started

    async def _drain_page_tasks(self) -> None:
        current = asyncio.current_task()
        while True:
            tasks = {task for task in self._page_tasks if task is not current and not task.done()}
            if not tasks:
                return
            await asyncio.gather(*tasks, return_exceptions=True)

    def _set_list_error(
        self,
        kind: Literal["workgroups", "catalogs", "databases"],
        state: PaneState,
        error_text: str,
    ) -> None:
        if kind == "workgroups":
            self._workgroups_state = state
            self._workgroups_error_text = error_text
        elif kind == "catalogs":
            self._catalogs_state = state
            self._catalogs_error_text = error_text
        else:
            self._databases_state = state
            self._databases_error_text = error_text
        self._notify_context_lists()

    def _set_list_state(
        self,
        kind: Literal["workgroups", "catalogs", "databases"],
        state: PaneState,
    ) -> None:
        if kind == "workgroups":
            self._workgroups_state = state
            if state in {PaneState.IDLE, PaneState.EMPTY}:
                self._workgroups_error_text = None
        elif kind == "catalogs":
            self._catalogs_state = state
            if state in {PaneState.IDLE, PaneState.EMPTY}:
                self._catalogs_error_text = None
        else:
            self._databases_state = state
            if state in {PaneState.IDLE, PaneState.EMPTY}:
                self._databases_error_text = None

    def _set_loading_more(
        self,
        kind: Literal["workgroups", "catalogs", "databases"],
        value: bool,
    ) -> None:
        attribute = f"_is_loading_more_{kind}"
        if getattr(self, attribute) == value:
            return
        setattr(self, attribute, value)
        self._notify(f"is_loading_more_{kind}")

    def _notify_context_lists(self) -> None:
        for property_name in (
            "workgroups",
            "catalogs",
            "databases",
            "has_more_workgroups",
            "has_more_catalogs",
            "has_more_databases",
            "workgroups_state",
            "catalogs_state",
            "databases_state",
            "workgroups_error_text",
            "catalogs_error_text",
            "databases_error_text",
        ):
            self._notify(property_name)

    def _notify_workgroup_detail(self) -> None:
        for property_name in (
            "workgroup_detail",
            "workgroup_detail_state",
            "workgroup_detail_error_text",
        ):
            self._notify(property_name)

    def _clear_context_store(self) -> None:
        for key in ("workgroup", "catalog", "database"):
            self._selection_store.discard(self._selection_scope, key)

    def _notify(self, property_name: str) -> None:
        if self._disposed:
            return
        self._hub.send(
            PropertyChangedMessage.create(
                self,
                "athena.page",
                property_name,
            )
        )
        self._on_property_changed.on_next(property_name)


__all__ = ["AthenaPageVM", "AthenaView"]
