"""Opinionated demo data for the showcase mode.

One function per service. Pure data — every call returns a freshly
seeded fake. Mutating the returned fake doesn't affect subsequent
calls. See ``docs/superpowers/specs/2026-06-28-demo-mode-design.md``
for the curated content rationale.
"""

from __future__ import annotations

import zlib
from datetime import datetime, timedelta

from aws_tui.demo.clock import DEMO_NOW
from aws_tui.demo.in_memory_athena import (
    InMemoryAthena,
    serialize_result_pages_csv,
)
from aws_tui.demo.in_memory_emr import InMemoryEmr
from aws_tui.demo.in_memory_fs import InMemoryFS
from aws_tui.demo.in_memory_glue import InMemoryGlue
from aws_tui.domain.data_catalog import Column
from aws_tui.domain.emr_logs import LogFileKind
from aws_tui.domain.emr_serverless import (
    ApplicationState,
    JobRunState,
)
from aws_tui.domain.filesystem import (
    FileSystemProvider,
    PathRef,
    PermissionDeniedError,
)
from aws_tui.domain.query import (
    AthenaQueryError,
    NamedQuery,
    PreparedStatement,
    QueryContext,
    QueryExecutionDetail,
    QueryExecutionRef,
    QueryExecutionSummary,
    QueryState,
    QueryStatistics,
    ResultColumn,
    ResultPage,
)

# Alias the shared deterministic clock so every service timeline advances
# together when the showcase data is refreshed.
_NOW: datetime = DEMO_NOW
_DEV_SUCCESS_ROWS: tuple[tuple[str | None, ...], ...] = (
    ("Ada", "42"),
    ("Lin", ""),
)
_PROD_SUCCESS_ROWS: tuple[tuple[str | None, ...], ...] = (("2026-07-25", "1048576"),)


# ── S3 seed data ────────────────────────────────────────────────────────────


_DEV_OBJECTS: tuple[tuple[str, int], ...] = (
    ("athena-results/dev/q-dev-succeeded.csv", 4_096),
    ("athena-results/dev/q-dev-empty.csv", 0),
    ("athena-results/dev/q-dev-access-denied.csv", 4_096),
    ("demo-dev/dev_analytics/dev_events/part-0000.parquet", 2_048),
    ("demo-dev/dev_analytics/dev_events_iceberg/metadata/v2.metadata.json", 3_840),
    ("demo-dev/dev_analytics/dev_events_iceberg/metadata/demo-dev-snap-4201.avro", 1_024),
    ("demo-dev/dev_analytics/dev_events_iceberg/metadata/demo-dev-snap-4202.avro", 1_280),
    (
        "demo-dev/dev_analytics/dev_events_iceberg/metadata/demo-dev-manifest-4202.avro",
        2_048,
    ),
    (
        "demo-dev/dev_analytics/dev_events_iceberg/data/event_date=2026-07-24/"
        "dev-events-0001.parquet",
        8_192,
    ),
    ("etl-input/raw/events/2026-06-25.json.gz", 2_140_000),
    ("etl-input/raw/events/2026-06-26.json.gz", 2_280_000),
    ("etl-input/raw/events/2026-06-27.json.gz", 2_310_000),
    ("etl-input/inbox/manifest.csv", 12_345),
    ("etl-input/inbox/schema.json", 4_200),
    ("etl-staging/processed/customers.parquet", 18_000_000),
    ("etl-staging/processed/orders.parquet", 41_000_000),
    ("etl-staging/processed/_SUCCESS", 0),
    ("etl-staging/dlq/2026-06-26.txt", 8_900),
    ("etl-staging/dlq/2026-06-27.txt", 11_200),
)


