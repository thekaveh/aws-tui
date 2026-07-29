from __future__ import annotations

import asyncio
import inspect
import logging
from dataclasses import replace
from datetime import UTC, datetime, timedelta, timezone, tzinfo
from typing import Any, cast

import pytest
from vmx import NULL_DISPATCHER, MessageHub, PropertyChangedMessage
from vmx.lifecycle.status import ConstructionStatus
from vmx.messages.protocols import Message

from aws_tui.domain.data_catalog import TableFormat, TableRef
from aws_tui.domain.filesystem import PermissionDeniedError, ProviderError
from aws_tui.domain.iceberg import (
    IcebergDataFile,
    IcebergHistoryEntry,
    IcebergManifest,
    IcebergPartition,
    IcebergReference,
    IcebergSnapshot,
)
from aws_tui.vm.file_manager.pane_vm import PaneState
from aws_tui.vm.glue.catalog_vm import GlueCatalogVM
from aws_tui.vm.glue.iceberg_vm import GlueIcebergVM
from aws_tui.vm.messages import OpenAthenaTableRequest
from tests.unit.vm.glue._fake_glue import InMemoryGlue

NOW = datetime(2026, 7, 28, 12, tzinfo=UTC)
ICEBERG_REF = TableRef(
    "AwsDataCatalog",
    "analytics",
    "events",
    "dev",
    "us-east-1",
)
OTHER_REF = replace(ICEBERG_REF, table_name="sessions")
THIRD_REF = replace(ICEBERG_REF, table_name="accounts")


def _snapshot(snapshot_id: int, *, age: int = 0) -> IcebergSnapshot:
    return IcebergSnapshot(
        committed_at=NOW - timedelta(minutes=age),
        snapshot_id=snapshot_id,
        parent_id=snapshot_id - 1,
        operation="append",
        manifest_list=f"s3://warehouse/metadata/snap-{snapshot_id}.avro",
        summary=(("added-records", "10"),),
    )


class RecordingInspector:
    def __init__(self) -> None:
        self.calls: list[tuple[str, TableRef]] = []
        self.snapshots = (_snapshot(41, age=2), _snapshot(43), _snapshot(42, age=1))
        self.history = (
            IcebergHistoryEntry(NOW - timedelta(minutes=1), 42, 41, True),
            IcebergHistoryEntry(NOW, 43, 42, True),
        )
        self.manifests = tuple(
            IcebergManifest(
                f"s3://warehouse/metadata/manifest-{index}.avro",
                100 + index,
                0,
                43,
                1,
                0,
                0,
                None,
            )
            for index in range(5)
        )
        self.files = (
            IcebergDataFile(
                0, "s3://warehouse/data/a.parquet", "PARQUET", 0, None, 10, 100, None, 0
            ),
        )
        self.partitions = (
            IcebergPartition(
                (("day", "2026-07-28"),),
                10,
                1,
                100,
                0,
                0,
                0,
                0,
                NOW,
                43,
            ),
        )
        self.refs = (IcebergReference("main", "BRANCH", 43, None, 2, 86_400_000),)
        self.errors: dict[str, Exception] = {}
        self.blocked: dict[str, tuple[asyncio.Event, asyncio.Event]] = {}
        self.ignore_cancellation_for: str | None = None
        self.cancellation_seen = asyncio.Event()

    def block(self, view: str) -> asyncio.Event:
        started = asyncio.Event()
        release = asyncio.Event()
        self.blocked[view] = (started, release)
        return started

    def release(self, view: str) -> None:
        self.blocked[view][1].set()

    async def _load(self, view: str, ref: TableRef) -> tuple[Any, ...]:
        self.calls.append((view, ref))
        if view in self.blocked:
            started, release = self.blocked[view]
            started.set()
            try:
                await release.wait()
            except asyncio.CancelledError:
                self.cancellation_seen.set()
                if self.ignore_cancellation_for != view:
                    raise
                await release.wait()
        if view in self.errors:
            raise self.errors[view]
        return tuple(getattr(self, view))

    async def list_snapshots(self, ref: TableRef) -> tuple[IcebergSnapshot, ...]:
        return await self._load("snapshots", ref)

    async def list_history(self, ref: TableRef) -> tuple[IcebergHistoryEntry, ...]:
        return await self._load("history", ref)

    async def list_manifests(self, ref: TableRef) -> tuple[IcebergManifest, ...]:
        return await self._load("manifests", ref)

    async def list_files(self, ref: TableRef) -> tuple[IcebergDataFile, ...]:
        return await self._load("files", ref)

    async def list_partitions(self, ref: TableRef) -> tuple[IcebergPartition, ...]:
        return await self._load("partitions", ref)

    async def list_refs(self, ref: TableRef) -> tuple[IcebergReference, ...]:
        return await self._load("refs", ref)


def make_vm(
    inspector: RecordingInspector | None = None,
    *,
    page_size: int = 2,
) -> tuple[GlueIcebergVM, RecordingInspector, MessageHub[Message]]:
    source = inspector or RecordingInspector()
    hub: MessageHub[Message] = MessageHub()
    vm = GlueIcebergVM(
        inspector=source,
        hub=hub,
        dispatcher=NULL_DISPATCHER,
        page_size=page_size,
    )
    vm.construct()
    return vm, source, hub


@pytest.mark.asyncio
async def test_bind_table_is_lazy_and_snapshots_load_newest_first() -> None:
    vm, inspector, _hub = make_vm()

    await vm.bind_table(ICEBERG_REF, table_format=TableFormat.ICEBERG)

    assert vm.available
    assert inspector.calls == []
    assert vm.state is PaneState.EMPTY

    await vm.select_view("snapshots")

    assert inspector.calls == [("snapshots", ICEBERG_REF)]
    assert [row.snapshot_id for row in vm.snapshots] == [43, 42]
    assert vm.has_more
    assert vm.state is PaneState.IDLE


