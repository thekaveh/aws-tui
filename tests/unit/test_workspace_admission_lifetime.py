"""Independent ownership lifetime checks; fake engine only, no repo edits."""

import asyncio
import contextlib
import threading

import pytest

from aws_tui.domain.data_catalog import TableFormat
from aws_tui.domain.filesystem import ProviderError
from aws_tui.infra.duckdb import DuckDbOutcome, DuckDbResult
from tests.unit.test_workspace_admission import admission, bounded
from tests.unit.vm.glue._fake_glue import InMemoryGlue
from tests.unit.vm.glue.test_iceberg_vm import ICEBERG_REF, RecordingInspector
from tests.unit.vm.glue.test_page_vm import make_page_vm


async def settle():
    for _ in range(12):
        await asyncio.sleep(0)


class HeldPort:
    def __init__(self, outcomes=(DuckDbOutcome.OK,)):
        self.outcomes = outcomes
        self.entered = [threading.Event() for _ in outcomes]
        self.release = [threading.Event() for _ in outcomes]
        self.finished = [threading.Event() for _ in outcomes]
        self._lock = threading.Lock()
        self._next = 0

    def query(self, sql, *, profile, region):
        with self._lock:
            index = self._next
            self._next += 1
        self.entered[index].set()
        try:
            assert self.release[index].wait(5), "fake query gate was never released"
            outcome = self.outcomes[index]
            if isinstance(outcome, BaseException):
                raise outcome
            return DuckDbResult(outcome=outcome, columns=("value",), rows=((str(index),),))
        finally:
            self.finished[index].set()

    def interrupt(self):
        # An interrupt request may precede actual thread termination.
        pass

    def release_all(self):
        for event in self.release:
            event.set()


async def bind(page, location="s3://fake/table"):
    await page.catalog.iceberg.bind_table(
        ICEBERG_REF, table_format=TableFormat.ICEBERG, location=location
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["success", "provider-error", "cancel"])
async def test_metadata_public_indicator_releases_for_every_completion(outcome):
    inspector = RecordingInspector()
    page = make_page_vm(InMemoryGlue(), iceberg_inspector=inspector)
    vm = page.catalog.iceberg
    task = None
    try:
        assert not vm.owns_active_work
        await bind(page)
        if outcome == "provider-error":
            inspector.errors["snapshots"] = ProviderError("fixture error")
        started = inspector.block("snapshots")
        task = asyncio.create_task(vm.select_view("snapshots"))
        await bounded(started.wait())
        assert vm.owns_active_work
        if outcome == "cancel":
            task.cancel()
        inspector.release("snapshots")
        with contextlib.suppress(asyncio.CancelledError):
            await bounded(task)
        await settle()
        assert not vm.owns_active_work
        assert not vm.preview.owns_active_work
        assert not admission(page)
    finally:
        if "snapshots" in inspector.blocked:
            inspector.release("snapshots")
        if task is not None:
            with contextlib.suppress(asyncio.CancelledError):
                await bounded(task)
        await page.shutdown()
        page.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("retire", ["clear", "silent-drain"])
async def test_metadata_retirement_keeps_indicator_until_cleanup_and_then_clears(retire):
    inspector = RecordingInspector()
    inspector.ignore_cancellation_for = "snapshots"
    page = make_page_vm(InMemoryGlue(), iceberg_inspector=inspector)
    vm = page.catalog.iceberg
    loading = draining = None
    try:
        await bind(page)
        started = inspector.block("snapshots")
        loading = asyncio.create_task(vm.select_view("snapshots"))
        await bounded(started.wait())
        if retire == "clear":
            vm.clear_table()
        else:
            draining = asyncio.create_task(vm.cancel_metadata_loads_and_drain_silently())
        await bounded(inspector.cancellation_seen.wait())
        await settle()
        assert vm.owns_active_work
        assert admission(page)
        inspector.release("snapshots")
        await bounded(loading)
        if draining is not None:
            await bounded(draining)
        await settle()
        assert not vm.owns_active_work
        assert not admission(page)
    finally:
        if "snapshots" in inspector.blocked:
            inspector.release("snapshots")
        for task in (loading, draining):
            if task is not None:
                with contextlib.suppress(asyncio.CancelledError):
                    await bounded(task)
        await page.shutdown()
        page.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "outcome",
    [
        DuckDbOutcome.OK,
        DuckDbOutcome.FAILED,
        DuckDbOutcome.CANCELLED,
        RuntimeError("fake engine exception"),
    ],
)
async def test_preview_public_indicator_releases_for_engine_outcomes(outcome):
    port = HeldPort((outcome,))
    page = make_page_vm(InMemoryGlue(), duckdb_port=port)
    vm = page.catalog.iceberg
    task = None
    try:
        await bind(page)
        assert not vm.preview.owns_active_work
        assert not vm.owns_active_work
        task = asyncio.create_task(vm.preview.load())
        assert await asyncio.to_thread(port.entered[0].wait, 5)
        assert vm.preview.owns_active_work
        assert vm.owns_active_work
        port.release[0].set()
        await bounded(task)
        await settle()
        assert port.finished[0].is_set()
        assert not vm.preview.owns_active_work
        assert not vm.owns_active_work
        assert not admission(page)
        assert vm.preview._active_loaders == 0  # invariant check, never an App dependency
    finally:
        port.release_all()
        if task is not None:
            with contextlib.suppress(asyncio.CancelledError):
                await bounded(task)
        await page.shutdown()
        page.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("retire", ["clear", "resource-cancel", "caller-cancel", "dispose"])
