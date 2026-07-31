"""Theme runtime swap — full propagation across the chrome.

Locks in:
- ``switch_theme`` broadcasts ``ThemeChangedMessage`` on the hub
- The banner widget repaints in the new theme's palette
- Multiple swaps don't accumulate stylesheet sources (read_from key
  is reused so the source is REPLACED, not appended)
- Cycle binding (``Shift+T``) advances to the next theme
"""

from __future__ import annotations

from pathlib import Path

import pytest
from textual.color import Color

from aws_tui.app import AwsTuiApp
from aws_tui.infra.theme_store import ThemeStore
from aws_tui.ui.widgets.brand_banner import _THEME_PALETTES, BrandBanner
from tests.integration.conftest import AppContextBuilder
from tests.snapshot.apps.athena import AthenaPageApp


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


@pytest.mark.asyncio
async def test_user_overlay_overrides_shared_operational_border(
    tmp_path: Path,
) -> None:
    overlay = tmp_path / "theme.tcss"
    overlay.write_text(
        "AthenaPage > #athena-context-header { border: solid #ff00ff; }\n",
        encoding="utf-8",
    )
    theme = ThemeStore(
        user_themes_dir=tmp_path / "themes",
        user_overlay=overlay,
    ).load("carbon")
    app = AthenaPageApp(theme="carbon", fixture="empty-query")
    app.CSS = theme

    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        header = app.query_one("#athena-context-header")
        assert header.styles.border_top == ("solid", Color.parse("#ff00ff"))
