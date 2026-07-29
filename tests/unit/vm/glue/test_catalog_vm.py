from __future__ import annotations

import asyncio
import traceback
from dataclasses import replace
from datetime import UTC, datetime

import pytest
from vmx import NULL_DISPATCHER, MessageHub, TokenPagedComposition
from vmx.messages.protocols import Message

from aws_tui.domain.data_catalog import TableFormat, TableRef
from aws_tui.domain.filesystem import PermissionDeniedError, ProviderError
from aws_tui.domain.iceberg import IcebergSnapshot
from aws_tui.vm.file_manager.pane_vm import PaneState
from aws_tui.vm.glue.catalog_vm import GlueCatalogVM
from aws_tui.vm.messages import OpenS3LocationRequest
from tests.unit.vm.glue._fake_glue import InMemoryGlue, seeded_glue


class CancellationResistantInspector:
    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.cancellation_seen = asyncio.Event()

    async def list_snapshots(self, _ref: TableRef) -> tuple[object, ...]:
        self.started.set()
        try:
            await self.release.wait()
        except asyncio.CancelledError:
            self.cancellation_seen.set()
            await self.release.wait()
        return ()


class RetryCancellationResistantInspector:
    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.cancellation_seen = asyncio.Event()
        self.calls = 0

    async def list_snapshots(self, _ref: TableRef) -> tuple[IcebergSnapshot, ...]:
        self.calls += 1
        if self.calls == 1:
            return (_snapshot(43), _snapshot(42))
        self.started.set()
        try:
            await self.release.wait()
        except asyncio.CancelledError:
            self.cancellation_seen.set()
            await self.release.wait()
        return (_snapshot(99),)


def _snapshot(snapshot_id: int) -> IcebergSnapshot:
    return IcebergSnapshot(
        committed_at=datetime(2026, 7, 28, 12, tzinfo=UTC),
        snapshot_id=snapshot_id,
        parent_id=snapshot_id - 1,
        operation="append",
        manifest_list=f"s3://warehouse/metadata/snap-{snapshot_id}.avro",
        summary=(("added-records", "10"),),
    )


def make_catalog_vm(
    fake: InMemoryGlue,
    *,
    iceberg_inspector: object | None = None,
) -> GlueCatalogVM:
    hub: MessageHub[Message] = MessageHub()
    vm = GlueCatalogVM(
        client=fake,
        iceberg_inspector=iceberg_inspector,  # type: ignore[arg-type]
        hub=hub,
        dispatcher=NULL_DISPATCHER,
    )
    vm.construct()
    return vm


def _catalog_state(vm: GlueCatalogVM) -> tuple[object, ...]:
    return (
        vm.selected_database_name,
        vm.selected_table_name,
        vm.table_detail,
        vm.column_statistics,
        vm.tables,
        vm.partitions,
        vm.databases_state,
        vm.tables_state,
        vm.detail_state,
        vm.partitions_state,
        vm.statistics_state,
        id(vm._table_pager),  # type: ignore[attr-defined]
        id(vm._partition_pager),  # type: ignore[attr-defined]
        vm.iceberg.table_ref,
        vm.iceberg.snapshots,
        vm.iceberg.state,
    )


def _catalog_selection_state(vm: GlueCatalogVM) -> tuple[object, ...]:
    return (
        vm.selected_database_name,
        vm.selected_table_name,
        vm.table_detail,
        vm.column_statistics,
        vm.tables,
        vm._table_pager.current_token,  # type: ignore[attr-defined]
        vm.has_more_tables,
        vm.partitions,
        vm._partition_pager.current_token,  # type: ignore[attr-defined]
        vm.has_more_partitions,
        vm.tables_state,
        vm.detail_state,
        vm.partitions_state,
        vm.statistics_state,
        vm.tables_error_text,
        vm.detail_error_text,
        vm.partitions_error_text,
        vm.statistics_error_text,
        id(vm._table_pager),  # type: ignore[attr-defined]
        id(vm._partition_pager),  # type: ignore[attr-defined]
    )


def _iceberg_state(vm: GlueCatalogVM) -> tuple[object, ...]:
    iceberg = vm.iceberg
    return (
        iceberg.available,
        iceberg.table_ref,
        iceberg.active_view,
        iceberg.snapshots,
        iceberg.history,
        iceberg.manifests,
        iceberg.files,
        iceberg.partitions,
        iceberg.refs,
        tuple(
            (
                iceberg.state_for(view),
                iceberg.error_text_for(view),
                iceberg.has_more_for(view),
            )
            for view in (
                "snapshots",
                "history",
                "manifests",
                "files",
                "partitions",
                "refs",
            )
        ),
        iceberg.selected_snapshot_id,
        iceberg.can_time_travel_in_athena,
    )


