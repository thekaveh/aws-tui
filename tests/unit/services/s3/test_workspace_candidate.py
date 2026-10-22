"""Saved file destinations prepare off-screen without touching live work."""

import asyncio
from dataclasses import replace

import pytest
from vmx import NULL_DISPATCHER, MessageHub

from aws_tui.demo.in_memory_fs import InMemoryFS
from aws_tui.domain.filesystem import PathRef
from aws_tui.domain.navigation_workspace import FileLocation, NavigationTarget, WorkspaceError
from aws_tui.domain.transfer_journal import TransferJournal
from aws_tui.infra.connection_resolver import Connection
from aws_tui.services.s3 import S3Service
from aws_tui.vm.workspace_panes import capture_pane_target


def connection(name="remote", region="eu-west-1", kind="aws"):
    return Connection(name=name, kind=kind, region=region, source="explicit")


def remote(name="remote", region="eu-west-1", segments=("bucket", "literal", "", "λ\\x")):
    return NavigationTarget("s3", name, region, location=FileLocation(s3=segments))


def local(path):
    return NavigationTarget("s3", location=FileLocation(local=str(path)))


def make_service(tmp_path, *, factory=None, **kwargs):
    return S3Service(
        transfer_journal=TransferJournal(base_dir=tmp_path / "journal"),
        hub=MessageHub(),
        dispatcher=NULL_DISPATCHER,
        local_root=tmp_path / "local",
        s3_fs_factory=factory or (lambda _connection: InMemoryFS()),
        **kwargs,
    )


@pytest.mark.asyncio
async def test_candidate_keeps_swapped_sources_literal_paths_and_launch_inputs(tmp_path):
    root = tmp_path / "local"
    (root / "chosen").mkdir(parents=True)
    (root / "launch").mkdir()
    fs = InMemoryFS()
    target = remote()
    await fs.mkdir(PathRef(target.location.s3))
    calls = []

    def factory(current):
        calls.append(current)
        return fs

    svc = make_service(
        tmp_path,
        factory=factory,
        initial_left_path=PathRef(("launch-bucket",)),
        initial_right_path=PathRef(("launch",)),
    )
    targets = (local(root / "chosen"), target)
    stage = svc.build_workspace_vm(targets, (connection(),))
    dual = stage.vm
    try:
        assert svc.navigation_panes() is None
        dual.construct()
        await dual.setup()
        assert (capture_pane_target(dual.left), capture_pane_target(dual.right)) == targets
        assert calls == [connection()]
        assert dual.transfer_runtime is svc.transfer_runtime
        stage.commit_selection()
        assert svc.navigation_panes() == targets
        await dual.left.navigate_to(PathRef(("launch",)))
        # An acknowledgement is idempotent; it cannot reset newer live navigation.
        stage.commit_selection()
        assert dual.left.path == PathRef(("launch",))
        normal = svc.build_vm(connection())
        try:
            assert normal.left.path == PathRef(("launch-bucket",))
            assert normal.right.path == PathRef(("launch",))
        finally:
            normal.dispose()
    finally:
        await dual.shutdown()
        dual.dispose()
        svc.transfer_runtime.dispose()


@pytest.mark.asyncio
async def test_two_local_panes_need_no_aws_connection(tmp_path):
    root = tmp_path / "local"
    for name in ("first", "second"):
        (root / name).mkdir(parents=True)
    calls = []
    svc = make_service(tmp_path, factory=lambda current: calls.append(current))
    targets = (local(root / "first"), local(root / "second"))
    stage = svc.build_workspace_vm(targets, ())
    try:
        stage.vm.construct()
        await stage.vm.setup()
        stage.commit_selection()
        assert svc.navigation_panes() == targets
        assert calls == []
    finally:
        await stage.vm.shutdown()
        stage.vm.dispose()
        svc.transfer_runtime.dispose()


@pytest.mark.asyncio
async def test_two_remote_panes_use_independent_current_credentials_and_regions(tmp_path):
    first = connection("first", "us-east-1")
    second = replace(
        connection("second", "ap-southeast-2", "s3-compatible"),
        access_key_id="synthetic-current",
        secret_access_key="synthetic-secret",
    )
    calls = []

    def factory(current):
        calls.append(current)
        return InMemoryFS()

    svc = make_service(tmp_path, factory=factory)
    targets = (remote(first.name, first.region, ()), remote(second.name, second.region, ()))
    stage = svc.build_workspace_vm(targets, (first, second))
    try:
        stage.vm.construct()
        await stage.vm.setup()
        assert calls == [first, second]
        assert (capture_pane_target(stage.vm.left), capture_pane_target(stage.vm.right)) == targets
        assert stage.vm.left.provider is not stage.vm.right.provider
    finally:
        await stage.vm.shutdown()
        stage.vm.dispose()
        svc.transfer_runtime.dispose()


