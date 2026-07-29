from __future__ import annotations

import asyncio

import pytest
from vmx import NULL_DISPATCHER, MessageHub, TokenPagedComposition
from vmx.messages.protocols import Message

from aws_tui.domain.filesystem import ProviderUnreachableError
from aws_tui.vm.file_manager.pane_vm import PaneState
from aws_tui.vm.glue.jobs_vm import GlueJobsVM
from tests.unit.vm.glue._fake_glue import InMemoryGlue, seeded_glue


def make_jobs_vm(fake: InMemoryGlue) -> GlueJobsVM:
    hub: MessageHub[Message] = MessageHub()
    vm = GlueJobsVM(client=fake, hub=hub, dispatcher=NULL_DISPATCHER)
    vm.construct()
    return vm


@pytest.mark.asyncio
async def test_jobs_pages_jobs_and_runs_for_selected_job() -> None:
    fake = seeded_glue()
    fake.job_page_size = 1
    fake.run_page_size = 1
    vm = make_jobs_vm(fake)

    assert isinstance(vm._job_pager, TokenPagedComposition)  # type: ignore[attr-defined]
    assert isinstance(vm._run_pager, TokenPagedComposition)  # type: ignore[attr-defined]

    await vm.setup()
    assert len(vm.jobs) == 1
    assert vm.has_more_jobs
    await vm.load_more_jobs()
    assert fake.job_tokens == [None, "1"]

    await vm.select_job("nightly")
    assert len(vm.runs) == 1
    assert vm.has_more_runs
    await vm.load_more_runs()
    assert len(vm.runs) == 2
    assert fake.run_requests[-1] == ("nightly", "1", ())


@pytest.mark.asyncio
async def test_select_job_discards_stale_runs() -> None:
    fake = seeded_glue()
    runs_started = fake.block_runs("nightly")
    vm = make_jobs_vm(fake)
    await vm.setup()

    first = asyncio.create_task(vm.select_job("nightly"))
    await runs_started.wait()
    await vm.select_job("hourly")
    fake.release_runs("nightly")
    await first

    assert vm.selected_job_name == "hourly"
    assert [run.job_name for run in vm.runs] == ["hourly"]


@pytest.mark.asyncio
async def test_job_run_filter_replaces_pager_and_resets_token_lineage() -> None:
    fake = seeded_glue()
    fake.run_page_size = 1
    vm = make_jobs_vm(fake)
    await vm.setup()
    await vm.select_job("nightly")
    old_pager = vm._run_pager  # type: ignore[attr-defined]
    await vm.load_more_runs()

    await vm.set_run_state_filter(frozenset({"RUNNING"}))

    assert old_pager._disposed  # type: ignore[attr-defined]
    assert old_pager.load_more_command._disposed  # type: ignore[attr-defined]
    assert vm.run_state_filter == frozenset({"RUNNING"})
    assert [run.state for run in vm.runs] == ["RUNNING"]
    assert fake.run_requests[-1] == ("nightly", None, ("RUNNING",))


@pytest.mark.asyncio
async def test_runs_error_is_scoped_to_runs_pane() -> None:
    class BrokenRuns(InMemoryGlue):
        async def list_job_runs_page(  # type: ignore[override]
            self,
            job_name: str,
            *,
            start_token: str | None = None,
            states: tuple[str, ...] = (),
        ) -> tuple[list, str | None]:
            raise ProviderUnreachableError("network down")

    fake = BrokenRuns()
    fake.add_job("nightly")
    vm = make_jobs_vm(fake)
    await vm.setup()
    await vm.select_job("nightly")

    assert vm.jobs_state is PaneState.IDLE
    assert vm.runs_state is PaneState.UNREACHABLE
    assert vm.state is PaneState.IDLE


@pytest.mark.asyncio
async def test_jobs_load_more_unexpected_errors_are_scoped_and_redacted() -> None:
    class BrokenNextPages(InMemoryGlue):
        async def list_jobs_page(
            self,
            *,
            start_token: str | None = None,
        ) -> tuple[list, str | None]:
            if start_token is not None:
                raise RuntimeError("Authorization: Bearer JOB_SECRET")
            return await super().list_jobs_page(start_token=start_token)

        async def list_job_runs_page(
            self,
            job_name: str,
            *,
            start_token: str | None = None,
            states: tuple[str, ...] = (),
        ) -> tuple[list, str | None]:
            if start_token is not None:
                raise RuntimeError("Authorization: Bearer RUN_SECRET")
            return await super().list_job_runs_page(
                job_name,
                start_token=start_token,
                states=states,
            )

    fake = BrokenNextPages()
    fake.add_run("nightly", "jr-1")
    fake.add_run("nightly", "jr-2")
    fake.add_job("hourly")
    fake.job_page_size = 1
    fake.run_page_size = 1
    vm = make_jobs_vm(fake)

    await vm.setup()
    await vm.load_more_jobs()
    assert vm.jobs_state is PaneState.ERROR
    assert vm.jobs_error_text is not None
    assert "JOB_SECRET" not in vm.jobs_error_text
    assert "[REDACTED]" in vm.jobs_error_text

    await vm.select_job("nightly")
    await vm.load_more_runs()
    assert vm.runs_state is PaneState.ERROR
    assert vm.runs_error_text is not None
    assert "RUN_SECRET" not in vm.runs_error_text
    assert "[REDACTED]" in vm.runs_error_text


@pytest.mark.asyncio
async def test_jobs_dispose_invalidates_blocked_load_without_notifications() -> None:
    fake = seeded_glue()
    runs_started = fake.block_runs("nightly")
    vm = make_jobs_vm(fake)
    await vm.setup()
    notifications: list[str] = []
    subscription = vm.on_property_changed.subscribe(on_next=notifications.append)

    selection = asyncio.create_task(vm.select_job("nightly"))
    await runs_started.wait()
    notifications.clear()
    generations = (
        vm._job_generation,  # type: ignore[attr-defined]
        vm._run_generation,  # type: ignore[attr-defined]
    )

    vm.dispose()
    fake.release_runs("nightly")
    await selection

    assert notifications == []
    assert (
        vm._job_generation,  # type: ignore[attr-defined]
        vm._run_generation,  # type: ignore[attr-defined]
    ) == tuple(generation + 1 for generation in generations)
    subscription.dispose()


def test_jobs_dispose_reaches_both_pagers_once(monkeypatch: pytest.MonkeyPatch) -> None:
    vm = make_jobs_vm(seeded_glue())
    pagers = [vm._job_pager, vm._run_pager]  # type: ignore[attr-defined]
    calls = {id(pager): 0 for pager in pagers}
    for pager in pagers:
        original = pager.dispose

        def counted_dispose(
            *,
            target: TokenPagedComposition = pager,
            dispose: object = original,
        ) -> None:
            calls[id(target)] += 1
            dispose()  # type: ignore[operator]

        monkeypatch.setattr(pager, "dispose", counted_dispose)

    vm.dispose()
    vm.dispose()

    assert set(calls.values()) == {1}
    assert all(pager.load_more_command._disposed for pager in pagers)  # type: ignore[attr-defined]
    assert all(pager.refresh_command._disposed for pager in pagers)  # type: ignore[attr-defined]
