"""Opinionated demo data for the showcase mode.

One function per service. Pure data — every call returns a freshly
seeded fake. Mutating the returned fake doesn't affect subsequent
calls. See ``docs/superpowers/specs/2026-06-28-demo-mode-design.md``
for the curated content rationale.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from aws_tui.demo.in_memory_emr import InMemoryEmr
from aws_tui.demo.in_memory_fs import InMemoryFS
from aws_tui.domain.emr_serverless import (
    ApplicationState,
    JobRunState,
)
from aws_tui.domain.filesystem import PathRef

# Fixed "now" for deterministic timestamps. Anchored at the spec's
# write date so the seed reads as "recently active" forever — bumping
# the anchor is a one-line change.
_NOW: datetime = datetime(2026, 6, 28, 12, 0, 0, tzinfo=UTC)


# ── S3 seed data ────────────────────────────────────────────────────────────


_DEV_OBJECTS: tuple[tuple[str, int], ...] = (
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
        fs._mtime[path] = _NOW - timedelta(hours=hash(key) % 168)


def seeded_demo_fs(profile: str) -> InMemoryFS:
    """Fresh ``InMemoryFS`` populated for the given demo profile."""
    fs = InMemoryFS()
    seed_s3_data(fs, profile=profile)
    return fs


# ── EMR seed data ───────────────────────────────────────────────────────────


_FAILED_LOG: tuple[str, ...] = (
    "26/06/27 11:42:12 INFO SparkContext: Running Spark version 3.5.0",
    "26/06/27 11:42:18 INFO ResourceProfile: Default ResourceProfile created.",
    "26/06/27 11:43:01 WARN MemoryManager: Total allocation exceeds 95% of heap",
    "26/06/27 11:43:15 ERROR Executor: Exception in task 0.0 in stage 4.0",
    "java.lang.OutOfMemoryError: Java heap space",
    "        at org.apache.spark.sql.execution.ShuffleExchangeExec.eval(ShuffleExchangeExec.scala:104)",
    "        at org.apache.spark.sql.execution.SparkPlan.eval(SparkPlan.scala:201)",
    "Caused by: java.lang.OutOfMemoryError: GC overhead limit exceeded",
    "        at java.util.HashMap.resize(HashMap.java:692)",
    "26/06/27 11:43:18 ERROR YarnScheduler: Lost executor 4 on ip-10-0-1-15.ec2.internal",
    "26/06/27 11:43:19 ERROR DAGScheduler: ResultStage 4 has failed the maximum allowable times.",
    "26/06/27 11:43:20 WARN TaskSetManager: Lost task 0.0 in stage 4.0",
    "26/06/27 11:43:21 ERROR Killed by AM",
)


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
    # 2 FAILED runs on etl-pipeline-1 with stateDetails containing
    # the bracket-laden ContainerError text. Exercises the markup-
    # escape fix from PR #96.
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
            s3_monitoring_log_uri=f"s3://demo-prod/emr-logs/{run_id}/",
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


__all__ = [
    "seed_emr_data",
    "seed_s3_data",
    "seeded_demo_emr",
    "seeded_demo_fs",
]
