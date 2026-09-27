from __future__ import annotations

import asyncio
import gc
from collections.abc import Awaitable, Callable
from dataclasses import replace
from typing import ClassVar

import pytest
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.coordinate import Coordinate
from textual.widgets import Button, DataTable, Static
from textual.worker import NoActiveWorker, Worker, get_current_worker
from vmx import NULL_DISPATCHER, MessageHub
from vmx.messages.protocols import Message

from aws_tui.domain.data_catalog import TableFormat
from aws_tui.infra.connection_resolver import Connection
from aws_tui.infra.duckdb import DuckDbOutcome, DuckDbPort, InMemoryDuckDb
from aws_tui.ui.widgets.glue.iceberg_view import GlueIcebergView
from aws_tui.ui.widgets.glue.page import GluePage
from aws_tui.vm.chrome.focus_coordinator_vm import FocusCoordinatorVM, FocusSlot
from aws_tui.vm.file_manager.pane_vm import PaneState
from aws_tui.vm.glue.page_vm import GluePageVM
from tests.helpers import focus_and_settle, wait_until
from tests.unit.vm.glue._fake_glue import InMemoryGlue
from tests.unit.vm.glue.test_iceberg_vm import ICEBERG_REF, OTHER_REF, RecordingInspector


def _build_vm(
    *,
    iceberg: bool = True,
    profile: str | None = "dev",
    duckdb_port: DuckDbPort | None = None,
) -> tuple[GluePageVM, RecordingInspector]:
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
            profile=profile,
        ),
        hub=hub,
        dispatcher=NULL_DISPATCHER,
        # Never a real engine in a unit test: the widget tests that click the
        # Peek tab inject rows through this double.
        duckdb_port=duckdb_port or InMemoryDuckDb(),
    )
    vm.construct()
    return vm, inspector


async def _wait_for_paint(pilot: object, predicate: Callable[[], bool], *, what: str) -> None:
    """Pause until ``predicate`` holds, then return.

    DataTable cursor state is painted by a refresh the view schedules through
    ``call_after_refresh``, so asserting after a fixed number of pauses assumes
    how many frames that takes. On windows-latest it takes more, which is how
    `assert table.cursor_row == 0` failed with 1 while macOS and the other
    Pythons passed the same commit.

    Bounded, and names what never settled so a real regression still fails.
    """
    for _ in range(50):
        if predicate():
            return
        await pilot.pause()  # type: ignore[attr-defined]
    raise AssertionError(f"never settled: {what}")


class _GlueIcebergApp(App[None]):
    BINDINGS: ClassVar[list[Binding]] = [
        Binding("enter", "activate_enter", "", show=False, priority=True),
        Binding("space", "activate_space", "", show=False, priority=True),
    ]

    def __init__(
        self,
        vm: GluePageVM,
        *,
        dispatcher: Callable[[str], Awaitable[None] | None] | None = None,
    ) -> None:
        super().__init__()
        self._vm = vm
        self._dispatcher = dispatcher
        self.action_ids: list[str] = []
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

    def action_dispatch(self, action_id: str) -> Awaitable[None] | None:
        self.action_ids.append(action_id)
        if self._dispatcher is not None:
            return self._dispatcher(action_id)
        return None


@pytest.mark.asyncio
async def test_iceberg_metadata_region_is_hidden_for_non_iceberg_table() -> None:
    vm, _inspector = _build_vm(iceberg=False)
    await vm.setup()

    async with _GlueIcebergApp(vm).run_test() as pilot:
        await pilot.pause()

        assert not vm.catalog.iceberg.available
        assert not pilot.app.query_one(GlueIcebergView).display


