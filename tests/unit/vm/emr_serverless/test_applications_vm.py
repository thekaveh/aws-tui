"""ApplicationsVM tests — pin the load/select/refresh contract."""

from __future__ import annotations

import pytest
from vmx import NULL_DISPATCHER, MessageHub
from vmx.messages.protocols import Message

from aws_tui.domain.emr_serverless import ApplicationState
from aws_tui.vm.emr_serverless.applications_vm import ApplicationsVM
from aws_tui.vm.file_manager.pane_vm import PaneState
from tests.unit.domain._in_memory_emr import _InMemoryEmr


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
    state group. The picker dropdown AND the Shift+S cycle consume
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
