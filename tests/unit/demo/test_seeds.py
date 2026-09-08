"""Seed determinism + clone state-machine progression tests."""

from __future__ import annotations

import asyncio
import logging
from datetime import timedelta

import pytest

from aws_tui.demo.seeds import (
    seeded_demo_athena,
    seeded_demo_emr,
    seeded_demo_fs,
)
from aws_tui.domain.emr_serverless import JobRunState
from aws_tui.domain.filesystem import NotFoundError, PathRef


def test_seeded_demo_fs_dev_profile_has_etl_input_bucket() -> None:
    fs = seeded_demo_fs("demo-dev")
    # Every demo-dev seed includes ``etl-input/`` at the root.
    # ``seeded_demo_fs`` is sync; reading is async — so we collect
    # via the event loop.
    result = asyncio.run(fs.list(PathRef(())))
    names = {entry.name for entry in result}
    assert "etl-input" in names, f"expected etl-input bucket; got {names}"


def test_seeded_demo_fs_prod_profile_has_data_lake_bucket() -> None:
    fs = seeded_demo_fs("demo-prod")
    result = asyncio.run(fs.list(PathRef(())))
    names = {entry.name for entry in result}
    assert "data-lake" in names


def test_seeded_demo_fs_unknown_profile_returns_minimal_default() -> None:
    fs = seeded_demo_fs("unknown-profile")
    # Minimal default: at least ONE bucket so the pane isn't empty.
    result = asyncio.run(fs.list(PathRef(())))
    assert len(result) >= 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("profile", "paths", "foreign_path"),
    [
        (
            "demo-dev",
            (
                "demo-dev/dev_analytics/dev_events_iceberg/metadata/v2.metadata.json",
                "demo-dev/dev_analytics/dev_events_iceberg/metadata/demo-dev-snap-4201.avro",
                "demo-dev/dev_analytics/dev_events_iceberg/metadata/demo-dev-manifest-4202.avro",
                "demo-dev/dev_analytics/dev_events_iceberg/data/event_date=2026-07-24/"
                "dev-events-0001.parquet",
            ),
            "demo-prod/prod_warehouse/prod_sales_iceberg/metadata/v8.metadata.json",
        ),
        (
            "demo-prod",
            (
                "demo-prod/prod_warehouse/prod_sales_iceberg/metadata/v8.metadata.json",
                "demo-prod/prod_warehouse/prod_sales_iceberg/metadata/demo-prod-snap-7701.avro",
                "demo-prod/prod_warehouse/prod_sales_iceberg/metadata/demo-prod-manifest-7702.avro",
                "demo-prod/prod_warehouse/prod_sales_iceberg/data/sales_date=2026-07-25/"
                "prod-sales-0001.parquet",
            ),
            "demo-shared/shared_lake/shared_metrics_iceberg/metadata/v3.metadata.json",
        ),
        (
            "demo-shared",
            (
                "demo-shared/shared_lake/shared_metrics_iceberg/metadata/v3.metadata.json",
                "demo-shared/shared_lake/shared_metrics_iceberg/metadata/"
                "demo-shared-snap-9901.avro",
                "demo-shared/shared_lake/shared_metrics_iceberg/metadata/"
                "demo-shared-manifest-9902.avro",
                "demo-shared/shared_lake/shared_metrics_iceberg/data/team=platform/"
                "shared-metrics-0001.parquet",
            ),
            "demo-dev/dev_analytics/dev_events_iceberg/metadata/v2.metadata.json",
        ),
    ],
)
async def test_seeded_demo_fs_contains_profile_local_iceberg_artifacts(
    profile: str,
    paths: tuple[str, ...],
    foreign_path: str,
) -> None:
    fs = seeded_demo_fs(profile)

    entries = [await fs.stat(PathRef(tuple(path.split("/")))) for path in paths]

    assert all(entry.size is not None and entry.size > 0 for entry in entries)
    with pytest.raises(NotFoundError):
        await fs.stat(PathRef(tuple(foreign_path.split("/"))))


