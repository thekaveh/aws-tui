"""Single-context AWS service source switching via ``Shift+S``."""

from __future__ import annotations

import asyncio
import contextlib
from pathlib import Path
from typing import cast

import pytest
from textual.widgets import OptionList, Static

from aws_tui.app import (
    AwsTuiApp,
    _build_swap_candidates,
    _next_service_source,
    _service_source_candidates,
)
from aws_tui.composition import AppContext, build_app_context
from aws_tui.demo.in_memory_emr import InMemoryEmr as _InMemoryEmr
from aws_tui.infra.aws_session import TokenProbeResult, TokenState
from aws_tui.infra.config_store import ConfigError
from aws_tui.infra.connection_resolver import Connection
from aws_tui.services.emr_serverless.service import EmrServerlessService
from aws_tui.services.glue import GlueClientProtocol, GlueService
from aws_tui.ui.widgets.context_picker import ContextPicker
from aws_tui.ui.widgets.emr_serverless.page import EmrServerlessPage
from aws_tui.ui.widgets.glue.page import GluePage
from tests.unit.vm.glue._fake_glue import seeded_glue


def _three_source_config(tmp_path: Path) -> Path:
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "config.toml").write_text(
        "[connections.prod-west]\n"
        'kind = "aws"\n'
        'profile = "prod"\n'
        'region = "us-west-2"\n'
        "\n"
        "[connections.minio]\n"
        'kind = "s3-compatible"\n'
        'endpoint_url = "http://127.0.0.1:9000"\n'
        'access_key_id = "x"\n'
        'secret_access_key = "y"\n'
        "\n"
        "[connections.dev]\n"
        'kind = "aws"\n'
        'profile = "dev"\n'
        'region = "us-east-1"\n'
        "\n"
        "[defaults]\n"
        'connection = "dev"\n'
    )
    return config_dir


def _aws_connections() -> tuple[Connection, Connection]:
    return (
        Connection(
            name="dev",
            kind="aws",
            profile="dev",
            region="us-east-1",
            source="config",
        ),
        Connection(
            name="prod-west",
            kind="aws",
            profile="prod",
            region="us-west-2",
            source="config",
        ),
    )


def _configure_auto_profiles(ctx: AppContext, tmp_path: Path) -> None:
    aws_config = tmp_path / "aws-config"
    aws_config.write_text(
        "[profile zulu]\nregion = eu-west-1\n\n[profile alpha]\nregion = ap-southeast-1\n"
    )
    ctx.connection_resolver._aws_config_path = aws_config
    ctx.connection_resolver._aws_credentials_path = tmp_path / "no-aws-credentials"


def test_service_candidates_include_only_supported_aws_connections(tmp_path: Path) -> None:
    ctx = build_app_context(
        config_dir=_three_source_config(tmp_path),
        cache_dir=tmp_path / "cache",
    )
    _configure_auto_profiles(ctx, tmp_path)
    candidates = _service_source_candidates(ctx, "emr-serverless")
    assert [(connection.name, connection.region) for connection in candidates] == [
        ("prod-west", "us-west-2"),
        ("dev", "us-east-1"),
        ("zulu", "eu-west-1"),
        ("alpha", "ap-southeast-1"),
    ]


@pytest.mark.parametrize("service_id", ["emr-serverless", "glue", "athena"])
def test_s3_unreachable_mark_does_not_suppress_other_service_candidates(
    tmp_path: Path,
    service_id: str,
) -> None:
    ctx = build_app_context(
        config_dir=_three_source_config(tmp_path),
        cache_dir=tmp_path / "cache",
    )
    ctx.unreachable_connections.add(("aws", "prod-west"))

    candidates = _service_source_candidates(ctx, service_id)

    assert any(connection.name == "prod-west" for connection in candidates)


def test_next_service_source_wraps_by_connection_name_and_region() -> None:
    dev, prod = _aws_connections()
    assert _next_service_source((dev, prod), dev) == prod
    assert _next_service_source((dev, prod), prod) == dev


