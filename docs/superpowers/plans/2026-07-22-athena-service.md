# Amazon Athena Service Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add Amazon Athena as a first-class, profile-aware service for read-only SQL, query history, paginated results, saved queries, and prepared statements.

**Architecture:** Athena wire mapping and pagination live in `domain/athena.py`; shared query values live in `domain/query.py`; `domain/sql_policy.py` validates one read-only statement with sqlglot before dispatch. Independent Query, History, Results, and Saved VMs compose under `AthenaPageVM`, while `AthenaService` creates a fresh client and VM tree per AWS connection.

**Tech Stack:** Python 3.11-3.13, Textual 8.x (`TextArea`, `DataTable`), VMx 3.1.x, aioboto3/botocore Athena API, `sqlglot>=30.13.0,<31`, pytest, pytest-textual-snapshot.

## Global Constraints

- Requires the completed shared profile-switching plan.
- Athena supports only `Connection.kind == "aws"`.
- Add exactly one runtime dependency: `sqlglot>=30.13.0,<31`.
- Accept one `SELECT`, `SHOW`, `DESCRIBE`, or `EXPLAIN` of an allowed statement.
- Reject multiple statements, parser failures, unknown commands, DDL, DML, CTAS, UNLOAD, and procedure calls before SDK dispatch.
- Athena IAM, Lake Formation, workgroup, and S3 policies remain authoritative.
- Only app-started active queries may be stopped by the app.
- Results are paginated and never fully materialized in memory.
- Full SQL, result values, and raw boto responses never enter logs or crash dumps.
- Iceberg metadata views and Glue cross-links belong to the later integration plan.
- Preserve the enforced View -> ViewModel -> Service -> Domain -> Infrastructure dependency direction.

---

### Task 1: Add Query Models and Fail-Closed SQL Validation

**Files:**
- Modify: `pyproject.toml`
- Modify: `uv.lock`
- Create: `src/aws_tui/domain/query.py`
- Create: `src/aws_tui/domain/sql_policy.py`
- Test: `tests/unit/domain/test_query.py`
- Test: `tests/unit/domain/test_sql_policy.py`

**Interfaces:**
- Produces: `QueryContext`, `QueryExecutionRef`, `QueryState`, `QueryStatistics`
- Produces: `QueryExecutionSummary`, `QueryExecutionDetail`
- Produces: `ResultColumn`, `ResultPage`, `NamedQuery`, `PreparedStatement`
- Produces: `ReadOnlySqlPolicy.validate(sql) -> str`
- Produces: `QueryRejectedError(ValidationError)`

- [ ] **Step 1: Write the SQL allow/deny matrix and immutable-model tests**

```python
@pytest.mark.parametrize(
    "sql",
    [
        "SELECT * FROM analytics.events LIMIT 100",
        "WITH recent AS (SELECT * FROM events) SELECT * FROM recent",
        "SHOW TABLES",
        "DESCRIBE analytics.events",
        "EXPLAIN SELECT * FROM analytics.events",
    ],
)
def test_policy_accepts_one_read_only_statement(sql: str) -> None:
    assert ReadOnlySqlPolicy().validate(sql) == sql.strip()


@pytest.mark.parametrize(
    "sql",
    [
        "",
        "SELECT 1; SELECT 2",
        "CREATE TABLE x AS SELECT 1",
        "INSERT INTO x SELECT 1",
        "UPDATE x SET value = 1",
        "DELETE FROM x",
        "MERGE INTO x USING y ON x.id = y.id WHEN MATCHED THEN DELETE",
        "UNLOAD (SELECT 1) TO 's3://bucket/out/'",
        "CALL system.runtime.kill_query()",
        "EXPLAIN CREATE TABLE x AS SELECT 1",
        "VACUUM x",
    ],
)
def test_policy_rejects_mutating_or_unknown_sql(sql: str) -> None:
    with pytest.raises(QueryRejectedError):
        ReadOnlySqlPolicy().validate(sql)


def test_query_context_includes_connection_region_and_workgroup() -> None:
    context = QueryContext("prod-west", "us-west-2", "analysts", "AwsDataCatalog", "sales")
    assert context.cache_key == (
        "prod-west",
        "us-west-2",
        "analysts",
        "AwsDataCatalog",
        "sales",
    )
```