@pytest.mark.asyncio
async def test_catalog_uses_token_pagers_and_loads_more_at_each_level() -> None:
    fake = seeded_glue()
    fake.database_page_size = 1
    fake.table_page_size = 1
    fake.partition_page_size = 1
    fake.add_database("warehouse")
    vm = make_catalog_vm(fake)

    assert isinstance(vm._database_pager, TokenPagedComposition)  # type: ignore[attr-defined]
    assert isinstance(vm._table_pager, TokenPagedComposition)  # type: ignore[attr-defined]
    assert isinstance(vm._partition_pager, TokenPagedComposition)  # type: ignore[attr-defined]

    await vm.setup()
    assert vm.has_more_databases
    await vm.load_more_databases()
    assert fake.database_tokens == [None, "1"]

    await vm.select_database("analytics")
    assert len(vm.tables) == 1
    assert vm.has_more_tables
    await vm.load_more_tables()
    assert fake.table_requests[-1] == ("analytics", "1")

    await vm.select_table("events")
    assert vm.table_detail is not None
    assert vm.column_statistics
    assert len(vm.partitions) == 1
    assert vm.has_more_partitions
    await vm.load_more_partitions()
    assert fake.partition_requests[-1][1] == "1"


@pytest.mark.asyncio
async def test_refresh_notifies_database_pager_replacement_and_result() -> None:
    fake = seeded_glue()
    fake.add_database("warehouse")
    fake.database_page_size = 1
    vm = make_catalog_vm(fake)
    await vm.setup()
    notifications: list[str] = []
    subscription = vm.on_property_changed.subscribe(on_next=notifications.append)
    databases_started = fake.block_databases()

    refresh = asyncio.create_task(vm.refresh_databases())
    await databases_started.wait()

    assert not vm.has_more_databases
    assert notifications.count("has_more_databases") == 1

    fake.release_databases()
    await refresh

    assert vm.has_more_databases
    assert notifications.count("has_more_databases") == 2
    subscription.dispose()


@pytest.mark.asyncio
async def test_catalog_select_database_resets_tables_and_discards_stale_load() -> None:
    fake = InMemoryGlue()
    fake.add_table("a", "a-table")
    fake.add_table("b", "b-table")
    tables_started = fake.block_tables("a")
    vm = make_catalog_vm(fake)
    await vm.setup()

    first = asyncio.create_task(vm.select_database("a"))
    await tables_started.wait()
    await vm.select_database("b")
    fake.release_tables("a")
    await first

    assert vm.selected_database_name == "b"
    assert [row.ref.database_name for row in vm.tables] == ["b"]


@pytest.mark.asyncio
async def test_catalog_select_table_discards_stale_detail_and_partitions() -> None:
    fake = seeded_glue()
    events = next(row for row in fake.tables["analytics"] if row.ref.table_name == "events")
    detail_started = fake.block_table_detail(events.ref)
    vm = make_catalog_vm(fake)
    await vm.setup()
    await vm.select_database("analytics")

    first = asyncio.create_task(vm.select_table("events"))
    await detail_started.wait()
    await vm.select_table("sessions")
    fake.release_table_detail(events.ref)
    await first

    assert vm.selected_table_name == "sessions"
    assert vm.table_detail is not None
    assert vm.table_detail.summary.ref.table_name == "sessions"
    assert vm.partitions == ()


