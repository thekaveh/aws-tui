"""Regression: pressing 's' OR clicking the +/- title toggles the
services rail's collapsed state, and the title glyph reflects state."""

from __future__ import annotations

import pytest
from textual.widgets import Static

from aws_tui.app import AwsTuiApp
from aws_tui.ui.widgets.services_menu import ServicesMenu, _ServicesMenuTitle
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
        # Services rail starts collapsed by default.
        assert menu.is_collapsed is True

        await pilot.press("s")
        await pilot.pause()
        assert menu.is_collapsed is False, "'s' didn't expand the services rail"

        await pilot.press("s")
        await pilot.pause()
        assert menu.is_collapsed is True, "'s' didn't collapse the rail again"


@pytest.mark.asyncio
async def test_title_glyph_reflects_collapsed_state(
    app_context_factory: AppContextBuilder,
) -> None:
    """The +/- glyph in the title row must update when the rail toggles."""
    ctx = app_context_factory()
    app = AwsTuiApp(ctx)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        await pilot.pause()

        menu = app.query_one(ServicesMenu)
        title = menu.query_one(_ServicesMenuTitle, Static)

        assert menu.is_collapsed is True
        assert str(title.render()) == "+", "collapsed rail must show '+'"

        await pilot.press("s")
        await pilot.pause()
        assert menu.is_collapsed is False
        assert "services" in str(title.render())
        assert str(title.render()).startswith("-"), "expanded rail must show '- services'"


@pytest.mark.asyncio
async def test_clicking_the_title_toggles_the_rail(
    app_context_factory: AppContextBuilder,
) -> None:
    """Clicking the +/- title row toggles collapsed state — discoverable
    affordance alongside the 's' shortcut."""
    ctx = app_context_factory()
    app = AwsTuiApp(ctx)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        await pilot.pause()

        menu = app.query_one(ServicesMenu)
        title = menu.query_one(_ServicesMenuTitle, Static)

        assert menu.is_collapsed is True
        await pilot.click(title)
        await pilot.pause()
        assert menu.is_collapsed is False, "clicking '+' didn't expand the rail"

        await pilot.click(title)
        await pilot.pause()
        assert menu.is_collapsed is True, "clicking '-' didn't collapse the rail"
