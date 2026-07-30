from __future__ import annotations

from dataclasses import replace
from typing import ClassVar

import pytest
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.widgets import Button, DataTable
from vmx import NULL_DISPATCHER, MessageHub
from vmx.messages.protocols import Message

from aws_tui.domain.data_catalog import TableFormat
from aws_tui.infra.connection_resolver import Connection
from aws_tui.ui.widgets.glue.iceberg_view import GlueIcebergView
from aws_tui.ui.widgets.glue.page import GluePage
from aws_tui.vm.chrome.focus_coordinator_vm import FocusCoordinatorVM, FocusSlot
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
    BINDINGS: ClassVar[list[Binding]] = [
        Binding("enter", "activate_enter", "", show=False, priority=True),
        Binding("space", "activate_space", "", show=False, priority=True),
    ]

    def __init__(self, vm: GluePageVM) -> None:
        super().__init__()
        self._vm = vm
        self.focus_coordinator = FocusCoordinatorVM(
            hub=vm.hub,
            dispatcher=NULL_DISPATCHER,
        )
        self.focus_coordinator.construct()

    def compose(self) -> ComposeResult:
        yield GluePage(
            self._vm,
            hub=self._vm.hub,
            focus_coordinator=self.focus_coordinator,
        )

    def on_unmount(self) -> None:
        self.focus_coordinator.dispose()

    def action_activate_enter(self) -> None:
        self.query_one(GluePage).activate_focused(space=False)

    def action_activate_space(self) -> None:
        self.query_one(GluePage).activate_focused(space=True)


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
async def test_enabled_iceberg_surface_is_in_the_glue_typed_focus_ring() -> None:
    vm, _inspector = _build_vm()
    vm.catalog.iceberg._page_size = 1  # type: ignore[attr-defined]
    await vm.setup()

    async with _GlueIcebergApp(vm).run_test(size=(100, 30)) as pilot:
        await pilot.click("#glue-iceberg-tab-snapshots")
        await pilot.pause()
        page = pilot.app.query_one(GluePage)
        target_ids = {widget.id for _slot, widget in page._focus_targets()}

        assert {
            "glue-iceberg-tab-snapshots",
            "glue-iceberg-tab-history",
            "glue-iceberg-tab-manifests",
            "glue-iceberg-tab-files",
            "glue-iceberg-tab-partitions",
            "glue-iceberg-tab-refs",
            "glue-iceberg-table",
            "glue-iceberg-more",
            "glue-iceberg-time-travel",
        }.issubset(target_ids)
        assert pilot.app.focus_coordinator.focused_slot is FocusSlot.GLUE_ICEBERG_SNAPSHOTS


@pytest.mark.asyncio
@pytest.mark.parametrize("key", ["enter", "space"])
async def test_enter_and_space_activate_focused_iceberg_tab(key: str) -> None:
    vm, inspector = _build_vm()
    await vm.setup()

    async with _GlueIcebergApp(vm).run_test(size=(100, 30)) as pilot:
        tab = pilot.app.query_one("#glue-iceberg-tab-history")
        pilot.app.set_focus(tab)
        await pilot.press(key)
        await pilot.pause()
        await pilot.app.workers.wait_for_complete(list(pilot.app.workers._workers))  # type: ignore[attr-defined]
        await pilot.pause()

        assert vm.catalog.iceberg.active_view == "history"
        assert inspector.calls == [("history", vm.catalog.table_detail.summary.ref)]


@pytest.mark.asyncio
@pytest.mark.parametrize("key", ["enter", "space"])
async def test_enter_and_space_press_all_enabled_iceberg_buttons(key: str) -> None:
    vm, inspector = _build_vm()
    vm.catalog.iceberg._page_size = 1  # type: ignore[attr-defined]
    inspector.errors["snapshots"] = PermissionError("denied")
    await vm.setup()
    messages: list[OpenAthenaTableRequest] = []
    subscription = vm.hub.messages.subscribe(
        on_next=lambda message: (
            messages.append(message) if isinstance(message, OpenAthenaTableRequest) else None
        )
    )

    async with _GlueIcebergApp(vm).run_test(size=(100, 30)) as pilot:
        snapshot_tab = pilot.app.query_one("#glue-iceberg-tab-snapshots")
        pilot.app.set_focus(snapshot_tab)
        await pilot.press(key)
        await pilot.pause()
        await pilot.app.workers.wait_for_complete(list(pilot.app.workers._workers))  # type: ignore[attr-defined]
        await pilot.pause()

        inspector.errors.pop("snapshots")
        retry = pilot.app.query_one("#glue-iceberg-retry", Button)
        assert not retry.disabled
        pilot.app.set_focus(retry)
        await pilot.press(key)
        await pilot.pause()
        await pilot.app.workers.wait_for_complete(list(pilot.app.workers._workers))  # type: ignore[attr-defined]
        await pilot.pause()
        assert len(vm.catalog.iceberg.snapshots) == 1

        more = pilot.app.query_one("#glue-iceberg-more", Button)
        assert not more.disabled
        pilot.app.set_focus(more)
        await pilot.press(key)
        await pilot.pause()
        await pilot.app.workers.wait_for_complete(list(pilot.app.workers._workers))  # type: ignore[attr-defined]
        await pilot.pause()
        assert len(vm.catalog.iceberg.snapshots) == 2

        time_travel = pilot.app.query_one("#glue-iceberg-time-travel", Button)
        assert not time_travel.disabled
        pilot.app.set_focus(time_travel)
        await pilot.press(key)
        await pilot.pause()
        assert messages == [
            OpenAthenaTableRequest(
                table_ref=vm.catalog.table_detail.summary.ref,
                snapshot_id=43,
            )
        ]
    subscription.dispose()


@pytest.mark.asyncio
async def test_glue_page_does_not_swallow_unhandled_iceberg_descendants() -> None:
    vm, _inspector = _build_vm()
    await vm.setup()

    async with _GlueIcebergApp(vm).run_test(size=(100, 30)) as pilot:
        await pilot.click("#glue-iceberg-tab-snapshots")
        await pilot.pause()
        table = pilot.app.query_one("#glue-iceberg-table", DataTable)
        pilot.app.set_focus(table)
        page = pilot.app.query_one(GluePage)

        assert page.activate_focused(space=False) is False
        assert page.activate_focused(space=True) is False


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
        assert retry in {widget for _slot, widget in pilot.app.query_one(GluePage)._focus_targets()}

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
