from __future__ import annotations

from dataclasses import replace

import pytest
from textual.app import App, ComposeResult
from textual.widgets import Button, DataTable
from vmx import NULL_DISPATCHER, MessageHub
from vmx.messages.protocols import Message

from aws_tui.domain.data_catalog import TableFormat
from aws_tui.infra.connection_resolver import Connection
from aws_tui.ui.widgets.glue.iceberg_view import GlueIcebergView
from aws_tui.ui.widgets.glue.page import GluePage
from aws_tui.vm.glue.page_vm import GluePageVM
from tests.unit.vm.glue._fake_glue import InMemoryGlue
from tests.unit.vm.glue.test_iceberg_vm import RecordingInspector


def _build_vm(*, iceberg: bool = True) -> tuple[GluePageVM, RecordingInspector]:
    fake = InMemoryGlue()
    table = fake.add_table("analytics", "events")
    if iceberg:
        fake.table_details[table.ref] = replace(
            fake.table_details[table.ref],
            table_format=TableFormat.ICEBERG,
        )
    inspector = RecordingInspector()
    hub: MessageHub[Message] = MessageHub()
    vm = GluePageVM(
        client=fake,
        iceberg_inspector=inspector,
        connection=Connection(
            name="dev",
            kind="aws",
            region="us-east-1",
            source="test",
            profile="dev",
        ),
        hub=hub,
        dispatcher=NULL_DISPATCHER,
    )
    vm.construct()
    return vm, inspector


class _GlueIcebergApp(App[None]):
    def __init__(self, vm: GluePageVM) -> None:
        super().__init__()
        self._vm = vm

    def compose(self) -> ComposeResult:
        yield GluePage(self._vm, hub=self._vm.hub)


@pytest.mark.asyncio
async def test_iceberg_metadata_region_is_hidden_for_non_iceberg_table() -> None:
    vm, _inspector = _build_vm(iceberg=False)
    await vm.setup()

    async with _GlueIcebergApp(vm).run_test() as pilot:
        await pilot.pause()

        assert not vm.catalog.iceberg.available
        assert not pilot.app.query_one(GlueIcebergView).display


@pytest.mark.asyncio
async def test_iceberg_view_composes_compact_tabs_table_and_time_travel_control() -> None:
    vm, inspector = _build_vm()
    await vm.setup()

    async with _GlueIcebergApp(vm).run_test(size=(140, 42)) as pilot:
        await pilot.pause()
        iceberg = pilot.app.query_one(GlueIcebergView)

        assert iceberg.display
        assert len(list(iceberg.query(".glue-iceberg-tab"))) == 6
        assert iceberg.query_one("#glue-iceberg-table", DataTable)
        assert iceberg.query_one("#glue-iceberg-time-travel", Button).disabled
        assert inspector.calls == []


@pytest.mark.asyncio
async def test_selecting_snapshot_tab_loads_rows_and_enables_time_travel() -> None:
    vm, inspector = _build_vm()
    await vm.setup()

    async with _GlueIcebergApp(vm).run_test(size=(140, 42)) as pilot:
        await pilot.pause()
        await pilot.click("#glue-iceberg-tab-snapshots")
        await pilot.pause()
        table = pilot.app.query_one("#glue-iceberg-table", DataTable)

        assert inspector.calls == [("snapshots", vm.catalog.table_detail.summary.ref)]
        assert table.row_count == 3

        table.focus()
        table.move_cursor(row=0)
        await pilot.pause()

        assert vm.catalog.iceberg.selected_snapshot_id == 43
        assert not pilot.app.query_one("#glue-iceberg-time-travel", Button).disabled


@pytest.mark.asyncio
async def test_switching_metadata_tabs_preserves_loaded_pane_and_focus_targets() -> None:
    vm, inspector = _build_vm()
    await vm.setup()

    async with _GlueIcebergApp(vm).run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        await pilot.click("#glue-iceberg-tab-refs")
        await pilot.pause()
        await pilot.click("#glue-iceberg-tab-files")
        await pilot.pause()
        await pilot.click("#glue-iceberg-tab-refs")
        await pilot.pause()
        await pilot.click("#glue-iceberg-tab-snapshots")
        await pilot.pause()

        assert [call[0] for call in inspector.calls] == ["refs", "files", "snapshots"]
        iceberg = pilot.app.query_one(GlueIcebergView)
        focus_ids = {
            widget.id
            for widget in pilot.app.screen.focus_chain
            if iceberg in widget.ancestors_with_self
        }
        assert "glue-iceberg-table" in focus_ids
        assert "glue-iceberg-time-travel" in focus_ids
