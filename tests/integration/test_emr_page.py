"""End-to-end integration: EMR nav row appears on AWS connections
and disappears on s3-compatible. Selecting the row mounts
EmrServerlessPage in the content host."""

from __future__ import annotations

import contextlib
from pathlib import Path

import pytest

from aws_tui.app import AwsTuiApp
from aws_tui.composition import build_app_context
from aws_tui.services.emr_serverless.service import EmrServerlessService
from aws_tui.ui.widgets.emr_serverless.job_run_detail_pane import JobRunDetailPane
from aws_tui.ui.widgets.emr_serverless.job_runs_pane import JobRunsPane
from aws_tui.ui.widgets.emr_serverless.page import EmrServerlessPage
from aws_tui.ui.widgets.nav_menu import NavMenu
from tests.unit.domain._in_memory_emr import _InMemoryEmr


def _prep(tmp_path: Path, toml_text: str) -> Path:
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "config.toml").write_text(toml_text)
    return config_dir


def _make_ctx_with_emr_fake(config_dir: Path, cache_dir: Path) -> tuple[object, _InMemoryEmr]:
    ctx = build_app_context(config_dir=config_dir, cache_dir=cache_dir)
    fake = _InMemoryEmr()
    fake.add_application(app_id="00emr", name="etl")
    # Swap the registered EmrServerlessService's client factory for
    # the test fake so no boto3 calls escape.
    for svc in ctx.root_vm._registry.all():  # type: ignore[attr-defined]
        if isinstance(svc, EmrServerlessService):
            svc._client_factory = lambda _conn: fake  # type: ignore[assignment]
    return ctx, fake


_AWS_TOML = (
    "[connections.dev]\n"
    'kind = "aws"\n'
    'profile = "dev"\n'
    'region = "us-east-1"\n'
    "[defaults]\n"
    'connection = "dev"\n'
)

_S3COMPAT_TOML = (
    "[connections.minio]\n"
    'kind = "s3-compatible"\n'
    'endpoint_url = "http://127.0.0.1:1"\n'
    'region = "us-east-1"\n'
    'access_key_id = "x"\n'
    'secret_access_key = "y"\n'
    "[defaults]\n"
    'connection = "minio"\n'
)


@pytest.mark.asyncio
async def test_emr_page_mounts_on_aws_connection(tmp_path: Path) -> None:
    config_dir = _prep(tmp_path, _AWS_TOML)
    ctx, _fake = _make_ctx_with_emr_fake(config_dir, tmp_path / "cache")
    app = AwsTuiApp(ctx)
    try:
        async with app.run_test() as pilot:
            await app.workers.wait_for_complete(list(app.workers._workers))  # type: ignore[attr-defined]
            await pilot.pause()
            # Switch to EMR via the menu VM (avoids keymap routing).
            ctx.root_vm.services_menu.switch_service_command.execute("emr-serverless")
            await app.workers.wait_for_complete(list(app.workers._workers))  # type: ignore[attr-defined]
            await pilot.pause()
            host = pilot.app.query_one("#content-host")
            assert len(host.query(EmrServerlessPage)) == 1, (
                "expected EmrServerlessPage mounted in #content-host"
            )
    finally:
        with contextlib.suppress(Exception):
            ctx.root_vm.dispose()


@pytest.mark.asyncio
async def test_emr_nav_row_hidden_on_s3_compatible_connection(tmp_path: Path) -> None:
    config_dir = _prep(tmp_path, _S3COMPAT_TOML)
    ctx = build_app_context(config_dir=config_dir, cache_dir=tmp_path / "cache")
    app = AwsTuiApp(ctx)
    try:
        async with app.run_test() as pilot:
            await app.workers.wait_for_complete(list(app.workers._workers))  # type: ignore[attr-defined]
            await pilot.pause()
            # The nav menu's items must NOT include "emr-serverless" when
            # the active connection is s3-compatible.
            ids = [item.descriptor.id for item in ctx.root_vm.services_menu.items]
            assert "emr-serverless" not in ids, (
                f"EMR must be filtered out on s3-compatible connections, got {ids}"
            )
    finally:
        with contextlib.suppress(Exception):
            ctx.root_vm.dispose()


@pytest.mark.asyncio
async def test_emr_page_tab_cycles_between_panes_not_to_nav_rail(tmp_path: Path) -> None:
    """Spec §2 / PR #66 contract: Tab on the EMR page cycles LEFT ↔ RIGHT,
    NEVER falls through to the App-level priority binding that focuses
    the nav rail. Mirrors the S3 page's Tab-cycle contract.
    """
    config_dir = _prep(tmp_path, _AWS_TOML)
    ctx, _fake = _make_ctx_with_emr_fake(config_dir, tmp_path / "cache")
    app = AwsTuiApp(ctx)
    try:
        async with app.run_test() as pilot:
            await app.workers.wait_for_complete(list(app.workers._workers))  # type: ignore[attr-defined]
            await pilot.pause()
            # Switch to EMR.
            ctx.root_vm.services_menu.switch_service_command.execute("emr-serverless")
            await app.workers.wait_for_complete(list(app.workers._workers))  # type: ignore[attr-defined]
            await pilot.pause()

            left = pilot.app.query_one(JobRunsPane)
            right = pilot.app.query_one(JobRunDetailPane)
            nav = pilot.app.query_one(NavMenu)

            # Focus the LEFT pane first.
            left.focus()
            await pilot.pause()

            # Press Tab — must move focus to RIGHT pane, NOT to nav rail.
            await pilot.press("tab")
            await pilot.pause()

            assert pilot.app.focused is right or right.has_focus_within, (
                f"Tab on EMR LEFT pane focused {pilot.app.focused!r} — expected RIGHT pane. "
                f"This is the spec §2 'exactly 2 slots' regression "
                f"(App-level priority Tab binding hijacking)."
            )
            assert not nav.has_focus_within, (
                "Tab on EMR page focused the nav rail — same UX bug PR #66 fixed for S3."
            )
    finally:
        with contextlib.suppress(Exception):
            ctx.root_vm.dispose()
