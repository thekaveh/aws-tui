from __future__ import annotations

import asyncio
import traceback
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pytest
from vmx import NULL_DISPATCHER, MessageHub, TokenPagedComposition
from vmx.lifecycle.status import ConstructionStatus
from vmx.messages.protocols import Message

from aws_tui.domain.athena import (
    AthenaCatalogSummary,
    AthenaWorkgroupDetail,
    AthenaWorkgroupSummary,
)
from aws_tui.domain.data_catalog import DatabaseRef, DatabaseSummary, TableRef
from aws_tui.domain.filesystem import (
    PermissionDeniedError,
    ProviderError,
    ProviderUnreachableError,
    ThrottledError,
)
from aws_tui.domain.query import (
    NamedQuery,
    PreparedStatement,
    PreparedStatementSummary,
    QueryContext,
    QueryExecutionDetail,
    QueryExecutionRef,
    QueryExecutionSummary,
    QueryState,
    QueryStatistics,
    ResultColumn,
    ResultPage,
)
from aws_tui.domain.sql_policy import ReadOnlySqlPolicy
from aws_tui.infra.connection_resolver import Connection
from aws_tui.infra.crash_dump import CrashDump
from aws_tui.vm.athena.page_vm import AthenaPageVM
from aws_tui.vm.file_manager.pane_vm import PaneState
from aws_tui.vm.messages import OpenGlueTableRequest
from aws_tui.vm.service_source_vm import SelectionScope, ServiceSelectionStore

_STATS = QueryStatistics(1, 1, 1, 1, 0, False)
_COLUMN = ResultColumn("value", "integer", "NULLABLE")


