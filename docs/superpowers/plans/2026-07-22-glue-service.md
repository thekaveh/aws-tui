# AWS Glue Service Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add AWS Glue as a first-class, profile-aware service for read-only Catalog, Jobs, and Crawlers workflows.

**Architecture:** Shared catalog identifiers live in `domain/data_catalog.py`; Glue wire mapping and pagination live in `domain/glue.py`; independent VM subtrees own Catalog, Jobs, and Crawlers state. `GlueService` composes a fresh client and page VM per connection, and the existing app service-view factory mounts a pure Textual `GluePage`.

**Tech Stack:** Python 3.11-3.13, Textual 8.x, VMx 3.1.x (`TokenPagedComposition`), aioboto3/botocore Glue API, pytest, pytest-textual-snapshot.

## Global Constraints

- Requires the completed shared profile-switching plan.
- Glue supports only `Connection.kind == "aws"`.
- V1 is read-only: no Glue create, update, delete, start, stop, retry, or scheduling API calls.
- Every list is token-paginated; no hidden fetch-all loops in a VM refresh.
- Every cache and restored selection is scoped by connection name and region.
- Service-specific access denial must not mark a profile globally unreachable.
- Raw boto dictionaries do not escape the domain client.
- Do not add PyIceberg, Arrow, DuckDB, DataFusion, or a JVM.
- Iceberg metadata queries and Glue-to-Athena navigation belong to the later integration plan.
- Preserve the enforced View -> ViewModel -> Service -> Domain -> Infrastructure dependency direction.

---

### Task 1: Define Shared Catalog Domain Models

**Files:**
- Create: `src/aws_tui/domain/data_catalog.py`
- Test: `tests/unit/domain/test_data_catalog.py`

**Interfaces:**
- Produces: `CatalogRef`, `DatabaseRef`, `TableRef`
- Produces: `Column`, `StorageDescriptor`, `TableFormat`
- Produces: `DatabaseSummary`, `TableSummary`, `TableDetail`
- Produces: `PartitionSummary`, `ColumnStatistics`

- [ ] **Step 1: Write failing immutable-model tests**

```python
from dataclasses import FrozenInstanceError

import pytest

from aws_tui.domain.data_catalog import (
    CatalogRef,
    Column,
    DatabaseRef,
    StorageDescriptor,
    TableFormat,
    TableRef,
)


def test_table_ref_preserves_security_context() -> None:
    ref = TableRef(
        catalog_name="AwsDataCatalog",
        database_name="analytics",
        table_name="events",
        connection_name="prod-west",
        region="us-west-2",
    )
    assert ref.database == DatabaseRef(
        catalog_name="AwsDataCatalog",
        database_name="analytics",
        connection_name="prod-west",
        region="us-west-2",
    )
    assert ref.catalog == CatalogRef("AwsDataCatalog", "prod-west", "us-west-2")


def test_catalog_models_are_frozen() -> None:
    column = Column("event_id", "string", None, False)
    with pytest.raises(FrozenInstanceError):
        column.type_name = "bigint"  # type: ignore[misc]


def test_storage_descriptor_keeps_s3_location() -> None:
    storage = StorageDescriptor(
        location="s3://lake/events/",
        input_format="org.apache.hadoop.hive.ql.io.parquet.MapredParquetInputFormat",
        output_format="org.apache.hadoop.hive.ql.io.parquet.MapredParquetOutputFormat",
        serde="org.apache.hadoop.hive.ql.io.parquet.serde.ParquetHiveSerDe",
        compressed=False,
        bucket_count=0,
    )
    assert storage.location == "s3://lake/events/"
    assert TableFormat.OTHER.value == "other"
```

- [ ] **Step 2: Run the model tests and verify import failure**

Run:

```bash
uv run pytest tests/unit/domain/test_data_catalog.py -q
```

Expected: collection fails because `data_catalog.py` does not exist.

- [ ] **Step 3: Implement the frozen catalog vocabulary**

Create frozen, slot-backed dataclasses with these exact fields:

