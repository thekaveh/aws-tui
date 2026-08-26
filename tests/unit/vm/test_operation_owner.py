from __future__ import annotations

import asyncio

import pytest

from aws_tui.vm.operation_owner import OperationOwner, OperationSuperseded


@pytest.mark.asyncio
async def test_drain_does_not_recancel_in_progress_cancellation_cleanup() -> None:
    owner = OperationOwner()
    operation_started = asyncio.Event()
    cleanup_started = asyncio.Event()
    release_cleanup = asyncio.Event()
    cleanup_finished = asyncio.Event()

    async def operation() -> None:
        operation_started.set()
        try:
            await asyncio.Event().wait()
        finally:
            cleanup_started.set()
            await release_cleanup.wait()
            cleanup_finished.set()

    caller = asyncio.create_task(owner.run(operation))
    await operation_started.wait()
    owner.close()
    await cleanup_started.wait()

    drain = asyncio.create_task(owner.cancel_and_drain())
    await asyncio.sleep(0)
    try:
        assert not drain.done()
        assert not cleanup_finished.is_set()
        release_cleanup.set()
        await drain
        assert cleanup_finished.is_set()
        with pytest.raises(OperationSuperseded):
            await caller
    finally:
        release_cleanup.set()
        await asyncio.gather(drain, caller, return_exceptions=True)


@pytest.mark.asyncio
async def test_drain_continues_after_owned_task_cleanup_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner = OperationOwner()
    slow_cleanup_started = asyncio.Event()
    release_slow_cleanup = asyncio.Event()
    slow_cleanup_finished = asyncio.Event()

    async def failing_operation() -> None:
        try:
            await asyncio.Event().wait()
        finally:
            raise RuntimeError("cleanup failed")

    async def slow_operation() -> None:
        try:
            await asyncio.Event().wait()
        finally:
            slow_cleanup_started.set()
            await release_slow_cleanup.wait()
            slow_cleanup_finished.set()

    failing = asyncio.create_task(failing_operation())
    slow = asyncio.create_task(slow_operation())
    owner.tasks.update((failing, slow))
    await asyncio.sleep(0)

    def cancel_in_order() -> tuple[asyncio.Task[None], asyncio.Task[None]]:
        failing.cancel()
        slow.cancel()
        return failing, slow

    monkeypatch.setattr(owner, "cancel", cancel_in_order)
    drain = asyncio.create_task(owner.cancel_and_drain())
    await slow_cleanup_started.wait()
    release_slow_cleanup.set()
    try:
        await drain
        assert slow_cleanup_finished.is_set()
        assert not owner.tasks
    finally:
        release_slow_cleanup.set()
        await asyncio.gather(drain, failing, slow, return_exceptions=True)
