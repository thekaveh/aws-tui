"""Regression: pressing 'm', clicking the floating hamburger, or pressing
the key all toggle the nav rail. The rail is fully hidden (display:none,
width:0) when collapsed; expanded it shows the 'menu' header."""

from __future__ import annotations

import pytest
from textual.widgets import Static

from aws_tui.app import AwsTuiApp
from aws_tui.ui.widgets.nav_menu import NavMenu
from aws_tui.ui.widgets.services_hamburger import ServicesHamburger
from tests.integration.conftest import AppContextBuilder


@pytest.mark.asyncio
async def test_m_key_toggles_services_collapsed_state(
    app_context_factory: AppContextBuilder,
) -> None:
    ctx = app_context_factory()
    app = AwsTuiApp(ctx)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        await pilot.pause()

        menu = app.query_one(NavMenu)
        # Nav rail starts collapsed by default.
        assert menu.is_collapsed is True

        await pilot.press("m")
        await pilot.pause()
        assert menu.is_collapsed is False, "'m' didn't expand the nav rail"

        await pilot.press("m")
        await pilot.pause()
        assert menu.is_collapsed is True, "'m' didn't collapse the rail again"


@pytest.mark.asyncio
async def test_title_shows_services_when_expanded(
    app_context_factory: AppContextBuilder,
) -> None:
    """The inline menu header becomes visible only when the rail is expanded
    and shows the word 'menu'. (The hamburger affordance lives
    elsewhere — :class:`ServicesHamburger` — so the header is a passive
    label.)"""
    ctx = app_context_factory()
    app = AwsTuiApp(ctx)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        await pilot.pause()

        menu = app.query_one(NavMenu)
        header = menu.query_one("#menu-header", Static)

        await pilot.press("m")
        await pilot.pause()
        assert menu.is_collapsed is False
        rendered = str(header.render())
        assert "menu" in rendered


@pytest.mark.asyncio
async def test_clicking_the_hamburger_toggles_the_rail(
    app_context_factory: AppContextBuilder,
) -> None:
    """The floating top-left ServicesHamburger is the always-visible
    affordance for opening (and closing) the rail."""
    ctx = app_context_factory()
    app = AwsTuiApp(ctx)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        await pilot.pause()

        menu = app.query_one(NavMenu)
        hamburger = app.query_one(ServicesHamburger)

        assert menu.is_collapsed is True
        await pilot.click(hamburger)
        await pilot.pause()
        assert menu.is_collapsed is False, "clicking the hamburger didn't expand the rail"

        await pilot.click(hamburger)
        await pilot.pause()
        assert menu.is_collapsed is True, "clicking the hamburger didn't collapse the rail"