@pytest.mark.asyncio
async def test_metadata_views_load_once_on_demand_and_retry_only_current_view() -> None:
    vm, inspector, _hub = make_vm()
    await vm.bind_table(ICEBERG_REF, table_format=TableFormat.ICEBERG)
    await vm.select_view("snapshots")
    await vm.select_view("refs")
    await vm.select_view("snapshots")

    assert [call[0] for call in inspector.calls] == ["snapshots", "refs"]

    await vm.retry()

    assert [call[0] for call in inspector.calls] == ["snapshots", "refs", "snapshots"]


@pytest.mark.asyncio
async def test_manifest_pagination_is_local_bounded_and_preserves_exact_records() -> None:
    vm, inspector, _hub = make_vm(page_size=2)
    await vm.bind_table(ICEBERG_REF, table_format=TableFormat.ICEBERG)
    await vm.select_view("manifests")

    assert vm.manifests == inspector.manifests[:2]
    assert vm.has_more

    await vm.load_more()
    await vm.load_more()
    await vm.load_more()

    assert vm.manifests == inspector.manifests
    assert not vm.has_more
    assert inspector.calls == [("manifests", ICEBERG_REF)]


@pytest.mark.asyncio
async def test_pane_failure_is_isolated_and_retry_does_not_erase_other_panes() -> None:
    vm, inspector, _hub = make_vm()
    await vm.bind_table(ICEBERG_REF, table_format=TableFormat.ICEBERG)
    await vm.select_view("refs")
    inspector.errors["snapshots"] = PermissionDeniedError("athena denied")

    await vm.select_view("snapshots")

    assert vm.state is PaneState.FORBIDDEN
    assert vm.error_text == "Iceberg metadata access denied"
    assert vm.refs == inspector.refs
    assert vm.state_for("refs") is PaneState.IDLE

    inspector.errors.pop("snapshots")
    await vm.retry()

    assert vm.state is PaneState.IDLE
    assert vm.refs == inspector.refs


@pytest.mark.asyncio
async def test_rebinding_discards_late_metadata_and_clears_snapshot_selection() -> None:
    vm, inspector, _hub = make_vm()
    await vm.bind_table(ICEBERG_REF, table_format=TableFormat.ICEBERG)
    started = inspector.block("snapshots")
    loading = asyncio.create_task(vm.select_view("snapshots"))
    await started.wait()

    await vm.bind_table(OTHER_REF, table_format=TableFormat.ICEBERG)

    cancelled_before_release = inspector.cancellation_seen.is_set()
    if not cancelled_before_release:
        inspector.release("snapshots")
    assert not await loading
    assert cancelled_before_release
    assert vm.table_ref == OTHER_REF
    assert vm.snapshots == ()
    assert vm.selected_snapshot_id is None
    assert vm.state is PaneState.EMPTY


@pytest.mark.asyncio
async def test_parent_database_change_cancels_and_drains_active_metadata_load() -> None:
    fake = InMemoryGlue()
    iceberg = fake.add_table("analytics", "events")
    fake.add_table("archive", "events")
    fake.table_details[iceberg.ref] = replace(
        fake.table_details[iceberg.ref],
        table_format=TableFormat.ICEBERG,
    )
    inspector = RecordingInspector()
    hub: MessageHub[Message] = MessageHub()
    catalog = GlueCatalogVM(
        client=fake,
        iceberg_inspector=inspector,
        hub=hub,
        dispatcher=NULL_DISPATCHER,
    )
    catalog.construct()
    await catalog.setup()
    await catalog.select_database("analytics")
    await catalog.select_table("events")
    started = inspector.block("snapshots")
    loading = asyncio.create_task(catalog.iceberg.select_view("snapshots"))
    await started.wait()

    await catalog.select_database("archive")

    cancelled_before_release = inspector.cancellation_seen.is_set()
    if not cancelled_before_release:
        inspector.release("snapshots")
    assert not await loading
    assert cancelled_before_release
    assert catalog.iceberg.table_ref is None
    assert catalog.iceberg.snapshots == ()


@pytest.mark.asyncio
async def test_shutdown_cancels_and_drains_metadata_load_idempotently() -> None:
    vm, inspector, _hub = make_vm()
    await vm.bind_table(ICEBERG_REF, table_format=TableFormat.ICEBERG)
    started = inspector.block("snapshots")
    loading = asyncio.create_task(vm.select_view("snapshots"))
    await started.wait()

    shutdown = getattr(vm, "shutdown", None)
    if shutdown is None:
        inspector.release("snapshots")
        await loading
        pytest.fail("GlueIcebergVM.shutdown is missing")
    await shutdown()
    await vm.shutdown()

    assert inspector.cancellation_seen.is_set()
    assert not await loading
    assert vm.table_ref is None
    assert vm.snapshots == ()
    assert not vm.available


@pytest.mark.asyncio
@pytest.mark.parametrize("termination_order", ["dispose-first", "concurrent"])
async def test_shutdown_durably_drains_after_dispose_without_notifications(
    termination_order: str,
) -> None:
    vm, inspector, hub = make_vm()
    inspector.ignore_cancellation_for = "snapshots"
    await vm.bind_table(ICEBERG_REF, table_format=TableFormat.ICEBERG)
    started = inspector.block("snapshots")
    loading = asyncio.create_task(vm.select_view("snapshots"))
    await started.wait()
    terminal_messages: list[Message] = []
    subscription = hub.messages.subscribe(on_next=terminal_messages.append)

    if termination_order == "dispose-first":
        vm.dispose()
        shutdown = asyncio.create_task(vm.shutdown())
    else:
        shutdown = asyncio.create_task(vm.shutdown())
        vm.dispose()
    await inspector.cancellation_seen.wait()
    shutdown_returned_before_provider = shutdown.done()
    inspector.release("snapshots")
    await shutdown
    assert not await loading

    await vm.shutdown()
    vm.dispose()

    assert not shutdown_returned_before_provider
    assert not any(isinstance(message, PropertyChangedMessage) for message in terminal_messages)
    assert vm.table_ref is None
    assert vm.snapshots == ()
    assert not vm._metadata_tasks  # type: ignore[attr-defined]
    subscription.dispose()