def test_live_connection_discovery_failure_is_nonfatal_and_reported(tmp_path: Path) -> None:
    ctx = build_app_context(demo=True, cache_dir=tmp_path / "cache")

    def fail_discovery() -> list[Connection]:
        raise ConfigError("config.toml is not valid TOML")

    ctx.connection_resolver.list = fail_discovery  # type: ignore[method-assign]
    try:
        assert _service_source_candidates(ctx, "glue") == ()
        candidates, skipped = _build_swap_candidates(ctx)

        assert candidates == [("local", "local")]
        assert skipped == []
        matching = [
            toast
            for toast in ctx.root_vm.chrome.toast_stack.toasts
            if toast.model.id == "connection-discovery-failed"
        ]
        assert len(matching) == 1
        assert "could not reload connections" in matching[0].model.text
    finally:
        ctx.root_vm.dispose()


def _multi_profile_emr_context(
    tmp_path: Path,
) -> tuple[AppContext, list[str]]:
    ctx = build_app_context(demo=True, cache_dir=tmp_path / "cache")
    dev, prod = _aws_connections()
    ctx.connection_resolver.list = lambda: [prod, dev]  # type: ignore[method-assign]
    calls: list[str] = []

    def build_client(connection: Connection) -> _InMemoryEmr:
        calls.append(connection.name)
        fake = _InMemoryEmr()
        fake.add_application(app_id=f"{connection.name}-app", name=connection.name)
        return fake

    for service in ctx.registry.all():
        if isinstance(service, EmrServerlessService):
            service._client_factory = build_client
    return ctx, calls


def _multi_profile_glue_context(
    tmp_path: Path,
) -> tuple[AppContext, list[str]]:
    ctx = build_app_context(demo=True, cache_dir=tmp_path / "cache")
    dev, prod = _aws_connections()
    ctx.connection_resolver.list = lambda: [prod, dev]  # type: ignore[method-assign]
    calls: list[str] = []

    def build_client(connection: Connection) -> GlueClientProtocol:
        calls.append(connection.name)
        return cast(GlueClientProtocol, seeded_glue())

    service = ctx.registry.get("glue")
    assert isinstance(service, GlueService)
    service._client_factory = build_client
    return ctx, calls


async def _await_service_mount(pilot: object, app: AwsTuiApp) -> None:
    await app.workers.wait_for_complete(list(app.workers._workers))
    setup_task = app.app_ctx.root_vm.content_host._setup_task
    if setup_task is not None and not setup_task.done():
        await setup_task
    await pilot.pause()  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_shift_s_rebuilds_emr_under_next_profile(tmp_path: Path) -> None:
    ctx, calls = _multi_profile_emr_context(tmp_path)
    app = AwsTuiApp(ctx)
    try:
        async with app.run_test() as pilot:
            await app.workers.wait_for_complete(list(app.workers._workers))
            await pilot.pause()
            ctx.root_vm.services_menu.switch_service_command.execute("emr-serverless")
            await _await_service_mount(pilot, app)
            before = ctx.root_vm.content_host.current
            selected: list[tuple[str, str, str]] = []
            rebuild = app._rebuild_single_context_source

            async def recording_rebuild(
                service_id: str,
                target: Connection,
            ) -> None:
                selected.append((service_id, target.name, target.region))
                await rebuild(service_id, target)

            app._rebuild_single_context_source = recording_rebuild  # type: ignore[method-assign]

            await app.action_swap_source()
            await _await_service_mount(pilot, app)

            after = ctx.root_vm.content_host.current
            assert before is not after
            assert after is not None
            assert ctx.root_vm.active_connection is not None
            assert ctx.root_vm.active_connection.name == "dev"
            assert after.source.connection_key == ("dev", "us-east-1")
            assert selected == [("emr-serverless", "dev", "us-east-1")]
            assert calls == ["prod-west", "dev"]
    finally:
        with contextlib.suppress(Exception):
            ctx.root_vm.dispose()


