"""Read-only recursive upload planning against native local trees."""

from __future__ import annotations

import os
from dataclasses import replace

import pytest

from aws_tui.demo.in_memory_fs import InMemoryFS
from aws_tui.domain.cross_fs import ConflictResolution
from aws_tui.domain.filesystem import EntryKind, FileEntry, PathRef, UnsupportedSourceError
from aws_tui.domain.local_fs import LocalFS
from aws_tui.domain.recursive_upload import (
    UploadPlanError,
    plan_upload,
)

pytestmark = pytest.mark.unit


class PlanningS3(InMemoryFS):
    def __init__(self):
        super().__init__()
        self.observations = []
        self.mutations = []
        self.keys = {}

    def upload_location(self, path):
        if not path.segments:
            raise UploadPlanError("Choose an S3 bucket")
        return path.segments[0], "/".join(path.segments[1:])

    async def inspect_upload_destination(self, path):
        self.observations.append(path)
        return self.keys.get(path)

    async def write_stream(self, *args, **kwargs):
        self.mutations.append((args, kwargs))
        raise AssertionError("planning must never write")


def path(value):
    return PathRef.from_posix(value)


@pytest.fixture
def tree(tmp_path):
    root = tmp_path / "tree"
    root.mkdir()
    (root / "data").mkdir()
    (root / "data" / "rows.json").write_bytes(b"[1,2]")
    (root / "empty").mkdir()
    (root / "zero").write_bytes(b"")
    return LocalFS(root=tmp_path)


async def test_nested_plan_preserves_relative_mapping_and_reports_empty_directories(tree):
    destination = PlanningS3()
    plan = await plan_upload(tree, destination, path("tree"), path("bucket/prefix/tree"))
    assert [(v.source, v.destination, v.source_entry.size) for v in plan.objects] == [
        (path("tree/data/rows.json"), path("bucket/prefix/tree/data/rows.json"), 5),
        (path("tree/zero"), path("bucket/prefix/tree/zero"), 0),
    ]
    assert plan.known_bytes == 5
    assert plan.empty_directories == (path("tree/empty"),)
    assert plan.policy == ConflictResolution.ERROR
    assert not plan.excluded
    assert not destination.mutations


async def test_planning_never_opens_source_payload(tree, monkeypatch):
    async def forbidden(*args, **kwargs):
        raise AssertionError("planning opened a payload")

    monkeypatch.setattr(tree, "read_stream", forbidden)
    await plan_upload(tree, PlanningS3(), path("tree"), path("bucket/tree"))


async def test_symlinks_are_named_and_not_followed(tree, tmp_path):
    (tmp_path / "tree" / "linked-dir").symlink_to(tmp_path / "tree/data", target_is_directory=True)
    (tmp_path / "tree" / "linked-file").symlink_to(tmp_path / "tree/data/rows.json")
    plan = await plan_upload(tree, PlanningS3(), path("tree"), path("bucket/tree"))
    assert {row.path.name for row in plan.excluded} == {"linked-dir", "linked-file"}
    assert all("symlink" in row.reason.lower() for row in plan.excluded)
    assert len(plan.objects) == 2


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="native FIFO creation unavailable")
async def test_special_file_is_named_without_opening_or_blocking(tree, tmp_path):
    os.mkfifo(tmp_path / "tree/pipe")
    plan = await plan_upload(tree, PlanningS3(), path("tree"), path("bucket/tree"))
    assert len(plan.objects) == 2
    assert [(row.path.name, "regular" in row.reason.lower()) for row in plan.excluded] == [
        ("pipe", True)
    ]


async def test_reparse_classification_is_skipped_without_recursing(tree, monkeypatch):
    original = tree.inspect_upload_entry

    async def inspect(value):
        observed = await original(value)
        if value == path("tree/data"):
            return replace(observed, kind=EntryKind.SYMLINK)
        return observed

    monkeypatch.setattr(tree, "inspect_upload_entry", inspect)
    plan = await plan_upload(tree, PlanningS3(), path("tree"), path("bucket/tree"))
    assert len(plan.objects) == 1
    assert plan.excluded[0].path == path("tree/data")


@pytest.mark.parametrize("policy", list(ConflictResolution))
async def test_exact_collisions_are_reviewed_with_policy_and_revision(tree, policy):
    destination = PlanningS3()
    key = path("bucket/tree/data/rows.json")
    observed = FileEntry("rows.json", EntryKind.FILE, 3, None, "old-revision")
    destination.keys[key] = observed
    plan = await plan_upload(tree, destination, path("tree"), path("bucket/tree"), policy=policy)
    row = next(row for row in plan.objects if row.source.name == "rows.json")
    assert row.conflict
    assert row.destination_entry == (None if policy == ConflictResolution.RENAME else observed)
    assert row.destination == (
        path("bucket/tree/data/rows (1).json") if policy == ConflictResolution.RENAME else key
    )
    assert plan.conflicts == 1
    assert not destination.mutations