@pytest.mark.asyncio
async def test_empty_refresh_does_not_clear_newer_database_result_after_iceberg_drain() -> None:
    fake = seeded_glue()
    events = next(row for row in fake.tables["analytics"] if row.ref.table_name == "events")
    fake.add_table("analytics", "third")
    fake.add_partition(events.ref, "2026-07-26")
    fake.table_page_size = 1
    fake.partition_page_size = 1
    fake.table_details[events.ref] = replace(
        fake.table_details[events.ref],
        table_format=TableFormat.ICEBERG,
    )
    inspector = RetryCancellationResistantInspector()
    vm = make_catalog_vm(fake, iceberg_inspector=inspector)
    await vm.setup()
    await vm.select_database("analytics")
    await vm.select_table("events")
    await vm.iceberg.select_view("snapshots")
    assert vm.iceberg.select_snapshot(43)
    catalog_baseline = _catalog_selection_state(vm)
    iceberg_baseline = _iceberg_state(vm)
    table_pager = vm._table_pager  # type: ignore[attr-defined]
    partition_pager = vm._partition_pager  # type: ignore[attr-defined]
    metadata_load = asyncio.create_task(vm.iceberg.retry())
    await inspector.started.wait()
    catalog_notifications: list[str] = []
    iceberg_notifications: list[str] = []
    catalog_subscription = vm.on_property_changed.subscribe(on_next=catalog_notifications.append)
    iceberg_subscription = vm.iceberg.on_property_changed.subscribe(
        on_next=iceberg_notifications.append
    )

    databases = tuple(fake.databases)
    fake.databases.clear()
    empty_refresh = asyncio.create_task(vm.refresh_databases())
    await inspector.cancellation_seen.wait()

    try:
        state_during_stale_drain = (
            _catalog_selection_state(vm),
            _iceberg_state(vm),
            tuple(iceberg_notifications),
        )
        fake.databases.extend(databases)
        await vm.refresh_databases()
        state_after_newer_refresh = (
            _catalog_selection_state(vm),
            _iceberg_state(vm),
            vm._table_pager,  # type: ignore[attr-defined]
            vm._partition_pager,  # type: ignore[attr-defined]
            tuple(catalog_notifications),
            tuple(iceberg_notifications),
        )
    finally:
        inspector.release.set()
    results = await asyncio.gather(empty_refresh, metadata_load, return_exceptions=True)

    assert results == [None, False]
    assert state_during_stale_drain == (catalog_baseline, iceberg_baseline, ())
    assert state_after_newer_refresh[:4] == (
        catalog_baseline,
        iceberg_baseline,
        table_pager,
        partition_pager,
    )
    catalog_notifications_after_newer_refresh = state_after_newer_refresh[4]
    assert set(catalog_notifications_after_newer_refresh) <= {
        "databases",
        "has_more_databases",
        "state",
    }
    assert state_after_newer_refresh[5] == ()
    assert _catalog_selection_state(vm) == catalog_baseline
    assert _iceberg_state(vm) == iceberg_baseline
    assert tuple(catalog_notifications) == catalog_notifications_after_newer_refresh
    assert iceberg_notifications == []

    await vm.load_more_tables()
    await vm.load_more_partitions()
    assert [row.ref.table_name for row in vm.tables] == ["events", "sessions"]
    assert [row.values for row in vm.partitions] == [
        ("2026-07-24",),
        ("2026-07-25",),
    ]
    assert table_pager.current_token == "2"
    assert partition_pager.current_token == "2"

    await vm.load_more_tables()
    await vm.load_more_partitions()
    assert [row.ref.table_name for row in vm.tables] == [
        "events",
        "sessions",
        "third",
    ]
    assert [row.values for row in vm.partitions] == [
        ("2026-07-24",),
        ("2026-07-25",),
        ("2026-07-26",),
    ]
    assert table_pager.current_token is None
    assert partition_pager.current_token is None
    assert _iceberg_state(vm) == iceberg_baseline
    catalog_subscription.dispose()
    iceberg_subscription.dispose()


@pytest.mark.asyncio
async def test_nonempty_refresh_does_not_supersede_blocked_table_list_selection() -> None:
    fake = seeded_glue()
    tables_started = fake.block_tables("analytics")
    vm = make_catalog_vm(fake)
    await vm.setup()
    selection = asyncio.create_task(vm.select_database("analytics"))
    await tables_started.wait()
    table_pager = vm._table_pager  # type: ignore[attr-defined]

    await vm.refresh_databases()
    notifications: list[str] = []
    subscription = vm.on_property_changed.subscribe(on_next=notifications.append)
    fake.release_tables("analytics")
    await selection

    assert vm._table_pager is table_pager  # type: ignore[attr-defined]
    assert [row.ref.table_name for row in vm.tables] == ["events", "sessions"]
    assert vm.tables_state is PaneState.IDLE
    assert vm.tables_error_text is None
    assert notifications == ["tables", "has_more_tables", "tables_state"]
    subscription.dispose()


@pytest.mark.asyncio
async def test_nonempty_refresh_does_not_supersede_blocked_table_detail_selection() -> None:
    fake = seeded_glue()
    events = fake.tables["analytics"][0]
    fake.table_details[events.ref] = replace(
        fake.table_details[events.ref],
        table_format=TableFormat.ICEBERG,
    )
    vm = make_catalog_vm(fake)
    await vm.setup()
    await vm.select_database("analytics")
    detail_started = fake.block_table_detail(events.ref)
    selection = asyncio.create_task(vm.select_table("events"))
    await detail_started.wait()

    await vm.refresh_databases()
    notifications: list[str] = []
    subscription = vm.on_property_changed.subscribe(on_next=notifications.append)
    fake.release_table_detail(events.ref)
    await selection

    assert vm.selected_table_name == "events"
    assert vm.table_detail == fake.table_details[events.ref]
    assert vm.partitions == tuple(fake.partitions[events.ref])
    assert vm.column_statistics == fake.statistics[events.ref]
    assert vm.detail_state is PaneState.IDLE
    assert vm.partitions_state is PaneState.IDLE
    assert vm.statistics_state is PaneState.IDLE
    assert vm.iceberg.table_ref == events.ref
    assert vm.iceberg.available
    assert notifications == [
        "table_detail",
        "detail_state",
        "partitions",
        "has_more_partitions",
        "partitions_state",
        "column_statistics",
        "statistics_state",
    ]
    subscription.dispose()