def test_iceberg_view_coalesces_property_bursts_into_one_refresh() -> None:
    vm, _inspector = _build_vm()
    view = GlueIcebergView(vm.catalog.iceberg)
    scheduled: list[Callable[[], None]] = []
    refreshes: list[None] = []
    view.call_after_refresh = scheduled.append  # type: ignore[method-assign]
    view._refresh = lambda: refreshes.append(None)  # type: ignore[method-assign]

    view._on_vm_changed("items")
    view._on_vm_changed("state")
    view._on_vm_changed("has_more")

    assert len(scheduled) == 1
    scheduled[0]()
    assert refreshes == [None]

    view._on_vm_changed("selected_snapshot_id")
    assert len(scheduled) == 2


@pytest.mark.asyncio
async def test_iceberg_view_composes_compact_tabs_table_and_time_travel_control() -> None:
    vm, inspector = _build_vm()
    await vm.setup()

    async with _GlueIcebergApp(vm).run_test(size=(140, 42)) as pilot:
        await pilot.pause()
        iceberg = pilot.app.query_one(GlueIcebergView)

        assert iceberg.display
        # Six metadata tabs plus Peek, present because this fixture's table
        # has an S3 location and the connection carries a profile.
        assert len(list(iceberg.query(".glue-iceberg-tab"))) == 7
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
        await wait_until(
            lambda: (
                vm.catalog.iceberg.active_view == "snapshots"
                and len(vm.catalog.iceberg.snapshots) > 1
            ),
            what="the view model holds the snapshot rows",
        )
        table.move_cursor(row=0)
        # Row 0 is already the default selection here, so a stale read would
        # pass for the wrong reason. Wait for the cursor and assert the
        # selection the highlight produced.
        await wait_until(
            lambda: table.cursor_row == 0,
            what="the cursor settled on row 0",
        )
        await pilot.pause()

        assert vm.catalog.iceberg.selected_snapshot_id == 43
        assert not pilot.app.query_one("#glue-iceberg-time-travel", Button).disabled


@pytest.mark.asyncio
async def test_time_travel_button_dispatches_the_registry_action_once() -> None:
    vm, _inspector = _build_vm()
    await vm.setup()

    async with _GlueIcebergApp(vm).run_test(size=(140, 42)) as pilot:
        await pilot.click("#glue-iceberg-tab-snapshots")
        await pilot.pause()

        await pilot.click("#glue-iceberg-time-travel")
        await pilot.pause()

        assert pilot.app.action_ids == ["glue.time_travel_in_athena"]


@pytest.mark.asyncio
async def test_time_travel_async_dispatch_is_created_and_completed_by_its_worker(
    recwarn: pytest.WarningsRecorder,
) -> None:
    vm, _inspector = _build_vm()
    await vm.setup()
    started = asyncio.Event()
    release = asyncio.Event()
    created_by: list[Worker[object] | None] = []
    completed: list[str] = []

    def dispatch(action_id: str) -> Awaitable[None]:
        try:
            created_by.append(get_current_worker())
        except NoActiveWorker:
            created_by.append(None)

        async def complete() -> None:
            started.set()
            await release.wait()
            completed.append(action_id)

        return complete()

    async with _GlueIcebergApp(vm, dispatcher=dispatch).run_test(size=(140, 42)) as pilot:
        await pilot.click("#glue-iceberg-tab-snapshots")
        await pilot.pause()
        await pilot.click("#glue-iceberg-time-travel")
        await asyncio.wait_for(started.wait(), timeout=2)

        release.set()
        await pilot.app.workers.wait_for_complete()
        await pilot.pause()

        assert pilot.app.action_ids == ["glue.time_travel_in_athena"]
        assert len(created_by) == 1
        assert created_by[0] is not None
        assert completed == ["glue.time_travel_in_athena"]

    gc.collect()
    assert not [
        warning
        for warning in recwarn
        if issubclass(warning.category, RuntimeWarning)
        and "was never awaited" in str(warning.message)
    ]


