"""Peek rows and their identity follow the selected Iceberg snapshot."""

from __future__ import annotations

import asyncio
import threading

import pytest
from textual.coordinate import Coordinate
from textual.widgets import Button, DataTable, Static

from aws_tui.infra.duckdb import DuckDbOutcome, DuckDbResult, InMemoryDuckDb
from aws_tui.ui.widgets.glue.page import GluePage
from aws_tui.vm.file_manager.pane_vm import PaneState
from tests.helpers import drain_workers, focus_and_settle, wait_until
from tests.unit.ui.glue.test_iceberg_view import _build_vm, _GlueIcebergApp
from tests.unit.vm.glue.test_iceberg_preview_vm import _build


async def _tab(pilot, view):
    await focus_and_settle(pilot.app.query_one(f"#glue-iceberg-tab-{view}"))
    await pilot.press("enter")
    await drain_workers(pilot.app)
    await pilot.pause()


@pytest.mark.asyncio
async def test_keyboard_peek_reloads_changed_snapshot_and_caches_unchanged_snapshot():
    port = InMemoryDuckDb(columns=("id",), rows=(("from-42",),))
    vm, _ = _build_vm(duckdb_port=port)
    await vm.setup()
    try:
        async with _GlueIcebergApp(vm).run_test(size=(120, 40)) as pilot:
            await _tab(pilot, "snapshots")
            table = pilot.app.query_one("#glue-iceberg-table", DataTable)
            await focus_and_settle(table)
            await pilot.press("down")
            await wait_until(
                lambda: vm.catalog.iceberg.selected_snapshot_id == 42, what="snapshot 42 selected"
            )
            await _tab(pilot, "preview")
            assert len(port.queries) == 1
            assert table.get_cell_at(Coordinate(0, 0)).plain == "from-42"
            assert "snapshot 42" in str(
                pilot.app.query_one("#glue-iceberg-footer", Static).render()
            )
            assert pilot.app.query_one("#glue-iceberg-more", Button).disabled
            assert not pilot.app.query_one("#glue-iceberg-retry", Button).display

            await _tab(pilot, "snapshots")
            await focus_and_settle(table)
            await pilot.press("up")
            await wait_until(
                lambda: vm.catalog.iceberg.selected_snapshot_id == 43, what="snapshot 43 selected"
            )
            port.rows = (("from-43",),)
            await _tab(pilot, "preview")
            assert len(port.queries) == 2
            assert "snapshot_from_id := 43" in port.queries[-1][0]
            assert table.get_cell_at(Coordinate(0, 0)).plain == "from-43"
            assert "snapshot 43" in str(
                pilot.app.query_one("#glue-iceberg-footer", Static).render()
            )
            assert pilot.app.query_one("#glue-iceberg-more", Button).disabled
            assert not pilot.app.query_one("#glue-iceberg-retry", Button).display

            await _tab(pilot, "snapshots")
            await _tab(pilot, "preview")
            await _tab(pilot, "preview")
            assert len(port.queries) == 2
    finally:
        await vm.shutdown()
        vm.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", [DuckDbOutcome.FORBIDDEN, DuckDbOutcome.CANCELLED])
async def test_failed_or_cancelled_reload_keeps_successful_rows_snapshot(outcome):
    port = InMemoryDuckDb(columns=("id",), rows=(("from-42",),))
    vm = _build(port)
    vm.bind("s3://bucket/table", profile="synthetic", region="us-east-1")
    try:
        await vm.load(42)
        port.outcome = outcome
        await vm.load(43)
        assert vm.rows == (("from-42",),)
        assert vm.snapshot_id == 42
        assert vm.state is (
            PaneState.FORBIDDEN if outcome is DuckDbOutcome.FORBIDDEN else PaneState.IDLE
        )
    finally:
        vm.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "outcome", [DuckDbOutcome.FORBIDDEN, DuckDbOutcome.CANCELLED, DuckDbOutcome.FAILED]
)
async def test_keyboard_failed_snapshot_reload_retains_label_and_offers_explicit_retry(outcome):
    port = InMemoryDuckDb(columns=("id",), rows=(("from-42",),))
    vm, _ = _build_vm(duckdb_port=port)
    await vm.setup()
    try:
        async with _GlueIcebergApp(vm).run_test(size=(120, 40)) as pilot:
            await _tab(pilot, "snapshots")
            table = pilot.app.query_one("#glue-iceberg-table", DataTable)
            await focus_and_settle(table)
            await pilot.press("down")
            await _tab(pilot, "preview")
            assert vm.catalog.iceberg.preview.snapshot_id == 42
            await _tab(pilot, "snapshots")
            await focus_and_settle(table)
            await pilot.press("up")
            port.outcome = outcome
            await _tab(pilot, "preview")
            assert len(port.queries) == 2
            assert vm.catalog.iceberg.selected_snapshot_id == 43
            assert vm.catalog.iceberg.preview.snapshot_id == 42
            assert table.get_cell_at(Coordinate(0, 0)).plain == "from-42"
            footer = str(pilot.app.query_one("#glue-iceberg-footer", Static).render())
            assert "snapshot 42" in footer
            assert "snapshot 43" not in footer
            retry = pilot.app.query_one("#glue-iceberg-retry", Button)
            assert retry.display
            assert not retry.disabled
            assert pilot.app.query_one("#glue-iceberg-more", Button).disabled
            await _tab(pilot, "preview")
            assert len(port.queries) == 2, "a cancelled/failed attempt must not loop on repaint"

            port.outcome = DuckDbOutcome.OK
            port.rows = (("from-43",),)
            await focus_and_settle(retry)
            await pilot.press("enter")
            await drain_workers(pilot.app)
            await pilot.pause()
            assert len(port.queries) == 3
            assert "snapshot_from_id := 43" in port.queries[-1][0]
            assert table.get_cell_at(Coordinate(0, 0)).plain == "from-43"
            assert "snapshot 43" in str(
                pilot.app.query_one("#glue-iceberg-footer", Static).render()
            )
            assert not retry.display
    finally:
        await vm.shutdown()
        vm.dispose()