@pytest.mark.asyncio
async def test_clear_table_is_a_synchronous_immediate_invalidation_entry_point() -> None:
    vm, _inspector, _hub = make_vm()
    await vm.bind_table(ICEBERG_REF, table_format=TableFormat.ICEBERG)
    await vm.select_view("snapshots")

    result = vm.clear_table()
    if inspect.iscoroutine(result):
        result.close()

    assert result is None
    assert vm.table_ref is None
    assert vm.snapshots == ()
    assert not vm.available


@pytest.mark.asyncio
async def test_clear_table_and_drain_waits_for_cancellation_resistant_provider() -> None:
    vm, inspector, _hub = make_vm()
    inspector.ignore_cancellation_for = "snapshots"
    await vm.bind_table(ICEBERG_REF, table_format=TableFormat.ICEBERG)
    started = inspector.block("snapshots")
    loading = asyncio.create_task(vm.select_view("snapshots"))
    await started.wait()
    drain_method = getattr(vm, "clear_table_and_drain", None)
    if drain_method is None:
        vm.dispose()
        await inspector.cancellation_seen.wait()
        inspector.release("snapshots")
        await loading
        pytest.fail("GlueIcebergVM.clear_table_and_drain is missing")

    result = vm.clear_table()
    if inspect.iscoroutine(result):
        result.close()
    draining = asyncio.create_task(drain_method())
    await inspector.cancellation_seen.wait()
    drain_returned_before_provider = draining.done()

    assert vm.table_ref is None
    assert vm.snapshots == ()

    inspector.release("snapshots")
    await draining

    assert not drain_returned_before_provider
    assert not await loading
    assert not vm._metadata_tasks  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_silent_metadata_drain_preserves_stable_binding_and_pane() -> None:
    vm, inspector, _hub = make_vm(page_size=1)
    await vm.bind_table(ICEBERG_REF, table_format=TableFormat.ICEBERG)
    await vm.select_view("snapshots")
    await vm.load_more()
    assert vm.select_snapshot(42)
    baseline = (
        vm.table_ref,
        vm.snapshots,
        vm.state,
        vm.selected_snapshot_id,
        vm.can_time_travel_in_athena,
    )
    inspector.snapshots = (_snapshot(99),)
    inspector.ignore_cancellation_for = "snapshots"
    started = inspector.block("snapshots")
    retry = asyncio.create_task(vm.retry())
    await started.wait()
    notifications: list[str] = []
    subscription = vm.on_property_changed.subscribe(on_next=notifications.append)
    drain_method = getattr(vm, "cancel_metadata_loads_and_drain_silently", None)
    if drain_method is None:
        inspector.release("snapshots")
        await retry
        pytest.fail("GlueIcebergVM.cancel_metadata_loads_and_drain_silently is missing")

    draining = asyncio.create_task(drain_method())
    await inspector.cancellation_seen.wait()

    assert not draining.done()
    assert (
        vm.table_ref,
        vm.snapshots,
        vm.state,
        vm.selected_snapshot_id,
        vm.can_time_travel_in_athena,
    ) == baseline
    assert notifications == []

    inspector.release("snapshots")
    assert await asyncio.gather(draining, retry) == [None, False]

    assert (
        vm.table_ref,
        vm.snapshots,
        vm.state,
        vm.selected_snapshot_id,
        vm.can_time_travel_in_athena,
    ) == baseline
    assert notifications == []
    assert not vm._metadata_tasks  # type: ignore[attr-defined]
    subscription.dispose()


@pytest.mark.asyncio
async def test_silent_metadata_drain_rejects_retry_until_owned_load_is_drained() -> None:
    vm, inspector, _hub = make_vm()
    inspector.ignore_cancellation_for = "snapshots"
    await vm.bind_table(ICEBERG_REF, table_format=TableFormat.ICEBERG)
    started = inspector.block("snapshots")
    original = asyncio.create_task(vm.select_view("snapshots"))
    await started.wait()
    notifications: list[str] = []
    subscription = vm.on_property_changed.subscribe(on_next=notifications.append)

    draining = asyncio.create_task(vm.cancel_metadata_loads_and_drain_silently())
    await inspector.cancellation_seen.wait()
    retry = asyncio.create_task(vm.retry())
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    retry_completed_during_drain = retry.done()
    calls_during_drain = tuple(inspector.calls)
    notifications_during_drain = tuple(notifications)
    drain_returned_before_provider = draining.done()

    inspector.release("snapshots")
    results = await asyncio.gather(draining, original, retry)

    assert retry_completed_during_drain
    assert calls_during_drain == (("snapshots", ICEBERG_REF),)
    assert notifications_during_drain == ()
    assert not drain_returned_before_provider
    assert results == [None, False, False]
    assert vm.state is PaneState.EMPTY
    assert vm.snapshots == ()
    assert not vm._metadata_tasks  # type: ignore[attr-defined]
    assert notifications == []
    subscription.dispose()