class PageClient:
    def __init__(
        self,
        *,
        connection_name: str = "analytics",
        region: str = "us-west-2",
    ) -> None:
        self.connection_name = connection_name
        self.region = region
        self.workgroups = [
            AthenaWorkgroupSummary("primary", "ENABLED", None, None),
            AthenaWorkgroupSummary("analysts", "ENABLED", None, None),
        ]
        self.catalogs = {
            "primary": [
                AthenaCatalogSummary("AwsDataCatalog", "LAMBDA", None),
                AthenaCatalogSummary("federated", "LAMBDA", None),
            ],
            "analysts": [AthenaCatalogSummary("AwsDataCatalog", "LAMBDA", None)],
        }
        self.databases = {
            ("primary", "AwsDataCatalog"): ["default"],
            ("primary", "federated"): ["remote"],
            ("analysts", "AwsDataCatalog"): ["events"],
        }
        self.workgroup_calls: list[str | None] = []
        self.workgroup_detail_calls: list[str] = []
        self.catalog_calls: list[tuple[str, str | None]] = []
        self.database_calls: list[tuple[str, str, str | None]] = []
        self.history_calls: list[tuple[str, str | None]] = []
        self.named_calls: list[tuple[str, str | None]] = []
        self.prepared_calls: list[tuple[str, str | None]] = []
        self.start_calls: list[tuple[str, QueryContext, str]] = []
        self.stop_calls: list[str] = []
        self.result_calls: list[tuple[str, str | None]] = []
        self.block_catalog_for: str | None = None
        self.workgroup_error: ProviderError | None = None
        self.workgroup_detail_error: ProviderError | None = None
        self.catalog_error: ProviderError | None = None
        self.database_error: ProviderError | None = None
        self.catalog_started = asyncio.Event()
        self.release_catalog = asyncio.Event()
        self.block_workgroup_detail_for: str | None = None
        self.workgroup_detail_started = asyncio.Event()
        self.release_workgroup_detail = asyncio.Event()
        self.block_results = False
        self.ignore_results_cancellation = False
        self.results_started = asyncio.Event()
        self.results_cancelled = asyncio.Event()
        self.release_results = asyncio.Event()
        self.block_prepared_detail = False
        self.ignore_prepared_detail_cancellation = False
        self.prepared_detail_started = asyncio.Event()
        self.prepared_detail_cancelled = asyncio.Event()
        self.release_prepared_detail = asyncio.Event()
        self.named = NamedQuery(
            "named-1",
            "Event count",
            None,
            "events",
            "SELECT count(*) FROM events",
            "analysts",
        )
        self.prepared = PreparedStatement(
            "prepared-1",
            "SELECT * FROM events WHERE id = ?",
            "analysts",
            "One event",
            datetime(2026, 7, 25, tzinfo=UTC),
        )
        self.workgroup_details = {
            "primary": AthenaWorkgroupDetail(
                self.workgroups[0],
                "s3://athena-results/primary/",
                True,
                True,
                None,
                "Athena engine version 3",
                False,
            ),
            "analysts": AthenaWorkgroupDetail(
                self.workgroups[1],
                None,
                True,
                False,
                1_000_000,
                "Athena engine version 3",
                True,
            ),
        }

    async def list_workgroups_page(
        self,
        *,
        start_token: str | None = None,
    ) -> tuple[list[AthenaWorkgroupSummary], str | None]:
        self.workgroup_calls.append(start_token)
        if self.workgroup_error is not None:
            raise self.workgroup_error
        return list(self.workgroups), None

    async def get_workgroup(self, name: str) -> AthenaWorkgroupDetail:
        self.workgroup_detail_calls.append(name)
        if self.workgroup_detail_error is not None:
            raise self.workgroup_detail_error
        if name == self.block_workgroup_detail_for:
            self.workgroup_detail_started.set()
            await self.release_workgroup_detail.wait()
        return self.workgroup_details[name]

    async def list_catalogs_page(
        self,
        *,
        workgroup: str | None = None,
        start_token: str | None = None,
    ) -> tuple[list[AthenaCatalogSummary], str | None]:
        assert workgroup is not None
        self.catalog_calls.append((workgroup, start_token))
        if self.catalog_error is not None:
            raise self.catalog_error
        if workgroup == self.block_catalog_for:
            self.catalog_started.set()
            await self.release_catalog.wait()
        return list(self.catalogs[workgroup]), None

    async def list_databases_page(
        self,
        catalog: str,
        *,
        workgroup: str | None = None,
        start_token: str | None = None,
    ) -> tuple[list[DatabaseSummary], str | None]:
        assert workgroup is not None
        self.database_calls.append((workgroup, catalog, start_token))
        if self.database_error is not None:
            raise self.database_error
        rows = [
            DatabaseSummary(
                DatabaseRef(
                    catalog,
                    name,
                    self.connection_name,
                    self.region,
                ),
                None,
                None,
                None,
            )
            for name in self.databases[(workgroup, catalog)]
        ]
        return rows, None

    async def list_query_executions_page(
        self,
        workgroup: str,
        *,
        start_token: str | None = None,
    ) -> tuple[list[QueryExecutionRef], str | None]:
        self.history_calls.append((workgroup, start_token))
        return [
            QueryExecutionRef(
                f"history-{workgroup}",
                self.connection_name,
                self.region,
                workgroup,
            )
        ], None

    async def get_query_execution(self, execution_id: str) -> QueryExecutionDetail:
        workgroup = execution_id.removeprefix("history-")
        if execution_id.startswith("q-app-"):
            workgroup = "primary"
        context = QueryContext(
            self.connection_name,
            self.region,
            workgroup,
            "AwsDataCatalog",
            "default",
        )
        ref = QueryExecutionRef(
            execution_id,
            self.connection_name,
            self.region,
            workgroup,
        )
        return QueryExecutionDetail(
            QueryExecutionSummary(
                ref,
                QueryState.SUCCEEDED,
                datetime(2026, 7, 25, tzinfo=UTC),
                datetime(2026, 7, 25, 0, 0, 1, tzinfo=UTC),
                "DML",
            ),
            None,
            context,
            _STATS,
            "s3://private/result.csv",
            "Athena engine version 3",
            None,
        )

    async def list_named_queries_page(
        self,
        workgroup: str,
        *,
        start_token: str | None = None,
    ) -> tuple[list[str], str | None]:
        self.named_calls.append((workgroup, start_token))
        return ([self.named.query_id] if workgroup == "analysts" else []), None

    async def get_named_queries(self, ids: list[str]) -> tuple[NamedQuery, ...]:
        return (self.named,) if self.named.query_id in ids else ()

    async def list_prepared_statements_page(
        self,
        workgroup: str,
        *,
        start_token: str | None = None,
    ) -> tuple[list[PreparedStatementSummary], str | None]:
        self.prepared_calls.append((workgroup, start_token))
        if workgroup != "analysts":
            return [], None
        return [PreparedStatementSummary(self.prepared.name, self.prepared.last_modified_at)], None

    async def get_prepared_statement(
        self,
        name: str,
        workgroup: str,
    ) -> PreparedStatement:
        assert (name, workgroup) == (self.prepared.name, self.prepared.workgroup)
        self.prepared_detail_started.set()
        if self.block_prepared_detail:
            try:
                await self.release_prepared_detail.wait()
            except asyncio.CancelledError:
                self.prepared_detail_cancelled.set()
                if not self.ignore_prepared_detail_cancellation:
                    raise
                await self.release_prepared_detail.wait()
        return self.prepared

    async def start_query(
        self,
        sql: str,
        context: QueryContext,
        *,
        request_token: str,
    ) -> QueryExecutionRef:
        self.start_calls.append((sql, context, request_token))
        return QueryExecutionRef(
            f"q-app-{len(self.start_calls)}",
            context.connection_name,
            context.region,
            context.workgroup,
        )

    async def stop_query(self, execution_id: str) -> None:
        self.stop_calls.append(execution_id)

    async def get_results_page(
        self,
        execution_id: str,
        *,
        start_token: str | None = None,
    ) -> ResultPage:
        self.result_calls.append((execution_id, start_token))
        if self.block_results:
            self.results_started.set()
            try:
                await self.release_results.wait()
            except asyncio.CancelledError:
                self.results_cancelled.set()
                if not self.ignore_results_cancellation:
                    raise
                await self.release_results.wait()
        return ResultPage((_COLUMN,), (("1",),), None)


class OwnedSelectionStore(ServiceSelectionStore):
    def __init__(self) -> None:
        super().__init__()
        self.dispose_calls = 0

    def dispose(self) -> None:
        self.dispose_calls += 1


def make_page_vm(
    client: PageClient,
    *,
    selection_store: ServiceSelectionStore | None = None,
    connection_name: str | None = None,
    region: str | None = None,
) -> AthenaPageVM:
    connection = Connection(
        name=connection_name or client.connection_name,
        kind="aws",
        region=region or client.region,
        source="config",
        profile=connection_name or client.connection_name,
    )
    hub: MessageHub[Message] = MessageHub()
    page = AthenaPageVM(
        client=client,
        policy=ReadOnlySqlPolicy(),
        connection=connection,
        hub=hub,
        dispatcher=NULL_DISPATCHER,
        selection_store=selection_store,
    )
    page.construct()
    return page


