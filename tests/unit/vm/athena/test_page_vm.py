from __future__ import annotations

import asyncio
import traceback
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pytest
from vmx import NULL_DISPATCHER, MessageHub
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
from aws_tui.vm.athena._pager_compat import SnapshotTokenPager, seed_token_pager
from aws_tui.vm.athena.page_vm import AthenaPageVM
from aws_tui.vm.athena.saved_vm import SavedQueryKind
from aws_tui.vm.file_manager.pane_vm import PaneState
from aws_tui.vm.messages import OpenGlueTableRequest, ServiceOperationFailedMessage
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
        self.history_error: ProviderError | None = None
        self.block_history_for: str | None = None
        self.history_started = asyncio.Event()
        self.release_history = asyncio.Event()
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
        self.database_row_override: DatabaseSummary | None = None
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
        self.prepared_detail_error: ProviderError | None = None
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
                10_000_000,
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
        if self.database_row_override is not None:
            rows = [self.database_row_override]
        return rows, None

    async def list_query_executions_page(
        self,
        workgroup: str,
        *,
        start_token: str | None = None,
    ) -> tuple[list[QueryExecutionRef], str | None]:
        self.history_calls.append((workgroup, start_token))
        if self.history_error is not None:
            raise self.history_error
        if workgroup == self.block_history_for:
            self.history_started.set()
            await self.release_history.wait()
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

    async def get_query_executions(
        self,
        execution_ids: list[str],
    ) -> tuple[QueryExecutionDetail, ...]:
        details: list[QueryExecutionDetail] = []
        for execution_id in execution_ids:
            details.append(await self.get_query_execution(execution_id))
        return tuple(details)

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
        if self.prepared_detail_error is not None:
            raise self.prepared_detail_error
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
    hub: MessageHub[Message] | None = None,
) -> AthenaPageVM:
    connection = Connection(
        name=connection_name or client.connection_name,
        kind="aws",
        region=region or client.region,
        source="config",
        profile=connection_name or client.connection_name,
    )
    resolved_hub: MessageHub[Message] = hub if hub is not None else MessageHub()
    page = AthenaPageVM(
        client=client,
        policy=ReadOnlySqlPolicy(),
        connection=connection,
        hub=resolved_hub,
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
        snapshot = None
        trace = "".join(
            traceback.TracebackException.from_exception(
                error,
                capture_locals=True,
            ).format()
        )
        crash_path = CrashDump(base_dir=crash_dir).write(exc=error)
        return str(error), trace, crash_path.read_text(encoding="utf-8")
    raise AssertionError("mismatched snapshot should fail closed")


def _page_restore_state(
    page: AthenaPageVM,
    store: ServiceSelectionStore,
) -> tuple[object, ...]:
    scope = SelectionScope("athena", "analytics", "us-west-2")
    return (
        page.context,
        page.active_view,
        page.query.context,
        page.query.sql,
        page.query.execution_ref,
        page.query.state,
        page.results.execution_id,
        page.results.columns,
        page.results.rows,
        page.results.state,
        page.results.error_text,
        page.results.has_more,
        page.results.limit_reached,
        page.history.selected_execution_id,
        page.history.items,
        page.history.detail,
        page.history.state,
        page.history.error_text,
        page.history.has_more,
        page.history.limit_reached,
        page.saved.selected_kind,
        page.saved.selected_query_id,
        page.saved.named_queries,
        page.saved.prepared_statements,
        page.saved.selected_named_query,
        page.saved.selected_prepared_statement,
        page.saved.named_state,
        page.saved.prepared_state,
        page.saved.detail_state,
        page.saved.named_error_text,
        page.saved.prepared_error_text,
        page.saved.detail_error_text,
        page.saved.named_limit_reached,
        page.saved.prepared_limit_reached,
        tuple(page.workgroups),
        tuple(page.catalogs),
        tuple(page.databases),
        page.workgroup_detail,
        page.workgroup_detail_state,
        page.workgroups_state,
        page.catalogs_state,
        page.databases_state,
        page.workgroups_error_text,
        page.catalogs_error_text,
        page.databases_error_text,
        page.workgroup_limit_reached,
        page.catalog_limit_reached,
        page.database_limit_reached,
        frozenset(page._loaded_views),  # type: ignore[attr-defined]
        dict(store._values),  # type: ignore[attr-defined]
        tuple(
            (key, store.get(scope, key))
            for key in (
                "workgroup",
                "catalog",
                "database",
                "active_view",
                "history_execution_id",
                "saved_query_id",
            )
        ),
    )


def _provider_call_counts(client: PageClient) -> tuple[int, ...]:
    return (
        len(client.workgroup_calls),
        len(client.workgroup_detail_calls),
        len(client.catalog_calls),
        len(client.database_calls),
        len(client.history_calls),
        len(client.named_calls),
        len(client.prepared_calls),
        len(client.start_calls),
        len(client.result_calls),
    )


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
        'SELECT * FROM "default"."events""archive" FOR VERSION AS OF 42 LIMIT 5'
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

    assert isinstance(page._workgroup_pager, SnapshotTokenPager)  # type: ignore[attr-defined]
    assert isinstance(page._catalog_pager, SnapshotTokenPager)  # type: ignore[attr-defined]
    assert isinstance(page._database_pager, SnapshotTokenPager)  # type: ignore[attr-defined]
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
async def test_query_refresh_selects_a_valid_fallback_when_workgroup_disappears() -> None:
    client = PageClient()
    store = ServiceSelectionStore()
    page = make_page_vm(client, selection_store=store)
    await page.setup()
    client.workgroups = [client.workgroups[1]]

    await page.refresh_query_context()

    assert page.context == QueryContext(
        "analytics",
        "us-west-2",
        "analysts",
        "AwsDataCatalog",
        "events",
    )
    assert page.query.context == page.context
    scope = SelectionScope("athena", "analytics", "us-west-2")
    assert store.get(scope, "workgroup") == "analysts"


@pytest.mark.asyncio
async def test_query_refresh_clears_executable_context_when_no_workgroups_remain() -> None:
    client = PageClient()
    store = ServiceSelectionStore()
    page = make_page_vm(client, selection_store=store)
    await page.setup()
    page.query.set_sql("SELECT 1")
    client.workgroups = []

    await page.refresh_query_context()
    await page.query.execute()

    empty = QueryContext("analytics", "us-west-2", "", "", "")
    assert page.context == empty
    assert page.query.context == empty
    assert page.workgroup_detail is None
    assert page.workgroup_detail_state is PaneState.EMPTY
    assert client.start_calls == []
    scope = SelectionScope("athena", "analytics", "us-west-2")
    assert all(store.get(scope, key) is None for key in ("workgroup", "catalog", "database"))


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
    await shutdown
    with pytest.raises(asyncio.CancelledError):
        await selection

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
async def test_page_snapshot_restores_all_context_collection_limit_states() -> None:
    source = make_page_vm(PageClient())
    await source.setup()
    seed_token_pager(
        source._workgroup_pager,  # type: ignore[attr-defined]
        source.workgroups,
        None,
        limit_reached=True,
    )
    seed_token_pager(
        source._catalog_pager,  # type: ignore[attr-defined]
        source.catalogs,
        None,
        limit_reached=True,
    )
    seed_token_pager(
        source._database_pager,  # type: ignore[attr-defined]
        source.databases,
        None,
        limit_reached=True,
    )

    destination = make_page_vm(PageClient())
    await destination.setup()
    await destination.restore_snapshot(source.export_snapshot())

    assert destination.workgroup_limit_reached
    assert destination.catalog_limit_reached
    assert destination.database_limit_reached


@pytest.mark.asyncio
async def test_page_snapshot_rejects_forged_cross_component_state_without_leaking(
    tmp_path: Path,
) -> None:
    marker = "FORGED_SNAPSHOT_PAYLOAD_SECRET"

    class SensitiveCell:
        __hash__ = None

        def __repr__(self) -> str:
            return marker

    source = make_page_vm(PageClient())
    await source.setup()
    source.query.set_sql("SELECT 'VALID_SNAPSHOT_SQL_SECRET'")
    await source.query.execute()
    snapshot = source.export_snapshot()
    assert snapshot.query.execution_ref is not None

    foreign_ref = replace(
        snapshot.query.execution_ref,
        execution_id=marker,
        connection_name="other-profile",
    )
    foreign_results = replace(
        snapshot.query.results,
        execution_id=marker,
    )
    hostile_snapshots = (
        replace(snapshot, query=replace(snapshot.query, execution_ref=foreign_ref)),
        replace(snapshot, query=replace(snapshot.query, state=QueryState.RUNNING)),
        replace(snapshot, query=replace(snapshot.query, results=foreign_results)),
        replace(
            snapshot,
            query=replace(
                snapshot.query,
                results=replace(
                    snapshot.query.results,
                    rows=((marker, "too-wide"),),
                ),
            ),
        ),
        replace(
            snapshot,
            query=replace(
                snapshot.query,
                results=replace(
                    snapshot.query.results,
                    rows=((SensitiveCell(),),),  # type: ignore[arg-type]
                ),
            ),
        ),
        replace(
            snapshot,
            query=replace(
                snapshot.query,
                results=replace(
                    snapshot.query.results,
                    is_loading_more=True,
                ),
            ),
        ),
        replace(
            snapshot,
            query=replace(
                snapshot.query,
                execution_ref=None,
            ),
        ),
        replace(
            snapshot,
            query=replace(
                snapshot.query,
                results=object(),  # type: ignore[arg-type]
            ),
        ),
        replace(snapshot, loaded_views=(SensitiveCell(),)),  # type: ignore[arg-type]
        replace(snapshot, history=object()),  # type: ignore[arg-type]
        replace(snapshot, saved=object()),  # type: ignore[arg-type]
        replace(snapshot, saved_kind=SavedQueryKind.NAMED, saved_query_id=None),
        replace(snapshot, saved_kind=None, saved_query_id=marker),
    )

    for index, hostile in enumerate(hostile_snapshots):
        destination = make_page_vm(PageClient())
        await destination.setup()
        destination.query.set_sql("UNCHANGED_DESTINATION_SQL")
        before = (
            destination.context,
            destination.active_view,
            destination.query.sql,
            destination.query.execution_ref,
            destination.results.execution_id,
            destination.results.rows,
        )

        error_text, trace, crash = await _snapshot_failure_artifacts(
            destination,
            hostile,
            tmp_path / f"crash-{index}",
        )

        assert error_text == "Athena snapshot is invalid"
        assert (
            destination.context,
            destination.active_view,
            destination.query.sql,
            destination.query.execution_ref,
            destination.results.execution_id,
            destination.results.rows,
        ) == before
        for secret in (marker, "VALID_SNAPSHOT_SQL_SECRET"):
            assert secret not in trace
            assert secret not in crash


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "record_kind",
    [
        "context_workgroup",
        "history",
        "saved_named",
        "saved_prepared",
    ],
)
async def test_page_snapshot_rejects_recursively_malformed_exact_records_atomically(
    record_kind: str,
) -> None:
    client = PageClient()
    source = make_page_vm(client)
    await source.setup()
    await source.select_workgroup("analysts")
    await source.select_view("history")
    await source.select_history_execution("history-analysts")
    await source.select_view("saved")
    await source.select_named_query("named-1")
    snapshot = source.export_snapshot()

    if record_kind == "context_workgroup":
        bad_row = replace(snapshot.context_state.workgroups[-1], state=object())
        hostile = replace(
            snapshot,
            context_state=replace(
                snapshot.context_state,
                workgroups=(*snapshot.context_state.workgroups[:-1], bad_row),
            ),
        )
    elif record_kind == "history":
        bad_summary = replace(snapshot.history.items[0], state="SUCCEEDED")
        hostile = replace(
            snapshot,
            history=replace(
                snapshot.history,
                items=(bad_summary,),
                details=(replace(snapshot.history.details[0], summary=bad_summary),),
            ),
        )
    elif record_kind == "saved_named":
        bad_named = replace(snapshot.saved.named_query_details[0], description=object())
        bad_summary = replace(snapshot.saved.named_queries[0], description=object())
        hostile = replace(
            snapshot,
            saved=replace(
                snapshot.saved,
                named_queries=(bad_summary,),
                named_query_details=(bad_named,),
                selected_named_query=bad_named,
            ),
        )
    else:
        bad_prepared = replace(
            snapshot.saved.prepared_statements[0],
            last_modified_at=object(),
        )
        hostile = replace(
            snapshot,
            saved=replace(
                snapshot.saved,
                prepared_statements=(bad_prepared,),
            ),
        )

    store = ServiceSelectionStore()
    destination = make_page_vm(client, selection_store=store)
    await destination.setup()
    before = _page_restore_state(destination, store)
    calls_before = _provider_call_counts(client)

    with pytest.raises(ValueError, match=r"^Athena snapshot is invalid$"):
        await destination.restore_snapshot(hostile)

    assert _provider_call_counts(client) == calls_before
    assert _page_restore_state(destination, store) == before


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "record_kind",
    [
        "workgroup",
        "workgroup_detail",
        "catalog",
        "database",
        "history",
        "named_query",
        "prepared_statement",
    ],
)
async def test_different_context_staging_rejects_recursively_malformed_provider_records(
    record_kind: str,
) -> None:
    client = PageClient()
    source = make_page_vm(client)
    await source.setup()
    await source.select_workgroup("analysts")
    await source.select_view("history")
    await source.select_history_execution("history-analysts")
    await source.select_view("saved")
    await source.select_named_query("named-1")
    snapshot = source.export_snapshot()

    if record_kind == "workgroup":
        client.workgroups[1] = replace(client.workgroups[1], state=object())
    elif record_kind == "workgroup_detail":
        client.workgroup_details["analysts"] = replace(
            client.workgroup_details["analysts"],
            engine_version=object(),
        )
    elif record_kind == "catalog":
        client.catalogs["analysts"][0] = replace(
            client.catalogs["analysts"][0],
            catalog_type=object(),
        )
    elif record_kind == "database":
        client.database_row_override = DatabaseSummary(
            DatabaseRef(
                "AwsDataCatalog",
                "events",
                "analytics",
                "us-west-2",
            ),
            None,
            None,
            object(),
        )
    elif record_kind == "history":
        original_get = client.get_query_execution

        async def malformed_history(execution_id: str) -> QueryExecutionDetail:
            detail = await original_get(execution_id)
            return replace(
                detail,
                summary=replace(detail.summary, state="SUCCEEDED"),
            )

        client.get_query_execution = malformed_history  # type: ignore[method-assign]
    elif record_kind == "named_query":
        client.named = replace(client.named, description=object())
    else:
        client.prepared = replace(client.prepared, last_modified_at=object())

    store = ServiceSelectionStore()
    destination = make_page_vm(client, selection_store=store)
    await destination.setup()
    before = _page_restore_state(destination, store)

    with pytest.raises(ValueError, match=r"^Athena snapshot context is unavailable$"):
        await destination.restore_snapshot(snapshot)

    assert _page_restore_state(destination, store) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("missing_level", ["workgroup", "catalog", "database"])