```python
class TableFormat(StrEnum):
    ICEBERG = "iceberg"
    HIVE = "hive"
    HUDI = "hudi"
    DELTA = "delta"
    OTHER = "other"


@dataclass(frozen=True, slots=True)
class CatalogRef:
    catalog_name: str
    connection_name: str
    region: str


@dataclass(frozen=True, slots=True)
class DatabaseRef:
    catalog_name: str
    database_name: str
    connection_name: str
    region: str

    @property
    def catalog(self) -> CatalogRef:
        return CatalogRef(self.catalog_name, self.connection_name, self.region)


@dataclass(frozen=True, slots=True)
class TableRef:
    catalog_name: str
    database_name: str
    table_name: str
    connection_name: str
    region: str

    @property
    def database(self) -> DatabaseRef:
        return DatabaseRef(
            self.catalog_name,
            self.database_name,
            self.connection_name,
            self.region,
        )

    @property
    def catalog(self) -> CatalogRef:
        return self.database.catalog


@dataclass(frozen=True, slots=True)
class Column:
    name: str
    type_name: str
    comment: str | None
    partition_key: bool


@dataclass(frozen=True, slots=True)
class StorageDescriptor:
    location: str | None
    input_format: str | None
    output_format: str | None
    serde: str | None
    compressed: bool
    bucket_count: int


@dataclass(frozen=True, slots=True)
class DatabaseSummary:
    ref: DatabaseRef
    description: str | None
    location_uri: str | None
    created_at: datetime | None


@dataclass(frozen=True, slots=True)
class TableSummary:
    ref: TableRef
    description: str | None
    owner: str | None
    table_type: str | None
    created_at: datetime | None
    updated_at: datetime | None


@dataclass(frozen=True, slots=True)
class TableDetail:
    summary: TableSummary
    columns: tuple[Column, ...]
    partition_keys: tuple[Column, ...]
    storage: StorageDescriptor
    classification: str | None
    table_format: TableFormat
    parameters: tuple[tuple[str, str], ...]


@dataclass(frozen=True, slots=True)
class PartitionSummary:
    values: tuple[str, ...]
    created_at: datetime | None
    last_accessed_at: datetime | None
    storage_location: str | None


@dataclass(frozen=True, slots=True)
class ColumnStatistics:
    column_name: str
    type_name: str
    analyzed_at: datetime | None
    values: tuple[tuple[str, str], ...]
```

Import `datetime` for the timestamp fields. Sort `parameters` and statistics `values` by key while mapping so snapshots and equality are deterministic. Export every public type through `__all__`.

- [ ] **Step 4: Run model tests and type checking**

Run:

```bash
uv run pytest tests/unit/domain/test_data_catalog.py -q
uv run mypy src/aws_tui/domain/data_catalog.py
```

Expected: both commands pass.

- [ ] **Step 5: Commit the catalog vocabulary**

```bash
git add src/aws_tui/domain/data_catalog.py tests/unit/domain/test_data_catalog.py
git commit -m "feat: add shared data catalog models"
```

### Task 2: Implement the Paginated Glue Domain Client

**Files:**
- Create: `src/aws_tui/domain/glue.py`
- Create: `tests/unit/domain/test_glue.py`
- Create: `tests/unit/domain/_fake_aws_client.py`

**Interfaces:**
- Produces: `GlueClient(aws_session, connection)`
- Produces: `GlueClient.list_databases_page(start_token=None) -> tuple[list[DatabaseSummary], str | None]`
- Produces: `GlueClient.list_tables_page(database, start_token=None) -> tuple[list[TableSummary], str | None]`
- Produces: `GlueClient.get_table(ref) -> TableDetail`
- Produces: `GlueClient.list_partitions_page(ref, start_token=None) -> tuple[list[PartitionSummary], str | None]`
- Produces: `GlueClient.get_column_statistics(ref, columns) -> tuple[ColumnStatistics, ...]`
- Produces: `GlueClient.list_jobs_page(start_token=None) -> tuple[list[GlueJobSummary], str | None]`
- Produces: `GlueClient.list_job_runs_page(job_name, start_token=None, states=()) -> tuple[list[GlueJobRunSummary], str | None]`
- Produces: `GlueClient.list_crawlers_page(start_token=None, state=None) -> tuple[list[GlueCrawlerSummary], str | None]`
- Produces: `GlueClient.get_crawler(name) -> GlueCrawlerDetail`
- Produces: `GlueClient.get_crawler_metrics(name) -> GlueCrawlerMetrics | None`

