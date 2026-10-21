"""Owned bounded execution and fresh retry of reviewed recursive uploads."""

from __future__ import annotations

import asyncio
from dataclasses import replace

import pytest

from aws_tui.domain.cross_fs import ConflictResolution
from aws_tui.domain.filesystem import (
    ConflictError,
    EntryKind,
    FileEntry,
    PathRef,
    ProviderError,
    TransferProgress,
)
from aws_tui.domain.local_fs import LocalFS
from aws_tui.domain.recursive_upload import UploadPlanError, plan_upload
from aws_tui.domain.s3_upload import UploadPublicationUnknownError
from aws_tui.domain.upload_batch import UploadBatch, UploadStatus, replan_failed

pytestmark = pytest.mark.unit


def path(value):
    return PathRef.from_posix(value)


class Destination:
    """Metadata and publication outcomes are independent, like the SDK seam."""

    def __init__(self):
        self.objects = {}
        self.reads = []
        self.writes = []
        self.errors = {}
        self.hold = None
        self.entered = asyncio.Queue()
        self.active = self.peak = 0
        self.cancelled_published = set()
        self.conflicts_once = set()
        self.unknown = set()

    def upload_location(self, value):
        return "bucket", "/".join(value.segments)

    async def inspect_upload_destination(self, value):
        self.reads.append(value)
        data = self.objects.get(value)
        return (
            None
            if data is None
            else FileEntry(value.name, EntryKind.FILE, len(data), None, data.hex())
        )

    async def write_upload_stream(
        self,
        value,
        stream,
        *,
        total_size,
        publication,
        reviewed=None,
        overwrite=False,
        progress=None,
    ):
        self.active += 1
        self.peak = max(self.peak, self.active)
        self.writes.append((value, overwrite))
        self.entered.put_nowait(value)
        publication.started = True
        try:
            if self.hold:
                await self.hold.wait()
            if value in self.errors:
                raise self.errors[value]
            if value in self.conflicts_once:
                self.conflicts_once.remove(value)
                self.objects[value] = b"concurrent"
                publication.attempted = True
                raise ConflictError("racing key")
            if value in self.objects and not overwrite:
                publication.attempted = True
                raise ConflictError("existing key")
            data = b"".join([chunk async for chunk in stream])
            if progress:
                progress(TransferProgress(len(data), total_size))
            publication.attempted = True
            if value in self.unknown:
                publication.uncertain = True
                raise UploadPublicationUnknownError("outcome unknown")
            self.objects[value] = data
            publication.published = True
            if value in self.cancelled_published:
                raise asyncio.CancelledError
        finally:
            self.active -= 1


@pytest.fixture
def local(tmp_path):
    (tmp_path / "tree").mkdir()
    for n in range(8):
        (tmp_path / "tree" / str(n)).write_bytes(f"file{n}".encode())
    return LocalFS(root=tmp_path)


async def prepared(local, destination=None, policy=ConflictResolution.ERROR):
    destination = destination or Destination()
    plan = await plan_upload(local, destination, path("tree"), path("prefix"), policy=policy)
    return destination, plan


async def test_all_objects_report_pending_running_success_and_aggregate(local):
    destination, plan = await prepared(local)
    events = []
    batch = UploadBatch(plan, on_change=lambda: events.append(batch.snapshot))
    assert batch.snapshot.status == UploadStatus.PENDING
    assert all(row.status == UploadStatus.PENDING for row in batch.snapshot.outcomes)
    result = await batch.run()
    assert result.finished
    assert result.status == UploadStatus.SUCCEEDED
    assert result.bytes_done == result.bytes_total == 40
    assert len(destination.objects) == 8
    assert all(row.status == UploadStatus.SUCCEEDED for row in result.outcomes)
    assert any(
        any(row.status == UploadStatus.RUNNING for row in snapshot.outcomes) for snapshot in events
    )
    assert all(row.status == UploadStatus.PENDING for row in events[0].outcomes)


async def test_change_notifications_are_value_free_and_allow_on_demand_projection(local):
    _, plan = await prepared(local)
    notifications = 0

    def changed():
        nonlocal notifications
        notifications += 1

    batch = UploadBatch(plan, on_change=changed)
    result = await batch.run()
    assert notifications >= len(plan.objects)
    assert batch.observer_error is None
    assert result.bytes_done == batch.bytes_done == 40


