# Iceberg Cross-Service Integration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Iceberg a shared Glue/Athena/S3 workflow with metadata inspection, connection-preserving cross-navigation, and generated time-travel queries.

**Architecture:** Central format detection and Iceberg records live in the domain layer. A bounded Athena query runner is extracted from the Athena VM and reused by `IcebergInspector`, so Glue can inspect Iceberg metadata without importing Athena VMs. VMx messages carry complete connection and table identity between Glue, Athena, and S3; the app composition root performs service mounting and destination selection.

**Tech Stack:** Python 3.11-3.13, Textual 8.x, VMx 3.1.x, aioboto3/botocore Glue and Athena APIs, sqlglot 30.x, pytest, pytest-textual-snapshot, repository architecture-diagram tooling.

## Global Constraints

- Requires the completed profile-switching, Glue, and Athena plans.
- Iceberg is not a separate navigation service.
- Do not add PyIceberg, Arrow, DuckDB, DataFusion, or a JVM.
- Iceberg metadata is read through bounded Athena metadata-table queries.
- Supported metadata tables are `$snapshots`, `$history`, `$manifests`, `$files`, `$partitions`, and `$refs`.
- Generated time-travel queries are inserted into the editor and never auto-executed.
- Cross-navigation preserves connection name, region, catalog, database, table, workgroup, and snapshot identity where applicable.
- Missing destination connections fail visibly; never substitute another profile.
- Full SQL, result rows, table parameters, and raw boto responses never enter logs or crash dumps.
- Preserve the enforced View -> ViewModel -> Service -> Domain -> Infrastructure dependency direction.

---

### Task 1: Detect Table Formats and Define Iceberg Metadata Records

**Files:**
- Modify: `src/aws_tui/domain/data_catalog.py`
- Create: `src/aws_tui/domain/iceberg.py`
- Modify: `src/aws_tui/domain/glue.py`
- Test: `tests/unit/domain/test_data_catalog.py`
- Test: `tests/unit/domain/test_iceberg.py`
- Test: `tests/unit/domain/test_glue.py`

**Interfaces:**
- Produces: `detect_table_format(parameters, input_format, table_type) -> TableFormat`
- Produces: `IcebergSnapshot`, `IcebergHistoryEntry`, `IcebergManifest`
- Produces: `IcebergDataFile`, `IcebergPartitionSpec`, `IcebergPartition`, `IcebergReference`
- Extends: `TableDetail.table_format`

- [ ] **Step 1: Write failing format-detection and record tests**

```python
@pytest.mark.parametrize(
    ("parameters", "input_format", "table_type", "expected"),
    [
        ({"table_type": "ICEBERG"}, None, "EXTERNAL_TABLE", TableFormat.ICEBERG),
        ({"tableType": "ICEBERG"}, None, "EXTERNAL_TABLE", TableFormat.ICEBERG),
        ({"classification": "hudi"}, None, "EXTERNAL_TABLE", TableFormat.HUDI),
        ({"spark.sql.sources.provider": "delta"}, None, "EXTERNAL_TABLE", TableFormat.DELTA),
        ({"classification": "parquet"}, "MapredParquetInputFormat", "EXTERNAL_TABLE", TableFormat.HIVE),
        ({}, None, "VIRTUAL_VIEW", TableFormat.OTHER),
    ],
)
def test_detect_table_format(parameters, input_format, table_type, expected) -> None:
    assert detect_table_format(parameters, input_format, table_type) is expected


def test_iceberg_snapshot_is_frozen_and_keeps_summary() -> None:
    snapshot = IcebergSnapshot(
        snapshot_id=42,
        parent_id=41,
        committed_at=NOW,
        operation="append",
        manifest_list="s3://lake/table/metadata/snap-42.avro",
        summary=(("added-records", "100"),),
    )
    assert snapshot.snapshot_id == 42
    assert snapshot.summary == (("added-records", "100"),)


def test_partition_spec_preserves_dynamic_partition_columns() -> None:
    spec = IcebergPartitionSpec(("event_date", "region_bucket"))
    assert spec.field_names == ("event_date", "region_bucket")
```

