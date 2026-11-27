"""A disk call whose owner is cancelled reports the cancellation even when the disk call fails."""

from __future__ import annotations

import asyncio
import threading

import pytest

from aws_tui.vm.file_manager.transfer_runtime import OwnedDiskOperations


@pytest.mark.asyncio
async def test_cancelled_owner_whose_disk_call_then_fails_reports_cancellation() -> None:
    """The owner's cancellation was replaced by the disk error.

    The caller then recorded a persistence failure instead of a cancellation.
    """
    disk = OwnedDiskOperations()
    release = threading.Event()

    def failing_write() -> None:
        release.wait()
        raise OSError("disk full")

    owner = asyncio.create_task(disk.call(failing_write))
    await asyncio.sleep(0.01)
    owner.cancel()
    release.set()

    with pytest.raises(asyncio.CancelledError):
        await owner


@pytest.mark.asyncio
async def test_late_progress_without_labels_keeps_the_transfer_direction(tmp_path) -> None:
    """A terminal message after release carried no labels, so the row became a local copy.

    The runtime now remembers each transfer's labels, so a late message without them is still
    registered as the download it was.
    """
    from vmx import NULL_DISPATCHER, MessageHub

    from aws_tui.domain.transfer_journal import TransferJournal
    from aws_tui.vm.file_manager.transfer_runtime import TransferRuntime
    from aws_tui.vm.file_manager.transfer_vm import TransferState
    from aws_tui.vm.file_manager.transfers_vm import TransfersVM

    hub = MessageHub()
    vm = TransfersVM(hub=hub, dispatcher=NULL_DISPATCHER)
    vm.construct()
    runtime = TransferRuntime(TransferJournal(base_dir=tmp_path / "journal"), hub)
    try:
        tid = await runtime.begin(
            source_uri="s3://lake/key", destination_uri="/tmp/out", bytes_total=10
        )
        runtime.release(tid)
        runtime.progress(tid, TransferState.COMPLETED, 10, 10)

        row = next(transfer for transfer in vm.transfers if transfer.id == tid)
        assert row.model.direction == "download"
        assert row.model.source_label == "s3://lake/key"
    finally:
        vm.dispose()
        await runtime.shutdown()
