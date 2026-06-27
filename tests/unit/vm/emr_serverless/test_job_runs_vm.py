"""JobRunsVM tests — application-scoped, state-filtered, selection-aware."""

from __future__ import annotations

import pytest
from vmx import NULL_DISPATCHER, MessageHub
from vmx.messages.protocols import Message

from aws_tui.domain.emr_serverless import JobRunState
from aws_tui.vm.emr_serverless.job_runs_vm import _ACTIVE_STATES, JobRunsVM
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


# ── has_active_runs() — used by EmrServerlessPage's cadence-decay poller ────


@pytest.mark.parametrize(
    "active_state",
    sorted(_ACTIVE_STATES, key=lambda s: s.value),
)
async def test_has_active_runs_true_for_each_active_state(
    active_state: JobRunState,
) -> None:
    """``has_active_runs()`` reports True if ANY cached run is in a
    pre-terminal state — the page-VM poller uses this to switch
    between the 10-s and 60-s cadence (see ``_ACTIVE_STATES`` in
    ``job_runs_vm.py``). One run in the given pre-terminal state is
    sufficient to count as active.

    Pass-2 L-2 (test-review): the parametrize iterates the imported
    ``_ACTIVE_STATES`` frozenset so a new pre-terminal state added
    upstream (e.g. a future ``PROVISIONING``) is automatically
    exercised without a parallel edit here."""
    vm, fake = _make()
    fake.add_application(app_id="a1", name="etl")
    fake.add_job_run(application_id="a1", job_run_id="r1", state=active_state)
    vm.set_application("a1")
    await vm.refresh()
    assert vm.has_active_runs() is True


@pytest.mark.parametrize(
    "terminal_state",
    [JobRunState.SUCCESS, JobRunState.FAILED, JobRunState.CANCELLED],
)
async def test_has_active_runs_false_when_only_terminal_runs(
    terminal_state: JobRunState,
) -> None:
    """If every cached run is terminal, the poller may safely back
    off to the 60-s cadence — ``has_active_runs()`` returns False."""
    vm, fake = _make()
    fake.add_application(app_id="a1", name="etl")
    fake.add_job_run(application_id="a1", job_run_id="r1", state=terminal_state)
    vm.set_application("a1")
    await vm.refresh()
    assert vm.has_active_runs() is False


async def test_has_active_runs_false_when_cache_empty() -> None:
    """No cached runs at all → no active runs."""
    vm, _fake = _make()
    assert vm.has_active_runs() is False


async def test_has_active_runs_uses_unfiltered_cache_not_state_filter() -> None:
    """The cadence decision must reflect what's ACTUALLY happening at
    the provider, not what the UI is currently filtering down to —
    otherwise filtering RUNNING out of the chip row would make the
    poller back off and the user would miss state transitions on the
    runs they explicitly de-selected from view."""
    vm, fake = _make()
    fake.add_application(app_id="a1", name="etl")
    fake.add_job_run(application_id="a1", job_run_id="r1", state=JobRunState.RUNNING)
    vm.set_application("a1")
    await vm.refresh()
    # Filter RUNNING out of view; the cache still holds an active run.
    vm.set_state_filter(frozenset({JobRunState.SUCCESS}))
    assert vm.runs == ()  # filtered out of the public list
    assert vm.has_active_runs() is True  # but the cache still says active


# ── Pagination (load_more / has_more) ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_refresh_starts_at_first_page_has_more_when_more_available() -> None:
    """Refresh fetches ONE page. ``has_more`` reflects the server's
    next-token; pinned so the pane's "Load more" sentinel stays
    in lockstep with the VM's paging state."""
    vm, fake = _make()
    fake.add_application(app_id="a1", name="etl")
    fake.page_size = 2  # force multi-page over 4 runs
    fake.add_job_run(application_id="a1", job_run_id="r1", state=JobRunState.SUCCESS)
    fake.add_job_run(application_id="a1", job_run_id="r2", state=JobRunState.RUNNING)
    fake.add_job_run(application_id="a1", job_run_id="r3", state=JobRunState.FAILED)
    fake.add_job_run(application_id="a1", job_run_id="r4", state=JobRunState.CANCELLED)
    vm.set_application("a1")
    await vm.refresh()
    assert vm.has_more is True
    assert len(vm.runs) == 2  # first page only


@pytest.mark.asyncio
async def test_load_more_appends_next_page_then_clears_has_more() -> None:
    vm, fake = _make()
    fake.add_application(app_id="a1", name="etl")
    fake.page_size = 2
    fake.add_job_run(application_id="a1", job_run_id="r1", state=JobRunState.SUCCESS)
    fake.add_job_run(application_id="a1", job_run_id="r2", state=JobRunState.RUNNING)
    fake.add_job_run(application_id="a1", job_run_id="r3", state=JobRunState.FAILED)
    vm.set_application("a1")
    await vm.refresh()
    assert vm.has_more is True
    assert len(vm.runs) == 2
    await vm.load_more()
    # Second page appends — full list now visible.
    assert {r.job_run_id for r in vm.runs} == {"r1", "r2", "r3"}
    # Third call drains the token to None.
    assert vm.has_more is False


@pytest.mark.asyncio
async def test_load_more_is_noop_when_no_more_pages() -> None:
    """Defensive: the pane keeps the PgDn binding even on
    fully-drained lists; calling load_more then must NOT re-fetch
    or alter state."""
    vm, fake = _make()
    fake.add_application(app_id="a1", name="etl")
    fake.page_size = 100
    fake.add_job_run(application_id="a1", job_run_id="r1", state=JobRunState.SUCCESS)
    vm.set_application("a1")
    await vm.refresh()
    assert vm.has_more is False
    before_calls = list(fake.calls)
    await vm.load_more()
    assert fake.calls == before_calls  # no new client call
    assert vm.has_more is False


@pytest.mark.asyncio
async def test_set_application_resets_paging_state() -> None:
    vm, fake = _make()
    fake.add_application(app_id="a1", name="etl")
    fake.add_application(app_id="a2", name="other")
    fake.page_size = 1
    fake.add_job_run(application_id="a1", job_run_id="r1", state=JobRunState.SUCCESS)
    fake.add_job_run(application_id="a1", job_run_id="r2", state=JobRunState.RUNNING)
    vm.set_application("a1")
    await vm.refresh()
    assert vm.has_more is True
    # Switch apps — paging state must reset; otherwise the new app
    # inherits an old next_token and load_more crashes / mis-fetches.
    vm.set_application("a2")
    assert vm.has_more is False
    assert vm.runs == ()