- [ ] **Step 2: Run domain tests and verify failure**

Run:

```bash
uv run pytest tests/unit/domain/test_data_catalog.py tests/unit/domain/test_iceberg.py tests/unit/domain/test_glue.py -q
```

Expected: missing detector, Iceberg module, and `TableDetail.table_format` failures.

- [ ] **Step 3: Implement centralized detection and frozen metadata models**

Implement detection without view-layer parameter inspection:

```python
def detect_table_format(
    parameters: Mapping[str, str],
    input_format: str | None,
    table_type: str | None,
) -> TableFormat:
    normalized = {key.casefold(): value.casefold() for key, value in parameters.items()}
    declared = normalized.get("table_type") or normalized.get("tabletype")
    classification = normalized.get("classification")
    provider = normalized.get("spark.sql.sources.provider")
    if declared == "iceberg" or classification == "iceberg" or provider == "iceberg":
        return TableFormat.ICEBERG
    if classification == "hudi" or provider == "hudi":
        return TableFormat.HUDI
    if classification == "delta" or provider == "delta":
        return TableFormat.DELTA
    if table_type in {"EXTERNAL_TABLE", "MANAGED_TABLE"} or input_format:
        return TableFormat.HIVE
    return TableFormat.OTHER
```

Keep table parameters internally for detection but expose only an immutable, redacted tuple on `TableDetail`. Add these exact frozen, slot-backed records; optional fields account for metadata differences across Iceberg versions and Athena engine output:

```python
@dataclass(frozen=True, slots=True)
class IcebergSnapshot:
    committed_at: datetime
    snapshot_id: int
    parent_id: int | None
    operation: str
    manifest_list: str
    summary: tuple[tuple[str, str], ...]


@dataclass(frozen=True, slots=True)
class IcebergHistoryEntry:
    made_current_at: datetime
    snapshot_id: int
    parent_id: int | None
    is_current_ancestor: bool


@dataclass(frozen=True, slots=True)
class IcebergManifest:
    path: str
    length: int
    partition_spec_id: int
    added_snapshot_id: int
    added_data_files_count: int
    existing_data_files_count: int
    deleted_data_files_count: int
    partition_summaries: str | None


@dataclass(frozen=True, slots=True)
class IcebergDataFile:
    content: int
    file_path: str
    file_format: str
    spec_id: int
    partition: str | None
    record_count: int
    file_size_in_bytes: int
    equality_ids: str | None
    sort_order_id: int | None


@dataclass(frozen=True, slots=True)
class IcebergPartitionSpec:
    field_names: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class IcebergPartition:
    values: tuple[tuple[str, str | None], ...]
    record_count: int
    file_count: int
    total_data_file_size_in_bytes: int
    position_delete_record_count: int | None
    position_delete_file_count: int | None
    equality_delete_record_count: int | None
    equality_delete_file_count: int | None
    last_updated_at: datetime | None
    last_updated_snapshot_id: int | None


@dataclass(frozen=True, slots=True)
class IcebergReference:
    name: str
    ref_type: str
    snapshot_id: int
    max_reference_age_in_ms: int | None
    min_snapshots_to_keep: int | None
    max_snapshot_age_in_ms: int | None
```

Glue mapping calls `detect_table_format` once while constructing detail. Parse structured Athena cells into these values at the inspector boundary; retain complex manifest/file fields as bounded display strings instead of exposing sqlglot or boto objects.

- [ ] **Step 4: Run domain tests and type checking**

Run:

```bash
uv run pytest tests/unit/domain/test_data_catalog.py tests/unit/domain/test_iceberg.py tests/unit/domain/test_glue.py -q
uv run mypy src/aws_tui/domain/data_catalog.py src/aws_tui/domain/iceberg.py src/aws_tui/domain/glue.py
```

Expected: all tests pass.

- [ ] **Step 5: Commit format detection and models**

```bash
git add src/aws_tui/domain/data_catalog.py src/aws_tui/domain/iceberg.py src/aws_tui/domain/glue.py tests/unit/domain/test_data_catalog.py tests/unit/domain/test_iceberg.py tests/unit/domain/test_glue.py
git commit -m "feat: detect Iceberg catalog tables"
```

