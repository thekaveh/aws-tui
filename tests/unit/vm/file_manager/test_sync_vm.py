"""Sync selection and fresh comparison remain separate from publication receipts."""

from __future__ import annotations

import asyncio
import hashlib
from dataclasses import replace

import pytest
from vmx import NULL_DISPATCHER, MessageHub

from aws_tui.domain.filesystem import ConflictError, PathRef, ProviderError
from aws_tui.domain.local_fs import LocalFS
from aws_tui.domain.sync_plan import SyncChecksum, SyncComparison, SyncLabel, SyncSettings
from aws_tui.domain.transfer_journal import TransferJournal
from aws_tui.domain.upload_batch import UploadStatus
from aws_tui.vm.file_manager.pane_vm import PaneVM
from aws_tui.vm.file_manager.sync_vm import SyncVM
from aws_tui.vm.file_manager.transfer_runtime import TransferRuntime
from tests.s3_versions_support import ReadGate
from tests.unit.vm.file_manager.test_recursive_upload_vm import UploadFS

pytestmark = pytest.mark.unit


class SyncFS(UploadFS):
    async def inspect_upload_destination(self, value):
        entry = await super().inspect_upload_destination(value)
        return replace(entry, etag="revision:" + entry.etag) if entry is not None else None

    async def read_sync_checksum(self, value, *, expected):
        data = self.objects[value]
        return SyncChecksum(hashlib.sha256(data).hexdigest(), len(data), expected.etag)


def source_path(name):
    return PathRef(("tree", name))


def target_path(name):
    return PathRef(("bucket", "prefix", "tree", name))


def view(vm, name):
    return next(row for row in vm.rows if row.comparison.relative == name)


@pytest.fixture
async def context(tmp_path):
    (tmp_path / "tree").mkdir()
    for name, data in (("a", b"hello"), ("b", b"world")):
        (tmp_path / "tree" / name).write_bytes(data)
    (tmp_path / "another").mkdir()
    hub, destination = MessageHub(), SyncFS()
    await destination.mkdir(PathRef(("bucket", "prefix")))
    source = PaneVM(provider=LocalFS(root=tmp_path), hub=hub, dispatcher=NULL_DISPATCHER)
    target = PaneVM(
        provider=destination,
        path_protocol="s3:",
        connection_key=("aws", "fixture"),
        hub=hub,
        dispatcher=NULL_DISPATCHER,
    )
    for pane in (source, target):
        pane.construct()
        await pane.setup()
    await target.navigate_to(PathRef(("bucket", "prefix")))
    source.move_cursor_to(
        next(i for i, row in enumerate(source.filtered_entries) if row.name == "tree")
    )
    vm = SyncVM(source=source, destination=target, hub=hub)
    vm.construct()
    runtime = TransferRuntime(TransferJournal(base_dir=tmp_path / "journal"), hub)
    try:
        yield vm, source, target, destination, runtime
    finally:
        await vm.shutdown()
        vm.dispose()
        await runtime.shutdown()
        runtime.dispose()
        source.dispose()
        target.dispose()


async def test_selection_projects_pending_counts_and_invalidates_confirmation(context):
    vm, _, _, destination, runtime = context
    await vm.load()
    old = vm.prepare()
    vm.toggle_operation(source_path("b"))
    assert not vm.accepts(old)
    assert [item.source for item in vm.plan.objects] == [source_path("a")]
    assert vm.selected_count == vm.remaining_count == 1
    assert vm.selected_bytes == vm.snapshot.bytes_total == 5
    assert len(vm.snapshot.outcomes) == 1
    assert view(vm, "b").state == "omitted"
    current = vm.prepare()
    vm.accept(current)
    await vm.execute(current, runtime)
    assert destination.writes == [(target_path("a"), True)]
    assert vm.remaining_count == 0
    assert view(vm, "b").state == "omitted"
    await vm.load()
    assert not vm.plan.objects
    assert not vm.can_retry
    assert target_path("b") not in destination.objects


async def test_setting_change_and_aba_input_edit_require_new_review(context):
    vm, _, _, destination, _ = context
    await vm.load()
    old = vm.prepare()
    vm.set_settings(SyncSettings(comparison=SyncComparison.SIZE_TIME))
    assert vm.plan is None
    assert vm.review is None
    assert not vm.accepts(old)
    await vm.load()
    current = vm.prepare()
    # Native field revision changes can occur even when final text is unchanged.
    vm.invalidate_settings()
    assert not vm.accepts(current)
    assert vm.state == "idle"
    assert not destination.writes


