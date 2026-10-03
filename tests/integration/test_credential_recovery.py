"""Full-app contracts for durable in-session credential recovery."""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator
from dataclasses import replace
from pathlib import Path

import pytest
from textual.containers import Container

from aws_tui.app import AwsTuiApp
from aws_tui.composition import AppContext, build_app_context
from aws_tui.demo.in_memory_fs import InMemoryFS
from aws_tui.demo.in_memory_glue import InMemoryGlue
from aws_tui.domain.data_catalog import TableFormat
from aws_tui.domain.emr_logs import FilterMode, LogFileKind, LogFilter
from aws_tui.domain.filesystem import (
    AuthRequiredError,
    FileEntry,
    PathRef,
    PermissionDeniedError,
)
from aws_tui.infra.aws_session import TokenProbeResult, TokenState
from aws_tui.services.athena import AthenaService
from aws_tui.services.glue import GlueService
from aws_tui.ui.widgets.athena.page import AthenaPage
from aws_tui.ui.widgets.emr_serverless.job_run_detail_pane import JobRunDetailPane
from aws_tui.ui.widgets.emr_serverless.job_run_logs_pane import JobRunLogsPane
from aws_tui.vm.athena.page_vm import AthenaPageVM
from aws_tui.vm.file_manager.pane_vm import PaneState
from aws_tui.vm.glue.page_vm import GluePageVM
from tests.helpers import wait_until
from tests.integration.test_emr_page import (
    _AWS_TOML,
    _make_ctx_with_emr_fake,
    _prep,
)
from tests.integration.test_glue_page import open_service
from tests.unit.vm.athena.test_page_vm import PageClient
from tests.unit.vm.glue._fake_glue import seeded_glue
from tests.unit.vm.glue.test_iceberg_vm import RecordingInspector
from tests.unit.vm.glue.test_page_vm import make_page_vm


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


def _connected_probe(_connection: object) -> TokenProbeResult:
    return TokenProbeResult(state=TokenState.CONNECTED)


def _mount_athena_for_recovery(
    tmp_path: Path,
) -> tuple[AwsTuiApp, AppContext, AthenaService, PageClient]:
    ctx = build_app_context(
        config_dir=tmp_path / "config",
        cache_dir=tmp_path / "cache",
        demo=True,
    )
    ctx.aws_session.probe_token = _connected_probe  # type: ignore[method-assign]
    service = ctx.registry.get("athena")
    assert isinstance(service, AthenaService)
    initial = PageClient(connection_name="demo-dev", region="us-east-1")
    service._client_factory = lambda _connection: initial  # type: ignore[attr-defined]
    return AwsTuiApp(ctx), ctx, service, initial


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


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("profile", "different-account"),
        ("endpoint_url", "https://different-endpoint.example.test"),
    ],
)
@pytest.mark.asyncio
async def test_same_name_and_region_with_changed_binding_is_rejected_before_read(
    app_context_factory,  # type: ignore[no-untyped-def]
    field: str,
    value: str,
) -> None:
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
        changed = replace(original, **{field: value})
        ctx.connection_resolver.resolve = lambda _name: changed  # type: ignore[method-assign]
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