### Task 2: Extract a Bounded Athena Runner and Implement IcebergInspector

**Files:**
- Create: `src/aws_tui/domain/athena_runner.py`
- Modify: `src/aws_tui/vm/athena/query_vm.py`
- Modify: `src/aws_tui/services/athena/service.py`
- Modify: `src/aws_tui/domain/iceberg.py`
- Test: `tests/unit/domain/test_athena_runner.py`
- Modify: `tests/unit/vm/athena/test_query_vm.py`
- Modify: `tests/unit/domain/test_iceberg.py`

**Interfaces:**
- Produces: `AthenaQueryRunner(client, policy, sleep=anyio.sleep)`
- Produces: `AthenaQueryRunner.run(sql, context, request_token, max_rows) -> BoundedQueryResult`
- Produces: `IcebergInspector(runner, context)`
- Produces: `list_snapshots`, `list_history`, `list_manifests`, `list_files`, `list_partitions`, and `list_refs`
- Produces: `IcebergInspector.partition_spec(table_ref) -> IcebergPartitionSpec`

- [ ] **Step 1: Write failing runner and inspector tests**

```python
async def test_runner_polls_and_stops_at_row_limit() -> None:
    fake = seeded_athena(states=[QueryState.QUEUED, QueryState.RUNNING, QueryState.SUCCEEDED])
    runner = AthenaQueryRunner(fake, ReadOnlySqlPolicy(), sleep=_no_sleep)
    result = await runner.run(
        "SELECT snapshot_id FROM x LIMIT 100",
        CONTEXT,
        request_token="metadata-1",
        max_rows=2,
    )
    assert result.detail.state is QueryState.SUCCEEDED
    assert len(result.rows) == 2
    assert fake.result_page_calls == 1


async def test_inspector_quotes_identifiers_and_maps_snapshots() -> None:
    runner = RecordingRunner(SNAPSHOT_RESULT)
    inspector = IcebergInspector(runner=runner, context=CONTEXT)
    rows = await inspector.list_snapshots(
        TableRef("AwsDataCatalog", "analytics", "order-events", "dev", "us-east-1")
    )
    assert runner.sql == (
        'SELECT committed_at, snapshot_id, parent_id, operation, manifest_list, summary '
        'FROM "AwsDataCatalog"."analytics"."order-events$snapshots" '
        'ORDER BY committed_at DESC LIMIT 100'
    )
    assert rows[0].snapshot_id == 42
```

Add tests for failure, cancellation, page bounds, files, manifests, history, partitions, refs, missing columns, hostile identifier quoting, and dynamic partition-spec extraction.

- [ ] **Step 2: Run runner/inspector tests and verify failure**

Run:

```bash
uv run pytest tests/unit/domain/test_athena_runner.py tests/unit/domain/test_iceberg.py tests/unit/vm/athena/test_query_vm.py -q
```

Expected: missing runner and inspector methods.

- [ ] **Step 3: Extract query execution and implement bounded metadata mapping**

Move policy validation, start, polling, statistics, and optional stop behavior into `AthenaQueryRunner`. The runner returns:

```python
@dataclass(frozen=True, slots=True)
class BoundedQueryResult:
    detail: QueryExecutionDetail
    columns: tuple[ResultColumn, ...]
    rows: tuple[tuple[str | None, ...], ...]
```

It pages only until `max_rows`, truncates the final page to the bound, and never starts a second query after cancellation. `AthenaQueryVM` delegates to the runner and retains UI ownership/generation logic.

Quote identifiers with:

```python
def quote_athena_identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'
```

Every inspector method uses an explicit projection, deterministic ordering where meaningful, and a hard limit: snapshots/history/refs 100, manifests/partitions 500, files 1000. Reject a `TableRef` whose connection or region differs from the inspector context.

