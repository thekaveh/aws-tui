"""Interpretations belong to loaded table state; read plans precede admission."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import timedelta

import pytest

from aws_tui.domain.data_catalog import TableFormat
from aws_tui.domain.filesystem import PermissionDeniedError
from aws_tui.domain.iceberg import IcebergCoverage, IcebergHistoryEntry, IcebergInspection
from aws_tui.vm.file_manager.pane_vm import PaneState
from tests.helpers import wait_until
from tests.unit.vm.glue.test_iceberg_vm import ICEBERG_REF, RecordingInspector, make_vm


@asynccontextmanager
async def managed(source=None, **kwargs):
    vm, source, _hub = make_vm(source, **kwargs)
    try:
        await vm.bind_table(ICEBERG_REF, table_format=TableFormat.ICEBERG)
        yield vm, source
    finally:
        await vm.shutdown()
        vm.dispose()


async def test_read_plan_is_acknowledged_before_initial_load_and_retry():
    async with managed() as (vm, source):
        assert hasattr(vm, "set_plan_presenter"), "Metadata queries lack preflight presentation"
        plans = []

        async def present(plan):
            assert len(source.calls) == len(plans)
            assert plan.table_ref == ICEBERG_REF
            assert (plan.collection, plan.row_limit, plan.request_limit) == ("snapshots", 100, 101)
            plans.append(plan)
            return True

        release_presenter = vm.set_plan_presenter(present)
        try:
            assert await vm.select_view("snapshots")
            assert await vm.retry()
            assert len(plans) == 2
            assert len(source.calls) == 2
        finally:
            release_presenter()


async def test_declined_or_changed_plan_owner_never_reaches_provider():
    async with managed() as (vm, source):
        assert hasattr(vm, "set_plan_presenter"), "Metadata queries lack preflight presentation"

        async def unavailable(_plan):
            return False

        release_presenter = vm.set_plan_presenter(unavailable)
        try:
            assert not await vm.select_view("snapshots")
            assert source.calls == []
            assert vm.state is PaneState.EMPTY
        finally:
            release_presenter()


async def test_cancelled_preflight_cannot_submit_after_suppressed_cancellation():
    entered, cancelled, release = (asyncio.Event() for _ in range(3))
    async with managed() as (vm, source):
        assert hasattr(vm, "set_plan_presenter"), "Metadata queries lack preflight presentation"

        async def present(_plan):
            entered.set()
            try:
                await release.wait()
            except asyncio.CancelledError:
                cancelled.set()
                await release.wait()
            return True

        dispose = vm.set_plan_presenter(present)
        pending = asyncio.create_task(vm.select_view("snapshots"))
        try:
            await wait_until(entered.is_set, what="metadata preflight entered")
            pending.cancel()
            await wait_until(cancelled.is_set, what="metadata preflight cancellation delivered")
            assert source.calls == []
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await pending
            assert source.calls == []
            assert vm.state is PaneState.EMPTY
        finally:
            release.set()
            dispose()
            if not pending.done():
                pending.cancel()
                await asyncio.gather(pending, return_exceptions=True)


async def test_comparison_uses_loaded_rows_and_pin_survives_view_change_without_query():
    async with managed(page_size=1) as (vm, source):
        await vm.select_view("snapshots")
        assert vm.select_snapshot(43)
        assert hasattr(vm, "pin_comparison_snapshot"), "No explicit table-bound snapshot A exists"
        assert vm.pin_comparison_snapshot()
        assert vm.comparison_snapshot_id == 43
        await vm.load_more()
        assert vm.select_snapshot(42)
        before = list(source.calls)
        report = vm.snapshot_comparison
        assert report.table_ref == ICEBERG_REF
        assert (report.left.snapshot_id, report.right.snapshot_id) == (43, 42)
        assert source.calls == before
        await vm.select_view("files")
        assert vm.selected_snapshot_id is None
        assert vm.comparison_snapshot_id == 43
        await vm.bind_table(
            replace(ICEBERG_REF, region="eu-west-2"), table_format=TableFormat.ICEBERG
        )
        assert vm.comparison_snapshot_id is None
        assert vm.snapshot_comparison is None


async def test_observation_uses_all_fetched_rows_not_local_page_window():
    source = RecordingInspector()
    source.files = tuple(replace(source.files[0], file_path=f"s3://fixture/{i}") for i in range(3))

    async def listed(ref):
        source.calls.append(("files", ref))
        return IcebergInspection(source.files, IcebergCoverage("complete", 1000, "files", ref))

    source.list_files = listed
    async with managed(source, page_size=1) as (vm, _):
        await vm.select_view("files")
        assert len(vm.files) == 1
        assert hasattr(vm, "observation_for"), "No timestamped current observation exists"
        report = vm.observation_for("files")
        assert report.sample_size == report.denominator == 3
        assert report.observed_at is not None
        timestamp = report.observed_at
        await vm.load_more()
        assert vm.observation_for("files") == report
        assert vm.observed_at_for("files") == timestamp


async def test_unknown_content_and_duplicate_summary_are_retained_for_explanation():
    source = RecordingInspector()
    source.files = (replace(source.files[0], content=999),)
    source.snapshots = (
        replace(source.snapshots[0], summary=(("total-records", "1"), ("total-records", "2"))),
    )
    async with managed(source) as (vm, _):
        assert await vm.select_view("files")
        assert vm.state is PaneState.IDLE
        assert vm.files[0].content == 999
        assert await vm.select_view("snapshots")
        assert vm.select_snapshot(source.snapshots[0].snapshot_id)
        detail = vm.selected_snapshot_detail
        metric = next(m for m in detail.metrics if m.key == "total-records")
        assert metric.value is None
        assert metric.raw_values == ("1", "2")


async def test_replaced_presenter_and_old_disposer_cannot_authorize_or_remove_new_owner():
    entered, release = asyncio.Event(), asyncio.Event()
    async with managed() as (vm, source):

        async def old(_plan):
            entered.set()
            await release.wait()
            return True

        dispose_old = vm.set_plan_presenter(old)
        pending = asyncio.create_task(vm.select_view("snapshots"))
        try:
            await wait_until(entered.is_set, what="old metadata plan pending")
            new_plans = []

            async def new(plan):
                new_plans.append(plan)
                return True

            dispose_new = vm.set_plan_presenter(new)
            dispose_old()
            release.set()
            assert not await pending
            assert source.calls == []
            assert await vm.retry()
            assert len(new_plans) == 1
            assert len(source.calls) == 1
            dispose_new()
        finally:
            release.set()
            if not pending.done():
                pending.cancel()
                await asyncio.gather(pending, return_exceptions=True)


async def test_shutdown_drains_actual_preflight_without_submitting_late_query():
    entered, cancelled, release = (asyncio.Event() for _ in range(3))
    async with managed() as (vm, source):

        async def present(_plan):
            entered.set()
            try:
                await release.wait()
            except asyncio.CancelledError:
                cancelled.set()
                await release.wait()
            return True

        dispose = vm.set_plan_presenter(present)
        pending = asyncio.create_task(vm.select_view("snapshots"))
        shutdown = None
        try:
            await wait_until(entered.is_set, what="preflight awaiting renderer")
            shutdown = asyncio.create_task(vm.shutdown())
            await wait_until(cancelled.is_set, what="shutdown cancels preflight")
            assert not shutdown.done()
            assert source.calls == []
            release.set()
            await shutdown
            assert not await pending
            assert source.calls == []
            assert not vm.available
        finally:
            release.set()
            dispose()
            await asyncio.gather(
                *(t for t in (pending, shutdown) if t is not None), return_exceptions=True
            )


async def test_failed_refresh_keeps_successful_observation_time_and_rows():
    async with managed() as (vm, source):
        await vm.select_view("files")
        report = vm.observation_for("files")
        source.errors["files"] = PermissionDeniedError("synthetic denied refresh")
        assert not await vm.retry()
        assert vm.state is PaneState.FORBIDDEN
        assert vm.observation_for("files") == report
        assert vm.observed_at_for("files") == report.observed_at
        assert len(source.calls) == 2


async def test_snapshot_refresh_drops_pin_if_its_row_is_no_longer_in_loaded_metadata():
    async with managed() as (vm, source):
        await vm.select_view("snapshots")
        assert vm.select_snapshot(43)
        assert vm.pin_comparison_snapshot()
        source.snapshots = tuple(r for r in source.snapshots if r.snapshot_id != 43)
        assert await vm.retry()
        assert vm.comparison_snapshot_id is None
        assert vm.snapshot_comparison is None


async def test_duplicate_partition_keys_remain_a_shape_error():
    source = RecordingInspector()
    source.partitions = (replace(source.partitions[0], values=(("day", "one"), ("day", "two"))),)
    async with managed(source) as (vm, _):
        assert not await vm.select_view("partitions")
        assert vm.state is PaneState.ERROR
        assert vm.observation_for("partitions") is None


@pytest.mark.parametrize("bad_code", [-1, True, "1"])
async def test_unknown_content_support_does_not_coerce_malformed_codes(bad_code):
    source = RecordingInspector()
    source.files = (replace(source.files[0], content=bad_code),)
    async with managed(source) as (vm, _):
        assert not await vm.select_view("files")
        assert vm.state is PaneState.ERROR
        assert vm.observation_for("files") is None


async def test_ancestry_uses_history_beyond_the_visible_page_without_fetching_more():
    source = RecordingInspector()
    source.snapshots = (
        replace(source.snapshots[0], snapshot_id=10, parent_id=None),
        replace(source.snapshots[1], snapshot_id=30, parent_id=20),
    )
    committed = source.snapshots[1].committed_at
    source.history = (
        IcebergHistoryEntry(committed, 30, 20, True),
        IcebergHistoryEntry(committed - timedelta(minutes=1), 20, 10, True),
    )
    async with managed(source, page_size=1) as (vm, _):
        await vm.select_view("snapshots")
        await vm.load_more()
        assert vm.select_snapshot(10)
        assert vm.pin_comparison_snapshot()
        await vm.select_view("history")
        assert [r.snapshot_id for r in vm.history] == [30]
        await vm.select_view("snapshots")
        assert vm.select_snapshot(30)
        before = list(source.calls)
        assert vm.snapshot_comparison.ancestry.path == (10, 20, 30)
        assert source.calls == before


async def test_table_invalidation_during_preflight_refuses_old_acknowledgement():
    entered, release = asyncio.Event(), asyncio.Event()
    async with managed() as (vm, source):

        async def present(_plan):
            entered.set()
            try:
                await release.wait()
            except asyncio.CancelledError:
                await release.wait()
            return True

        dispose = vm.set_plan_presenter(present)
        pending = asyncio.create_task(vm.select_view("snapshots"))
        try:
            await wait_until(entered.is_set, what="table-scoped preflight started")
            vm.clear_table()
            release.set()
            assert not await pending
            assert source.calls == []
            assert vm.table_ref is None
        finally:
            release.set()
            dispose()
            if not pending.done():
                pending.cancel()
                await asyncio.gather(pending, return_exceptions=True)


async def test_presenter_released_from_loading_notification_never_falls_back_to_headless():
    async with managed() as (vm, source):
        shown = []

        async def present(plan):
            shown.append(plan)
            return True

        dispose = vm.set_plan_presenter(present)

        def remove_at_loading(_name):
            if vm.state is PaneState.LOADING:
                dispose()

        subscription = vm.on_property_changed.subscribe(remove_at_loading)
        try:
            assert not await vm.select_view("snapshots")
            assert source.calls == []
            assert shown == []
            assert not await vm.retry()
            assert source.calls == []
        finally:
            subscription.dispose()
            dispose()


async def test_installing_same_callback_twice_still_has_distinct_lease_ownership():
    async with managed() as (vm, source):
        plans = []

        async def present(plan):
            plans.append(plan)
            return True

        dispose_old = vm.set_plan_presenter(present)
        dispose_new = vm.set_plan_presenter(present)
        try:
            dispose_old()
            assert await vm.select_view("snapshots")
            assert len(plans) == 1
            assert len(source.calls) == 1
        finally:
            dispose_new()


async def test_uncancelling_presenter_cannot_revive_retired_request():
    entered, cancelled, release = (asyncio.Event() for _ in range(3))
    async with managed() as (vm, source):

        async def present(_plan):
            entered.set()
            try:
                await release.wait()
            except asyncio.CancelledError:
                current = asyncio.current_task()
                while current.cancelling():
                    current.uncancel()
                cancelled.set()
                await release.wait()
            return True

        dispose = vm.set_plan_presenter(present)
        pending = asyncio.create_task(vm.select_view("snapshots"))
        try:
            await wait_until(entered.is_set, what="uncancelling presenter entered")
            pending.cancel()
            await wait_until(cancelled.is_set, what="presenter erased its cancellation")
            assert source.calls == []
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await pending
            assert source.calls == []
        finally:
            release.set()
            dispose()
            if not pending.done():
                pending.cancel()
                await asyncio.gather(pending, return_exceptions=True)


async def test_real_inspector_runner_records_no_call_for_detail_pin_and_comparison():
    from vmx import NULL_DISPATCHER, MessageHub

    from aws_tui.domain.iceberg import IcebergInspector
    from aws_tui.vm.glue.iceberg_vm import GlueIcebergVM
    from tests.unit.domain.test_iceberg import CONTEXT, TABLE, RecordingRunner, _query_result

    runner = RecordingRunner(
        _query_result(
            ("committed_at", "snapshot_id", "parent_id", "operation", "manifest_list", "summary"),
            (
                (
                    "2026-07-26 12:30:00+00:00",
                    "42",
                    "41",
                    "append",
                    "s3://fixture/42.avro",
                    "{added-records=10}",
                ),
                (
                    "2026-07-26 12:29:00+00:00",
                    "41",
                    "40",
                    "append",
                    "s3://fixture/41.avro",
                    "{added-records=7}",
                ),
            ),
        )
    )
    vm = GlueIcebergVM(
        inspector=IcebergInspector(runner=runner, context=CONTEXT),
        hub=MessageHub(),
        dispatcher=NULL_DISPATCHER,
    )
    vm.construct()
    try:
        await vm.bind_table(TABLE, table_format=TableFormat.ICEBERG)
        assert await vm.select_view("snapshots")
        runner.calls.clear()
        assert vm.select_snapshot(41)
        assert vm.selected_snapshot_detail.snapshot_id == 41
        assert vm.pin_comparison_snapshot()
        assert vm.select_snapshot(42)
        comparison = vm.snapshot_comparison
        assert (comparison.left.snapshot_id, comparison.right.snapshot_id) == (41, 42)
        from aws_tui.ui.widgets.glue.iceberg_info import comparison_text, snapshot_text

        assert "added-records" in snapshot_text(vm.selected_snapshot_detail)
        assert "+3 records" in comparison_text(comparison)
        assert runner.calls == []
    finally:
        await vm.shutdown()
        vm.dispose()