@pytest.mark.asyncio
async def test_table_selection_stays_superseded_by_database_selection_after_iceberg_drain() -> None:
    fake = seeded_glue()
    fake.add_table("archive", "old-events")
    events = next(row for row in fake.tables["analytics"] if row.ref.table_name == "events")
    fake.table_details[events.ref] = replace(
        fake.table_details[events.ref],
        table_format=TableFormat.ICEBERG,
    )
    inspector = CancellationResistantInspector()
    vm = make_catalog_vm(fake, iceberg_inspector=inspector)
    await vm.setup()
    await vm.select_database("analytics")
    await vm.select_table("events")
    fake.table_detail_requests.clear()
    metadata_load = asyncio.create_task(vm.iceberg.select_view("snapshots"))
    await inspector.started.wait()
    baseline = _catalog_selection_state(vm)
    notifications: list[str] = []
    observed_states: list[tuple[object, ...]] = []

    bindings: list[TableRef | None] = []
    bind_table = vm.iceberg.bind_table

    async def record_binding(
        table_ref: TableRef | None,
        *,
        table_format: TableFormat | None = None,
    ) -> None:
        bindings.append(table_ref)
        await bind_table(table_ref, table_format=table_format)

    vm.iceberg.bind_table = record_binding  # type: ignore[method-assign]

    def record_notification(property_name: str) -> None:
        notifications.append(property_name)
        observed_states.append(_catalog_selection_state(vm))

    subscription = vm.on_property_changed.subscribe(on_next=record_notification)
    stale_selection = asyncio.create_task(vm.select_table("events"))
    await inspector.cancellation_seen.wait()

    assert _catalog_selection_state(vm) == baseline
    assert notifications == []

    archive_tables_started = fake.block_tables("archive")
    newer_selection = asyncio.create_task(vm.select_database("archive"))
    await asyncio.sleep(0)

    assert _catalog_selection_state(vm) == baseline
    assert notifications == []

    inspector.release.set()
    await stale_selection
    await archive_tables_started.wait()

    committed_state = _catalog_selection_state(vm)
    assert fake.table_detail_requests == []
    assert bindings == []
    assert vm.selected_database_name == "archive"
    assert vm.selected_table_name is None
    assert vm.table_detail is None
    assert vm.column_statistics == ()
    assert vm.tables == ()
    assert vm.partitions == ()
    assert not vm.has_more_tables
    assert not vm.has_more_partitions
    assert vm.tables_state is PaneState.LOADING
    assert vm.detail_state is PaneState.EMPTY
    assert vm.partitions_state is PaneState.EMPTY
    assert vm.statistics_state is PaneState.EMPTY
    assert vm.tables_error_text is None
    assert vm.detail_error_text is None
    assert vm.partitions_error_text is None
    assert vm.statistics_error_text is None
    assert notifications == [
        "tables_state",
        "detail_state",
        "partitions_state",
        "statistics_state",
        "selected_database_name",
        "selected_table_name",
        "tables",
        "table_detail",
        "partitions",
        "column_statistics",
    ]
    assert all(state == committed_state for state in observed_states)

    fake.release_tables("archive")
    results = await asyncio.gather(newer_selection, metadata_load, return_exceptions=True)

    assert results == [None, False]
    subscription.dispose()


@pytest.mark.asyncio
async def test_database_refresh_preserves_valid_table_and_partition_pagers() -> None:
    fake = seeded_glue()
    events = fake.tables["analytics"][0]
    fake.add_table("analytics", "third")
    fake.add_partition(events.ref, "2026-07-26")
    fake.table_page_size = 1
    fake.partition_page_size = 1
    vm = make_catalog_vm(fake)
    await vm.setup()
    await vm.select_database("analytics")
    await vm.select_table("events")

    table_pager = vm._table_pager  # type: ignore[attr-defined]
    partition_pager = vm._partition_pager  # type: ignore[attr-defined]
    await vm.refresh_databases()

    assert vm._table_pager is table_pager  # type: ignore[attr-defined]
    assert vm._partition_pager is partition_pager  # type: ignore[attr-defined]
    assert table_pager.current_token == "1"
    assert partition_pager.current_token == "1"

    await vm.load_more_tables()
    await vm.load_more_partitions()

    assert [row.ref.table_name for row in vm.tables] == ["events", "sessions"]
    assert table_pager.current_token == "2"
    assert vm.has_more_tables
    assert [row.values for row in vm.partitions] == [
        ("2026-07-24",),
        ("2026-07-25",),
    ]
    assert partition_pager.current_token == "2"
    assert vm.has_more_partitions

    await vm.load_more_tables()
    await vm.load_more_partitions()

    assert [row.ref.table_name for row in vm.tables] == [
        "events",
        "sessions",
        "third",
    ]
    assert table_pager.current_token is None
    assert not vm.has_more_tables
    assert [row.values for row in vm.partitions] == [
        ("2026-07-24",),
        ("2026-07-25",),
        ("2026-07-26",),
    ]
    assert partition_pager.current_token is None
    assert not vm.has_more_partitions


