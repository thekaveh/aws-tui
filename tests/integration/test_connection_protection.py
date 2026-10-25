"""Real composition and Settings navigation with controlled service providers."""

import contextlib

import pytest
from textual.widgets import Button, TextArea

from aws_tui.app import AwsTuiApp
from aws_tui.composition import build_app_context
from aws_tui.demo.in_memory_fs import InMemoryFS
from aws_tui.domain.mutation_guard import PROTECTED_MESSAGE
from aws_tui.infra.aws_session import TokenProbeResult, TokenState
from aws_tui.ui.widgets.service_source_header import ServiceSourceHeader
from aws_tui.vm.athena.page_vm import AthenaPageVM
from aws_tui.vm.file_manager.pane_vm import PaneState
from tests.helpers import drain_workers, focus_and_settle, seed_athena_sql, wait_until
from tests.integration.test_glue_page import open_service
from tests.unit.vm.athena.test_page_vm import PageClient


@pytest.mark.parametrize("size", [(80, 24), (120, 45)])
@pytest.mark.parametrize("entry", ["keyboard", "mouse", "palette"])
async def test_settings_protection_survives_navigation_and_denies_query(
    tmp_path, monkeypatch, entry, size
):
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "config.toml").write_text(
        '[connections.synthetic]\nkind="aws"\nprofile="synthetic"\nregion="us-east-1"\n[defaults]\nconnection="synthetic"\n'
    )
    aws = tmp_path / "aws-config"
    aws.write_text("[profile synthetic]\nregion=us-east-1\n")
    monkeypatch.setenv("AWS_CONFIG_FILE", str(aws))
    monkeypatch.setenv("AWS_SHARED_CREDENTIALS_FILE", str(tmp_path / "absent"))
    client = PageClient(connection_name="synthetic", region="us-east-1")
    ctx = build_app_context(config_dir=config_dir, cache_dir=tmp_path / "cache")
    ctx.registry.get("s3")._s3_fs_factory = lambda c: InMemoryFS()
    ctx.registry.get("athena")._client_factory = lambda c: client
    monkeypatch.setattr(
        ctx.aws_session, "probe_token", lambda c: TokenProbeResult(TokenState.CONNECTED)
    )
    app = AwsTuiApp(ctx)
    try:
        async with app.run_test(size=size) as pilot:
            await open_service(ctx, pilot, "settings")
            await wait_until(
                lambda: len(app.query("#connection-protection-0")) == 1,
                what="connection policy control",
            )
            button = app.query_one("#connection-protection-0", Button)
            await wait_until(lambda: button.is_mounted, what="mounted policy control")
            await focus_and_settle(button)
            await pilot.press("enter")
            await wait_until(
                lambda: ctx.config_store.load().connections["synthetic"].protected,
                what="saved protection",
            )
            assert ctx.root_vm.active_connection.protected
            await open_service(ctx, pilot, "athena")
            page = ctx.root_vm.content_host.current
            assert isinstance(page, AthenaPageVM)
            assert "Protected" in app.query_one(ServiceSourceHeader)._source.label
            editor = app.query_one("#athena-editor", TextArea)
            await seed_athena_sql(pilot, page.query, editor, "SELECT 1")
            await focus_and_settle(editor)
            if entry == "keyboard":
                await pilot.press("ctrl+enter")
            elif entry == "mouse":
                await pilot.click("#athena-execute")
            else:
                from aws_tui.ui.widgets.command_palette import CommandPalette

                await pilot.press("ctrl+k")
                await wait_until(
                    lambda: isinstance(app.screen, CommandPalette), what="query command palette"
                )
                await pilot.press(*"Execute Athena query")
                await wait_until(
                    lambda: len(ctx.command_palette_vm.filtered_entries) == 1,
                    what="query command selected",
                )
                await pilot.press("enter")
            await drain_workers(app)
            assert page.query.pane_state == PaneState.FORBIDDEN
            assert page.query.error_text == PROTECTED_MESSAGE
            assert client.start_calls == []
            await open_service(ctx, pilot, "settings")
            await wait_until(
                lambda: len(app.query("#connection-protection-0")) == 1,
                what="policy control after returning",
            )
            button = app.query_one("#connection-protection-0", Button)
            await wait_until(lambda: button.is_mounted, what="returned control mounted")
            await focus_and_settle(button)
            await pilot.press("enter")
            await wait_until(
                lambda: not ctx.config_store.load().connections["synthetic"].protected,
                what="protection disabled",
            )
            assert client.start_calls == []
    finally:
        with contextlib.suppress(Exception):
            await ctx.root_vm.content_host.shutdown()
        with contextlib.suppress(Exception):
            ctx.root_vm.dispose()
        ctx.log_sink.close()
