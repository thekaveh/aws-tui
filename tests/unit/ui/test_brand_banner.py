"""BrandBanner — palette swaps on theme change via the hub.

Locks in:
- Default palette is carbon (the genai-vanilla reference)
- Each theme name maps to a distinct 6-stop palette
- Receiving a ThemeChangedMessage on the hub causes a palette swap
- set_theme is idempotent on the same theme
"""

from __future__ import annotations

import pytest
from textual.app import App, ComposeResult
from vmx import MessageHub, RxDispatcher

from aws_tui.ui.widgets.brand_banner import _THEME_PALETTES, BrandBanner
from aws_tui.vm.messages import ThemeChangedMessage

assert RxDispatcher  # silence unused-import nag


def test_palette_dict_has_all_builtin_themes() -> None:
    """Every shipped theme must have its own banner palette so the
    banner can adopt that theme's color family on switch. Iterates the
    full ``_THEME_PALETTES`` registry rather than a hand-maintained
    subset, so adding a new theme to ``_THEME_HUES_DEGREES`` without
    producing a 6-stop entry would be caught.
    """
    assert _THEME_PALETTES, "no banner palettes registered"
    for theme, palette in _THEME_PALETTES.items():
        assert len(palette) == 6, f"{theme} palette must have 6 stops"


def test_palettes_are_distinct() -> None:
    """A theme switch must produce a visibly different gradient."""
    palettes = list(_THEME_PALETTES.values())
    seen: set[tuple[str, ...]] = set()
    for p in palettes:
        assert p not in seen, "duplicate banner palette across themes"
        seen.add(p)


@pytest.mark.asyncio
async def test_banner_swaps_palette_on_hub_message() -> None:
    hub: MessageHub = MessageHub()

    class _App(App[None]):
        def compose(self) -> ComposeResult:
            yield BrandBanner(theme_name="carbon", hub=hub, id="banner")

    app = _App()
    async with app.run_test() as pilot:
        await pilot.pause()
        banner = app.query_one(BrandBanner)
        before = banner.palette
        assert before == _THEME_PALETTES["carbon"]

        hub.send(ThemeChangedMessage(name="amber"))
        await pilot.pause()
        after = banner.palette
        assert after == _THEME_PALETTES["amber"]
        assert before != after


@pytest.mark.asyncio
async def test_banner_set_theme_idempotent() -> None:
    hub: MessageHub = MessageHub()

    class _App(App[None]):
        def compose(self) -> ComposeResult:
            yield BrandBanner(theme_name="carbon", hub=hub, id="banner")

    app = _App()
    async with app.run_test() as pilot:
        await pilot.pause()
        banner = app.query_one(BrandBanner)
        # Same theme name — no-op.
        before = banner.palette
        banner.set_theme("carbon")
        assert banner.palette is before