@pytest.mark.asyncio
async def test_athena_recovery_stages_failure_without_mutating_live_page_or_logging_secret(
    tmp_path: Path,
) -> None:
    app, ctx, service, _initial = _mount_athena_for_recovery(tmp_path)
    try:
        async with app.run_test(size=(120, 40)) as pilot:
            await open_service(ctx, pilot, "athena")
            live_vm = ctx.root_vm.content_host.current
            live_page = app.query_one("#content-athena-page", AthenaPage)
            assert isinstance(live_vm, AthenaPageVM)
            before = (
                live_vm.active_view,
                live_vm.context,
                live_vm.workgroups,
                ctx.root_vm.active_connection,
                ctx.root_vm.active_auth_state,
            )
            candidate_client = PageClient(connection_name="demo-dev", region="us-east-1")
            candidate_client.workgroup_error = RuntimeError("credential-recovery-secret")  # type: ignore[assignment]
            service._client_factory = lambda _connection: candidate_client  # type: ignore[attr-defined]

            await app.action_authenticate()

            assert ctx.root_vm.content_host.current is live_vm
            assert app.query_one("#content-athena-page", AthenaPage) is live_page
            assert (
                live_vm.active_view,
                live_vm.context,
                live_vm.workgroups,
                ctx.root_vm.active_connection,
                ctx.root_vm.active_auth_state,
            ) == before
            toast = ctx.root_vm.chrome.toast_stack.toasts[-1].model
            assert toast.id == "credential-recovery-other"
            assert "credential-recovery-secret" not in toast.text
            ctx.log_sink.flush()
            log_text = ctx.log_sink.path.read_text(encoding="utf-8")
            assert "credential-recovery-secret" not in log_text
            assert "credential_recovery.candidate_failure" in log_text
            assert '"error_type":"RuntimeError"' in log_text
    finally:
        with contextlib.suppress(Exception):
            await ctx.root_vm.content_host.shutdown()
        with contextlib.suppress(Exception):
            ctx.root_vm.dispose()
        ctx.log_sink.close()


@pytest.mark.asyncio
async def test_athena_recovery_cancellation_preserves_live_page(tmp_path: Path) -> None:
    app, ctx, service, _initial = _mount_athena_for_recovery(tmp_path)
    try:
        async with app.run_test(size=(120, 40)) as pilot:
            await open_service(ctx, pilot, "athena")
            live_vm = ctx.root_vm.content_host.current
            live_page = app.query_one("#content-athena-page", AthenaPage)
            candidate_client = PageClient(connection_name="demo-dev", region="us-east-1")
            candidate_client.block_workgroup_detail_for = "primary"
            service._client_factory = lambda _connection: candidate_client  # type: ignore[attr-defined]

            recovery = asyncio.create_task(app._recover_active_source())
            await asyncio.wait_for(candidate_client.workgroup_detail_started.wait(), timeout=1)
            recovery.cancel()
            with pytest.raises(asyncio.CancelledError):
                await recovery

            assert ctx.root_vm.content_host.current is live_vm
            assert app.query_one("#content-athena-page", AthenaPage) is live_page
    finally:
        with contextlib.suppress(Exception):
            await ctx.root_vm.content_host.shutdown()
        with contextlib.suppress(Exception):
            ctx.root_vm.dispose()
        ctx.log_sink.close()


@pytest.mark.asyncio
async def test_athena_recovery_atomically_replaces_hosted_vm_after_success(tmp_path: Path) -> None:
    app, ctx, service, _initial = _mount_athena_for_recovery(tmp_path)
    try:
        async with app.run_test(size=(120, 40)) as pilot:
            await open_service(ctx, pilot, "athena")
            live_vm = ctx.root_vm.content_host.current
            live_page = app.query_one("#content-athena-page", AthenaPage)
            original = ctx.root_vm.active_connection
            assert isinstance(live_vm, AthenaPageVM)
            assert original is not None
            refreshed = replace(original, session_token="rotated-session-token")
            ctx.connection_resolver.resolve = lambda _name: refreshed  # type: ignore[method-assign]
            candidate_client = PageClient(connection_name="demo-dev", region="us-east-1")
            service._client_factory = lambda _connection: candidate_client  # type: ignore[attr-defined]

            await app.action_authenticate()

            recovered_vm = ctx.root_vm.content_host.current
            assert isinstance(recovered_vm, AthenaPageVM)
            assert recovered_vm is not live_vm
            assert recovered_vm.connection is refreshed
            assert ctx.root_vm.active_connection is refreshed
            assert ctx.root_vm.active_auth_state is TokenState.CONNECTED
            assert app.query_one("#content-athena-page", AthenaPage) is not live_page
            assert recovered_vm.workgroups
    finally:
        with contextlib.suppress(Exception):
            await ctx.root_vm.content_host.shutdown()
        with contextlib.suppress(Exception):
            ctx.root_vm.dispose()
        ctx.log_sink.close()