async def test_page_snapshot_restore_preflights_context_without_any_mutation(
    missing_level: str,
) -> None:
    client = PageClient()
    source = make_page_vm(client)
    await source.setup()
    await source.select_workgroup("analysts")
    snapshot = source.export_snapshot()

    store = ServiceSelectionStore()
    destination = make_page_vm(client, selection_store=store)
    await destination.setup()
    destination.query.set_sql("UNCHANGED_DESTINATION_SQL")
    if missing_level == "workgroup":
        client.workgroups = [row for row in client.workgroups if row.name != "analysts"]
    elif missing_level == "catalog":
        client.catalogs["analysts"] = []
    else:
        client.databases[("analysts", "AwsDataCatalog")] = []
    before = _page_restore_state(destination, store)

    with pytest.raises(ValueError, match=r"^Athena snapshot context is unavailable$"):
        await destination.restore_snapshot(snapshot)

    assert _page_restore_state(destination, store) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["oversized", "duplicate"])
async def test_cross_context_preflight_rejects_unsafe_discovery_atomically(
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
) -> None:
    from aws_tui.vm.athena import page_vm

    client = PageClient()
    source = make_page_vm(client)
    await source.setup()
    await source.select_workgroup("analysts")
    snapshot = source.export_snapshot()

    store = ServiceSelectionStore()
    destination = make_page_vm(client, selection_store=store)
    await destination.setup()
    before = _page_restore_state(destination, store)
    other = AthenaWorkgroupSummary("other", "ENABLED", None, None)

    async def unsafe_workgroups(
        *,
        start_token: str | None = None,
    ) -> tuple[list[AthenaWorkgroupSummary], str | None]:
        client.workgroup_calls.append(start_token)
        if start_token is None:
            if failure == "oversized":
                return [client.workgroups[0], other], "next"
            return [client.workgroups[0]], "next"
        if failure == "duplicate":
            return [client.workgroups[0], client.workgroups[1]], None
        return [client.workgroups[1]], None

    client.list_workgroups_page = unsafe_workgroups  # type: ignore[method-assign]
    if failure == "oversized":
        monkeypatch.setattr(page_vm, "_MAX_CONTEXT_ITEMS", 2, raising=False)

    with pytest.raises(ValueError, match=r"^Athena snapshot context is unavailable$"):
        await destination.restore_snapshot(snapshot)

    assert _page_restore_state(destination, store) == before


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error_attribute",
    [
        "workgroup_error",
        "workgroup_detail_error",
        "catalog_error",
        "database_error",
    ],
)
async def test_page_snapshot_preflight_provider_failures_are_atomic(
    error_attribute: str,
) -> None:
    client = PageClient()
    source = make_page_vm(client)
    await source.setup()
    await source.select_workgroup("analysts")
    snapshot = source.export_snapshot()

    store = ServiceSelectionStore()
    hub: MessageHub[Message] = MessageHub()
    destination = make_page_vm(client, selection_store=store, hub=hub)
    await destination.setup()
    destination.query.set_sql("UNCHANGED_DESTINATION_SQL")
    setattr(client, error_attribute, ProviderError("SNAPSHOT_PROVIDER_SECRET"))
    before = _page_restore_state(destination, store)
    messages: list[Message] = []
    subscription = hub.messages.subscribe(messages.append)

    try:
        with pytest.raises(ValueError, match=r"^Athena snapshot context is unavailable$"):
            await destination.restore_snapshot(snapshot)
    finally:
        subscription.dispose()

    assert _page_restore_state(destination, store) == before
    assert not any(isinstance(message, ServiceOperationFailedMessage) for message in messages)


