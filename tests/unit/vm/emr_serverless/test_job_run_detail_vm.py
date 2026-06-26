"""JobRunDetailVM tests — target tracking + refresh contract."""

from __future__ import annotations

import pytest
from vmx import NULL_DISPATCHER, MessageHub
from vmx.messages.protocols import Message

from aws_tui.domain.emr_serverless import JobRunState
from aws_tui.vm.emr_serverless.job_run_detail_vm import JobRunDetailVM
from aws_tui.vm.file_manager.pane_vm import PaneState
from tests.unit.domain._in_memory_emr import _InMemoryEmr


def _make() -> tuple[JobRunDetailVM, _InMemoryEmr]:
    fake = _InMemoryEmr()
    hub: MessageHub[Message] = MessageHub()
    vm = JobRunDetailVM(client=fake, hub=hub, dispatcher=NULL_DISPATCHER)
    vm.construct()
    return vm, fake


@pytest.mark.asyncio
async def test_refresh_with_target_loads_detail() -> None:
    vm, fake = _make()
    fake.add_job_run(application_id="a1", job_run_id="r1", state=JobRunState.RUNNING)
    fake.add_job_run_detail(application_id="a1", job_run_id="r1", entry_point="s3://b/x.py")
    vm.set_target("a1", "r1")
    await vm.refresh()
    assert vm.detail is not None
    assert vm.detail.entry_point == "s3://b/x.py"
    assert vm.state is PaneState.IDLE


@pytest.mark.asyncio
async def test_set_target_to_none_clears_detail() -> None:
    vm, fake = _make()
    fake.add_job_run(application_id="a1", job_run_id="r1")
    fake.add_job_run_detail(application_id="a1", job_run_id="r1")
    vm.set_target("a1", "r1")
    await vm.refresh()
    vm.set_target(None, None)
    assert vm.detail is None
    assert vm.state is PaneState.EMPTY


@pytest.mark.asyncio
async def test_is_terminal_state_returns_true_on_success() -> None:
    vm, fake = _make()
    fake.add_job_run(application_id="a1", job_run_id="r1", state=JobRunState.SUCCESS)
    fake.add_job_run_detail(application_id="a1", job_run_id="r1")
    vm.set_target("a1", "r1")
    await vm.refresh()
    assert vm.is_terminal_state()


@pytest.mark.asyncio
async def test_is_terminal_state_returns_false_on_running() -> None:
    vm, fake = _make()
    fake.add_job_run(application_id="a1", job_run_id="r1", state=JobRunState.RUNNING)
    fake.add_job_run_detail(application_id="a1", job_run_id="r1")
    vm.set_target("a1", "r1")
    await vm.refresh()
    assert not vm.is_terminal_state()
