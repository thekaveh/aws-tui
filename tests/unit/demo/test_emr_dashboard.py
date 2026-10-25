"""Demo dashboard reads are synthetic, fresh and leave run state untouched."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from aws_tui.demo.in_memory_emr import InMemoryEmr
from aws_tui.demo.seeds import seeded_demo_emr
from aws_tui.domain.emr_dashboard import DashboardLink
from aws_tui.domain.filesystem import NotFoundError, ValidationError


@pytest.fixture(autouse=True)
def no_demo_delay(monkeypatch):
    monkeypatch.setattr("aws_tui.demo.in_memory_emr._DEMO_LATENCY_SEC", 0)


@pytest.mark.parametrize("app_type", ["SPARK", "HIVE"])
async def test_demo_spark_hive_fresh_private_links_and_no_mutations(app_type):
    fake = seeded_demo_emr()
    if app_type == "HIVE":
        fake.add_application(app_id="hive-demo-app", name="Hive", app_type="HIVE")
        fake.add_job_run(application_id="hive-demo-app", job_run_id="hive-demo-run")
    apps = await fake.list_applications()
    app = next(app for app in apps if app.type == app_type)
    runs = await fake.list_job_runs(app.id)
    run = runs[0]
    before = dict(fake._runs[app.id])
    fake.calls.clear()
    with patch("aioboto3.Session", side_effect=AssertionError("network")):
        first = await fake.get_dashboard_for_job_run(app.id, run.job_run_id)
        second = await fake.get_dashboard_for_job_run(
            app.id, run.job_run_id, attempt=2, access_system_profile_logs=True
        )
    assert isinstance(first, DashboardLink)
    assert first.url != second.url
    assert first.url.startswith("https://")
    assert repr(first) == "DashboardLink()"
    assert list(fake.calls) == [
        ("get_dashboard_for_job_run", (app.id, run.job_run_id, None, False)),
        ("get_dashboard_for_job_run", (app.id, run.job_run_id, 2, True)),
    ]
    assert first.url not in repr(fake.calls)
    assert second.url not in repr(fake.calls)
    assert fake._runs[app.id] == before
    assert not fake._state_tasks


@pytest.mark.parametrize(("app", "run"), [("missing", "missing"), ("etl-pipeline-1", "missing")])
async def test_demo_requires_known_application_and_run(app, run):
    fake = seeded_demo_emr()
    with pytest.raises(NotFoundError):
        await fake.get_dashboard_for_job_run(app, run)


async def test_demo_rejects_unsupported_application_type():
    fake = InMemoryEmr()
    fake.add_application(app_id="unsupported", name="other", app_type="OTHER")
    fake.add_job_run(application_id="unsupported", job_run_id="run")
    with pytest.raises(ValidationError):
        await fake.get_dashboard_for_job_run("unsupported", "run")


@pytest.mark.parametrize(
    "options",
    [
        {"attempt": True},
        {"attempt": 0},
        {"attempt": -1},
        {"attempt": 1.0},
        {"attempt": "1"},
        {"access_system_profile_logs": None},
        {"access_system_profile_logs": 1},
    ],
)
async def test_demo_validates_options_before_recording_request(options):
    fake = seeded_demo_emr()
    with pytest.raises(ValidationError):
        await fake.get_dashboard_for_job_run("etl-pipeline-1", "r-etl-success-000", **options)
    assert not fake.calls


@pytest.mark.parametrize(("app", "run"), [(None, "r"), ("a", 3), ("a\n", "r"), ("", "r")])
async def test_demo_rejects_invalid_identifiers_without_logging_them(app, run):
    fake = InMemoryEmr()
    with pytest.raises(ValidationError):
        await fake.get_dashboard_for_job_run(app, run)
    assert not fake.calls