async def test_maximum_four_uploads_and_cancel_stops_queued_objects(local):
    destination, plan = await prepared(local)
    destination.hold = asyncio.Event()
    batch = UploadBatch(plan)
    task = asyncio.create_task(batch.run())
    for _ in range(4):
        await asyncio.wait_for(destination.entered.get(), 1)
    assert destination.active == 4
    batch.cancel()
    result = await task
    assert result.status == UploadStatus.CANCELED
    assert destination.peak == 4
    assert len(destination.writes) == 4
    assert all(row.status == UploadStatus.CANCELED for row in result.outcomes)
    assert destination.active == 0


async def test_failed_child_never_returns_success_and_other_children_continue(local):
    destination, plan = await prepared(local)
    destination.errors[path("prefix/3")] = ProviderError("denied")
    result = await UploadBatch(plan).run()
    assert result.status == UploadStatus.FAILED
    assert sum(row.status == UploadStatus.SUCCEEDED for row in result.outcomes) == 7
    assert result.outcomes[3].error is not None


async def test_published_object_remains_success_when_its_worker_is_canceled(local):
    destination, plan = await prepared(local)
    destination.cancelled_published.add(path("prefix/0"))
    result = await UploadBatch(plan).run()
    assert result.outcomes[0].status == UploadStatus.SUCCEEDED
    assert result.outcomes[0].published
    assert destination.objects[path("prefix/0")] == b"file0"


