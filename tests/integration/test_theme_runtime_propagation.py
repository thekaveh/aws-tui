"""Theme runtime swap — full propagation across the chrome.

Locks in:
- ``switch_theme`` broadcasts ``ThemeChangedMessage`` on the hub
- The banner widget repaints in the new theme's palette
- Multiple swaps don't accumulate stylesheet sources (read_from key
  is reused so the source is REPLACED, not appended)
- Cycle binding (``Shift+T``) advances to the next theme
"""

from __future__ import annotations

import pytest

from aws_tui.app import AwsTuiApp
from aws_tui.ui.widgets.brand_banner import _THEME_PALETTES, BrandBanner
from tests.integration.conftest import AppContextBuilder


@pytest.mark.asyncio
async def test_switch_theme_repaints_banner_via_hub(
    app_context_factory: AppContextBuilder,
) -> None:
    ctx = app_context_factory()
    app = AwsTuiApp(ctx)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        await pilot.pause()
        banner = app.query_one(BrandBanner)
        assert banner.palette == _THEME_PALETTES["carbon"]

        app.switch_theme("amber")
        await pilot.pause()

        assert banner.palette == _THEME_PALETTES["amber"]
        assert ctx.initial_theme == "amber"


@pytest.mark.asyncio
async def test_repeated_theme_swaps_dont_accumulate_sources(
    app_context_factory: AppContextBuilder,
) -> None:
    """A stable ``read_from`` key makes subsequent ``switch_theme``
    calls REPLACE the theme source instead of stacking them. Without
    that key the stylesheet would grow unbounded across swaps."""
    ctx = app_context_factory()
    app = AwsTuiApp(ctx)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        await pilot.pause()
        baseline = len(app.stylesheet.source)
        for theme in ("amber", "voidline", "lattice", "carbon"):
            app.switch_theme(theme)
            await pilot.pause()
        # Stylesheet source count should not have grown by the number
        # of switches — at most by 1 (the new theme source replacing
        # any pre-existing one).
        assert len(app.stylesheet.source) <= baseline + 1


@pytest.mark.asyncio
async def test_shift_t_cycles_theme(
    app_context_factory: AppContextBuilder,
) -> None:
    ctx = app_context_factory()
    app = AwsTuiApp(ctx)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        await pilot.pause()
        before = ctx.initial_theme
        await pilot.press("T")  # uppercase T = Shift+t
        await pilot.pause()
        assert ctx.initial_theme != before