async def _snapshot_failure_artifacts(
    page: AthenaPageVM,
    snapshot: object,
    crash_dir: Path,
) -> tuple[str, str, str]:
    try:
        await page.restore_snapshot(snapshot)  # type: ignore[arg-type]
    except ValueError as error:
        trace = "".join(
            traceback.TracebackException.from_exception(
                error,
                capture_locals=True,
            ).format()
        )
        crash_path = CrashDump(base_dir=crash_dir).write(exc=error)
        return str(error), trace, crash_path.read_text(encoding="utf-8")
    raise AssertionError("mismatched snapshot should fail closed")


@pytest.mark.asyncio
async def test_open_table_preserves_context_and_prefills_without_execution() -> None:
    client = PageClient()
    page = make_page_vm(client)
    await page.setup()
    ref = TableRef(
        "AwsDataCatalog",
        "default",
        'events"archive',
        "analytics",
        "us-west-2",
    )

    await page.open_table(ref, snapshot_id=42)

    assert page.active_view == "query"
    assert page.context == QueryContext(
        "analytics",
        "us-west-2",
        "primary",
        "AwsDataCatalog",
        "default",
    )
    assert page.query.sql == (
        'SELECT * FROM "AwsDataCatalog"."default"."events""archive" FOR VERSION AS OF 42 LIMIT 100'
    )
    assert page.query.execution_ref is None
    assert client.start_calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "ref",
    [
        TableRef("AwsDataCatalog", "default", "events", "other", "us-west-2"),
        TableRef("AwsDataCatalog", "default", "events", "analytics", "us-east-1"),
        TableRef("missing", "default", "events", "analytics", "us-west-2"),
        TableRef("AwsDataCatalog", "missing", "events", "analytics", "us-west-2"),
    ],
)
async def test_open_table_rejects_mismatched_or_unavailable_identity(
    ref: TableRef,
) -> None:
    page = make_page_vm(PageClient())
    await page.setup()
    before = (page.context, page.active_view, page.query.sql)

    with pytest.raises(ValueError, match="table"):
        await page.open_table(ref)

    assert (page.context, page.active_view, page.query.sql) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("token_mode", ["repeated", "unique"])
async def test_open_table_bounds_empty_catalog_discovery_pages(token_mode: str) -> None:
    class EndlessCatalogClient(PageClient):
        def __init__(self) -> None:
            super().__init__()
            self.initial = AthenaCatalogSummary("initial", "LAMBDA", None)
            self.catalogs["primary"] = [self.initial]
            self.databases[("primary", "initial")] = ["default"]
            self.page_number = 0

        async def list_catalogs_page(  # type: ignore[override]
            self,
            *,
            workgroup: str | None = None,
            start_token: str | None = None,
        ) -> tuple[list[AthenaCatalogSummary], str | None]:
            assert workgroup is not None
            self.catalog_calls.append((workgroup, start_token))
            if start_token is None:
                return [self.initial], "TOKEN_SECRET-1"
            self.page_number += 1
            next_token = (
                "TOKEN_SECRET-1"
                if token_mode == "repeated"
                else f"TOKEN_SECRET-{self.page_number + 1}"
            )
            return [], next_token

    client = EndlessCatalogClient()
    page = make_page_vm(client)
    await page.setup()
    missing = TableRef(
        "missing",
        "default",
        "events",
        "analytics",
        "us-west-2",
    )

    with pytest.raises(
        ProviderError,
        match=r"^Athena catalog discovery did not complete$",
    ) as caught:
        await asyncio.wait_for(page.open_table(missing), timeout=1)

    assert len(client.catalog_calls) <= 8
    rendered = "".join(
        traceback.TracebackException.from_exception(
            caught.value,
            capture_locals=True,
        ).format()
    )
    assert "TOKEN_SECRET" not in rendered


