from __future__ import annotations

import asyncio

import pytest
from vmx import (
    NULL_DISPATCHER,
    ComponentVM,
    ConstructionStatus,
    Message,
    MessageHub,
    PropertyChangedMessage,
    TokenPagedComposition,
)

from aws_tui.domain.filesystem import ProviderError, ProviderUnreachableError
from aws_tui.vm.file_manager.pane_vm import PaneState
from aws_tui.vm.glue.jobs_vm import GlueJobsVM
from tests.pager_helpers import assert_pager_disposed, assert_pager_live
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
async def test_job_pagers_reject_multi_token_cycles_before_mutation() -> None:
    fake = seeded_glue()
    lineage = {None: "A", "A": "B", "B": "A"}
    original_jobs = fake.list_jobs_page
    original_runs = fake.list_job_runs_page

    async def jobs(*, start_token: str | None = None) -> tuple[list, str | None]:
        rows, _ = await original_jobs(start_token=None)
        return rows, lineage[start_token]

    async def runs(
        job_name: str,
        *,
        start_token: str | None = None,
        states: tuple[str, ...] = (),
    ) -> tuple[list, str | None]:
        rows, _ = await original_runs(job_name, start_token=None, states=states)
        return rows, lineage[start_token]

    fake.list_jobs_page = jobs  # type: ignore[method-assign]
    fake.list_job_runs_page = runs  # type: ignore[method-assign]
    vm = make_jobs_vm(fake)
    pagers = (
        (vm._make_job_pager(), "job continuation"),
        (vm._make_run_pager("nightly"), "job-run continuation"),
    )
    try:
        for pager, message in pagers:
            await pager.refresh_command.execute_async()
            await pager.load_more_command.execute_async()
            before = pager.items
            with pytest.raises(ProviderError, match=message):
                await pager.load_more_command.execute_async()
            assert pager.items == before
    finally:
        for pager, _message in pagers:
            pager.dispose()
        vm.dispose()


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
    assert_pager_live(old_pager)
    await vm.load_more_runs()

    await vm.set_run_state_filter(frozenset({"RUNNING"}))

    assert_pager_disposed(old_pager)
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

    assert [name for name in notifications if name not in {"status", "is_constructed"}] == []
    assert (
        vm._job_generation,  # type: ignore[attr-defined]
        vm._run_generation,  # type: ignore[attr-defined]
    ) == tuple(generation + 1 for generation in generations)
    subscription.dispose()


def test_jobs_dispose_reaches_both_pagers_once(monkeypatch: pytest.MonkeyPatch) -> None:
    vm = make_jobs_vm(seeded_glue())
    pagers = [vm._job_pager, vm._run_pager]  # type: ignore[attr-defined]
    for pager in pagers:
        assert_pager_live(pager)
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
    for pager in pagers:
        assert_pager_disposed(pager)


def test_jobs_vm_is_a_component_vm_with_its_own_lifecycle() -> None:
    vm = make_jobs_vm(seeded_glue())
    assert isinstance(vm, ComponentVM)
    assert vm.name == "glue.jobs"
    assert vm.status is ConstructionStatus.CONSTRUCTED
    vm.dispose()
    assert vm.status is ConstructionStatus.DISPOSED


def test_jobs_construct_after_dispose_is_a_silent_no_op() -> None:
    vm = make_jobs_vm(seeded_glue())
    vm.dispose()
    vm.construct()
    assert vm.status is ConstructionStatus.DISPOSED


async def test_jobs_construct_after_shutdown_is_a_silent_no_op() -> None:
    vm = make_jobs_vm(seeded_glue())
    await vm.shutdown()
    before = vm.status
    vm.construct()
    assert vm.status is before
    vm.dispose()


async def test_jobs_raising_property_subscriber_does_not_stop_later_subscribers() -> None:
    vm = make_jobs_vm(seeded_glue())
    seen: list[str] = []

    def boom(_name: str) -> None:
        raise RuntimeError("subscriber failed")

    vm.on_property_changed.subscribe(on_next=boom)
    vm.on_property_changed.subscribe(on_next=seen.append)
    await vm.setup()
    assert "jobs" in seen
    vm.dispose()


async def test_jobs_hub_messages_keep_the_vm_as_sender() -> None:
    hub: MessageHub[Message] = MessageHub()
    sent: list[PropertyChangedMessage] = []
    hub.messages.subscribe(
        on_next=lambda m: sent.append(m) if isinstance(m, PropertyChangedMessage) else None
    )
    vm = GlueJobsVM(client=seeded_glue(), hub=hub, dispatcher=NULL_DISPATCHER)
    vm.construct()
    sent.clear()
    await vm.setup()
    matching = [m for m in sent if m.property_name == "jobs"]
    assert len(matching) == 1
    assert matching[0].sender_object is vm
    assert matching[0].sender_name == "glue.jobs"
    vm.dispose()