async def test_preview_retirement_cannot_drop_ownership_before_thread_finishes(retire):
    port = HeldPort()
    page = make_page_vm(InMemoryGlue(), duckdb_port=port)
    vm = page.catalog.iceberg
    task = None
    try:
        await bind(page)
        task = asyncio.create_task(vm.preview.load())
        assert await asyncio.to_thread(port.entered[0].wait, 5)
        if retire == "clear":
            vm.clear_table()
        elif retire == "resource-cancel":
            await vm.preview.cancel()
        elif retire == "caller-cancel":
            task.cancel()
        else:
            vm.preview.dispose()
        await settle()
        assert not port.finished[0].is_set(), "fake worker thread remains held"
        assert vm.preview.owns_active_work, (
            "retired/cancelled loader lost a still-running engine thread"
        )
        assert vm.owns_active_work
        assert admission(page)
        port.release[0].set()
        with contextlib.suppress(asyncio.CancelledError):
            await bounded(task)
        assert await asyncio.to_thread(port.finished[0].wait, 5)
        await settle()
        assert not vm.preview.owns_active_work
        assert not vm.owns_active_work
        assert vm.preview._active_loaders == 0
    finally:
        port.release_all()
        if task is not None:
            with contextlib.suppress(asyncio.CancelledError):
                await bounded(task)
        assert await asyncio.to_thread(port.finished[0].wait, 5)
        await page.shutdown()
        page.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("finish_order", [(0, 1, 2), (2, 0, 1)])
async def test_overlapping_retired_previews_remain_owned_until_last_thread_finishes(finish_order):
    port = HeldPort((DuckDbOutcome.OK,) * 3)
    page = make_page_vm(InMemoryGlue(), duckdb_port=port)
    vm = page.catalog.iceberg
    tasks = []
    try:
        for index in range(3):
            await bind(page, f"s3://fake/table{index}")
            tasks.append(asyncio.create_task(vm.preview.load()))
            assert await asyncio.to_thread(port.entered[index].wait, 5)
            await settle()
            assert vm.preview.owns_active_work
            assert vm.owns_active_work
        for position, index in enumerate(finish_order):
            port.release[index].set()
            assert await asyncio.to_thread(port.finished[index].wait, 5)
            with contextlib.suppress(asyncio.CancelledError):
                await bounded(tasks[index])
            await settle()
            if position < 2:
                assert vm.preview.owns_active_work, (
                    "one retired query completion cleared another owner"
                )
                assert vm.owns_active_work
                assert admission(page)
            else:
                assert not vm.preview.owns_active_work
                assert not vm.owns_active_work
                assert not admission(page)
            assert vm.preview._active_loaders >= 0
        assert vm.preview._active_loaders == 0
    finally:
        port.release_all()
        await asyncio.gather(*tasks, return_exceptions=True)
        for event in port.finished[: len(tasks)]:
            assert await asyncio.to_thread(event.wait, 5)
        await page.shutdown()
        page.dispose()


@pytest.mark.asyncio
async def test_preview_counts_an_accepted_loader_waiting_for_local_thread_capacity(monkeypatch):
    from types import SimpleNamespace

    import anyio

    from aws_tui.vm.glue import iceberg_preview_vm as preview_module

    # Only this module's adapter is replaced. The process/global AnyIO limiter is untouched.
    limiter = anyio.CapacityLimiter(1)
    original_run_sync = anyio.to_thread.run_sync

    async def local_run_sync(function):
        return await original_run_sync(function, limiter=limiter)

    monkeypatch.setattr(
        preview_module, "anyio", SimpleNamespace(to_thread=SimpleNamespace(run_sync=local_run_sync))
    )
    port = HeldPort()
    page = make_page_vm(InMemoryGlue(), duckdb_port=port)
    vm = page.catalog.iceberg
    task = None
    capacity_held = False
    try:
        await bind(page)
        await limiter.acquire()
        capacity_held = True
        task = asyncio.create_task(vm.preview.load())
        await settle()
        assert not port.entered[0].is_set(), "query is queued behind test-local capacity"
        assert vm.preview.owns_active_work
        assert vm.owns_active_work
        assert admission(page)
        limiter.release()
        capacity_held = False
        assert await asyncio.to_thread(port.entered[0].wait, 5)
        assert vm.preview.owns_active_work
        port.release[0].set()
        await bounded(task)
        await settle()
        assert not vm.preview.owns_active_work
        assert not vm.owns_active_work
        assert vm.preview._active_loaders == 0
    finally:
        if capacity_held:
            limiter.release()
        port.release_all()
        if task is not None:
            with contextlib.suppress(asyncio.CancelledError):
                await bounded(task)
        await page.shutdown()
        page.dispose()