_PROD_OBJECTS: tuple[tuple[str, int], ...] = (
    ("athena-results/prod/q-prod-succeeded.csv", 8_192),
    ("demo-prod/prod_warehouse/prod_sales/part-0000.parquet", 4_096),
    ("demo-prod/prod_warehouse/prod_sales_iceberg/metadata/v8.metadata.json", 5_120),
    (
        "demo-prod/prod_warehouse/prod_sales_iceberg/metadata/demo-prod-snap-7701.avro",
        2_048,
    ),
    (
        "demo-prod/prod_warehouse/prod_sales_iceberg/metadata/demo-prod-snap-7702.avro",
        2_560,
    ),
    (
        "demo-prod/prod_warehouse/prod_sales_iceberg/metadata/demo-prod-manifest-7702.avro",
        2_048,
    ),
    (
        "demo-prod/prod_warehouse/prod_sales_iceberg/data/sales_date=2026-07-25/"
        "prod-sales-0001.parquet",
        16_384,
    ),
    ("data-lake/silver/customers/year=2026/month=06/part-0000.parquet", 142_000_000),
    ("data-lake/silver/customers/year=2026/month=06/part-0001.parquet", 138_000_000),
    ("data-lake/silver/customers/year=2026/month=06/_SUCCESS", 0),
    ("data-lake/silver/orders/year=2026/month=06/part-0000.parquet", 510_000_000),
    ("data-lake/silver/orders/year=2026/month=06/_SUCCESS", 0),
    ("data-lake/gold/marts/sales/snapshot.parquet", 88_000_000),
    ("data-lake/gold/marts/customers/snapshot.parquet", 24_000_000),
    ("etl-output/exports/2026-06-27/customers.csv.gz", 14_000_000),
    ("etl-output/exports/2026-06-27/orders.csv.gz", 67_000_000),
    ("etl-output/manifest.json", 2_400),
)


_SHARED_OBJECTS: tuple[tuple[str, int], ...] = (
    ("demo-shared/shared_lake/shared_metrics_iceberg/metadata/v3.metadata.json", 4_096),
    (
        "demo-shared/shared_lake/shared_metrics_iceberg/metadata/demo-shared-snap-9901.avro",
        1_536,
    ),
    (
        "demo-shared/shared_lake/shared_metrics_iceberg/metadata/demo-shared-snap-9902.avro",
        1_792,
    ),
    (
        "demo-shared/shared_lake/shared_metrics_iceberg/metadata/demo-shared-manifest-9902.avro",
        2_048,
    ),
    (
        "demo-shared/shared_lake/shared_metrics_iceberg/data/team=platform/"
        "shared-metrics-0001.parquet",
        12_288,
    ),
    ("assets/logo.png", 84_000),
    ("assets/report-2026Q2.pdf", 4_200_000),
    ("assets/style-guide.md", 18_400),
    ("archive/backup-01.tar.gz", 1_280_000_000),
    ("archive/backup-02.tar.gz", 1_290_000_000),
    ("archive/backup-03.tar.gz", 1_300_000_000),
)


_DEFAULT_OBJECTS: tuple[tuple[str, int], ...] = (("demo-bucket/welcome.txt", 64),)


_PROFILE_OBJECTS: dict[str, tuple[tuple[str, int], ...]] = {
    "demo-dev": _DEV_OBJECTS,
    "demo-prod": _PROD_OBJECTS,
    "demo-shared": _SHARED_OBJECTS,
}

_RESULT_COLUMNS = (
    ResultColumn("value", "varchar", "NULLABLE"),
    ResultColumn("metric", "varchar", "NULLABLE"),
)
_RESULT_ARTIFACTS: dict[str, dict[str, bytes]] = {
    "demo-dev": {
        "athena-results/dev/q-dev-succeeded.csv": serialize_result_pages_csv(
            (
                ResultPage(
                    _RESULT_COLUMNS,
                    _DEV_SUCCESS_ROWS,
                    None,
                ),
            )
        ),
        "athena-results/dev/q-dev-empty.csv": serialize_result_pages_csv(
            (ResultPage(_RESULT_COLUMNS, (), None),)
        ),
        "athena-results/dev/q-dev-access-denied.csv": serialize_result_pages_csv(
            (ResultPage(_RESULT_COLUMNS, (), None),)
        ),
    },
    "demo-prod": {
        "athena-results/prod/q-prod-succeeded.csv": serialize_result_pages_csv(
            (
                ResultPage(
                    _RESULT_COLUMNS,
                    _PROD_SUCCESS_ROWS,
                    None,
                ),
            )
        ),
    },
}