@pytest.mark.asyncio
async def test_page_snapshot_preflight_reports_unexpected_context_failure() -> None:
    client = PageClient()
    source = make_page_vm(client)
    await source.setup()
    await source.select_workgroup("analysts")
    snapshot = source.export_snapshot()
    hub: MessageHub[Message] = MessageHub()
    destination = make_page_vm(client, hub=hub)
    await destination.setup()
    client.workgroup_error = RuntimeError(  # type: ignore[assignment]
        "Authorization: Bearer SNAPSHOT_CONTEXT_SECRET"
    )
    messages: list[Message] = []
    subscription = hub.messages.subscribe(messages.append)
    try:
        with pytest.raises(ValueError, match=r"^Athena snapshot context is unavailable$"):
            await destination.restore_snapshot(snapshot)
    finally:
        subscription.dispose()

    diagnostics = [
        message for message in messages if isinstance(message, ServiceOperationFailedMessage)
    ]
    assert len(diagnostics) == 1
    assert diagnostics[0].operation == "restore_snapshot_context"
    assert diagnostics[0].source == snapshot.context.connection_name
    assert diagnostics[0].region == snapshot.context.region
    assert diagnostics[0].safe_error == "Authorization: Bearer [REDACTED]"


@pytest.mark.asyncio
async def test_page_snapshot_preflight_forwards_staged_child_diagnostic() -> None:
    client = PageClient()
    source = make_page_vm(client)
    await source.setup()
    await source.select_workgroup("analysts")
    await source.select_view("history")
    snapshot = source.export_snapshot()
    hub: MessageHub[Message] = MessageHub()
    destination = make_page_vm(client, hub=hub)
    await destination.setup()
    client.history_error = RuntimeError(  # type: ignore[assignment]
        "Authorization: Bearer SNAPSHOT_CHILD_SECRET"
    )
    messages: list[Message] = []
    subscription = hub.messages.subscribe(messages.append)
    try:
        with pytest.raises(ValueError, match=r"^Athena snapshot context is unavailable$"):
            await destination.restore_snapshot(snapshot)
    finally:
        subscription.dispose()

    diagnostics = [
        message for message in messages if isinstance(message, ServiceOperationFailedMessage)
    ]
    assert len(diagnostics) == 1
    assert diagnostics[0].operation == "list_query_executions"
    assert diagnostics[0].source == snapshot.context.connection_name
    assert diagnostics[0].region == snapshot.context.region
    assert diagnostics[0].safe_error == "Authorization: Bearer [REDACTED]"