Include comments, trailing semicolon, quoted identifiers, nested subqueries, Athena comments, invalid syntax, `SHOW CREATE TABLE` rejection unless explicitly read-only, and `EXPLAIN ANALYZE` rejection because it executes the statement.

- [ ] **Step 2: Add the dependency and run tests to verify missing implementation**

Run:

```bash
uv add 'sqlglot>=30.13.0,<31'
uv run pytest tests/unit/domain/test_query.py tests/unit/domain/test_sql_policy.py -q
```

Expected: dependency resolution succeeds; tests fail because query models and policy do not exist.

- [ ] **Step 3: Implement models and structured validation**

Define exact query context and states:

```python
class QueryState(StrEnum):
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


@dataclass(frozen=True, slots=True)
class QueryContext:
    connection_name: str
    region: str
    workgroup: str
    catalog: str
    database: str

    @property
    def cache_key(self) -> tuple[str, str, str, str, str]:
        return (
            self.connection_name,
            self.region,
            self.workgroup,
            self.catalog,
            self.database,
        )


@dataclass(frozen=True, slots=True)
class QueryExecutionRef:
    execution_id: str
    connection_name: str
    region: str
    workgroup: str


@dataclass(frozen=True, slots=True)
class QueryStatistics:
    engine_ms: int | None
    queue_ms: int | None
    planning_ms: int | None
    service_ms: int | None
    bytes_scanned: int | None
    reused_previous_result: bool


@dataclass(frozen=True, slots=True)
class AthenaQueryError:
    category: int | None
    error_type: int | None
    retryable: bool
    message: str


@dataclass(frozen=True, slots=True)
class QueryExecutionSummary:
    ref: QueryExecutionRef
    state: QueryState
    submitted_at: datetime | None
    completed_at: datetime | None
    statement_type: str | None


@dataclass(frozen=True, slots=True)
class QueryExecutionDetail:
    summary: QueryExecutionSummary
    state_reason: str | None
    context: QueryContext
    statistics: QueryStatistics
    output_location: str | None
    engine_version: str | None
    error: AthenaQueryError | None


@dataclass(frozen=True, slots=True)
class ResultColumn:
    name: str
    type_name: str
    nullable: str


@dataclass(frozen=True, slots=True)
class ResultPage:
    columns: tuple[ResultColumn, ...]
    rows: tuple[tuple[str | None, ...], ...]
    next_token: str | None


@dataclass(frozen=True, slots=True)
class NamedQuery:
    query_id: str
    name: str
    description: str | None
    database: str
    query_string: str
    workgroup: str


@dataclass(frozen=True, slots=True)
class PreparedStatement:
    name: str
    query_statement: str
    workgroup: str
    description: str | None
    last_modified_at: datetime | None
```

Import `datetime` for timestamp fields. Keep query text in domain values for explicit UI display, but never include `query_string`, result rows, or AWS error payloads in `repr`-based logs; application logs use execution IDs and redacted stable error text only.

Implement validation with sqlglot's Athena dialect:

```python
class ReadOnlySqlPolicy:
    def validate(self, sql: str) -> str:
        normalized = sql.strip()
        if not normalized:
            raise QueryRejectedError("query is empty")
        try:
            statements = sqlglot.parse(normalized, read="athena")
        except ParseError as exc:
            raise QueryRejectedError("query could not be parsed as Athena SQL") from exc
        if len(statements) != 1:
            raise QueryRejectedError("exactly one read-only statement is required")
        self._validate_expression(statements[0])
        return normalized

    def _validate_expression(self, expression: exp.Expression) -> None:
        if isinstance(expression, exp.Select | exp.Describe):
            return
        if isinstance(expression, exp.Command):
            verb = str(expression.this).upper()
            body = expression.expression
            if verb == "SHOW":
                return
            if verb == "EXPLAIN" and isinstance(body, exp.Literal) and body.is_string:
                explained = sqlglot.parse(body.this, read="athena")
                if len(explained) == 1:
                    self._validate_expression(explained[0])
                    return
        raise QueryRejectedError(f"statement type {expression.key!r} is not read-only")
```

Before allowing a sqlglot `Command`, test its exact verb. Reject `EXPLAIN ANALYZE` explicitly before recursively parsing its body. Export all public models and errors through `__all__`.

