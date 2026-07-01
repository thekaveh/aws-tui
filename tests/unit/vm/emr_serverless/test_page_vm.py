"""EmrServerlessPageVM tests — orchestration of three child VMs."""

from __future__ import annotations

import pytest
from vmx import NULL_DISPATCHER, MessageHub
from vmx.messages.protocols import Message

from aws_tui.domain.emr_serverless import EMR_BOTO_CONFIG, ApplicationState, JobRunState
from aws_tui.infra.connection_resolver import Connection
from aws_tui.vm.emr_serverless.job_run_logs_vm import LogsState
from aws_tui.vm.emr_serverless.page_vm import EmrServerlessPageVM
from tests.unit.domain._in_memory_emr import _InMemoryEmr


def _make() -> tuple[EmrServerlessPageVM, _InMemoryEmr]:
    fake = _InMemoryEmr()
    hub: MessageHub[Message] = MessageHub()
    page = EmrServerlessPageVM(
        client=fake,
        hub=hub,
        dispatcher=NULL_DISPATCHER,
        connection=Connection(
            name="dev", kind="aws", region="us-east-1", source="config", profile="dev"
        ),
    )
    page.construct()
    return page, fake


def test_page_vm_threads_emr_boto_config_into_logs_client() -> None:
    page, _ = _make()
    assert page.job_run_logs._client.boto_config is EMR_BOTO_CONFIG  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_setup_loads_applications_and_auto_selects_first() -> None:
    page, fake = _make()
    fake.add_application(app_id="a1", name="etl")
    fake.add_application(app_id="a2", name="ad-hoc")
    await page.setup()
    assert {a.id for a in page.applications.applications} == {"a1", "a2"}
    # Auto-select the first app so the LEFT pane has something to load.
    assert page.applications.selected_id in {"a1", "a2"}


@pytest.mark.asyncio
async def test_select_application_propagates_to_job_runs() -> None:
    page, fake = _make()
    fake.add_application(app_id="a1", name="etl")
    fake.add_application(app_id="a2", name="ad-hoc")
    fake.add_job_run(application_id="a2", job_run_id="r9", state=JobRunState.RUNNING)
    fake.add_job_run_detail(application_id="a2", job_run_id="r9", entry_point="s3://b/y.py")
    await page.setup()
    await page.select_application("a2")
    assert page.applications.selected_id == "a2"
    assert page.job_runs.application_id == "a2"
    assert {r.job_run_id for r in page.job_runs.runs} == {"r9"}


@pytest.mark.asyncio
async def test_select_job_run_propagates_to_detail() -> None:
    page, fake = _make()
    fake.add_application(app_id="a1", name="etl")
    fake.add_job_run(application_id="a1", job_run_id="r1")
    fake.add_job_run_detail(application_id="a1", job_run_id="r1", entry_point="s3://b/x.py")
    await page.setup()
    # set_application is implicit in setup() if there's only one app.
    await page.select_job_run("r1")
    assert page.job_run_detail.detail is not None
    assert page.job_run_detail.detail.entry_point == "s3://b/x.py"


@pytest.mark.asyncio
async def test_dispose_cascades_to_children() -> None:
    """``dispose()`` must reach the four child VMs (applications /
    job_runs / job_run_detail / job_run_logs) — the prior body only
    asserted "didn't raise on double-dispose", which would silently
    pass a regression that disposed ``_inner`` while skipping the
    children. ``ApplicationsVM`` / ``JobRunsVM`` expose ``status``
    proxies; ``JobRunDetailVM`` / ``JobRunLogsVM`` do not, so we
    read their inner status via test-only dunder access — the
    cascade is what matters, not the public surface."""
    from vmx.lifecycle.status import ConstructionStatus

    page, _ = _make()
    assert page.applications.status is ConstructionStatus.CONSTRUCTED
    assert page.job_runs.status is ConstructionStatus.CONSTRUCTED
    page.dispose()
    assert page.applications.status is ConstructionStatus.DISPOSED
    assert page.job_runs.status is ConstructionStatus.DISPOSED
    assert page.job_run_detail._inner.status is ConstructionStatus.DISPOSED  # type: ignore[attr-defined]
    assert page.job_run_logs._inner.status is ConstructionStatus.DISPOSED  # type: ignore[attr-defined]
    page.dispose()  # idempotent