- [ ] **Step 1: Write failing mapping, request, pagination, and error tests**

```python
async def test_list_tables_page_maps_context_and_next_token() -> None:
    client, boto = _glue_client(
        "get_tables",
        {
            "TableList": [
                {
                    "Name": "events",
                    "DatabaseName": "analytics",
                    "Description": "curated events",
                    "TableType": "EXTERNAL_TABLE",
                    "Parameters": {"classification": "parquet"},
                    "CreateTime": NOW,
                    "UpdateTime": NOW,
                }
            ],
            "NextToken": "page-2",
        },
    )
    rows, token = await client.list_tables_page("analytics")
    assert rows[0].ref == TableRef(
        "AwsDataCatalog", "analytics", "events", "dev", "us-east-1"
    )
    assert token == "page-2"
    boto.get_tables.assert_awaited_once_with(
        DatabaseName="analytics",
        MaxResults=100,
    )


async def test_second_page_passes_opaque_token() -> None:
    client, boto = _glue_client("get_databases", {"DatabaseList": []})
    await client.list_databases_page(start_token="opaque")
    boto.get_databases.assert_awaited_once_with(MaxResults=100, NextToken="opaque")


async def test_access_denied_maps_to_permission_error() -> None:
    client, boto = _glue_client("get_jobs", None)
    boto.get_jobs.side_effect = _client_error("AccessDeniedException", "denied")
    with pytest.raises(PermissionDeniedError, match="denied"):
        await client.list_jobs_page()
```

Also cover missing optional fields, malformed required fields, credentials, throttling, transport errors, Lake Formation access-denied messages, partitions, column-statistics batches, jobs/runs, crawler detail, and crawler metrics.

- [ ] **Step 2: Run the Glue domain tests and verify failure**

Run:

```bash
uv run pytest tests/unit/domain/test_glue.py -q
```

Expected: collection fails because `aws_tui.domain.glue` does not exist.

- [ ] **Step 3: Implement domain records, API calls, and canonical error mapping**

Define these frozen, slot-backed records exactly:

```python
@dataclass(frozen=True, slots=True)
class GlueJobSummary:
    name: str
    description: str | None
    role: str
    glue_version: str | None
    command_name: str
    script_location: str | None
    worker_type: str | None
    worker_count: int | None
    timeout_minutes: int | None
    max_retries: int | None
    default_arguments: tuple[tuple[str, str], ...]


@dataclass(frozen=True, slots=True)
class GlueJobRunSummary:
    job_name: str
    run_id: str
    state: str
    attempt: int
    trigger_name: str | None
    started_at: datetime | None
    completed_at: datetime | None
    execution_time_seconds: int | None
    execution_class: str | None
    allocated_capacity: int | None
    arguments: tuple[tuple[str, str], ...]
    predecessor_run_ids: tuple[str, ...]
    error_message: str | None
    state_detail: str | None
    log_group_name: str | None


@dataclass(frozen=True, slots=True)
class GlueCrawlerSummary:
    name: str
    state: str
    role: str
    database_name: str | None
    schedule_expression: str | None


@dataclass(frozen=True, slots=True)
class GlueCrawlerMetrics:
    crawler_name: str
    still_estimating: bool
    time_left_seconds: float | None
    median_runtime_seconds: float | None
    tables_created: int
    tables_updated: int
    tables_deleted: int


@dataclass(frozen=True, slots=True)
class GlueCrawlerDetail:
    summary: GlueCrawlerSummary
    targets: tuple[str, ...]
    classifiers: tuple[str, ...]
    recrawl_behavior: str | None
    schema_update_behavior: str | None
    schema_delete_behavior: str | None
    security_configuration: str | None
    lake_formation_account_id: str | None
    use_lake_formation_credentials: bool
    tags: tuple[tuple[str, str], ...]
    last_crawl_status: str | None
    last_crawl_started_at: datetime | None
    last_crawl_duration_seconds: float | None
    last_crawl_error: str | None
    metrics: GlueCrawlerMetrics | None
    supplemental_warnings: tuple[str, ...]
```