@pytest.mark.asyncio
async def test_unloaded_view_selection_is_noop_while_silent_metadata_drain_is_held() -> None:
    vm, inspector, hub = make_vm()
    await vm.bind_table(ICEBERG_REF, table_format=TableFormat.ICEBERG)
    assert await vm.select_view("snapshots")
    assert vm.select_snapshot(43)
    inspector.ignore_cancellation_for = "snapshots"
    started = inspector.block("snapshots")
    retry = asyncio.create_task(vm.retry())
    await started.wait()
    draining = asyncio.create_task(vm.cancel_metadata_loads_and_drain_silently())
    await inspector.cancellation_seen.wait()
    property_notifications: list[str] = []
    messages: list[Message] = []
    property_subscription = vm.on_property_changed.subscribe(on_next=property_notifications.append)
    message_subscription = hub.messages.subscribe(on_next=messages.append)
    baseline = (
        vm.active_view,
        vm.selected_snapshot_id,
        vm.state,
        vm.error_text,
        vm.items,
        vm.has_more,
    )
    calls_before_selection = tuple(inspector.calls)

    try:
        result = await vm.select_view("refs")
        state_during_drain = (
            vm.active_view,
            vm.selected_snapshot_id,
            vm.state,
            vm.error_text,
            vm.items,
            vm.has_more,
        )
        calls_during_drain = tuple(inspector.calls)
        property_notifications_during_drain = tuple(property_notifications)
        messages_during_drain = tuple(messages)
        drain_was_held = not draining.done()
    finally:
        inspector.release("snapshots")
    drain_result, retry_result = await asyncio.gather(draining, retry)

    assert not result
    assert drain_was_held
    assert state_during_drain == baseline
    assert calls_during_drain == calls_before_selection
    assert property_notifications_during_drain == ()
    assert messages_during_drain == ()
    assert drain_result is None
    assert not retry_result
    assert (
        vm.active_view,
        vm.selected_snapshot_id,
        vm.state,
        vm.error_text,
        vm.items,
        vm.has_more,
    ) == baseline
    assert property_notifications == []
    assert messages == []
    property_subscription.dispose()
    message_subscription.dispose()