class _SnapshotPort:
    def __init__(self):
        self.entered = threading.Event()
        self.release = threading.Event()
        self.returned = threading.Event()

    def query(self, sql, *, profile, region):
        if "snapshot_from_id := 43" in sql:
            self.entered.set()
            assert self.release.wait(10), "controlled snapshot scan was never released"
            self.returned.set()
        value = "table-b" if "table-b" in sql else "table-a"
        return DuckDbResult(outcome=DuckDbOutcome.OK, columns=("id",), rows=((value,),))

    def interrupt(self):
        self.release.set()


@pytest.mark.asyncio
@pytest.mark.parametrize("superseding", ["cancel", "rebind"])
async def test_pending_snapshot_identity_is_retained_and_late_completion_is_rejected(superseding):
    port = _SnapshotPort()
    vm = _build(port)
    vm.bind("s3://bucket/table-a", profile="synthetic", region="us-east-1")
    await vm.load(42)
    loading = asyncio.create_task(vm.load(43))
    try:
        await wait_until(port.entered.is_set, what="snapshot 43 scan entered")
        assert vm.state is PaneState.LOADING
        assert vm.snapshot_id == 42
        assert vm.rows == (("table-a",),)
        if superseding == "cancel":
            loading.cancel()
            await asyncio.gather(loading, return_exceptions=True)
            expected = 42, (("table-a",),)
        else:
            vm.bind("s3://bucket/table-b", profile="synthetic", region="us-east-1")
            assert vm.snapshot_id is None
            assert vm.rows == ()
            await vm.load(77)
            expected = 77, (("table-b",),)
        port.release.set()
        await asyncio.gather(loading, return_exceptions=True)
        await wait_until(port.returned.is_set, what="old scan returned")
        assert (vm.snapshot_id, vm.rows) == expected
    finally:
        port.release.set()
        await asyncio.gather(loading, return_exceptions=True)
        vm.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize(("rows", "snapshot"), [((), 43), ((("current",),), None)])
async def test_empty_and_unpinned_success_replace_previous_snapshot_atomically(rows, snapshot):
    port = InMemoryDuckDb(columns=("id",), rows=(("old",),))
    vm = _build(port)
    vm.bind("s3://bucket/table", profile="synthetic", region="us-east-1")
    try:
        await vm.ensure_loaded(42)
        port.rows = rows
        await vm.ensure_loaded(snapshot)
        assert vm.rows == rows
        assert vm.snapshot_id == snapshot
        assert vm.state is PaneState.IDLE
        await vm.ensure_loaded(snapshot)
        assert len(port.queries) == 2
    finally:
        vm.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", [DuckDbOutcome.FORBIDDEN, DuckDbOutcome.CANCELLED])
async def test_retained_full_page_cannot_page_into_the_attempted_snapshot(outcome):
    port = InMemoryDuckDb(columns=("id",), rows=tuple((str(n),) for n in range(100)))
    vm, _ = _build_vm(duckdb_port=port)
    await vm.setup()
    try:
        async with _GlueIcebergApp(vm).run_test(size=(120, 40)) as pilot:
            await _tab(pilot, "snapshots")
            table = pilot.app.query_one("#glue-iceberg-table", DataTable)
            await focus_and_settle(table)
            await pilot.press("down")
            await _tab(pilot, "preview")
            assert not pilot.app.query_one("#glue-iceberg-more", Button).disabled
            await _tab(pilot, "snapshots")
            await focus_and_settle(table)
            await pilot.press("up")
            port.outcome = outcome
            await _tab(pilot, "preview")
            assert vm.catalog.iceberg.preview.snapshot_id == 42
            assert pilot.app.query_one("#glue-iceberg-more", Button).disabled
            assert pilot.app.query_one("#glue-iceberg-retry", Button).display
            assert not pilot.app.query_one(GluePage).can_load_more(
                focused_ids=frozenset({"glue-iceberg-view"})
            )
            calls = len(port.queries)
            port.outcome = DuckDbOutcome.OK
            assert not await vm.catalog.iceberg.preview.load_more(43)
            await pilot.app.query_one(GluePage).action_load_more()
            await drain_workers(pilot.app)
            await pilot.pause()
            assert len(port.queries) == calls
            assert vm.catalog.iceberg.preview.limit == 100
            assert vm.catalog.iceberg.preview.snapshot_id == 42
    finally:
        await vm.shutdown()
        vm.dispose()