@pytest.mark.asyncio
async def test_cycle_application_wraps_at_end_and_cascades() -> None:
    """Pin Shift+S behaviour: ``cycle_application(1)`` selects the
    next app and wraps at the end. The full ``select_application``
    cascade runs so JobRuns + Detail panes refresh in lockstep —
    user feedback: "shift + s … doesn't result in an actual app
    switching" (pre-fix it just opened the picker).
    """
    page, fake = _make()
    fake.add_application(app_id="a1", name="etl")
    fake.add_application(app_id="a2", name="ad-hoc")
    fake.add_application(app_id="a3", name="ml")
    fake.add_job_run(application_id="a2", job_run_id="r-1", state=JobRunState.SUCCESS)
    fake.add_job_run_detail(application_id="a2", job_run_id="r-1")
    await page.setup()
    # Cycle source-of-truth is the SORTED list (STARTED first, then
    # alphabetical); the dropdown listing and Shift+S ring stay in
    # lockstep.
    apps_in_order = [a.id for a in page.applications.sorted_applications]
    # ``setup()`` auto-selected the first app.
    first = page.applications.selected_id
    assert first is not None
    # Cycle forward: should land on the next id in the picker's list.
    await page.cycle_application(1)
    current_idx = apps_in_order.index(first)
    expected_next = apps_in_order[(current_idx + 1) % len(apps_in_order)]
    assert page.applications.selected_id == expected_next
    # Cascade ran: JobRunsVM is re-scoped to the new app id.
    assert page.job_runs.application_id == expected_next
    # Wrap around — keep cycling until we land back on ``first``.
    for _ in range(len(apps_in_order)):
        await page.cycle_application(1)
    assert page.applications.selected_id == expected_next, (
        "After ``len(apps)`` more cycles starting from ``expected_next``, "
        "we should be back at ``expected_next`` (one full lap around the ring)."
    )


@pytest.mark.asyncio
async def test_cycle_application_walks_the_picker_sort_not_raw_order() -> None:
    """User feedback: "make sure this newly ordered list of
    applications is the source of truth through which switch app
    command cycles".

    Seeds three apps in a deliberately-wrong raw order — STOPPED
    first, TERMINATED second, STARTED last. After ``setup()``
    auto-selects the first app of the sorted list (the STARTED
    one), each ``cycle_application(1)`` MUST visit the next entry
    in :attr:`ApplicationsVM.sorted_applications` — STARTED →
    STOPPED → TERMINATED → STARTED. The raw-order ring would
    visit STOPPED → TERMINATED → STARTED which is the bug we're
    pinning against.
    """
    page, fake = _make()
    fake.add_application(app_id="dead", name="killed", state=ApplicationState.TERMINATED)
    fake.add_application(app_id="off", name="snoozy", state=ApplicationState.STOPPED)
    fake.add_application(app_id="live", name="alpha", state=ApplicationState.STARTED)
    await page.setup()
    sorted_ids = [a.id for a in page.applications.sorted_applications]
    assert sorted_ids == ["live", "off", "dead"], (
        "Sanity: STARTED first, then STOPPED, then TERMINATED."
    )
    # setup() auto-selects the FIRST entry — which is the sorted
    # first, not the raw first. That's "live" (STARTED).
    assert page.applications.selected_id == "live"
    await page.cycle_application(1)
    assert page.applications.selected_id == "off"
    await page.cycle_application(1)
    assert page.applications.selected_id == "dead"
    await page.cycle_application(1)
    assert page.applications.selected_id == "live", "wraps around"
    # Reverse direction also walks the sorted order.
    await page.cycle_application(-1)
    assert page.applications.selected_id == "dead"


@pytest.mark.asyncio
async def test_cycle_application_with_fewer_than_two_apps_is_noop() -> None:
    """One-app case: nothing to cycle to; the call is a no-op.
    Zero-app case: same."""
    page, fake = _make()
    fake.add_application(app_id="solo", name="only")
    await page.setup()
    before = page.applications.selected_id
    await page.cycle_application(1)
    assert page.applications.selected_id == before


@pytest.mark.asyncio
async def test_select_job_run_cascades_to_logs_vm_with_log_uri() -> None:
    """When select_job_run cascades to logs VM, it should transition
    to LogsState.IDLE if the detail has s3_monitoring_log_uri."""
    page, fake = _make()
    fake.add_application(app_id="a1", name="app")
    fake.add_job_run(application_id="a1", job_run_id="r1")
    fake.add_job_run_detail(
        application_id="a1",
        job_run_id="r1",
        entry_point="s3://b/x.py",
        s3_monitoring_log_uri="s3://logs-bucket/app-logs/",
    )
    await page.setup()
    await page.select_job_run("r1")
    assert page.job_run_logs.state is LogsState.IDLE


@pytest.mark.asyncio
async def test_select_job_run_cascades_to_logs_vm_without_log_uri() -> None:
    """When select_job_run cascades to logs VM, it should transition
    to LogsState.NO_LOG_CONFIG if the detail has no s3_monitoring_log_uri."""
    page, fake = _make()
    fake.add_application(app_id="a1", name="app")
    fake.add_job_run(application_id="a1", job_run_id="r1")
    fake.add_job_run_detail(
        application_id="a1",
        job_run_id="r1",
        entry_point="s3://b/x.py",
        s3_monitoring_log_uri=None,
    )
    await page.setup()
    await page.select_job_run("r1")
    assert page.job_run_logs.state is LogsState.NO_LOG_CONFIG