def seed_s3_data(fs: InMemoryFS, *, profile: str) -> None:
    """Populate ``fs`` with the per-profile showcase objects."""
    fs._mtime[PathRef(())] = _NOW
    objects = _PROFILE_OBJECTS.get(profile, _DEFAULT_OBJECTS)
    result_artifacts = _RESULT_ARTIFACTS.get(profile, {})
    for key, size in objects:
        path = PathRef(tuple(key.split("/")))
        # The fake stores file bytes as a bytes object; we don't
        # actually need ``size`` worth of data for the demo (the UI
        # cares about the reported size, which comes from the
        # FileEntry.size property). Pad with NULs sized to the
        # smaller of (declared, 4 KiB) so memory stays bounded.
        body = result_artifacts.get(key, b"\x00" * min(size, 4096))
        # Ensure parent dirs exist.
        for i in range(1, len(path.segments)):
            ancestor = PathRef(path.segments[:i])
            if ancestor not in fs._tree:
                fs._tree[ancestor] = None
                fs._mtime[ancestor] = _NOW
        fs._tree[path] = body
        fs._mtime[path] = _NOW - timedelta(hours=zlib.crc32(key.encode()) % 168)


def seeded_demo_fs(profile: str) -> InMemoryFS:
    """Fresh ``InMemoryFS`` populated for the given demo profile."""
    fs = InMemoryFS()
    seed_s3_data(fs, profile=profile)
    return fs


# ── EMR seed data ───────────────────────────────────────────────────────────


def seed_emr_data(emr: InMemoryEmr) -> None:
    """Populate ``emr`` with 2 apps + 10 runs spanning 4 states."""
    emr.add_application(
        app_id="etl-pipeline-1",
        name="etl-pipeline-1",
        state=ApplicationState.STARTED,
    )
    emr.add_application(
        app_id="ad-hoc-queries",
        name="ad-hoc-queries",
        state=ApplicationState.STOPPED,
    )
    # 4 SUCCESS runs on etl-pipeline-1.
    for i, days_ago in enumerate([6, 5, 4, 1]):
        run_id = f"r-etl-success-{i:03d}"
        emr.add_job_run(
            application_id="etl-pipeline-1",
            job_run_id=run_id,
            name=f"nightly-2026-06-{22 + i:02d}",
            state=JobRunState.SUCCESS,
            created_at=_NOW - timedelta(days=days_ago),
        )
        emr.add_job_run_detail(
            application_id="etl-pipeline-1",
            job_run_id=run_id,
            entry_point="s3://demo-prod/etl/scripts/nightly.py",
            s3_monitoring_log_uri="s3://demo-emr-logs/logs",
        )
        emr.add_log_file(
            application_id="etl-pipeline-1",
            job_run_id=run_id,
            kind=LogFileKind.DRIVER_STDERR,
            lines=("INFO nightly job started", "INFO nightly job completed"),
        )
    # 2 SUCCESS runs on ad-hoc-queries.
    for i, days_ago in enumerate([3, 2]):
        run_id = f"r-adhoc-success-{i:03d}"
        emr.add_job_run(
            application_id="ad-hoc-queries",
            job_run_id=run_id,
            name=f"ad-hoc-{i:02d}",
            state=JobRunState.SUCCESS,
            created_at=_NOW - timedelta(days=days_ago),
        )
        emr.add_job_run_detail(
            application_id="ad-hoc-queries",
            job_run_id=run_id,
            entry_point="s3://demo-prod/etl/scripts/ad-hoc.py",
            s3_monitoring_log_uri="s3://demo-emr-logs/logs",
        )
        emr.add_log_file(
            application_id="ad-hoc-queries",
            job_run_id=run_id,
            kind=LogFileKind.DRIVER_STDOUT,
            lines=("INFO query accepted", "INFO query completed"),
        )
    # 2 FAILED runs on etl-pipeline-1 with useful filtered log output.
    for i, days_ago in enumerate([3, 1]):
        run_id = f"r-etl-failed-{i:03d}"
        emr.add_job_run(
            application_id="etl-pipeline-1",
            job_run_id=run_id,
            name=f"nightly-2026-06-{25 + i:02d}",
            state=JobRunState.FAILED,
            created_at=_NOW - timedelta(days=days_ago),
        )
        emr.add_job_run_detail(
            application_id="etl-pipeline-1",
            job_run_id=run_id,
            entry_point="s3://demo-prod/etl/scripts/nightly.py",
            s3_monitoring_log_uri="s3://demo-emr-logs/logs",
        )
        emr.add_log_file(
            application_id="etl-pipeline-1",
            job_run_id=run_id,
            kind=LogFileKind.DRIVER_STDERR,
            lines=(
                "ERROR nightly transform failed",
                "Caused by: demo input schema mismatch",
            ),
        )
    # 1 RUNNING run on etl-pipeline-1.
    emr.add_job_run(
        application_id="etl-pipeline-1",
        job_run_id="r-etl-running-000",
        name="adhoc-now",
        state=JobRunState.RUNNING,
        created_at=_NOW - timedelta(minutes=5),
    )
    emr.add_job_run_detail(
        application_id="etl-pipeline-1",
        job_run_id="r-etl-running-000",
        entry_point="s3://demo-prod/etl/scripts/adhoc.py",
    )
    # 1 PENDING run on ad-hoc-queries.
    emr.add_job_run(
        application_id="ad-hoc-queries",
        job_run_id="r-adhoc-pending-000",
        name="queued-now",
        state=JobRunState.PENDING,
        created_at=_NOW - timedelta(seconds=30),
    )
    emr.add_job_run_detail(
        application_id="ad-hoc-queries",
        job_run_id="r-adhoc-pending-000",
        entry_point="s3://demo-prod/etl/scripts/adhoc.py",
    )


