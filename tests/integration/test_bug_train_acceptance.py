"""§9.bis.9 bug-train acceptance criteria — integration anchors.

The round-2 spec amendment (§9.bis.9) walks the 2026-06-28 bug-train
PRs (#98 / #99 / #100 / #101 / #103 / #105) and pins each one to a
specific Phase whose acceptance criterion is "the bug from this PR
cannot recur because the mechanism that caused it no longer exists
in the code". This file is the integration-test side of those
criteria — unit tests cover the VM-side mechanics; these cover the
end-to-end signal flow.

The tests deliberately use lightweight VM constructions over the
in-memory EMR fake / mock filesystem rather than the full
``AppContext`` so they pin the SIGNAL semantics independent of
chrome rendering.
"""

from __future__ import annotations

import pytest
from vmx import NULL_DISPATCHER, MessageHub
from vmx.messages.protocols import Message

from aws_tui.domain.emr_serverless import ApplicationState, JobRunState
from aws_tui.vm.chrome.focus_coordinator_vm import FocusCoordinatorVM, FocusSlot
from aws_tui.vm.emr_serverless.applications_vm import ApplicationsVM
from aws_tui.vm.emr_serverless.job_runs_vm import JobRunsVM
from tests.unit.domain._in_memory_emr import _InMemoryEmr


def _hub() -> MessageHub[Message]:
    return MessageHub()


# -------------------- PR #100(b): fingerprint guard absorbed into VM --------------------


@pytest.mark.asyncio
async def test_pr_100b_no_event_flow_on_no_change_application_refresh() -> None:
    """Acceptance: ApplicationsVM.refresh() with an unchanged
    upstream list must NOT propagate any `applications`
    PropertyChanged through the shared hub OR the per-VM
    Observable. The fingerprint guard PR #100(b) added in the View
    is now structurally unreachable."""
    fake = _InMemoryEmr()
    fake.add_application(app_id="a1", name="etl")
    fake.add_application(app_id="a2", name="ad-hoc", state=ApplicationState.STOPPED)
    hub = _hub()
    vm = ApplicationsVM(client=fake, hub=hub, dispatcher=NULL_DISPATCHER)
    vm.construct()
    try:
        await vm.refresh()  # first load — sets up the cache

        hub_events: list[str] = []
        sub_hub = hub.messages.subscribe(
            on_next=lambda m: hub_events.append(getattr(m, "property_name", ""))
        )
        vm_events: list[str] = []
        sub_vm = vm.on_property_changed.subscribe(on_next=vm_events.append)
        try:
            await vm.refresh()  # second load — same list
            assert "applications" not in hub_events
            assert "applications" not in vm_events
            assert "selected_id" not in hub_events
            assert "selected_id" not in vm_events
        finally:
            sub_hub.dispose()
            sub_vm.dispose()
    finally:
        vm.dispose()


@pytest.mark.asyncio
async def test_pr_100b_dedup_parity_for_job_runs_vm() -> None:
    """The dedup-on-set parity (commit c114b36) extends the PR
    #100(b) guarantee to JobRunsVM. Same acceptance contract."""
    fake = _InMemoryEmr()
    fake.add_application(app_id="a1", name="etl")
    fake.add_job_run(application_id="a1", job_run_id="r1", state=JobRunState.SUCCESS)
    fake.add_job_run(application_id="a1", job_run_id="r2", state=JobRunState.RUNNING)
    hub = _hub()
    vm = JobRunsVM(client=fake, hub=hub, dispatcher=NULL_DISPATCHER)
    vm.construct()
    try:
        vm.set_application("a1")
        await vm.refresh()
        events: list[str] = []
        sub = vm.on_property_changed.subscribe(on_next=events.append)
        try:
            await vm.refresh()  # same page
            assert "runs" not in events
            assert "selected_id" not in events
        finally:
            sub.dispose()
    finally:
        vm.dispose()


# -------------------- PR #103: cross-VM hub-event isolation --------------------


@pytest.mark.asyncio
async def test_pr_103_two_emr_vms_on_same_hub_have_isolated_observables() -> None:
    """The §9.bis.9 PR #103 structural assertion: constructing two
    EMR VMs on the same MessageHub and triggering events on one
    must NOT cause its per-VM Observable to fire events on the
    other VM's Observable. This is the contract that makes
    `sender_object` filtering unnecessary in the View."""
    hub = _hub()
    fake1 = _InMemoryEmr()
    fake1.add_application(app_id="a1", name="etl")
    fake1.add_job_run(application_id="a1", job_run_id="r1", state=JobRunState.RUNNING)
    vm_runs_1 = JobRunsVM(client=fake1, hub=hub, dispatcher=NULL_DISPATCHER)
    vm_runs_1.construct()

    fake2 = _InMemoryEmr()
    fake2.add_application(app_id="b1", name="other")
    vm_runs_2 = JobRunsVM(client=fake2, hub=hub, dispatcher=NULL_DISPATCHER)
    vm_runs_2.construct()

    try:
        events_on_vm1: list[str] = []
        sub = vm_runs_1.on_property_changed.subscribe(on_next=events_on_vm1.append)
        try:
            vm_runs_2.set_application("b1")
            await vm_runs_2.refresh()
            # vm_runs_2's refresh emitted state + runs on ITS
            # Observable. vm_runs_1's Observable must stay silent.
            assert events_on_vm1 == []
        finally:
            sub.dispose()
    finally:
        vm_runs_1.dispose()
        vm_runs_2.dispose()


