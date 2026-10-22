"""Reviewed sync compares snapshots without confusing revisions with content."""

from __future__ import annotations

import asyncio
import hashlib
from contextlib import suppress
from dataclasses import replace
from datetime import UTC, datetime

import pytest

from aws_tui.domain.filesystem import PathRef, PermissionDeniedError
from aws_tui.domain.local_fs import LocalFS
from aws_tui.domain.recursive_upload import UploadPlanError
from aws_tui.domain.sync_plan import (
    SyncChecksum,
    SyncComparison,
    SyncLabel,
    SyncSettings,
    parse_sync_patterns,
    plan_sync,
    replan_sync,
    select_sync_operations,
)
from aws_tui.domain.upload_batch import UploadBatch, UploadStatus
from tests.unit.domain.test_upload_batch import Destination

pytestmark = pytest.mark.unit


def path(value):
    return PathRef.from_posix(value)


class SyncDestination(Destination):
    def __init__(self):
        super().__init__()
        self.checksums = []
        self.denied = set()
        self.during_checksum = None

    async def inspect_upload_destination(self, value):
        if value in self.denied:
            raise PermissionDeniedError("Cannot inspect this key")
        entry = await super().inspect_upload_destination(value)
        return replace(entry, etag="revision:" + entry.etag) if entry is not None else None

    async def read_sync_checksum(self, value, *, expected):
        self.checksums.append(value)
        data = self.objects[value]
        if self.during_checksum:
            self.during_checksum()
        return SyncChecksum(hashlib.sha256(data).hexdigest(), len(data), expected.etag)


def tree(tmp_path, files):
    root = tmp_path / "tree"
    root.mkdir()
    for name, data in files.items():
        file = root / name
        file.parent.mkdir(exist_ok=True, parents=True)
        file.write_bytes(data)
    return LocalFS(root=tmp_path)


async def review(source, destination, **kwargs):
    return await plan_sync(source, destination, path("tree"), path("uploads/tree"), **kwargs)


async def test_default_comparison_is_deterministic_and_writes_nothing(tmp_path):
    source = tree(tmp_path, {"new": b"new", "same": b"same", "changed": b"after"})
    destination = SyncDestination()
    destination.objects = {
        path("uploads/tree/same"): b"same",
        path("uploads/tree/changed"): b"prior",
    }
    first = await review(source, destination)
    second = await review(source, destination)
    assert first == second
    assert [(row.relative, row.label) for row in first.rows] == [
        ("changed", SyncLabel.UPDATE),
        ("new", SyncLabel.CREATE),
        ("same", SyncLabel.UNCHANGED),
    ]
    assert all(row.reason for row in first.rows)
    assert first.rows[0].evidence == "SHA256 FULL_OBJECT"
    assert first.rows[2].evidence == "SHA256 FULL_OBJECT"
    assert [item.source for item in first.operations] == [path("tree/changed"), path("tree/new")]
    assert first.operations[0].destination == path("uploads/tree/changed")
    assert not destination.writes


async def test_equal_size_different_bytes_are_updates_despite_multipart_revision(tmp_path):
    source = tree(tmp_path, {"file": b"abcd"})

    class Multipart(SyncDestination):
        async def inspect_upload_destination(self, value):
            entry = await super().inspect_upload_destination(value)
            return replace(entry, etag='"unrelated-encrypted-or-multipart-2"') if entry else None

    destination = Multipart()
    destination.objects[path("uploads/tree/file")] = b"efgh"
    plan = await review(source, destination)
    assert plan.rows[0].label == SyncLabel.UPDATE
    assert plan.rows[0].evidence == "SHA256 FULL_OBJECT"
    assert destination.checksums == [path("uploads/tree/file")]


async def test_optional_size_time_heuristic_never_claims_verified_content(tmp_path):
    source = tree(tmp_path, {"file": b"abcd"})

    class LaterDestination(SyncDestination):
        async def inspect_upload_destination(self, value):
            entry = await super().inspect_upload_destination(value)
            return replace(entry, modified=datetime(2100, 1, 1, tzinfo=UTC)) if entry else None

    destination = LaterDestination()
    destination.objects[path("uploads/tree/file")] = b"efgh"
    plan = await review(
        source, destination, settings=SyncSettings(comparison=SyncComparison.SIZE_TIME)
    )
    assert plan.rows[0].label == SyncLabel.UNCHANGED
    assert "heuristic" in plan.rows[0].evidence.lower()
    assert "checksum" not in plan.rows[0].evidence.lower()
    assert not destination.checksums
    assert not plan.operations


async def test_filters_and_reviewer_omissions_match_actual_execution_without_pruning(tmp_path):
    source = tree(tmp_path, {"a.csv": b"a", "nested/b.csv": b"b", "omit.csv": b"o", "note": b"n"})
    destination = SyncDestination()
    destination.objects[path("uploads/tree/destination-only")] = b"keep"
    plan = await review(
        source, destination, settings=SyncSettings(include=("*.csv",), exclude=("omit*",))
    )
    assert [row.relative for row in plan.rows] == ["a.csv", "nested/b.csv"]
    selected = select_sync_operations(plan, excluded=(path("tree/a.csv"),))
    assert [item.source for item in selected.operations] == [path("tree/nested/b.csv")]
    result = await UploadBatch(selected.execution).run()
    assert result.status == UploadStatus.SUCCEEDED
    assert destination.writes == [(path("uploads/tree/nested/b.csv"), True)]
    assert destination.objects[path("uploads/tree/destination-only")] == b"keep"