@pytest.mark.asyncio
async def test_replaced_time_travel_async_dispatch_cancels_only_the_superseded_worker(
    recwarn: pytest.WarningsRecorder,
) -> None:
    vm, _inspector = _build_vm()
    await vm.setup()
    first_started = asyncio.Event()
    first_cancelled = asyncio.Event()
    second_started = asyncio.Event()
    release_second = asyncio.Event()
    created_by: list[Worker[object] | None] = []
    completed: list[str] = []
    calls = 0

    def dispatch(action_id: str) -> Awaitable[None]:
        nonlocal calls
        calls += 1
        try:
            created_by.append(get_current_worker())
        except NoActiveWorker:
            created_by.append(None)
        current_call = calls

        async def complete() -> None:
            if current_call == 1:
                first_started.set()
                try:
                    await asyncio.Event().wait()
                except asyncio.CancelledError:
                    first_cancelled.set()
                    raise
            else:
                second_started.set()
                await release_second.wait()
                completed.append(action_id)

        return complete()

    async with _GlueIcebergApp(vm, dispatcher=dispatch).run_test(size=(140, 42)) as pilot:
        await pilot.click("#glue-iceberg-tab-snapshots")
        await pilot.pause()
        view = pilot.app.query_one(GlueIcebergView)
        view._time_travel_selected()
        await asyncio.wait_for(first_started.wait(), timeout=2)

        view._time_travel_selected()
        await asyncio.wait_for(first_cancelled.wait(), timeout=2)
        await asyncio.wait_for(second_started.wait(), timeout=2)

        release_second.set()
        await wait_until(
            lambda: completed == ["glue.time_travel_in_athena"],
            what="the second handoff completed",
        )
        await pilot.pause()

        assert pilot.app.action_ids == [
            "glue.time_travel_in_athena",
            "glue.time_travel_in_athena",
        ]
        assert len(created_by) == 2
        assert all(worker is not None for worker in created_by)
        assert completed == ["glue.time_travel_in_athena"]

    gc.collect()
    assert not [
        warning
        for warning in recwarn
        if issubclass(warning.category, RuntimeWarning)
        and "was never awaited" in str(warning.message)
    ]


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
        await focus_and_settle(tab)
        await pilot.press(key)
        await wait_until(
            lambda: len(inspector.calls) == 1,
            what="the history tab issued its inspector call",
        )
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
    async with _GlueIcebergApp(vm).run_test(size=(100, 30)) as pilot:
        snapshot_tab = pilot.app.query_one("#glue-iceberg-tab-snapshots")
        await focus_and_settle(snapshot_tab)
        await pilot.press(key)
        await wait_until(
            lambda: vm.catalog.iceberg.error_text is not None,
            what="the denied snapshot load surfaced an error",
        )
        await pilot.pause()

        inspector.errors.pop("snapshots")
        retry = pilot.app.query_one("#glue-iceberg-retry", Button)
        assert not retry.disabled
        await focus_and_settle(retry)
        await pilot.press(key)
        await wait_until(
            lambda: len(vm.catalog.iceberg.snapshots) == 1,
            what="the retry loaded the first snapshot page",
        )
        await pilot.pause()
        assert len(vm.catalog.iceberg.snapshots) == 1

        more = pilot.app.query_one("#glue-iceberg-more", Button)
        assert not more.disabled
        await focus_and_settle(more)
        await pilot.press(key)
        await wait_until(
            lambda: len(vm.catalog.iceberg.snapshots) == 2,
            what="the pager loaded the second snapshot page",
        )
        await pilot.pause()
        assert len(vm.catalog.iceberg.snapshots) == 2

        time_travel = pilot.app.query_one("#glue-iceberg-time-travel", Button)
        assert not time_travel.disabled
        await focus_and_settle(time_travel)
        await pilot.press(key)
        await pilot.pause()
        assert pilot.app.action_ids == ["glue.time_travel_in_athena"]