Construct the client as:

```python
class GlueClient:
    def __init__(self, *, aws_session: AwsSession, connection: Connection) -> None:
        self._aws_session = aws_session
        self._connection = connection

    async def list_databases_page(
        self,
        *,
        start_token: str | None = None,
    ) -> tuple[list[DatabaseSummary], str | None]:
        kwargs: dict[str, object] = {"MaxResults": 100}
        if start_token is not None:
            kwargs["NextToken"] = start_token
        try:
            async with await self._aws_session.client(self._connection, "glue") as client:
                response = await client.get_databases(**kwargs)
            rows = [self._map_database(item) for item in response.get("DatabaseList", ())]
            return rows, cast(str | None, response.get("NextToken"))
        except Exception as exc:
            raise_mapped_glue_error(exc)
```

Each list method performs one AWS page call. Omit optional request keys instead of sending `None`. Use `MaxResults=100` for databases/tables/jobs/crawlers and `MaxResults=200` for job runs. Batch column names to Glue's API maximum. A non-empty job-run state tuple maps to Glue `States`; crawler state is a local page filter because `GetCrawlers` has no state request parameter. `get_crawler_metrics(name)` calls `GetCrawlerMetrics` with `CrawlerNameList=[name]` and returns `None` for an empty metrics list.

`GetCrawler` does not return resource tags. On the first crawler-detail request, obtain and cache the account ID and partition from `sts:GetCallerIdentity`, build `arn:{partition}:glue:{region}:{account_id}:crawler/{name}`, and call Glue `GetTags`; combine that bounded response with `GetCrawler` and `GetCrawlerMetrics` into `GlueCrawlerDetail`. Test the `aws`, `aws-us-gov`, and `aws-cn` ARN partitions, and surface a tag/metrics permission failure as a partial detail warning rather than erasing the core crawler response.

Map Glue and botocore errors to existing provider errors. Add a `LakeFormationPermissionError(PermissionDeniedError)` for access-denied responses whose service/message identifies Lake Formation. Redact visible messages through `infra.redaction.redact_text` before a VM displays them.

- [ ] **Step 4: Run Glue domain and contract tests**

Run:

```bash
uv run pytest tests/unit/domain/test_glue.py tests/unit/domain/test_data_catalog.py -q
uv run mypy src/aws_tui/domain/glue.py src/aws_tui/domain/data_catalog.py
```

Expected: all tests pass.

- [ ] **Step 5: Commit the Glue client**

```bash
git add src/aws_tui/domain/glue.py tests/unit/domain/test_glue.py tests/unit/domain/_fake_aws_client.py
git commit -m "feat: add paginated Glue domain client"
```

### Task 3: Build Glue Catalog, Jobs, and Crawlers ViewModels

**Files:**
- Create: `src/aws_tui/vm/glue/__init__.py`
- Create: `src/aws_tui/vm/glue/_errors.py`
- Create: `src/aws_tui/vm/glue/catalog_vm.py`
- Create: `src/aws_tui/vm/glue/jobs_vm.py`
- Create: `src/aws_tui/vm/glue/crawlers_vm.py`
- Create: `src/aws_tui/vm/glue/page_vm.py`
- Test: `tests/unit/vm/glue/test_catalog_vm.py`
- Test: `tests/unit/vm/glue/test_jobs_vm.py`
- Test: `tests/unit/vm/glue/test_crawlers_vm.py`
- Test: `tests/unit/vm/glue/test_page_vm.py`