- [ ] **Step 4: Run policy tests, lock verification, and type checking**

Run:

```bash
uv lock --check
uv run pytest tests/unit/domain/test_query.py tests/unit/domain/test_sql_policy.py -q
uv run mypy src/aws_tui/domain/query.py src/aws_tui/domain/sql_policy.py
```

Expected: all commands pass.

- [ ] **Step 5: Commit query policy and models**

```bash
git add pyproject.toml uv.lock src/aws_tui/domain/query.py src/aws_tui/domain/sql_policy.py tests/unit/domain/test_query.py tests/unit/domain/test_sql_policy.py
git commit -m "feat: add read-only Athena SQL policy"
```

### Task 2: Implement the Paginated Athena Domain Client

**Files:**
- Create: `src/aws_tui/domain/athena.py`
- Create: `tests/unit/domain/test_athena.py`
- Reuse: `tests/unit/domain/_fake_aws_client.py`

**Interfaces:**
- Produces: `AthenaClient(aws_session, connection)`
- Produces: `list_workgroups_page(start_token=None) -> tuple[list[AthenaWorkgroupSummary], str | None]`
- Produces: `get_workgroup(name) -> AthenaWorkgroupDetail`
- Produces: `list_catalogs_page(start_token=None) -> tuple[list[AthenaCatalogSummary], str | None]`
- Produces: `list_databases_page(catalog, start_token=None) -> tuple[list[DatabaseSummary], str | None]`
- Produces: `list_tables_page(catalog, database, start_token=None) -> tuple[list[TableSummary], str | None]`
- Produces: `list_query_executions_page(workgroup, start_token=None) -> tuple[list[QueryExecutionRef], str | None]`
- Produces: `get_query_execution(execution_id) -> QueryExecutionDetail`
- Produces: `get_query_runtime_statistics(execution_id) -> QueryStatistics`
- Produces: `start_query(sql, context, request_token) -> QueryExecutionRef`
- Produces: `stop_query(execution_id) -> None`
- Produces: `get_results_page(execution_id, start_token=None) -> ResultPage`
- Produces: `list_named_queries_page(workgroup, start_token=None) -> tuple[list[str], str | None]`
- Produces: `get_named_queries(ids) -> tuple[NamedQuery, ...]`
- Produces: `list_prepared_statements_page(workgroup, start_token=None) -> tuple[list[PreparedStatement], str | None]`

- [ ] **Step 1: Write failing request, mapping, and pagination tests**

```python
async def test_start_query_sends_exact_context_and_token() -> None:
    client, boto = _athena_client(
        "start_query_execution",
        {"QueryExecutionId": "q-123"},
    )
    context = QueryContext("dev", "us-east-1", "analysts", "AwsDataCatalog", "analytics")
    ref = await client.start_query("SELECT 1", context, request_token="token-123")
    assert ref.execution_id == "q-123"
    boto.start_query_execution.assert_awaited_once_with(
        QueryString="SELECT 1",
        ClientRequestToken="token-123",
        QueryExecutionContext={"Catalog": "AwsDataCatalog", "Database": "analytics"},
        WorkGroup="analysts",
    )


async def test_results_page_removes_header_only_on_first_page() -> None:
    client, _ = _athena_client("get_query_results", FIRST_RESULT_RESPONSE)
    page = await client.get_results_page("q-123")
    assert [column.name for column in page.columns] == ["event_id", "count"]
    assert page.rows == (("a", "3"),)
    assert page.next_token == "next"


async def test_access_denied_maps_without_exposing_query_text() -> None:
    client, boto = _athena_client("get_query_execution", None)
    boto.get_query_execution.side_effect = _client_error("AccessDeniedException", "denied")
    with pytest.raises(PermissionDeniedError, match="denied"):
        await client.get_query_execution("q-123")
```

Cover workgroup enforced result configuration, missing output configuration, query states, runtime statistics, Athena structured error category/type/retryability, result reuse, null versus empty strings, named queries, prepared statements, malformed responses, credentials, throttling, and transport errors.

- [ ] **Step 2: Run Athena client tests and verify failure**

Run:

```bash
uv run pytest tests/unit/domain/test_athena.py -q
```