@pytest.mark.asyncio
async def test_direct_source_selection_probes_and_mounts_exact_target(tmp_path: Path) -> None:
    ctx, calls = _multi_profile_emr_context(tmp_path)
    app = AwsTuiApp(ctx)
    try:
        async with app.run_test() as pilot:
            await app.workers.wait_for_complete(list(app.workers._workers))
            await pilot.pause()
            ctx.root_vm.services_menu.switch_service_command.execute("emr-serverless")
            await _await_service_mount(pilot, app)
            probed: list[tuple[str, str]] = []

            def probe(connection: Connection) -> TokenProbeResult:
                probed.append((connection.name, connection.region))
                return TokenProbeResult(TokenState.CONNECTED)

            ctx.aws_session.probe_token = probe  # type: ignore[method-assign]
            await app._switch_single_context_source_to(
                "emr-serverless",
                "dev",
                "us-east-1",
            )
            await _await_service_mount(pilot, app)

            assert probed == [("dev", "us-east-1")]
            assert calls == ["prod-west", "dev"]
            assert ctx.root_vm.active_connection is not None
            assert (
                ctx.root_vm.active_connection.name,
                ctx.root_vm.active_connection.region,
            ) == ("dev", "us-east-1")
            assert ctx.root_vm.content_host.current is not None
            assert ctx.root_vm.content_host.current.source.connection_key == (
                "dev",
                "us-east-1",
            )
    finally:
        with contextlib.suppress(Exception):
            ctx.root_vm.dispose()


@pytest.mark.asyncio
async def test_emr_source_picker_rebuilds_exact_selected_target(tmp_path: Path) -> None:
    ctx, calls = _multi_profile_emr_context(tmp_path)
    app = AwsTuiApp(ctx)
    try:
        async with app.run_test() as pilot:
            await app.workers.wait_for_complete(list(app.workers._workers))
            await pilot.pause()
            ctx.root_vm.services_menu.switch_service_command.execute("emr-serverless")
            await _await_service_mount(pilot, app)
            page = app.query_one("#content-emr-page", EmrServerlessPage)
            picker = page.query_one("#emr-source-header-picker", ContextPicker)
            probed: list[tuple[str, str]] = []

            def probe(connection: Connection) -> TokenProbeResult:
                probed.append((connection.name, connection.region))
                return TokenProbeResult(TokenState.CONNECTED)

            ctx.aws_session.probe_token = probe  # type: ignore[method-assign]
            picker.focus()
            await pilot.press("enter")
            await pilot.pause()
            assert picker.is_open
            await pilot.press("down")
            await pilot.pause()
            assert picker.query_one(OptionList).highlighted == 1
            await pilot.press("enter")
            await _await_service_mount(pilot, app)

            current = ctx.root_vm.content_host.current
            assert current is not None
            assert current.source.connection_key == ("dev", "us-east-1")
            assert probed == [("dev", "us-east-1")]
            assert calls == ["prod-west", "dev"]
    finally:
        with contextlib.suppress(Exception):
            ctx.root_vm.dispose()


@pytest.mark.asyncio
async def test_failed_source_mount_restores_previous_source(tmp_path: Path) -> None:
    ctx, _calls = _multi_profile_emr_context(tmp_path)
    app = AwsTuiApp(ctx)
    try:
        async with app.run_test() as pilot:
            await app.workers.wait_for_complete(list(app.workers._workers))
            await pilot.pause()
            ctx.root_vm.services_menu.switch_service_command.execute("emr-serverless")
            await _await_service_mount(pilot, app)
            prior = ctx.root_vm.active_connection
            assert prior is not None
            target = next(
                connection
                for connection in _service_source_candidates(ctx, "emr-serverless")
                if connection != prior
            )
            real_mount = app._mount_service_view
            mount_targets: list[str] = []

            async def fail_target_mount(
                service_id: str,
                *,
                required_connection: Connection | None = None,
            ) -> bool:
                assert required_connection is not None
                mount_targets.append(required_connection.name)
                if required_connection == target:
                    return False
                return await real_mount(
                    service_id,
                    required_connection=required_connection,
                )

            app._mount_service_view = fail_target_mount  # type: ignore[method-assign]
            accepted = await app._rebuild_single_context_source("emr-serverless", target)

            assert accepted is False
            assert ctx.root_vm.active_connection == prior
            assert ctx.root_vm.content_host.current is not None
            assert ctx.root_vm.content_host.current.source.connection_key == (
                prior.name,
                prior.region,
            )
            assert mount_targets == [target.name, prior.name]
    finally:
        with contextlib.suppress(Exception):
            ctx.root_vm.dispose()