@pytest.mark.asyncio
async def test_glue_page_does_not_swallow_unhandled_iceberg_descendants() -> None:
    vm, _inspector = _build_vm()
    await vm.setup()

    async with _GlueIcebergApp(vm).run_test(size=(100, 30)) as pilot:
        await pilot.click("#glue-iceberg-tab-snapshots")
        await pilot.pause()
        table = pilot.app.query_one("#glue-iceberg-table", DataTable)
        await focus_and_settle(table)
        # Assert the precondition rather than assume it: without focus on the
        # table, ``activate_focused`` returns False for the wrong reason and the
        # test passes while proving nothing.
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
    notifications: list[str] = []
    selection_subscription = vm.catalog.iceberg.on_property_changed.subscribe(
        on_next=lambda name: notifications.append(name)
    )

    async with _GlueIcebergApp(vm).run_test(size=(100, 30)) as pilot:
        await pilot.click("#glue-iceberg-tab-snapshots")
        await pilot.pause()
        iceberg = pilot.app.query_one(GlueIcebergView)
        table = iceberg.query_one("#glue-iceberg-table", DataTable)

        # `on_data_table_row_highlighted` DROPS the event when
        # `vm.active_view != "snapshots"` or `cursor_row >= len(vm.snapshots)`
        # (iceberg_view.py:226-234), and the snapshots load through a lifecycle
        # worker started by the tab click. So moving the cursor before the view
        # model holds the rows fires a highlight that is silently discarded --
        # the selection then never changes, no matter how long anything waits.
        # That is what failed on windows-latest as `assert 43 == 42`, and what
        # made a 15s wait for the selection time out rather than settle.
        await wait_until(
            lambda: (
                vm.catalog.iceberg.active_view == "snapshots"
                and len(vm.catalog.iceberg.snapshots) > 1
            ),
            what="the view model holds the snapshot rows",
        )

        table.move_cursor(row=1)
        # The cursor moves synchronously; the view model is updated from the
        # `RowHighlighted` event, so the selection still reads the row-0
        # snapshot (43) on the line after the move.
        await wait_until(
            lambda: vm.catalog.iceberg.selected_snapshot_id == 42,
            what="the row-1 highlight reached the view model",
        )
        assert vm.catalog.iceberg.selected_snapshot_id == 42
        selection_notifications = notifications.count("selected_snapshot_id")

        iceberg._refresh()
        # `_refresh` re-arms `_enable_highlight` through `call_after_refresh`, so
        # the deferred callback lands a frame after the refresh itself. A single
        # pause observes the intermediate state and can see an extra highlight
        # notification.
        await pilot.pause()
        await pilot.pause()

        await _wait_for_paint(pilot, lambda: table.cursor_row == 1, what="cursor settled on row 1")
        assert vm.catalog.iceberg.selected_snapshot_id == 42
        assert notifications.count("selected_snapshot_id") == selection_notifications
        await pilot.click("#glue-iceberg-time-travel")
        # The button handler dispatches through `run_worker`; without draining it
        # the assertions below race the dispatch.
        await pilot.app.workers.wait_for_complete()
        assert vm.catalog.iceberg.selected_snapshot_id == 42
        assert pilot.app.action_ids == ["glue.time_travel_in_athena"]
    selection_subscription.dispose()


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
        # `on_data_table_row_highlighted` DROPS the event when
        # `vm.active_view != "snapshots"` or `cursor_row >= len(vm.snapshots)`
        # (iceberg_view.py:226-234), and the snapshots load through a lifecycle
        # worker started by the tab click. So moving the cursor before the view
        # model holds the rows fires a highlight that is silently discarded --
        # the selection then never changes, no matter how long anything waits.
        # That is what failed on windows-latest as `assert 43 == 42`, and what
        # made a 15s wait for the selection time out rather than settle.
        await wait_until(
            lambda: (
                vm.catalog.iceberg.active_view == "snapshots"
                and len(vm.catalog.iceberg.snapshots) > 1
            ),
            what="the view model holds the snapshot rows",
        )

        table.move_cursor(row=1)
        # The cursor moves synchronously; the view model is updated from the
        # `RowHighlighted` event, so the selection still reads the row-0
        # snapshot (43) on the line after the move.
        await wait_until(
            lambda: vm.catalog.iceberg.selected_snapshot_id == 42,
            what="the row-1 highlight reached the view model",
        )
        assert vm.catalog.iceberg.selected_snapshot_id == 42

        inspector.snapshots = tuple(row for row in inspector.snapshots if row.snapshot_id != 42)
        await vm.catalog.iceberg.retry()
        await pilot.pause()

        await _wait_for_paint(
            pilot,
            lambda: table.cursor_row == 0,
            what="cursor fell back to row 0 after the selected row vanished",
        )
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

        assert labels == ["Snaps", "Hist", "Mnfst", "Files", "Parts", "Refs", "Peek"]


