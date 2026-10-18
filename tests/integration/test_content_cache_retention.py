"""Removed service widgets must not stay alive in ancestor query caches."""

from __future__ import annotations

import gc
import weakref
from pathlib import Path

import pytest
from textual.containers import Container
from textual.widgets import Static

from aws_tui.app import AwsTuiApp
from aws_tui.composition import build_app_context
from aws_tui.infra.clipboard import InMemoryClipboard
from tests.helpers import wait_until
from tests.integration.test_glue_athena_navigation import _open_service


@pytest.mark.parametrize("reset_host", [False, True])
async def test_content_removal_releases_cached_detached_widgets(
    tmp_path: Path, reset_host: bool
) -> None:
    ctx = build_app_context(
        config_dir=tmp_path / "config",
        cache_dir=tmp_path / "cache",
        demo=True,
        clipboard=InMemoryClipboard(),
    )
    app = AwsTuiApp(ctx)
    async with app.run_test(size=(120, 40)) as pilot:
        await _open_service(ctx, app, pilot, "glue")

        async def mount_retired() -> weakref.ReferenceType[Static]:
            page = Static("retired service data", id="retired-service")
            await app._replace_content_widget(app.query_one("#content-host", Container), page)
            await pilot.pause()
            assert app.query_one("#retired-service", Static) is page
            assert app.query_one("#content-host", Container).query_one("#retired-service") is page
            return weakref.ref(page)

        retired = await mount_retired()
        if reset_host:
            await app._reset_content_host(app.query_one("#content-host", Container))
        else:
            await app._replace_content_widget(
                app.query_one("#content-host", Container),
                Static("current service", id="current-service"),
            )
        # Keep no test-owned parent whose layout snapshot could retain the old child.
        await pilot.pause()

        def released() -> bool:
            gc.collect()
            return retired() is None

        await wait_until(released, what="removed service widget to be released from query caches")
        assert app.query_one("#content-host", Container).is_attached
