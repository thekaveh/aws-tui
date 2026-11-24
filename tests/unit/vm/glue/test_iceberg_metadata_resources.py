"""Metadata panes owned by AsyncResourceVM (#553).

The pinned cancellation contracts live in ``test_iceberg_vm.py``. These tests
cover what the resource ownership adds: a refused or cancelled read restores
the settled page, a rebind clears every pane, and the resources stay off the
shared hub.
"""

from __future__ import annotations

import asyncio
from dataclasses import replace

import pytest
from vmx import PropertyChangedMessage

from aws_tui.domain.data_catalog import TableFormat
from aws_tui.domain.filesystem import PermissionDeniedError
from aws_tui.vm.file_manager.pane_vm import PaneState
from tests.unit.vm.glue.test_iceberg_vm import ICEBERG_REF, make_vm

OTHER_REF = replace(ICEBERG_REF, table_name="other_events")
VIEWS = ("snapshots", "history", "manifests", "files", "partitions", "refs")


async def _refuse(_plan: object) -> bool:
    return False


@pytest.mark.asyncio
async def test_refused_plan_restores_an_unloaded_pane_without_a_provider_call() -> None:
    vm, inspector, _hub = make_vm()
    await vm.bind_table(ICEBERG_REF, table_format=TableFormat.ICEBERG)
    release = vm.set_plan_presenter(_refuse)
    try:
        assert not await vm.select_view("history")
    finally:
        release()

    assert inspector.calls == []
    assert vm.state_for("history") is PaneState.EMPTY
    assert vm.error_text_for("history") is None
    assert vm.coverage_for("history") is None
    assert vm.observed_at_for("history") is None


@pytest.mark.asyncio
async def test_refused_reload_keeps_a_loaded_pane_at_its_settled_page() -> None:
    vm, inspector, _hub = make_vm()
    await vm.bind_table(ICEBERG_REF, table_format=TableFormat.ICEBERG)
    assert await vm.select_view("history")
    rows = vm.history
    coverage = vm.coverage_for("history")
    observed = vm.observed_at_for("history")

    release = vm.set_plan_presenter(_refuse)
    try:
        assert not await vm.retry()
    finally:
        release()

    assert len(inspector.calls) == 1
    assert vm.history == rows
    assert vm.coverage_for("history") == coverage
    assert vm.observed_at_for("history") == observed
    assert vm.state_for("history") is PaneState.IDLE


@pytest.mark.asyncio
async def test_cancelled_reload_restores_rows_coverage_observation_and_state() -> None:
    vm, inspector, _hub = make_vm()
    await vm.bind_table(ICEBERG_REF, table_format=TableFormat.ICEBERG)
    assert await vm.select_view("files")
    before = (vm.files, vm.coverage_for("files"), vm.observed_at_for("files"))

    started = inspector.block("files")
    reload = asyncio.create_task(vm.retry())
    await started.wait()
    assert vm.state_for("files") is PaneState.LOADING
    assert vm.files == before[0]

    reload.cancel()
    with pytest.raises(asyncio.CancelledError):
        await reload

    assert (vm.files, vm.coverage_for("files"), vm.observed_at_for("files")) == before
    assert vm.state_for("files") is PaneState.IDLE
    assert vm.error_text_for("files") is None


@pytest.mark.asyncio
async def test_cancelled_reload_after_a_failure_restores_the_failed_state_and_rows() -> None:
    vm, inspector, _hub = make_vm()
    await vm.bind_table(ICEBERG_REF, table_format=TableFormat.ICEBERG)
    assert await vm.select_view("files")
    rows = vm.files

    inspector.errors["files"] = PermissionDeniedError("denied")
    assert not await vm.retry()
    assert vm.state_for("files") is PaneState.FORBIDDEN
    assert vm.error_text_for("files") == "Iceberg metadata access denied"
    assert vm.files == rows

    inspector.errors.pop("files")
    started = inspector.block("files")
    reload = asyncio.create_task(vm.retry())
    await started.wait()
    reload.cancel()
    with pytest.raises(asyncio.CancelledError):
        await reload

    assert vm.state_for("files") is PaneState.FORBIDDEN
    assert vm.error_text_for("files") == "Iceberg metadata access denied"
    assert vm.files == rows


@pytest.mark.asyncio
async def test_cancelled_reload_keeps_the_paging_position() -> None:
    vm, inspector, _hub = make_vm(page_size=2)
    await vm.bind_table(ICEBERG_REF, table_format=TableFormat.ICEBERG)
    assert await vm.select_view("manifests")
    assert len(vm.manifests) == 2
    assert await vm.load_more()
    assert len(vm.manifests) == 4

    started = inspector.block("manifests")
    reload = asyncio.create_task(vm.retry())
    await started.wait()
    reload.cancel()
    with pytest.raises(asyncio.CancelledError):
        await reload

    assert len(vm.manifests) == 4
    assert vm.has_more
    assert vm.state_for("manifests") is PaneState.IDLE


@pytest.mark.asyncio
async def test_rebind_empties_every_pane_and_drops_a_late_result() -> None:
    vm, inspector, _hub = make_vm()
    await vm.bind_table(ICEBERG_REF, table_format=TableFormat.ICEBERG)
    assert await vm.select_view("snapshots")
    assert await vm.select_view("refs")

    # The provider ignores cancellation, so its rows arrive after the rebind.
    inspector.ignore_cancellation_for = "history"
    started = inspector.block("history")
    late = asyncio.create_task(vm.select_view("history"))
    await started.wait()

    await vm.bind_table(OTHER_REF, table_format=TableFormat.ICEBERG)
    for view in VIEWS:
        assert vm.state_for(view) is PaneState.EMPTY
        assert not vm.has_more_for(view)

    inspector.release("history")
    assert not await late
    assert vm.state_for("history") is PaneState.EMPTY
    assert vm.history == ()


@pytest.mark.asyncio
async def test_pane_resources_never_send_a_shared_hub_message() -> None:
    vm, inspector, hub = make_vm()
    messages: list[object] = []
    subscription = hub.messages.subscribe(on_next=messages.append)
    try:
        await vm.bind_table(ICEBERG_REF, table_format=TableFormat.ICEBERG)
        assert await vm.select_view("snapshots")

        started = inspector.block("history")
        late = asyncio.create_task(vm.select_view("history"))
        await started.wait()
        reload = asyncio.create_task(vm.retry())
        await asyncio.sleep(0)
        reload.cancel()
        with pytest.raises(asyncio.CancelledError):
            await reload
        await vm.bind_table(OTHER_REF, table_format=TableFormat.ICEBERG)
        inspector.release("history")
        assert not await late
    finally:
        subscription.dispose()

    assert messages, "the VM itself must still announce its own property changes"
    senders = {
        message.sender_name for message in messages if isinstance(message, PropertyChangedMessage)
    }
    assert senders == {"glue.iceberg"}