@pytest.mark.asyncio
async def test_peek_tab_is_present_for_an_iceberg_table() -> None:
    vm, _ = _build_vm()
    await vm.setup()

    async with _GlueIcebergApp(vm).run_test(size=(100, 30)) as pilot:
        await pilot.pause()

        tab = pilot.app.query_one("#glue-iceberg-tab-preview")
        assert tab.display is True


@pytest.mark.asyncio
async def test_peek_tab_is_hidden_without_an_aws_profile() -> None:
    # The table itself is Iceberg-formatted (the metadata tabs stay visible);
    # only the profile is missing. Visibility must be honest: the Peek tab is
    # hidden, not present and broken. The tab is still *composed* -- always,
    # like its six siblings -- so that availability changing after mount (see
    # below) has something to turn visible.
    vm, _ = _build_vm(profile=None)
    await vm.setup()

    async with _GlueIcebergApp(vm).run_test(size=(100, 30)) as pilot:
        await pilot.pause()

        assert vm.catalog.iceberg.available
        assert not vm.catalog.iceberg.preview.available
        tab = pilot.app.query_one("#glue-iceberg-tab-preview")
        assert tab.display is False


@pytest.mark.asyncio
async def test_peek_tab_appears_after_navigating_to_an_iceberg_table() -> None:
    """Presence is decided at compose time; availability changes after mount.

    Landing on a non-Iceberg table first is the common case, so a tab that is
    only created when `available` happens to be true at mount is a feature the
    user can never reach.
    """
    vm, _ = _build_vm(iceberg=False)
    await vm.setup()
    app = _GlueIcebergApp(vm)
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        iceberg = vm.catalog.iceberg
        assert iceberg.preview.available is False

        await iceberg.bind_table(
            ICEBERG_REF, table_format=TableFormat.ICEBERG, location="s3://bkt/t"
        )
        await wait_until(
            lambda: iceberg.preview.available,
            what="the preview became available after selecting an Iceberg table",
        )
        await pilot.pause()

        tab = app.query_one("#glue-iceberg-tab-preview")
        assert tab.display is True