@pytest.mark.asyncio
async def test_same_context_snapshot_restore_is_fully_local() -> None:
    client = PageClient()
    store = ServiceSelectionStore()
    page = make_page_vm(client, selection_store=store)
    await page.setup()
    await page.select_workgroup("analysts")
    await page.select_view("history")
    await page.select_history_execution("history-analysts")
    await page.select_view("saved")
    await page.select_named_query("named-1")
    snapshot = page.export_snapshot()
    expected = _page_restore_state(page, store)
    page.query.set_sql("SELECT 'temporary'")
    await page.select_view("query")
    calls_before = _provider_call_counts(client)

    await page.restore_snapshot(snapshot)

    assert _provider_call_counts(client) == calls_before
    assert _page_restore_state(page, store) == expected


@pytest.mark.asyncio
async def test_same_context_snapshot_restores_after_failed_context_refresh_without_calls() -> None:
    client = PageClient()
    store = ServiceSelectionStore()
    page = make_page_vm(client, selection_store=store)
    await page.setup()
    await page.select_workgroup("analysts")
    await page.select_view("history")
    await page.select_history_execution("history-analysts")
    snapshot = page.export_snapshot()
    expected = _page_restore_state(page, store)

    client.workgroup_error = ProviderError("provider outage")
    await page.refresh_workgroups()
    assert page.workgroups == ()
    assert page.workgroups_state is PaneState.ERROR
    calls_before = _provider_call_counts(client)

    await page.restore_snapshot(snapshot)

    assert _provider_call_counts(client) == calls_before
    assert _page_restore_state(page, store) == expected


