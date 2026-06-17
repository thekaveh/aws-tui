"""Regression: pressing 's' collapses/expands the services rail."""

from __future__ import annotations

import pytest

from aws_tui.app import AwsTuiApp
from aws_tui.ui.widgets.services_menu import ServicesMenu
from tests.integration.conftest import AppContextBuilder


@pytest.mark.asyncio
async def test_s_key_toggles_services_collapsed_state(
    app_context_factory: AppContextBuilder,
) -> None:
    ctx = app_context_factory()
    app = AwsTuiApp(ctx)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        await pilot.pause()

        menu = app.query_one(ServicesMenu)
        # Pass-10 default: starts collapsed.
        assert menu.is_collapsed is True

        await pilot.press("s")
        await pilot.pause()
        assert menu.is_collapsed is False, "'s' didn't expand the services rail"

        await pilot.press("s")
        await pilot.pause()
        assert menu.is_collapsed is True, "'s' didn't collapse the rail again"
