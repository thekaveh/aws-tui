"""Provider progress cannot resurrect an evicted cancelled transfer row."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from vmx import NULL_DISPATCHER, MessageHub

from aws_tui.domain.cross_fs import ConflictResolution
from aws_tui.domain.filesystem import PathRef, TransferProgress
from aws_tui.domain.transfer_journal import TransferJournal
from aws_tui.vm.file_manager.transfer_runtime import TransferRuntime
from aws_tui.vm.file_manager.transfer_vm import TransferState
from aws_tui.vm.file_manager.transfers_vm import TransfersVM


@pytest.mark.asyncio
async def test_cancelled_evicted_row_stays_terminal_during_late_provider_progress(
    tmp_path: Path,
) -> None:
    hub = MessageHub()
    vm = TransfersVM(hub=hub, dispatcher=NULL_DISPATCHER)
    vm.construct()
    runtime = TransferRuntime(TransferJournal(base_dir=tmp_path / "journal"), hub)
    started, cancelled, late, emitted, settle = [asyncio.Event() for _ in range(5)]
    ids: list[str] = []

    async def operation(src: PathRef, dst: PathRef, *, progress, on_conflict) -> bool:
        started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancelled.set()
        await late.wait()
        progress(TransferProgress(730, 1000))
        emitted.set()
        await settle.wait()
        return True

    async def owner() -> bool:
        tid = await runtime.begin(
            source_uri="/source", destination_uri="/destination", bytes_total=1000
        )
        ids.append(tid)
        runtime.progress(tid, TransferState.RUNNING, 0, 1000, "/source", "/destination")
        try:
            return await runtime.run_one(
                operation=operation,
                src_path=PathRef.from_posix("/source"),
                dst_path=PathRef.from_posix("/destination"),
                on_conflict=ConflictResolution.ERROR,
                transfer_id=tid,
                bytes_total=1000,
            )
        finally:
            runtime.release(tid)

    task = asyncio.create_task(owner())
    try:
        await asyncio.wait_for(started.wait(), 3)
        vm.cancel(ids[0])
        await asyncio.wait_for(cancelled.wait(), 3)
        assert vm.active_count == 0
        assert (
            next(row for row in vm.transfers if row.id == ids[0]).state is TransferState.CANCELLED
        )
        for index in range(100):
            runtime.progress(f"{index:016x}", TransferState.COMPLETED, 1, 1)
        assert len(vm.finished) == 100
        assert not any(row.id == ids[0] for row in vm.transfers)
        late.set()
        await asyncio.wait_for(emitted.wait(), 3)
        assert not task.done()
        assert vm.active_count == 0
        assert not any(row.id == ids[0] for row in vm.transfers)
        settle.set()
        assert await asyncio.wait_for(task, 3) is False
        terminal = next(row for row in vm.transfers if row.id == ids[0])
        assert terminal.state is TransferState.CANCELLED
        assert terminal.model.bytes_done == 730
        assert vm.active_count == 0
        assert len(vm.finished) == 100
        assert not runtime.owned_ids
    finally:
        late.set()
        settle.set()
        if not task.done():
            task.cancel()
        await runtime.shutdown()
        await asyncio.gather(task, return_exceptions=True)
        runtime.dispose()
        vm.dispose()