@pytest.mark.asyncio
async def test_snapshot_publication_is_coherent_and_isolates_all_observer_failures(
    caplog: pytest.LogCaptureFixture,
) -> None:
    client = PageClient()
    source_store = ServiceSelectionStore()
    source = make_page_vm(client, selection_store=source_store)
    await source.setup()
    await source.select_workgroup("analysts")
    await source.select_view("history")
    await source.select_history_execution("history-analysts")
    await source.select_view("saved")
    await source.select_named_query("named-1")
    snapshot = source.export_snapshot()
    expected = _page_restore_state(source, source_store)

    destination_store = ServiceSelectionStore()
    destination = make_page_vm(client, selection_store=destination_store)
    await destination.setup()
    destination.query.set_sql("UNCHANGED_DESTINATION_SQL")
    observed: dict[str, list[bool]] = {
        "page": [],
        "query": [],
        "results": [],
        "history": [],
        "saved": [],
    }
    subscriptions = []

    marker = "OBSERVER_PAYLOAD_SECRET"

    def raising_observer(_: str) -> None:
        raise RuntimeError(marker)

    def coherence_observer(owner: str) -> object:
        return lambda _: observed[owner].append(
            _page_restore_state(destination, destination_store) == expected
        )

    for owner, vm in (
        ("page", destination),
        ("query", destination.query),
        ("results", destination.results),
        ("history", destination.history),
        ("saved", destination.saved),
    ):
        subscriptions.append(vm.on_property_changed.subscribe(raising_observer))
        subscriptions.append(vm.on_property_changed.subscribe(coherence_observer(owner)))

    try:
        await destination.restore_snapshot(snapshot)
    finally:
        for subscription in subscriptions:
            subscription.dispose()

    assert _page_restore_state(destination, destination_store) == expected
    assert all(values and all(values) for values in observed.values())
    assert marker not in caplog.text