@pytest.mark.asyncio
async def test_metadata_barrier_rechecks_before_entering_loading(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    vm, inspector, _hub = make_vm()
    await vm.bind_table(ICEBERG_REF, table_format=TableFormat.ICEBERG)
    states_at_admission_check: list[PaneState] = []

    def close_barrier_after_entry() -> bool:
        states_at_admission_check.append(vm.state)
        return len(states_at_admission_check) == 1

    monkeypatch.setattr(vm, "_metadata_load_is_admitted", close_barrier_after_entry)
    notifications: list[str] = []
    subscription = vm.on_property_changed.subscribe(on_next=notifications.append)

    assert not await vm.retry()

    assert states_at_admission_check == [PaneState.EMPTY, PaneState.EMPTY]
    assert vm.state is PaneState.EMPTY
    assert inspector.calls == []
    assert notifications == []
    subscription.dispose()


@pytest.mark.asyncio
async def test_concurrent_silent_drains_hold_admission_until_last_lease(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    vm, inspector, _hub = make_vm()
    inspector.ignore_cancellation_for = "snapshots"
    await vm.bind_table(ICEBERG_REF, table_format=TableFormat.ICEBERG)
    started = inspector.block("snapshots")
    original_load = asyncio.create_task(vm.select_view("snapshots"))
    await started.wait()
    original_drain = vm._cancel_and_drain_metadata_tasks
    original_invalidate = vm._invalidate_metadata_loads_silently
    second_lease_acquired = asyncio.Event()
    second_drain_entered = asyncio.Event()
    second_drain_release = asyncio.Event()
    invalidations = 0
    drains = 0

    def record_invalidation() -> None:
        nonlocal invalidations
        invalidations += 1
        original_invalidate()
        if invalidations == 2:
            second_lease_acquired.set()

    async def control_second_drain() -> None:
        nonlocal drains
        drains += 1
        if drains == 2:
            second_drain_entered.set()
            await second_drain_release.wait()
        await original_drain()

    monkeypatch.setattr(vm, "_invalidate_metadata_loads_silently", record_invalidation)
    monkeypatch.setattr(vm, "_cancel_and_drain_metadata_tasks", control_second_drain)

    first_drain = asyncio.create_task(vm.cancel_metadata_loads_and_drain_silently())
    await inspector.cancellation_seen.wait()
    second_drain = asyncio.create_task(vm.cancel_metadata_loads_and_drain_silently())
    await second_lease_acquired.wait()
    inspector.release("snapshots")
    await second_drain_entered.wait()

    calls_before_retry = tuple(inspector.calls)
    assert not await vm.retry()
    assert tuple(inspector.calls) == calls_before_retry

    second_drain_release.set()
    first_result, second_result, load_result = await asyncio.gather(
        first_drain,
        second_drain,
        original_load,
    )
    assert first_result is None
    assert second_result is None
    assert not load_result
    assert await vm.retry()
    assert tuple(inspector.calls) == (
        ("snapshots", ICEBERG_REF),
        ("snapshots", ICEBERG_REF),
    )


@pytest.mark.asyncio
async def test_silent_snapshot_restore_preserves_selection_cleared_by_view_switch() -> None:
    vm, inspector, _hub = make_vm()
    await vm.bind_table(ICEBERG_REF, table_format=TableFormat.ICEBERG)
    await vm.select_view("snapshots")
    assert vm.select_snapshot(43)
    inspector.ignore_cancellation_for = "snapshots"
    started = inspector.block("snapshots")
    retry = asyncio.create_task(vm.retry())
    await started.wait()

    assert await vm.select_view("history")
    assert vm.selected_snapshot_id is None
    notifications: list[str] = []
    subscription = vm.on_property_changed.subscribe(on_next=notifications.append)
    draining = asyncio.create_task(vm.cancel_metadata_loads_and_drain_silently())
    await inspector.cancellation_seen.wait()
    selection_during_drain = vm.selected_snapshot_id
    notifications_during_drain = tuple(notifications)

    inspector.release("snapshots")
    results = await asyncio.gather(draining, retry)

    assert results == [None, False]
    assert selection_during_drain is None
    assert vm.selected_snapshot_id is None
    assert notifications_during_drain == ()
    assert notifications == []
    subscription.dispose()


@pytest.mark.asyncio
async def test_synchronous_clear_supersedes_inflight_rebind() -> None:
    vm, inspector, _hub = make_vm()
    inspector.ignore_cancellation_for = "snapshots"
    await vm.bind_table(ICEBERG_REF, table_format=TableFormat.ICEBERG)
    started = inspector.block("snapshots")
    loading = asyncio.create_task(vm.select_view("snapshots"))
    await started.wait()

    rebinding = asyncio.create_task(vm.bind_table(OTHER_REF, table_format=TableFormat.ICEBERG))
    await inspector.cancellation_seen.wait()
    result = vm.clear_table()
    if inspect.iscoroutine(result):
        result.close()

    assert vm.table_ref is None
    assert vm.snapshots == ()

    inspector.release("snapshots")
    await rebinding

    assert not await loading
    assert vm.table_ref is None
    assert vm.snapshots == ()


@pytest.mark.asyncio
async def test_clear_from_invalidation_notification_supersedes_outer_bind() -> None:
    vm, _inspector, _hub = make_vm()
    await vm.bind_table(ICEBERG_REF, table_format=TableFormat.ICEBERG)
    cleared = False

    def clear_reentrantly(property_name: str) -> None:
        nonlocal cleared
        if property_name == "available" and vm.table_ref is None and not cleared:
            cleared = True
            vm.clear_table()

    subscription = vm.on_property_changed.subscribe(on_next=clear_reentrantly)

    await vm.bind_table(OTHER_REF, table_format=TableFormat.ICEBERG)

    assert cleared
    assert vm.table_ref is None
    assert not vm.available
    subscription.dispose()


@pytest.mark.asyncio
async def test_bind_from_invalidation_notification_prevents_outer_bind_publish() -> None:
    vm, _inspector, _hub = make_vm()
    await vm.bind_table(ICEBERG_REF, table_format=TableFormat.ICEBERG)
    rebound: asyncio.Task[None] | None = None
    published_refs: list[TableRef | None] = []

    def bind_reentrantly(property_name: str) -> None:
        nonlocal rebound
        if property_name == "available" and vm.table_ref is None and rebound is None:
            rebound = asyncio.create_task(
                vm.bind_table(THIRD_REF, table_format=TableFormat.ICEBERG)
            )
        if property_name == "table_ref":
            published_refs.append(vm.table_ref)

    subscription = vm.on_property_changed.subscribe(on_next=bind_reentrantly)

    await vm.bind_table(OTHER_REF, table_format=TableFormat.ICEBERG)
    assert rebound is not None
    await rebound

    assert vm.table_ref == THIRD_REF
    assert OTHER_REF not in published_refs
    subscription.dispose()


@pytest.mark.asyncio
async def test_shutdown_from_invalidation_notification_prevents_outer_bind_publish() -> None:
    vm, _inspector, _hub = make_vm()
    await vm.bind_table(ICEBERG_REF, table_format=TableFormat.ICEBERG)
    shutdown: asyncio.Task[None] | None = None
    published_refs: list[TableRef | None] = []

    def shutdown_reentrantly(property_name: str) -> None:
        nonlocal shutdown
        if property_name == "available" and vm.table_ref is None and shutdown is None:
            shutdown = asyncio.create_task(vm.shutdown())
        if property_name == "table_ref":
            published_refs.append(vm.table_ref)

    subscription = vm.on_property_changed.subscribe(on_next=shutdown_reentrantly)

    await vm.bind_table(OTHER_REF, table_format=TableFormat.ICEBERG)
    assert shutdown is not None
    await shutdown

    assert vm.table_ref is None
    assert OTHER_REF not in published_refs
    subscription.dispose()


@pytest.mark.asyncio
async def test_cancelled_load_rolls_back_without_stranding_loading_state() -> None:
    vm, inspector, _hub = make_vm()
    await vm.bind_table(ICEBERG_REF, table_format=TableFormat.ICEBERG)
    started = inspector.block("snapshots")
    loading = asyncio.create_task(vm.select_view("snapshots"))
    await started.wait()

    loading.cancel()
    with pytest.raises(asyncio.CancelledError):
        await loading

    assert vm.state is PaneState.EMPTY
    assert vm.snapshots == ()


@pytest.mark.asyncio
async def test_cancelled_retry_never_restores_ownerless_loading_state() -> None:
    vm, inspector, _hub = make_vm()
    await vm.bind_table(ICEBERG_REF, table_format=TableFormat.ICEBERG)
    started = inspector.block("snapshots")
    original = asyncio.create_task(vm.select_view("snapshots"))
    await started.wait()

    retry = asyncio.create_task(vm.retry())
    await asyncio.sleep(0)
    retry.cancel()
    with pytest.raises(asyncio.CancelledError):
        await retry

    assert vm.state is PaneState.EMPTY
    assert vm.snapshots == ()

    inspector.release("snapshots")
    assert not await original
    assert vm.state is PaneState.EMPTY


@pytest.mark.asyncio
@pytest.mark.parametrize("subscriber_kind", ["property", "hub"])
async def test_cancelled_loading_notification_restores_before_provider_call(
    subscriber_kind: str,
) -> None:
    vm, inspector, hub = make_vm()
    await vm.bind_table(ICEBERG_REF, table_format=TableFormat.ICEBERG)

    def cancel_notification(_value: object) -> None:
        raise asyncio.CancelledError

    subscription = (
        vm.on_property_changed.subscribe(on_next=cancel_notification)
        if subscriber_kind == "property"
        else hub.messages.subscribe(on_next=cancel_notification)
    )

    with pytest.raises(asyncio.CancelledError):
        await vm.select_view("snapshots")

    assert inspector.calls == []
    assert vm.state is PaneState.EMPTY
    assert vm.snapshots == ()
    subscription.dispose()
    vm.dispose()
    assert vm.status is ConstructionStatus.DISPOSED


@pytest.mark.asyncio
async def test_dispose_from_loading_notification_prevents_provider_task_creation() -> None:
    vm, inspector, _hub = make_vm()
    await vm.bind_table(ICEBERG_REF, table_format=TableFormat.ICEBERG)

    def dispose_on_loading(property_name: str) -> None:
        if property_name == "state" and vm.state is PaneState.LOADING:
            vm.dispose()

    subscription = vm.on_property_changed.subscribe(on_next=dispose_on_loading)

    assert not await vm.select_view("snapshots")
    assert inspector.calls == []
    assert vm.snapshots == ()
    assert not vm._metadata_tasks  # type: ignore[attr-defined]
    assert vm.status is ConstructionStatus.DISPOSED
    subscription.dispose()


@pytest.mark.asyncio
async def test_retry_supersedes_retry_and_only_latest_completion_can_publish() -> None:
    vm, inspector, _hub = make_vm()
    await vm.bind_table(ICEBERG_REF, table_format=TableFormat.ICEBERG)
    started = inspector.block("snapshots")
    first = asyncio.create_task(vm.retry())
    await started.wait()
    second = asyncio.create_task(vm.retry())
    await asyncio.sleep(0)

    inspector.release("snapshots")

    assert not await first
    assert await second
    assert vm.state is PaneState.IDLE
    assert [row.snapshot_id for row in vm.snapshots] == [43, 42]


@pytest.mark.asyncio
async def test_cancellation_resistant_provider_cannot_publish_late_rows() -> None:
    vm, inspector, _hub = make_vm()
    inspector.ignore_cancellation_for = "snapshots"
    await vm.bind_table(ICEBERG_REF, table_format=TableFormat.ICEBERG)
    started = inspector.block("snapshots")
    loading = asyncio.create_task(vm.select_view("snapshots"))
    await started.wait()

    loading.cancel()
    await inspector.cancellation_seen.wait()
    inspector.release("snapshots")
    with pytest.raises(asyncio.CancelledError):
        await loading

    assert vm.snapshots == ()
    assert vm.state is PaneState.EMPTY


@pytest.mark.asyncio
async def test_selected_snapshot_sends_exact_time_travel_request_without_provider_call() -> None:
    vm, inspector, hub = make_vm()
    received: list[OpenAthenaTableRequest] = []
    subscription = hub.messages.subscribe(
        on_next=lambda message: (
            received.append(message) if isinstance(message, OpenAthenaTableRequest) else None
        )
    )
    await vm.bind_table(ICEBERG_REF, table_format=TableFormat.ICEBERG)
    await vm.select_view("snapshots")

    assert vm.select_snapshot(43)
    calls_before = tuple(inspector.calls)
    assert vm.time_travel_in_athena()

    assert tuple(inspector.calls) == calls_before
    assert received == [OpenAthenaTableRequest(table_ref=ICEBERG_REF, snapshot_id=43)]
    subscription.dispose()


@pytest.mark.asyncio
async def test_cancelled_retry_restores_expanded_snapshot_page_and_selection() -> None:
    vm, inspector, _hub = make_vm(page_size=1)
    await vm.bind_table(ICEBERG_REF, table_format=TableFormat.ICEBERG)
    await vm.select_view("snapshots")
    await vm.load_more()
    assert vm.select_snapshot(42)
    started = inspector.block("snapshots")

    retry = asyncio.create_task(vm.retry())
    await started.wait()
    retry.cancel()
    with pytest.raises(asyncio.CancelledError):
        await retry

    assert [row.snapshot_id for row in vm.snapshots] == [43, 42]
    assert vm.selected_snapshot_id == 42
    assert vm.can_time_travel_in_athena


@pytest.mark.asyncio
async def test_failed_retry_preserves_expanded_rows_but_disables_time_travel() -> None:
    vm, inspector, _hub = make_vm(page_size=1)
    await vm.bind_table(ICEBERG_REF, table_format=TableFormat.ICEBERG)
    await vm.select_view("snapshots")
    await vm.load_more()
    assert vm.select_snapshot(42)
    inspector.errors["snapshots"] = PermissionDeniedError("denied")

    assert not await vm.retry()

    assert [row.snapshot_id for row in vm.snapshots] == [43, 42]
    assert vm.selected_snapshot_id == 42
    assert vm.state is PaneState.FORBIDDEN
    assert not vm.can_time_travel_in_athena
    assert not vm.time_travel_in_athena()


@pytest.mark.asyncio
async def test_snapshot_selection_requires_visible_active_stable_snapshot_pane() -> None:
    vm, _inspector, _hub = make_vm(page_size=1)
    await vm.bind_table(ICEBERG_REF, table_format=TableFormat.ICEBERG)
    await vm.select_view("snapshots")

    assert not vm.select_snapshot(42)
    assert vm.select_snapshot(43)
    assert vm.can_time_travel_in_athena

    await vm.select_view("history")

    assert vm.selected_snapshot_id is None
    assert not vm.can_time_travel_in_athena
    assert not vm.time_travel_in_athena()


@pytest.mark.asyncio
async def test_time_travel_is_noop_for_missing_stale_or_non_integer_snapshot() -> None:
    vm, _inspector, _hub = make_vm()
    await vm.bind_table(ICEBERG_REF, table_format=TableFormat.ICEBERG)

    assert not vm.select_snapshot(True)
    assert not vm.select_snapshot(999)
    assert not vm.time_travel_in_athena()


@pytest.mark.asyncio
async def test_bind_table_rejects_malformed_or_non_iceberg_context_without_calls() -> None:
    vm, inspector, _hub = make_vm()
    malformed = replace(ICEBERG_REF, region="")

    await vm.bind_table(malformed, table_format=TableFormat.ICEBERG)
    assert not vm.available
    await vm.bind_table(ICEBERG_REF, table_format=TableFormat.HIVE)
    assert not vm.available
    assert inspector.calls == []

    await vm.bind_table(ICEBERG_REF, table_format=TableFormat.ICEBERG)
    assert vm.available


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("view", "rows"),
    [
        ("snapshots", (replace(_snapshot(43), snapshot_id=-1),)),
        (
            "history",
            (IcebergHistoryEntry(NOW, 43, 42, cast(Any, 1)),),
        ),
        (
            "manifests",
            (IcebergManifest("", 1, 0, 43, 0, 0, 0, None),),
        ),
        (
            "files",
            (IcebergDataFile(True, "s3://data/a", "PARQUET", 0, None, 1, 1, None, 0),),
        ),
        (
            "partitions",
            (
                IcebergPartition(
                    (("day", object()),),
                    1,
                    1,
                    1,
                    None,
                    None,
                    None,
                    None,
                    NOW,
                    43,
                ),
            ),
        ),
        ("refs", (IcebergReference("main", "UNKNOWN", 43, None, None, None),)),
    ],
)
async def test_malformed_exact_metadata_records_are_rejected_before_publish(
    view: str,
    rows: tuple[object, ...],
) -> None:
    vm, inspector, _hub = make_vm()
    setattr(inspector, view, rows)
    await vm.bind_table(ICEBERG_REF, table_format=TableFormat.ICEBERG)

    assert not await vm.select_view(cast(Any, view))
    assert vm.state is PaneState.ERROR
    assert vm.items == ()
    assert vm.selected_snapshot_id is None
    assert not vm.time_travel_in_athena()

    await vm.bind_table(None)

    assert not vm.available
    assert not vm.time_travel_in_athena()


class _InvalidOffset(tzinfo):
    def utcoffset(self, _dt: datetime | None) -> timedelta:
        return timedelta(hours=24)

    def dst(self, _dt: datetime | None) -> timedelta:
        return timedelta(0)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("view", "rows"),
    [
        ("snapshots", (replace(_snapshot(43), committed_at=datetime(2026, 7, 28, 12)),)),
        (
            "history",
            (IcebergHistoryEntry(datetime(2026, 7, 28, 12), 43, 42, True),),
        ),
        (
            "partitions",
            (
                IcebergPartition(
                    (("day", "2026-07-28"),),
                    1,
                    1,
                    1,
                    None,
                    None,
                    None,
                    None,
                    datetime(2026, 7, 28, 12, tzinfo=_InvalidOffset()),
                    43,
                ),
            ),
        ),
    ],
)
async def test_metadata_rejects_naive_or_invalid_offset_datetimes_atomically(
    view: str,
    rows: tuple[object, ...],
) -> None:
    vm, inspector, _hub = make_vm()
    await vm.bind_table(ICEBERG_REF, table_format=TableFormat.ICEBERG)
    await vm.select_view(cast(Any, view))
    previous = vm.items
    setattr(inspector, view, rows)

    assert not await vm.retry()

    assert vm.items == previous
    assert vm.state is PaneState.ERROR


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "reference",
    [
        IcebergReference("tag", "TAG", 43, None, 1, None),
        IcebergReference("tag", "TAG", 43, None, None, 1),
        IcebergReference("branch", "BRANCH", 43, 0, None, None),
        IcebergReference("branch", "BRANCH", 43, None, -1, None),
        IcebergReference("branch", "BRANCH", 43, None, True, None),
        IcebergReference("branch", "BRANCH", 43, None, None, 0),
    ],
)
async def test_metadata_rejects_invalid_reference_retention_atomically(
    reference: IcebergReference,
) -> None:
    vm, inspector, _hub = make_vm()
    await vm.bind_table(ICEBERG_REF, table_format=TableFormat.ICEBERG)
    await vm.select_view("refs")
    previous = vm.refs
    inspector.refs = (reference,)

    assert not await vm.retry()

    assert vm.refs == previous
    assert vm.state is PaneState.ERROR


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "reference",
    [
        IcebergReference("tag", "TAG", 43, 86_400_000, None, None),
        IcebergReference("branch", "BRANCH", 43, None, None, None),
        IcebergReference("branch", "BRANCH", 43, 1, 1, 1),
    ],
)
async def test_metadata_accepts_spec_valid_reference_retention(
    reference: IcebergReference,
) -> None:
    vm, inspector, _hub = make_vm()
    inspector.refs = (reference,)
    await vm.bind_table(ICEBERG_REF, table_format=TableFormat.ICEBERG)

    assert await vm.select_view("refs")
    assert vm.refs == (reference,)