@pytest.mark.asyncio
async def test_open_table_allows_finite_empty_catalog_pages_before_match() -> None:
    class SparseCatalogClient(PageClient):
        def __init__(self) -> None:
            super().__init__()
            self.initial = AthenaCatalogSummary("initial", "LAMBDA", None)
            self.target = AthenaCatalogSummary("target", "LAMBDA", None)
            self.catalogs["primary"] = [self.initial]
            self.databases[("primary", "initial")] = ["default"]
            self.databases[("primary", "target")] = ["warehouse"]

        async def list_catalogs_page(  # type: ignore[override]
            self,
            *,
            workgroup: str | None = None,
            start_token: str | None = None,
        ) -> tuple[list[AthenaCatalogSummary], str | None]:
            assert workgroup is not None
            self.catalog_calls.append((workgroup, start_token))
            pages = {
                None: ([self.initial], "page-1"),
                "page-1": ([], "page-2"),
                "page-2": ([], "page-3"),
                "page-3": ([self.target], None),
            }
            return pages[start_token]

    client = SparseCatalogClient()
    page = make_page_vm(client)
    await page.setup()
    target = TableRef(
        "target",
        "warehouse",
        "events",
        "analytics",
        "us-west-2",
    )

    await asyncio.wait_for(page.open_table(target), timeout=1)

    assert page.context.catalog == "target"
    assert page.context.database == "warehouse"
    assert client.catalog_calls == [
        ("primary", None),
        ("primary", "page-1"),
        ("primary", "page-2"),
        ("primary", "page-3"),
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("token_mode", ["repeated", "unique"])
async def test_open_table_bounds_empty_database_discovery_pages(token_mode: str) -> None:
    class EndlessDatabaseClient(PageClient):
        def __init__(self) -> None:
            super().__init__()
            self.page_number = 0

        async def list_databases_page(  # type: ignore[override]
            self,
            catalog: str,
            *,
            workgroup: str | None = None,
            start_token: str | None = None,
        ) -> tuple[list[DatabaseSummary], str | None]:
            assert workgroup is not None
            self.database_calls.append((workgroup, catalog, start_token))
            if start_token is None:
                return [
                    DatabaseSummary(
                        DatabaseRef(
                            catalog,
                            "default",
                            self.connection_name,
                            self.region,
                        ),
                        None,
                        None,
                        None,
                    )
                ], "page-1"
            self.page_number += 1
            next_token = "page-1" if token_mode == "repeated" else f"page-{self.page_number + 1}"
            return [], next_token

    client = EndlessDatabaseClient()
    page = make_page_vm(client)
    await page.setup()
    missing = TableRef(
        "AwsDataCatalog",
        "missing",
        "events",
        "analytics",
        "us-west-2",
    )

    with pytest.raises(ProviderError, match=r"^Athena catalog discovery did not complete$"):
        await asyncio.wait_for(page.open_table(missing), timeout=1)

    assert len(client.database_calls) <= 8


@pytest.mark.asyncio
async def test_open_table_in_glue_requires_one_visible_unambiguous_table() -> None:
    page = make_page_vm(PageClient())
    await page.setup()
    messages: list[OpenGlueTableRequest] = []
    subscription = page._hub.messages.subscribe(  # type: ignore[attr-defined]
        on_next=lambda message: (
            messages.append(message) if isinstance(message, OpenGlueTableRequest) else None
        )
    )
    try:
        page.query.set_sql("SELECT * FROM events AS first JOIN events AS second USING (id)")
        assert page.open_table_in_glue()
        assert messages == [
            OpenGlueTableRequest(
                TableRef(
                    "AwsDataCatalog",
                    "default",
                    "events",
                    "analytics",
                    "us-west-2",
                )
            )
        ]

        page.query.set_sql("SELECT * FROM events JOIN users USING (id)")
        assert not page.open_table_in_glue()
        page.query.set_sql('SELECT * FROM "foreign"."default"."events"')
        assert not page.open_table_in_glue()
        page.query.set_sql("SELECT * FROM events")
        await page.select_view("history")
        assert not page.open_table_in_glue()
        assert len(messages) == 1
    finally:
        subscription.dispose()


def test_athena_package_exports_task_4_viewmodels() -> None:
    from aws_tui.vm.athena import AthenaHistoryVM, AthenaPageVM, AthenaSavedVM

    assert AthenaHistoryVM.__name__ == "AthenaHistoryVM"
    assert AthenaSavedVM.__name__ == "AthenaSavedVM"
    assert AthenaPageVM.__name__ == "AthenaPageVM"


@pytest.mark.asyncio
async def test_setup_loads_context_lists_in_order_and_keeps_other_views_lazy() -> None:
    client = PageClient()
    page = make_page_vm(client)

    await page.setup()

    assert isinstance(page._workgroup_pager, TokenPagedComposition)  # type: ignore[attr-defined]
    assert isinstance(page._catalog_pager, TokenPagedComposition)  # type: ignore[attr-defined]
    assert isinstance(page._database_pager, TokenPagedComposition)  # type: ignore[attr-defined]
    assert page.context == QueryContext(
        "analytics",
        "us-west-2",
        "primary",
        "AwsDataCatalog",
        "default",
    )
    assert client.workgroup_calls == [None]
    assert client.workgroup_detail_calls == ["primary"]
    assert page.workgroup_detail == client.workgroup_details["primary"]
    assert page.workgroup_detail_state is PaneState.IDLE
    assert client.catalog_calls == [("primary", None)]
    assert client.database_calls == [("primary", "AwsDataCatalog", None)]
    assert client.history_calls == []
    assert client.named_calls == []
    assert client.prepared_calls == []


@pytest.mark.asyncio
async def test_workgroup_detail_error_is_stable_and_blocks_executable_context() -> None:
    client = PageClient()
    client.workgroup_detail_error = ProviderError(
        "failed for s3://private-bucket/sensitive-prefix/"
    )
    page = make_page_vm(client)

    await page.setup()

    assert page.workgroup_detail is None
    assert page.workgroup_detail_state is PaneState.ERROR
    assert page.workgroup_detail_error_text == "Athena workgroup request failed"
    assert "private-bucket" not in repr(page)
    assert page.context.catalog == ""
    assert page.context.database == ""
    assert client.catalog_calls == []


@pytest.mark.asyncio
async def test_query_refresh_recovers_failed_current_workgroup_detail_context() -> None:
    client = PageClient()
    client.workgroup_detail_error = ProviderError("temporarily unavailable")
    page = make_page_vm(client)

    await page.setup()

    assert page.context.workgroup == "primary"
    assert page.context.catalog == ""
    assert page.context.database == ""
    assert page.workgroup_detail_state is PaneState.ERROR

    client.workgroup_detail_error = None
    await page.refresh_query_context()

    assert client.workgroup_detail_calls == ["primary", "primary"]
    assert page.context == QueryContext(
        "analytics",
        "us-west-2",
        "primary",
        "AwsDataCatalog",
        "default",
    )
    assert page.workgroup_detail_state is PaneState.IDLE
    assert page.workgroup_detail_error_text is None
    assert page.catalogs_state is PaneState.IDLE
    assert page.catalogs_error_text is None
    assert page.databases_state is PaneState.IDLE
    assert page.databases_error_text is None
    page.query.set_sql("SELECT 1")
    await page.query.execute()
    assert client.start_calls[-1][1] == page.context


@pytest.mark.asyncio
async def test_late_workgroup_detail_cannot_replace_the_current_selection() -> None:
    client = PageClient()
    page = make_page_vm(client)
    await page.setup()
    client.block_workgroup_detail_for = "analysts"

    stale = asyncio.create_task(page.select_workgroup("analysts"))
    await client.workgroup_detail_started.wait()
    client.block_workgroup_detail_for = None
    await page.select_workgroup("primary")
    client.release_workgroup_detail.set()
    await stale

    assert page.context.workgroup == "primary"
    assert page.workgroup_detail == client.workgroup_details["primary"]
    assert page.workgroup_detail_state is PaneState.IDLE


@pytest.mark.asyncio
async def test_shutdown_drains_workgroup_detail_without_late_publication() -> None:
    client = PageClient()
    page = make_page_vm(client)
    await page.setup()
    client.block_workgroup_detail_for = "analysts"

    selection = asyncio.create_task(page.select_workgroup("analysts"))
    await client.workgroup_detail_started.wait()
    shutdown = asyncio.create_task(page.shutdown())
    await asyncio.sleep(0)

    assert not shutdown.done()
    client.release_workgroup_detail.set()
    await asyncio.gather(selection, shutdown)

    assert page.context.workgroup == ""
    assert page.workgroup_detail is None
    assert page.workgroup_detail_state is PaneState.EMPTY


@pytest.mark.asyncio
async def test_history_is_scoped_to_selected_workgroup_and_loaded_once() -> None:
    client = PageClient()
    page = make_page_vm(client)
    await page.setup()
    await page.select_workgroup("analysts")

    await page.select_view("history")
    await page.select_view("query")
    await page.select_view("history")

    assert {row.ref.workgroup for row in page.history.items} == {"analysts"}
    assert client.history_calls == [("analysts", None)]
    assert client.stop_calls == []


@pytest.mark.asyncio
async def test_catalog_change_invalidates_results_before_new_context_load_finishes() -> None:
    client = PageClient()
    page = make_page_vm(client)
    await page.setup()
    page.query.set_sql("SELECT 1")
    await page.query.execute()
    assert page.results.rows == (("1",),)

    change = asyncio.create_task(page.select_catalog("federated"))
    await asyncio.sleep(0)

    assert page.results.rows == ()
    await change
    assert page.context.catalog == "federated"
    assert page.context.database == "remote"


@pytest.mark.asyncio
async def test_page_snapshot_round_trip_restores_query_results_and_selections_without_execution(
    tmp_path: Path,
) -> None:
    client = PageClient()
    store = ServiceSelectionStore()
    source = make_page_vm(client, selection_store=store)
    await source.setup()
    await source.select_view("history")
    await source.select_history_execution("history-primary")
    await source.select_view("query")
    source.query.set_sql("SELECT 'PAGE_SNAPSHOT_SQL_SECRET'")
    await source.query.execute()
    await source.select_view("results")
    snapshot = source.export_snapshot()
    assert source.query.execution_ref is not None
    execution_id = source.query.execution_ref.execution_id
    start_count = len(client.start_calls)
    result_call_count = len(client.result_calls)

    destination = make_page_vm(client, selection_store=store)
    await destination.setup()
    await destination.restore_snapshot(snapshot)

    assert destination.context == source.context
    assert destination.active_view == "results"
    assert destination.query.sql == "SELECT 'PAGE_SNAPSHOT_SQL_SECRET'"
    assert destination.query.execution_ref is not None
    assert destination.query.execution_ref.execution_id == execution_id
    assert destination.query.state is QueryState.SUCCEEDED
    assert destination.query.statistics == _STATS
    assert destination.results.execution_id == execution_id
    assert destination.results.columns == (_COLUMN,)
    assert destination.results.rows == (("1",),)
    assert destination.history.selected_execution_id == "history-primary"
    assert len(client.start_calls) == start_count
    assert len(client.result_calls) == result_call_count

    rendered = repr(snapshot)
    assert "PAGE_SNAPSHOT_SQL_SECRET" not in rendered
    assert execution_id not in rendered

    hostile = replace(
        snapshot,
        context=replace(snapshot.context, connection_name="other-profile"),
    )
    error_text, trace, crash = await _snapshot_failure_artifacts(
        destination,
        hostile,
        tmp_path / "crash",
    )
    assert error_text == "Athena snapshot does not match the active source"
    assert "PAGE_SNAPSHOT_SQL_SECRET" not in trace
    assert "PAGE_SNAPSHOT_SQL_SECRET" not in crash
    assert execution_id not in trace
    assert execution_id not in crash


@pytest.mark.asyncio
async def test_page_snapshot_restores_context_from_later_discovery_pages() -> None:
    class PagedContextClient(PageClient):
        def __init__(self) -> None:
            super().__init__()
            self.catalogs["analysts"] = [
                AthenaCatalogSummary("federated", "LAMBDA", None),
                AthenaCatalogSummary("AwsDataCatalog", "LAMBDA", None),
            ]
            self.databases[("analysts", "federated")] = ["remote"]
            self.databases[("analysts", "AwsDataCatalog")] = ["staging", "events"]

        async def list_workgroups_page(
            self,
            *,
            start_token: str | None = None,
        ) -> tuple[list[AthenaWorkgroupSummary], str | None]:
            self.workgroup_calls.append(start_token)
            if start_token is None:
                return [self.workgroups[0]], "workgroups-next"
            return [self.workgroups[1]], None

        async def list_catalogs_page(
            self,
            *,
            workgroup: str | None = None,
            start_token: str | None = None,
        ) -> tuple[list[AthenaCatalogSummary], str | None]:
            assert workgroup is not None
            self.catalog_calls.append((workgroup, start_token))
            rows = self.catalogs[workgroup]
            if workgroup != "analysts":
                return list(rows), None
            if start_token is None:
                return [rows[0]], "catalogs-next"
            return [rows[1]], None

        async def list_databases_page(
            self,
            catalog: str,
            *,
            workgroup: str | None = None,
            start_token: str | None = None,
        ) -> tuple[list[DatabaseSummary], str | None]:
            assert workgroup is not None
            self.database_calls.append((workgroup, catalog, start_token))
            names = self.databases[(workgroup, catalog)]
            if (workgroup, catalog) != ("analysts", "AwsDataCatalog"):
                selected_names = names
                token = None
            elif start_token is None:
                selected_names = names[:1]
                token = "databases-next"
            else:
                selected_names = names[1:]
                token = None
            return [
                DatabaseSummary(
                    DatabaseRef(
                        catalog,
                        name,
                        self.connection_name,
                        self.region,
                    ),
                    None,
                    None,
                    None,
                )
                for name in selected_names
            ], token

    client = PagedContextClient()
    store = ServiceSelectionStore()
    source = make_page_vm(client, selection_store=store)
    await source.setup()
    await source.load_more_workgroups()
    await source.select_workgroup("analysts")
    await source.load_more_catalogs()
    await source.select_catalog("AwsDataCatalog")
    await source.load_more_databases()
    await source.select_database("events")
    snapshot = source.export_snapshot()
    assert source.context == QueryContext(
        "analytics",
        "us-west-2",
        "analysts",
        "AwsDataCatalog",
        "events",
    )

    destination = make_page_vm(client, selection_store=store)
    await destination.setup()
    assert destination.context.workgroup == "primary"

    await destination.restore_snapshot(snapshot)

    assert destination.context == source.context
    assert destination.query.context == source.context


@pytest.mark.asyncio
async def test_open_saved_query_copies_sql_without_execution() -> None:
    client = PageClient()
    store = ServiceSelectionStore()
    scope = SelectionScope("athena", "analytics", "us-west-2")
    store.set(scope, "workgroup", "analysts")
    store.set(scope, "active_view", "saved")
    page = make_page_vm(client, selection_store=store)
    await page.setup()
    await page.saved.select_named_query("named-1")

    await page.open_saved_in_editor()

    assert page.active_view == "query"
    assert page.query.sql == "SELECT count(*) FROM events"
    assert client.start_calls == []
    assert "SELECT count(*) FROM events" not in repr(page)


@pytest.mark.asyncio
async def test_selection_restore_is_validated_and_scoped_by_connection_region() -> None:
    store = ServiceSelectionStore()
    scope = SelectionScope("athena", "analytics", "us-west-2")
    store.set(scope, "workgroup", "analysts")
    store.set(scope, "catalog", "deleted")
    store.set(scope, "database", "deleted")
    first = make_page_vm(PageClient(), selection_store=store)

    await first.setup()

    assert first.context.workgroup == "analysts"
    assert first.context.catalog == "AwsDataCatalog"
    assert first.context.database == "events"
    assert store.get(scope, "catalog") == "AwsDataCatalog"
    assert store.get(scope, "database") == "events"

    other = PageClient(connection_name="analytics", region="us-east-1")
    second = make_page_vm(other, selection_store=store)
    await second.setup()

    assert second.context.workgroup == "primary"
    assert store.get(SelectionScope("athena", "analytics", "us-east-1"), "workgroup") == ("primary")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("level", "state_attribute"),
    [
        ("workgroup", "workgroups_state"),
        ("catalog", "catalogs_state"),
        ("database", "databases_state"),
    ],
)
@pytest.mark.parametrize(
    ("error", "expected_state"),
    [
        (PermissionDeniedError("denied"), PaneState.FORBIDDEN),
        (ThrottledError("throttled"), PaneState.ERROR),
        (ProviderUnreachableError("transport"), PaneState.UNREACHABLE),
        (ProviderError("request failed"), PaneState.ERROR),
    ],
)
async def test_transient_context_errors_preserve_all_persisted_selections(
    level: str,
    state_attribute: str,
    error: ProviderError,
    expected_state: PaneState,
) -> None:
    client = PageClient()
    setattr(client, f"{level}_error", error)
    store = ServiceSelectionStore()
    scope = SelectionScope("athena", "analytics", "us-west-2")
    expected = {
        "workgroup": "analysts",
        "catalog": "AwsDataCatalog",
        "database": "events",
    }
    for key, value in expected.items():
        store.set(scope, key, value)
    page = make_page_vm(client, selection_store=store)

    await page.setup()

    assert getattr(page, state_attribute) is expected_state
    assert {key: store.get(scope, key) for key in expected} == expected


