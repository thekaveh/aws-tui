"""Additional bounded tests of the protected engine task; no native engine."""

import asyncio
import contextlib
from types import SimpleNamespace

import anyio
import pytest

from aws_tui.infra.duckdb import DuckDbOutcome
from tests.unit.test_workspace_admission import admission, bounded
from tests.unit.test_workspace_admission_lifetime import HeldPort, bind, settle
from tests.unit.vm.glue._fake_glue import InMemoryGlue
from tests.unit.vm.glue.test_page_vm import make_page_vm


async def wait_for_unowned(preview):
    async def wait():
        while preview.owns_active_work:
            await asyncio.sleep(0.001)

    await bounded(wait())


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "outcome",
    [
        DuckDbOutcome.OK,
        DuckDbOutcome.FAILED,
        DuckDbOutcome.CANCELLED,
        RuntimeError("retired fake engine failure"),
    ],
)
async def test_repeated_loader_cancellation_keeps_engine_owned_and_consumes_final_outcome(outcome):
    port = HeldPort((outcome,))
    page = make_page_vm(InMemoryGlue(), duckdb_port=port)
    vm = page.catalog.iceberg
    loader = None
    try:
        await bind(page)
        # Target the real loader directly to deliver repeated asyncio cancellation
        # even after the public AsyncResource waiter has already retired.
        loader = asyncio.create_task(vm.preview._load_page())
        assert await asyncio.to_thread(port.entered[0].wait, 5)
        for _ in range(4):
            loader.cancel()
            await settle()
            assert not loader.done()
            assert not port.finished[0].is_set()
            assert vm.preview.owns_active_work
            assert vm.owns_active_work
            assert admission(page)
            assert vm.preview._active_loaders == 1
        port.release[0].set()
        with pytest.raises(asyncio.CancelledError):
            await bounded(loader)
        assert port.finished[0].is_set()
        assert not vm.preview.owns_active_work
        assert not vm.owns_active_work
        assert vm.preview._active_loaders == 0
        assert not admission(page)
    finally:
        port.release_all()
        if loader is not None:
            with contextlib.suppress(asyncio.CancelledError):
                await bounded(loader)
        await page.shutdown()
        page.dispose()


@pytest.mark.asyncio
async def test_repeated_cancellation_of_queued_engine_retains_ownership_until_thread_finishes(
    monkeypatch,
):
    from aws_tui.vm.glue import iceberg_preview_vm as preview_module

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
    loader = None
    capacity_held = False
    try:
        await bind(page)
        await limiter.acquire()
        capacity_held = True
        loader = asyncio.create_task(vm.preview._load_page())
        await settle()
        assert not port.entered[0].is_set()
        for _ in range(4):
            loader.cancel()
            await settle()
            assert not loader.done()
            assert not port.entered[0].is_set()
            assert vm.preview.owns_active_work
            assert vm.owns_active_work
            assert admission(page)
            assert vm.preview._active_loaders == 1
        limiter.release()
        capacity_held = False
        assert await asyncio.to_thread(port.entered[0].wait, 5)
        assert vm.preview.owns_active_work
        port.release[0].set()
        with pytest.raises(asyncio.CancelledError):
            await bounded(loader)
        assert port.finished[0].is_set()
        assert not vm.preview.owns_active_work
        assert not vm.owns_active_work
        assert vm.preview._active_loaders == 0
    finally:
        if capacity_held:
            limiter.release()
        port.release_all()
        if loader is not None:
            with contextlib.suppress(asyncio.CancelledError):
                await bounded(loader)
        await page.shutdown()
        page.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("order", [(2, 0, 1), (0, 2, 1), (1, 2, 0)])
async def test_overlapping_retired_engine_failures_preserve_other_owners_and_current_rows(order):
    port = HeldPort((RuntimeError("old fake failure"), DuckDbOutcome.FAILED, DuckDbOutcome.OK))
    page = make_page_vm(InMemoryGlue(), duckdb_port=port)
    vm = page.catalog.iceberg
    callers = []
    try:
        for index in range(3):
            await bind(page, f"s3://fake/table{index}")
            callers.append(asyncio.create_task(vm.preview.load()))
            assert await asyncio.to_thread(port.entered[index].wait, 5)
            await settle()
            assert vm.preview._active_loaders == index + 1
        for position, index in enumerate(order):
            port.release[index].set()
            assert await asyncio.to_thread(port.finished[index].wait, 5)
            await bounded(callers[index])
            await settle()
            if position < 2:
                assert vm.preview.owns_active_work
                assert vm.owns_active_work
                assert admission(page)
            else:
                await wait_for_unowned(vm.preview)
                assert not vm.owns_active_work
                assert not admission(page)
            assert vm.preview._active_loaders >= 0
        assert vm.preview._active_loaders == 0
        assert vm.preview.rows == (("2",),)
        assert vm.preview.error_text is None
    finally:
        port.release_all()
        await asyncio.gather(*callers, return_exceptions=True)
        for event in port.finished[: len(callers)]:
            assert await asyncio.to_thread(event.wait, 5)
        await wait_for_unowned(vm.preview)
        await page.shutdown()
        page.dispose()
