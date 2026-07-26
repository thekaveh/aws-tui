from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import pytest
from vmx import NULL_DISPATCHER, MessageHub, TokenPagedComposition
from vmx.lifecycle.status import ConstructionStatus
from vmx.messages.protocols import Message

from aws_tui.domain.athena import AthenaCatalogSummary, AthenaWorkgroupSummary
from aws_tui.domain.data_catalog import DatabaseRef, DatabaseSummary
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
from aws_tui.vm.athena.page_vm import AthenaPageVM
from aws_tui.vm.file_manager.pane_vm import PaneState
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
        self.catalog_calls: list[tuple[str, str | None]] = []
        self.database_calls: list[tuple[str, str, str | None]] = []
        self.history_calls: list[tuple[str, str | None]] = []
        self.named_calls: list[tuple[str, str | None]] = []
        self.prepared_calls: list[tuple[str, str | None]] = []
        self.start_calls: list[tuple[str, QueryContext, str]] = []
        self.stop_calls: list[str] = []
        self.block_catalog_for: str | None = None
        self.workgroup_error: ProviderError | None = None
        self.catalog_error: ProviderError | None = None
        self.database_error: ProviderError | None = None
        self.catalog_started = asyncio.Event()
        self.release_catalog = asyncio.Event()
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

    async def list_workgroups_page(
        self,
        *,
        start_token: str | None = None,
    ) -> tuple[list[AthenaWorkgroupSummary], str | None]:
        self.workgroup_calls.append(start_token)
        if self.workgroup_error is not None:
            raise self.workgroup_error
        return list(self.workgroups), None

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
    assert client.catalog_calls == [("primary", None)]
    assert client.database_calls == [("primary", "AwsDataCatalog", None)]
    assert client.history_calls == []
    assert client.named_calls == []
    assert client.prepared_calls == []


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
