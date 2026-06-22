"""Regression: pressing ``m``, or clicking the inline hamburger at the
top of the nav rail, toggles between the collapsed (icon-only) and
expanded (icon + label) width states. The rail is **always visible** —
there is no longer a fully-hidden state; the legacy ``ServicesHamburger``
external widget was removed in the always-visible nav rework, the
hamburger now lives inside :class:`NavMenu` itself (``#menu-hamburger``).
"""

from __future__ import annotations

import pytest
from textual.widgets import Static

from aws_tui.app import AwsTuiApp
from aws_tui.ui.widgets.nav_menu import NavMenu
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
async def test_hamburger_glyph_reflects_next_action(
    app_context_factory: AppContextBuilder,
) -> None:
    """The inline hamburger at the top of the rail shows ``+`` when
    collapsed (click expands) and ``-`` when expanded (click collapses)."""
    ctx = app_context_factory()
    app = AwsTuiApp(ctx)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        await pilot.pause()

        menu = app.query_one(NavMenu)
        ham = menu.query_one("#menu-hamburger", Static)
        # Default state: collapsed → glyph signals "+ click to expand".
        assert menu.is_collapsed is True
        assert "+" in str(ham.render())

        await pilot.press("m")
        await pilot.pause()
        assert menu.is_collapsed is False
        assert "-" in str(ham.render())

        await pilot.press("m")
        await pilot.pause()
        assert menu.is_collapsed is True
        assert "+" in str(ham.render())


@pytest.mark.asyncio
async def test_clicking_the_hamburger_toggles_the_rail(
    app_context_factory: AppContextBuilder,
) -> None:
    """The inline hamburger Static at the top of the rail is the
    mouse-driven affordance for the same collapsed/expanded toggle."""
    ctx = app_context_factory()
    app = AwsTuiApp(ctx)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        await pilot.pause()

        menu = app.query_one(NavMenu)
        ham = menu.query_one("#menu-hamburger", Static)

        assert menu.is_collapsed is True
        await pilot.click(ham)
        await pilot.pause()
        assert menu.is_collapsed is False, "clicking the hamburger didn't expand the rail"

        await pilot.click(ham)
        await pilot.pause()
        assert menu.is_collapsed is True, "clicking the hamburger didn't collapse the rail"


@pytest.mark.asyncio
async def test_rail_is_always_visible(
    app_context_factory: AppContextBuilder,
) -> None:
    """The rail is never fully hidden — the legacy
    ``display: none; width: 0`` state was dropped. Collapsed = width 4
    (icon-only); expanded = width 18."""
    ctx = app_context_factory()
    app = AwsTuiApp(ctx)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        await pilot.pause()

        menu = app.query_one(NavMenu)
        # Even when collapsed, the rail occupies real space.
        assert menu.is_collapsed is True
        assert menu.region.width > 0, "collapsed rail must still be visible"
        # Expanded rail is wider than collapsed.
        collapsed_width = menu.region.width
        await pilot.press("m")
        await pilot.pause()
        assert menu.region.width > collapsed_width
