from __future__ import annotations

from typing import Any, Literal, TypeAlias

from vmx import ComponentVMOf, Message, MessageHub, PropertyChangedMessage
from vmx.services.dispatcher import Dispatcher

from aws_tui.domain.data_catalog import TableRef
from aws_tui.infra.connection_resolver import Connection
from aws_tui.vm.file_manager.pane_vm import PaneState
from aws_tui.vm.glue.catalog_vm import GlueCatalogVM
from aws_tui.vm.glue.crawlers_vm import GlueCrawlersVM
from aws_tui.vm.glue.jobs_vm import GlueJobsVM
from aws_tui.vm.service_source_vm import (
    SelectionScope,
    ServiceSelectionStore,
    ServiceSourceContext,
)

GlueView: TypeAlias = Literal["catalog", "jobs", "crawlers"]
_VIEWS = frozenset({"catalog", "jobs", "crawlers"})
_SUCCESS_STATES = frozenset({PaneState.IDLE, PaneState.EMPTY})


class GluePageVM:
    def __init__(
        self,
        *,
        client: Any,
        connection: Connection,
        hub: MessageHub[Message],
        dispatcher: Dispatcher,
        selection_store: ServiceSelectionStore | None = None,
    ) -> None:
        self._client = client
        self._connection = connection
        self._hub = hub
        self._dispatcher = dispatcher
        self._source = ServiceSourceContext.from_connection(connection)
        self._selection_scope = SelectionScope("glue", connection.name, connection.region)
        self._selection_store = selection_store or ServiceSelectionStore()
        self._active_view: GlueView = "catalog"
        self._loaded_views: set[GlueView] = set()
        self._disposed = False
        self._lifecycle_generation = 0
        self._inner: ComponentVMOf[None] = (
            ComponentVMOf[None]
            .builder()
            .name("glue.page")
            .model(None)
            .services(hub, dispatcher)
            .build()
        )
        self.catalog = GlueCatalogVM(client=client, hub=hub, dispatcher=dispatcher)
        self.jobs = GlueJobsVM(client=client, hub=hub, dispatcher=dispatcher)
        self.crawlers = GlueCrawlersVM(client=client, hub=hub, dispatcher=dispatcher)

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
    def hub(self) -> MessageHub[Message]:
        return self._hub

    @property
    def dispatcher(self) -> Dispatcher:
        return self._dispatcher

    @property
    def active_view(self) -> GlueView:
        return self._active_view

    def construct(self) -> None:
        self._inner.construct()
        self.catalog.construct()
        self.jobs.construct()
        self.crawlers.construct()

    def dispose(self) -> None:
        if self._disposed:
            return
        self._disposed = True
        self._lifecycle_generation += 1
        self.crawlers.dispose()
        self.jobs.dispose()
        self.catalog.dispose()
        self._inner.dispose()

    async def setup(self) -> None:
        if self._disposed:
            return
        generation = self._lifecycle_generation
        stored_view = self._selection_store.get(self._selection_scope, "active_view")
        if stored_view in _VIEWS:
            self._active_view = stored_view  # type: ignore[assignment]
        elif stored_view is not None:
            self._selection_store.discard(self._selection_scope, "active_view")
        await self._setup_view(self._active_view, generation)

    async def select_view(self, view: GlueView) -> None:
        if view not in _VIEWS:
            raise ValueError(f"unknown Glue view: {view}")
        if self._disposed:
            return
        generation = self._lifecycle_generation
        changed = view != self._active_view
        self._active_view = view
        self._selection_store.set(self._selection_scope, "active_view", view)
        if changed:
            self._notify("active_view")
        await self._setup_view(view, generation)

    async def select_database(self, database_name: str) -> None:
        generation = self._lifecycle_generation
        await self._select_database(
            database_name,
            preferred_table=None,
            generation=generation,
        )

    async def select_table(self, table_name: str) -> None:
        generation = self._lifecycle_generation
        await self._select_table(table_name, generation)

    async def open_table(self, table_ref: TableRef) -> None:
        """Open an exact table and persist its destination selection."""
        if (
            self._disposed
            or table_ref.connection_name != self._connection.name
            or table_ref.region != self._connection.region
        ):
            raise ValueError("table is unavailable in the active Glue source")
        generation = self._lifecycle_generation
        await self.select_view("catalog")
        if not self._is_current(generation):
            raise ValueError("table is unavailable in the active Glue source")
        await self.catalog.open_table(table_ref)
        if not self._is_current(generation):
            raise ValueError("table is unavailable in the active Glue source")
        self._selection_store.set(
            self._selection_scope,
            "database_name",
            table_ref.database_name,
        )
        self._selection_store.set(
            self._selection_scope,
            "table_name",
            table_ref.table_name,
        )

    async def _select_table(self, table_name: str, generation: int) -> None:
        await self.catalog.select_table(table_name)
        if not self._is_current(generation):
            return
        if self.catalog.selected_table_name == table_name:
            self._selection_store.set(self._selection_scope, "table_name", table_name)

    async def select_job(self, job_name: str) -> None:
        generation = self._lifecycle_generation
        await self._select_job(job_name, generation)

    async def _select_job(self, job_name: str, generation: int) -> None:
        await self.jobs.select_job(job_name)
        if not self._is_current(generation):
            return
        if self.jobs.selected_job_name == job_name:
            self._selection_store.set(self._selection_scope, "job_name", job_name)

    async def set_job_run_states(self, states: frozenset[str]) -> None:
        generation = self._lifecycle_generation
        await self.jobs.set_run_state_filter(states)
        if not self._is_current(generation):
            return
        self._selection_store.set(
            self._selection_scope,
            "job_run_states",
            ",".join(sorted(states)),
        )

    async def select_crawler(self, name: str) -> None:
        generation = self._lifecycle_generation
        await self._select_crawler(name, generation)

    async def _select_crawler(self, name: str, generation: int) -> None:
        await self.crawlers.select_crawler(name)
        if not self._is_current(generation):
            return
        if self.crawlers.selected_crawler_name == name:
            self._selection_store.set(self._selection_scope, "crawler_name", name)

    async def set_crawler_state(self, state: str | None) -> None:
        generation = self._lifecycle_generation
        await self.crawlers.set_state_filter(state)
        if not self._is_current(generation):
            return
        if state is None:
            self._selection_store.discard(self._selection_scope, "crawler_state")
        else:
            self._selection_store.set(self._selection_scope, "crawler_state", state)
        if self.crawlers.state not in _SUCCESS_STATES:
            return
        stored_name = self._selection_store.get(self._selection_scope, "crawler_name")
        crawler_names = tuple(crawler.name for crawler in self.crawlers.crawlers)
        selected = stored_name if stored_name in crawler_names else next(iter(crawler_names), None)
        if selected is None:
            self._selection_store.discard(self._selection_scope, "crawler_name")
            return
        await self._select_crawler(selected, generation)

    async def refresh_active(self) -> None:
        if self._disposed:
            return
        generation = self._lifecycle_generation
        if self._active_view == "catalog":
            self._loaded_views.discard("catalog")
        elif self._active_view == "jobs":
            self._loaded_views.discard("jobs")
        else:
            self._loaded_views.discard("crawlers")
        await self._setup_view(self._active_view, generation)

    async def _setup_view(self, view: GlueView, generation: int) -> None:
        if not self._is_current(generation) or view in self._loaded_views:
            return
        if view == "catalog":
            await self._setup_catalog(generation)
        elif view == "jobs":
            await self._setup_jobs(generation)
        else:
            await self._setup_crawlers(generation)
        if not self._is_current(generation):
            return
        self._loaded_views.add(view)

    async def _setup_catalog(self, generation: int) -> None:
        await self.catalog.setup()
        if not self._is_current(generation):
            return
        if self.catalog.databases_state not in _SUCCESS_STATES:
            return
        stored_database = self._selection_store.get(self._selection_scope, "database_name")
        database_names = tuple(row.ref.database_name for row in self.catalog.databases)
        database_name = (
            stored_database
            if stored_database in database_names
            else next(iter(database_names), None)
        )
        if database_name is None:
            self._selection_store.discard(self._selection_scope, "database_name")
            self._selection_store.discard(self._selection_scope, "table_name")
            return
        stored_table = self._selection_store.get(self._selection_scope, "table_name")
        await self._select_database(
            database_name,
            preferred_table=stored_table,
            generation=generation,
        )

    async def _select_database(
        self,
        database_name: str,
        *,
        preferred_table: str | None,
        generation: int,
    ) -> None:
        await self.catalog.select_database(database_name)
        if not self._is_current(generation):
            return
        if self.catalog.selected_database_name != database_name:
            return
        self._selection_store.set(self._selection_scope, "database_name", database_name)
        if self.catalog.tables_state not in _SUCCESS_STATES:
            return
        table_names = tuple(row.ref.table_name for row in self.catalog.tables)
        table_name = (
            preferred_table if preferred_table in table_names else next(iter(table_names), None)
        )
        if table_name is None:
            self._selection_store.discard(self._selection_scope, "table_name")
            return
        await self._select_table(table_name, generation)

    async def _setup_jobs(self, generation: int) -> None:
        stored_states = self._selection_store.get(self._selection_scope, "job_run_states")
        if stored_states is not None:
            states = frozenset(state for state in stored_states.split(",") if state)
            await self.jobs.set_run_state_filter(states)
            if not self._is_current(generation):
                return
        await self.jobs.setup()
        if not self._is_current(generation):
            return
        if self.jobs.jobs_state not in _SUCCESS_STATES:
            return
        stored_job = self._selection_store.get(self._selection_scope, "job_name")
        job_names = tuple(job.name for job in self.jobs.jobs)
        job_name = stored_job if stored_job in job_names else next(iter(job_names), None)
        if job_name is None:
            self._selection_store.discard(self._selection_scope, "job_name")
            return
        await self._select_job(job_name, generation)

    async def _setup_crawlers(self, generation: int) -> None:
        stored_state = self._selection_store.get(self._selection_scope, "crawler_state")
        if stored_state is None:
            await self.crawlers.setup()
        elif stored_state != self.crawlers.state_filter:
            await self.crawlers.set_state_filter(stored_state)
        else:
            await self.crawlers.setup()
        if not self._is_current(generation):
            return
        if self.crawlers.state not in _SUCCESS_STATES:
            return
        stored_name = self._selection_store.get(self._selection_scope, "crawler_name")
        crawler_names = tuple(crawler.name for crawler in self.crawlers.crawlers)
        crawler_name = (
            stored_name if stored_name in crawler_names else next(iter(crawler_names), None)
        )
        if crawler_name is None:
            self._selection_store.discard(self._selection_scope, "crawler_name")
            return
        await self._select_crawler(crawler_name, generation)

    def _is_current(self, generation: int) -> bool:
        return not self._disposed and generation == self._lifecycle_generation

    def _notify(self, property_name: str) -> None:
        if self._disposed:
            return
        self._hub.send(PropertyChangedMessage.create(self, "glue.page", property_name))


__all__ = ["GluePageVM", "GlueView"]