Expected: collection fails because `aws_tui.domain.athena` does not exist.

- [ ] **Step 3: Implement one-page calls and domain mapping**

Construct the client with `AwsSession` and `Connection`. Define these Athena-owned records in `domain/athena.py`:

```python
@dataclass(frozen=True, slots=True)
class AthenaWorkgroupSummary:
    name: str
    state: str
    description: str | None
    created_at: datetime | None


@dataclass(frozen=True, slots=True)
class AthenaWorkgroupDetail:
    summary: AthenaWorkgroupSummary
    output_location: str | None
    enforce_workgroup_configuration: bool
    publish_cloudwatch_metrics: bool
    bytes_scanned_cutoff: int | None
    engine_version: str | None


@dataclass(frozen=True, slots=True)
class AthenaCatalogSummary:
    name: str
    catalog_type: str
    description: str | None
```

Every list method performs one AWS request and returns an opaque next token. Batch `BatchGetNamedQuery` IDs to 50 and preserve service order when joining responses. Implement `start_query` as shown in the test and create `QueryExecutionRef` using the client connection and supplied context.

Implement result parsing explicitly:

```python
def _map_result_page(
    response: dict[str, Any],
    *,
    first_page: bool,
) -> ResultPage:
    result_set = response.get("ResultSet", {})
    metadata = result_set.get("ResultSetMetadata", {})
    columns = tuple(
        ResultColumn(
            name=column.get("Name", ""),
            type_name=column.get("Type", "unknown"),
            nullable=column.get("Nullable", "UNKNOWN"),
        )
        for column in metadata.get("ColumnInfo", ())
    )
    rows = tuple(
        tuple(cell.get("VarCharValue") if "VarCharValue" in cell else None for cell in row.get("Data", ()))
        for row in result_set.get("Rows", ())
    )
    if first_page and rows and tuple(column.name for column in columns) == rows[0]:
        rows = rows[1:]
    return ResultPage(columns, rows, response.get("NextToken"))
```

The public `get_results_page` receives `start_token`; `first_page` is `start_token is None`. Add typed `ResultConfigurationRequiredError(ValidationError)` for workgroups with neither enforced nor caller-supplied output configuration.

- [ ] **Step 4: Run Athena domain and SQL-policy tests**

Run:

```bash
uv run pytest tests/unit/domain/test_athena.py tests/unit/domain/test_query.py tests/unit/domain/test_sql_policy.py -q
uv run mypy src/aws_tui/domain/athena.py src/aws_tui/domain/query.py
```

Expected: all tests pass.

- [ ] **Step 5: Commit the Athena client**

```bash
git add src/aws_tui/domain/athena.py tests/unit/domain/test_athena.py tests/unit/domain/_fake_aws_client.py
git commit -m "feat: add paginated Athena domain client"
```

### Task 3: Build Query and Result ViewModels

**Files:**
- Create: `src/aws_tui/vm/athena/__init__.py`
- Create: `src/aws_tui/vm/athena/_errors.py`
- Create: `src/aws_tui/vm/athena/query_vm.py`
- Create: `src/aws_tui/vm/athena/results_vm.py`
- Test: `tests/unit/vm/athena/test_query_vm.py`
- Test: `tests/unit/vm/athena/test_results_vm.py`

**Interfaces:**
- Produces: `AthenaQueryVM(client, policy, context, hub, dispatcher, sleep=anyio.sleep)`
- Produces: `AthenaResultsVM(client, hub, dispatcher)`
- Produces: `execute_command`, `cancel_command`, `load_more_command`

- [ ] **Step 1: Write failing query lifecycle and results tests**

```python
async def test_execute_validates_before_sdk_dispatch() -> None:
    fake = InMemoryAthena()
    vm = make_query_vm(fake)
    vm.set_sql("DELETE FROM prod.events")
    await vm.execute()
    assert vm.validation_error is not None
    assert fake.calls == []


async def test_execute_polls_to_success_and_loads_first_result_page() -> None:
    fake = seeded_athena(states=[QueryState.QUEUED, QueryState.RUNNING, QueryState.SUCCEEDED])
    vm = make_query_vm(fake, sleep=_no_sleep)
    vm.set_sql("SELECT 1")
    await vm.execute()
    assert vm.state is QueryState.SUCCEEDED
    assert vm.results.rows == (("1",),)
    assert vm.statistics.bytes_scanned == 0


async def test_shutdown_stops_only_active_app_started_query() -> None:
    fake = seeded_athena(states=[QueryState.RUNNING])
    vm = make_query_vm(fake, sleep=_blocking_sleep)
    task = asyncio.create_task(vm.execute())
    await fake.poll_started.wait()
    await vm.shutdown()
    vm.dispose()
    await task
    assert fake.stop_calls == ["q-app-1"]
```