def seeded_demo_emr() -> InMemoryEmr:
    """Fresh ``InMemoryEmr`` pre-seeded with showcase data."""
    emr = InMemoryEmr()
    seed_emr_data(emr)
    return emr


def seeded_demo_glue() -> dict[str, InMemoryGlue]:
    """Build disjoint Glue catalogs for each AWS demo connection."""
    dev = InMemoryGlue(connection_name="demo-dev", region="us-east-1")
    dev_events = dev.add_table("dev_analytics", "dev_events")
    dev.add_iceberg_table(
        "dev_analytics",
        "dev_events_iceberg",
        columns=(
            Column("event_id", "string", "Event identifier", False),
            Column("event_name", "string", "Event name", False),
            Column("event_ts", "timestamp", "Event timestamp", False),
            Column("event_date", "date", "Event date", True),
        ),
        partition_columns=("event_date",),
        metadata_version=2,
    )
    dev.add_partition(dev_events.ref, "event_date=2026-07-24")
    dev.add_partition(dev_events.ref, "event_date=2026-07-25")
    dev.add_run("dev_daily_etl", "jr-dev-running", "RUNNING")
    dev.add_run("dev_daily_etl", "jr-dev-succeeded", "SUCCEEDED")
    dev.add_crawler("dev-ready", "READY", database_name="dev_analytics")
    dev.add_crawler("dev-running", "RUNNING", database_name="dev_analytics")

    prod = InMemoryGlue(connection_name="demo-prod", region="us-east-1")
    prod_sales = prod.add_table("prod_warehouse", "prod_sales")
    prod.add_iceberg_table(
        "prod_warehouse",
        "prod_sales_iceberg",
        columns=(
            Column("order_id", "bigint", "Order identifier", False),
            Column("gross_total", "decimal(12,2)", "Gross sales", False),
            Column("region", "string", "Sales region", False),
            Column("sales_date", "date", "Sales date", True),
        ),
        partition_columns=("sales_date",),
        metadata_version=8,
    )
    prod.add_partition(prod_sales.ref, "event_date=2026-07-25")
    prod.add_run("prod_sales_etl", "jr-prod-succeeded", "SUCCEEDED")
    prod.add_run("prod_sales_etl", "jr-prod-failed", "FAILED")
    prod.add_crawler("prod-ready", "READY", database_name="prod_warehouse")
    prod.add_crawler("prod-failed", "FAILED", database_name="prod_warehouse")

    shared = InMemoryGlue(connection_name="demo-shared", region="us-west-2")
    shared.add_iceberg_table(
        "shared_lake",
        "shared_metrics_iceberg",
        columns=(
            Column("metric_name", "string", "Metric name", False),
            Column("metric_value", "double", "Metric value", False),
            Column("observed_at", "timestamp", "Observation time", False),
            Column("team", "string", "Owning team", True),
        ),
        partition_columns=("team",),
        metadata_version=3,
    )
    shared.crawlers_error = PermissionDeniedError("glue:GetCrawlers denied in demo-shared")

    return {
        "demo-dev": dev,
        "demo-prod": prod,
        "demo-shared": shared,
    }