@pytest.mark.asyncio
async def test_athena_initial_auth_failure_recovers_without_full_snapshot(tmp_path: Path) -> None:
    app, ctx, service, initial = _mount_athena_for_recovery(tmp_path)
    initial.workgroup_error = AuthRequiredError("expired credentials")
    try:
        async with app.run_test(size=(120, 40)) as pilot:
            await open_service(ctx, pilot, "athena")
            live = ctx.root_vm.content_host.current
            assert isinstance(live, AthenaPageVM)
            assert live.workgroups_state is PaneState.AUTH_REQUIRED
            live.query.set_sql("SELECT * FROM retained_draft")
            service._client_factory = lambda _connection: PageClient(  # type: ignore[attr-defined]
                connection_name="demo-dev",
                region="us-east-1",
            )

            await app.action_authenticate()

            recovered = ctx.root_vm.content_host.current
            assert isinstance(recovered, AthenaPageVM)
            assert recovered is not live
            assert recovered.workgroups_state is PaneState.IDLE
            assert recovered.query.sql == "SELECT * FROM retained_draft"
    finally:
        with contextlib.suppress(Exception):
            await ctx.root_vm.content_host.shutdown()
        with contextlib.suppress(Exception):
            ctx.root_vm.dispose()
        ctx.log_sink.close()


@pytest.mark.asyncio
async def test_athena_results_recovery_preserves_execution_and_unsaved_query(
    tmp_path: Path,
) -> None:
    app, ctx, service, _initial = _mount_athena_for_recovery(tmp_path)
    try:
        async with app.run_test(size=(120, 40)) as pilot:
            await open_service(ctx, pilot, "athena")
            live_vm = ctx.root_vm.content_host.current
            assert isinstance(live_vm, AthenaPageVM)
            live_vm.query.set_sql("SELECT * FROM unsaved_work")
            await live_vm.results.load("q-results", from_history=True)
            await live_vm.select_view("results")
            candidate_client = PageClient(connection_name="demo-dev", region="us-east-1")
            service._client_factory = lambda _connection: candidate_client  # type: ignore[attr-defined]

            await app.action_authenticate()

            recovered = ctx.root_vm.content_host.current
            assert isinstance(recovered, AthenaPageVM)
            assert recovered is not live_vm
            assert recovered.active_view == "results"
            assert recovered.results.execution_id == "q-results"
            assert recovered.results.rows == (("1",),)
            assert recovered.query.sql == "SELECT * FROM unsaved_work"
            assert candidate_client.start_calls == []
            assert candidate_client.stop_calls == []
    finally:
        with contextlib.suppress(Exception):
            await ctx.root_vm.content_host.shutdown()
        with contextlib.suppress(Exception):
            ctx.root_vm.dispose()
        ctx.log_sink.close()


