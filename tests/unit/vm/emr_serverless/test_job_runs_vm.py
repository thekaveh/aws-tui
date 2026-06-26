"""JobRunsVM tests — application-scoped, state-filtered, selection-aware."""

from __future__ import annotations

import pytest
from vmx import NULL_DISPATCHER, MessageHub
from vmx.messages.protocols import Message

from aws_tui.domain.emr_serverless import JobRunState
from aws_tui.vm.emr_serverless.job_runs_vm import JobRunsVM
from aws_tui.vm.file_manager.pane_vm import PaneState
from tests.unit.domain._in_memory_emr import _InMemoryEmr


def _seed_runs(fake: _InMemoryEmr, app: str) -> None:
    fake.add_application(app_id=app, name="etl")
    fake.add_job_run(application_id=app, job_run_id="r1", state=JobRunState.SUCCESS)
    fake.add_job_run(application_id=app, job_run_id="r2", state=JobRunState.RUNNING)
    fake.add_job_run(application_id=app, job_run_id="r3", state=JobRunState.FAILED)
    fake.add_job_run(application_id=app, job_run_id="r4", state=JobRunState.CANCELLED)


def _make() -> tuple[JobRunsVM, _InMemoryEmr]:
    fake = _InMemoryEmr()
    hub: MessageHub[Message] = MessageHub()
    vm = JobRunsVM(client=fake, hub=hub, dispatcher=NULL_DISPATCHER)
    vm.construct()
    return vm, fake


@pytest.mark.asyncio
async def test_set_application_loads_runs() -> None:
    vm, fake = _make()
    _seed_runs(fake, "a1")
    vm.set_application("a1")
    await vm.refresh()
    assert vm.state is PaneState.IDLE
    assert {r.job_run_id for r in vm.runs} == {"r1", "r2", "r3", "r4"}


@pytest.mark.asyncio
async def test_state_filter_drops_runs_not_matching() -> None:
    vm, fake = _make()
    _seed_runs(fake, "a1")
    vm.set_application("a1")
    vm.set_state_filter(frozenset({JobRunState.SUCCESS, JobRunState.RUNNING}))
    await vm.refresh()
    assert {r.job_run_id for r in vm.runs} == {"r1", "r2"}


@pytest.mark.asyncio
async def test_toggle_state_filter_flips_membership() -> None:
    vm, fake = _make()
    _seed_runs(fake, "a1")
    vm.set_application("a1")
    await vm.refresh()
    vm.toggle_state_filter(JobRunState.FAILED)
    assert JobRunState.FAILED not in vm.state_filter
    vm.toggle_state_filter(JobRunState.FAILED)
    assert JobRunState.FAILED in vm.state_filter


@pytest.mark.asyncio
async def test_select_routes_change_notification() -> None:
    vm, fake = _make()
    _seed_runs(fake, "a1")
    vm.set_application("a1")
    await vm.refresh()
    notified: list[str] = []
    sub = vm._hub.messages.subscribe(  # type: ignore[attr-defined]
        on_next=lambda m: notified.append(getattr(m, "property_name", ""))
    )
    try:
        vm.select("r2")
        assert vm.selected_id == "r2"
        assert "selected_id" in notified
    finally:
        sub.dispose()


@pytest.mark.asyncio
async def test_set_application_with_none_clears_list() -> None:
    vm, fake = _make()
    _seed_runs(fake, "a1")
    vm.set_application("a1")
    await vm.refresh()
    vm.set_application(None)
    assert vm.runs == ()
    assert vm.selected_id is None
    assert vm.state is PaneState.EMPTY