@pytest.mark.asyncio
async def test_seeded_demo_emr_has_two_applications() -> None:
    emr = seeded_demo_emr()
    apps = await emr.list_applications()
    ids = {a.id for a in apps}
    # Spec: 2 applications: etl-pipeline-1 (STARTED), ad-hoc-queries (STOPPED).
    assert "etl-pipeline-1" in ids
    assert "ad-hoc-queries" in ids


@pytest.mark.asyncio
async def test_seeded_demo_emr_has_runs_across_states() -> None:
    emr = seeded_demo_emr()
    runs, _ = await emr.list_job_runs_page("etl-pipeline-1")
    runs2, _ = await emr.list_job_runs_page("ad-hoc-queries")
    all_states = {r.state for r in (*runs, *runs2)}
    # Spec: at least SUCCESS + FAILED + RUNNING + PENDING.
    assert JobRunState.SUCCESS in all_states
    assert JobRunState.FAILED in all_states
    assert JobRunState.RUNNING in all_states
    assert JobRunState.PENDING in all_states


def test_demo_services_share_one_recent_bounded_clock() -> None:
    from aws_tui.demo.clock import DEMO_NOW

    fs = seeded_demo_fs("demo-dev")
    emr = seeded_demo_emr()
    athena = seeded_demo_athena("demo-dev")
    timestamps = [
        *fs._mtime.values(),
        *(
            run.created_at
            for application_runs in emr._runs.values()
            for run in application_runs.values()
        ),
        *(
            timestamp
            for detail in athena.query_executions.values()
            for timestamp in (detail.summary.submitted_at, detail.summary.completed_at)
            if timestamp is not None
        ),
    ]

    assert timestamps
    assert max(timestamps) <= DEMO_NOW
    assert min(timestamps) >= DEMO_NOW - timedelta(days=7)


def test_demo_call_recorders_keep_only_the_recent_bounded_window() -> None:
    from aws_tui.demo._bounded_log import MAX_RECORDED_CALLS, BoundedCallLog

    emr = seeded_demo_emr()
    athena = seeded_demo_athena("demo-dev")

    assert isinstance(emr.calls, BoundedCallLog)
    assert isinstance(athena.calls, BoundedCallLog)
    for index in range(MAX_RECORDED_CALLS + 5):
        emr.calls.append(("probe", (index,)))

    assert len(emr.calls) == MAX_RECORDED_CALLS
    assert emr.calls[0] == ("probe", (5,))


@pytest.mark.asyncio
async def test_seeded_demo_failed_runs_have_streamable_logs() -> None:
    from aws_tui.domain.emr_logs import DEFAULT_LOG_FILTER, build_run_prefix, parse_log_uri

    emr = seeded_demo_emr()
    runs, _ = await emr.list_job_runs_page("etl-pipeline-1")
    failed_ids = [r.job_run_id for r in runs if r.state is JobRunState.FAILED]
    assert failed_ids

    details = [await emr.get_job_run("etl-pipeline-1", run_id) for run_id in failed_ids]
    assert {detail.s3_monitoring_log_uri for detail in details} == {"s3://demo-emr-logs/logs"}
    detail = details[0]
    location = parse_log_uri(detail.s3_monitoring_log_uri or "")
    files = await emr.list_files(
        bucket=location.bucket,
        run_prefix=build_run_prefix(location, detail.application_id, detail.job_run_id),
    )
    assert files
    chunks = [
        chunk
        async for chunk in emr.stream(
            log_file=files[0],
            bucket=location.bucket,
            max_bytes=1024,
            filter_=DEFAULT_LOG_FILTER,
        )
    ]
    assert any("ERROR" in line for chunk in chunks for line in chunk.lines)


