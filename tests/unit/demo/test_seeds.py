"""Seed determinism + clone state-machine progression tests."""

from __future__ import annotations

import asyncio

import pytest

from aws_tui.demo.seeds import (
    seeded_demo_emr,
    seeded_demo_fs,
)
from aws_tui.domain.emr_serverless import JobRunState
from aws_tui.domain.filesystem import PathRef


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


@pytest.mark.asyncio
async def test_seeded_demo_failed_runs_do_not_advertise_fake_s3_logs() -> None:
    emr = seeded_demo_emr()
    runs, _ = await emr.list_job_runs_page("etl-pipeline-1")
    failed_ids = [r.job_run_id for r in runs if r.state is JobRunState.FAILED]
    assert failed_ids

    details = [await emr.get_job_run("etl-pipeline-1", run_id) for run_id in failed_ids]
    assert {detail.s3_monitoring_log_uri for detail in details} == {None}


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
            return
    raise AssertionError(f"state walk did not reach SUCCESS; final state was {detail.state!r}")


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