@pytest.mark.parametrize(
    "connections",
    [
        (),
        (connection("other"),),
        (connection(region="us-east-1"),),
        (connection(kind="unsupported"),),
        (connection(), connection()),
    ],
)
def test_all_source_references_are_validated_before_any_provider(tmp_path, connections):
    calls = []
    svc = make_service(tmp_path, factory=lambda current: calls.append(current))
    try:
        with pytest.raises(WorkspaceError):
            svc.build_workspace_vm((remote(), remote()), connections)
        assert calls == []
        assert svc.navigation_panes() is None
    finally:
        svc.transfer_runtime.dispose()


@pytest.mark.parametrize("destination", ["missing-root", "missing-child", "outside", "file"])
@pytest.mark.asyncio
async def test_local_missing_or_unreadable_destination_is_not_a_successful_empty_root(
    tmp_path, destination
):
    root = tmp_path / "local"
    if destination != "missing-root":
        root.mkdir()
    if destination == "file":
        (root / "file").write_text("data")
    path = {
        "missing-root": root,
        "missing-child": root / "gone",
        "outside": tmp_path / "outside",
        "file": root / "file",
    }[destination]
    svc = make_service(tmp_path)
    stage = None

    async def prepare_and_commit():
        nonlocal stage
        stage = svc.build_workspace_vm((local(path), remote(segments=())), (connection(),))
        stage.vm.construct()
        await stage.vm.setup()
        stage.commit_selection()

    try:
        with pytest.raises(WorkspaceError):
            await prepare_and_commit()
        assert svc.navigation_panes() is None
    finally:
        if stage is not None:
            await stage.vm.shutdown()
            stage.vm.dispose()
        svc.transfer_runtime.dispose()


@pytest.mark.parametrize("adopt", [False, True])
@pytest.mark.asyncio
async def test_only_an_adopted_candidate_may_shutdown_the_shared_transfer_owner(tmp_path, adopt):
    root = tmp_path / "local"
    root.mkdir()
    svc = make_service(tmp_path)
    stage = svc.build_workspace_vm((local(root), remote(segments=())), (connection(),))
    dual = stage.vm
    dual.construct()
    await dual.setup()
    registered = asyncio.Event()
    release = asyncio.Event()
    ids = []

    async def accepted_before_visible_card():
        tid = await svc.transfer_runtime.begin(
            source_uri="local/synthetic", destination_uri="s3:/bucket/synthetic", bytes_total=1
        )
        ids.append(tid)
        registered.set()
        try:
            await release.wait()
        finally:
            svc.transfer_runtime.release(tid)

    owner = asyncio.create_task(accepted_before_visible_card())
    try:
        await asyncio.wait_for(registered.wait(), 5)
        event = svc.transfer_runtime.cancel_events[ids[0]]
        assert ids[0] in svc.transfer_runtime.owned_ids
        if adopt:
            stage.commit_selection()
        await dual.shutdown()
        dual.dispose()
        if adopt:
            assert owner.cancelled()
            assert event.is_set()
        else:
            assert not owner.done()
            assert not event.is_set()
            assert ids[0] in svc.transfer_runtime.owned_ids
        release.set()
        await asyncio.gather(owner, return_exceptions=True)
    finally:
        release.set()
        await asyncio.gather(owner, return_exceptions=True)
        dual.dispose()
        await svc.transfer_runtime.shutdown()
        svc.transfer_runtime.dispose()


def test_inactive_panes_install_validates_the_entire_batch_before_mutation(tmp_path):
    svc = make_service(tmp_path)
    targets = (remote(segments=()), local(tmp_path / "local"))
    try:
        svc.install_navigation_panes(targets)
        with pytest.raises(WorkspaceError):
            svc.install_navigation_panes((targets[0], NavigationTarget("glue", "x", "us-east-1")))
        assert svc.navigation_panes() == targets
    finally:
        svc.transfer_runtime.dispose()


@pytest.mark.parametrize("change", ["before-setup", "path", "source", "shutdown", "dispose"])
@pytest.mark.asyncio
async def test_candidate_cannot_publish_unprepared_or_changed_destinations(tmp_path, change):
    root = tmp_path / "local"
    (root / "other").mkdir(parents=True)
    svc = make_service(tmp_path)
    stage = svc.build_workspace_vm((local(root), remote(segments=())), (connection(),))
    try:
        stage.vm.construct()
        if change != "before-setup":
            await stage.vm.setup()
        if change == "path":
            await stage.vm.left.navigate_to(PathRef(("other",)))
        elif change == "source":
            await stage.vm.right.swap_provider(
                InMemoryFS(),
                path_protocol="s3:",
                connection_key=("aws", "changed"),
                connection_region="eu-west-1",
            )
        elif change == "shutdown":
            await stage.vm.shutdown()
        elif change == "dispose":
            stage.vm.dispose()
        with pytest.raises(WorkspaceError):
            stage.commit_selection()
        assert svc.navigation_panes() is None
    finally:
        await stage.vm.shutdown()
        stage.vm.dispose()
        svc.transfer_runtime.dispose()