@pytest.mark.asyncio
async def test_database_selection_invalidates_blocked_table_page() -> None:
    class BlockingTables(InMemoryGlue):
        def __init__(self) -> None:
            super().__init__()
            self.next_page_started = asyncio.Event()
            self.release_next_page = asyncio.Event()

        async def list_tables_page(  # type: ignore[override]
            self,
            database: str,
            *,
            start_token: str | None = None,
        ) -> tuple[list, str | None]:
            if database == "analytics" and start_token == "1":
                self.next_page_started.set()
                await self.release_next_page.wait()
            return await super().list_tables_page(database, start_token=start_token)

    fake = BlockingTables()
    fake.add_table("analytics", "events")
    fake.add_table("analytics", "sessions")
    fake.add_table("archive", "old-events")
    fake.table_page_size = 1
    vm = make_catalog_vm(fake)
    await vm.setup()
    await vm.select_database("analytics")

    stale_page = asyncio.create_task(vm.load_more_tables())
    await fake.next_page_started.wait()
    await vm.select_database("archive")
    notifications: list[str] = []
    subscription = vm.on_property_changed.subscribe(on_next=notifications.append)

    fake.release_next_page.set()
    await stale_page

    assert vm.selected_database_name == "archive"
    assert [row.ref.table_name for row in vm.tables] == ["old-events"]
    assert notifications == []
    subscription.dispose()


@pytest.mark.asyncio
async def test_table_selection_invalidates_blocked_partition_page() -> None:
    class BlockingPartitions(InMemoryGlue):
        def __init__(self) -> None:
            super().__init__()
            self.next_page_started = asyncio.Event()
            self.release_next_page = asyncio.Event()

        async def list_partitions_page(  # type: ignore[override]
            self,
            ref: TableRef,
            *,
            start_token: str | None = None,
        ) -> tuple[list, str | None]:
            if ref.table_name == "events" and start_token == "1":
                self.next_page_started.set()
                await self.release_next_page.wait()
            return await super().list_partitions_page(ref, start_token=start_token)

    fake = BlockingPartitions()
    events = fake.add_table("analytics", "events")
    fake.add_table("analytics", "sessions")
    fake.add_partition(events.ref, "2026-07-24")
    fake.add_partition(events.ref, "2026-07-25")
    fake.partition_page_size = 1
    vm = make_catalog_vm(fake)
    await vm.setup()
    await vm.select_database("analytics")
    await vm.select_table("events")

    stale_page = asyncio.create_task(vm.load_more_partitions())
    await fake.next_page_started.wait()
    await vm.select_table("sessions")
    notifications: list[str] = []
    subscription = vm.on_property_changed.subscribe(on_next=notifications.append)

    fake.release_next_page.set()
    await stale_page

    assert vm.selected_table_name == "sessions"
    assert vm.partitions == ()
    assert notifications == []
    subscription.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "selection",
    ["database", "table", "empty-database-refresh"],
)
async def test_shutdown_during_iceberg_drain_freezes_catalog_terminal_state(
    selection: str,
) -> None:
    fake = seeded_glue()
    fake.add_table("archive", "old-events")
    events = next(row for row in fake.tables["analytics"] if row.ref.table_name == "events")
    fake.table_details[events.ref] = replace(
        fake.table_details[events.ref],
        table_format=TableFormat.ICEBERG,
    )
    inspector = CancellationResistantInspector()
    vm = make_catalog_vm(fake, iceberg_inspector=inspector)
    await vm.setup()
    await vm.select_database("analytics")
    await vm.select_table("events")
    metadata_load = asyncio.create_task(vm.iceberg.select_view("snapshots"))
    await inspector.started.wait()
    catalog_notifications: list[str] = []
    iceberg_notifications: list[str] = []
    catalog_subscription = vm.on_property_changed.subscribe(on_next=catalog_notifications.append)
    iceberg_subscription = vm.iceberg.on_property_changed.subscribe(
        on_next=iceberg_notifications.append
    )

    if selection == "database":
        operation = asyncio.create_task(vm.select_database("archive"))
    elif selection == "table":
        operation = asyncio.create_task(vm.select_table("sessions"))
    else:
        fake.databases.clear()
        operation = asyncio.create_task(vm.refresh_databases())
    await inspector.cancellation_seen.wait()

    shutdown = asyncio.create_task(vm.shutdown())
    await asyncio.sleep(0)
    state_at_shutdown = _catalog_state(vm)
    catalog_notifications.clear()
    iceberg_notifications.clear()
    inspector.release.set()
    results = await asyncio.gather(
        operation,
        metadata_load,
        shutdown,
        return_exceptions=True,
    )

    assert results == [None, False, None]
    assert _catalog_state(vm) == state_at_shutdown
    assert catalog_notifications == []
    assert iceberg_notifications == []
    catalog_subscription.dispose()
    iceberg_subscription.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("token_mode", ["repeated", "unique"])