async def test_parent_cancellation_drains_owned_workers_and_preserves_outcomes(local):
    destination, plan = await prepared(local)
    destination.hold = asyncio.Event()
    batch = UploadBatch(plan)
    task = asyncio.create_task(batch.run())
    for _ in range(4):
        await asyncio.wait_for(destination.entered.get(), 1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert batch.snapshot.finished
    assert batch.snapshot.status == UploadStatus.CANCELED
    assert not destination.active


async def test_cancel_before_run_performs_no_upload_and_run_is_single_use(local):
    destination, plan = await prepared(local)
    batch = UploadBatch(plan)
    batch.cancel()
    assert (await batch.run()).status == UploadStatus.CANCELED
    assert not destination.writes
    with pytest.raises(UploadPlanError):
        await batch.run()


@pytest.mark.parametrize("policy", [ConflictResolution.ERROR, ConflictResolution.SKIP])
async def test_reviewed_conflicts_never_open_source_for_error_or_skip(local, monkeypatch, policy):
    destination = Destination()
    destination.objects[path("prefix/0")] = b"old"
    _, plan = await prepared(local, destination, policy)
    original = local.read_upload_stream
    reads = []

    async def stream(value, **kwargs):
        reads.append(value)
        return await original(value, **kwargs)

    monkeypatch.setattr(local, "read_upload_stream", stream)
    result = await UploadBatch(plan).run()
    assert path("tree/0") not in reads
    assert result.outcomes[0].status == (
        UploadStatus.FAILED if policy == ConflictResolution.ERROR else UploadStatus.SKIPPED
    )
    assert destination.objects[path("prefix/0")] == b"old"


@pytest.mark.parametrize(
    "policy", [ConflictResolution.ERROR, ConflictResolution.SKIP, ConflictResolution.RENAME]
)
async def test_race_policy_is_applied_only_to_definitely_unpublished_conflict(local, policy):
    destination, plan = await prepared(local, policy=policy)
    destination.conflicts_once.add(path("prefix/0"))
    result = await UploadBatch(plan).run()
    expected = {
        ConflictResolution.ERROR: UploadStatus.FAILED,
        ConflictResolution.SKIP: UploadStatus.SKIPPED,
        ConflictResolution.RENAME: UploadStatus.SUCCEEDED,
    }
    assert result.outcomes[0].status == expected[policy]
    assert destination.objects[path("prefix/0")] == b"concurrent"
    if policy == ConflictResolution.RENAME:
        assert result.outcomes[0].destination == path("prefix/0 (1)")
        assert destination.objects[path("prefix/0 (1)")] == b"file0"


async def test_unknown_publication_is_not_renamed_or_eligible_for_retry(local):
    destination, plan = await prepared(local, policy=ConflictResolution.RENAME)
    destination.unknown.add(path("prefix/0"))
    batch = UploadBatch(plan)
    result = await batch.run()
    assert result.status == UploadStatus.FAILED
    assert result.outcomes[0].uncertain
    assert len([value for value, _ in destination.writes if value.name.startswith("0")]) == 1
    retry = await replan_failed(batch)
    assert not retry.objects


async def test_retry_replans_only_failed_objects_and_never_successful_overwrite(local, tmp_path):
    destination = Destination()
    destination.objects[path("prefix/0")] = b"old"
    destination.errors[path("prefix/3")] = ProviderError("failed")
    _, plan = await prepared(local, destination, ConflictResolution.OVERWRITE)
    batch = UploadBatch(plan)
    await batch.run()
    destination.errors.clear()
    (tmp_path / "tree/3").write_bytes(b"revised-source")
    (tmp_path / "tree/unrelated-new").write_bytes(b"not part of retry")
    destination.reads.clear()
    retry = await replan_failed(batch)
    assert [row.source for row in retry.objects] == [path("tree/3")]
    assert retry.objects[0].source_entry.size == 14
    assert destination.reads == [path("prefix/3")]
    assert destination.objects[path("prefix/0")] == b"file0"
    before = len(destination.writes)
    await UploadBatch(retry).run()
    assert destination.writes[before:] == [(path("prefix/3"), True)]


async def test_retry_of_running_batch_is_refused(local):
    _, plan = await prepared(local)
    with pytest.raises(UploadPlanError):
        await replan_failed(UploadBatch(plan))


async def test_retry_policy_change_rechecks_only_failed_subset_without_replaying_successes(local):
    destination = Destination()
    destination.objects[path("prefix/0")] = b"old"
    _, plan = await prepared(local, destination)
    batch = UploadBatch(plan)
    await batch.run()
    destination.reads.clear()
    retry = await replan_failed(batch, policy=ConflictResolution.OVERWRITE)
    assert retry.policy == ConflictResolution.OVERWRITE
    assert [row.source for row in retry.objects] == [path("tree/0")]
    assert destination.reads == [path("prefix/0")]
    before = len(destination.writes)
    result = await UploadBatch(retry).run()
    assert result.status == UploadStatus.SUCCEEDED
    assert destination.writes[before:] == [(path("prefix/0"), True)]


async def test_source_changed_after_review_is_failed_before_upload(local, tmp_path):
    destination, plan = await prepared(local)
    (tmp_path / "tree/0").write_bytes(b"changed")
    result = await UploadBatch(plan).run()
    assert result.outcomes[0].status == UploadStatus.FAILED
    assert path("prefix/0") not in destination.objects


async def test_changed_root_identity_refuses_entire_batch_before_upload(local, tmp_path):
    destination, plan = await prepared(local)
    (tmp_path / "tree").rename(tmp_path / "old-tree")
    (tmp_path / "tree").mkdir()
    with pytest.raises(UploadPlanError, match="root"):
        await UploadBatch(plan).run()
    assert not destination.writes


async def test_race_rename_reserves_other_planned_targets(local, tmp_path):
    (tmp_path / "tree/0 (1)").write_bytes(b"separate")
    destination, plan = await prepared(local, policy=ConflictResolution.RENAME)
    destination.conflicts_once.add(path("prefix/0"))
    result = await UploadBatch(plan).run()
    row = next(row for row in result.outcomes if row.item.source == path("tree/0"))
    assert row.destination == path("prefix/0 (2)")
    assert destination.objects[path("prefix/0 (1)")] == b"separate"


async def test_error_before_consuming_source_still_closes_iterator(local, monkeypatch):
    destination, plan = await prepared(local)
    destination.errors[path("prefix/0")] = ProviderError("writer failed before read")
    original = local.read_upload_stream
    closed = []

    class OwnedStream:
        def __init__(self, wrapped, value):
            self.wrapped, self.value = wrapped, value

        def __aiter__(self):
            return self

        async def __anext__(self):
            return await anext(self.wrapped)

        async def aclose(self):
            closed.append(self.value)
            await self.wrapped.aclose()

    async def stream(value, **kwargs):
        return OwnedStream(await original(value, **kwargs), value)

    monkeypatch.setattr(local, "read_upload_stream", stream)
    await UploadBatch(plan).run()
    assert set(closed) == {path(f"tree/{n}") for n in range(8)}


async def test_cancellation_retry_excludes_active_canceled_rows_but_includes_unattempted(local):
    destination, plan = await prepared(local)
    destination.hold = asyncio.Event()
    batch = UploadBatch(plan)
    task = asyncio.create_task(batch.run())
    for _ in range(4):
        await asyncio.wait_for(destination.entered.get(), 1)
    batch.cancel()
    await task
    destination.reads.clear()
    retry = await replan_failed(batch)
    assert [item.source for item in retry.objects] == [path(f"tree/{n}") for n in range(4, 8)]
    assert destination.reads == [path(f"prefix/{n}") for n in range(4, 8)]


@pytest.mark.parametrize("replacement", [{"size": 5 * 1024**4 + 1}, {"etag": 7}, {"etag": ""}])
async def test_retry_rejects_unsupported_snapshot_before_destination_reads(
    local, monkeypatch, replacement
):
    destination, plan = await prepared(local)
    destination.errors[path("prefix/0")] = ProviderError("failed")
    batch = UploadBatch(plan)
    await batch.run()
    original = local.inspect_upload_entry

    async def inspect(value):
        observed = await original(value)
        return replace(observed, **replacement) if value == path("tree/0") else observed

    monkeypatch.setattr(local, "inspect_upload_entry", inspect)
    destination.reads.clear()
    with pytest.raises(UploadPlanError):
        await replan_failed(batch)
    assert not destination.reads


async def test_retry_execution_preserves_prior_successful_target_reservations(local, tmp_path):
    (tmp_path / "tree/0 (1)").write_bytes(b"other source")
    destination, plan = await prepared(local, policy=ConflictResolution.RENAME)
    destination.errors[path("prefix/0")] = ProviderError("failed")
    batch = UploadBatch(plan)
    await batch.run()
    destination.errors.clear()
    destination.objects[path("prefix/0")] = b"concurrent"
    retry = await replan_failed(batch)
    assert retry.objects[0].destination == path("prefix/0 (2)")
    del destination.objects[path("prefix/0 (1)")]
    destination.conflicts_once.add(path("prefix/0 (2)"))
    result = await UploadBatch(retry).run()
    assert result.outcomes[0].destination == path("prefix/0 (3)")


async def test_observer_failure_does_not_abort_transfers_or_hide_their_outcomes(local):
    destination, plan = await prepared(local)
    error = RuntimeError("observer failed")

    def observe():
        snapshot = batch.snapshot
        if any(row.status == UploadStatus.RUNNING for row in snapshot.outcomes):
            raise error

    batch = UploadBatch(plan, on_change=observe)
    result = await batch.run()
    assert result.status == UploadStatus.SUCCEEDED
    assert len(destination.objects) == 8
    assert batch.observer_error is error


async def test_cancel_from_running_observer_returns_canceled_result_without_mutation(local):
    destination, plan = await prepared(local)

    def observe():
        snapshot = batch.snapshot
        if any(row.status == UploadStatus.RUNNING for row in snapshot.outcomes):
            batch.cancel()

    batch = UploadBatch(plan, on_change=observe)
    result = await batch.run()
    assert result.status == UploadStatus.CANCELED
    assert not destination.writes


@pytest.mark.parametrize("error", [RuntimeError(), asyncio.CancelledError()])
async def test_published_object_cleanup_failure_is_retained_but_aggregate_fails(
    local, monkeypatch, error
):
    _, plan = await prepared(local)
    original = local.read_upload_stream

    class Stream:
        def __init__(self, wrapped):
            self.wrapped = wrapped

        def __aiter__(self):
            return self

        async def __anext__(self):
            return await anext(self.wrapped)

        async def aclose(self):
            await self.wrapped.aclose()
            raise error

    async def read(value, **kwargs):
        return Stream(await original(value, **kwargs))

    monkeypatch.setattr(local, "read_upload_stream", read)
    result = await UploadBatch(plan).run()
    assert result.status == UploadStatus.FAILED
    assert all(row.published and row.cleanup_error for row in result.outcomes)
    assert all(row.status == UploadStatus.SUCCEEDED for row in result.outcomes)