@pytest.mark.asyncio
async def test_peek_falls_back_to_the_first_tab_when_it_stops_being_available() -> None:
    port = InMemoryDuckDb(columns=("id",), rows=(("1",),))
    vm, _ = _build_vm(duckdb_port=port)
    await vm.setup()

    async with _GlueIcebergApp(vm).run_test(size=(100, 30)) as pilot:
        # Establish a non-default active metadata view first, so landing on
        # "snapshots" below proves the fallback goes to the *first* tab, not
        # merely back to whatever was active before Peek.
        await pilot.click("#glue-iceberg-tab-refs")
        await pilot.pause()
        await pilot.click("#glue-iceberg-tab-preview")
        await wait_until(
            lambda: vm.catalog.iceberg.preview.state is PaneState.IDLE,
            what="the preview pane finished loading",
        )
        await pilot.pause()

        # Navigate to a different Iceberg table with no usable S3 location:
        # the pane stays visible (still Iceberg-formatted), but Peek stops
        # being available.
        await vm.catalog.iceberg.bind_table(
            OTHER_REF, table_format=TableFormat.ICEBERG, location="not-s3"
        )
        await wait_until(
            lambda: not vm.catalog.iceberg.preview.available,
            what="the preview became unavailable on the new table",
        )
        await wait_until(
            lambda: vm.catalog.iceberg.active_view == "snapshots",
            what="the active view fell back to the first tab",
        )

        snapshots_tab = pilot.app.query_one("#glue-iceberg-tab-snapshots")
        refs_tab = pilot.app.query_one("#glue-iceberg-tab-refs")
        preview_tab = pilot.app.query_one("#glue-iceberg-tab-preview")
        # The view model settling and the widget repainting are two different
        # events: `-active` is applied by the scheduled `_refresh`, so waiting on
        # `active_view` alone and then pausing once asserts on whichever the
        # scheduler happened to reach first.
        await wait_until(
            lambda: snapshots_tab.has_class("-active"),
            what="the first tab to repaint as active",
        )
        assert snapshots_tab.has_class("-active")
        assert not refs_tab.has_class("-active")
        assert not preview_tab.has_class("-active")
        assert preview_tab.display is False
        footer = pilot.app.query_one("#glue-iceberg-footer", Static)
        assert "limit" not in str(footer.render())


@pytest.mark.asyncio
async def test_peek_renders_rows_and_keeps_null_distinct() -> None:
    port = InMemoryDuckDb(
        columns=("id", "name"),
        rows=(("1", "alice"), ("2", None)),
    )
    vm, _ = _build_vm(duckdb_port=port)
    await vm.setup()

    async with _GlueIcebergApp(vm).run_test(size=(100, 30)) as pilot:
        await pilot.click("#glue-iceberg-tab-preview")
        await wait_until(
            lambda: vm.catalog.iceberg.preview.state is PaneState.IDLE,
            what="the preview pane finished loading",
        )
        await pilot.pause()
        table = pilot.app.query_one("#glue-iceberg-table", DataTable)

        assert table.row_count == 2
        real_cell = table.get_cell_at(Coordinate(0, 1))
        assert real_cell.plain == "alice"
        assert real_cell.style == ""
        null_cell = table.get_cell_at(Coordinate(1, 1))
        # A real SQL NULL renders dimmed, never as the four literal
        # characters, so it is never confused with a column that genuinely
        # contains the string "NULL".
        assert null_cell.plain == "NULL"
        assert null_cell.style == "dim italic"


@pytest.mark.asyncio
async def test_peek_footer_reports_rows_and_limit_without_more_available_phrasing() -> None:
    port = InMemoryDuckDb(columns=("id",), rows=(("1",), ("2",)))
    vm, _ = _build_vm(duckdb_port=port)
    await vm.setup()

    async with _GlueIcebergApp(vm).run_test(size=(100, 30)) as pilot:
        await pilot.click("#glue-iceberg-tab-preview")
        await wait_until(
            lambda: vm.catalog.iceberg.preview.state is PaneState.IDLE,
            what="the preview pane finished loading",
        )
        await pilot.pause()
        footer = pilot.app.query_one("#glue-iceberg-footer", Static)

        assert str(footer.render()) == "2 rows · limit 100"
        assert "more available" not in str(footer.render())