async def test_open_table_bounds_empty_database_discovery_pages(token_mode: str) -> None:
    class EndlessDatabasePages(InMemoryGlue):
        def __init__(self) -> None:
            super().__init__()
            self.visible = self.add_database("visible")
            self.page_number = 0

        async def list_databases_page(  # type: ignore[override]
            self,
            *,
            start_token: str | None = None,
        ) -> tuple[list, str | None]:
            self.database_tokens.append(start_token)
            if start_token is None:
                return [self.visible], "TOKEN_SECRET-1"
            self.page_number += 1
            next_token = (
                "TOKEN_SECRET-1"
                if token_mode == "repeated"
                else f"TOKEN_SECRET-{self.page_number + 1}"
            )
            return [], next_token

    fake = EndlessDatabasePages()
    vm = make_catalog_vm(fake)
    await vm.setup()
    missing = TableRef(
        "AwsDataCatalog",
        "missing",
        "events",
        "dev",
        "us-east-1",
    )

    with pytest.raises(
        ProviderError,
        match=r"^Glue catalog discovery did not complete$",
    ) as caught:
        await asyncio.wait_for(vm.open_table(missing), timeout=1)

    assert len(fake.database_tokens) <= 8
    rendered = "".join(
        traceback.TracebackException.from_exception(
            caught.value,
            capture_locals=True,
        ).format()
    )
    assert "TOKEN_SECRET" not in rendered


@pytest.mark.asyncio
async def test_open_table_allows_finite_empty_database_pages_before_match() -> None:
    class SparseDatabasePages(InMemoryGlue):
        def __init__(self) -> None:
            super().__init__()
            self.visible = self.add_database("visible")
            self.target = self.add_table("target", "events")
            self.target_database = next(
                row for row in self.databases if row.ref.database_name == "target"
            )

        async def list_databases_page(  # type: ignore[override]
            self,
            *,
            start_token: str | None = None,
        ) -> tuple[list, str | None]:
            self.database_tokens.append(start_token)
            pages = {
                None: ([self.visible], "page-1"),
                "page-1": ([], "page-2"),
                "page-2": ([], "page-3"),
                "page-3": ([self.target_database], None),
            }
            return pages[start_token]

    fake = SparseDatabasePages()
    vm = make_catalog_vm(fake)
    await vm.setup()

    await asyncio.wait_for(vm.open_table(fake.target.ref), timeout=1)

    assert vm.selected_database_name == "target"
    assert vm.selected_table_name == "events"
    assert fake.database_tokens == [None, "page-1", "page-2", "page-3"]


@pytest.mark.asyncio
@pytest.mark.parametrize("token_mode", ["repeated", "unique"])
async def test_open_table_bounds_empty_table_discovery_pages(token_mode: str) -> None:
    class EndlessTablePages(InMemoryGlue):
        def __init__(self) -> None:
            super().__init__()
            self.visible = self.add_table("analytics", "visible")
            self.page_number = 0

        async def list_tables_page(  # type: ignore[override]
            self,
            database: str,
            *,
            start_token: str | None = None,
        ) -> tuple[list, str | None]:
            self.table_requests.append((database, start_token))
            if start_token is None:
                return [self.visible], "page-1"
            self.page_number += 1
            next_token = "page-1" if token_mode == "repeated" else f"page-{self.page_number + 1}"
            return [], next_token

    fake = EndlessTablePages()
    vm = make_catalog_vm(fake)
    await vm.setup()
    missing = TableRef(
        "AwsDataCatalog",
        "analytics",
        "missing",
        "dev",
        "us-east-1",
    )

    with pytest.raises(ProviderError, match=r"^Glue catalog discovery did not complete$"):
        await asyncio.wait_for(vm.open_table(missing), timeout=1)

    assert len(fake.table_requests) <= 8


