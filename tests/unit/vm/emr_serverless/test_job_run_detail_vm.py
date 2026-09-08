"""JobRunDetailVM tests — target tracking + refresh contract."""

from __future__ import annotations

import asyncio

import pytest
from vmx import NULL_DISPATCHER, MessageHub
from vmx.messages.protocols import Message

from aws_tui.demo.in_memory_emr import InMemoryEmr as _InMemoryEmr
from aws_tui.domain.emr_serverless import JobRunState
from aws_tui.domain.filesystem import ProviderError
from aws_tui.vm.emr_serverless.job_run_detail_vm import JobRunDetailVM
from aws_tui.vm.file_manager.pane_vm import PaneState


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


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure",
    [ProviderError("access denied"), RuntimeError("unexpected client failure")],
)
async def test_a_target_switch_mid_flight_drops_the_previous_runs_error(
    failure: Exception,
) -> None:
    """Run A's slow failure must not be written into run B's pane.

    ``refresh()`` captures the target before awaiting ``get_job_run`` and
    re-checks it in all three exits. The SUCCESS exit is pinned by an existing
    test; neither error exit was. Dropping the target comparison from either
    ``except`` arm survived the whole repo suite, while a slow "permission
    denied" for the run the user just navigated away from landed in the pane as
    the CURRENT run's error.
    """
    vm, fake = _make()
    fake.add_job_run(application_id="a1", job_run_id="r1", state=JobRunState.RUNNING)
    started = asyncio.Event()
    released = asyncio.Event()

    async def slow_failure(_app_id: str, _run_id: str) -> object:
        started.set()
        await released.wait()
        raise failure

    fake.get_job_run = slow_failure  # type: ignore[method-assign]
    vm.set_target("a1", "r1")
    refresh_task = asyncio.create_task(vm.refresh())

    await asyncio.wait_for(started.wait(), timeout=2)
    vm.set_target("a1", "r2")  # user navigates away while r1 is still in flight
    released.set()
    await asyncio.wait_for(refresh_task, timeout=2)

    assert vm.error_text is None, "r1's error was applied to r2's pane"
    assert vm.state is not PaneState.ERROR
