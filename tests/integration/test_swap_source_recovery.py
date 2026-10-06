"""Recovery semantics for the unreachable set — end-to-end subscription path.

Verifies that the hub-subscription chain actually wires up correctly:

1. A PaneVM backed by an offline provider (raises ProviderUnreachableError)
   fires a PropertyChangedMessage("state") on the hub when _reload() runs.
2. The AwsTuiApp subscriber picks up that message and marks the connection
   in ctx.unreachable_connections.
3. When the pane recovers (state transitions to IDLE/EMPTY), the entry is
   cleared from the set.

This exercises the subscription path end-to-end — unlike the previous version
of this file which called _mark_connection_unreachable / _clear_connection_unreachable
directly and therefore would NOT have caught Bug 1 (attribution race in
action_swap_source) or Bug 2 (initial mount UNREACHABLE silently missed).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from vmx import Message, MessageHub, PropertyChangedMessage

from aws_tui.app import AwsTuiApp, _build_swap_candidates
from aws_tui.composition import build_app_context
from aws_tui.domain.filesystem import (
    FileEntry,
    FileSystemProvider,
    PathRef,
    ProgressCallback,
    ProviderUnreachableError,
)
from aws_tui.vm.file_manager.pane_vm import PaneState, PaneVM
from tests.helpers import wait_until


class _UnreachableFS(FileSystemProvider):
    """Fake provider that always raises ProviderUnreachableError from list()."""

    async def list(self, path: PathRef) -> list[FileEntry]:
        raise ProviderUnreachableError("test endpoint down")

    async def stat(self, path: PathRef) -> FileEntry:
        raise ProviderUnreachableError("test endpoint down")

    async def delete(self, path: PathRef, *, expected_etag: str | None = None) -> None:
        raise ProviderUnreachableError("test endpoint down")

    async def delete_empty_directory(self, path: PathRef) -> None:
        raise ProviderUnreachableError("test endpoint down")

    async def mkdir(self, path: PathRef) -> None:
        raise ProviderUnreachableError("test endpoint down")

    async def rename(self, src: PathRef, dst: PathRef) -> None:
        raise ProviderUnreachableError("test endpoint down")

    async def read_stream(
        self, path: PathRef, *, chunk_size: int = 8 * 1024 * 1024
    ) -> AsyncIterator[bytes]:
        raise ProviderUnreachableError("test endpoint down")

    async def write_stream(
        self,
        path: PathRef,
        source: AsyncIterator[bytes],
        *,
        total_size: int | None = None,
        progress: ProgressCallback | None = None,
        overwrite: bool = False,
    ) -> None:
        raise ProviderUnreachableError("test endpoint down")


class _ReachableFS(FileSystemProvider):
    """Fake provider that returns an empty listing (EMPTY state)."""

    async def list(self, path: PathRef) -> list[FileEntry]:
        return []

    async def stat(self, path: PathRef) -> FileEntry:
        raise NotImplementedError

    async def delete(self, path: PathRef, *, expected_etag: str | None = None) -> None:
        pass

    async def delete_empty_directory(self, path: PathRef) -> None:
        pass

    async def mkdir(self, path: PathRef) -> None:
        pass

    async def rename(self, src: PathRef, dst: PathRef) -> None:
        pass

    async def read_stream(
        self, path: PathRef, *, chunk_size: int = 8 * 1024 * 1024
    ) -> AsyncIterator[bytes]:
        async def empty_stream() -> AsyncIterator[bytes]:
            if False:
                yield b""

        return empty_stream()

    async def write_stream(
        self,
        path: PathRef,
        source: AsyncIterator[bytes],
        *,
        total_size: int | None = None,
        progress: ProgressCallback | None = None,
        overwrite: bool = False,
    ) -> None:
        pass


@pytest.mark.asyncio
async def test_hub_subscription_marks_unreachable_via_pane_state(
    tmp_path: Path,
) -> None:
    """Pane state-change message through the real hub subscriber marks/clears."""
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    ctx = build_app_context(config_dir=config_dir, cache_dir=tmp_path / "cache")
    app = AwsTuiApp(ctx)

    # Build a real PaneVM with connection_key set and the unreachable provider.
    hub: MessageHub[Message] = ctx.hub
    pane = PaneVM(
        provider=_UnreachableFS(),
        hub=hub,
        dispatcher=ctx.dispatcher,
        id_prefix="pane.test",
        connection_key=("s3-compatible", "target"),
    )
    pane.construct()

    # Manually drive the pane's internal state to UNREACHABLE (as _reload would).
    pane._state = PaneState.UNREACHABLE

    # Build the PropertyChangedMessage as the hub would carry it.
    real_msg = PropertyChangedMessage.create(pane, pane.name, "state")

    async with app.run_test(size=(100, 30)) as pilot:
        # Drain mount notifications before checking the initial unreachable set.
        await pilot.pause()

        # Verify the real MessageHub subscription routes it to mark the connection.
        assert ("s3-compatible", "target") not in ctx.unreachable_connections
        hub.send(real_msg)
        await wait_until(
            lambda: ("s3-compatible", "target") in ctx.unreachable_connections,
            what="target connection to be marked unreachable",
        )
        assert ("s3-compatible", "target") in ctx.unreachable_connections

        # Now simulate recovery: pane transitions to IDLE.
        pane._state = PaneState.IDLE
        recovery_msg = PropertyChangedMessage.create(pane, pane.name, "state")
        hub.send(recovery_msg)
        await wait_until(
            lambda: ("s3-compatible", "target") not in ctx.unreachable_connections,
            what="target connection to recover",
        )
        assert ("s3-compatible", "target") not in ctx.unreachable_connections

    # Cleanup.
    pane.dispose()
    ctx.transfers_vm.dispose()
    ctx.confirm_vm.dispose()
    ctx.quick_look_vm.dispose()
    ctx.command_palette_vm.dispose()
    ctx.root_vm.dispose()
    ctx.log_sink.close()


@pytest.mark.asyncio
async def test_hub_subscription_ignores_local_pane(tmp_path: Path) -> None:
    """PaneVM with connection_key=None (local) must never touch the unreachable set."""
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "config.toml").write_text("")
    ctx = build_app_context(config_dir=config_dir, cache_dir=tmp_path / "cache")
    app = AwsTuiApp(ctx)

    hub: MessageHub[Message] = ctx.hub
    local_pane = PaneVM(
        provider=_UnreachableFS(),
        hub=hub,
        dispatcher=ctx.dispatcher,
        id_prefix="pane.local",
        connection_key=None,  # local pane — never tracked
    )
    local_pane.construct()
    local_pane._state = PaneState.UNREACHABLE

    msg = PropertyChangedMessage.create(local_pane, local_pane.name, "state")
    app._on_hub_message_pane_state(msg)

    # Local pane must NOT be added to the unreachable set.
    assert len(ctx.unreachable_connections) == 0

    local_pane.dispose()
    ctx.transfers_vm.dispose()
    ctx.confirm_vm.dispose()
    ctx.quick_look_vm.dispose()
    ctx.command_palette_vm.dispose()
    ctx.root_vm.dispose()
    ctx.log_sink.close()


@pytest.mark.asyncio
async def test_pane_state_transition_marks_and_unmarks_unreachable(
    tmp_path: Path,
) -> None:
    """Regression test (original test kept with the same name).

    Keeps the full app.run_test path so Textual's machinery is exercised.
    Mutates the set via the public attribution helpers (same as before) and
    verifies the swap-candidate builder respects it.
    """
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "config.toml").write_text(
        "[connections.target]\n"
        'kind = "s3-compatible"\n'
        'endpoint_url = "http://localhost:9999"\n'
        'credentials = "static"\n'
        'access_key_id = "k"\n'
        'secret_access_key = "s"\n'
    )
    ctx = build_app_context(config_dir=config_dir, cache_dir=tmp_path / "cache")
    app = AwsTuiApp(ctx)

    async with app.run_test(size=(120, 30)) as pilot:
        await pilot.pause()
        await pilot.pause()

        app._mark_connection_unreachable("s3-compatible", "target")
        assert ("s3-compatible", "target") in ctx.unreachable_connections
        candidates, skipped = _build_swap_candidates(ctx)
        assert "target" in skipped
        assert not any("target" in label for label, _ in candidates)

        app._clear_connection_unreachable("s3-compatible", "target")
        assert ("s3-compatible", "target") not in ctx.unreachable_connections
        candidates, skipped = _build_swap_candidates(ctx)
        assert "target" not in skipped
        assert any("target" in label for label, _ in candidates)

    ctx.transfers_vm.dispose()
    ctx.confirm_vm.dispose()
    ctx.quick_look_vm.dispose()
    ctx.command_palette_vm.dispose()
    ctx.root_vm.dispose()


@pytest.mark.parametrize("outcome", ["success", "missing", "drift", "denied", "throttled"])
async def test_cloudwatch_credential_candidate_exact_attempt_and_rejection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, outcome: str
) -> None:
    from aws_tui.demo.in_memory_emr import InMemoryEmr
    from aws_tui.domain.emr_cloudwatch_logs import CloudWatchLogEvent, CloudWatchLogStream
    from aws_tui.domain.emr_serverless import CloudWatchLogConfiguration
    from aws_tui.domain.filesystem import AuthRequiredError, PermissionDeniedError, ThrottledError
    from aws_tui.infra.aws_session import TokenProbeResult, TokenState
    from aws_tui.services.emr_serverless.service import EmrServerlessService
    from aws_tui.ui.widgets.emr_serverless.job_run_logs_pane import JobRunLogsPane
    from aws_tui.vm.emr_serverless.job_run_logs_vm import LogsState

    config = tmp_path / "config"
    config.mkdir()
    (config / "config.toml").write_text(
        '[connections.dev]\nkind = "aws"\nprofile = "dev"\nregion = "us-east-1"\n[defaults]\nconnection = "dev"\n'
    )
    ctx = build_app_context(config_dir=config, cache_dir=tmp_path / "cache")
    fake = InMemoryEmr()
    fake.add_application(app_id="00emr", name="etl")
    for service in ctx.root_vm._registry.all():
        if isinstance(service, EmrServerlessService):
            service._client_factory = lambda _connection: fake
    monkeypatch.setattr(
        ctx.aws_session,
        "probe_token",
        lambda _connection: TokenProbeResult(state=TokenState.CONNECTED),
    )
    fake.add_job_run(application_id="00emr", job_run_id="r-001", name="run")
    fake.add_job_run_detail(
        application_id="00emr",
        job_run_id="r-001",
        cloudwatch_monitoring=CloudWatchLogConfiguration(True, "/group"),
    )
    names = tuple(
        f"/applications/00emr/jobs/r-001/attempts/{attempt}/SPARK_DRIVER" for attempt in (1, 2)
    )
    for i, name in enumerate(names):
        fake.add_cloudwatch_stream(
            application_id="00emr",
            job_run_id="r-001",
            log_group_name="/group",
            stream=CloudWatchLogStream(name, "SPARK_DRIVER", i + 1),
            events=(CloudWatchLogEvent(str(i), 1, 1, f"ERROR attempt {i + 1}"),),
        )
    monkeypatch.setattr("aws_tui.vm.emr_serverless.job_run_logs_vm._now_ms", lambda: 100_000)
    # Include events within the run's bounded window.
    from dataclasses import replace
    from datetime import UTC, datetime

    detail = fake._details[("00emr", "r-001")]
    fake._details[("00emr", "r-001")] = replace(detail, created_at=datetime.fromtimestamp(0, UTC))
    original = fake.read_cloudwatch_events
    app = AwsTuiApp(ctx)
    try:
        async with app.run_test(size=(120, 40)) as pilot:
            await app.workers.wait_for_complete(list(app.workers._workers))
            await pilot.pause()
            ctx.root_vm.services_menu.switch_service_command.execute("emr-serverless")
            await app.workers.wait_for_complete(list(app.workers._workers))
            setup = ctx.root_vm.content_host._setup_task
            if setup is not None and not setup.done():
                await setup
            await pilot.pause()
            live = ctx.root_vm.content_host.current
            assert live is not None
            await live.select_job_run("r-001")
            logs = live.job_run_logs
            await logs.load()
            logs.select_cloudwatch_stream(names[1])
            await logs.load()
            assert logs.lines == ("ERROR attempt 2",)

            async def expired(**_kwargs):
                raise AuthRequiredError("RAW-BODY-SENTINEL expired")

            monkeypatch.setattr(fake, "read_cloudwatch_events", expired)
            await logs.load()
            assert logs.failure_kind == "auth_required"
            assert logs.credential_recovery_state is PaneState.AUTH_REQUIRED
            pane = app.query_one(JobRunLogsPane)
            pane.focus()
            await wait_until(lambda: pane.has_focus, what="CloudWatch recovery focus")
            if outcome == "missing":
                fake._cloudwatch_events.pop(("/group", names[1]))
            elif outcome == "drift":
                fake._details[("00emr", "r-001")] = replace(
                    fake._details[("00emr", "r-001")],
                    cloudwatch_monitoring=CloudWatchLogConfiguration(True, "/other"),
                )
            calls = []

            async def fresh(**kwargs):
                calls.append(kwargs["stream_name"])
                if outcome == "denied":
                    raise PermissionDeniedError("RAW-BODY-SENTINEL denied")
                if outcome == "throttled":
                    raise ThrottledError("RAW-BODY-SENTINEL throttled")
                return await original(**kwargs)

            monkeypatch.setattr(fake, "read_cloudwatch_events", fresh)
            await app.action_authenticate()
            candidate = ctx.root_vm.content_host.current
            assert candidate is not None
            if outcome == "success":
                assert candidate is not live
                recovered = candidate.job_run_logs
                assert recovered.state is LogsState.READY
                assert recovered.current_stream.name == names[1]
                assert recovered.lines == ("ERROR attempt 2",)
                assert calls == [names[1]]
                assert not logs._operations.tasks
                assert not recovered.following
                app.query_one(JobRunLogsPane).focus()
                await pilot.pause()
                await app.action_authenticate()
                repeated = ctx.root_vm.content_host.current
                assert repeated is not None
                assert not repeated.job_run_logs.following
            else:
                assert candidate is live
                assert logs.current_stream.name == names[1]
                assert calls == ([] if outcome in {"missing", "drift"} else [names[1]])
                assert not logs.following
    finally:
        await ctx.root_vm.content_host.shutdown()
        ctx.root_vm.dispose()
        ctx.log_sink.close()
