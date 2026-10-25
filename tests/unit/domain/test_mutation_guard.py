from types import SimpleNamespace

import pytest

from aws_tui.demo.in_memory_fs import InMemoryFS
from aws_tui.domain import filesystem
from aws_tui.domain.cross_fs import CrossFsCopy, CrossFsMove
from aws_tui.domain.filesystem import PathRef, PermissionDeniedError


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "method",
    [
        "mkdir",
        "delete",
        "delete_empty_directory",
        "rename",
        "write_stream",
        "write_upload_stream",
        "write_recovery_stream",
        "start_query",
        "start_job_run",
    ],
)
async def test_denied_mutation_never_calls_provider(method):
    from aws_tui.domain.mutation_guard import GuardedRemote

    calls = []

    async def mutation(*args, **kwargs):
        calls.append((args, kwargs))

    async def deny():
        raise PermissionDeniedError("Connection is protected; remote changes are blocked")

    guarded = GuardedRemote(SimpleNamespace(**{method: mutation}), deny)
    with pytest.raises(PermissionDeniedError, match="protected"):
        await getattr(guarded, method)()
    assert calls == []


@pytest.mark.asyncio
async def test_fresh_policy_no_automatic_replay_and_reads_and_cancellation_allowed():
    from aws_tui.domain.mutation_guard import GuardedRemote

    protected = True
    calls = []

    async def check():
        if protected:
            raise PermissionDeniedError("protected")

    async def read():
        calls.append("read")
        return "metadata"

    async def start():
        calls.append("start")

    async def cancel():
        calls.append("cancel")

    client = GuardedRemote(
        SimpleNamespace(start_query=start, list=read, stop_query=cancel, cancel_job_run=cancel),
        check,
    )
    assert await client.list() == "metadata"
    await client.stop_query()
    await client.cancel_job_run()
    with pytest.raises(PermissionDeniedError):
        await client.start_query()
    protected = False
    assert calls == ["read", "cancel", "cancel"]
    await client.start_query()
    assert calls == ["read", "cancel", "cancel", "start"]


def test_filesystem_capabilities_are_preserved_without_fabrication():
    from aws_tui.domain.mutation_guard import GuardedRemote

    async def allow():
        pass

    for provider in (InMemoryFS(), SimpleNamespace()):
        wrapped = GuardedRemote(provider, allow)
        for name in dir(filesystem):
            protocol = getattr(filesystem, name)
            if getattr(protocol, "_is_runtime_protocol", False):
                assert isinstance(wrapped, protocol) == isinstance(provider, protocol), name
    assert not hasattr(GuardedRemote(SimpleNamespace(), allow), "write_upload_stream")


@pytest.mark.asyncio
async def test_recovery_destination_inherits_guard():
    from aws_tui.domain.mutation_guard import GuardedRemote

    calls = []

    async def deny():
        raise PermissionDeniedError("protected")

    async def write(*args):
        calls.append("write")

    async def recovery(*args):
        return SimpleNamespace(write_stream=write)

    provider = GuardedRemote(SimpleNamespace(recovery_destination=recovery), deny)
    writer = await provider.recovery_destination(None, None)
    with pytest.raises(PermissionDeniedError):
        await writer.write_stream()
    assert calls == []


@pytest.mark.asyncio
async def test_move_from_protected_source_refuses_before_destination_write(tmp_path):
    from aws_tui.domain.local_fs import LocalFS
    from aws_tui.domain.mutation_guard import GuardedRemote

    source_root = tmp_path / "source"
    dest_root = tmp_path / "destination"
    source_root.mkdir()
    dest_root.mkdir()
    (source_root / "value").write_bytes(b"synthetic")

    async def deny():
        raise PermissionDeniedError("protected")

    source = GuardedRemote(LocalFS(root=source_root), deny)
    destination = LocalFS(root=dest_root)
    path = PathRef(("value",))
    with pytest.raises(PermissionDeniedError):
        await CrossFsMove(source=source, destination=destination).move(path, path)
    assert list(dest_root.iterdir()) == []
    assert (source_root / "value").read_bytes() == b"synthetic"
    assert await CrossFsCopy(source=source, destination=destination).copy(path, path)
    assert (dest_root / "value").read_bytes() == b"synthetic"


def test_object_details_and_version_protocol_capabilities_are_preserved():
    from aws_tui.domain.mutation_guard import GuardedRemote
    from aws_tui.domain.s3_fs import S3FS
    from aws_tui.domain.s3_object_details import S3ObjectDetailsProvider
    from aws_tui.domain.s3_versions import S3VersionProvider

    async def allow():
        pass

    provider = S3FS(session=object(), bucket=None)
    wrapped = GuardedRemote(provider, allow)
    assert isinstance(wrapped, S3ObjectDetailsProvider)
    assert isinstance(wrapped, S3VersionProvider)


@pytest.mark.asyncio
async def test_recovery_publication_warning_is_preserved():
    from aws_tui.domain.mutation_guard import GuardedRemote
    from aws_tui.domain.s3_versions import S3RecoveryDestination

    original = S3RecoveryDestination(SimpleNamespace(), SimpleNamespace(publication="owned"))

    async def recovery(*args):
        return original

    async def allow():
        pass

    wrapped = GuardedRemote(SimpleNamespace(recovery_destination=recovery), allow)
    destination = await wrapped.recovery_destination(None, None)
    assert isinstance(destination, S3RecoveryDestination)
    assert destination.publication == "owned"


@pytest.mark.asyncio
async def test_athena_preserves_safe_protection_explanation():
    from aws_tui.domain.athena_runner import _prepared_provider_error
    from aws_tui.domain.mutation_guard import PROTECTED_MESSAGE, fixed_mutation_check
    from aws_tui.vm.athena._errors import map_provider_error

    with pytest.raises(PermissionDeniedError) as caught:
        await fixed_mutation_check(True)()
    prepared = _prepared_provider_error(caught.value, phase="start")
    _, message = map_provider_error(prepared.public_error(), fallback="failed")
    assert message == PROTECTED_MESSAGE


def test_iceberg_preserves_safe_protection_explanation():
    from aws_tui.domain.mutation_guard import PROTECTED_MESSAGE, ConnectionProtectedError
    from aws_tui.vm.glue.iceberg_vm import _map_iceberg_provider_error

    _, message = _map_iceberg_provider_error(ConnectionProtectedError(PROTECTED_MESSAGE))
    assert message == PROTECTED_MESSAGE


@pytest.mark.asyncio
async def test_policy_change_does_not_interrupt_an_accepted_provider_call():
    import asyncio

    from aws_tui.domain.mutation_guard import GuardedRemote, fixed_mutation_check

    protected = False
    entered, release = asyncio.Event(), asyncio.Event()
    calls = []

    async def check():
        await fixed_mutation_check(protected)()

    async def write():
        entered.set()
        await release.wait()
        calls.append("finished with cleanup")

    provider = GuardedRemote(SimpleNamespace(write_stream=write), check)
    task = asyncio.create_task(provider.write_stream())
    await entered.wait()
    protected = True
    release.set()
    await task
    assert calls == ["finished with cleanup"]
    with pytest.raises(PermissionDeniedError):
        await provider.write_stream()