**Interfaces:**
- Produces: `GlueCatalogVM`, `GlueJobsVM`, `GlueCrawlersVM`
- Produces: `GluePageVM(client, connection, hub, dispatcher)`
- Produces: `GluePageVM.source -> ServiceSourceContext`
- Produces: `GluePageVM.active_view` with values `catalog`, `jobs`, `crawlers`
- Consumes: `ServiceSelectionStore` and `SelectionScope` from the foundation plan
- Consumes: the exact `GlueClient` methods from Task 2

- [ ] **Step 1: Write failing VM behavior tests**

```python
async def test_catalog_select_database_resets_tables_and_discards_stale_load() -> None:
    fake = InMemoryGlue()
    fake.add_database("a")
    fake.add_database("b")
    fake.block_tables("a")
    vm = make_catalog_vm(fake)
    await vm.setup()
    first = asyncio.create_task(vm.select_database("a"))
    await fake.tables_started.wait()
    await vm.select_database("b")
    fake.release_tables("a")
    await first
    assert vm.selected_database_name == "b"
    assert [row.ref.database_name for row in vm.tables] == ["b"]


async def test_jobs_pages_runs_for_selected_job() -> None:
    fake = seeded_glue()
    fake.run_page_size = 1
    vm = make_jobs_vm(fake)
    await vm.setup()
    await vm.select_job("nightly")
    assert len(vm.runs) == 1
    assert vm.has_more_runs
    await vm.load_more_runs()
    assert len(vm.runs) == 2


async def test_crawler_access_denied_is_scoped_to_crawlers_view() -> None:
    fake = seeded_glue()
    fake.crawlers_error = PermissionDeniedError("glue:GetCrawlers denied")
    page = make_page_vm(fake)
    await page.setup()
    await page.select_view("crawlers")
    assert page.crawlers.state is PaneState.FORBIDDEN
    assert page.catalog.state is PaneState.IDLE
```

Add lifecycle tests proving every pager/command is disposed and per-connection selection keys include region.

- [ ] **Step 2: Run Glue VM tests and verify missing modules**

Run:

```bash
uv run pytest tests/unit/vm/glue -q
```

Expected: collection fails because the Glue VM package does not exist.

- [ ] **Step 3: Implement independent paged VMs and the page coordinator**

Use `TokenPagedComposition` for databases, tables, partitions, jobs, runs, and crawlers. Each load callback captures a monotonically increasing generation:

```python
async def select_database(self, database_name: str) -> None:
    self._generation += 1
    generation = self._generation
    self._selected_database_name = database_name
    self._replace_table_pager(database_name)
    await self._table_pager.load_initial()
    if generation != self._generation:
        return
    self._notify("selected_database_name")
    self._notify("tables")
```

`GluePageVM.setup()` loads only the active `catalog` view. A view is loaded on first selection. Construct `source = ServiceSourceContext.from_connection(connection)` and `scope = SelectionScope("glue", connection.name, connection.region)`. Read and write `active_view`, `database_name`, `table_name`, `job_name`, `job_run_states`, `crawler_name`, and `crawler_state` through the injected `ServiceSelectionStore`; serialize multi-state filters as sorted comma-separated values. Restore an identifier only after the newest page confirms it still exists, otherwise discard it and select the first available item. Changing a filter replaces its pager so old pagination tokens cannot leak across request semantics. `dispose()` cascades through every child exactly once but does not dispose the service-owned store.

Use the existing `map_provider_error` behavior for `PaneState`; extend Glue's mapper so Lake Formation denial maps to `FORBIDDEN` with a redacted message.

- [ ] **Step 4: Run all Glue VM tests**

Run:

```bash
uv run pytest tests/unit/vm/glue -q
uv run mypy src/aws_tui/vm/glue
```

Expected: all tests pass.

- [ ] **Step 5: Commit Glue VMs**