@pytest.mark.asyncio
async def test_snapshot_shared_hub_notification_is_value_free_and_bounded(
    caplog: pytest.LogCaptureFixture,
) -> None:
    client = PageClient()
    source = make_page_vm(client)
    await source.setup()
    await source.select_workgroup("analysts")
    await source.select_view("history")
    snapshot = source.export_snapshot()

    hub: MessageHub[Message] = MessageHub()
    destination = make_page_vm(client, hub=hub)
    await destination.setup()
    marker = "HOSTILE_SHARED_HUB_OBSERVER_MARKER"
    delivered: list[Message] = []

    def fail(_: Message) -> None:
        raise RuntimeError(marker)

    failing = hub.messages.subscribe(fail)
    remaining = hub.messages.subscribe(delivered.append)
    caplog.clear()
    try:
        await destination.restore_snapshot(snapshot)
    finally:
        failing.dispose()
        remaining.dispose()

    assert destination.export_snapshot() == snapshot
    assert 1 <= len(delivered) <= 64
    assert marker not in caplog.text
    assert all(record.exc_info is None for record in caplog.records)


def test_page_disposal_completes_with_throwing_terminal_observers(
    caplog: pytest.LogCaptureFixture,
) -> None:
    page = make_page_vm(PageClient())
    marker = "HOSTILE_DISPOSAL_OBSERVER_MARKER"
    completed: list[str] = []

    def fail() -> None:
        raise RuntimeError(marker)

    for name, vm in (
        ("page", page),
        ("query", page.query),
        ("results", page.results),
        ("history", page.history),
        ("saved", page.saved),
    ):
        vm.on_property_changed.subscribe(on_completed=fail)
        vm.on_property_changed.subscribe(on_completed=lambda owner=name: completed.append(owner))

    page.dispose()

    assert completed == ["saved", "history", "results", "query", "page"]
    assert all(
        vm.status is ConstructionStatus.DISPOSED
        for vm in (page, page.query, page.results, page.history, page.saved)
    )
    assert marker not in caplog.text


@pytest.mark.asyncio
async def test_history_restore_cancellation_is_atomic() -> None:
    client = PageClient()
    source = make_page_vm(client)
    await source.setup()
    await source.select_workgroup("analysts")
    await source.select_view("history")
    await source.select_history_execution("history-analysts")
    snapshot = source.export_snapshot()

    store = ServiceSelectionStore()
    destination = make_page_vm(client, selection_store=store)
    await destination.setup()
    before = _page_restore_state(destination, store)
    client.block_history_for = "analysts"
    restoring = asyncio.create_task(destination.restore_snapshot(snapshot))
    await client.history_started.wait()

    restoring.cancel()
    with pytest.raises(asyncio.CancelledError):
        await restoring

    assert _page_restore_state(destination, store) == before


@pytest.mark.asyncio
async def test_history_restore_provider_failure_is_atomic() -> None:
    client = PageClient()
    source = make_page_vm(client)
    await source.setup()
    await source.select_workgroup("analysts")
    await source.select_view("history")
    await source.select_history_execution("history-analysts")
    snapshot = source.export_snapshot()

    store = ServiceSelectionStore()
    destination = make_page_vm(client, selection_store=store)
    await destination.setup()
    before = _page_restore_state(destination, store)
    client.history_error = ProviderError("HISTORY_RESTORE_SECRET")

    with pytest.raises(ValueError, match=r"^Athena snapshot context is unavailable$"):
        await destination.restore_snapshot(snapshot)

    assert _page_restore_state(destination, store) == before