async def test_failed_then_matching_retry_resolves_work_without_inventing_publication(context):
    vm, _, _, destination, runtime = context
    destination.errors[target_path("a")] = ProviderError("controlled failure")
    await vm.load()
    prepared = vm.prepare()
    vm.accept(prepared)
    await vm.execute(prepared, runtime)
    assert view(vm, "a").outcome.status == UploadStatus.FAILED
    assert vm.can_retry
    destination.errors.clear()
    destination.objects[target_path("a")] = b"hello"  # Another writer, not our receipt.
    writes = tuple(destination.writes)
    await vm.load()
    assert not vm.plan.objects
    assert vm.remaining_count == 0
    assert not vm.can_retry
    assert view(vm, "a").comparison.label == SyncLabel.UNCHANGED
    assert view(vm, "a").state == "unchanged"
    assert view(vm, "a").outcome.status == UploadStatus.FAILED
    assert not view(vm, "a").outcome.published
    assert tuple(destination.writes) == writes


async def test_missing_retry_source_is_still_unresolved_conflict(context, tmp_path):
    vm, _, _, destination, runtime = context
    destination.errors[target_path("a")] = ProviderError("controlled failure")
    await vm.load()
    prepared = vm.prepare()
    vm.accept(prepared)
    await vm.execute(prepared, runtime)
    (tmp_path / "tree/a").unlink()
    await vm.load()
    assert not vm.plan.objects
    assert view(vm, "a").state == "conflict"
    assert vm.remaining_count == 1
    assert vm.can_retry
    with pytest.raises(ConflictError):
        vm.prepare()


async def test_changed_source_after_review_is_an_explicit_execution_conflict(context, tmp_path):
    vm, _, _, destination, runtime = context
    await vm.load()
    prepared = vm.prepare()
    (tmp_path / "tree/a").write_bytes(b"changed after review")
    vm.accept(prepared)
    await vm.execute(prepared, runtime)
    assert target_path("a") not in destination.objects
    assert view(vm, "a").state == "conflict"
    assert vm.remaining_count == 1
    assert vm.can_retry


async def test_uncertain_publication_survives_another_paths_noop_resolution(context):
    vm, _, _, destination, runtime = context
    destination.unknown.add(target_path("a"))
    destination.errors[target_path("b")] = ProviderError("controlled failure")
    await vm.load()
    prepared = vm.prepare()
    vm.accept(prepared)
    await vm.execute(prepared, runtime)
    destination.errors.clear()
    destination.objects[target_path("b")] = b"world"
    await vm.load()
    assert not vm.plan.objects
    assert view(vm, "a").state == "uncertain"
    assert vm.remaining_count == 1
    assert not vm.can_retry
    assert vm.snapshot.status == UploadStatus.FAILED


async def test_navigation_retires_a_resistant_plan_without_installing_sync_rows(context):
    vm, _, target, destination, _ = context
    gate = destination.read_gate = ReadGate(resistant=True)
    task = asyncio.create_task(vm.load())
    await asyncio.wait_for(gate.entered.wait(), 3)
    await target.navigate_to(PathRef(("bucket",)))
    gate.release.set()
    await task
    assert vm.state == "closed"
    assert vm.plan is None
    assert vm.review is None
    assert not destination.writes


async def test_unchanged_initial_plan_is_a_clear_noop_without_confirmation(context):
    vm, _, _, destination, _ = context
    destination.objects = {target_path("a"): b"hello", target_path("b"): b"world"}
    await vm.load()
    assert vm.state == "ready"
    assert vm.selected_count == vm.selected_bytes == vm.remaining_count == 0
    assert [row.state for row in vm.rows] == ["unchanged", "unchanged"]
    with pytest.raises(ConflictError):
        vm.prepare()
    assert not destination.writes


async def test_same_settings_fresh_scan_replaces_previous_unchanged_resolution(context, tmp_path):
    vm, _, _, destination, _ = context
    destination.objects[target_path("a")] = b"hello"
    await vm.load()
    assert view(vm, "a").state == "unchanged"
    assert vm.remaining_count == 1
    (tmp_path / "tree/a").write_bytes(b"other")
    await vm.load()
    assert view(vm, "a").comparison.label == SyncLabel.UPDATE
    assert view(vm, "a").state == "update"
    assert vm.selected_count == vm.remaining_count == 2
    assert [item.source.name for item in vm.prepare().plan.objects] == ["a", "b"]


