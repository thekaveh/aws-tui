"""EMR dashboard collaborator as a VMx component (#557).

The dashboard publishes busy, delivery_status and outcome on the hub and
locally. A failing subscriber or a hostile hub observer cannot break a
delivery, and its text reaches no log record.
"""

from __future__ import annotations

import inspect
import logging

import pytest
from vmx import MessageHub, PropertyChangedMessage
from vmx.lifecycle.status import ConstructionStatus

from aws_tui.vm._component import IsolatedComponentVM
from aws_tui.vm.emr_serverless.dashboard_vm import DashboardVM
from tests.unit.vm.emr_serverless.test_dashboard_vm import perform, setup_vm


def test_component_base_dashboard_derives_from_isolated_component_without_callbacks() -> None:
    assert issubclass(DashboardVM, IsolatedComponentVM)
    parameters = inspect.signature(DashboardVM.__init__).parameters
    assert "notify" not in parameters
    assert "is_current" not in parameters


@pytest.mark.asyncio
async def test_collaborator_lifecycle_dashboard_reaches_constructed_and_disposed() -> None:
    vm, _current, _client, _browser, _copy = setup_vm()
    vm.construct()
    assert vm.status is ConstructionStatus.CONSTRUCTED
    vm.dispose()
    assert vm.status is ConstructionStatus.DISPOSED


@pytest.mark.asyncio
async def test_per_property_dashboard_publishes_busy_status_and_outcome() -> None:
    hub: MessageHub = MessageHub()
    sent: list[object] = []
    hub_subscription = hub.messages.subscribe(on_next=sent.append)
    vm, current, _client, browser, copy = setup_vm(hub=hub)
    local: list[str] = []
    local_subscription = vm.on_property_changed.subscribe(local.append)
    try:
        result = await perform(vm, current, browser, copy)
    finally:
        local_subscription.dispose()
        hub_subscription.dispose()
    assert result.status == "opened"
    expected = {"busy", "delivery_status", "outcome"}
    assert expected <= set(local)
    sent_names = {
        message.property_name
        for message in sent
        if isinstance(message, PropertyChangedMessage) and message.sender_name == "emr.dashboard"
    }
    assert expected <= sent_names


@pytest.mark.asyncio
async def test_raising_subscriber_dashboard_stops_neither_later_subscribers_nor_delivery() -> None:
    vm, current, _client, browser, copy = setup_vm()

    def failing(_name: str) -> None:
        raise RuntimeError("subscriber failure")

    recorded: list[str] = []
    vm.on_property_changed.subscribe(failing)
    vm.on_property_changed.subscribe(recorded.append)
    result = await perform(vm, current, browser, copy)
    assert result.status == "opened"
    assert "outcome" in recorded
    browser.open.assert_called_once()


@pytest.mark.asyncio
async def test_hostile_hub_dashboard_text_reaches_no_log_record(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    marker = "HOSTILE_HUB_DASHBOARD_COLLABORATOR_MARKER"
    hub: MessageHub = MessageHub()

    def hostile(_message: object) -> None:
        raise RuntimeError(marker)

    hub_subscription = hub.messages.subscribe(on_next=hostile)
    vm, current, _client, browser, copy = setup_vm(hub=hub)
    try:
        result = await perform(vm, current, browser, copy)
    finally:
        hub_subscription.dispose()
    assert result.status == "opened"
    assert marker not in caplog.text