```bash
git add src/aws_tui/vm/glue tests/unit/vm/glue
git commit -m "feat: add Glue catalog jobs and crawlers viewmodels"
```

### Task 4: Build the Glue Textual Page

**Files:**
- Create: `src/aws_tui/ui/widgets/glue/__init__.py`
- Create: `src/aws_tui/ui/widgets/glue/page.py`
- Create: `src/aws_tui/ui/widgets/glue/catalog_view.py`
- Create: `src/aws_tui/ui/widgets/glue/jobs_view.py`
- Create: `src/aws_tui/ui/widgets/glue/crawlers_view.py`
- Create: `src/aws_tui/ui/widgets/glue/detail_rows.py`
- Modify: `src/aws_tui/ui/widgets/service_view_factory.py`
- Modify: `src/aws_tui/vm/chrome/hint_legend_vm.py`
- Modify: `src/aws_tui/infra/keymap_store.py`
- Test: `tests/unit/ui/glue/test_page.py`
- Test: `tests/unit/ui/test_service_view_factory.py`
- Test: `tests/snapshot/test_glue.py`

**Interfaces:**
- Produces: `GluePage(vm, hub, focus_coordinator, id=None)`
- Extends: `build_service_view("glue", ...)`

- [ ] **Step 1: Write page composition and snapshot tests**

```python
async def test_glue_page_composes_three_views(app) -> None:
    page = app.query_one(GluePage)
    assert page.query_one("#glue-source-header")
    assert page.query_one("#glue-view-tabs")
    assert page.query_one(GlueCatalogView)


@pytest.mark.parametrize("theme", THEMES)
def test_glue_catalog_populated_snapshot(snap_compare, theme: str) -> None:
    assert snap_compare(
        "snapshot_apps/glue_catalog.py",
        terminal_size=(150, 44),
        theme=theme,
    )
```

Pair each snapshot with SVG assertions for the source label, database, table, storage location, job state, crawler state, and forbidden/empty placeholders.

- [ ] **Step 2: Run UI tests and verify failure**

Run:

```bash
uv run pytest tests/unit/ui/glue tests/unit/ui/test_service_view_factory.py tests/snapshot/test_glue.py -q
```

Expected: missing Glue widgets and factory branch failures.

- [ ] **Step 3: Implement the page and focused-pane actions**

Build an unframed source header followed by a compact three-choice tab strip. Catalog uses database, table, and detail panes; Jobs uses job, run-state filter, run, and detail panes; Crawlers uses a state filter, crawler list, and detail panes. Use menu/select controls for filters. Keep stable grid widths with `fr` tracks and permit vertical scrolling inside detail panes.

Add service actions with unambiguous IDs:

```python
"glue.catalog": ("1",),
"glue.jobs": ("2",),
"glue.crawlers": ("3",),
```

Register labels in `HintLegendVM`, route `r` to the focused Glue VM refresh, and extend `build_service_view`:

```python
if service_id == "glue":
    return GluePage(
        vm,
        hub=hub,
        focus_coordinator=focus_coordinator,
        id="content-glue-page",
    )
```

Use `DEFAULT_CSS` for stable geometry and existing theme classes/tokens for focus, state, and selection. Never render AWS-controlled text with Rich markup enabled.

- [ ] **Step 4: Update snapshots and run UI tests**

Run:

```bash
uv run pytest tests/snapshot/test_glue.py --snapshot-update
uv run pytest tests/unit/ui/glue tests/unit/ui/test_service_view_factory.py tests/snapshot/test_glue.py -q
```

Expected: all tests pass on every theme and SVG content guards pass.

- [ ] **Step 5: Commit Glue UI**

```bash
git add src/aws_tui/ui/widgets/glue src/aws_tui/ui/widgets/service_view_factory.py src/aws_tui/vm/chrome/hint_legend_vm.py src/aws_tui/infra/keymap_store.py tests/unit/ui/glue tests/unit/ui/test_service_view_factory.py tests/snapshot/test_glue.py tests/snapshot/__snapshots__/test_glue
git commit -m "feat: add Glue service page"
```