async def test_unchanged_second_run_has_empty_mutation_plan_and_zero_writes(tmp_path):
    source = tree(tmp_path, {"empty": b"", "nested/a": b"payload"})
    destination = SyncDestination()
    first = await review(source, destination)
    assert (await UploadBatch(first.execution).run()).status == UploadStatus.SUCCEEDED
    count = len(destination.writes)
    second = await review(source, destination)
    assert not second.operations
    assert all(row.label == SyncLabel.UNCHANGED for row in second.rows)
    assert not second.execution.objects
    assert len(destination.writes) == count


async def test_denied_observation_is_unsupported_and_never_an_executable_create(tmp_path):
    source = tree(tmp_path, {"denied": b"data", "new": b"new"})
    destination = SyncDestination()
    destination.denied.add(path("uploads/tree/denied"))
    plan = await review(source, destination)
    assert plan.rows[0].label == SyncLabel.UNSUPPORTED
    assert "inspect" in plan.rows[0].reason.lower()
    assert [row.source for row in plan.operations] == [path("tree/new")]
    assert not destination.writes


async def test_changed_destination_during_comparison_becomes_conflict(tmp_path):
    source = tree(tmp_path, {"file": b"same"})
    destination = SyncDestination()
    target = path("uploads/tree/file")
    destination.objects[target] = b"same"
    destination.during_checksum = lambda: destination.objects.update({target: b"race"})
    plan = await review(source, destination)
    assert plan.rows[0].label == SyncLabel.CONFLICT
    assert not plan.operations


async def test_new_source_after_review_is_not_silently_added(tmp_path):
    source = tree(tmp_path, {"file": b"data"})
    destination = SyncDestination()
    plan = await review(source, destination)
    (tmp_path / "tree/new").write_bytes(b"new")
    batch = UploadBatch(plan.execution)
    with pytest.raises(UploadPlanError, match="root changed"):
        await batch.run()
    assert not destination.writes
    assert path("uploads/tree/new") not in destination.objects


async def test_fresh_retry_may_resolve_previously_failed_path_as_unchanged(tmp_path):
    source = tree(tmp_path, {"file": b"data"})
    destination = SyncDestination()
    plan = await review(source, destination)
    destination.objects[path("uploads/tree/file")] = b"data"
    (tmp_path / "tree/new").write_bytes(b"not silently included")
    retry = await replan_sync(plan, (path("tree/file"),))
    assert [(row.relative, row.label) for row in retry.rows] == [("file", SyncLabel.UNCHANGED)]
    assert not retry.operations
    assert not destination.writes


async def test_overlapping_physical_provider_identity_is_refused(tmp_path):
    source = tree(tmp_path, {"file": b"data"})

    class SameStore(SyncDestination):
        storage_identity = source.storage_identity

    with pytest.raises(UploadPlanError, match=r"overlap|same.*storage"):
        await review(source, SameStore())


@pytest.mark.parametrize(
    "include",
    [("../*",), ("/absolute",), ("a/../b",), ("bad\x00name",), ("x" * 1025,), ("*",) * 65],
)
def test_unsafe_or_unbounded_filter_rules_are_refused(include):
    with pytest.raises(UploadPlanError):
        SyncSettings(include=include)


def test_pattern_input_preserves_quoted_spaces_and_canonicalizes_rules():
    assert parse_sync_patterns('"a b/*" *.csv') == ("a b/*", "*.csv")
    assert SyncSettings(include=("b*", "a*", "a*")).include == ("a*", "b*")


async def test_review_selection_and_retry_reject_foreign_paths(tmp_path):
    source = tree(tmp_path, {"file": b"data"})
    destination = SyncDestination()
    plan = await review(source, destination)
    with pytest.raises(UploadPlanError):
        select_sync_operations(plan, excluded=(path("elsewhere"),))
    with pytest.raises(UploadPlanError):
        await replan_sync(plan, (path("tree/other"),))


async def test_filters_precede_mapping_overlong_keys_and_denied_observations(tmp_path):
    long_name = "/".join(["x" * 70] * 4)
    source = tree(tmp_path, {"ok": b"data", "denied": b"d", long_name: b"skip"})
    destination = SyncDestination()
    prefix = PathRef(("p" * 800,))
    destination.denied.add(PathRef((*prefix.segments, "denied")))
    plan = await plan_sync(
        source, destination, path("tree"), prefix, settings=SyncSettings(include=("ok",))
    )
    assert [row.relative for row in plan.rows] == ["ok"]
    assert destination.reads == [PathRef((*prefix.segments, "ok"))]
    assert len(plan.execution.excluded) == 2


