"""ApplicationsVM tests — pin the load/select/refresh contract."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import pytest
from vmx import NULL_DISPATCHER, MessageHub
from vmx.messages.protocols import Message

from aws_tui.demo.in_memory_emr import InMemoryEmr as _InMemoryEmr
from aws_tui.domain.emr_serverless import ApplicationState, ApplicationSummary
from aws_tui.vm.emr_serverless.applications_vm import ApplicationsVM
from aws_tui.vm.file_manager.pane_vm import PaneState


def _make() -> tuple[ApplicationsVM, _InMemoryEmr]:
    fake = _InMemoryEmr()
    hub: MessageHub[Message] = MessageHub()
    vm = ApplicationsVM(client=fake, hub=hub, dispatcher=NULL_DISPATCHER)
    vm.construct()
    return vm, fake


@pytest.mark.asyncio
async def test_starts_loading_then_idle_after_refresh() -> None:
    vm, fake = _make()
    fake.add_application(app_id="a1", name="etl")
    fake.add_application(app_id="a2", name="ad-hoc", state=ApplicationState.STOPPED)
    assert vm.state is PaneState.LOADING
    await vm.refresh()
    assert vm.state is PaneState.IDLE
    assert [a.id for a in vm.applications] == ["a1", "a2"] or [a.id for a in vm.applications] == [
        "a2",
        "a1",
    ]


@pytest.mark.asyncio
async def test_throwing_property_observer_does_not_interrupt_refresh() -> None:
    vm, fake = _make()
    fake.add_application(app_id="a1", name="etl")
    observed: list[str] = []

    def fail(_property: str) -> None:
        raise RuntimeError("subscriber failed")

    failing = vm.on_property_changed.subscribe(fail)
    healthy = vm.on_property_changed.subscribe(observed.append)
    try:
        await vm.refresh()
    finally:
        failing.dispose()
        healthy.dispose()

    assert vm.state is PaneState.IDLE
    assert "applications" in observed


@pytest.mark.asyncio
async def test_refresh_with_no_apps_lands_on_empty_state() -> None:
    vm, _ = _make()
    await vm.refresh()
    assert vm.state is PaneState.EMPTY
    assert vm.applications == ()


@pytest.mark.asyncio
async def test_select_publishes_property_changed() -> None:
    vm, fake = _make()
    fake.add_application(app_id="a1", name="etl")
    await vm.refresh()
    notified: list[str] = []
    sub = vm._hub.messages.subscribe(  # type: ignore[attr-defined]
        on_next=lambda m: notified.append(getattr(m, "property_name", ""))
    )
    try:
        vm.select("a1")
        assert vm.selected_id == "a1"
        assert "selected_id" in notified
    finally:
        sub.dispose()


@pytest.mark.asyncio
async def test_sorted_applications_is_started_first_then_alphabetical() -> None:
    """``sorted_applications`` is the single source of truth for the
    user-facing application order: STARTED first, then transitional
    (STARTING / STOPPING), then non-active (CREATING / CREATED /
    STOPPED), then terminal (TERMINATED); alphabetical within each
    state group. The picker dropdown AND the Shift+A cycle consume
    this property — pinning it pins both consumers."""
    vm, fake = _make()
    # Deliberately shuffled — confirms the sort, not insertion order, drives.
    fake.add_application(app_id="t1", name="killed", state=ApplicationState.TERMINATED)
    fake.add_application(app_id="s1", name="zzz-quiet", state=ApplicationState.STOPPED)
    fake.add_application(app_id="r1", name="bravo", state=ApplicationState.STARTED)
    fake.add_application(app_id="x1", name="warming-up", state=ApplicationState.STARTING)
    fake.add_application(app_id="r2", name="alpha", state=ApplicationState.STARTED)
    fake.add_application(app_id="c1", name="ready", state=ApplicationState.CREATED)
    await vm.refresh()
    assert [a.id for a in vm.sorted_applications] == [
        "r2",  # STARTED alpha
        "r1",  # STARTED bravo
        "x1",  # STARTING warming-up
        "c1",  # CREATED ready
        "s1",  # STOPPED zzz-quiet
        "t1",  # TERMINATED killed
    ]


@pytest.mark.asyncio
async def test_sorted_applications_empty_when_no_apps() -> None:
    vm, _ = _make()
    await vm.refresh()
    assert vm.sorted_applications == ()


@pytest.mark.asyncio
async def test_refresh_is_no_op_when_application_list_unchanged() -> None:
    """Dedup-on-set: a refresh that returns identical (id, state, name)
    triples to the current list must NOT fire any
    ``applications`` PropertyChangedMessage.

    Regression anchor for PR #100(b) — the View-side fingerprint guard
    relocated into the VM per the round-3 directive (spec §9.bis.11 +
    §9.bis.9 / Q-A). The 30 s applications poller fires
    ``refresh()`` every tick; the in-memory demo provider returns the
    same list every time; the VM must absorb the no-change case so
    downstream View consumers don't even see the event.
    """
    vm, fake = _make()
    fake.add_application(app_id="a1", name="etl")
    fake.add_application(app_id="a2", name="ad-hoc")
    await vm.refresh()  # First load — sets up the cache.

    notified: list[str] = []
    sub = vm._hub.messages.subscribe(  # type: ignore[attr-defined]
        on_next=lambda m: notified.append(getattr(m, "property_name", ""))
    )
    try:
        # Second refresh — same upstream list. No ``applications``
        # PropertyChanged should fire. (State LOADING→IDLE transitions
        # still emit ``state`` notifications; that's expected.)
        await vm.refresh()
        assert "applications" not in notified, (
            "VM did not absorb the no-change refresh — fingerprint guard regression"
        )
        assert "selected_id" not in notified
    finally:
        sub.dispose()


@pytest.mark.asyncio
async def test_refresh_is_no_op_when_provider_only_changes_application_order() -> None:
    vm, fake = _make()
    fake.add_application(app_id="stopped", name="zeta", state=ApplicationState.STOPPED)
    fake.add_application(app_id="started-b", name="bravo", state=ApplicationState.STARTED)
    fake.add_application(app_id="started-a", name="alpha", state=ApplicationState.STARTED)

    applications = list(await fake.list_applications())
    orders = iter((applications, list(reversed(applications))))

    async def list_in_permuted_order():  # type: ignore[no-untyped-def]
        return next(orders)

    fake.list_applications = list_in_permuted_order  # type: ignore[method-assign]
    await vm.refresh()
    assert [app.id for app in vm.applications] == ["started-a", "started-b", "stopped"]

    notified: list[str] = []
    sub = vm.on_property_changed.subscribe(notified.append)
    try:
        await vm.refresh()
    finally:
        sub.dispose()

    assert "applications" not in notified
    assert [app.id for app in vm.applications] == ["started-a", "started-b", "stopped"]


# -------------------- Phase 1: composite-backed selection (§4.2.3) --------------------


@pytest.mark.asyncio
async def test_selected_id_derives_from_composite_current() -> None:
    """selected_id is a derived @property over ``_inner.current``; no
    hand-rolled ``_selected_id`` field exists after Phase 1."""
    vm, fake = _make()
    fake.add_application(app_id="a1", name="etl")
    await vm.refresh()
    assert not hasattr(vm, "_selected_id"), (
        "ApplicationsVM must not have a hand-rolled _selected_id field; "
        "selection lives in CompositeVM.current after Phase 1."
    )
    vm.select("a1")
    assert vm.selected_id == "a1"
    assert vm._inner.current is not None
    assert vm._inner.current.model.id == "a1"


@pytest.mark.asyncio
async def test_select_promotes_to_composite_current_slot() -> None:
    vm, fake = _make()
    fake.add_application(app_id="a1", name="etl")
    fake.add_application(app_id="a2", name="ad-hoc")
    await vm.refresh()
    assert vm._inner.current is None
    vm.select("a2")
    assert vm._inner.current is not None
    assert vm._inner.current.model.id == "a2"


@pytest.mark.asyncio
async def test_unknown_app_id_select_is_no_op() -> None:
    vm, fake = _make()
    fake.add_application(app_id="a1", name="etl")
    await vm.refresh()
    vm.select("a1")
    vm.select("missing")
    # Selection unchanged.
    assert vm.selected_id == "a1"


@pytest.mark.asyncio
async def test_reselecting_same_app_is_idempotent() -> None:
    """Re-selecting fires no ``selected_id`` PropertyChanged."""
    vm, fake = _make()
    fake.add_application(app_id="a1", name="etl")
    await vm.refresh()
    vm.select("a1")
    notified: list[str] = []
    sub = vm._hub.messages.subscribe(  # type: ignore[attr-defined]
        on_next=lambda m: notified.append(getattr(m, "property_name", ""))
    )
    try:
        vm.select("a1")
        assert "selected_id" not in notified
    finally:
        sub.dispose()


@pytest.mark.asyncio
async def test_refresh_with_changed_list_emits_applications_event() -> None:
    """A genuine list-change MUST fire ``applications`` PropertyChanged
    (complement of the dedup-on-set test)."""
    vm, fake = _make()
    fake.add_application(app_id="a1", name="etl")
    await vm.refresh()
    notified: list[str] = []
    sub = vm._hub.messages.subscribe(  # type: ignore[attr-defined]
        on_next=lambda m: notified.append(getattr(m, "property_name", ""))
    )
    try:
        # Add a second app — list changes.
        fake.add_application(app_id="a2", name="zeta")
        await vm.refresh()
        assert "applications" in notified
    finally:
        sub.dispose()


@pytest.mark.asyncio
async def test_refresh_drops_selection_when_selected_app_vanishes() -> None:
    """If the selected application is no longer in the new list, the
    composite drops current (via _remove_at clearing) and the VM
    emits ``selected_id``."""
    vm, fake = _make()
    fake.add_application(app_id="a1", name="etl")
    fake.add_application(app_id="a2", name="ad-hoc")
    await vm.refresh()
    vm.select("a2")
    # Remove a2 from the fake (deletes both the app and its runs slot).
    fake._apps.pop("a2", None)  # type: ignore[attr-defined]
    fake._runs.pop("a2", None)  # type: ignore[attr-defined]
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
async def test_composite_on_collection_changed_fires_on_real_change() -> None:
    """Composite must emit on_collection_changed when refresh actually
    rebuilds (complement of dedup-on-set: no-change → no event)."""
    vm, fake = _make()
    fake.add_application(app_id="a1", name="etl")
    await vm.refresh()
    events: list[object] = []
    sub = vm._inner.on_collection_changed.subscribe(on_next=events.append)
    try:
        fake.add_application(app_id="a2", name="alpha")
        await vm.refresh()
        assert len(events) > 0
    finally:
        sub.dispose()


@pytest.mark.asyncio
async def test_dispose_cleans_up_items() -> None:
    vm, fake = _make()
    fake.add_application(app_id="a1", name="etl")
    await vm.refresh()
    assert len(vm._items) == 1  # type: ignore[attr-defined]
    vm.dispose()
    # After dispose, items list cleared and composite disposed.
    assert vm._items == []  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_on_property_changed_fires_per_vm_instance() -> None:
    """Round-3 / PR #103 retirement path: the per-VM Observable
    fires scoped to THIS VM only. View consumers binding here are
    immune to cross-VM `state` PropertyChanged collisions on the
    shared hub."""
    vm, fake = _make()
    fake.add_application(app_id="a1", name="etl")
    events: list[str] = []
    sub = vm.on_property_changed.subscribe(on_next=events.append)
    try:
        await vm.refresh()
        # state transitions: LOADING → IDLE; applications populated.
        assert "state" in events
        assert "applications" in events
    finally:
        sub.dispose()


@pytest.mark.asyncio
async def test_on_property_changed_isolates_cross_vm_state_events() -> None:
    """Constructing a second ApplicationsVM on the same hub must NOT
    cause `state` events on it to fire on the first VM's
    Observable. PR #103 acceptance criterion structurally enforced
    by per-VM Subject."""
    vm1, _ = _make()
    fake2 = _make()[1]
    hub: MessageHub[Message] = vm1._hub  # type: ignore[attr-defined]
    vm2 = ApplicationsVM(client=fake2, hub=hub, dispatcher=NULL_DISPATCHER)
    vm2.construct()
    events_on_vm1: list[str] = []
    sub = vm1.on_property_changed.subscribe(on_next=events_on_vm1.append)
    try:
        await vm2.refresh()  # vm2 transitions LOADING→EMPTY
        # vm1 should not see any of vm2's events.
        assert events_on_vm1 == []
    finally:
        sub.dispose()
        vm2.dispose()


@pytest.mark.asyncio
async def test_refresh_failure_surfaces_unreachable_state() -> None:
    from aws_tui.domain.filesystem import ProviderUnreachableError

    class _BrokenClient:
        async def list_applications(self) -> list:  # type: ignore[no-untyped-def]
            raise ProviderUnreachableError("network blip")

    hub: MessageHub[Message] = MessageHub()
    vm = ApplicationsVM(client=_BrokenClient(), hub=hub, dispatcher=NULL_DISPATCHER)
    vm.construct()
    await vm.refresh()
    assert vm.state is PaneState.UNREACHABLE


@pytest.mark.asyncio
async def test_concurrent_refreshes_commit_in_request_order() -> None:
    first_started = asyncio.Event()
    release_first = asyncio.Event()
    second_started = asyncio.Event()
    first = ApplicationSummary(
        id="old",
        name="old",
        state=ApplicationState.STARTED,
        type="SPARK",
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    second = ApplicationSummary(
        id="new",
        name="new",
        state=ApplicationState.STARTED,
        type="SPARK",
        created_at=datetime(2026, 1, 2, tzinfo=UTC),
    )

    class _SequencedClient:
        def __init__(self) -> None:
            self.calls = 0

        async def list_applications(self) -> list[ApplicationSummary]:
            self.calls += 1
            if self.calls == 1:
                first_started.set()
                await release_first.wait()
                return [first]
            second_started.set()
            return [second]

    client = _SequencedClient()
    hub: MessageHub[Message] = MessageHub()
    vm = ApplicationsVM(client=client, hub=hub, dispatcher=NULL_DISPATCHER)
    vm.construct()
    first_refresh = asyncio.create_task(vm.refresh())
    await asyncio.wait_for(first_started.wait(), timeout=1)
    second_refresh = asyncio.create_task(vm.refresh())
    await asyncio.sleep(0)

    assert not second_started.is_set()

    release_first.set()
    await asyncio.gather(first_refresh, second_refresh)

    assert second_started.is_set()
    assert vm.applications == (second,)
    vm.dispose()