@pytest.mark.asyncio
async def test_open_table_enforces_absolute_discovery_page_cap() -> None:
    class EndlessProgressPages(InMemoryGlue):
        def __init__(self) -> None:
            super().__init__()
            self.visible = self.add_database("visible")
            self.page_number = 0

        async def list_databases_page(  # type: ignore[override]
            self,
            *,
            start_token: str | None = None,
        ) -> tuple[list, str | None]:
            self.database_tokens.append(start_token)
            if start_token is None:
                return [self.visible], "page-1"
            self.page_number += 1
            row = self.add_database(f"unrelated-{self.page_number}")
            return [row], f"page-{self.page_number + 1}"

    fake = EndlessProgressPages()
    vm = make_catalog_vm(fake)
    await vm.setup()
    missing = TableRef(
        "AwsDataCatalog",
        "missing",
        "events",
        "dev",
        "us-east-1",
    )

    with pytest.raises(ProviderError, match=r"^Glue catalog discovery did not complete$"):
        await asyncio.wait_for(vm.open_table(missing), timeout=1)

    assert len(fake.database_tokens) == 65


@pytest.mark.asyncio
async def test_table_access_denial_is_scoped_to_table_pane() -> None:
    class BrokenTables(InMemoryGlue):
        async def list_tables_page(  # type: ignore[override]
            self,
            database: str,
            *,
            start_token: str | None = None,
        ) -> tuple[list, str | None]:
            raise PermissionDeniedError("glue:GetTables denied")

    fake = BrokenTables()
    fake.add_database("analytics")
    vm = make_catalog_vm(fake)
    await vm.setup()
    await vm.select_database("analytics")

    assert vm.databases_state is PaneState.IDLE
    assert vm.tables_state is PaneState.FORBIDDEN
    assert vm.state is PaneState.IDLE
    assert vm.tables_error_text == "glue:GetTables denied"


@pytest.mark.asyncio
async def test_table_detail_denial_does_not_strand_sibling_panes_loading() -> None:
    class BrokenDetail(InMemoryGlue):
        async def get_table(self, ref):  # type: ignore[no-untyped-def,override]
            raise PermissionDeniedError("glue:GetTable denied")

    fake = BrokenDetail()
    fake.add_table("analytics", "events")
    vm = make_catalog_vm(fake)
    await vm.setup()
    await vm.select_database("analytics")

    await vm.select_table("events")

    assert vm.detail_state is PaneState.FORBIDDEN
    assert vm.partitions_state is PaneState.EMPTY
    assert vm.statistics_state is PaneState.EMPTY
    assert vm.tables_state is PaneState.IDLE


@pytest.mark.asyncio
async def test_catalog_load_more_unexpected_errors_are_scoped_and_redacted() -> None:
    class BrokenNextPages(InMemoryGlue):
        async def list_databases_page(
            self,
            *,
            start_token: str | None = None,
        ) -> tuple[list, str | None]:
            if start_token is not None:
                raise RuntimeError("Authorization: Bearer DATABASE_SECRET")
            return await super().list_databases_page(start_token=start_token)

        async def list_tables_page(
            self,
            database: str,
            *,
            start_token: str | None = None,
        ) -> tuple[list, str | None]:
            if start_token is not None:
                raise RuntimeError("Authorization: Bearer TABLE_SECRET")
            return await super().list_tables_page(database, start_token=start_token)

        async def list_partitions_page(
            self,
            ref,
            *,
            start_token: str | None = None,
        ) -> tuple[list, str | None]:
            if start_token is not None:
                raise RuntimeError("Authorization: Bearer PARTITION_SECRET")
            return await super().list_partitions_page(ref, start_token=start_token)

    fake = BrokenNextPages()
    first = fake.add_table("analytics", "events")
    fake.add_table("analytics", "sessions")
    fake.add_database("warehouse")
    fake.add_partition(first.ref, "2026-07-24")
    fake.add_partition(first.ref, "2026-07-25")
    fake.database_page_size = 1
    fake.table_page_size = 1
    fake.partition_page_size = 1
    vm = make_catalog_vm(fake)

    await vm.setup()
    await vm.load_more_databases()
    assert vm.databases_state is PaneState.ERROR
    assert vm.databases_error_text is not None
    assert "DATABASE_SECRET" not in vm.databases_error_text
    assert "[REDACTED]" in vm.databases_error_text

    await vm.select_database("analytics")
    await vm.load_more_tables()
    assert vm.tables_state is PaneState.ERROR
    assert vm.tables_error_text is not None
    assert "TABLE_SECRET" not in vm.tables_error_text
    assert "[REDACTED]" in vm.tables_error_text

    await vm.select_table("events")
    await vm.load_more_partitions()
    assert vm.partitions_state is PaneState.ERROR
    assert vm.partitions_error_text is not None
    assert "PARTITION_SECRET" not in vm.partitions_error_text
    assert "[REDACTED]" in vm.partitions_error_text


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "location",
    [
        pytest.param("s3://[GLUE_URI_SECRET", id="malformed-authority"),
        pytest.param("s3://valid-bucket:443/GLUE_URI_SECRET", id="port"),
        pytest.param("s3://valid-bucket/prefix?GLUE_URI_SECRET=1", id="query"),
        pytest.param("s3://valid-bucket/raw\x00GLUE_URI_SECRET", id="raw-control"),
        pytest.param("s3://valid-bucket/%0AGLUE_URI_SECRET", id="encoded-control"),
    ],
)
async def test_catalog_rejects_invalid_s3_locations_without_publishing(
    location: str,
) -> None:
    vm = make_catalog_vm(seeded_glue())
    await vm.setup()
    await vm.select_database("analytics")
    await vm.select_table("events")
    detail = vm.table_detail
    assert detail is not None
    vm._table_detail = replace(  # type: ignore[attr-defined]
        detail,
        storage=replace(detail.storage, location=location),
    )
    published: list[Message] = []
    subscription = vm._hub.messages.subscribe(on_next=published.append)  # type: ignore[attr-defined]

    try:
        assert not vm.open_s3_location()
        assert published == []
    finally:
        subscription.dispose()