async def test_shuffled_source_listing_produces_identical_complete_plan(tmp_path):
    tree(tmp_path, {"z": b"z", "nested/b": b"b", "a": b"a"})

    class Shuffled(LocalFS):
        reverse = False

        async def list_upload_directory(self, value):
            entries = await super().list_upload_directory(value)
            self.reverse = not self.reverse
            return sorted(entries, key=lambda entry: entry.name, reverse=self.reverse)

    source = Shuffled(root=tmp_path)
    destination = SyncDestination()
    assert await review(source, destination) == await review(source, destination)


async def test_incompatible_checksum_binding_is_never_reported_unchanged(tmp_path):
    source = tree(tmp_path, {"file": b"same"})

    class WrongRevision(SyncDestination):
        async def read_sync_checksum(self, value, *, expected):
            return replace(
                await super().read_sync_checksum(value, expected=expected), revision="other"
            )

    destination = WrongRevision()
    destination.objects[path("uploads/tree/file")] = b"same"
    plan = await review(source, destination)
    assert plan.rows[0].label == SyncLabel.UNSUPPORTED
    assert not plan.operations


async def test_swallowed_cancellation_during_comparison_never_starts_the_next_file(tmp_path):
    source = tree(tmp_path, {"a": b"same", "b": b"same"})
    entered = asyncio.Event()

    class Resistant(SyncDestination):
        async def read_sync_checksum(self, value, *, expected):
            if value.name == "a":
                entered.set()
                with suppress(asyncio.CancelledError):
                    await asyncio.Event().wait()
            return await super().read_sync_checksum(value, expected=expected)

    destination = Resistant()
    destination.objects = {path("uploads/tree/a"): b"same", path("uploads/tree/b"): b"same"}
    task = asyncio.create_task(review(source, destination))
    await asyncio.wait_for(entered.wait(), 3)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert destination.checksums == [path("uploads/tree/a")]


@pytest.mark.parametrize("replacement", ["missing", "directory"])
async def test_retry_retains_missing_or_type_changed_source_as_conflict(tmp_path, replacement):
    source = tree(tmp_path, {"file": b"data"})
    destination = SyncDestination()
    plan = await review(source, destination)
    (tmp_path / "tree/file").unlink()
    if replacement == "directory":
        (tmp_path / "tree/file").mkdir()
    retry = await replan_sync(plan, (path("tree/file"),))
    assert [(row.relative, row.label) for row in retry.rows] == [("file", SyncLabel.CONFLICT)]
    assert retry.rows[0].reason
    assert not retry.operations


async def test_retry_does_not_walk_new_unrelated_denied_subtree(tmp_path):
    tree(tmp_path, {"file": b"data"})

    class Isolated(LocalFS):
        async def list_upload_directory(self, value):
            if value.name == "unrelated":
                raise PermissionDeniedError("Do not scan this unrelated subtree")
            return await super().list_upload_directory(value)

    source = Isolated(root=tmp_path)
    destination = SyncDestination()
    plan = await review(source, destination)
    (tmp_path / "tree/unrelated").mkdir()
    retry = await replan_sync(plan, (path("tree/file"),))
    assert [(row.relative, row.label) for row in retry.rows] == [("file", SyncLabel.CREATE)]


async def test_malformed_destination_revision_is_never_an_executable_update(tmp_path):
    source = tree(tmp_path, {"file": b"longer"})

    class Malformed(SyncDestination):
        async def inspect_upload_destination(self, value):
            entry = await super().inspect_upload_destination(value)
            return replace(entry, etag=47) if entry else None

    destination = Malformed()
    destination.objects[path("uploads/tree/file")] = b"old"
    plan = await review(source, destination)
    assert plan.rows[0].label == SyncLabel.UNSUPPORTED
    assert not plan.operations


async def test_malformed_heuristic_timestamp_blocks_only_its_own_path(tmp_path):
    source = tree(tmp_path, {"file": b"same", "new": b"new"})

    class Malformed(SyncDestination):
        async def inspect_upload_destination(self, value):
            entry = await super().inspect_upload_destination(value)
            return replace(entry, modified="not a timestamp") if entry else None

    destination = Malformed()
    destination.objects[path("uploads/tree/file")] = b"same"
    plan = await review(
        source, destination, settings=SyncSettings(comparison=SyncComparison.SIZE_TIME)
    )
    assert [row.label for row in plan.rows] == [SyncLabel.UNSUPPORTED, SyncLabel.CREATE]
    assert [item.source for item in plan.operations] == [path("tree/new")]


async def test_object_and_descendant_mapping_overlap_is_refused_before_destination_reads(tmp_path):
    source = tree(tmp_path, {"a": b"a", "b": b"b"})

    class Aliased(SyncDestination):
        def upload_location(self, value):
            return "bucket", "same/child" if value.name == "b" else "same"

    destination = Aliased()
    with pytest.raises(UploadPlanError, match=r"(?i)overlap|ambiguous"):
        await review(source, destination)
    assert not destination.reads