`$partitions` has table-dependent leading columns. Derive `IcebergPartitionSpec.field_names` from the result metadata by excluding the fixed metric columns `record_count`, `file_count`, `total_data_file_size_in_bytes`, `position_delete_record_count`, `position_delete_file_count`, `equality_delete_record_count`, `equality_delete_file_count`, `last_updated_at`, and `last_updated_snapshot_id`. Preserve the remaining column order and map those cells into `IcebergPartition.values`; fail with a typed metadata-shape error when any required fixed metric column is absent.

- [ ] **Step 4: Run domain and Athena VM tests**

Run:

```bash
uv run pytest tests/unit/domain/test_athena_runner.py tests/unit/domain/test_iceberg.py tests/unit/vm/athena -q
uv run mypy src/aws_tui/domain/athena_runner.py src/aws_tui/domain/iceberg.py src/aws_tui/vm/athena/query_vm.py
```

Expected: all tests pass.

- [ ] **Step 5: Commit the shared runner and inspector**

```bash
git add src/aws_tui/domain/athena_runner.py src/aws_tui/domain/iceberg.py src/aws_tui/vm/athena/query_vm.py src/aws_tui/services/athena/service.py tests/unit/domain/test_athena_runner.py tests/unit/domain/test_iceberg.py tests/unit/vm/athena/test_query_vm.py
git commit -m "feat: inspect Iceberg metadata through Athena"
```

### Task 3: Add Connection-Preserving Glue and Athena Cross-Navigation

**Files:**
- Modify: `src/aws_tui/vm/messages.py`
- Modify: `src/aws_tui/vm/glue/catalog_vm.py`
- Modify: `src/aws_tui/vm/athena/page_vm.py`
- Modify: `src/aws_tui/domain/sql_policy.py`
- Modify: `src/aws_tui/app.py`
- Test: `tests/unit/vm/test_messages.py`
- Modify: `tests/unit/domain/test_sql_policy.py`
- Test: `tests/integration/test_glue_athena_navigation.py`

**Interfaces:**
- Produces: `OpenAthenaTableRequest(table_ref, snapshot_id=None)`
- Produces: `OpenGlueTableRequest(table_ref)`
- Produces: `ReadOnlySqlPolicy.table_refs(sql, context) -> tuple[TableRef, ...]`
- Produces: `AthenaPageVM.open_table(table_ref, snapshot_id=None) -> None`
- Produces: `GlueCatalogVM.open_table(table_ref) -> None`

- [ ] **Step 1: Write failing message, SQL-source, and navigation tests**

```python
def test_cross_service_messages_preserve_table_security_context() -> None:
    table = TableRef("AwsDataCatalog", "analytics", "events", "prod-west", "us-west-2")
    athena = OpenAthenaTableRequest(table_ref=table, snapshot_id=42)
    glue = OpenGlueTableRequest(table_ref=table)
    assert athena.table_ref == glue.table_ref == table
    assert athena.snapshot_id == 42


def test_sql_policy_extracts_one_default_catalog_table() -> None:
    refs = ReadOnlySqlPolicy().table_refs("SELECT * FROM events", CONTEXT)
    assert refs == (
        TableRef("AwsDataCatalog", "analytics", "events", "prod-west", "us-west-2"),
    )


async def test_glue_to_athena_preserves_profile_and_prefills_without_running(...) -> None:
    await open_glue_table("prod-west", "analytics", "events")
    await invoke("glue.query_in_athena")
    await wait_for_service_setup(ctx)
    assert ctx.root_vm.content_host.current_id == "athena"
    assert ctx.root_vm.active_connection.name == "prod-west"
    page = ctx.root_vm.content_host.current
    assert page.context.database == "analytics"
    assert page.query.sql.endswith('FROM "AwsDataCatalog"."analytics"."events" LIMIT 100')
    assert page.query.active_ref is None
```

Also test deleted connections, region mismatch, multiple query tables disabling direct Glue navigation, and Athena-to-Glue table selection.

- [ ] **Step 2: Run cross-navigation tests and verify failure**

Run:

```bash
uv run pytest tests/unit/vm/test_messages.py tests/unit/domain/test_sql_policy.py tests/integration/test_glue_athena_navigation.py -q
```

Expected: missing message classes, source extraction, and app handlers.

- [ ] **Step 3: Implement messages, source extraction, and app orchestration**