@pytest.mark.asyncio
async def test_athena_recovery_mount_failure_preserves_live_vm_widget_and_auth(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app, ctx, service, _initial = _mount_athena_for_recovery(tmp_path)
    try:
        async with app.run_test(size=(120, 40)) as pilot:
            await open_service(ctx, pilot, "athena")
            live_vm = ctx.root_vm.content_host.current
            live_page = app.query_one("#content-athena-page", AthenaPage)
            original = ctx.root_vm.active_connection
            assert original is not None
            ctx.root_vm.refresh_connection_state(original, TokenState.EXPIRED)
            candidate_client = PageClient(connection_name="demo-dev", region="us-east-1")
            service._client_factory = lambda _connection: candidate_client  # type: ignore[attr-defined]
            host = app.query_one("#content-host", Container)

            async def fail_mount(*_widgets: object, **_kwargs: object) -> None:
                raise RuntimeError("forced candidate mount failure")

            monkeypatch.setattr(host, "mount", fail_mount)

            await app.action_authenticate()

            assert ctx.root_vm.content_host.current is live_vm
            assert ctx.root_vm.active_connection is original
            assert ctx.root_vm.active_auth_state is TokenState.EXPIRED
            assert app.query_one("#content-athena-page", AthenaPage) is live_page
            assert live_page.display
    finally:
        with contextlib.suppress(Exception):
            await ctx.root_vm.content_host.shutdown()
        with contextlib.suppress(Exception):
            ctx.root_vm.dispose()
        ctx.log_sink.close()


@pytest.mark.asyncio
async def test_glue_jobs_recovery_preserves_job_run_and_filter(tmp_path: Path) -> None:
    ctx = build_app_context(
        config_dir=tmp_path / "config",
        cache_dir=tmp_path / "cache",
        demo=True,
    )
    ctx.aws_session.probe_token = _connected_probe  # type: ignore[method-assign]
    app = AwsTuiApp(ctx)
    try:
        async with app.run_test(size=(120, 40)) as pilot:
            await open_service(ctx, pilot, "glue")
            live = ctx.root_vm.content_host.current
            assert isinstance(live, GluePageVM)
            await live.select_view("jobs")
            await live.set_job_run_states(frozenset({"RUNNING", "SUCCEEDED"}))
            await live.select_job("dev_daily_etl")
            live.select_job_run("jr-dev-succeeded")

            await app.action_authenticate()

            recovered = ctx.root_vm.content_host.current
            assert isinstance(recovered, GluePageVM)
            assert recovered is not live
            assert recovered.active_view == "jobs"
            assert recovered.jobs.run_state_filter == frozenset({"RUNNING", "SUCCEEDED"})
            assert recovered.jobs.selected_job_name == "dev_daily_etl"
            assert recovered.jobs.selected_run_id == "jr-dev-succeeded"
    finally:
        with contextlib.suppress(Exception):
            await ctx.root_vm.content_host.shutdown()
        with contextlib.suppress(Exception):
            ctx.root_vm.dispose()
        ctx.log_sink.close()


@pytest.mark.parametrize("missing_target", ["job", "run", "crawler"])
@pytest.mark.asyncio
async def test_glue_recovery_rejects_missing_exact_selection(
    tmp_path: Path,
    missing_target: str,
) -> None:
    ctx = build_app_context(
        config_dir=tmp_path / "config",
        cache_dir=tmp_path / "cache",
        demo=True,
    )
    ctx.aws_session.probe_token = _connected_probe  # type: ignore[method-assign]
    app = AwsTuiApp(ctx)
    try:
        async with app.run_test(size=(120, 40)) as pilot:
            await open_service(ctx, pilot, "glue")
            live = ctx.root_vm.content_host.current
            connection = ctx.root_vm.active_connection
            service = ctx.registry.get("glue")
            assert isinstance(live, GluePageVM)
            assert connection is not None
            assert isinstance(service, GlueService)
            assert service._client_factory is not None  # type: ignore[attr-defined]
            fake = service._client_factory(connection)  # type: ignore[attr-defined]
            assert isinstance(fake, InMemoryGlue)
            if missing_target in {"job", "run"}:
                await live.select_view("jobs")
                await live.select_job("dev_daily_etl")
                live.select_job_run("jr-dev-succeeded")
                if missing_target == "job":
                    fake.jobs.clear()
                    fake.runs.clear()
                else:
                    fake.runs["dev_daily_etl"] = [
                        row
                        for row in fake.runs["dev_daily_etl"]
                        if row.run_id != "jr-dev-succeeded"
                    ]
            else:
                await live.select_view("crawlers")
                await live.select_crawler("dev-running")
                fake.crawlers = [row for row in fake.crawlers if row.name != "dev-running"]
                fake.crawler_details.pop("dev-running")

            await app.action_authenticate()

            assert ctx.root_vm.content_host.current is live
    finally:
        with contextlib.suppress(Exception):
            await ctx.root_vm.content_host.shutdown()
        with contextlib.suppress(Exception):
            ctx.root_vm.dispose()
        ctx.log_sink.close()


@pytest.mark.parametrize("missing_target", ["view", "snapshot"])
@pytest.mark.asyncio
async def test_glue_recovery_rejects_missing_exact_iceberg_state(
    missing_target: str,
) -> None:
    live_fake = seeded_glue()
    live_ref = live_fake.tables["analytics"][0].ref
    live_fake.table_details[live_ref] = replace(
        live_fake.table_details[live_ref],
        table_format=TableFormat.ICEBERG,
    )
    live_inspector = RecordingInspector()
    live = make_page_vm(live_fake, iceberg_inspector=live_inspector)
    candidate_fake = seeded_glue()
    candidate_ref = candidate_fake.tables["analytics"][0].ref
    candidate_fake.table_details[candidate_ref] = replace(
        candidate_fake.table_details[candidate_ref],
        table_format=TableFormat.ICEBERG,
    )
    candidate_inspector = RecordingInspector()
    candidate = make_page_vm(candidate_fake, iceberg_inspector=candidate_inspector)
    try:
        await live.setup()
        await live.select_table("events")
        assert await live.catalog.iceberg.select_view("snapshots")
        assert live.catalog.iceberg.select_snapshot(43)
        snapshot = AwsTuiApp._capture_glue_page_snapshot(live)
        if missing_target == "view":
            candidate_inspector.errors["snapshots"] = PermissionDeniedError("denied")
        else:
            candidate_inspector.snapshots = tuple(
                row for row in candidate_inspector.snapshots if row.snapshot_id != 43
            )
        await candidate.setup()

        with pytest.raises(ValueError, match="Iceberg"):
            await AwsTuiApp._restore_glue_page_snapshot(candidate, snapshot)
    finally:
        live.dispose()
        candidate.dispose()


@pytest.mark.asyncio
async def test_emr_detail_recovery_preserves_non_first_selected_run(tmp_path: Path) -> None:
    config_dir = _prep(tmp_path, _AWS_TOML)
    ctx, fake = _make_ctx_with_emr_fake(config_dir, tmp_path / "cache")
    ctx.aws_session.probe_token = _connected_probe  # type: ignore[method-assign]
    fake.add_job_run(application_id="00emr", job_run_id="r-001", name="older")
    fake.add_job_run_detail(application_id="00emr", job_run_id="r-001")
    fake.add_job_run(application_id="00emr", job_run_id="r-002", name="newer")
    fake.add_job_run_detail(application_id="00emr", job_run_id="r-002")
    app = AwsTuiApp(ctx)
    try:
        async with app.run_test(size=(120, 40)) as pilot:
            await open_service(ctx, pilot, "emr-serverless")
            live = ctx.root_vm.content_host.current
            await live.select_job_run("r-001")
            detail = app.query_one(JobRunDetailPane)
            detail.focus()
            await wait_until(
                lambda: detail.has_focus or detail.has_focus_within,
                what="EMR detail pane to receive focus",
            )

            await app.action_authenticate()

            recovered = ctx.root_vm.content_host.current
            assert recovered is not live
            assert recovered.job_runs.selected_id == "r-001"
            assert recovered.job_run_detail.detail is not None
            assert recovered.job_run_detail.detail.job_run_id == "r-001"
    finally:
        with contextlib.suppress(Exception):
            await ctx.root_vm.content_host.shutdown()
        with contextlib.suppress(Exception):
            ctx.root_vm.dispose()
        ctx.log_sink.close()


@pytest.mark.asyncio
async def test_emr_logs_recovery_preserves_run_file_and_filter(tmp_path: Path) -> None:
    config_dir = _prep(tmp_path, _AWS_TOML)
    ctx, fake = _make_ctx_with_emr_fake(config_dir, tmp_path / "cache")
    ctx.aws_session.probe_token = _connected_probe  # type: ignore[method-assign]
    fake.add_job_run(application_id="00emr", job_run_id="r-001", name="older")
    fake.add_job_run_detail(
        application_id="00emr",
        job_run_id="r-001",
        s3_monitoring_log_uri="s3://bucket/logs",
    )
    fake.add_job_run(application_id="00emr", job_run_id="r-002", name="newer")
    fake.add_job_run_detail(application_id="00emr", job_run_id="r-002")
    stdout = fake.add_log_file(
        application_id="00emr",
        job_run_id="r-001",
        kind=LogFileKind.DRIVER_STDOUT,
        lines=("INFO retained",),
    )
    fake.add_log_file(
        application_id="00emr",
        job_run_id="r-001",
        kind=LogFileKind.DRIVER_STDERR,
        lines=("ERROR default",),
    )
    app = AwsTuiApp(ctx)
    try:
        async with app.run_test(size=(120, 40)) as pilot:
            await open_service(ctx, pilot, "emr-serverless")
            live = ctx.root_vm.content_host.current
            await live.select_job_run("r-001")
            filter_ = LogFilter(patterns=(), mode=FilterMode.PASSTHROUGH)
            live.job_run_logs.set_filter(filter_)
            await live.job_run_logs.load(use_cache=False)
            live.job_run_logs.select_log_file_key(stdout.key)
            await live.job_run_logs.load(use_cache=False)
            logs = app.query_one(JobRunLogsPane)
            logs.focus()
            await wait_until(
                lambda: logs.has_focus or logs.has_focus_within,
                what="EMR logs pane to receive focus",
            )

            await app.action_authenticate()

            recovered = ctx.root_vm.content_host.current
            assert recovered is not live
            assert recovered.job_runs.selected_id == "r-001"
            assert recovered.job_run_logs.job_run_id == "r-001"
            assert recovered.job_run_logs.current_file is not None
            assert recovered.job_run_logs.current_file.key == stdout.key
            assert recovered.job_run_logs.filter == filter_
            assert recovered.job_run_logs.lines == ("INFO retained",)
    finally:
        with contextlib.suppress(Exception):
            await ctx.root_vm.content_host.shutdown()
        with contextlib.suppress(Exception):
            ctx.root_vm.dispose()
        ctx.log_sink.close()


@pytest.mark.asyncio
async def test_emr_logs_recovery_rejects_missing_exact_file(tmp_path: Path) -> None:
    config_dir = _prep(tmp_path, _AWS_TOML)
    ctx, fake = _make_ctx_with_emr_fake(config_dir, tmp_path / "cache")
    ctx.aws_session.probe_token = _connected_probe  # type: ignore[method-assign]
    fake.add_job_run(application_id="00emr", job_run_id="r-001", name="run")
    fake.add_job_run_detail(
        application_id="00emr",
        job_run_id="r-001",
        s3_monitoring_log_uri="s3://bucket/logs",
    )
    stdout = fake.add_log_file(
        application_id="00emr",
        job_run_id="r-001",
        kind=LogFileKind.DRIVER_STDOUT,
        lines=("INFO retained",),
    )
    fake.add_log_file(
        application_id="00emr",
        job_run_id="r-001",
        kind=LogFileKind.DRIVER_STDERR,
        lines=("ERROR fallback",),
    )
    app = AwsTuiApp(ctx)
    try:
        async with app.run_test(size=(120, 40)) as pilot:
            await open_service(ctx, pilot, "emr-serverless")
            live = ctx.root_vm.content_host.current
            await live.select_job_run("r-001")
            await live.job_run_logs.load(use_cache=False)
            live.job_run_logs.select_log_file_key(stdout.key)
            await live.job_run_logs.load(use_cache=False)
            app.query_one(JobRunLogsPane).focus()
            fake._log_files.pop(stdout.key)  # type: ignore[attr-defined]

            await app.action_authenticate()

            assert ctx.root_vm.content_host.current is live
            assert live.job_run_logs.current_file is not None
            assert live.job_run_logs.current_file.key == stdout.key
    finally:
        with contextlib.suppress(Exception):
            await ctx.root_vm.content_host.shutdown()
        with contextlib.suppress(Exception):
            ctx.root_vm.dispose()
        ctx.log_sink.close()