### Task 5: Register Glue and Add Multi-Profile Demo Data

**Files:**
- Create: `src/aws_tui/services/glue/__init__.py`
- Create: `src/aws_tui/services/glue/service.py`
- Create: `src/aws_tui/demo/in_memory_glue.py`
- Modify: `src/aws_tui/demo/seeds.py`
- Modify: `src/aws_tui/composition.py`
- Modify: `src/aws_tui/app.py`
- Test: `tests/unit/services/glue/test_service.py`
- Test: `tests/integration/test_glue_page.py`
- Test: `tests/integration/test_demo_mode.py`

**Interfaces:**
- Produces: `GlueService`
- Produces: `InMemoryGlue` implementing the Task 2 client surface
- Extends: registry order to S3, EMR Serverless, Glue

- [ ] **Step 1: Write service registration and profile-isolation tests**

```python
def test_glue_service_is_aws_only() -> None:
    service = GlueService(hub=hub, dispatcher=dispatcher, aws_session=aws_session)
    assert service.supports(aws_connection)
    assert not service.supports(minio_connection)


async def test_glue_demo_profiles_have_disjoint_catalogs(tmp_path: Path) -> None:
    ctx = build_app_context(config_dir=tmp_path / "config", cache_dir=tmp_path / "cache", demo=True)
    app = AwsTuiApp(ctx)
    async with app.run_test() as pilot:
        await open_service(ctx, pilot, "glue")
        assert "dev_events" in app.screen.render_str()
        await app.action_swap_source()
        await wait_for_service_setup(ctx)
        rendered = app.screen.render_str()
        assert "prod_sales" in rendered
        assert "dev_events" not in rendered
```

- [ ] **Step 2: Run service and demo tests to verify failure**

Run:

```bash
uv run pytest tests/unit/services/glue/test_service.py tests/integration/test_glue_page.py tests/integration/test_demo_mode.py -q
```

Expected: missing Glue service/demo modules and nav row.

- [ ] **Step 3: Implement service composition and profile-keyed fake data**

`GlueService` must accept a client factory for tests/demo:

```python
GlueClientFactory = Callable[[Connection], GlueClientProtocol]


class GlueService:
    descriptor = ServiceDescriptor(id="glue", label="Glue", icon="🔗")

    def build_vm(self, connection: Connection) -> GluePageVM:
        client = (
            self._client_factory(connection)
            if self._client_factory is not None
            else GlueClient(aws_session=self._aws_session, connection=connection)
        )
        return GluePageVM(
            client=client,
            connection=connection,
            selections=self._selections,
            hub=self._hub,
            dispatcher=self._dispatcher,
        )
```

Initialize `self._selections = ServiceSelectionStore()` once in `GlueService.__init__`; the service outlives rebuilt page VMs and therefore retains validated selections across profile switches. Seed at least two disjoint demo profiles with ordinary Parquet tables, successful and failed jobs/runs, ready/running/failed crawlers, empty states, and one access-denied hook. Store demo clients by connection name rather than sharing one global fake.

Register Glue after EMR in `build_app_context`; add `demo_glue` lifecycle storage if the fake owns tasks. Add Glue focus and mount cases in `app.py` using the service view factory.

- [ ] **Step 4: Run service, integration, and demo tests**

Run:

```bash
uv run pytest tests/unit/services/glue tests/integration/test_glue_page.py tests/integration/test_demo_mode.py -q
```

Expected: all tests pass.

- [ ] **Step 5: Commit registration and demo mode**

```bash
git add src/aws_tui/services/glue src/aws_tui/demo/in_memory_glue.py src/aws_tui/demo/seeds.py src/aws_tui/composition.py src/aws_tui/app.py tests/unit/services/glue tests/integration/test_glue_page.py tests/integration/test_demo_mode.py
git commit -m "feat: register Glue service and demo data"
```