Add tests for deterministic request tokens, double-submit suppression, bounded backoff, failed/cancelled states, query replacement, stale generation suppression, result pagination, and null rendering.

- [ ] **Step 2: Run query/results VM tests and verify missing modules**

Run:

```bash
uv run pytest tests/unit/vm/athena/test_query_vm.py tests/unit/vm/athena/test_results_vm.py -q
```

Expected: collection fails because Athena VM modules do not exist.

- [ ] **Step 3: Implement lifecycle-safe query execution**

Use one task per execution. Compute request token from connection context plus a fresh UUID, not SQL text. Poll at `0.25, 0.5, 1, 2, 4, 5` seconds and cap at 5 seconds. Capture a generation before submit and check it after each await.

```python
async def _poll(self, ref: QueryExecutionRef, generation: int) -> None:
    delay = 0.25
    while generation == self._generation:
        detail = await self._client.get_query_execution(ref.execution_id)
        if generation != self._generation:
            return
        self._apply_detail(detail)
        if detail.state in TERMINAL_QUERY_STATES:
            if detail.state is QueryState.SUCCEEDED:
                await self._results.load(ref.execution_id)
            return
        await self._sleep(delay)
        delay = min(delay * 2, 5.0)
```

Track `_owns_active_query`; only explicit cancel and `shutdown()` of an owned non-terminal query call `stop_query`. `AthenaQueryVM.shutdown()` awaits the remote stop request, cancels and awaits its local execution task, and is idempotent. `dispose()` remains synchronous and only releases VMx commands/advisors after shutdown. Draining a local cancellation must not emit an error toast.

- [ ] **Step 4: Run query/results VM tests and type checking**

Run:

```bash
uv run pytest tests/unit/vm/athena/test_query_vm.py tests/unit/vm/athena/test_results_vm.py -q
uv run mypy src/aws_tui/vm/athena/query_vm.py src/aws_tui/vm/athena/results_vm.py
```

Expected: all tests pass.

- [ ] **Step 5: Commit Query and Results VMs**

```bash
git add src/aws_tui/vm/athena tests/unit/vm/athena/test_query_vm.py tests/unit/vm/athena/test_results_vm.py
git commit -m "feat: add Athena query and result viewmodels"
```

### Task 4: Build History, Saved, and Page ViewModels

**Files:**
- Create: `src/aws_tui/vm/athena/history_vm.py`
- Create: `src/aws_tui/vm/athena/saved_vm.py`
- Create: `src/aws_tui/vm/athena/page_vm.py`
- Test: `tests/unit/vm/athena/test_history_vm.py`
- Test: `tests/unit/vm/athena/test_saved_vm.py`
- Test: `tests/unit/vm/athena/test_page_vm.py`

**Interfaces:**
- Produces: `AthenaHistoryVM`, `AthenaSavedVM`, `AthenaPageVM`
- Produces: `AthenaPageVM.source -> ServiceSourceContext`
- Produces: `AthenaPageVM.shutdown() -> None`
- Produces: page views `query`, `history`, `results`, `saved`
- Consumes: Task 2 client and Task 3 query/results VMs
- Consumes: `ServiceSelectionStore` and `SelectionScope` from the foundation plan

- [ ] **Step 1: Write failing paged-list and context-switch tests**