@pytest.mark.asyncio
async def test_cancelled_source_switch_during_teardown_restores_previous_source(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ctx, _calls = _multi_profile_emr_context(tmp_path)
    app = AwsTuiApp(ctx)
    release_shutdown = asyncio.Event()
    try:
        async with app.run_test() as pilot:
            await app.workers.wait_for_complete(list(app.workers._workers))
            await pilot.pause()
            ctx.root_vm.services_menu.switch_service_command.execute("emr-serverless")
            await _await_service_mount(pilot, app)
            prior = ctx.root_vm.active_connection
            prior_vm = ctx.root_vm.content_host.current
            assert prior is not None
            assert prior_vm is not None
            target = next(
                connection
                for connection in _service_source_candidates(ctx, "emr-serverless")
                if connection != prior
            )
            shutdown_started = asyncio.Event()
            original_shutdown = prior_vm.shutdown

            async def blocked_shutdown() -> None:
                shutdown_started.set()
                await release_shutdown.wait()
                await original_shutdown()

            monkeypatch.setattr(prior_vm, "shutdown", blocked_shutdown)
            rebuild = asyncio.create_task(
                app._rebuild_single_context_source("emr-serverless", target)
            )
            await asyncio.wait_for(shutdown_started.wait(), timeout=1)

            rebuild.cancel()
            release_shutdown.set()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(rebuild, timeout=2)
            await _await_service_mount(pilot, app)

            assert ctx.root_vm.active_connection == prior
            assert ctx.root_vm.content_host.current is not None
            assert ctx.root_vm.content_host.current.source.connection_key == (
                prior.name,
                prior.region,
            )
    finally:
        release_shutdown.set()
        with contextlib.suppress(Exception):
            ctx.root_vm.dispose()


@pytest.mark.asyncio
async def test_cancelled_source_mount_durably_restores_previous_source(tmp_path: Path) -> None:
    ctx, _calls = _multi_profile_emr_context(tmp_path)
    app = AwsTuiApp(ctx)
    release_rollback = asyncio.Event()
    try:
        async with app.run_test() as pilot:
            await app.workers.wait_for_complete(list(app.workers._workers))
            await pilot.pause()
            ctx.root_vm.services_menu.switch_service_command.execute("emr-serverless")
            await _await_service_mount(pilot, app)
            prior = ctx.root_vm.active_connection
            assert prior is not None
            target = next(
                connection
                for connection in _service_source_candidates(ctx, "emr-serverless")
                if connection != prior
            )
            real_mount = app._mount_service_view
            target_mount_started = asyncio.Event()
            rollback_mount_started = asyncio.Event()

            async def blocked_mount(
                service_id: str,
                *,
                required_connection: Connection | None = None,
            ) -> bool:
                assert required_connection is not None
                if required_connection == target:
                    target_mount_started.set()
                    await asyncio.Event().wait()
                rollback_mount_started.set()
                await release_rollback.wait()
                return await real_mount(
                    service_id,
                    required_connection=required_connection,
                )

            app._mount_service_view = blocked_mount  # type: ignore[method-assign]
            rebuild = asyncio.create_task(
                app._rebuild_single_context_source("emr-serverless", target)
            )
            await asyncio.wait_for(target_mount_started.wait(), timeout=1)
            assert ctx.root_vm.active_connection == target

            rebuild.cancel()
            await asyncio.wait_for(rollback_mount_started.wait(), timeout=1)
            rebuild.cancel()
            rebuild.cancel()
            await asyncio.sleep(0)
            assert not rebuild.done()
            release_rollback.set()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(rebuild, timeout=2)
            await _await_service_mount(pilot, app)

            assert ctx.root_vm.active_connection == prior
            assert ctx.root_vm.content_host.current is not None
            assert ctx.root_vm.content_host.current.source.connection_key == (
                prior.name,
                prior.region,
            )
    finally:
        release_rollback.set()
        with contextlib.suppress(Exception):
            ctx.root_vm.dispose()


@pytest.mark.asyncio
async def test_glue_source_picker_event_rebuilds_exact_selected_target(
    tmp_path: Path,
) -> None:
    ctx, calls = _multi_profile_glue_context(tmp_path)
    app = AwsTuiApp(ctx)
    try:
        async with app.run_test() as pilot:
            await app.workers.wait_for_complete(list(app.workers._workers))
            await pilot.pause()
            ctx.root_vm.services_menu.switch_service_command.execute("glue")
            await _await_service_mount(pilot, app)
            page = app.query_one("#content-glue-page", GluePage)
            picker = page.query_one("#glue-source-header-picker", ContextPicker)
            probed: list[tuple[str, str]] = []

            def probe(connection: Connection) -> TokenProbeResult:
                probed.append((connection.name, connection.region))
                return TokenProbeResult(TokenState.CONNECTED)

            ctx.aws_session.probe_token = probe  # type: ignore[method-assign]
            picker.focus()
            await pilot.press("enter", "down", "enter")
            await _await_service_mount(pilot, app)

            current = ctx.root_vm.content_host.current
            assert current is not None
            assert current.source.connection_key == ("dev", "us-east-1")
            assert probed == [("dev", "us-east-1")]
            assert calls == ["prod-west", "dev"]
    finally:
        with contextlib.suppress(Exception):
            ctx.root_vm.dispose()


@pytest.mark.asyncio
async def test_glue_source_picker_restores_active_source_when_target_disappears(
    tmp_path: Path,
) -> None:
    ctx, calls = _multi_profile_glue_context(tmp_path)
    app = AwsTuiApp(ctx)
    try:
        async with app.run_test() as pilot:
            await app.workers.wait_for_complete(list(app.workers._workers))
            await pilot.pause()
            ctx.root_vm.services_menu.switch_service_command.execute("glue")
            await _await_service_mount(pilot, app)
            page = app.query_one("#content-glue-page", GluePage)
            picker = page.query_one("#glue-source-header-picker", ContextPicker)
            current = ctx.root_vm.content_host.current
            assert current is not None
            assert current.source.connection_key == ("prod-west", "us-west-2")
            assert picker.value == "0"

            _dev, prod = _aws_connections()
            ctx.connection_resolver.list = lambda: [prod]  # type: ignore[method-assign]
            assert _service_source_candidates(ctx, "glue") == (prod,)
            switch_to = app._switch_single_context_source_to
            handled = asyncio.Event()
            accepted: list[tuple[str, str, bool]] = []

            async def recording_switch_to(
                service_id: str,
                connection_name: str,
                region: str,
            ) -> bool:
                result = await switch_to(service_id, connection_name, region)
                accepted.append((connection_name, region, result))
                handled.set()
                return result

            app._switch_single_context_source_to = recording_switch_to  # type: ignore[method-assign]
            picker.focus()
            await pilot.press("enter")
            await pilot.pause()
            await pilot.press("down", "enter")
            await asyncio.wait_for(handled.wait(), timeout=1)
            await pilot.pause()

            assert accepted == [("dev", "us-east-1", False)]
            assert picker.value == "0"
            assert str(
                picker.query_one(".context-picker-value", Static).render()
            ) == current.source.label.replace(" · ", "·")
            assert ctx.root_vm.active_connection is not None
            assert (
                ctx.root_vm.active_connection.name,
                ctx.root_vm.active_connection.region,
            ) == ("prod-west", "us-west-2")
            assert ctx.root_vm.content_host.current is current
            assert calls == ["prod-west"]
    finally:
        with contextlib.suppress(Exception):
            ctx.root_vm.dispose()