### Task 6: Add Glue-to-S3 Handoff, Documentation, and Full Verification

**Files:**
- Modify: `src/aws_tui/vm/messages.py`
- Modify: `src/aws_tui/vm/glue/catalog_vm.py`
- Modify: `src/aws_tui/app.py`
- Modify: `README.md`
- Modify: `docs/architecture.md`
- Modify: `docs/adding-a-service.md`
- Modify: `docs/connections.md`
- Modify: `docs/cookbook.md`
- Modify: `docs/contract-ledger.md`
- Modify: `docs/keybindings.md`
- Modify: `CHANGELOG.md`
- Test: `tests/unit/vm/test_messages.py`
- Test: `tests/integration/test_glue_s3_handoff.py`
- Test: `tests/e2e/test_journeys.py`

**Interfaces:**
- Produces: `OpenS3LocationRequest(connection_name, region, uri, preferred_pane)`
- Consumes: selected `TableDetail.storage.location`

- [ ] **Step 1: Write failing message and handoff tests**

```python
def test_open_s3_request_carries_source_identity() -> None:
    request = OpenS3LocationRequest(
        connection_name="prod-west",
        region="us-west-2",
        uri="s3://lake/events/",
        preferred_pane="left",
    )
    assert request.sender_name == "service_navigation"
    assert request.sender_object is request


async def test_glue_table_location_opens_same_profile_in_s3(...) -> None:
    await open_glue_table("prod-west", "analytics", "events")
    await invoke("glue.open_s3_location")
    await wait_for_service_setup(ctx)
    assert ctx.root_vm.content_host.current_id == "s3"
    assert ctx.root_vm.active_connection.name == "prod-west"
    pane = ctx.root_vm.content_host.current.left
    assert pane.path.as_posix() == "/lake/events"
```

- [ ] **Step 2: Run handoff tests and verify failure**

Run:

```bash
uv run pytest tests/unit/vm/test_messages.py tests/integration/test_glue_s3_handoff.py -q
```

Expected: missing message and app subscriber failures.

- [ ] **Step 3: Implement connection-preserving S3 navigation**

Add the immutable message to `vm/messages.py` with a service-neutral sender because Athena reuses it:

```python
@dataclass(frozen=True, slots=True)
class OpenS3LocationRequest:
    connection_name: str
    region: str
    uri: str
    preferred_pane: Literal["left", "right"] = "left"
    sender_name: str = "service_navigation"

    @property
    def sender_object(self) -> object:
        return self
```

The Glue VM command validates an `s3://` location and sends the request. The app subscriber must:

1. resolve `connection_name`;
2. reject a region mismatch instead of silently substituting a connection;
3. switch root connection and service to `s3`;
4. mount the S3 view;
5. choose the requested pane;
6. bind it to the resolved connection;
7. navigate to `PathRef.from_posix(urlparse(uri).netloc + urlparse(uri).path)`;
8. focus that pane.

Malformed or missing locations produce an advisory toast and no navigation. Do not log the full URI if query parameters or credentials are present.

Update all listed docs with Glue features, permissions, profile switching, API operations, demo behavior, and keybindings. Do not document Iceberg metadata or Athena cross-links yet.

- [ ] **Step 4: Run the full Glue verification matrix**

Run:

```bash
uv run pytest tests/unit tests/integration tests/e2e -q
uv run pytest tests/snapshot -q
uv run ruff check .
uv run ruff format --check .
uv run mypy src
./scripts/check-layers.sh
uv run pytest tests/docs -q
uv build
```

Expected: all required checks pass; only documented optional dependency skips are allowed.

- [ ] **Step 5: Commit Glue handoff and documentation**

```bash
git add src/aws_tui/vm/messages.py src/aws_tui/vm/glue/catalog_vm.py src/aws_tui/app.py README.md docs CHANGELOG.md tests/unit/vm/test_messages.py tests/integration/test_glue_s3_handoff.py tests/e2e/test_journeys.py
git commit -m "docs: complete Glue service workflows"
```
