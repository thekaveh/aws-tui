"""Athena collaborators as VMx components (#557).

Each collaborator derives from IsolatedComponentVM, publishes named properties
on the hub and locally, and survives a failing subscriber or a hostile hub
observer. These tests pin that contract for the three Athena collaborators.
"""

from __future__ import annotations

import inspect
import logging

import pytest
from vmx import MessageHub, PropertyChangedMessage
from vmx.lifecycle.status import ConstructionStatus

from aws_tui.vm._component import IsolatedComponentVM
from aws_tui.vm.athena.authoring_vm import AthenaAuthoringVM
from aws_tui.vm.athena.completion_vm import AthenaCompletionVM
from aws_tui.vm.athena.export_vm import AthenaExportVM
from tests.unit.vm.athena.test_authoring_vm import make_authoring, yes
from tests.unit.vm.athena.test_completion_vm import make as make_completion
from tests.unit.vm.athena.test_export_vm import make_vm as make_export

COLLABORATORS = (AthenaAuthoringVM, AthenaCompletionVM, AthenaExportVM)


def _sent_names(messages: list[object], sender: str) -> set[str]:
    return {
        message.property_name
        for message in messages
        if isinstance(message, PropertyChangedMessage) and message.sender_name == sender
    }


def test_component_base_collaborators_derive_from_isolated_component_without_callbacks() -> None:
    for cls in COLLABORATORS:
        assert issubclass(cls, IsolatedComponentVM)
        parameters = inspect.signature(cls.__init__).parameters
        assert "notify" not in parameters
        assert "is_current" not in parameters


@pytest.mark.asyncio
async def test_collaborator_lifecycle_reaches_constructed_and_disposed(tmp_path) -> None:
    authoring, _query, _client = make_authoring(tmp_path)
    completion, _query, _fake = make_completion(tmp_path)
    export, _results, _context = make_export()
    for vm in (authoring, completion, export):
        vm.construct()
        assert vm.status is ConstructionStatus.CONSTRUCTED
        vm.dispose()
        assert vm.status is ConstructionStatus.DISPOSED


@pytest.mark.asyncio
async def test_per_property_authoring_open_publishes_names_locally_and_on_hub(tmp_path) -> None:
    hub: MessageHub = MessageHub()
    sent: list[object] = []
    hub_subscription = hub.messages.subscribe(on_next=sent.append)
    vm, _query, _client = make_authoring(tmp_path, hub=hub)
    local: list[str] = []
    local_subscription = vm.on_property_changed.subscribe(local.append)
    path = tmp_path / "query.sql"
    path.write_text("SELECT 2")
    try:
        assert await vm.open_sql(path, confirm=yes)
    finally:
        local_subscription.dispose()
        hub_subscription.dispose()
    expected = {"path", "dirty", "ownership_revision", "replacement_revision", "status_message"}
    assert expected <= set(local)
    assert expected <= _sent_names(sent, "athena.authoring")


@pytest.mark.asyncio
async def test_per_property_completion_publishes_busy_state_and_message(tmp_path) -> None:
    hub: MessageHub = MessageHub()
    sent: list[object] = []
    hub_subscription = hub.messages.subscribe(on_next=sent.append)
    vm, query, _fake = make_completion(tmp_path, hub=hub)
    local: list[str] = []
    local_subscription = vm.on_property_changed.subscribe(local.append)
    query.set_sql("SELECT amount FROM ev")
    try:
        await vm.complete(len(query.sql))
    finally:
        local_subscription.dispose()
        hub_subscription.dispose()
    expected = {"busy", "state", "message"}
    assert expected <= set(local)
    assert expected <= _sent_names(sent, "athena.completion")


@pytest.mark.asyncio
async def test_per_property_export_publishes_busy_review_receipt_and_status(tmp_path) -> None:
    hub: MessageHub = MessageHub()
    sent: list[object] = []
    hub_subscription = hub.messages.subscribe(on_next=sent.append)
    vm, _results, _context = make_export(hub=hub)
    local: list[str] = []
    local_subscription = vm.on_property_changed.subscribe(local.append)
    try:
        review = await vm.prepare(scope="loaded", format="csv", path=tmp_path / "result")
        assert review is not None
        receipt = await vm.run(review)
        assert receipt is not None
    finally:
        local_subscription.dispose()
        hub_subscription.dispose()
    expected = {"busy", "review", "receipt", "status_message"}
    assert expected <= set(local)
    assert expected <= _sent_names(sent, "athena.exports")


@pytest.mark.asyncio
async def test_raising_subscriber_stops_neither_later_subscribers_nor_the_action(tmp_path) -> None:
    vm, _query, _client = make_authoring(tmp_path)

    def failing(_name: str) -> None:
        raise RuntimeError("subscriber failure")

    recorded: list[str] = []
    vm.on_property_changed.subscribe(failing)
    vm.on_property_changed.subscribe(recorded.append)
    path = tmp_path / "query.sql"
    path.write_text("SELECT 2")

    assert await vm.open_sql(path, confirm=yes)
    assert "path" in recorded
    assert "status_message" in recorded


@pytest.mark.asyncio
async def test_hostile_hub_subscriber_text_reaches_no_log_record(
    tmp_path, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    marker = "HOSTILE_HUB_ATHENA_COLLABORATOR_MARKER"
    hub: MessageHub = MessageHub()

    def hostile(_message: object) -> None:
        raise RuntimeError(marker)

    hub_subscription = hub.messages.subscribe(on_next=hostile)
    vm, _query, _client = make_authoring(tmp_path, hub=hub)
    path = tmp_path / "query.sql"
    path.write_text("SELECT 2")
    try:
        assert await vm.open_sql(path, confirm=yes)
    finally:
        hub_subscription.dispose()
    assert marker not in caplog.text


@pytest.mark.asyncio
async def test_raising_subscriber_completion_stops_neither_later_subscribers_nor_the_action(
    tmp_path,
) -> None:
    vm, query, _fake = make_completion(tmp_path)

    def failing(_name: str) -> None:
        raise RuntimeError("subscriber failure")

    recorded: list[str] = []
    vm.on_property_changed.subscribe(failing)
    vm.on_property_changed.subscribe(recorded.append)
    query.set_sql("SELECT amount FROM ev")

    batch = await vm.complete(len(query.sql))

    assert batch is not None
    assert "state" in recorded
    assert "busy" in recorded


@pytest.mark.asyncio
async def test_hostile_hub_completion_text_reaches_no_log_record(
    tmp_path, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    marker = "HOSTILE_HUB_COMPLETION_COLLABORATOR_MARKER"
    hub: MessageHub = MessageHub()

    def hostile(_message: object) -> None:
        raise RuntimeError(marker)

    hub_subscription = hub.messages.subscribe(on_next=hostile)
    vm, query, _fake = make_completion(tmp_path, hub=hub)
    query.set_sql("SELECT amount FROM ev")
    try:
        await vm.complete(len(query.sql))
    finally:
        hub_subscription.dispose()
    assert marker not in caplog.text