@pytest.mark.asyncio
async def test_query_becoming_active_rejects_snapshot_restore_atomically() -> None:
    client = PageClient()
    source = make_page_vm(client)
    await source.setup()
    await source.select_workgroup("analysts")
    await source.select_view("history")
    await source.select_history_execution("history-analysts")
    snapshot = source.export_snapshot()

    store = ServiceSelectionStore()
    destination = make_page_vm(client, selection_store=store)
    await destination.setup()
    client.block_history_for = "analysts"
    restoring = asyncio.create_task(destination.restore_snapshot(snapshot))
    await client.history_started.wait()
    client.block_results = True
    destination.query.set_sql("SELECT 1")
    execution = asyncio.create_task(destination.query.execute())
    await client.results_started.wait()
    before = _page_restore_state(destination, store)
    client.release_history.set()
    try:
        with pytest.raises(ValueError, match=r"^Athena snapshot restore is unavailable$"):
            await restoring
        assert _page_restore_state(destination, store) == before
    finally:
        client.release_results.set()
        await execution


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["cancel", "provider"])
async def test_saved_restore_failure_is_atomic(failure: str) -> None:
    client = PageClient()
    source = make_page_vm(client)
    await source.setup()
    await source.select_workgroup("analysts")
    await source.select_view("saved")
    await source.select_prepared_statement("prepared-1")
    snapshot = source.export_snapshot()

    store = ServiceSelectionStore()
    destination = make_page_vm(client, selection_store=store)
    await destination.setup()
    before = _page_restore_state(destination, store)
    if failure == "cancel":
        client.block_prepared_detail = True
    else:
        client.prepared_detail_error = ProviderError("SAVED_RESTORE_SECRET")
    client.prepared_detail_started.clear()
    restoring = asyncio.create_task(destination.restore_snapshot(snapshot))
    await client.prepared_detail_started.wait()

    if failure == "cancel":
        restoring.cancel()
        with pytest.raises(asyncio.CancelledError):
            await restoring
    else:
        with pytest.raises(ValueError, match=r"^Athena snapshot context is unavailable$"):
            await restoring

    assert _page_restore_state(destination, store) == before