# ── Athena seed data ────────────────────────────────────────────────────────


def seeded_demo_athena(
    profile: str,
    *,
    connection_name: str | None = None,
    region: str | None = None,
    result_store: FileSystemProvider | None = None,
) -> InMemoryAthena:
    """Build one fresh, profile-local Athena demo client."""
    regions = {
        "demo-dev": "us-east-1",
        "demo-prod": "us-east-1",
        "demo-shared": "us-west-2",
    }
    athena = InMemoryAthena(
        connection_name=connection_name or profile,
        region=region or regions.get(profile, "us-east-1"),
        storage_namespace=profile,
        result_store=result_store,
    )
    if profile == "demo-dev":
        _seed_dev_athena(athena)
    elif profile == "demo-prod":
        _seed_prod_athena(athena)
    elif profile == "demo-shared":
        _seed_shared_athena(athena)
    return athena


def _seed_dev_athena(athena: InMemoryAthena) -> None:
    athena.add_workgroup(
        "dev-analytics",
        output_location="s3://athena-results/dev/",
    )
    athena.add_workgroup(
        "dev-empty",
        output_location="s3://athena-results/dev-empty/",
    )
    athena.add_catalog("dev-analytics", "DevDataCatalog")
    athena.add_catalog("dev-analytics", "AwsDataCatalog")
    athena.add_database(
        "dev-analytics",
        "DevDataCatalog",
        "dev_events",
    )
    athena.add_table(
        "dev-analytics",
        "DevDataCatalog",
        "dev_events",
        "events",
    )
    athena.add_database(
        "dev-analytics",
        "AwsDataCatalog",
        "dev_analytics",
    )
    athena.add_table(
        "dev-analytics",
        "AwsDataCatalog",
        "dev_analytics",
        "dev_events_iceberg",
    )
    context = QueryContext(
        athena.connection_name,
        athena.region,
        "dev-analytics",
        "DevDataCatalog",
        "dev_events",
    )
    _seed_select_one(athena, context)
    _add_demo_execution(
        athena,
        context,
        "q-dev-succeeded",
        QueryState.SUCCEEDED,
        output_location="s3://athena-results/dev/q-dev-succeeded.csv",
        rows=_DEV_SUCCESS_ROWS,
        reused=True,
    )
    _add_demo_execution(
        athena,
        context,
        "q-dev-running",
        QueryState.RUNNING,
        output_location="s3://athena-results/dev/running/",
    )
    _add_demo_execution(
        athena,
        context,
        "q-dev-failed",
        QueryState.FAILED,
        output_location="s3://athena-results/dev/failed/",
        error=AthenaQueryError(2, 1006, False, "Demo query validation failed"),
    )
    _add_demo_execution(
        athena,
        context,
        "q-dev-empty",
        QueryState.SUCCEEDED,
        output_location="s3://athena-results/dev/q-dev-empty.csv",
        rows=(),
    )
    _add_demo_execution(
        athena,
        context,
        "q-dev-access-denied",
        QueryState.SUCCEEDED,
        output_location="s3://athena-results/dev/q-dev-access-denied.csv",
        result_error=PermissionDeniedError("result access denied"),
    )
    _add_demo_execution(
        athena,
        context,
        "q-dev-missing-output",
        QueryState.SUCCEEDED,
        output_location=None,
        rows=(("missing", None),),
    )
    athena.add_named_query(
        NamedQuery(
            "nq-dev-events",
            "Recent dev events",
            "Bounded demo query",
            "dev_events",
            "SELECT * FROM events LIMIT 25",
            "dev-analytics",
        )
    )
    athena.add_prepared_statement(
        PreparedStatement(
            "ps-dev-event-by-id",
            "SELECT * FROM events WHERE event_id = ?",
            "dev-analytics",
            "Find one development event",
            _NOW,
        )
    )
    _seed_iceberg_queries(
        athena,
        workgroup="dev-analytics",
        database="dev_analytics",
        table="dev_events_iceberg",
        snapshot_ids=(4202, 4201),
        ref_name="dev-main",
        partition_name="event_date",
        partition_value="2026-07-24",
        data_file="dev-events-0001.parquet",
        time_travel_rows=(
            ("2026-07-24T12:00:00Z", "dev-checkout", "17"),
            ("2026-07-24T12:05:00Z", "dev-search", "9"),
        ),
    )