Use sqlglot table nodes to resolve catalog/database defaults. Do not infer a Glue target when a table uses a different catalog not visible in the current context or when more than one distinct table appears.

Generate starter SQL with exact quoting:

```python
def select_starter_sql(ref: TableRef, snapshot_id: int | None = None) -> str:
    qualified = ".".join(
        quote_athena_identifier(part)
        for part in (ref.catalog_name, ref.database_name, ref.table_name)
    )
    travel = f" FOR VERSION AS OF {snapshot_id}" if snapshot_id is not None else ""
    return f"SELECT * FROM {qualified}{travel} LIMIT 100"
```

The app handler resolves the exact connection, checks region equality, switches service, mounts the destination, and then calls `open_table`. Missing or mismatched connections produce an error toast and leave the current page unchanged.

- [ ] **Step 4: Run navigation, Glue, and Athena integration tests**

Run:

```bash
uv run pytest tests/integration/test_glue_athena_navigation.py tests/integration/test_glue_page.py tests/integration/test_athena_page.py -q
```

Expected: all tests pass.

- [ ] **Step 5: Commit cross-navigation**

```bash
git add src/aws_tui/vm/messages.py src/aws_tui/vm/glue/catalog_vm.py src/aws_tui/vm/athena/page_vm.py src/aws_tui/domain/sql_policy.py src/aws_tui/app.py tests/unit/vm/test_messages.py tests/unit/domain/test_sql_policy.py tests/integration/test_glue_athena_navigation.py
git commit -m "feat: link Glue tables and Athena queries"
```

### Task 4: Add Iceberg Metadata ViewModels and Glue UI

**Files:**
- Create: `src/aws_tui/vm/glue/iceberg_vm.py`
- Modify: `src/aws_tui/vm/glue/catalog_vm.py`
- Modify: `src/aws_tui/vm/glue/page_vm.py`
- Modify: `src/aws_tui/services/glue/service.py`
- Create: `src/aws_tui/ui/widgets/glue/iceberg_view.py`
- Modify: `src/aws_tui/ui/widgets/glue/catalog_view.py`
- Test: `tests/unit/vm/glue/test_iceberg_vm.py`
- Test: `tests/unit/ui/glue/test_iceberg_view.py`
- Modify: `tests/snapshot/test_glue.py`

**Interfaces:**
- Produces: `GlueIcebergVM(inspector, hub, dispatcher)`
- Produces: metadata views `snapshots`, `history`, `manifests`, `files`, `partitions`, `refs`
- Consumes: `IcebergInspector` from Task 2

- [ ] **Step 1: Write failing on-demand loading and partial-failure tests**

```python
async def test_iceberg_tabs_appear_only_for_iceberg_table() -> None:
    catalog = make_catalog_vm(seeded_glue())
    await catalog.select_table(ICEBERG_REF)
    assert catalog.iceberg.available
    await catalog.select_table(PARQUET_REF)
    assert not catalog.iceberg.available


async def test_snapshot_view_loads_on_demand() -> None:
    inspector = RecordingInspector()
    vm = make_iceberg_vm(inspector)
    await vm.bind_table(ICEBERG_REF)
    assert inspector.calls == []
    await vm.select_view("snapshots")
    assert inspector.calls == [("snapshots", ICEBERG_REF)]


async def test_athena_denial_does_not_erase_glue_schema() -> None:
    page = make_glue_page(iceberg_error=PermissionDeniedError("athena denied"))
    await page.catalog.select_table(ICEBERG_REF)
    await page.catalog.iceberg.select_view("snapshots")
    assert page.catalog.iceberg.state is PaneState.FORBIDDEN
    assert page.catalog.selected_table.columns
```

- [ ] **Step 2: Run Glue Iceberg tests and verify failure**

Run:

```bash
uv run pytest tests/unit/vm/glue/test_iceberg_vm.py tests/unit/ui/glue/test_iceberg_view.py tests/snapshot/test_glue.py -q
```

Expected: missing Iceberg VM/view and catalog properties.

- [ ] **Step 3: Implement on-demand metadata panes**

