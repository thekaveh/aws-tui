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