def _seed_prod_athena(athena: InMemoryAthena) -> None:
    athena.add_workgroup(
        "prod-reporting",
        output_location="s3://athena-results/prod/",
    )
    athena.add_catalog("prod-reporting", "ProdDataCatalog")
    athena.add_catalog("prod-reporting", "AwsDataCatalog")
    athena.add_database(
        "prod-reporting",
        "ProdDataCatalog",
        "prod_sales",
    )
    athena.add_table(
        "prod-reporting",
        "ProdDataCatalog",
        "prod_sales",
        "daily_sales",
    )
    athena.add_database(
        "prod-reporting",
        "AwsDataCatalog",
        "prod_warehouse",
    )
    athena.add_table(
        "prod-reporting",
        "AwsDataCatalog",
        "prod_warehouse",
        "prod_sales_iceberg",
    )
    context = QueryContext(
        athena.connection_name,
        athena.region,
        "prod-reporting",
        "ProdDataCatalog",
        "prod_sales",
    )
    _seed_select_one(athena, context)
    _add_demo_execution(
        athena,
        context,
        "q-prod-succeeded",
        QueryState.SUCCEEDED,
        output_location="s3://athena-results/prod/q-prod-succeeded.csv",
        rows=_PROD_SUCCESS_ROWS,
    )
    _add_demo_execution(
        athena,
        context,
        "q-prod-cancelled",
        QueryState.CANCELLED,
        output_location="s3://athena-results/prod/cancelled/",
    )
    athena.add_named_query(
        NamedQuery(
            "nq-prod-daily-sales",
            "Daily sales",
            "Production daily summary",
            "prod_sales",
            "SELECT * FROM daily_sales LIMIT 25",
            "prod-reporting",
        )
    )
    athena.add_prepared_statement(
        PreparedStatement(
            "ps-prod-sales-by-day",
            "SELECT * FROM daily_sales WHERE sales_date = ?",
            "prod-reporting",
            "Production sales for one day",
            _NOW,
        )
    )
    _seed_iceberg_queries(
        athena,
        workgroup="prod-reporting",
        database="prod_warehouse",
        table="prod_sales_iceberg",
        snapshot_ids=(7702, 7701),
        ref_name="prod-main",
        partition_name="sales_date",
        partition_value="2026-07-25",
        data_file="prod-sales-0001.parquet",
        time_travel_rows=(
            ("2026-07-25", "us-east-1", "1048576.25"),
            ("2026-07-25", "eu-west-1", "524288.50"),
        ),
    )


def _seed_shared_athena(athena: InMemoryAthena) -> None:
    athena.add_workgroup(
        "shared-retired",
        output_location="s3://athena-results/shared-retired/",
        state="DISABLED",
    )
    athena.add_workgroup(
        "shared-insights",
        output_location="s3://athena-results/shared/",
    )
    athena.add_catalog("shared-insights", "AwsDataCatalog")
    athena.add_database(
        "shared-insights",
        "AwsDataCatalog",
        "shared_lake",
    )
    athena.add_table(
        "shared-insights",
        "AwsDataCatalog",
        "shared_lake",
        "shared_metrics_iceberg",
    )
    _seed_select_one(
        athena,
        QueryContext(
            athena.connection_name,
            athena.region,
            "shared-insights",
            "AwsDataCatalog",
            "shared_lake",
        ),
    )
    _seed_iceberg_queries(
        athena,
        workgroup="shared-insights",
        database="shared_lake",
        table="shared_metrics_iceberg",
        snapshot_ids=(9902, 9901),
        ref_name="shared-main",
        partition_name="team",
        partition_value="platform",
        data_file="shared-metrics-0001.parquet",
        time_travel_rows=(
            ("platform", "query_latency_ms", "84.0"),
            ("platform", "freshness_minutes", "6.0"),
        ),
    )


