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
async def test_job_runs_uses_vmx_token_pager() -> None:
    from vmx import TokenPagedComposition

    vm, _fake = _make()
    try:
        assert isinstance(vm._pager, TokenPagedComposition)
    finally:
        vm.dispose()


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


# -------------------- Phase 2: composite-backed selection (§4.2.1) --------------------


@pytest.mark.asyncio
async def test_selected_id_derives_from_composite_current() -> None:
    """selected_id is derived from ``_inner.current.model.job_run_id``;
    no hand-rolled ``_selected_id`` field exists after Phase 2."""
    vm, fake = _make()
    _seed_runs(fake, "a1")
    vm.set_application("a1")
    await vm.refresh()
    assert not hasattr(vm, "_selected_id"), (
        "JobRunsVM must not have a hand-rolled _selected_id field; "
        "selection lives in CompositeVM.current after Phase 2."
    )
    vm.select("r2")
    assert vm.selected_id == "r2"
    assert vm._inner.current is not None
    assert vm._inner.current.model.job_run_id == "r2"


@pytest.mark.asyncio
async def test_select_unknown_run_id_is_noop() -> None:
    vm, fake = _make()
    _seed_runs(fake, "a1")
    vm.set_application("a1")
    await vm.refresh()
    vm.select("r1")
    vm.select("does-not-exist")
    # Selection unchanged.
    assert vm.selected_id == "r1"


@pytest.mark.asyncio
async def test_reselecting_same_run_id_is_idempotent() -> None:
    """Re-selecting fires no ``selected_id`` PropertyChanged. Pins
    the §9.bis.9 PR #99(b) acceptance: no spurious notifications for
    a no-op cursor move."""
    vm, fake = _make()
    _seed_runs(fake, "a1")
    vm.set_application("a1")
    await vm.refresh()
    vm.select("r2")
    notified: list[str] = []
    sub = vm._hub.messages.subscribe(  # type: ignore[attr-defined]
        on_next=lambda m: notified.append(getattr(m, "property_name", ""))
    )
    try:
        vm.select("r2")
        assert "selected_id" not in notified
    finally:
        sub.dispose()


@pytest.mark.asyncio
async def test_refresh_restores_selection_when_run_id_still_present() -> None:
    """If the prior selection's id is still in the new page, the
    cursor restores to it across refresh — keeps View bindings stable
    across polls."""
    vm, fake = _make()
    _seed_runs(fake, "a1")
    vm.set_application("a1")
    await vm.refresh()
    vm.select("r2")
    await vm.refresh()
    # selected_id preserved across refresh.
    assert vm.selected_id == "r2"


@pytest.mark.asyncio
async def test_refresh_clears_selection_when_run_id_vanished() -> None:
    """If the prior selection is gone after refresh, cursor goes to
    None and the VM emits ``selected_id``."""
    vm, fake = _make()
    fake.add_application(app_id="a1", name="etl")
    fake.add_job_run(application_id="a1", job_run_id="r1", state=JobRunState.SUCCESS)
    fake.add_job_run(application_id="a1", job_run_id="r2", state=JobRunState.RUNNING)
    vm.set_application("a1")
    await vm.refresh()
    vm.select("r1")
    # Remove r1 from the fake (the fake's _runs is dict[app_id, dict[run_id, ...]]).
    fake._runs["a1"].pop("r1", None)  # type: ignore[attr-defined]
    notified: list[str] = []
    sub = vm._hub.messages.subscribe(  # type: ignore[attr-defined]
        on_next=lambda m: notified.append(getattr(m, "property_name", ""))
    )
    try:
        await vm.refresh()
        assert vm.selected_id is None
        assert "selected_id" in notified
    finally:
        sub.dispose()


@pytest.mark.asyncio
async def test_composite_on_collection_changed_fires_on_refresh() -> None:
    """Composite emits on_collection_changed events when refresh
    appends new items."""
    vm, fake = _make()
    _seed_runs(fake, "a1")
    vm.set_application("a1")
    events: list[object] = []
    sub = vm._inner.on_collection_changed.subscribe(on_next=events.append)
    try:
        await vm.refresh()
        # First refresh populates the composite — expect at least
        # one "add" event per added run.
        assert len(events) > 0
    finally:
        sub.dispose()