```python
async def test_history_is_scoped_to_selected_workgroup() -> None:
    fake = seeded_athena()
    page = make_page_vm(fake)
    await page.setup()
    await page.select_workgroup("analysts")
    await page.select_view("history")
    assert {row.ref.workgroup for row in page.history.items} == {"analysts"}


async def test_changing_catalog_invalidates_old_results() -> None:
    page = make_page_vm(seeded_athena())
    await page.setup()
    await page.query.execute_sql("SELECT 1")
    assert page.results.rows
    await page.select_catalog("federated")
    assert page.results.rows == ()


async def test_open_saved_query_copies_sql_without_execution() -> None:
    fake = seeded_athena()
    page = make_page_vm(fake)
    await page.saved.select_named_query("named-1")
    await page.open_saved_in_editor()
    assert page.query.sql == "SELECT count(*) FROM events"
    assert fake.start_calls == []


async def test_profile_selection_is_restored_only_when_still_valid() -> None:
    store = ServiceSelectionStore()
    first = make_page_vm(seeded_athena(), selections=store)
    await first.setup()
    await first.select_workgroup("analysts")
    first.dispose()
    second = make_page_vm(seeded_athena(), selections=store)
    await second.setup()
    assert second.context.workgroup == "analysts"
```

- [ ] **Step 2: Run page VM tests and verify failure**

Run:

```bash
uv run pytest tests/unit/vm/athena/test_history_vm.py tests/unit/vm/athena/test_saved_vm.py tests/unit/vm/athena/test_page_vm.py -q
```

Expected: missing modules and properties.

- [ ] **Step 3: Implement context-coordinated pagers**

`AthenaPageVM.setup()` loads workgroups, selects a valid workgroup, then loads catalogs and databases. Query history and saved lists load only when first selected. Use `TokenPagedComposition` for every list.

When workgroup/catalog/database changes:

```python
self._context_generation += 1
self._query.replace_context(new_context)
self._history.replace_workgroup(new_context.workgroup)
self._saved.replace_workgroup(new_context.workgroup)
self._results.clear()
self._notify("context")
```

Construct `source = ServiceSourceContext.from_connection(connection)` and `scope = SelectionScope("athena", connection.name, connection.region)`. Persist `active_view`, `workgroup`, `catalog`, `database`, `history_execution_id`, and `saved_query_id` through the injected service-owned `ServiceSelectionStore`. Restore identifiers only after the latest list confirms they still exist. Unknown or deleted selections are discarded and fall back to the first available value. `AthenaPageVM.shutdown()` delegates to `AthenaQueryVM.shutdown()`; synchronous `dispose()` then cascades through every child exactly once without disposing the store.

- [ ] **Step 4: Run all Athena VM tests**

Run:

```bash
uv run pytest tests/unit/vm/athena -q
uv run mypy src/aws_tui/vm/athena
```

Expected: all tests pass.

- [ ] **Step 5: Commit page, history, and saved VMs**

```bash
git add src/aws_tui/vm/athena tests/unit/vm/athena
git commit -m "feat: add Athena history and saved query viewmodels"
```

### Task 5: Build and Register the Athena Textual Service

**Files:**
- Create: `src/aws_tui/ui/widgets/athena/__init__.py`
- Create: `src/aws_tui/ui/widgets/athena/page.py`
- Create: `src/aws_tui/ui/widgets/athena/query_view.py`
- Create: `src/aws_tui/ui/widgets/athena/history_view.py`
- Create: `src/aws_tui/ui/widgets/athena/results_view.py`
- Create: `src/aws_tui/ui/widgets/athena/saved_view.py`
- Create: `src/aws_tui/services/athena/__init__.py`
- Create: `src/aws_tui/services/athena/service.py`
- Modify: `src/aws_tui/ui/widgets/service_view_factory.py`
- Modify: `src/aws_tui/composition.py`
- Modify: `src/aws_tui/app.py`
- Modify: `src/aws_tui/infra/keymap_store.py`
- Modify: `src/aws_tui/vm/chrome/hint_legend_vm.py`
- Test: `tests/unit/ui/athena/test_page.py`
- Test: `tests/unit/services/athena/test_service.py`
- Test: `tests/integration/test_athena_page.py`
- Test: `tests/snapshot/test_athena.py`

**Interfaces:**
- Produces: `AthenaPage`
- Produces: `AthenaService`
- Extends: registry order to S3, EMR Serverless, Glue, Athena
- Extends: `build_service_view("athena", ...)`

- [ ] **Step 1: Write service, composition, and snapshot tests**