@pytest.mark.asyncio
@pytest.mark.parametrize("empty_level", ["workgroup", "catalog", "database"])
async def test_successful_empty_context_page_clears_only_confirmed_absence(
    empty_level: str,
) -> None:
    client = PageClient()
    if empty_level == "workgroup":
        client.workgroups = []
    elif empty_level == "catalog":
        client.catalogs["analysts"] = []
    else:
        client.databases[("analysts", "AwsDataCatalog")] = []
    store = ServiceSelectionStore()
    scope = SelectionScope("athena", "analytics", "us-west-2")
    for key, value in (
        ("workgroup", "analysts"),
        ("catalog", "AwsDataCatalog"),
        ("database", "events"),
    ):
        store.set(scope, key, value)
    page = make_page_vm(client, selection_store=store)

    await page.setup()

    if empty_level == "workgroup":
        assert store.get(scope, "workgroup") is None
        assert store.get(scope, "catalog") is None
        assert store.get(scope, "database") is None
    elif empty_level == "catalog":
        assert store.get(scope, "workgroup") == "analysts"
        assert store.get(scope, "catalog") is None
        assert store.get(scope, "database") is None
    else:
        assert store.get(scope, "workgroup") == "analysts"
        assert store.get(scope, "catalog") == "AwsDataCatalog"
        assert store.get(scope, "database") is None


