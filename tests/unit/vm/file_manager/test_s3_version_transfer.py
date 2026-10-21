"""Exact historical requests use the shared cancellable transfer lifecycle."""

import asyncio

from vmx import MessageHub

from aws_tui.domain.filesystem import PathRef
from aws_tui.domain.local_fs import LocalFS
from aws_tui.domain.transfer_journal import TransferJournal
from aws_tui.vm.file_manager.s3_versions_vm import VersionTransferRequest
from aws_tui.vm.file_manager.transfer_runtime import TransferRuntime
from aws_tui.vm.messages import TransferCancelRequestedMessage, TransferProgressMessage
from tests.s3_versions_support import ReadGate, VersionsFS


def setup(tmp_path):
    fs = VersionsFS()
    source = PathRef(("bucket", "prefix", "deleted.txt"))
    version = fs.versions(source)[1]
    local = LocalFS(root=tmp_path)
    journal = TransferJournal(base_dir=tmp_path / "journal")
    hub = MessageHub()
    runtime = TransferRuntime(journal, hub)
    request = VersionTransferRequest(
        version,
        fs,
        local,
        PathRef(("historical.txt",)),
        str(tmp_path / "historical.txt"),
        "download",
    )
    return fs, request, runtime, hub, journal


async def test_download_uses_exact_version_progress_and_releases_all_runtime_ownership(tmp_path):
    from aws_tui.vm.file_manager.s3_version_transfer import execute_version_transfer

    fs, request, runtime, hub, journal = setup(tmp_path)
    messages = []
    subscription = hub.messages.subscribe(messages.append)
    try:
        assert await execute_version_transfer(request, runtime)
        assert (tmp_path / "historical.txt").read_bytes() == b"payload"
        rows = [m for m in messages if isinstance(m, TransferProgressMessage)]
        assert rows[-1].state == "completed"
        assert rows[-1].bytes_transferred == 7
        assert "versionId=old+%2B%2F%3D" in rows[0].source_label
        assert not runtime.owned_ids
        assert not runtime.cancel_events
        assert not journal.find_unfinished()
        assert fs.stream_closed == 1
    finally:
        subscription.dispose()
        await runtime.shutdown()
        runtime.dispose()


async def test_existing_cancel_message_drains_historical_stream_and_local_stage(tmp_path):
    from aws_tui.vm.file_manager.s3_version_transfer import execute_version_transfer

    fs, request, runtime, hub, _ = setup(tmp_path)
    gate = fs.stream_gate = ReadGate()
    task = asyncio.create_task(execute_version_transfer(request, runtime))
    try:
        await asyncio.wait_for(gate.entered.wait(), 2)
        transfer_id = next(iter(runtime.owned_ids))
        hub.send(TransferCancelRequestedMessage(transfer_id=transfer_id))
        assert await task is False
        assert fs.stream_closed == 1
        assert not (tmp_path / "historical.txt").exists()
        assert not list(tmp_path.glob(".aws-tui-*"))
        assert not runtime.owned_ids
    finally:
        gate.release.set()
        await runtime.shutdown()
        runtime.dispose()


async def test_cancelled_recovery_retains_published_outcome_for_app_warning(tmp_path):
    from aws_tui.vm.file_manager.s3_version_transfer import execute_version_transfer

    fs, original, runtime, hub, _ = setup(tmp_path)
    gate = fs.publish_gate = ReadGate()
    await fs.mkdir(PathRef(("bucket", "prefix")))
    destination = PathRef(("bucket", "prefix", "recovered"))
    target = await fs.recovery_destination(original.version, destination)
    request = VersionTransferRequest(
        original.version, fs, target, destination, "s3://bucket/prefix/recovered", "recovery"
    )
    task = asyncio.create_task(execute_version_transfer(request, runtime))
    try:
        await asyncio.wait_for(gate.entered.wait(), 2)
        hub.send(TransferCancelRequestedMessage(transfer_id=next(iter(runtime.owned_ids))))
        assert await task is False
        assert (await fs.stat(destination)).size == 7
        assert "published and preserved" in request.warning
        assert "s3://bucket/prefix/recovered" in request.warning
        assert not runtime.owned_ids
    finally:
        gate.release.set()
        await runtime.shutdown()
        runtime.dispose()