`GlueIcebergVM.bind_table` increments generation, clears prior rows, and stores the table without issuing Athena calls. `select_view` creates or resets a pager and loads that metadata table. Retry affects only the selected metadata view.

Render a compact secondary tab strip inside the unframed table detail region. Snapshots and history show newest first; manifests/files/partitions remain paginated; refs show branch/tag type and retention fields. Selecting a snapshot enables `glue.time_travel_in_athena`, which sends `OpenAthenaTableRequest` with its integer snapshot ID.

`GlueService` constructs `IcebergInspector` with the same connection and a configured Athena workgroup selection. If no workgroup can be resolved, inject an unavailable inspector whose typed error affects only Iceberg panes.

- [ ] **Step 4: Update snapshots and run Glue tests**

Run:

```bash
uv run pytest tests/snapshot/test_glue.py --snapshot-update
uv run pytest tests/unit/vm/glue tests/unit/ui/glue tests/snapshot/test_glue.py -q
```

Expected: all tests pass and content guards show snapshot IDs, operations, manifests, files, and refs.

- [ ] **Step 5: Commit Iceberg Glue views**

```bash
git add src/aws_tui/vm/glue/iceberg_vm.py src/aws_tui/vm/glue/catalog_vm.py src/aws_tui/vm/glue/page_vm.py src/aws_tui/services/glue/service.py src/aws_tui/ui/widgets/glue/iceberg_view.py src/aws_tui/ui/widgets/glue/catalog_view.py tests/unit/vm/glue/test_iceberg_vm.py tests/unit/ui/glue/test_iceberg_view.py tests/snapshot/test_glue.py tests/snapshot/__snapshots__/test_glue
git commit -m "feat: browse Iceberg metadata from Glue"
```

### Task 5: Complete Integrated Demo and End-to-End Journeys

**Files:**
- Modify: `src/aws_tui/demo/in_memory_glue.py`
- Modify: `src/aws_tui/demo/in_memory_athena.py`
- Modify: `src/aws_tui/demo/seeds.py`
- Modify: `tests/integration/test_demo_mode.py`
- Modify: `tests/e2e/test_journeys.py`
- Modify: `tests/snapshot/test_demo_mode.py`

**Interfaces:**
- Demonstrates: profile isolation, Glue -> Athena, Athena -> Glue, Glue/Athena -> S3, Iceberg metadata, and snapshot time travel

- [ ] **Step 1: Write failing integrated demo journeys**

```python
async def test_demo_iceberg_time_travel_journey(app, pilot) -> None:
    await open_service("glue")
    await select_table("analytics", "events_iceberg")
    await select_iceberg_view("snapshots")
    await select_snapshot(42)
    await invoke("glue.time_travel_in_athena")
    await wait_for_service_setup(app.app_ctx)
    assert app.app_ctx.root_vm.content_host.current_id == "athena"
    editor = app.query_one("#athena-query-editor", TextArea)
    assert "FOR VERSION AS OF 42" in editor.text
    assert app.app_ctx.root_vm.content_host.current.query.active_ref is None


async def test_profile_switch_clears_iceberg_metadata_before_new_load(app, pilot) -> None:
    await open_demo_prod_iceberg_table()
    assert "snapshot-prod-42" in app.screen.render_str()
    await app.action_swap_source()
    assert "snapshot-prod-42" not in app.screen.render_str()
```

- [ ] **Step 2: Run integrated demo tests and verify missing seeds**

Run:

```bash
uv run pytest tests/integration/test_demo_mode.py tests/e2e/test_journeys.py tests/snapshot/test_demo_mode.py -q
```

Expected: failures report absent Iceberg demo metadata and journeys.

- [ ] **Step 3: Seed disjoint multi-profile Iceberg workflows**

Add one Iceberg table per demo profile with different schemas, snapshots, refs, manifests, files, and partitions. Seed corresponding Athena metadata-query responses and time-travel result rows. Ensure generated queries are recorded but not automatically executed. Keep all identifiers deterministic for snapshots.

- [ ] **Step 4: Update demo snapshots and run integrated tests**

Run:

```bash
uv run pytest tests/snapshot/test_demo_mode.py --snapshot-update
uv run pytest tests/integration/test_demo_mode.py tests/e2e/test_journeys.py tests/snapshot/test_demo_mode.py -q
```

Expected: all tests pass.

- [ ] **Step 5: Commit integrated demo coverage**

```bash
git add src/aws_tui/demo/in_memory_glue.py src/aws_tui/demo/in_memory_athena.py src/aws_tui/demo/seeds.py tests/integration/test_demo_mode.py tests/e2e/test_journeys.py tests/snapshot/test_demo_mode.py tests/snapshot/__snapshots__/test_demo_mode
git commit -m "test: add integrated Iceberg demo journeys"
```

### Task 6: Synchronize Documentation, Diagram, Contracts, and Release Checks

**Files:**
- Modify: `README.md`
- Modify: `docs/architecture.md`
- Modify: `docs/adding-a-service.md`
- Modify: `docs/connections.md`
- Modify: `docs/cookbook.md`
- Modify: `docs/contract-ledger.md`
- Modify: `docs/keybindings.md`
- Modify: `docs/RELEASING.md`
- Modify: `docs/manifest.yaml` if a new public guide is added
- Modify: `docs/diagrams/architecture.html`
- Regenerate: `docs/diagrams/img/architecture.png`
- Regenerate: `docs/diagrams/img/architecture.svg`
- Modify: `CHANGELOG.md`
- Test: `tests/docs/test_scaffolding.py`
- Test: `tests/docs/test_render_diagrams.py`

**Interfaces:**
- Documents: one coherent Glue/Athena/S3 Iceberg workflow and its security/cost model

- [ ] **Step 1: Add failing documentation and diagram assertions**

```python
def test_docs_publish_cross_service_iceberg_workflow(repo_root: Path) -> None:
    cookbook = (repo_root / "docs/cookbook.md").read_text()
    assert "Glue → Athena" in cookbook
    assert "FOR VERSION AS OF" in cookbook
    assert "never executes generated queries automatically" in cookbook


def test_architecture_diagram_names_new_services(repo_root: Path) -> None:
    html = (repo_root / "docs/diagrams/architecture.html").read_text()
    assert "AWS Glue" in html
    assert "Amazon Athena" in html
    assert "IcebergInspector" in html
```

- [ ] **Step 2: Run docs tests and verify missing content**

Run:

```bash
uv run pytest tests/docs/test_scaffolding.py tests/docs/test_render_diagrams.py -q
```

Expected: new assertions fail before docs and diagram are updated; Cairo-dependent tests may skip only when the dependency is unavailable.

- [ ] **Step 3: Update all public surfaces and regenerate the architecture diagram**

Use the `architecture-diagram` skill to update the diagram in landscape mode. Show the Textual views, VM subtrees, Glue/Athena service plugins, shared catalog/query models, `IcebergInspector`, AWS APIs, and cross-service messages. Keep boxes and arrows non-overlapping, avoid arrows crossing boxes, and use perpendicular broken arrows where they improve readability.

Document profile switching, catalog/job/crawler browsing, read-only SQL, result artifacts and cost, Lake Formation, S3 handoffs, Iceberg metadata limits, time travel, generated-query review, demo flows, and exact boto API operations. Update generated docs-site/wiki inputs under the repository's three-surface policy.

- [ ] **Step 4: Render docs and run the complete release verification matrix**

Run:

```bash
uv run python -m scripts.docs.render_diagrams
uv run python -m scripts.docs.check_docs
uv run pytest
uv run pytest -m integration
uv run ruff check .
uv run ruff format --check .
uv run mypy src
./scripts/check-layers.sh
uv lock --check
uv run pip-audit
uv build
uv run twine check dist/*
```

Expected: every required check passes; only documented environment-dependent skips are allowed. Visually inspect the regenerated PNG/SVG at desktop and narrow documentation widths before committing.

- [ ] **Step 5: Commit synchronized docs and diagram**

```bash
git add README.md docs CHANGELOG.md tests/docs/test_scaffolding.py tests/docs/test_render_diagrams.py
git commit -m "docs: publish Glue Athena Iceberg workflows"
```