@pytest.mark.asyncio
async def test_workgroup_change_cannot_publish_a_late_catalog_page() -> None:
    client = PageClient()
    page = make_page_vm(client)
    await page.setup()
    client.block_catalog_for = "analysts"

    old_change = asyncio.create_task(page.select_workgroup("analysts"))
    await client.catalog_started.wait()
    client.block_catalog_for = None
    await page.select_workgroup("primary")
    client.release_catalog.set()
    await old_change

    assert page.context.workgroup == "primary"
    assert tuple(catalog.name for catalog in page.catalogs) == (
        "AwsDataCatalog",
        "federated",
    )
    assert page.context.database == "default"


@pytest.mark.asyncio
async def test_context_load_more_exposes_busy_state_without_reloading_page_one() -> None:
    client = PageClient()
    page = make_page_vm(client)
    await page.setup()
    page._catalog_pager._current_token = "catalog-next"  # type: ignore[attr-defined]
    client.block_catalog_for = "primary"

    loading = asyncio.create_task(page.load_more_catalogs())
    await client.catalog_started.wait()

    assert page.is_loading_more_catalogs
    assert client.catalog_calls[-1] == ("primary", "catalog-next")
    assert client.catalog_calls.count(("primary", None)) == 1

    client.release_catalog.set()
    await loading

    assert not page.is_loading_more_catalogs


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("error_attribute", "loader_name", "pager_attribute", "state_attribute", "text_attribute"),
    [
        (
            "workgroup_error",
            "load_more_workgroups",
            "_workgroup_pager",
            "workgroups_state",
            "workgroups_error_text",
        ),
        (
            "catalog_error",
            "load_more_catalogs",
            "_catalog_pager",
            "catalogs_state",
            "catalogs_error_text",
        ),
        (
            "database_error",
            "load_more_databases",
            "_database_pager",
            "databases_state",
            "databases_error_text",
        ),
    ],
)
async def test_context_load_more_retry_clears_stale_error(
    error_attribute: str,
    loader_name: str,
    pager_attribute: str,
    state_attribute: str,
    text_attribute: str,
) -> None:
    client = PageClient()
    page = make_page_vm(client)
    await page.setup()
    getattr(page, pager_attribute)._current_token = "retry-token"  # type: ignore[attr-defined]
    setattr(client, error_attribute, ProviderError("temporary failure"))

    await getattr(page, loader_name)()

    assert getattr(page, state_attribute) is PaneState.ERROR
    assert getattr(page, text_attribute) == "Athena context request failed"

    setattr(client, error_attribute, None)
    await getattr(page, loader_name)()

    assert getattr(page, state_attribute) is PaneState.IDLE
    assert getattr(page, text_attribute) is None


