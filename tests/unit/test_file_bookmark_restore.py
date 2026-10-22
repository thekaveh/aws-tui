"""A file bookmark prepares one normal pane replacement without side effects."""

import asyncio
from dataclasses import replace

import pytest

from aws_tui.demo.in_memory_fs import InMemoryFS
from aws_tui.domain.filesystem import NotFoundError, PathRef
from aws_tui.domain.navigation_workspace import WorkspaceError
from aws_tui.domain.s3_fs import S3FS
from aws_tui.vm.workspace_panes import capture_pane_target
from tests.s3_readonly import LiteralS3Client
from tests.unit.services.s3.test_workspace_candidate import connection, local, make_service, remote


class PrefixProbeClient(LiteralS3Client):
    async def list_objects_v2(self, **kwargs):
        if kwargs.get("MaxKeys") == 1:
            self.calls.append(("list", kwargs))
            matches = [key for key in self.objects if key.startswith(kwargs["Prefix"])]
            return {
                "KeyCount": min(len(matches), 1),
                "Contents": [{"Key": key} for key in matches[:1]],
            }
        return await super().list_objects_v2(**kwargs)


@pytest.mark.parametrize("segments", [("reports-bucket", ""), ("reports-bucket", "", "")])
@pytest.mark.asyncio
async def test_real_s3_missing_literal_slash_prefix_is_not_bucket_root(tmp_path, segments):
    from aws_tui.workspace_restore import prepare_pane_bookmark

    (tmp_path / "local").mkdir()
    service = make_service(tmp_path)
    live = service.build_vm(connection())
    live.construct()
    client = PrefixProbeClient("different/")
    service._s3_fs_factory = lambda _source: S3FS(session=client, bucket=None)
    try:
        with pytest.raises(WorkspaceError):
            await prepare_pane_bookmark(
                live.left, remote(segments=segments), service, (connection(),)
            )
    finally:
        await live.shutdown()
        live.dispose()
        service.transfer_runtime.dispose()


@pytest.mark.parametrize("operation", ["bookmark", "workspace"])
@pytest.mark.asyncio
async def test_real_s3_empty_listing_does_not_prove_saved_prefix_exists(tmp_path, operation):
    from aws_tui.workspace_restore import prepare_pane_bookmark

    (tmp_path / "local").mkdir()
    service = make_service(tmp_path)
    live = service.build_vm(connection())
    live.construct()
    client = PrefixProbeClient("different/")
    fs = S3FS(session=client, bucket=None)
    target = remote(segments=("reports-bucket", "removed"))
    assert await fs.list(PathRef(target.location.s3)) == []
    service._s3_fs_factory = lambda _source: fs
    candidate = None
    try:

        async def attempt():
            nonlocal candidate
            if operation == "bookmark":
                await prepare_pane_bookmark(live.left, target, service, (connection(),))
            else:
                candidate = service.build_workspace_vm(
                    (target, local(tmp_path / "local")), (connection(),)
                ).vm
                candidate.construct()
                await candidate.setup()

        with pytest.raises(WorkspaceError):
            await attempt()
    finally:
        if candidate is not None:
            await candidate.shutdown()
            candidate.dispose()
        await live.shutdown()
        live.dispose()
        service.transfer_runtime.dispose()