def _seed_select_one(
    athena: InMemoryAthena,
    context: QueryContext,
) -> None:
    athena.add_query_result(
        "SELECT 1",
        context,
        columns=(ResultColumn("_col0", "integer", "NULLABLE"),),
        rows=(("1",),),
    )


def _seed_iceberg_queries(
    athena: InMemoryAthena,
    *,
    workgroup: str,
    database: str,
    table: str,
    snapshot_ids: tuple[int, int],
    ref_name: str,
    partition_name: str,
    partition_value: str,
    data_file: str,
    time_travel_rows: tuple[tuple[str, str, str], ...],
) -> None:
    newest, older = snapshot_ids
    context = QueryContext(
        athena.connection_name,
        athena.region,
        workgroup,
        "AwsDataCatalog",
        database,
    )
    root = f"s3://{athena.storage_namespace}/{database}/{table}"
    metadata = f'"AwsDataCatalog"."{database}"."{table}'
    nullable = "NULLABLE"

    def add(
        sql: str,
        columns: tuple[tuple[str, str], ...],
        rows: tuple[tuple[str | None, ...], ...],
    ) -> None:
        athena.add_query_result(
            sql,
            context,
            columns=tuple(ResultColumn(name, type_name, nullable) for name, type_name in columns),
            rows=rows,
        )

    add(
        (
            "SELECT committed_at, snapshot_id, parent_id, operation, manifest_list, summary "
            f'FROM {metadata}$snapshots" ORDER BY committed_at DESC LIMIT 100'
        ),
        (
            ("committed_at", "timestamp"),
            ("snapshot_id", "bigint"),
            ("parent_id", "bigint"),
            ("operation", "varchar"),
            ("manifest_list", "varchar"),
            ("summary", "varchar"),
        ),
        (
            (
                "2026-07-25T12:00:00+00:00",
                str(newest),
                str(older),
                "append",
                f"{root}/metadata/{athena.storage_namespace}-snap-{newest}.avro",
                "{added-records=17,total-records=26}",
            ),
            (
                "2026-07-24T12:00:00+00:00",
                str(older),
                None,
                "append",
                f"{root}/metadata/{athena.storage_namespace}-snap-{older}.avro",
                "{added-records=9,total-records=9}",
            ),
        ),
    )
    add(
        (
            "SELECT made_current_at, snapshot_id, parent_id, is_current_ancestor "
            f'FROM {metadata}$history" ORDER BY made_current_at DESC LIMIT 100'
        ),
        (
            ("made_current_at", "timestamp"),
            ("snapshot_id", "bigint"),
            ("parent_id", "bigint"),
            ("is_current_ancestor", "boolean"),
        ),
        (
            ("2026-07-25T12:00:00+00:00", str(newest), str(older), "true"),
            ("2026-07-24T12:00:00+00:00", str(older), None, "true"),
        ),
    )
    add(
        (
            "SELECT path, length, partition_spec_id, added_snapshot_id, "
            "added_data_files_count, existing_data_files_count, "
            f'deleted_data_files_count, partition_summaries FROM {metadata}$manifests" '
            "ORDER BY added_snapshot_id DESC, path LIMIT 500"
        ),
        (
            ("path", "varchar"),
            ("length", "bigint"),
            ("partition_spec_id", "integer"),
            ("added_snapshot_id", "bigint"),
            ("added_data_files_count", "integer"),
            ("existing_data_files_count", "integer"),
            ("deleted_data_files_count", "integer"),
            ("partition_summaries", "varchar"),
        ),
        (
            (
                f"{root}/metadata/{athena.storage_namespace}-manifest-{newest}.avro",
                "2048",
                "0",
                str(newest),
                "1",
                "0",
                "0",
                f"{{{partition_name}={partition_value}}}",
            ),
        ),
    )
    add(
        (
            "SELECT content, file_path, file_format, spec_id, partition, record_count, "
            f'file_size_in_bytes, equality_ids, sort_order_id FROM {metadata}$files" '
            "ORDER BY file_path LIMIT 1000"
        ),
        (
            ("content", "integer"),
            ("file_path", "varchar"),
            ("file_format", "varchar"),
            ("spec_id", "integer"),
            ("partition", "varchar"),
            ("record_count", "bigint"),
            ("file_size_in_bytes", "bigint"),
            ("equality_ids", "varchar"),
            ("sort_order_id", "integer"),
        ),
        (
            (
                "0",
                f"{root}/data/{partition_name}={partition_value}/{data_file}",
                "PARQUET",
                "0",
                f"{{{partition_name}={partition_value}}}",
                "26",
                "8192",
                None,
                "0",
            ),
        ),
    )
    add(
        f'SELECT * FROM {metadata}$partitions" LIMIT 500',
        (
            ("partition", f"row({partition_name} varchar)"),
            ("spec_id", "integer"),
            ("record_count", "bigint"),
            ("file_count", "integer"),
            ("total_data_file_size_in_bytes", "bigint"),
            ("position_delete_record_count", "bigint"),
            ("position_delete_file_count", "integer"),
            ("equality_delete_record_count", "bigint"),
            ("equality_delete_file_count", "integer"),
            ("last_updated_at", "timestamp"),
            ("last_updated_snapshot_id", "bigint"),
        ),
        (
            (
                f"{{{partition_name}={partition_value}}}",
                "0",
                "26",
                "1",
                "8192",
                "0",
                "0",
                "0",
                "0",
                "2026-07-25T12:00:00+00:00",
                str(newest),
            ),
        ),
    )
    add(
        (
            "SELECT name, type, snapshot_id, max_reference_age_in_ms, "
            f'min_snapshots_to_keep, max_snapshot_age_in_ms FROM {metadata}$refs" '
            "ORDER BY name LIMIT 100"
        ),
        (
            ("name", "varchar"),
            ("type", "varchar"),
            ("snapshot_id", "bigint"),
            ("max_reference_age_in_ms", "bigint"),
            ("min_snapshots_to_keep", "integer"),
            ("max_snapshot_age_in_ms", "bigint"),
        ),
        ((ref_name, "BRANCH", str(newest), None, "2", "604800000"),),
    )
    add(
        (
            f'SELECT * FROM "AwsDataCatalog"."{database}"."{table}" '
            f"FOR VERSION AS OF {older} LIMIT 100"
        ),
        (
            ("dimension", "varchar"),
            ("name", "varchar"),
            ("value", "varchar"),
        ),
        time_travel_rows,
    )