```python
def test_athena_service_is_aws_only() -> None:
    service = AthenaService(hub=hub, dispatcher=dispatcher, aws_session=aws_session)
    assert service.supports(aws_connection)
    assert not service.supports(minio_connection)


async def test_query_editor_and_results_are_mounted(app) -> None:
    page = app.query_one(AthenaPage)
    assert page.query_one(TextArea)
    assert page.query_one("#athena-execute")
    await page.select_view("results")
    assert page.query_one(DataTable)
```

Add snapshots for empty query, running, success/results, failure detail, history, saved queries, forbidden, and missing result configuration across every theme with content guards.

- [ ] **Step 2: Run service/UI tests and verify failure**

Run:

```bash
uv run pytest tests/unit/services/athena tests/unit/ui/athena tests/integration/test_athena_page.py tests/snapshot/test_athena.py -q
```

Expected: missing service and UI modules.

- [ ] **Step 3: Implement the page, service, actions, and factory route**

Use one compact source/context header, a four-choice tab strip, a multiline `TextArea`, icon buttons for execute/cancel, a stable status line, and a `DataTable` for results. Never place panes inside decorative cards. Ensure the editor and results each retain usable height at 100x30 and 150x44 terminals.

Register:

```python
"athena.query": ("1",),
"athena.history": ("2",),
"athena.results": ("3",),
"athena.saved": ("4",),
"athena.execute": ("ctrl+enter",),
"athena.cancel": ("escape",),
```

`AthenaService` accepts a client factory and SQL-policy factory for tests, owns one long-lived `ServiceSelectionStore`, and injects it into each disposable `AthenaPageVM`. Extend the view factory with `content-athena-page`, register Athena after Glue, and add app focus/action routing without service-type checks in views. The async content-host shutdown hook from the foundation plan must await `AthenaPageVM.shutdown()` before replacement or application exit, then invoke synchronous disposal.

- [ ] **Step 4: Update snapshots and run service/UI tests**

Run:

```bash
uv run pytest tests/snapshot/test_athena.py --snapshot-update
uv run pytest tests/unit/services/athena tests/unit/ui/athena tests/integration/test_athena_page.py tests/snapshot/test_athena.py -q
```

Expected: all tests pass.

- [ ] **Step 5: Commit Athena service and UI**

```bash
git add src/aws_tui/ui/widgets/athena src/aws_tui/services/athena src/aws_tui/ui/widgets/service_view_factory.py src/aws_tui/composition.py src/aws_tui/app.py src/aws_tui/infra/keymap_store.py src/aws_tui/vm/chrome/hint_legend_vm.py tests/unit/ui/athena tests/unit/services/athena tests/integration/test_athena_page.py tests/snapshot/test_athena.py tests/snapshot/__snapshots__/test_athena
git commit -m "feat: add Athena service page"
```

### Task 6: Add Multi-Profile Athena Demo Data and S3 Result Handoff

**Files:**
- Create: `src/aws_tui/demo/in_memory_athena.py`
- Modify: `src/aws_tui/demo/seeds.py`
- Modify: `src/aws_tui/composition.py`
- Modify: `src/aws_tui/vm/athena/history_vm.py`
- Modify: `src/aws_tui/vm/athena/results_vm.py`
- Modify: `src/aws_tui/app.py`
- Test: `tests/unit/demo/test_in_memory_athena.py`
- Test: `tests/integration/test_athena_s3_handoff.py`
- Test: `tests/integration/test_demo_mode.py`
- Test: `tests/e2e/test_journeys.py`

**Interfaces:**
- Produces: `InMemoryAthena` implementing Task 2's client surface
- Consumes: `OpenS3LocationRequest` from the Glue plan

- [ ] **Step 1: Write fake-client and handoff tests**

```python
async def test_demo_query_walks_to_success_deterministically() -> None:
    fake = seeded_demo_athena("demo-dev")
    ref = await fake.start_query("SELECT 1", DEV_CONTEXT, request_token="token")
    states = [(await fake.get_query_execution(ref.execution_id)).state for _ in range(3)]
    assert states == [QueryState.QUEUED, QueryState.RUNNING, QueryState.SUCCEEDED]


async def test_history_result_location_opens_same_profile_in_s3(...) -> None:
    await open_athena_history("prod-west", "q-123")
    await invoke("athena.open_result_location")
    await wait_for_service_setup(ctx)
    assert ctx.root_vm.content_host.current_id == "s3"
    assert ctx.root_vm.active_connection.name == "prod-west"
    assert ctx.root_vm.content_host.current.left.path.as_posix().startswith("/athena-results/")
```