async def test_rename_does_not_collide_with_another_planned_object(tree, tmp_path):
    (tmp_path / "tree/data/rows (1).json").write_bytes(b"other")
    destination = PlanningS3()
    destination.keys[path("bucket/tree/data/rows.json")] = FileEntry(
        "rows.json", EntryKind.FILE, 3, None, "revision"
    )
    plan = await plan_upload(
        tree, destination, path("tree"), path("bucket/tree"), policy=ConflictResolution.RENAME
    )
    row = next(row for row in plan.objects if row.source.name == "rows.json")
    assert row.destination == path("bucket/tree/data/rows (2).json")
    assert len({row.destination for row in plan.objects}) == len(plan.objects)


@pytest.mark.parametrize("source", ["tree/zero", "missing"])
async def test_invalid_source_root_fails_before_destination_reads(tree, source):
    destination = PlanningS3()
    with pytest.raises(UploadPlanError):
        await plan_upload(tree, destination, path(source), path("bucket/tree"))
    assert not destination.observations
    assert not destination.mutations


async def test_bucketless_destination_rejected_before_walk(tree):
    with pytest.raises(UploadPlanError, match="bucket"):
        await plan_upload(tree, PlanningS3(), path("tree"), PathRef())


async def test_budget_refusal_occurs_before_destination_reads(tree):
    destination = PlanningS3()
    with pytest.raises(UploadPlanError, match=r"entry.*limit"):
        await plan_upload(tree, destination, path("tree"), path("bucket/tree"), max_entries=2)
    assert not destination.observations
    with pytest.raises(UploadPlanError, match=r"depth.*limit"):
        await plan_upload(tree, destination, path("tree"), path("bucket/tree"), max_depth=1)
    assert not destination.observations


@pytest.mark.parametrize("name", ["..", ".", "a/b", "", "nul\x00name"])
async def test_ambiguous_child_names_fail_before_destination_reads(tree, monkeypatch, name):
    async def listing(_):
        return [FileEntry(name, EntryKind.FILE, 1, None, "revision")]

    monkeypatch.setattr(tree, "list_upload_directory", listing)
    destination = PlanningS3()
    with pytest.raises(UploadPlanError, match="name"):
        await plan_upload(tree, destination, path("tree"), path("bucket/tree"))
    assert not destination.observations


async def test_duplicate_listing_names_are_not_silently_overwritten(tree, monkeypatch):
    original = tree.list_upload_directory

    async def listing(value):
        rows = await original(value)
        return rows + rows[:1]

    monkeypatch.setattr(tree, "list_upload_directory", listing)
    with pytest.raises(UploadPlanError, match="Duplicate"):
        await plan_upload(tree, PlanningS3(), path("tree"), path("bucket/tree"))


async def test_oversized_utf8_key_fails_before_destination_reads(tree):
    destination = PlanningS3()
    with pytest.raises(UploadPlanError, match="1024"):
        await plan_upload(tree, destination, path("tree"), PathRef(("bucket", "é" * 510)))
    assert not destination.observations


async def test_plan_is_immutable_and_names_original_endpoints(tree):
    plan = await plan_upload(tree, PlanningS3(), path("tree"), path("bucket/prefix"))
    assert plan.source_root == path("tree")
    assert plan.destination_prefix == path("bucket/prefix")
    with pytest.raises(AttributeError):
        plan.objects = ()


async def test_root_replaced_between_initial_observation_and_walk_is_rejected(tree, monkeypatch):
    original = tree.inspect_upload_entry
    seen = 0

    async def inspect(value):
        nonlocal seen
        observed = await original(value)
        if value == path("tree"):
            seen += 1
            if seen > 1:
                return replace(observed, kind=EntryKind.FILE, size=0)
        return observed

    monkeypatch.setattr(tree, "inspect_upload_entry", inspect)
    destination = PlanningS3()
    with pytest.raises(UploadPlanError, match="root changed"):
        await plan_upload(tree, destination, path("tree"), path("bucket/tree"))
    assert not destination.observations