@pytest.mark.asyncio
async def test_metadata_accepts_aware_datetime_with_non_utc_offset() -> None:
    vm, inspector, _hub = make_vm()
    aware = datetime(2026, 7, 28, 12, tzinfo=timezone(timedelta(hours=5, minutes=30)))
    inspector.snapshots = (replace(_snapshot(43), committed_at=aware),)
    await vm.bind_table(ICEBERG_REF, table_format=TableFormat.ICEBERG)

    assert await vm.select_view("snapshots")
    assert vm.snapshots[0].committed_at is aware


@pytest.mark.asyncio
async def test_catalog_exposes_iceberg_only_for_detected_table_and_keeps_glue_detail() -> None:
    fake = InMemoryGlue()
    iceberg = fake.add_table("analytics", "events")
    parquet = fake.add_table("analytics", "sessions")
    fake.table_details[iceberg.ref] = replace(
        fake.table_details[iceberg.ref],
        table_format=TableFormat.ICEBERG,
    )
    inspector = RecordingInspector()
    hub: MessageHub[Message] = MessageHub()
    catalog = GlueCatalogVM(
        client=fake,
        iceberg_inspector=inspector,
        hub=hub,
        dispatcher=NULL_DISPATCHER,
    )
    catalog.construct()
    await catalog.setup()
    await catalog.select_database("analytics")

    await catalog.select_table(iceberg.ref.table_name)
    assert catalog.iceberg.available
    await catalog.iceberg.select_view("snapshots")
    assert catalog.table_detail is fake.table_details[iceberg.ref]

    await catalog.select_table(parquet.ref.table_name)

    assert not catalog.iceberg.available
    assert catalog.iceberg.snapshots == ()
    assert catalog.table_detail is fake.table_details[parquet.ref]
    assert catalog.table_detail.table_format is TableFormat.HIVE


