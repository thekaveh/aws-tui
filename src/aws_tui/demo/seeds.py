"""Opinionated demo data for the showcase mode.

One function per service. Pure data — every call returns a freshly
seeded fake. Mutating the returned fake doesn't affect subsequent
calls. See ``docs/superpowers/specs/2026-06-28-demo-mode-design.md``
for the curated content rationale.
"""

from __future__ import annotations

import zlib
from datetime import UTC, datetime, timedelta

from aws_tui.demo.in_memory_athena import InMemoryAthena
from aws_tui.demo.in_memory_emr import InMemoryEmr
from aws_tui.demo.in_memory_fs import InMemoryFS
from aws_tui.demo.in_memory_glue import InMemoryGlue
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

# Fixed "now" for deterministic timestamps. Anchored at the spec's
# write date so the seed reads as "recently active" forever — bumping
# the anchor is a one-line change.
_NOW: datetime = datetime(2026, 6, 28, 12, 0, 0, tzinfo=UTC)


# ── S3 seed data ────────────────────────────────────────────────────────────


_DEV_OBJECTS: tuple[tuple[str, int], ...] = (
    ("athena-results/dev/q-dev-succeeded.csv", 4_096),
    ("athena-results/dev/q-dev-empty.csv", 0),
    ("athena-results/dev/q-dev-access-denied.csv", 4_096),
    ("demo-dev/dev_analytics/dev_events/part-0000.parquet", 2_048),
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


def seed_s3_data(fs: InMemoryFS, *, profile: str) -> None:
    """Populate ``fs`` with the per-profile showcase objects."""
    objects = _PROFILE_OBJECTS.get(profile, _DEFAULT_OBJECTS)
    for key, size in objects:
        path = PathRef(tuple(key.split("/")))
        # The fake stores file bytes as a bytes object; we don't
        # actually need ``size`` worth of data for the demo (the UI
        # cares about the reported size, which comes from the
        # FileEntry.size property). Pad with NULs sized to the
        # smaller of (declared, 4 KiB) so memory stays bounded.
        body = b"\x00" * min(size, 4096)
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
        )
    # 2 FAILED runs on etl-pipeline-1. Details intentionally omit fake
    # S3 log URIs; the demo client returns typed no-log states instead
    # of pointing at nonexistent in-memory log files.
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
    dev.add_partition(dev_events.ref, "event_date=2026-07-24")
    dev.add_partition(dev_events.ref, "event_date=2026-07-25")
    dev.add_run("dev_daily_etl", "jr-dev-running", "RUNNING")
    dev.add_run("dev_daily_etl", "jr-dev-succeeded", "SUCCEEDED")
    dev.add_crawler("dev-ready", "READY", database_name="dev_analytics")
    dev.add_crawler("dev-running", "RUNNING", database_name="dev_analytics")

    prod = InMemoryGlue(connection_name="demo-prod", region="us-east-1")
    prod_sales = prod.add_table("prod_warehouse", "prod_sales")
    prod.add_partition(prod_sales.ref, "event_date=2026-07-25")
    prod.add_run("prod_sales_etl", "jr-prod-succeeded", "SUCCEEDED")
    prod.add_run("prod_sales_etl", "jr-prod-failed", "FAILED")
    prod.add_crawler("prod-ready", "READY", database_name="prod_warehouse")
    prod.add_crawler("prod-failed", "FAILED", database_name="prod_warehouse")

    shared = InMemoryGlue(connection_name="demo-shared", region="us-west-2")
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
    result_store: FileSystemProvider | None = None,
) -> InMemoryAthena:
    """Build one fresh, profile-local Athena demo client."""
    regions = {
        "demo-dev": "us-east-1",
        "demo-prod": "us-east-1",
        "demo-shared": "us-west-2",
    }
    athena = InMemoryAthena(
        connection_name=profile,
        region=regions.get(profile, "us-east-1"),
        result_store=result_store,
    )
    if profile == "demo-dev":
        _seed_dev_athena(athena)
    elif profile == "demo-prod":
        _seed_prod_athena(athena)
    elif profile == "demo-shared":
        athena.access_error = PermissionDeniedError("Athena access denied in demo-shared")
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
    context = QueryContext(
        "demo-dev",
        "us-east-1",
        "dev-analytics",
        "DevDataCatalog",
        "dev_events",
    )
    _add_demo_execution(
        athena,
        context,
        "q-dev-succeeded",
        QueryState.SUCCEEDED,
        output_location="s3://athena-results/dev/q-dev-succeeded.csv",
        rows=(("Ada", "42"), ("Lin", "")),
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


def _seed_prod_athena(athena: InMemoryAthena) -> None:
    athena.add_workgroup(
        "prod-reporting",
        output_location="s3://athena-results/prod/",
    )
    athena.add_catalog("prod-reporting", "ProdDataCatalog")
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
    context = QueryContext(
        "demo-prod",
        "us-east-1",
        "prod-reporting",
        "ProdDataCatalog",
        "prod_sales",
    )
    _add_demo_execution(
        athena,
        context,
        "q-prod-succeeded",
        QueryState.SUCCEEDED,
        output_location="s3://athena-results/prod/q-prod-succeeded.csv",
        rows=(("2026-07-25", "1048576"),),
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
