"""Single-context AWS service source switching via ``Shift+S``."""

from __future__ import annotations

import contextlib
from pathlib import Path

import pytest

from aws_tui.app import AwsTuiApp, _next_service_source, _service_source_candidates
from aws_tui.composition import AppContext, build_app_context
from aws_tui.infra.aws_session import TokenProbeResult, TokenState
from aws_tui.infra.connection_resolver import Connection
from aws_tui.services.emr_serverless.service import EmrServerlessService
from tests.unit.domain._in_memory_emr import _InMemoryEmr


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


def test_next_service_source_wraps_by_connection_name_and_region() -> None:
    dev, prod = _aws_connections()
    assert _next_service_source((dev, prod), dev) == prod
    assert _next_service_source((dev, prod), prod) == dev


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
            switch_to = app._switch_single_context_source_to

            async def recording_switch_to(
                service_id: str,
                connection_name: str,
                region: str,
            ) -> None:
                selected.append((service_id, connection_name, region))
                await switch_to(service_id, connection_name, region)

            app._switch_single_context_source_to = recording_switch_to  # type: ignore[method-assign]

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