@pytest.mark.asyncio
async def test_context_change_during_history_result_load_cannot_switch_to_results() -> None:
    client = PageClient()
    client.block_results = True
    client.ignore_results_cancellation = True
    store = ServiceSelectionStore()
    scope = SelectionScope("athena", "analytics", "us-west-2")
    page = make_page_vm(client, selection_store=store)
    await page.setup()
    await page.select_view("history")
    assert page.history.selected_execution_id == "history-primary"

    opening = asyncio.create_task(page.open_history_results())
    await client.results_started.wait()
    await page.select_workgroup("analysts")
    try:
        await asyncio.wait_for(client.results_cancelled.wait(), timeout=1)
    finally:
        client.release_results.set()
        await opening

    assert page.context.workgroup == "analysts"
    assert page.active_view == "history"
    assert store.get(scope, "active_view") == "history"
    assert page.results.rows == ()


@pytest.mark.asyncio
async def test_cancelled_history_result_load_cannot_switch_to_results() -> None:
    client = PageClient()
    client.block_results = True
    client.ignore_results_cancellation = True
    store = ServiceSelectionStore()
    scope = SelectionScope("athena", "analytics", "us-west-2")
    page = make_page_vm(client, selection_store=store)
    await page.setup()
    await page.select_view("history")

    opening = asyncio.create_task(page.open_history_results())
    await client.results_started.wait()
    opening.cancel()
    try:
        await asyncio.wait_for(client.results_cancelled.wait(), timeout=1)
    finally:
        client.release_results.set()
        await asyncio.gather(opening, return_exceptions=True)

    assert page.active_view == "history"
    assert store.get(scope, "active_view") == "history"