def _add_demo_execution(
    athena: InMemoryAthena,
    context: QueryContext,
    execution_id: str,
    state: QueryState,
    *,
    output_location: str | None,
    rows: tuple[tuple[str | None, ...], ...] = (),
    error: AthenaQueryError | None = None,
    result_error: Exception | None = None,
    reused: bool = False,
) -> None:
    ref = QueryExecutionRef(
        execution_id,
        context.connection_name,
        context.region,
        context.workgroup,
    )
    detail = QueryExecutionDetail(
        QueryExecutionSummary(
            ref,
            state,
            _NOW - timedelta(minutes=10),
            _NOW - timedelta(minutes=9) if state is not QueryState.RUNNING else None,
            "DML",
        ),
        error.message if error is not None else None,
        context,
        QueryStatistics(420, 12, 24, 8, 2_097_152, reused),
        output_location,
        "Athena engine version 3",
        error,
    )
    result_pages = (
        ResultPage(
            (
                ResultColumn("value", "varchar", "NULLABLE"),
                ResultColumn("metric", "varchar", "NULLABLE"),
            ),
            rows,
            None,
        ),
    )
    athena.add_query_execution(
        detail,
        result_pages=result_pages,
        result_error=result_error,
    )


__all__ = [
    "seed_emr_data",
    "seed_s3_data",
    "seeded_demo_athena",
    "seeded_demo_emr",
    "seeded_demo_fs",
    "seeded_demo_glue",
]
