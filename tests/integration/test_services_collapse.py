"""Regression: pressing 's', clicking the floating hamburger, or clicking
the inline title (when expanded) all toggle the services rail. The rail
is fully hidden (display:none, width:0) when collapsed; expanded it
shows the '≡ services' title."""

from __future__ import annotations

import pytest
from textual.widgets import Static

from aws_tui.app import AwsTuiApp
from aws_tui.ui.widgets.services_hamburger import ServicesHamburger
from aws_tui.ui.widgets.services_menu import ServicesMenu, _ServicesMenuTitle
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

        menu = app.query_one(ServicesMenu)
        # Services rail starts collapsed by default.
        assert menu.is_collapsed is True

        await pilot.press("m")
        await pilot.pause()
        assert menu.is_collapsed is False, "'m' didn't expand the services rail"

        await pilot.press("m")
        await pilot.pause()
        assert menu.is_collapsed is True, "'m' didn't collapse the rail again"


@pytest.mark.asyncio
async def test_title_shows_services_when_expanded(
    app_context_factory: AppContextBuilder,
) -> None:
    """The inline title becomes visible only when the rail is expanded
    and shows the hamburger glyph + the word 'services'."""
    ctx = app_context_factory()
    app = AwsTuiApp(ctx)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        await pilot.pause()

        menu = app.query_one(ServicesMenu)
        title = menu.query_one(_ServicesMenuTitle, Static)

        await pilot.press("m")
        await pilot.pause()
        assert menu.is_collapsed is False
        rendered = str(title.render())
        assert "services" in rendered
        assert rendered.startswith("≡"), "expanded title must lead with the hamburger glyph"


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

        menu = app.query_one(ServicesMenu)
        hamburger = app.query_one(ServicesHamburger)

        assert menu.is_collapsed is True
        await pilot.click(hamburger)
        await pilot.pause()
        assert menu.is_collapsed is False, "clicking the hamburger didn't expand the rail"

        await pilot.click(hamburger)
        await pilot.pause()
        assert menu.is_collapsed is True, "clicking the hamburger didn't collapse the rail"