@pytest.mark.asyncio
@pytest.mark.parametrize("terminal_state", ["dispose", "shutdown"])
async def test_late_prepared_selection_after_page_termination_preserves_page_and_saved_state(
    terminal_state: str,
) -> None:
    client = PageClient()
    client.block_prepared_detail = True
    client.ignore_prepared_detail_cancellation = True
    store = ServiceSelectionStore()
    scope = SelectionScope("athena", "analytics", "us-west-2")
    page = make_page_vm(client, selection_store=store)
    await page.setup()
    await page.select_workgroup("analysts")
    await page.select_view("saved")
    store.set(scope, "saved_query_id", "before-termination")

    selection = asyncio.create_task(page.select_prepared_statement("prepared-1"))
    await client.prepared_detail_started.wait()
    if terminal_state == "dispose":
        page.dispose()
        terminal = None
    else:
        terminal = asyncio.create_task(page.shutdown())
    try:
        await asyncio.wait_for(client.prepared_detail_cancelled.wait(), timeout=1)
        snapshot = (
            page.context,
            page.active_view,
            page.query.context,
            page.saved.selected_query_id,
            page.saved.selected_prepared_statement,
            page.saved.detail_state,
            page.saved.detail_error_text,
            store.get(scope, "saved_query_id"),
        )
    finally:
        client.release_prepared_detail.set()
        await asyncio.gather(
            selection,
            *(task for task in (terminal,) if task is not None),
        )

    assert client.prepared_detail_cancelled.is_set()
    assert (
        page.context,
        page.active_view,
        page.query.context,
        page.saved.selected_query_id,
        page.saved.selected_prepared_statement,
        page.saved.detail_state,
        page.saved.detail_error_text,
        store.get(scope, "saved_query_id"),
    ) == snapshot
    assert store.get(scope, "saved_query_id") == "before-termination"


@pytest.mark.asyncio
@pytest.mark.parametrize("terminal_state", ["shutdown", "dispose"])
@pytest.mark.parametrize(
    ("selector", "value"),
    [
        ("select_workgroup", "analysts"),
        ("select_catalog", "federated"),
        ("select_database", "default"),
    ],
)
async def test_context_selectors_are_noops_after_page_termination(
    terminal_state: str,
    selector: str,
    value: str,
) -> None:
    client = PageClient()
    store = ServiceSelectionStore()
    scope = SelectionScope("athena", "analytics", "us-west-2")
    page = make_page_vm(client, selection_store=store)
    await page.setup()
    context = page.context
    child_context = page.query.context
    stored = {
        key: store.get(scope, key) for key in ("active_view", "workgroup", "catalog", "database")
    }
    if terminal_state == "shutdown":
        await page.shutdown()
        context = page.context
        child_context = page.query.context
    else:
        page.dispose()

    await getattr(page, selector)(value)

    assert page.context == context
    assert page.query.context == child_context
    assert {
        key: store.get(scope, key) for key in ("active_view", "workgroup", "catalog", "database")
    } == stored


@pytest.mark.asyncio
@pytest.mark.parametrize("terminal_state", ["shutdown", "dispose"])
async def test_all_other_public_mutators_are_noops_after_page_termination(
    terminal_state: str,
) -> None:
    client = PageClient()
    store = ServiceSelectionStore()
    scope = SelectionScope("athena", "analytics", "us-west-2")
    page = make_page_vm(client, selection_store=store)
    await page.setup()
    await page.select_view("history")
    if terminal_state == "shutdown":
        await page.shutdown()
    else:
        page.dispose()
    context = page.context
    child_context = page.query.context
    stored = {
        key: store.get(scope, key)
        for key in (
            "active_view",
            "workgroup",
            "catalog",
            "database",
            "history_execution_id",
            "saved_query_id",
        )
    }
    call_counts = (
        len(client.workgroup_calls),
        len(client.catalog_calls),
        len(client.database_calls),
        len(client.history_calls),
        len(client.named_calls),
        len(client.prepared_calls),
    )

    page.construct()
    await page.setup()
    await page.select_view("results")
    await page.load_more_workgroups()
    await page.load_more_catalogs()
    await page.load_more_databases()
    await page.select_history_execution("history-primary")
    await page.select_named_query("named-1")
    await page.select_prepared_statement("missing")
    await page.open_saved_in_editor()
    await page.open_history_results()
    await page.refresh_workgroups()
    await page.shutdown()

    assert page.context == context
    assert page.query.context == child_context
    assert {
        key: store.get(scope, key)
        for key in (
            "active_view",
            "workgroup",
            "catalog",
            "database",
            "history_execution_id",
            "saved_query_id",
        )
    } == stored
    assert (
        len(client.workgroup_calls),
        len(client.catalog_calls),
        len(client.database_calls),
        len(client.history_calls),
        len(client.named_calls),
        len(client.prepared_calls),
    ) == call_counts


@pytest.mark.asyncio
async def test_shutdown_and_dispose_cascade_once_without_owning_store(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = OwnedSelectionStore()
    page = make_page_vm(PageClient(), selection_store=store)
    shutdown_calls = {"query": 0, "history": 0, "saved": 0}
    dispose_calls = {"query": 0, "history": 0, "saved": 0}

    for name in shutdown_calls:
        child = getattr(page, name)
        original_shutdown = child.shutdown
        original_dispose = child.dispose

        async def counted_shutdown(
            *,
            child_name: str = name,
            shutdown: object = original_shutdown,
        ) -> None:
            shutdown_calls[child_name] += 1
            await shutdown()  # type: ignore[operator]

        def counted_dispose(
            *,
            child_name: str = name,
            dispose: object = original_dispose,
        ) -> None:
            dispose_calls[child_name] += 1
            dispose()  # type: ignore[operator]

        monkeypatch.setattr(child, "shutdown", counted_shutdown)
        monkeypatch.setattr(child, "dispose", counted_dispose)

    await page.shutdown()
    await page.shutdown()
    page.dispose()
    page.dispose()

    assert set(shutdown_calls.values()) == {1}
    assert set(dispose_calls.values()) == {1}
    assert store.dispose_calls == 0
    assert page._inner.status is ConstructionStatus.DISPOSED  # type: ignore[attr-defined]