@pytest.mark.asyncio
async def test_clone_state_machine_walks_to_success(monkeypatch: pytest.MonkeyPatch) -> None:
    """Submitting a fresh job kicks off a SUBMITTED → SCHEDULED →
    RUNNING → SUCCESS walk. With a mocked sleep we collapse the
    walk to milliseconds for the test."""
    emr = seeded_demo_emr()

    real_sleep = asyncio.sleep

    async def _fast_sleep(secs: float) -> None:
        # Collapse all sleeps > 50 ms to a single tick so the state
        # walk completes in well under a test second.
        await real_sleep(0.001 if secs > 0.05 else 0.001)  # noqa: RUF034

    monkeypatch.setattr("aws_tui.demo.in_memory_emr.asyncio.sleep", _fast_sleep)

    new_id = await emr.start_job_run(
        "etl-pipeline-1",
        execution_role_arn="arn:aws:iam::111111111111:role/EmrJobRole",
        entry_point="s3://demo/etl.py",
        entry_point_arguments=(),
        spark_submit_parameters=None,
        name="test-clone",
    )
    # Wait for the state walk to complete.
    for _ in range(50):
        await asyncio.sleep(0.001)
        detail = await emr.get_job_run("etl-pipeline-1", new_id)
        if detail.state is JobRunState.SUCCESS:
            assert detail.updated_at > detail.created_at
            assert detail.duration_ms == int(
                (detail.updated_at - detail.created_at).total_seconds() * 1000
            )
            return
    raise AssertionError(f"state walk did not reach SUCCESS; final state was {detail.state!r}")


@pytest.mark.asyncio
async def test_successive_demo_clones_are_newest_and_strictly_ordered() -> None:
    emr = seeded_demo_emr()
    first_id = await emr.start_job_run(
        "etl-pipeline-1",
        execution_role_arn="arn:aws:iam::111111111111:role/EmrJobRole",
        entry_point="s3://demo/etl.py",
        entry_point_arguments=(),
        spark_submit_parameters=None,
        name="first-clone",
    )
    second_id = await emr.start_job_run(
        "etl-pipeline-1",
        execution_role_arn="arn:aws:iam::111111111111:role/EmrJobRole",
        entry_point="s3://demo/etl.py",
        entry_point_arguments=(),
        spark_submit_parameters=None,
        name="second-clone",
    )
    try:
        runs = await emr.list_job_runs("etl-pipeline-1")
        first = next(run for run in runs if run.job_run_id == first_id)
        second = next(run for run in runs if run.job_run_id == second_id)

        assert runs[:2] == [second, first]
        assert second.created_at > first.created_at
        assert first.created_at > runs[2].created_at
    finally:
        await emr.aclose()


@pytest.mark.asyncio
async def test_concurrent_demo_clones_each_report_five_second_duration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    emr = seeded_demo_emr()
    real_sleep = asyncio.sleep

    async def _fast_sleep(_seconds: float) -> None:
        await real_sleep(0)

    monkeypatch.setattr("aws_tui.demo.in_memory_emr.asyncio.sleep", _fast_sleep)
    first_id, second_id = await asyncio.gather(
        emr.start_job_run(
            "etl-pipeline-1",
            execution_role_arn="arn:aws:iam::111111111111:role/EmrJobRole",
            entry_point="s3://demo/etl.py",
            entry_point_arguments=(),
            spark_submit_parameters=None,
            name="first-concurrent-clone",
        ),
        emr.start_job_run(
            "etl-pipeline-1",
            execution_role_arn="arn:aws:iam::111111111111:role/EmrJobRole",
            entry_point="s3://demo/etl.py",
            entry_point_arguments=(),
            spark_submit_parameters=None,
            name="second-concurrent-clone",
        ),
    )

    for _ in range(20):
        await real_sleep(0)
        first = await emr.get_job_run("etl-pipeline-1", first_id)
        second = await emr.get_job_run("etl-pipeline-1", second_id)
        if first.state is JobRunState.SUCCESS and second.state is JobRunState.SUCCESS:
            break

    assert first.duration_ms == 5_000
    assert second.duration_ms == 5_000
    await emr.aclose()