@pytest.mark.asyncio
async def test_peek_more_button_reruns_the_query_at_the_next_row_limit() -> None:
    # A full page of exactly the current limit: honest paging, not a widened
    # local window -- ``has_more`` is real, and clicking the button issues a
    # genuinely new DuckDB scan at the next row-limit step.
    port = InMemoryDuckDb(columns=("id",), rows=tuple((str(i),) for i in range(100)))
    vm, _ = _build_vm(duckdb_port=port)
    await vm.setup()

    async with _GlueIcebergApp(vm).run_test(size=(100, 30)) as pilot:
        await pilot.click("#glue-iceberg-tab-preview")
        await wait_until(
            lambda: vm.catalog.iceberg.preview.state is PaneState.IDLE,
            what="the preview pane finished loading",
        )
        await pilot.pause()
        more = pilot.app.query_one("#glue-iceberg-more", Button)
        assert not more.disabled
        assert vm.catalog.iceberg.preview.limit == 100

        await pilot.click("#glue-iceberg-more")
        await wait_until(
            lambda: vm.catalog.iceberg.preview.limit == 1000,
            what="load-more advanced to the next row-limit step",
        )
        await wait_until(
            lambda: vm.catalog.iceberg.preview.state is PaneState.IDLE,
            what="the second preview scan finished",
        )
        await pilot.pause()

        assert [sql.endswith("LIMIT 100") for sql, _profile, _region in port.queries] == [
            True,
            False,
        ]
        assert port.queries[1][0].endswith("LIMIT 1000")
        # The canned double never grows past 100 rows, so the pane is
        # honestly out of pages after the second scan.
        assert pilot.app.query_one("#glue-iceberg-more", Button).disabled


@pytest.mark.asyncio
async def test_peek_pins_the_snapshot_selected_on_snaps_when_loaded_from_the_tab() -> None:
    # I1: the pane's stated goal is a preview "pinned to the selected
    # snapshot when one is chosen" -- this drives the whole path through the
    # widget (tab click, DataTable row selection, tab click) rather than
    # calling the VM directly, because the bug was in the *view*'s wiring:
    # ``on__iceberg_tab_selected`` called ``preview.load`` with no snapshot
    # at all, even though ``selected_snapshot_id`` was already read for the
    # Snaps table highlight one line away.
    port = InMemoryDuckDb(columns=("id",), rows=(("1",),))
    vm, _ = _build_vm(duckdb_port=port)
    await vm.setup()

    async with _GlueIcebergApp(vm).run_test(size=(100, 30)) as pilot:
        await pilot.click("#glue-iceberg-tab-snapshots")
        await wait_until(
            lambda: len(vm.catalog.iceberg.snapshots) > 1,
            what="the snapshots pane loaded",
        )
        await pilot.pause()
        table = pilot.app.query_one("#glue-iceberg-table", DataTable)
        table.focus()
        # Row 0 is snapshot 43 (the newest, and already the default
        # selection) -- picking row 1 instead proves the pin tracks a
        # deliberate selection rather than passing on the coincidence that
        # row 0 was selected anyway.
        table.move_cursor(row=1)
        await wait_until(
            lambda: vm.catalog.iceberg.selected_snapshot_id == 42,
            what="the Snaps table selection landed on snapshot 42",
        )
        await pilot.pause()

        await pilot.click("#glue-iceberg-tab-preview")
        await wait_until(
            lambda: vm.catalog.iceberg.preview.state is PaneState.IDLE,
            what="the preview pane finished loading",
        )
        await pilot.pause()

        assert len(port.queries) == 1
        assert "snapshot_from_id := 42" in port.queries[0][0]
        footer = pilot.app.query_one("#glue-iceberg-footer", Static)
        assert "· snapshot 42" in str(footer.render())