@pytest.mark.asyncio
async def test_page_snapshot_rejection_is_value_free_for_arbitrary_and_exact_payloads(
    tmp_path: Path,
) -> None:
    marker = "PAGE_SNAPSHOT_PAYLOAD_SECRET"

    class HostileSnapshot:
        def __repr__(self) -> str:
            return marker

    page = make_page_vm(PageClient())
    await page.setup()
    exact = replace(page.export_snapshot(), active_view=marker)  # type: ignore[arg-type]
    assert repr(exact) == "AthenaPageSnapshot()"

    for index, payload in enumerate((HostileSnapshot(), exact)):
        destination = make_page_vm(PageClient())
        await destination.setup()
        error_text, trace, crash = await _snapshot_failure_artifacts(
            destination,
            payload,
            tmp_path / f"crash-{index}",
        )

        assert error_text == "Athena snapshot is invalid"
        assert marker not in trace
        assert marker not in crash


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
    await source.select_view("history")
    await source.select_view("saved")
    for pager, items in (
        (source._workgroup_pager, source.workgroups),  # type: ignore[attr-defined]
        (source._catalog_pager, source.catalogs),  # type: ignore[attr-defined]
        (source._database_pager, source.databases),  # type: ignore[attr-defined]
        (source.history._pager, source.history.items),  # type: ignore[attr-defined]
        (source.saved._named_pager, source.saved.named_queries),  # type: ignore[attr-defined]
        (source.saved._prepared_pager, source.saved.prepared_statements),  # type: ignore[attr-defined]
    ):
        seed_token_pager(pager, items, None, limit_reached=True)
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
    assert destination.export_snapshot() == snapshot


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
    ("level", "state_attribute", "has_more_attribute"),
    [
        ("workgroup", "workgroups_state", "has_more_workgroups"),
        ("catalog", "catalogs_state", "has_more_catalogs"),
        ("database", "databases_state", "has_more_databases"),
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
    has_more_attribute: str,
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
    assert not getattr(page, has_more_attribute)
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
    seed_token_pager(
        page._catalog_pager,  # type: ignore[attr-defined]
        page.catalogs,
        "catalog-next",
    )
    client.block_catalog_for = "primary"

    loading = asyncio.create_task(page.load_more_catalogs())
    await client.catalog_started.wait()

    assert page.is_loading_more_catalogs
    assert client.catalog_calls[-1] == ("primary", "catalog-next")
    assert client.catalog_calls.count(("primary", None)) == 1

    client.release_catalog.set()
    await loading

    assert not page.is_loading_more_catalogs


def test_retired_context_worker_cannot_clear_current_busy_state() -> None:
    page = make_page_vm(PageClient())
    old_worker = page._catalog_worker  # type: ignore[attr-defined]
    page._begin_loading_more("catalogs", old_worker)  # type: ignore[attr-defined]

    page._catalog_generation += 1  # type: ignore[attr-defined]
    page._replace_catalog_worker("primary")  # type: ignore[attr-defined]
    current_worker = page._catalog_worker  # type: ignore[attr-defined]
    page._begin_loading_more("catalogs", current_worker)  # type: ignore[attr-defined]
    page._finish_loading_more("catalogs", old_worker)  # type: ignore[attr-defined]

    assert page.is_loading_more_catalogs
    page._finish_loading_more("catalogs", current_worker)  # type: ignore[attr-defined]
    assert not page.is_loading_more_catalogs
    page.dispose()


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
@pytest.mark.parametrize(
    "token_field",
    ["workgroups_token", "catalogs_token", "databases_token"],
)
async def test_page_snapshot_rejects_empty_context_tokens_without_provider_calls(
    token_field: str,
) -> None:
    client = PageClient()
    page = make_page_vm(client)
    await page.setup()
    snapshot = page.export_snapshot()
    hostile = replace(
        snapshot,
        context_state=replace(snapshot.context_state, **{token_field: ""}),
    )
    before = _provider_call_counts(client)

    with pytest.raises(ValueError, match=r"^Athena snapshot is invalid$"):
        await page.restore_snapshot(hostile)

    assert _provider_call_counts(client) == before
    assert page.export_snapshot() == snapshot


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "violation",
    [
        "duplicate_workgroup",
        "mismatched_workgroup_detail",
        "duplicate_catalog",
        "duplicate_database",
        "foreign_database_profile",
        "foreign_database_region",
        "foreign_database_catalog",
    ],
)
async def test_page_snapshot_rejects_incoherent_context_identity_without_calls(
    violation: str,
) -> None:
    client = PageClient()
    page = make_page_vm(client)
    await page.setup()
    snapshot = page.export_snapshot()
    context_state = snapshot.context_state

    if violation == "duplicate_workgroup":
        context_state = replace(
            context_state,
            workgroups=(context_state.workgroups[0], *context_state.workgroups),
        )
    elif violation == "mismatched_workgroup_detail":
        context_state = replace(
            context_state,
            workgroup_detail=replace(
                context_state.workgroup_detail,
                summary=replace(
                    context_state.workgroup_detail.summary,
                    description="different summary",
                ),
            ),
        )
    elif violation == "duplicate_catalog":
        context_state = replace(
            context_state,
            catalogs=(context_state.catalogs[0], *context_state.catalogs),
        )
    elif violation == "duplicate_database":
        context_state = replace(
            context_state,
            databases=(context_state.databases[0], *context_state.databases),
        )
    else:
        field = {
            "foreign_database_profile": "connection_name",
            "foreign_database_region": "region",
            "foreign_database_catalog": "catalog_name",
        }[violation]
        context_state = replace(
            context_state,
            databases=(
                *context_state.databases,
                replace(
                    context_state.databases[0],
                    ref=replace(
                        context_state.databases[0].ref,
                        **{field: "FOREIGN_PROFILE_SECRET"},
                    ),
                ),
            ),
        )

    hostile = replace(snapshot, context_state=context_state)
    before = _provider_call_counts(client)

    with pytest.raises(ValueError, match=r"^Athena snapshot is invalid$"):
        await page.restore_snapshot(hostile)

    assert _provider_call_counts(client) == before
    assert page.export_snapshot() == snapshot
    assert "FOREIGN_PROFILE_SECRET" not in repr(hostile)


@pytest.mark.asyncio
async def test_page_snapshot_foreign_context_rejection_is_value_free(
    tmp_path: Path,
) -> None:
    marker = "FOREIGN_CONTEXT_SNAPSHOT_SECRET"
    page = make_page_vm(PageClient())
    await page.setup()
    snapshot = page.export_snapshot()
    database = snapshot.context_state.databases[0]
    hostile = replace(
        snapshot,
        context_state=replace(
            snapshot.context_state,
            databases=(
                database,
                replace(
                    database,
                    ref=replace(database.ref, connection_name=marker),
                ),
            ),
        ),
    )

    error_text, trace, crash = await _snapshot_failure_artifacts(
        page,
        hostile,
        tmp_path / "crash",
    )

    assert error_text == "Athena snapshot is invalid"
    assert marker not in trace
    assert marker not in crash


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