@pytest.mark.asyncio
async def test_demo_state_walk_failure_is_logged(
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    emr = seeded_demo_emr()

    async def fail_state_walk(_application_id: str, _job_run_id: str) -> None:
        raise RuntimeError("state walk failed")

    monkeypatch.setattr(emr, "_advance_state", fail_state_walk)
    with caplog.at_level(logging.ERROR, logger="aws_tui.demo.in_memory_emr"):
        await emr.start_job_run(
            "etl-pipeline-1",
            execution_role_arn="arn:aws:iam::111111111111:role/EmrJobRole",
            entry_point="s3://demo/etl.py",
            entry_point_arguments=(),
            spark_submit_parameters=None,
            name="broken-clone",
        )
        for _ in range(5):
            await asyncio.sleep(0)
            if not emr._state_tasks:
                break

    record = next(
        record for record in caplog.records if record.message == "demo.emr.state_walk.failed"
    )
    assert record.error_type == "RuntimeError"
    await emr.aclose()


@pytest.mark.asyncio
async def test_aclose_cancels_and_drains_in_flight_state_walks() -> None:
    """``InMemoryEmr.aclose()`` cancels pending state-walk tasks so
    demo shutdown doesn't surface ``Task was destroyed but it is
    pending`` warnings."""
    emr = seeded_demo_emr()
    await emr.start_job_run(
        "etl-pipeline-1",
        execution_role_arn="arn:aws:iam::111111111111:role/EmrJobRole",
        entry_point="s3://demo/etl.py",
        entry_point_arguments=(),
        spark_submit_parameters=None,
        name="never-finishes",
    )
    assert emr._state_tasks, "expected at least one tracked task"
    await emr.aclose()
    # All tracked tasks should be cancelled, drained, and removed.
    assert not emr._state_tasks


@pytest.mark.asyncio
async def test_dispose_requests_state_walk_cancellation() -> None:
    """Synchronous callers still get cancellation even though only
    ``aclose()`` can await the cancelled tasks."""
    emr = seeded_demo_emr()
    await emr.start_job_run(
        "etl-pipeline-1",
        execution_role_arn="arn:aws:iam::111111111111:role/EmrJobRole",
        entry_point="s3://demo/etl.py",
        entry_point_arguments=(),
        spark_submit_parameters=None,
        name="dispose-cancels",
    )
    tasks = tuple(emr._state_tasks)
    assert tasks

    emr.dispose()
    await asyncio.gather(*tasks, return_exceptions=True)

    assert all(task.cancelled() for task in tasks)
    assert not emr._state_tasks


@pytest.mark.parametrize(
    ("profile", "workgroup", "database", "table"),
    [
        ("demo-dev", "dev-analytics", "dev_analytics", "dev_events_iceberg"),
        ("demo-prod", "prod-reporting", "prod_warehouse", "prod_sales_iceberg"),
        ("demo-shared", "shared-insights", "shared_lake", "shared_metrics_iceberg"),
    ],
)
async def test_glue_handoff_starter_sql_is_runnable_in_demo(
    profile: str,
    workgroup: str,
    database: str,
    table: str,
) -> None:
    """The query the Glue handoff prefills must have a demo fixture.

    ``select_starter_sql`` moved to two-part quoting, and only the
    ``FOR VERSION AS OF`` variant was re-seeded. The plain starter — what
    "Query table in Athena" actually prefills — had no fixture, so the headline
    cross-service demo filled the editor and then failed on Run with
    "Athena demo query fixture is unavailable".
    """
    from uuid import uuid4

    from aws_tui.domain.data_catalog import TableRef
    from aws_tui.domain.query import QueryContext
    from aws_tui.domain.sql_policy import select_starter_sql

    athena = seeded_demo_athena(profile)
    ref = TableRef("AwsDataCatalog", database, table, athena.connection_name, athena.region)
    context = QueryContext(
        athena.connection_name, athena.region, workgroup, "AwsDataCatalog", database
    )

    execution = await athena.start_query(
        select_starter_sql(ref), context, request_token=str(uuid4())
    )

    assert execution.execution_id
