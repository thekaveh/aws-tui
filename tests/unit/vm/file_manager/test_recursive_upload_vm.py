"""Reviewed upload admission and outcome lineage use the captured pane owners."""

import asyncio
from dataclasses import replace

import pytest
from vmx import NULL_DISPATCHER, MessageHub

from aws_tui.demo.in_memory_fs import InMemoryFS
from aws_tui.domain.cross_fs import ConflictResolution
from aws_tui.domain.filesystem import ConflictError, PathRef, ProviderError
from aws_tui.domain.local_fs import LocalFS
from aws_tui.domain.transfer_journal import TransferJournal
from aws_tui.domain.upload_batch import UploadStatus
from aws_tui.vm.file_manager.pane_vm import PaneVM
from aws_tui.vm.file_manager.transfer_runtime import TransferRuntime
from tests.s3_versions_support import ReadGate
from tests.unit.domain.test_upload_batch import Destination


class UploadFS(Destination, InMemoryFS):
    def __init__(self):
        Destination.__init__(self)
        InMemoryFS.__init__(self)
        self.read_gate = None

    def upload_location(self, value):
        if not value.segments:
            raise ProviderError("Choose a bucket first")
        return value.segments[0], "/".join(value.segments[1:])

    async def inspect_upload_destination(self, value):
        if self.read_gate:
            await self.read_gate.wait()
        return await super().inspect_upload_destination(value)


@pytest.fixture
async def context(tmp_path):
    (tmp_path / "tree").mkdir()
    (tmp_path / "tree" / "a").write_bytes(b"hello")
    (tmp_path / "tree" / "b").write_bytes(b"world")
    (tmp_path / "tree" / "empty").mkdir()
    (tmp_path / "another").mkdir()
    hub = MessageHub()
    destination = UploadFS()
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
    from aws_tui.vm.file_manager.recursive_upload_vm import RecursiveUploadVM

    vm = RecursiveUploadVM(source=source, destination=target, hub=hub)
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


async def test_plan_is_read_only_and_admission_requires_the_exact_current_review(context):
    vm, _, _, destination, runtime = context
    await vm.load()
    assert vm.state == "ready"
    assert vm.plan.policy == ConflictResolution.ERROR
    assert vm.plan.known_bytes == 10
    assert len(vm.plan.empty_directories) == 1
    prepared = vm.prepare()
    assert not destination.writes
    with pytest.raises(ConflictError, match="accepted"):
        await vm.execute(prepared, runtime)
    assert not vm.accepts(replace(prepared, plan=replace(prepared.plan)))
    vm.accept(prepared)
    await vm.execute(prepared, runtime)
    assert vm.snapshot.status == UploadStatus.SUCCEEDED
    assert len(destination.objects) == 2
    with pytest.raises(ConflictError):
        await vm.execute(prepared, runtime)


async def test_policy_change_retires_review_and_requires_fresh_metadata(context):
    vm, _, _, destination, _ = context
    destination.objects[PathRef(("bucket", "prefix", "tree", "a"))] = b"old"
    await vm.load()
    first = vm.prepare()
    assert first.plan.conflicts == 1
    vm.set_policy(ConflictResolution.OVERWRITE)
    assert vm.plan is None
    assert not vm.accepts(first)
    await vm.load()
    assert vm.prepare().plan.policy == ConflictResolution.OVERWRITE
    assert not destination.writes


@pytest.mark.parametrize("side", ["source", "destination"])
async def test_navigation_retires_even_a_cancellation_resistant_plan(context, side):
    vm, source, target, destination, _ = context
    gate = destination.read_gate = ReadGate(resistant=True)
    task = asyncio.create_task(vm.load())
    await asyncio.wait_for(gate.entered.wait(), 1)
    pane = source if side == "source" else target
    await pane.navigate_to(PathRef(("another",)) if side == "source" else PathRef(("bucket",)))
    gate.release.set()
    await task
    assert vm.state == "closed"
    assert vm.plan is None
    assert not destination.writes


async def test_loading_observer_can_invalidate_before_any_provider_read(context):
    vm, _, _, destination, _ = context
    subscription = vm.on_property_changed.subscribe(
        lambda _: vm.close() if vm.state == "planning" else None
    )
    await vm.load()
    subscription.dispose()
    assert not destination.reads
    assert vm.state == "closed"


