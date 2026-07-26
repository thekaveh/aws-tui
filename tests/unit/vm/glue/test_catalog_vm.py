from __future__ import annotations

import asyncio
from dataclasses import replace

import pytest
from vmx import NULL_DISPATCHER, MessageHub, TokenPagedComposition
from vmx.messages.protocols import Message

from aws_tui.domain.filesystem import PermissionDeniedError
from aws_tui.vm.file_manager.pane_vm import PaneState
from aws_tui.vm.glue.catalog_vm import GlueCatalogVM
from aws_tui.vm.messages import OpenS3LocationRequest
from tests.unit.vm.glue._fake_glue import InMemoryGlue, seeded_glue


def make_catalog_vm(fake: InMemoryGlue) -> GlueCatalogVM:
    hub: MessageHub[Message] = MessageHub()
    vm = GlueCatalogVM(client=fake, hub=hub, dispatcher=NULL_DISPATCHER)
    vm.construct()
    return vm


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