async def test_changed_directory_invalidates_entire_plan_before_destination_reads(
    tree, monkeypatch
):
    original = tree.inspect_upload_entry
    seen = 0

    async def inspect(value):
        nonlocal seen
        observed = await original(value)
        if value == path("tree/data"):
            seen += 1
            if seen > 1:
                return replace(observed, etag="changed")
        return observed

    monkeypatch.setattr(tree, "inspect_upload_entry", inspect)
    destination = PlanningS3()
    with pytest.raises(UploadPlanError, match="Directory changed"):
        await plan_upload(tree, destination, path("tree"), path("bucket/tree"))
    assert not destination.observations


@pytest.mark.parametrize(
    ("entries", "depth"), [(10_001, 128), (10_000, 129), (True, 128), (10_000, -1)]
)
async def test_callers_cannot_raise_the_existing_safety_limits(tree, entries, depth):
    with pytest.raises(UploadPlanError, match="limit"):
        await plan_upload(
            tree,
            PlanningS3(),
            path("tree"),
            path("bucket/tree"),
            max_entries=entries,
            max_depth=depth,
        )


async def test_overwrite_requires_observed_destination_revision(tree):
    destination = PlanningS3()
    destination.keys[path("bucket/tree/zero")] = FileEntry("zero", EntryKind.FILE, 1, None)
    with pytest.raises(UploadPlanError, match="revision"):
        await plan_upload(
            tree,
            destination,
            path("tree"),
            path("bucket/tree"),
            policy=ConflictResolution.OVERWRITE,
        )


@pytest.mark.skipif(os.name == "nt", reason="POSIX component traversal contract")
async def test_unrooted_source_does_not_follow_an_ancestor_symlink(tmp_path):
    real = tmp_path / "real"
    real.mkdir()
    (real / "tree").mkdir()
    (real / "tree/secret").write_bytes(b"outside")
    alias = tmp_path / "alias"
    alias.symlink_to(real, target_is_directory=True)
    source = LocalFS()
    requested = PathRef.from_posix(str(alias / "tree"))
    with pytest.raises(UploadPlanError):
        await plan_upload(source, PlanningS3(), requested, path("bucket/tree"))


@pytest.mark.skipif(os.name == "nt", reason="POSIX component traversal contract")
async def test_unrooted_upload_listing_rejects_symlinked_directory(tmp_path):
    real = tmp_path / "real"
    real.mkdir()
    alias = tmp_path / "alias"
    alias.symlink_to(real, target_is_directory=True)
    source = LocalFS()
    from aws_tui.domain.filesystem import ProviderError

    with pytest.raises(ProviderError):
        await source.list_upload_directory(PathRef.from_posix(str(alias)))


@pytest.mark.parametrize("mode", ["entries", "depth"])
async def test_real_default_budgets_reject_excess_without_remote_reads(mode):
    class SyntheticSource:
        async def inspect_upload_entry(self, value):
            kind = (
                EntryKind.DIRECTORY if mode == "depth" or value == path("tree") else EntryKind.FILE
            )
            return FileEntry(
                value.name, kind, None if kind == EntryKind.DIRECTORY else 0, None, "rev"
            )

        async def list_upload_directory(self, value):
            if mode == "entries":
                return [FileEntry(str(n), EntryKind.FILE, 0, None, "rev") for n in range(10_000)]
            if len(value.segments) <= 129:
                return [FileEntry("nested", EntryKind.DIRECTORY, None, None, "rev")]
            return []

    destination = PlanningS3()
    with pytest.raises(UploadPlanError, match="limit"):
        await plan_upload(SyntheticSource(), destination, path("tree"), path("bucket/tree"))
    assert not destination.observations


@pytest.mark.parametrize("which", ["source", "destination"])
async def test_unsupported_provider_is_named_before_walk(tree, which):
    with pytest.raises(UploadPlanError, match="support"):
        await plan_upload(
            object() if which == "source" else tree,
            object() if which == "destination" else PlanningS3(),
            path("tree"),
            path("bucket/tree"),
        )


async def test_root_replaced_with_special_file_cannot_be_an_excluded_empty_plan(tree, monkeypatch):
    original = tree.inspect_upload_entry
    seen = 0

    async def inspect(value):
        nonlocal seen
        if value == path("tree"):
            seen += 1
            if seen > 1:
                raise UnsupportedSourceError("not a regular file or directory")
        return await original(value)

    monkeypatch.setattr(tree, "inspect_upload_entry", inspect)
    destination = PlanningS3()
    with pytest.raises(UploadPlanError, match="root changed"):
        await plan_upload(tree, destination, path("tree"), path("bucket/tree"))
    assert not destination.observations
