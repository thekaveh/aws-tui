from __future__ import annotations

import asyncio

import pytest
from vmx import NULL_DISPATCHER, MessageHub, TokenPagedComposition
from vmx.messages.protocols import Message

from aws_tui.domain.filesystem import PermissionDeniedError
from aws_tui.vm.file_manager.pane_vm import PaneState
from aws_tui.vm.glue.catalog_vm import GlueCatalogVM
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