@pytest.mark.asyncio
async def test_dispose_invalidates_late_results_and_completes_lifecycle() -> None:
    vm, inspector, _hub = make_vm()
    await vm.bind_table(ICEBERG_REF, table_format=TableFormat.ICEBERG)
    started = inspector.block("snapshots")
    loading = asyncio.create_task(vm.select_view("snapshots"))
    await started.wait()

    vm.dispose()
    inspector.release("snapshots")
    await loading

    assert vm.snapshots == ()
    assert not vm.available
    assert not await vm.select_view("refs")


@pytest.mark.asyncio
async def test_throwing_property_observer_cannot_interrupt_binding_loading_or_disposal() -> None:
    vm, _inspector, _hub = make_vm()
    received: list[str] = []

    def broken(property_name: str) -> None:
        raise RuntimeError(f"HOSTILE_OBSERVER_{property_name}")

    vm.on_property_changed.subscribe(on_next=broken)
    vm.on_property_changed.subscribe(on_next=received.append)

    await vm.bind_table(ICEBERG_REF, table_format=TableFormat.ICEBERG)
    await vm.select_view("refs")
    vm.dispose()

    assert vm.refs == ()
    assert "available" in received
    assert "refs" in received


@pytest.mark.parametrize("position", ["first", "middle"])
def test_dispose_isolates_cancelled_completion_observers_and_cleans_up(
    caplog: pytest.LogCaptureFixture,
    position: str,
) -> None:
    vm, _inspector, _hub = make_vm()
    completed: list[str] = []
    marker = "HOSTILE_ICEBERG_COMPLETION_CANCELLATION"

    def cancel() -> None:
        raise asyncio.CancelledError(marker)

    def fail() -> None:
        raise RuntimeError("HOSTILE_ICEBERG_COMPLETION_EXCEPTION")

    callbacks = (
        (cancel, fail, lambda: completed.append("remaining"))
        if position == "first"
        else (fail, cancel, lambda: completed.append("remaining"))
    )
    for callback in callbacks:
        vm.on_property_changed.subscribe(on_completed=callback)

    vm.dispose()
    vm.dispose()

    assert completed == ["remaining"]
    assert vm.status is ConstructionStatus.DISPOSED
    assert vm._on_property_changed._subject.is_disposed  # type: ignore[attr-defined]
    assert marker not in caplog.text
    assert "HOSTILE_ICEBERG_COMPLETION_EXCEPTION" not in caplog.text
    assert all(record.exc_info is None for record in caplog.records)


