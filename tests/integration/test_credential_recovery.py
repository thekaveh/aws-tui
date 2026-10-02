"""Full-app contracts for durable in-session credential recovery."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from dataclasses import replace

import pytest

from aws_tui.app import AwsTuiApp
from aws_tui.demo.in_memory_fs import InMemoryFS
from aws_tui.domain.filesystem import FileEntry, PathRef, PermissionDeniedError
from aws_tui.infra.aws_session import TokenProbeResult, TokenState
from tests.helpers import wait_until


async def _stream(payload: bytes) -> AsyncIterator[bytes]:
    yield payload


class RecordingRecoveryFS(InMemoryFS):
    def __init__(self) -> None:
        super().__init__()
        self.list_calls: list[PathRef] = []
        self.operation_log: list[str] = []
        self.failure: BaseException | None = None
        self.failure_on_call: int | None = None
        self.block = False
        self.block_on_call: int | None = None
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def list(self, path: PathRef) -> list[FileEntry]:
        self.list_calls.append(path)
        self.operation_log.append("list")
        call_number = len(self.list_calls)
        if self.block or self.block_on_call == call_number:
            self.started.set()
            await self.release.wait()
        if self.failure is not None and (
            self.failure_on_call is None or self.failure_on_call == call_number
        ):
            raise self.failure
        return await super().list(path)


def _dual_projection(app: AwsTuiApp) -> tuple[object, ...]:
    dual = app._dual_pane()
    assert dual is not None
    return (
        dual,
        dual.left.provider,
        dual.left.path,
        dual.left.entries,
        dual.left.state,
        dual.right.provider,
        dual.right.path,
        dual.right.entries,
        dual.right.state,
        app.app_ctx.root_vm.active_connection,
        app.app_ctx.root_vm.active_auth_state,
        app.app_ctx.root_vm.content_host.current_id,
    )


async def _await_local_fallback(app: AwsTuiApp) -> None:
    await wait_until(
        lambda: app._chain_resolved_to_local and app._dual_pane() is not None,
        what="credential failure to mount the local-only S3 fallback",
    )
    await wait_until(
        lambda: (
            (dual := app._dual_pane()) is not None
            and dual.left.state.value != "loading"
            and dual.right.state.value != "loading"
        ),
        what="both local fallback panes to finish their initial listing",
    )


@pytest.mark.asyncio
async def test_expired_source_recovers_in_place_from_the_a_key(app_context_factory) -> None:  # type: ignore[no-untyped-def]
    remote = RecordingRecoveryFS()
    await remote.write_stream(PathRef(("recovered.txt",)), _stream(b"ready"))
    ctx = app_context_factory(fs=remote)
    token_state = TokenState.MISSING
    ctx.aws_session.probe_token = lambda _connection: TokenProbeResult(  # type: ignore[method-assign]
        state=token_state
    )
    app = AwsTuiApp(ctx)

    async with app.run_test(size=(110, 34)) as pilot:
        await _await_local_fallback(app)
        dual = app._dual_pane()
        assert dual is not None
        right_before = (dual.right.provider, dual.right.path, dual.right.entries)
        original = ctx.root_vm.active_connection
        assert original is not None
        assert ctx.root_vm.active_auth_state is TokenState.MISSING
        remote.list_calls.clear()
        remote.operation_log.clear()

        token_state = TokenState.CONNECTED
        await pilot.press("a")
        await wait_until(
            lambda: ctx.root_vm.active_auth_state is TokenState.CONNECTED,
            what="credential recovery to publish connected auth",
        )

        assert ctx.root_vm.active_connection is not None
        assert (
            ctx.root_vm.active_connection.kind,
            ctx.root_vm.active_connection.name,
            ctx.root_vm.active_connection.region,
        ) == (original.kind, original.name, original.region)
        assert ctx.root_vm.content_host.current_id == "s3"
        assert dual.left.current_connection_key == (original.kind, original.name)
        assert dual.left.path == PathRef(())
        assert [entry.entry.name for entry in dual.left.entries] == ["recovered.txt"]
        assert (dual.right.provider, dual.right.path, dual.right.entries) == right_before
        assert remote.list_calls == [PathRef(())]
        assert remote.operation_log == ["list"]
        assert app._chain_resolved_to_local is False


@pytest.mark.asyncio
async def test_denied_recovery_preserves_local_fallback_and_auth(app_context_factory) -> None:  # type: ignore[no-untyped-def]
    remote = RecordingRecoveryFS()
    ctx = app_context_factory(fs=remote)
    token_state = TokenState.MISSING
    ctx.aws_session.probe_token = lambda _connection: TokenProbeResult(  # type: ignore[method-assign]
        state=token_state
    )
    app = AwsTuiApp(ctx)

    async with app.run_test(size=(110, 34)):
        await _await_local_fallback(app)
        before = _dual_projection(app)
        remote.failure = PermissionDeniedError("secret denial detail")
        token_state = TokenState.CONNECTED

        await app.action_authenticate()

        assert _dual_projection(app) == before
        assert ctx.root_vm.active_auth_state is TokenState.MISSING
        assert remote.operation_log == ["list"]
        toast = ctx.root_vm.chrome.toast_stack.toasts[-1].model
        assert toast.id == "credential-recovery-access-denied"
        assert "does not have permission" in toast.text
        assert "secret denial detail" not in toast.text


@pytest.mark.asyncio
async def test_concurrent_retries_coalesce_to_one_provider_read(app_context_factory) -> None:  # type: ignore[no-untyped-def]
    remote = RecordingRecoveryFS()
    await remote.write_stream(PathRef(("ready.txt",)), _stream(b"ready"))
    ctx = app_context_factory(fs=remote)
    token_state = TokenState.MISSING
    ctx.aws_session.probe_token = lambda _connection: TokenProbeResult(  # type: ignore[method-assign]
        state=token_state
    )
    app = AwsTuiApp(ctx)

    async with app.run_test(size=(110, 34)):
        await _await_local_fallback(app)
        remote.block = True
        remote.list_calls.clear()
        remote.operation_log.clear()
        token_state = TokenState.CONNECTED

        first = asyncio.create_task(app.action_authenticate())
        await asyncio.wait_for(remote.started.wait(), timeout=1)
        second = asyncio.create_task(app.action_authenticate())
        await asyncio.sleep(0)
        remote.release.set()
        await asyncio.gather(first, second)

        assert remote.list_calls == [PathRef(())]
        assert remote.operation_log == ["list"]
        assert ctx.root_vm.active_auth_state is TokenState.CONNECTED


@pytest.mark.asyncio
async def test_source_change_away_and_back_discards_held_recovery(app_context_factory) -> None:  # type: ignore[no-untyped-def]
    remote = RecordingRecoveryFS()
    await remote.write_stream(PathRef(("stale.txt",)), _stream(b"stale"))
    ctx = app_context_factory(fs=remote)
    token_state = TokenState.MISSING
    ctx.aws_session.probe_token = lambda _connection: TokenProbeResult(  # type: ignore[method-assign]
        state=token_state
    )
    app = AwsTuiApp(ctx)

    async with app.run_test(size=(110, 34)):
        await _await_local_fallback(app)
        before = _dual_projection(app)
        original = ctx.root_vm.active_connection
        assert original is not None
        other = replace(original, name="other")
        remote.block = True
        token_state = TokenState.CONNECTED
        recovering = asyncio.create_task(app.action_authenticate())
        await asyncio.wait_for(remote.started.wait(), timeout=1)

        ctx.root_vm.refresh_connection_state(other, TokenState.CONNECTED)
        ctx.root_vm.refresh_connection_state(original, TokenState.MISSING)
        remote.release.set()
        await recovering

        assert _dual_projection(app) == before
        assert remote.list_calls == [PathRef(())]


@pytest.mark.asyncio
async def test_same_name_with_different_region_is_rejected_before_read(app_context_factory) -> None:  # type: ignore[no-untyped-def]
    remote = RecordingRecoveryFS()
    ctx = app_context_factory(fs=remote)
    token_state = TokenState.MISSING
    ctx.aws_session.probe_token = lambda _connection: TokenProbeResult(  # type: ignore[method-assign]
        state=token_state
    )
    app = AwsTuiApp(ctx)

    async with app.run_test(size=(110, 34)):
        await _await_local_fallback(app)
        before = _dual_projection(app)
        original = ctx.root_vm.active_connection
        assert original is not None
        ctx.connection_resolver.resolve = lambda _name: replace(  # type: ignore[method-assign]
            original, region="eu-west-1"
        )
        token_state = TokenState.CONNECTED

        await app.action_authenticate()

        assert _dual_projection(app) == before
        assert remote.list_calls == []


@pytest.mark.asyncio
async def test_two_remote_panes_do_not_partially_commit_when_second_read_fails(
    app_context_factory,  # type: ignore[no-untyped-def]
) -> None:
    remote = RecordingRecoveryFS()
    await remote.write_stream(PathRef(("recovered.txt",)), _stream(b"ready"))
    ctx = app_context_factory(fs=remote)
    token_state = TokenState.MISSING
    ctx.aws_session.probe_token = lambda _connection: TokenProbeResult(  # type: ignore[method-assign]
        state=token_state
    )
    app = AwsTuiApp(ctx)

    async with app.run_test(size=(110, 34)):
        await _await_local_fallback(app)
        dual = app._dual_pane()
        connection = ctx.root_vm.active_connection
        assert dual is not None
        assert connection is not None
        live_left = InMemoryFS()
        live_right = InMemoryFS()
        await live_left.write_stream(PathRef(("left-live.txt",)), _stream(b"left"))
        await live_right.write_stream(PathRef(("right-live.txt",)), _stream(b"right"))
        identity = "aws · test · us-east-1"
        key = (connection.kind, connection.name)
        await dual.left.swap_provider(
            live_left,
            identity_label=identity,
            path_protocol="s3:",
            connection_key=key,
        )
        await dual.right.swap_provider(
            live_right,
            identity_label=identity,
            path_protocol="s3:",
            connection_key=key,
        )
        app._chain_resolved_to_local = False
        before = _dual_projection(app)
        remote.list_calls.clear()
        remote.operation_log.clear()
        remote.failure = PermissionDeniedError("second pane denied")
        remote.failure_on_call = 2
        token_state = TokenState.CONNECTED

        await app.action_authenticate()

        assert _dual_projection(app) == before
        assert remote.list_calls == [PathRef(()), PathRef(())]
        assert remote.operation_log == ["list", "list"]


@pytest.mark.asyncio
async def test_cancelling_shared_attempt_after_first_stage_preserves_both_panes(
    app_context_factory,  # type: ignore[no-untyped-def]
) -> None:
    remote = RecordingRecoveryFS()
    await remote.write_stream(PathRef(("recovered.txt",)), _stream(b"ready"))
    ctx = app_context_factory(fs=remote)
    token_state = TokenState.MISSING
    ctx.aws_session.probe_token = lambda _connection: TokenProbeResult(  # type: ignore[method-assign]
        state=token_state
    )
    app = AwsTuiApp(ctx)

    async with app.run_test(size=(110, 34)):
        await _await_local_fallback(app)
        dual = app._dual_pane()
        connection = ctx.root_vm.active_connection
        assert dual is not None
        assert connection is not None
        key = (connection.kind, connection.name)
        for pane, filename in (
            (dual.left, "left-live.txt"),
            (dual.right, "right-live.txt"),
        ):
            live = InMemoryFS()
            await live.write_stream(PathRef((filename,)), _stream(filename.encode()))
            await pane.swap_provider(
                live,
                identity_label="aws · test · us-east-1",
                path_protocol="s3:",
                connection_key=key,
            )
        app._chain_resolved_to_local = False
        before = _dual_projection(app)
        remote.list_calls.clear()
        remote.operation_log.clear()
        remote.block_on_call = 2
        token_state = TokenState.CONNECTED
        waiter = asyncio.create_task(app.action_authenticate())
        await asyncio.wait_for(remote.started.wait(), timeout=1)
        shared = app._auth_recovery_task
        assert shared is not None

        shared.cancel()
        remote.release.set()
        with pytest.raises(asyncio.CancelledError):
            await waiter

        assert _dual_projection(app) == before
        assert remote.list_calls == [PathRef(()), PathRef(())]
        assert remote.operation_log == ["list", "list"]


@pytest.mark.asyncio
async def test_cancelling_one_waiter_does_not_cancel_coalesced_recovery(
    app_context_factory,  # type: ignore[no-untyped-def]
) -> None:
    remote = RecordingRecoveryFS()
    await remote.write_stream(PathRef(("ready.txt",)), _stream(b"ready"))
    ctx = app_context_factory(fs=remote)
    token_state = TokenState.MISSING
    ctx.aws_session.probe_token = lambda _connection: TokenProbeResult(  # type: ignore[method-assign]
        state=token_state
    )
    app = AwsTuiApp(ctx)

    async with app.run_test(size=(110, 34)):
        await _await_local_fallback(app)
        remote.block = True
        remote.list_calls.clear()
        remote.operation_log.clear()
        token_state = TokenState.CONNECTED
        cancelled_waiter = asyncio.create_task(app.action_authenticate())
        await asyncio.wait_for(remote.started.wait(), timeout=1)
        surviving_waiter = asyncio.create_task(app.action_authenticate())

        cancelled_waiter.cancel()
        with pytest.raises(asyncio.CancelledError):
            await cancelled_waiter
        remote.release.set()
        await surviving_waiter

        assert remote.list_calls == [PathRef(())]
        assert ctx.root_vm.active_auth_state is TokenState.CONNECTED


@pytest.mark.asyncio
async def test_successful_retry_preserves_existing_remote_path_and_identity(
    app_context_factory,  # type: ignore[no-untyped-def]
) -> None:
    remote = RecordingRecoveryFS()
    await remote.mkdir(PathRef(("folder",)))
    await remote.write_stream(PathRef(("folder", "item.txt")), _stream(b"item"))
    ctx = app_context_factory(fs=remote)
    ctx.aws_session.probe_token = lambda _connection: TokenProbeResult(  # type: ignore[method-assign]
        state=TokenState.CONNECTED
    )
    app = AwsTuiApp(ctx)

    async with app.run_test(size=(110, 34)):
        await wait_until(
            lambda: (
                (dual := app._dual_pane()) is not None
                and dual.left.state.value not in {"loading", "error"}
            ),
            what="initial remote pane to load",
        )
        dual = app._dual_pane()
        assert dual is not None
        await dual.left.navigate_to(PathRef(("folder",)))
        identity = dual.left.viewmodel.border_subtitle
        remote.list_calls.clear()
        remote.operation_log.clear()

        await app.action_authenticate()

        assert dual.left.path == PathRef(("folder",))
        assert dual.left.viewmodel.border_subtitle == identity
        assert [entry.entry.name for entry in dual.left.entries] == ["..", "item.txt"]
        assert remote.list_calls == [PathRef(("folder",))]
        assert remote.operation_log == ["list"]