async def test_retry_refreshes_only_failed_and_retains_successful_overwrite(context):
    vm, _, target, destination, runtime = context
    a, b = (PathRef(("bucket", "prefix", "tree", name)) for name in ("a", "b"))
    destination.objects[a] = b"old"
    destination.errors[b] = ProviderError("denied")
    vm.set_policy(ConflictResolution.OVERWRITE)
    await vm.load()
    prepared = vm.prepare()
    vm.accept(prepared)
    await vm.execute(prepared, runtime)
    assert vm.snapshot.status == UploadStatus.FAILED
    assert vm.can_retry
    destination.errors.clear()
    await target.refresh()
    await vm.load()
    assert [row.source.name for row in vm.plan.objects] == ["b"]
    prepared = vm.prepare()
    vm.accept(prepared)
    await vm.execute(prepared, runtime)
    assert vm.snapshot.status == UploadStatus.SUCCEEDED
    assert len(vm.snapshot.outcomes) == 2
    assert sum(path == a for path, _ in destination.writes) == 1


async def test_retry_success_does_not_erase_previous_uncertain_publication(context):
    vm, _, _, destination, runtime = context
    a, b = (PathRef(("bucket", "prefix", "tree", name)) for name in ("a", "b"))
    destination.unknown.add(a)
    destination.errors[b] = ProviderError("denied")
    await vm.load()
    prepared = vm.prepare()
    vm.accept(prepared)
    await vm.execute(prepared, runtime)
    destination.errors.clear()
    await vm.load()
    assert [row.source.name for row in vm.plan.objects] == ["b"]
    prepared = vm.prepare()
    vm.accept(prepared)
    await vm.execute(prepared, runtime)
    assert vm.snapshot.status == UploadStatus.FAILED
    assert any(row.uncertain for row in vm.snapshot.outcomes)
    assert not vm.can_retry
    assert sum(path == a for path, _ in destination.writes) == 1


async def test_hiding_modal_does_not_cancel_an_accepted_app_owned_transfer(context):
    vm, _, _, destination, runtime = context
    destination.hold = asyncio.Event()
    await vm.load()
    prepared = vm.prepare()
    vm.accept(prepared)
    vm.close()
    vm.dispose()
    task = asyncio.create_task(vm.execute(prepared, runtime))
    await asyncio.wait_for(destination.entered.get(), 2)
    assert not task.done()
    destination.hold.set()
    await task
    assert len(destination.objects) == 2
    assert vm.snapshot.status == UploadStatus.SUCCEEDED
    assert vm.state == "closed"


async def test_raw_planning_cancellation_releases_controls_and_can_replan(context):
    vm, _, _, destination, _ = context
    gate = destination.read_gate = ReadGate()
    task = asyncio.create_task(vm.load())
    await asyncio.wait_for(gate.entered.wait(), 1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not vm.busy
    destination.read_gate = None
    await vm.load()
    assert vm.state == "ready"


async def test_cancel_between_acceptance_and_app_worker_entry_prevents_all_uploads(context):
    vm, _, _, destination, runtime = context
    await vm.load()
    prepared = vm.prepare()
    vm.accept(prepared)
    vm.cancel()
    await vm.execute(prepared, runtime)
    assert not destination.writes
    assert vm.snapshot.status == UploadStatus.CANCELED


async def test_changed_retry_size_updates_aggregate_without_replaying_success(context, tmp_path):
    vm, _, _, destination, runtime = context
    b = PathRef(("bucket", "prefix", "tree", "b"))
    destination.errors[b] = ProviderError("denied")
    await vm.load()
    prepared = vm.prepare()
    vm.accept(prepared)
    await vm.execute(prepared, runtime)
    (tmp_path / "tree" / "b").write_bytes(b"changed content")
    destination.errors.clear()
    await vm.load()
    prepared = vm.prepare()
    vm.accept(prepared)
    await vm.execute(prepared, runtime)
    assert vm.snapshot.bytes_total == vm.snapshot.bytes_done == 20


async def test_retry_review_is_retired_by_refresh_after_prepare_but_can_replan(context):
    vm, _, target, destination, runtime = context
    b = PathRef(("bucket", "prefix", "tree", "b"))
    destination.errors[b] = ProviderError("denied")
    await vm.load()
    prepared = vm.prepare()
    vm.accept(prepared)
    await vm.execute(prepared, runtime)
    with pytest.raises(ConflictError, match="Review"):
        vm.prepare()
    await target.refresh()  # Refresh before a new review is allowed.
    await vm.load()
    prepared = vm.prepare()
    assert vm.accepts(prepared)
    await target.refresh()  # A review cannot survive a metadata revision change.
    assert not vm.accepts(prepared)
    assert vm.plan is None
    assert vm.state != "closed"
    await vm.load()
    assert vm.accepts(vm.prepare())


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
