"""The shared transfer lifecycle retains truthful batch publication outcomes."""

import asyncio

import pytest
from vmx import MessageHub

from aws_tui.domain.filesystem import PathRef, ProviderError
from aws_tui.domain.local_fs import LocalFS
from aws_tui.domain.recursive_upload import plan_upload
from aws_tui.domain.transfer_journal import TransferJournal
from aws_tui.domain.upload_batch import UploadStatus
from aws_tui.vm.file_manager.recursive_upload_transfer import execute_upload_transfer
from aws_tui.vm.file_manager.transfer_runtime import HistoryWriteError, TransferRuntime
from aws_tui.vm.messages import (
    TransferCancelRequestedMessage,
    TransferProgressMessage,
    TransferState,
)
from tests.unit.domain.test_upload_batch import Destination


async def setup(tmp_path, *, count=8, destination=None):
    (tmp_path / "tree").mkdir()
    for n in range(count):
        (tmp_path / "tree" / str(n)).write_bytes(b"hello")
    destination = destination or Destination()
    plan = await plan_upload(
        LocalFS(root=tmp_path), destination, PathRef(("tree",)), PathRef(("prefix",))
    )
    hub = MessageHub()
    journal = TransferJournal(base_dir=tmp_path / "journal")
    return plan, destination, TransferRuntime(journal, hub), hub, journal


async def execute(plan, runtime, **kwargs):
    return await execute_upload_transfer(
        plan, runtime, source_uri="local:///tree", destination_uri="s3://bucket/prefix/", **kwargs
    )


async def test_complete_batch_has_one_truthful_transfer_and_releases_runtime_ownership(tmp_path):
    plan, _, runtime, hub, journal = await setup(tmp_path)
    events = []
    subscription = hub.messages.subscribe(events.append)
    try:
        batch = await execute(plan, runtime)
        assert batch.snapshot.status == UploadStatus.SUCCEEDED
        rows = [row for row in events if isinstance(row, TransferProgressMessage)]
        assert len({row.transfer_id for row in rows}) == 1
        assert rows[-1].state == TransferState.COMPLETED
        assert rows[-1].bytes_transferred == rows[-1].bytes_total == 40
        assert not runtime.owned_ids
        assert not runtime.cancel_events
        assert not journal.find_unfinished()
    finally:
        subscription.dispose()
        await runtime.shutdown()
        runtime.dispose()


async def test_failed_child_is_failed_aggregate_and_successes_are_retained(tmp_path):
    plan, destination, runtime, hub, journal = await setup(tmp_path)
    destination.errors[PathRef(("prefix", "3"))] = ProviderError("denied")
    events = []
    subscription = hub.messages.subscribe(events.append)
    try:
        batch = await execute(plan, runtime)
        assert batch.snapshot.status == UploadStatus.FAILED
        assert len(destination.objects) == 7
        assert [row for row in events if isinstance(row, TransferProgressMessage)][
            -1
        ].state == TransferState.FAILED
        assert not runtime.owned_ids
        assert not journal.find_unfinished()
    finally:
        subscription.dispose()
        await runtime.shutdown()
        runtime.dispose()


async def test_existing_transfer_cancel_message_stops_batch_and_drains_workers(tmp_path):
    plan, destination, runtime, hub, journal = await setup(tmp_path)
    destination.hold = asyncio.Event()
    task = asyncio.create_task(execute(plan, runtime))
    try:
        for _ in range(4):
            await asyncio.wait_for(destination.entered.get(), 2)
        hub.send(TransferCancelRequestedMessage(transfer_id=next(iter(runtime.owned_ids))))
        batch = await task
        assert batch.snapshot.status == UploadStatus.CANCELED
        assert len(destination.writes) == 4
        assert not destination.active
        assert not runtime.owned_ids
        assert not journal.find_unfinished()
    finally:
        await runtime.shutdown()
        runtime.dispose()


async def test_late_cancel_retains_successful_publication_and_completed_journal(tmp_path):
    class PublishedDestination(Destination):
        def __init__(self):
            super().__init__()
            self.committed = asyncio.Event()

        async def write_upload_stream(self, *args, **kwargs):
            await super().write_upload_stream(*args, **kwargs)
            self.committed.set()
            await asyncio.Event().wait()

    destination = PublishedDestination()
    plan, _, runtime, hub, journal = await setup(tmp_path, count=1, destination=destination)
    events = []
    subscription = hub.messages.subscribe(events.append)
    task = asyncio.create_task(execute(plan, runtime))
    try:
        await asyncio.wait_for(destination.committed.wait(), 2)
        hub.send(TransferCancelRequestedMessage(transfer_id=next(iter(runtime.owned_ids))))
        batch = await task
        assert batch.snapshot.status == UploadStatus.SUCCEEDED
        assert batch.snapshot.outcomes[0].published
        assert [row for row in events if isinstance(row, TransferProgressMessage)][
            -1
        ].state == TransferState.COMPLETED
        assert not runtime.owned_ids
        assert not journal.find_unfinished()
    finally:
        subscription.dispose()
        await runtime.shutdown()
        runtime.dispose()


async def test_parent_cancellation_releases_history_and_all_provider_workers(tmp_path):
    plan, destination, runtime, _, journal = await setup(tmp_path)
    destination.hold = asyncio.Event()
    batches = []
    task = asyncio.create_task(execute(plan, runtime, on_change=batches.append))
    try:
        for _ in range(4):
            await asyncio.wait_for(destination.entered.get(), 2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert batches[-1].snapshot.finished
        assert not destination.active
        assert not runtime.owned_ids
        assert not journal.find_unfinished()
    finally:
        await runtime.shutdown()
        runtime.dispose()


async def test_journal_begin_failure_is_before_all_provider_writes(tmp_path, monkeypatch):
    plan, destination, runtime, _, _ = await setup(tmp_path)

    def failed(**kwargs):
        raise OSError("journal unavailable")

    monkeypatch.setattr(runtime.journal, "begin", failed)
    try:
        with pytest.raises(HistoryWriteError):
            await execute(plan, runtime)
        assert not destination.writes
        assert not runtime.owned_ids
    finally:
        await runtime.shutdown()
        runtime.dispose()


async def test_failed_progress_observer_does_not_orphan_published_batch_or_journal(tmp_path):
    plan, destination, runtime, hub, journal = await setup(tmp_path)

    def failed(message):
        if isinstance(message, TransferProgressMessage) and message.state == TransferState.RUNNING:
            raise RuntimeError("subscriber failed")

    subscription = hub.messages.subscribe(failed)
    try:
        batch = await execute(plan, runtime)
        assert batch.snapshot.status == UploadStatus.SUCCEEDED
        assert len(destination.objects) == 8
        assert not runtime.owned_ids
        assert not journal.find_unfinished()
    finally:
        subscription.dispose()
        await runtime.shutdown()
        runtime.dispose()