@pytest.mark.asyncio
async def test_pr_103_applications_vm_isolated_from_job_runs_vm() -> None:
    """Same isolation contract across the heterogeneous VM pair —
    a JobRunsVM event must not echo on ApplicationsVM's Observable."""
    hub = _hub()
    fake = _InMemoryEmr()
    fake.add_application(app_id="a1", name="etl")
    fake.add_job_run(application_id="a1", job_run_id="r1", state=JobRunState.RUNNING)
    apps_vm = ApplicationsVM(client=fake, hub=hub, dispatcher=NULL_DISPATCHER)
    apps_vm.construct()
    runs_vm = JobRunsVM(client=fake, hub=hub, dispatcher=NULL_DISPATCHER)
    runs_vm.construct()
    try:
        events_on_apps: list[str] = []
        sub = apps_vm.on_property_changed.subscribe(on_next=events_on_apps.append)
        try:
            runs_vm.set_application("a1")
            await runs_vm.refresh()
            assert events_on_apps == []  # JobRuns events stay on JobRuns
        finally:
            sub.dispose()
    finally:
        apps_vm.dispose()
        runs_vm.dispose()


# -------------------- PR #98(3): FocusCoordinator drives -rail-active --------------------


def test_pr_98_3_focus_coordinator_drives_nav_menu_slot() -> None:
    """Acceptance: the FocusCoordinatorVM owns the `focused_slot`
    discriminator that the View's `-rail-active` Screen class
    subscribes to. Setting the slot to NAV_MENU emits an event
    that the (mocked) View consumer observes; setting to anything
    else demotes it. Demonstrates the round-3 pattern from
    spec §4.3."""
    # Start on S3_LEFT so the first set_focused_slot(NAV_MENU) actually
    # fires an event — initial=NAV_MENU would no-op that call.
    coord = FocusCoordinatorVM(hub=_hub(), dispatcher=NULL_DISPATCHER, initial=FocusSlot.S3_LEFT)
    coord.construct()
    try:
        observed: list[FocusSlot] = []
        sub = coord.on_focused_slot_changed.subscribe(on_next=observed.append)
        try:
            coord.set_focused_slot(FocusSlot.NAV_MENU)
            coord.set_focused_slot(FocusSlot.S3_RIGHT)
            assert observed == [FocusSlot.NAV_MENU, FocusSlot.S3_RIGHT]
        finally:
            sub.dispose()
    finally:
        coord.dispose()


def test_pr_98_3_modal_open_preserves_prior_slot() -> None:
    """Modal precedence: opening a modal while NavMenu has the
    slot saves NavMenu and promotes MODAL. Closing restores
    NavMenu. The Screen's `-rail-active` class should follow."""
    coord = FocusCoordinatorVM(hub=_hub(), dispatcher=NULL_DISPATCHER, initial=FocusSlot.NAV_MENU)
    coord.construct()
    try:
        coord.modal_open()
        assert coord.focused_slot is FocusSlot.MODAL
        coord.modal_close()
        assert coord.focused_slot is FocusSlot.NAV_MENU  # restored
    finally:
        coord.dispose()


# -------------------- PR #105: uniform selection (NavRow) --------------------


def test_pr_105_nav_menu_selection_is_singular() -> None:
    """Acceptance: at any moment exactly ONE NavItemVM has
    `is_selected = True` (or zero before any selection). The
    composite's `current` slot drives this — there's no separate
    per-item bool to drift."""
    from aws_tui.infra.connection_resolver import Connection
    from aws_tui.vm.nav_menu_vm import NavMenuVM
    from aws_tui.vm.services_protocol import ServiceDescriptor, ServiceRegistry

    class _Service:
        def __init__(self, sid: str, label: str) -> None:
            self.descriptor = ServiceDescriptor(id=sid, label=label, icon=sid[0])

        def supports(self, _connection: object) -> bool:
            return True

    reg = ServiceRegistry()
    reg.register(_Service("s3", "S3"))  # type: ignore[arg-type]
    reg.register(_Service("ec2", "EC2"))  # type: ignore[arg-type]
    hub = _hub()
    menu = NavMenuVM(registry=reg, hub=hub, dispatcher=NULL_DISPATCHER)
    menu.construct()
    try:
        menu.update_connection(Connection(name="aws", kind="aws", region="us-east-1", source="env"))
        menu.switch_service_command.execute("s3")
        selected = [it for it in menu.items if it.is_selected]
        assert len(selected) == 1
        assert selected[0].descriptor.id == "s3"
        menu.switch_service_command.execute("ec2")
        selected = [it for it in menu.items if it.is_selected]
        assert len(selected) == 1
        assert selected[0].descriptor.id == "ec2"
    finally:
        menu.dispose()