def test_dispose_propagates_process_control_after_terminal_cleanup() -> None:
    vm, _inspector, _hub = make_vm()

    def interrupt() -> None:
        raise KeyboardInterrupt

    vm.on_property_changed.subscribe(on_completed=interrupt)

    with pytest.raises(KeyboardInterrupt):
        vm.dispose()
    vm.dispose()

    assert vm.status is ConstructionStatus.DISPOSED
    assert vm._on_property_changed._subject.is_disposed  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_hostile_hub_subscriber_is_value_free_and_does_not_interrupt_state(
    caplog: pytest.LogCaptureFixture,
) -> None:
    vm, _inspector, hub = make_vm()
    marker = "HOSTILE_ICEBERG_HUB_VALUE"

    def broken(_message: Message) -> None:
        raise RuntimeError(marker)

    hub.messages.subscribe(on_next=broken)
    with caplog.at_level(logging.ERROR):
        await vm.bind_table(ICEBERG_REF, table_format=TableFormat.ICEBERG)
        await vm.select_view("refs")

    assert vm.refs
    assert marker not in caplog.text


@pytest.mark.asyncio
async def test_unexpected_inspector_error_is_value_free() -> None:
    vm, inspector, _hub = make_vm()
    inspector.errors["refs"] = RuntimeError("HOSTILE_ICEBERG_PROVIDER_VALUE")
    await vm.bind_table(ICEBERG_REF, table_format=TableFormat.ICEBERG)

    await vm.select_view("refs")

    assert vm.state is PaneState.ERROR
    assert vm.error_text == "Iceberg metadata request failed"


@pytest.mark.asyncio
async def test_provider_error_with_hostile_string_is_contained_and_value_free(
    caplog: pytest.LogCaptureFixture,
) -> None:
    marker = "HOSTILE_ICEBERG_PROVIDER_MARKER"

    class HostileProviderError(ProviderError):
        def __str__(self) -> str:
            raise RuntimeError(marker)

        def __repr__(self) -> str:
            return marker

    vm, inspector, _hub = make_vm()
    inspector.errors["snapshots"] = HostileProviderError()
    await vm.bind_table(ICEBERG_REF, table_format=TableFormat.ICEBERG)

    with caplog.at_level(logging.DEBUG):
        assert not await vm.select_view("snapshots")

    assert vm.state is PaneState.ERROR
    assert vm.error_text == "Iceberg metadata request failed"
    assert marker not in caplog.text
