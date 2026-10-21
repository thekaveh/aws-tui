"""One shared transfer entry owned until all recursive publication receipts drain."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any, cast

from aws_tui.domain.recursive_upload import UploadPlan
from aws_tui.domain.transfer_history import HistoryStatus
from aws_tui.domain.upload_batch import UploadBatch, UploadStatus
from aws_tui.vm.file_manager.transfer_runtime import TransferRuntime
from aws_tui.vm.messages import TransferState


async def _drain(task: asyncio.Future[Any]) -> bool:
    interrupted = False
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            owner = asyncio.current_task()
            interrupted = interrupted or bool(owner and owner.cancelling())
        except Exception:
            break
    if not task.cancelled():
        task.exception()  # Observe failure without interrupting remaining cleanup.
    return interrupted


async def execute_upload_transfer(
    plan: UploadPlan,
    runtime: TransferRuntime,
    *,
    source_uri: str,
    destination_uri: str,
    on_change: Callable[[UploadBatch], None] | None = None,
) -> UploadBatch:
    """Use shared journal/cancel controls while preserving the actual batch result.

    A generic directory-copy recovery descriptor cannot safely replay this plan.
    Terminal classification occurs once, after receipt and iterator drainage,
    so a late cancellation cannot mark a wholly published batch as aborted.
    """
    tid: str | None = None
    task: asyncio.Task[Any] | None = None
    watcher: asyncio.Task[bool] | None = None
    canceled = False
    error: Exception | None = None

    def changed() -> None:
        if tid is not None and not runtime.cancel_events[tid].is_set():
            runtime.progress(tid, TransferState.RUNNING, batch.bytes_done, batch.bytes_total)
        if on_change is not None:
            on_change(batch)

    batch = UploadBatch(plan, on_change=changed)

    def notify_final() -> None:
        if on_change is not None:
            try:
                on_change(batch)
            except Exception as exc:
                batch.observer_error = exc

    def stop() -> None:
        batch.cancel()
        if task is not None and not task.done() and not task.cancelling():
            task.cancel()

    try:
        notify_final()  # Expose cancellation before the journal's disk await.
        tid = await runtime.begin(
            source_uri=source_uri,
            destination_uri=destination_uri,
            bytes_total=batch.bytes_total,
            descriptor=None,
        )
        await runtime.claim_running(tid)
        runtime.progress(
            tid, TransferState.PENDING, 0, batch.bytes_total, source_uri, destination_uri
        )
        event = runtime.cancel_events[tid]
        if event.is_set():
            batch.cancel()
        task = asyncio.create_task(batch.run())
        watcher = asyncio.create_task(event.wait())
        await asyncio.wait({task, watcher}, return_when=asyncio.FIRST_COMPLETED)
        if watcher.done() and not task.done():
            stop()
        canceled = await _drain(task)
        task_error = task.exception() if not task.cancelled() else None
        if isinstance(task_error, Exception):
            error = task_error
    except asyncio.CancelledError:
        canceled = True
        stop()
    except Exception as exc:
        error = exc
        stop()
    finally:
        if watcher is not None:
            watcher.cancel()
            canceled = await _drain(watcher) or canceled
        if task is None:
            batch.cancel()
            task = asyncio.create_task(batch.run())
        canceled = await _drain(task) or canceled
        if tid is not None:
            snapshot = batch.snapshot
            if snapshot.status == UploadStatus.SUCCEEDED:
                state = (
                    TransferState.COMPLETED
                    if any(row.published for row in snapshot.outcomes)
                    else TransferState.SKIPPED
                )
            elif snapshot.status == UploadStatus.FAILED:
                state = TransferState.FAILED
            else:
                state = TransferState.CANCELLED

            async def finish() -> None:
                assert tid is not None
                try:
                    try:
                        runtime.progress(tid, state, batch.bytes_done, batch.bytes_total)
                    except Exception as exc:
                        batch.observer_error = exc
                    await runtime.finish(
                        tid,
                        cast(HistoryStatus, state.value),
                        batch.bytes_done,
                        batch.bytes_total,
                        "provider_error" if state == TransferState.FAILED else None,
                    )
                finally:
                    await runtime.settle_batch([(tid, batch.bytes_total)], {tid})

            finalizing = asyncio.create_task(finish())
            canceled = await _drain(finalizing) or canceled
            finalizing.result()
        notify_final()
    if canceled:
        raise asyncio.CancelledError
    if error is not None:
        raise error
    return batch