@pytest.mark.asyncio
async def test_catalog_publishes_valid_dotted_hyphenated_s3_prefix() -> None:
    vm = make_catalog_vm(seeded_glue())
    await vm.setup()
    await vm.select_database("analytics")
    await vm.select_table("events")
    detail = vm.table_detail
    assert detail is not None
    location = "s3://warehouse.prod-2026/events-data/prefix/"
    vm._table_detail = replace(  # type: ignore[attr-defined]
        detail,
        storage=replace(detail.storage, location=location),
    )
    published: list[Message] = []
    subscription = vm._hub.messages.subscribe(on_next=published.append)  # type: ignore[attr-defined]

    try:
        assert vm.open_s3_location(preferred_pane="right")
        assert published == [
            OpenS3LocationRequest(
                connection_name=detail.summary.ref.connection_name,
                region=detail.summary.ref.region,
                uri=location,
                preferred_pane="right",
            )
        ]
    finally:
        subscription.dispose()


@pytest.mark.asyncio
async def test_catalog_dispose_invalidates_blocked_load_without_notifications() -> None:
    fake = seeded_glue()
    tables_started = fake.block_tables("analytics")
    vm = make_catalog_vm(fake)
    await vm.setup()
    notifications: list[str] = []
    subscription = vm.on_property_changed.subscribe(on_next=notifications.append)

    selection = asyncio.create_task(vm.select_database("analytics"))
    await tables_started.wait()
    notifications.clear()
    generations = (
        vm._database_generation,  # type: ignore[attr-defined]
        vm._table_generation,  # type: ignore[attr-defined]
        vm._detail_generation,  # type: ignore[attr-defined]
        vm._partition_generation,  # type: ignore[attr-defined]
    )

    vm.dispose()
    fake.release_tables("analytics")
    await selection

    assert notifications == []
    assert (
        vm._database_generation,  # type: ignore[attr-defined]
        vm._table_generation,  # type: ignore[attr-defined]
        vm._detail_generation,  # type: ignore[attr-defined]
        vm._partition_generation,  # type: ignore[attr-defined]
    ) == tuple(generation + 1 for generation in generations)
    subscription.dispose()


@pytest.mark.asyncio
async def test_replacing_catalog_pagers_disposes_old_pagers_and_commands() -> None:
    fake = seeded_glue()
    vm = make_catalog_vm(fake)
    await vm.setup()
    old_table_pager = vm._table_pager  # type: ignore[attr-defined]
    old_partition_pager = vm._partition_pager  # type: ignore[attr-defined]

    await vm.select_database("analytics")

    assert old_table_pager._disposed  # type: ignore[attr-defined]
    assert old_table_pager.load_more_command._disposed  # type: ignore[attr-defined]
    assert old_table_pager.refresh_command._disposed  # type: ignore[attr-defined]
    assert old_partition_pager._disposed  # type: ignore[attr-defined]


def test_catalog_dispose_reaches_every_pager_and_command_exactly_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    vm = make_catalog_vm(seeded_glue())
    pagers = [
        vm._database_pager,  # type: ignore[attr-defined]
        vm._table_pager,  # type: ignore[attr-defined]
        vm._partition_pager,  # type: ignore[attr-defined]
    ]
    calls = {id(pager): 0 for pager in pagers}
    for pager in pagers:
        original = pager.dispose

        def counted_dispose(
            *,
            target: TokenPagedComposition = pager,
            dispose: object = original,
        ) -> None:
            calls[id(target)] += 1
            dispose()  # type: ignore[operator]

        monkeypatch.setattr(pager, "dispose", counted_dispose)

    vm.dispose()
    vm.dispose()

    assert set(calls.values()) == {1}
    for pager in pagers:
        assert pager._disposed  # type: ignore[attr-defined]
        assert pager.load_more_command._disposed  # type: ignore[attr-defined]
        assert pager.refresh_command._disposed  # type: ignore[attr-defined]