async def test_retry_never_discovers_new_files_or_replays_owned_success(context, tmp_path):
    vm, _, _, destination, runtime = context
    destination.errors[target_path("a")] = ProviderError("controlled failure")
    await vm.load()
    prepared = vm.prepare()
    vm.accept(prepared)
    await vm.execute(prepared, runtime)
    destination.errors.clear()
    (tmp_path / "tree/new").write_bytes(b"not reviewed")
    await vm.load()
    assert [item.source for item in vm.plan.objects] == [source_path("a")]
    prepared = vm.prepare()
    vm.accept(prepared)
    await vm.execute(prepared, runtime)
    assert vm.remaining_count == 0
    assert not vm.can_retry
    assert sum(path == target_path("b") for path, _ in destination.writes) == 1
    assert target_path("new") not in destination.objects


async def test_reincluded_draft_operation_and_new_settings_do_not_reuse_old_token(context):
    vm, _, _, destination, _ = context
    await vm.load()
    vm.toggle_operation(source_path("a"))
    old = vm.prepare()
    vm.toggle_operation(source_path("a"))
    assert not vm.accepts(old)
    assert vm.selected_count == vm.remaining_count == 2
    assert vm.selected_bytes == vm.snapshot.bytes_total == 10
    vm.set_settings(SyncSettings(exclude=("b",)))
    await vm.load()
    assert [item.source for item in vm.plan.objects] == [source_path("a")]
    assert not destination.writes


async def test_dropped_failed_retry_is_not_reintroduced_by_next_retry(context):
    vm, _, _, destination, runtime = context
    destination.errors = {
        target_path(name): ProviderError("controlled failure") for name in ("a", "b")
    }
    await vm.load()
    prepared = vm.prepare()
    vm.accept(prepared)
    await vm.execute(prepared, runtime)
    destination.errors.clear()
    await vm.load()
    vm.toggle_operation(source_path("b"))
    assert vm.remaining_count == 1
    prepared = vm.prepare()
    vm.accept(prepared)
    await vm.execute(prepared, runtime)
    await vm.load()
    assert not vm.plan.objects
    assert not vm.can_retry
    assert view(vm, "b").state == "omitted"
    assert view(vm, "b").outcome.status == UploadStatus.FAILED


async def test_cancellation_and_hiding_keep_receipts_and_replan_unattempted_subset(
    context, tmp_path
):
    vm, _, _, destination, runtime = context
    for name in ("c", "d", "e", "f"):
        (tmp_path / "tree" / name).write_bytes(b"more")
    destination.hold = asyncio.Event()
    await vm.load()
    prepared = vm.prepare()
    vm.accept(prepared)
    task = asyncio.create_task(vm.execute(prepared, runtime))
    for _ in range(4):
        await asyncio.wait_for(destination.entered.get(), 3)
    vm.cancel()
    destination.hold.set()
    await task
    assert vm.snapshot.finished
    assert not destination.objects
    assert vm.remaining_count == 6
    assert vm.can_retry
    destination.hold = None
    await vm.load()
    assert [item.source.name for item in vm.plan.objects] == ["e", "f"]
    prepared = vm.prepare()
    vm.accept(prepared)
    vm.close()
    await vm.execute(prepared, runtime)
    assert len(destination.objects) == 2
    assert vm.state == "closed"
    assert vm.remaining_count == 4
    assert not vm.can_retry
    assert sum(row.state == "canceled" for row in vm.rows) == 4


async def test_protection_enabled_after_review_blocks_all_upload_requests(context):
    from aws_tui.domain.filesystem import PermissionDeniedError
    from aws_tui.domain.mutation_guard import GuardedRemote

    existing, source, target, destination, runtime = context
    protected = False

    async def check():
        if protected:
            raise PermissionDeniedError("Connection is protected; remote changes are blocked")

    await target.swap_provider(
        GuardedRemote(destination, check), path_protocol="s3:", connection_key=("aws", "fixture")
    )
    await target.navigate_to(PathRef(("bucket", "prefix")))
    vm = type(existing)(source=source, destination=target, hub=existing._hub)
    vm.construct()
    try:
        await vm.load()
        prepared = vm.prepare()
        vm.accept(prepared)
        protected = True
        await vm.execute(prepared, runtime)
        assert vm.snapshot.status == UploadStatus.FAILED
        assert not destination.writes
        assert not destination.objects
        protected = False
        assert not destination.writes
    finally:
        await vm.shutdown()
        vm.dispose()
