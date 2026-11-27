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