@pytest.mark.asyncio
async def test_peek_load_more_keeps_the_pinned_snapshot() -> None:
    # I1's second broken site: the "more" button re-ran the scan with no
    # snapshot at all.
    port = InMemoryDuckDb(columns=("id",), rows=tuple((str(i),) for i in range(100)))
    vm, _ = _build_vm(duckdb_port=port)
    await vm.setup()

    async with _GlueIcebergApp(vm).run_test(size=(100, 30)) as pilot:
        await pilot.click("#glue-iceberg-tab-snapshots")
        await wait_until(
            lambda: len(vm.catalog.iceberg.snapshots) > 1,
            what="the snapshots pane loaded",
        )
        await pilot.pause()
        table = pilot.app.query_one("#glue-iceberg-table", DataTable)
        table.focus()
        table.move_cursor(row=1)
        await wait_until(
            lambda: vm.catalog.iceberg.selected_snapshot_id == 42,
            what="the Snaps table selection landed on snapshot 42",
        )
        await pilot.pause()

        await pilot.click("#glue-iceberg-tab-preview")
        await wait_until(
            lambda: vm.catalog.iceberg.preview.state is PaneState.IDLE,
            what="the preview pane finished loading",
        )
        await pilot.pause()

        await pilot.click("#glue-iceberg-more")
        await wait_until(
            lambda: vm.catalog.iceberg.preview.limit == 1000,
            what="load-more advanced to the next row-limit step",
        )
        await wait_until(
            lambda: vm.catalog.iceberg.preview.state is PaneState.IDLE,
            what="the second preview scan finished",
        )
        await pilot.pause()

        assert len(port.queries) == 2
        assert "snapshot_from_id := 42" in port.queries[1][0]


@pytest.mark.asyncio
async def test_peek_retry_keeps_the_pinned_snapshot() -> None:
    # I1's third broken site: retry passed ``preview.snapshot_id``, which is
    # never set by anything but ``load`` itself -- always None in practice.
    error_port = InMemoryDuckDb(outcome=DuckDbOutcome.FAILED)
    vm, _ = _build_vm(duckdb_port=error_port)
    await vm.setup()

    async with _GlueIcebergApp(vm).run_test(size=(100, 30)) as pilot:
        await pilot.click("#glue-iceberg-tab-snapshots")
        await wait_until(
            lambda: len(vm.catalog.iceberg.snapshots) > 1,
            what="the snapshots pane loaded",
        )
        await pilot.pause()
        table = pilot.app.query_one("#glue-iceberg-table", DataTable)
        table.focus()
        table.move_cursor(row=1)
        await wait_until(
            lambda: vm.catalog.iceberg.selected_snapshot_id == 42,
            what="the Snaps table selection landed on snapshot 42",
        )
        await pilot.pause()

        await pilot.click("#glue-iceberg-tab-preview")
        await wait_until(
            lambda: vm.catalog.iceberg.preview.state is PaneState.ERROR,
            what="the preview pane finished failing",
        )
        await pilot.pause()

        await pilot.click("#glue-iceberg-retry")
        await wait_until(
            lambda: len(error_port.queries) == 2,
            what="retry re-ran the scan",
        )
        await pilot.pause()

        assert "snapshot_from_id := 42" in error_port.queries[1][0]


@pytest.mark.asyncio
async def test_peek_loads_the_newly_selected_table_while_it_stays_active() -> None:
    """Navigating A -> B with Peek active must read B, not sit blank.

    The pane only ever asked for a scan from tab selection, and selecting a
    different Iceberg table does not re-fire that -- so the rebind cleared the
    table and nothing reloaded it. The retry button was hidden on EMPTY too, so
    the pane offered no way out.
    """
    port = InMemoryDuckDb(columns=("id",), rows=(("1",),))
    vm, _ = _build_vm(duckdb_port=port)
    await vm.setup()

    async with _GlueIcebergApp(vm).run_test(size=(100, 30)) as pilot:
        await pilot.click("#glue-iceberg-tab-preview")
        table = pilot.app.query_one("#glue-iceberg-table", DataTable)
        await wait_until(lambda: table.row_count == 1, what="the first table's rows to render")

        port.rows = (("7",), ("8",))
        await vm.catalog.iceberg.bind_table(
            OTHER_REF, table_format=TableFormat.ICEBERG, location="s3://bkt/other"
        )

        await wait_until(
            lambda: table.row_count == 2,
            what="the newly selected table's rows to render",
        )
        assert vm.catalog.iceberg.preview.state is PaneState.IDLE