@pytest.mark.asyncio
async def test_canceled_prepare_drains_a_resistant_listing_without_publishing(tmp_path):
    root = tmp_path / "local"
    root.mkdir()
    started = asyncio.Event()
    canceled = asyncio.Event()
    release = asyncio.Event()
    finished = asyncio.Event()

    class ResistantFS(InMemoryFS):
        async def list(self, path):
            started.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                canceled.set()
                await release.wait()
                return []
            finally:
                finished.set()

    svc = make_service(tmp_path, factory=lambda _current: ResistantFS())
    stage = svc.build_workspace_vm((local(root), remote(segments=())), (connection(),))
    stage.vm.construct()
    task = asyncio.create_task(stage.vm.setup())
    try:
        await asyncio.wait_for(started.wait(), 5)
        task.cancel()
        await asyncio.wait_for(canceled.wait(), 5)
        assert not task.done()
        assert not finished.is_set()
        assert svc.navigation_panes() is None
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert finished.is_set()
        with pytest.raises(WorkspaceError):
            stage.commit_selection()
        assert svc.navigation_panes() is None
    finally:
        release.set()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await stage.vm.shutdown()
        stage.vm.dispose()
        svc.transfer_runtime.dispose()


@pytest.mark.asyncio
async def test_virtual_empty_prefix_is_validated_by_listing_without_object_head(tmp_path):
    root = tmp_path / "local"
    root.mkdir()
    calls = []

    class VirtualFS(InMemoryFS):
        async def list(self, path):
            calls.append(("list", path))
            return []

        async def stat(self, path):
            raise AssertionError("Virtual prefixes need not have object/directory markers")

    svc = make_service(tmp_path, factory=lambda _current: VirtualFS())
    targets = (local(root), remote())
    stage = svc.build_workspace_vm(targets, (connection(),))
    try:
        stage.vm.construct()
        await stage.vm.setup()
        stage.commit_selection()
        assert svc.navigation_panes() == targets
        assert calls == [("list", PathRef(targets[1].location.s3))]
    finally:
        await stage.vm.shutdown()
        stage.vm.dispose()
        svc.transfer_runtime.dispose()


@pytest.mark.parametrize("cancel_shutdown", [False, True])
@pytest.mark.asyncio
async def test_candidate_shutdown_drains_initial_reads_before_acknowledging(
    tmp_path, cancel_shutdown
):
    root = tmp_path / "local"
    root.mkdir()
    started = asyncio.Event()
    canceled = asyncio.Event()
    release = asyncio.Event()
    finished = asyncio.Event()

    class HeldFS(InMemoryFS):
        async def list(self, path):
            started.set()
            try:
                try:
                    await release.wait()
                except asyncio.CancelledError:
                    canceled.set()
                    await release.wait()
                return []
            finally:
                finished.set()

    svc = make_service(tmp_path, factory=lambda _current: HeldFS())
    stage = svc.build_workspace_vm((local(root), remote(segments=())), (connection(),))
    stage.vm.construct()
    setup = asyncio.create_task(stage.vm.setup())
    shutdown = None
    try:
        await asyncio.wait_for(started.wait(), 5)
        shutdown = asyncio.create_task(stage.vm.shutdown())
        for _ in range(10):
            await asyncio.sleep(0)
        assert not shutdown.done()
        await asyncio.wait_for(canceled.wait(), 5)
        if cancel_shutdown:
            for _ in range(2):
                shutdown.cancel()
                await asyncio.sleep(0)
                assert not shutdown.done()
        assert not finished.is_set()
        release.set()
        results = await asyncio.gather(shutdown, return_exceptions=True)
        if cancel_shutdown:
            assert isinstance(results[0], asyncio.CancelledError)
        else:
            assert results == [None]
        assert finished.is_set()
        assert setup.done()
        assert svc.navigation_panes() is None
    finally:
        release.set()
        await asyncio.gather(setup, return_exceptions=True)
        if shutdown is not None:
            await asyncio.gather(shutdown, return_exceptions=True)
        stage.vm.dispose()
        svc.transfer_runtime.dispose()