@pytest.mark.asyncio
async def test_has_active_runs_uses_composite_items() -> None:
    """has_active_runs traverses composite items, not the old
    _runs_cache tuple."""
    vm, fake = _make()
    fake.add_application(app_id="a1", name="etl")
    fake.add_job_run(application_id="a1", job_run_id="r1", state=JobRunState.RUNNING)
    vm.set_application("a1")
    await vm.refresh()
    assert vm.has_active_runs() is True
    # Re-fetch with only terminal runs.
    fake._runs["a1"] = {}  # type: ignore[attr-defined]
    fake.add_job_run(application_id="a1", job_run_id="r2", state=JobRunState.SUCCESS)
    await vm.refresh()
    assert vm.has_active_runs() is False


@pytest.mark.asyncio
async def test_state_filter_does_not_lose_selection_on_filter_change() -> None:
    """Filter-text changes are projections; they must NOT clear the
    composite cursor. (Selection-clear-on-vanish only happens when
    the cache is rebuilt — refresh / set_application.)"""
    vm, fake = _make()
    _seed_runs(fake, "a1")
    vm.set_application("a1")
    await vm.refresh()
    vm.select("r1")  # SUCCESS
    # Narrow filter to exclude SUCCESS — cursor's identity unchanged.
    vm.set_state_filter(frozenset({JobRunState.RUNNING}))
    # selected_id is still "r1" (it's a property on the composite, not
    # on the filtered view).
    assert vm.selected_id == "r1"


@pytest.mark.asyncio
async def test_dispose_cleans_up_items() -> None:
    vm, fake = _make()
    _seed_runs(fake, "a1")
    vm.set_application("a1")
    await vm.refresh()
    assert len(vm._items) == 4  # type: ignore[attr-defined]
    vm.dispose()
    assert vm._items == []  # type: ignore[attr-defined]


# -------------------- Phase 2 dedup-on-set parity (§9.bis.9 Q-A) --------------------


@pytest.mark.asyncio
async def test_refresh_is_no_op_when_page_is_unchanged() -> None:
    """Dedup-on-set: a refresh that returns the SAME first page must
    NOT fire `runs`/`selected_id`/`on_collection_changed` events.
    Mirrors ApplicationsVM's PR #100(b) guard."""
    vm, fake = _make()
    _seed_runs(fake, "a1")
    vm.set_application("a1")
    await vm.refresh()
    notified: list[str] = []
    sub = vm.on_property_changed.subscribe(on_next=notified.append)
    composite_events: list[object] = []
    sub2 = vm._inner.on_collection_changed.subscribe(  # type: ignore[attr-defined]
        on_next=composite_events.append
    )
    try:
        await vm.refresh()  # same page
        # State LOADING→IDLE transitions still fire — that's fine; the
        # data-shape signal "runs" must NOT.
        assert "runs" not in notified, (
            "JobRunsVM did not absorb the no-change refresh — Phase 2 dedup parity regression"
        )
        assert "selected_id" not in notified
        assert composite_events == []
    finally:
        sub.dispose()
        sub2.dispose()


# -------------------- PR #103 retirement: per-VM Observable --------------------


@pytest.mark.asyncio
async def test_on_property_changed_fires_per_vm_instance() -> None:
    """Round-3 / PR #103 retirement path: the per-VM Observable
    fires scoped to THIS VM only."""
    vm, fake = _make()
    _seed_runs(fake, "a1")
    vm.set_application("a1")
    events: list[str] = []
    sub = vm.on_property_changed.subscribe(on_next=events.append)
    try:
        await vm.refresh()
        assert "state" in events
        assert "runs" in events
    finally:
        sub.dispose()


@pytest.mark.asyncio
async def test_on_property_changed_isolates_cross_vm_state_events() -> None:
    """The §9.bis.9 PR #103 acceptance criterion: constructing two
    JobRunsVMs on the same hub and triggering `state` on one must
    NOT fire events on the other VM's Observable. Structural —
    enforced by per-VM Subject identity, NOT by sender_object
    filtering."""
    vm1, _ = _make()
    fake2 = _InMemoryEmr()
    fake2.add_application(app_id="b1", name="other")
    hub: MessageHub[Message] = vm1._hub  # type: ignore[attr-defined]
    vm2 = JobRunsVM(client=fake2, hub=hub, dispatcher=NULL_DISPATCHER)
    vm2.construct()
    events_on_vm1: list[str] = []
    sub = vm1.on_property_changed.subscribe(on_next=events_on_vm1.append)
    try:
        vm2.set_application("b1")
        await vm2.refresh()
        # vm1 should not see any of vm2's events.
        assert events_on_vm1 == []
    finally:
        sub.dispose()
        vm2.dispose()
