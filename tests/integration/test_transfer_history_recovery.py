"""History recovery uses the composed endpoint factory and original bindings."""

from __future__ import annotations

from dataclasses import replace

import pytest

from aws_tui.demo.in_memory_fs import InMemoryFS
from aws_tui.domain.cross_fs import ConflictResolution
from aws_tui.domain.filesystem import PathRef
from aws_tui.domain.transfer_history import TransferHistoryDescriptor
from aws_tui.vm.credential_recovery import connection_history_identity
from aws_tui.vm.file_manager.transfer_history_vm import RecoveryRefused


async def seed(provider):
    async def data():
        yield b"original"

    await provider.write_stream(PathRef(("original?#%.txt",)), data())


async def choose_error(plan):
    assert plan.default_conflict == ConflictResolution.ERROR
    return ConflictResolution.ERROR


async def case(app_context_factory):
    remote = InMemoryFS()
    await seed(remote)
    ctx = app_context_factory(fs=remote)
    connection = ctx.connection_resolver.resolve("test")
    service = ctx.registry.get("s3")
    dual = service.build_vm(connection)
    descriptor = TransferHistoryDescriptor(
        "copy",
        dual.left.transfer_connection,
        dual.right.transfer_connection,
        "s3://original?#%.txt",
        "/copied?#%.txt",
        8,
    )
    tid = ctx.transfer_journal.begin(
        source_uri=descriptor.source_uri,
        destination_uri=descriptor.destination_uri,
        bytes_total=8,
        descriptor=descriptor,
    )
    await ctx.transfer_history_vm.load()
    return ctx, dual, service, connection, tid


async def test_composed_retry_uses_original_paths_despite_pane_navigation(app_context_factory):
    ctx, dual, service, _, tid = await case(app_context_factory)
    dual.construct()
    await dual.setup()
    try:
        await dual.left.provider.mkdir(PathRef(("other",)))
        await dual.left.navigate_to(PathRef(("other",)))
        new_id = await ctx.transfer_history_vm.retry(tid, choose_error)
        assert new_id != tid
        fresh_local = service.build_local_provider()
        copied = b"".join(
            [part async for part in await fresh_local.read_stream(PathRef(("copied?#%.txt",)))]
        )
        assert copied == b"original"
        assert ctx.transfers_vm.transfers[0].id == new_id
    finally:
        await ctx.transfer_history_vm.shutdown()
        dual.dispose()
        ctx.close_unstarted()


@pytest.mark.parametrize("change", ["routing", "local_root", "remote_backing"])
async def test_composed_resolver_refuses_redirected_original_endpoint(
    app_context_factory, change, monkeypatch, tmp_path
):
    ctx, dual, service, connection, tid = await case(app_context_factory)
    try:
        if change == "routing":
            monkeypatch.setattr(
                ctx.connection_resolver, "resolve", lambda _: replace(connection, region="other")
            )
        elif change == "local_root":
            service._local_root = tmp_path
        else:
            replacement = InMemoryFS()
            await seed(replacement)
            service._s3_fs_factory = lambda _: replacement
        decisions = []

        async def decision(plan):
            decisions.append(plan)
            return ConflictResolution.OVERWRITE

        with pytest.raises(RecoveryRefused, match="original connection"):
            await ctx.transfer_history_vm.retry(tid, decision)
        assert not decisions
        assert len(ctx.transfer_journal.load_history()) == 1
    finally:
        await ctx.transfer_history_vm.shutdown()
        dual.dispose()
        ctx.close_unstarted()


def test_provider_namespace_hash_covers_fixed_bucket_and_prefix():
    from aws_tui.domain.s3_fs import S3FS
    from aws_tui.infra.connection_resolver import Connection

    connection = Connection("same", "aws", "region", "config", profile="profile")
    first = S3FS(session=object(), bucket="bucket", prefix="one")
    changed_bucket = S3FS(session=object(), bucket="other", prefix="one")
    changed_prefix = S3FS(session=object(), bucket="bucket", prefix="two")
    bucketless = S3FS(session=object(), bucket=None)
    assert connection_history_identity(connection, first) != connection_history_identity(
        connection, changed_bucket
    )
    assert connection_history_identity(connection, first) != connection_history_identity(
        connection, changed_prefix
    )
    assert connection_history_identity(connection, bucketless) == connection_history_identity(
        connection, S3FS(session=object(), bucket=None)
    )


async def test_missing_optional_namespace_keeps_durable_outcome_but_refuses_recheck(
    app_context_factory,
):
    class NamespaceUnavailableFS(InMemoryFS):
        @property
        def storage_identity(self):
            return None

    remote = NamespaceUnavailableFS()
    await seed(remote)
    ctx = app_context_factory(fs=remote)
    connection = ctx.connection_resolver.resolve("test")
    dual = ctx.registry.get("s3").build_vm(connection)
    dual.construct()
    await dual.setup()
    try:
        dual.left.enter_multiselect_command.execute()
        dual.left.select_all_command.execute()
        await dual.copy_across()
        await ctx.transfer_history_vm.load()
        assert ctx.transfer_history_vm.records[0].status == "completed"
        assert ctx.transfer_history_vm.records[0].source_connection.name == "test"
        with pytest.raises(RecoveryRefused, match="original connection"):
            await ctx.transfer_history_vm.recheck(ctx.transfer_history_vm.records[0].id)
    finally:
        await ctx.transfer_history_vm.shutdown()
        dual.dispose()
        ctx.close_unstarted()


async def test_context_without_s3_service_still_has_safe_empty_history(app_context_factory):
    import inspect

    from aws_tui.composition import AppContext
    from aws_tui.vm.services_protocol import ServiceRegistry

    original = app_context_factory()
    kwargs = {
        name: getattr(original, name)
        for name, parameter in inspect.signature(AppContext).parameters.items()
        if parameter.default is inspect.Parameter.empty
    }
    kwargs["registry"] = ServiceRegistry()
    alternate = None
    try:
        alternate = AppContext(**kwargs)
        await alternate.transfer_history_vm.load()
        assert alternate.transfer_history_vm.records == ()
    finally:
        if alternate is not None:
            await alternate.transfer_history_vm.shutdown()
            alternate.close_unstarted()
        await original.transfer_history_vm.shutdown()
        original.close_unstarted()