- [ ] **Step 2: Run demo and handoff tests to verify failure**

Run:

```bash
uv run pytest tests/unit/demo/test_in_memory_athena.py tests/integration/test_athena_s3_handoff.py tests/integration/test_demo_mode.py -q
```

Expected: missing fake and result-location commands.

- [ ] **Step 3: Implement profile-keyed query scenarios and reuse S3 navigation**

Seed two profiles with disjoint workgroups, catalogs, databases, query history, named queries, prepared statements, and results. Include running, succeeded, failed, and cancelled queries plus result reuse and missing-output errors. The fake advances only app-started query state and records every call for assertions.

History and Results VMs send `OpenS3LocationRequest` for result URIs. Reuse the app's existing handler; do not create a second S3 navigation path. Export means opening the existing result object in S3, not rerunning or buffering the query.

- [ ] **Step 4: Run Athena integration and E2E tests**

Run:

```bash
uv run pytest tests/unit/demo/test_in_memory_athena.py tests/integration/test_athena_s3_handoff.py tests/integration/test_demo_mode.py tests/e2e/test_journeys.py -q
```

Expected: all tests pass.

- [ ] **Step 5: Commit demo data and S3 handoff**

```bash
git add src/aws_tui/demo/in_memory_athena.py src/aws_tui/demo/seeds.py src/aws_tui/composition.py src/aws_tui/vm/athena/history_vm.py src/aws_tui/vm/athena/results_vm.py src/aws_tui/app.py tests/unit/demo/test_in_memory_athena.py tests/integration/test_athena_s3_handoff.py tests/integration/test_demo_mode.py tests/e2e/test_journeys.py
git commit -m "feat: add Athena demo and result handoff"
```

### Task 7: Document and Verify Standalone Athena Support

**Files:**
- Modify: `README.md`
- Modify: `docs/architecture.md`
- Modify: `docs/adding-a-service.md`
- Modify: `docs/connections.md`
- Modify: `docs/cookbook.md`
- Modify: `docs/contract-ledger.md`
- Modify: `docs/keybindings.md`
- Modify: `docs/RELEASING.md`
- Modify: `CHANGELOG.md`
- Test: `tests/docs/test_scaffolding.py`

**Interfaces:**
- Documents: Athena permissions, workgroups, query policy, results, costs, profile behavior, and demo flows

- [ ] **Step 1: Add failing documentation assertions**

```python
def test_public_docs_cover_athena_read_only_contract(repo_root: Path) -> None:
    cookbook = (repo_root / "docs/cookbook.md").read_text()
    assert "Athena" in cookbook
    assert "SELECT, SHOW, DESCRIBE, and EXPLAIN" in cookbook
    assert "result artifacts" in cookbook
    assert "bytes scanned" in cookbook
```

- [ ] **Step 2: Run docs tests and verify missing coverage**

Run:

```bash
uv run pytest tests/docs/test_scaffolding.py -q
```

Expected: the new assertion fails until docs are updated.

- [ ] **Step 3: Update all documentation surfaces**

Document minimum IAM/Lake Formation/S3 result permissions, workgroup selection, profile/region switching, read-only parser behavior, supported statements, rejected statements, query costs, result artifacts, saved queries, demo mode, keybindings, troubleshooting, and the exact API operations in the contract ledger. Do not claim Iceberg metadata or Glue-to-Athena navigation before the integration plan ships.

- [ ] **Step 4: Run the full Athena verification matrix**

Run:

```bash
uv run pytest tests/unit tests/integration tests/e2e -q
uv run pytest tests/snapshot -q
uv run ruff check .
uv run ruff format --check .
uv run mypy src
./scripts/check-layers.sh
uv run pytest tests/docs -q
uv lock --check
uv run pip-audit
uv build
```

Expected: all required checks pass; only documented optional dependency skips are allowed.

- [ ] **Step 5: Commit Athena documentation**

```bash
git add README.md docs CHANGELOG.md tests/docs/test_scaffolding.py
git commit -m "docs: document Athena query workflows"
```