@pytest.mark.parametrize("contents", ["marker", "children", "object_and_prefix"])
@pytest.mark.asyncio
async def test_real_s3_prefix_validation_preserves_literal_directory_namespace(tmp_path, contents):
    from aws_tui.workspace_restore import prepare_pane_bookmark

    (tmp_path / "local").mkdir()
    prefix = "literal//λ\\x/"
    client = PrefixProbeClient(prefix)
    if contents == "marker":
        client.objects = {prefix: b""}
    elif contents == "object_and_prefix":
        client.objects[prefix.removesuffix("/")] = b"same-name-object"
    service = make_service(tmp_path)
    live = service.build_vm(connection())
    live.construct()
    service._s3_fs_factory = lambda _source: S3FS(session=client, bucket=None)
    target = remote(segments=("reports-bucket", "literal", "", "λ\\x"))
    try:
        prepared = await prepare_pane_bookmark(live.left, target, service, (connection(),))
        prepared.commit()
        assert capture_pane_target(live.left) == target
        assert client.calls[0] == (
            "list",
            {"Bucket": "reports-bucket", "Prefix": prefix, "MaxKeys": 1},
        )
        assert all(operation == "list" for operation, _kwargs in client.calls)
    finally:
        await live.shutdown()
        live.dispose()
        service.transfer_runtime.dispose()


@pytest.mark.parametrize("destination", ["local", "remote"])
@pytest.mark.asyncio
async def test_one_pane_bookmark_prepares_then_commits_exact_destination(tmp_path, destination):
    from aws_tui.workspace_restore import prepare_pane_bookmark

    fs = InMemoryFS()
    target = remote(segments=("bucket", "literal", "", "λ\\x"))
    await fs.mkdir(PathRef(target.location.s3))
    (tmp_path / "local" / "chosen").mkdir(parents=True)
    if destination == "local":
        target = local(tmp_path / "local" / "chosen")
    service = make_service(tmp_path, factory=lambda _source: fs)
    live = service.build_vm(connection())
    live.construct()
    await live.setup()
    before = capture_pane_target(live.right)
    other_provider = live.left.provider
    try:
        stage = await prepare_pane_bookmark(live.right, target, service, (connection(),))
        assert capture_pane_target(live.right) == before
        assert stage.is_current()
        stage.commit()
        assert capture_pane_target(live.right) == target
        assert live.left.provider is other_provider
        assert not live._shutdown_started
    finally:
        await live.shutdown()
        live.dispose()
        service.transfer_runtime.dispose()


@pytest.mark.parametrize("segments", [(), ("removed",)])
@pytest.mark.asyncio
async def test_missing_bookmark_is_never_root_fallback(tmp_path, segments):
    from aws_tui.workspace_restore import prepare_pane_bookmark

    class Missing(InMemoryFS):
        async def list(self, path):
            raise NotFoundError("gone")

    (tmp_path / "local").mkdir()
    service = make_service(tmp_path)
    live = service.build_vm(connection())
    live.construct()
    await live.setup()
    service._s3_fs_factory = lambda _source: Missing()
    before = capture_pane_target(live.left)
    try:
        with pytest.raises(WorkspaceError):
            await prepare_pane_bookmark(
                live.left, remote(segments=segments), service, (connection(),)
            )
        assert capture_pane_target(live.left) == before
    finally:
        await live.shutdown()
        live.dispose()
        service.transfer_runtime.dispose()


@pytest.mark.asyncio
async def test_prepared_bookmark_cannot_replace_newer_pane_navigation(tmp_path):
    from aws_tui.workspace_restore import prepare_pane_bookmark

    (tmp_path / "local" / "chosen").mkdir(parents=True)
    service = make_service(tmp_path)
    live = service.build_vm(connection())
    live.construct()
    await live.setup()
    try:
        stage = await prepare_pane_bookmark(
            live.right, local(tmp_path / "local" / "chosen"), service, ()
        )
        await live.right.navigate_to(PathRef(("chosen",)))
        await live.right.navigate_to(PathRef(()))
        assert not stage.is_current()
        with pytest.raises(WorkspaceError):
            stage.commit()
        assert live.right.path == PathRef(())
    finally:
        await live.shutdown()
        live.dispose()
        service.transfer_runtime.dispose()


