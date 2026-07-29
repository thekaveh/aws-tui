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
from aws_tui.vm.messages import OpenAthenaTableRequest
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
        assert {
            "glue-iceberg-tab-snapshots",
            "glue-iceberg-tab-history",
            "glue-iceberg-tab-manifests",
            "glue-iceberg-tab-files",
            "glue-iceberg-tab-partitions",
            "glue-iceberg-tab-refs",
        }.issubset(focus_ids)


@pytest.mark.asyncio
async def test_switching_from_snapshots_disables_time_travel_control() -> None:
    vm, _inspector = _build_vm()
    await vm.setup()

    async with _GlueIcebergApp(vm).run_test(size=(100, 30)) as pilot:
        await pilot.click("#glue-iceberg-tab-snapshots")
        await pilot.pause()
        button = pilot.app.query_one("#glue-iceberg-time-travel", Button)
        assert not button.disabled

        await pilot.click("#glue-iceberg-tab-history")
        await pilot.pause()

        assert vm.catalog.iceberg.selected_snapshot_id is None
        assert not vm.catalog.iceberg.can_time_travel_in_athena
        assert button.disabled


@pytest.mark.asyncio
async def test_older_snapshot_selection_survives_refresh_and_drives_time_travel() -> None:
    vm, _inspector = _build_vm()
    await vm.setup()
    messages: list[OpenAthenaTableRequest] = []
    subscription = vm.hub.messages.subscribe(
        on_next=lambda message: (
            messages.append(message) if isinstance(message, OpenAthenaTableRequest) else None
        )
    )
    notifications: list[str] = []
    selection_subscription = vm.catalog.iceberg.on_property_changed.subscribe(
        on_next=lambda name: notifications.append(name)
    )

    async with _GlueIcebergApp(vm).run_test(size=(100, 30)) as pilot:
        await pilot.click("#glue-iceberg-tab-snapshots")
        await pilot.pause()
        iceberg = pilot.app.query_one(GlueIcebergView)
        table = iceberg.query_one("#glue-iceberg-table", DataTable)

        table.move_cursor(row=1)
        await pilot.pause()
        assert vm.catalog.iceberg.selected_snapshot_id == 42
        selection_notifications = notifications.count("selected_snapshot_id")

        iceberg._refresh()
        await pilot.pause()

        assert table.cursor_row == 1
        assert vm.catalog.iceberg.selected_snapshot_id == 42
        assert notifications.count("selected_snapshot_id") == selection_notifications
        await pilot.click("#glue-iceberg-time-travel")
        assert vm.catalog.iceberg.selected_snapshot_id == 42
        assert messages == [
            OpenAthenaTableRequest(
                table_ref=vm.catalog.table_detail.summary.ref,
                snapshot_id=42,
            )
        ]
    selection_subscription.dispose()
    subscription.dispose()


@pytest.mark.asyncio
async def test_snapshot_pagination_preserves_selection_and_removed_row_falls_back() -> None:
    vm, inspector = _build_vm()
    vm.catalog.iceberg._page_size = 1  # type: ignore[attr-defined]
    await vm.setup()

    async with _GlueIcebergApp(vm).run_test(size=(100, 30)) as pilot:
        await pilot.click("#glue-iceberg-tab-snapshots")
        await pilot.click("#glue-iceberg-more")
        await pilot.pause()
        table = pilot.app.query_one("#glue-iceberg-table", DataTable)
        table.move_cursor(row=1)
        await pilot.pause()
        assert vm.catalog.iceberg.selected_snapshot_id == 42

        inspector.snapshots = tuple(row for row in inspector.snapshots if row.snapshot_id != 42)
        await vm.catalog.iceberg.retry()
        await pilot.pause()

        assert table.cursor_row == 0
        assert vm.catalog.iceberg.selected_snapshot_id == 43


@pytest.mark.asyncio
async def test_retry_and_load_more_buttons_run_current_view_actions() -> None:
    vm, inspector = _build_vm()
    vm.catalog.iceberg._page_size = 1  # type: ignore[attr-defined]
    inspector.errors["snapshots"] = PermissionError("denied")
    await vm.setup()

    async with _GlueIcebergApp(vm).run_test(size=(100, 30)) as pilot:
        await pilot.click("#glue-iceberg-tab-snapshots")
        await pilot.pause()
        retry = pilot.app.query_one("#glue-iceberg-retry", Button)
        assert retry.display
        assert not retry.disabled
        assert retry in pilot.app.screen.focus_chain

        inspector.errors.pop("snapshots")
        await pilot.click("#glue-iceberg-retry")
        await pilot.pause()
        assert vm.catalog.iceberg.state.name == "IDLE"
        assert len(vm.catalog.iceberg.snapshots) == 1
        assert pilot.app.query_one("#glue-iceberg-more", Button) in pilot.app.screen.focus_chain

        await pilot.click("#glue-iceberg-more")
        await pilot.pause()
        assert len(vm.catalog.iceberg.snapshots) == 2


@pytest.mark.asyncio
async def test_compact_tab_labels_are_distinct_and_untruncated_at_80_columns() -> None:
    vm, _inspector = _build_vm()
    await vm.setup()

    async with _GlueIcebergApp(vm).run_test(size=(80, 24)) as pilot:
        await pilot.pause()
        labels = [
            str(tab.render())
            for tab in pilot.app.query(GlueIcebergView).first().query(".glue-iceberg-tab")
        ]

        assert labels == ["Snaps", "Hist", "Mnfst", "Files", "Parts", "Refs"]