@pytest.mark.asyncio
async def test_bookmark_source_revalidated_before_provider_creation(tmp_path):
    from aws_tui.workspace_restore import prepare_pane_bookmark

    (tmp_path / "local").mkdir()
    service = make_service(tmp_path)
    live = service.build_vm(connection())
    live.construct()
    called = []
    service._s3_fs_factory = lambda source: called.append(source) or InMemoryFS()
    try:
        with pytest.raises(WorkspaceError):
            await prepare_pane_bookmark(
                live.left, remote(), service, (replace(connection(), region="wrong"),)
            )
        assert not called
    finally:
        await live.shutdown()
        live.dispose()
        service.transfer_runtime.dispose()


@pytest.mark.asyncio
async def test_cancelled_bookmark_drains_real_provider_cleanup_before_return(tmp_path):
    from aws_tui.workspace_restore import prepare_pane_bookmark

    entered, cleanup, release, drained = (asyncio.Event() for _ in range(4))

    class Held(InMemoryFS):
        async def list(self, path):
            entered.set()
            try:
                await asyncio.Future()
            finally:
                cleanup.set()
                await release.wait()
                drained.set()

    (tmp_path / "local").mkdir()
    service = make_service(tmp_path)
    live = service.build_vm(connection())
    live.construct()
    service._s3_fs_factory = lambda _source: Held()
    task = asyncio.create_task(prepare_pane_bookmark(live.left, remote(), service, (connection(),)))
    try:
        await entered.wait()
        task.cancel()
        await cleanup.wait()
        task.cancel()
        await asyncio.sleep(0)
        assert not task.done()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert drained.is_set()
        assert not live._shutdown_started
        assert not service.transfer_runtime.owned_ids
    finally:
        release.set()
        await live.shutdown()
        live.dispose()
        service.transfer_runtime.dispose()


@pytest.mark.parametrize("missing_source", [False, True])
@pytest.mark.asyncio
async def test_inactive_bookmark_prepares_only_target_and_preserves_companion(
    tmp_path, missing_source
):
    from aws_tui.workspace_restore import prepare_file_bookmark, retire_workspace_candidate

    (tmp_path / "local" / "chosen").mkdir(parents=True)
    target = local(tmp_path / "local" / "chosen")
    companion = (
        remote(name="gone", segments=("old-bucket", "old-prefix"))
        if missing_source
        else local(tmp_path / "local" / "removed")
    )
    service = make_service(tmp_path)
    stage = await prepare_file_bookmark(target, companion, service, ())
    try:
        assert stage.connection is None
        assert capture_pane_target(stage.recovery.vm.left) == target
        assert capture_pane_target(stage.recovery.vm.right) == companion
        assert not stage.recovery.vm._navigation_adopted
        assert not stage.recovery.vm._refresh_tasks
        stage.recovery.commit_selection()
        await asyncio.gather(*tuple(stage.recovery.vm._refresh_tasks))
        assert capture_pane_target(stage.recovery.vm.right) == companion
        assert stage.recovery.vm.right.state.value in {"error", "unreachable"}
        assert service.navigation_panes() == (target, companion)
    finally:
        await retire_workspace_candidate(stage.recovery.vm)
        service.transfer_runtime.dispose()


@pytest.mark.asyncio
async def test_unavailable_companion_cannot_perform_any_filesystem_operation(tmp_path):
    from aws_tui.domain.filesystem import ProviderError
    from aws_tui.domain.unavailable_navigation_fs import UnavailableNavigationFS

    target = remote(name="gone")
    fs = UnavailableNavigationFS(target)
    assert fs.navigation_target is target
    path = PathRef(("anything",))

    async def content():
        pytest.fail("Unavailable provider must not consume bytes")
        yield b""

    for call in (
        lambda: fs.list(path),
        lambda: fs.stat(path),
        lambda: fs.mkdir(path),
        lambda: fs.delete(path),
        lambda: fs.delete_empty_directory(path),
        lambda: fs.rename(path, path),
        lambda: fs.read_stream(path),
        lambda: fs.write_stream(path, content()),
    ):
        with pytest.raises(ProviderError):
            await call()
