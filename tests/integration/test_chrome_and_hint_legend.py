"""Chrome composition + hint legend content. Locks in the current
visual decisions:

- ``BrandBanner`` is mounted at the top
- The old top-strip ``StatusBar`` widget is NOT present (identity is
  surfaced via the pane border subtitle instead)
- ``ServicesMenu`` starts collapsed
- ``HintLegend`` includes the action ids the user can reach via
  bindings: t themes, T cycle, S swap source, c copy, d delete,
  enter open, tab switch, r refresh, ? help, q quit
- Footer chips use the themable ``.hint-key`` / ``.hint-label`` /
  ``.hint-sep`` classes (not Rich inline styles)
"""

from __future__ import annotations

import pytest
from textual.widgets import Static

from aws_tui.app import AwsTuiApp
from aws_tui.ui.widgets.brand_banner import BrandBanner
from aws_tui.ui.widgets.hint_legend import HintLegend
from aws_tui.ui.widgets.services_menu import ServicesMenu
from tests.integration.conftest import AppContextBuilder


def _strip_text(host: HintLegend) -> str:
    return " ".join(str(s.render()) for s in host.query(Static))


@pytest.mark.asyncio
async def test_chrome_has_banner_no_statusbar(
    app_context_factory: AppContextBuilder,
) -> None:
    """StatusBar is not mounted; BrandBanner sits at the top of the chrome."""
    ctx = app_context_factory()
    app = AwsTuiApp(ctx)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        await pilot.pause()
        assert len(app.query(BrandBanner)) == 1
        # No StatusBar widget should be mounted.
        from aws_tui.ui.widgets.status_bar import StatusBar

        assert len(app.query(StatusBar)) == 0


@pytest.mark.asyncio
async def test_services_menu_starts_collapsed(
    app_context_factory: AppContextBuilder,
) -> None:
    ctx = app_context_factory()
    app = AwsTuiApp(ctx)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        await pilot.pause()
        menu = app.query_one(ServicesMenu)
        assert menu.is_collapsed is True


@pytest.mark.asyncio
async def test_hint_legend_contains_all_expected_action_chips(
    app_context_factory: AppContextBuilder,
) -> None:
    """Every action the user might reach for must be discoverable in
    the bottom strip."""
    ctx = app_context_factory()
    app = AwsTuiApp(ctx)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        await pilot.pause()
        legend = app.query_one(HintLegend)
        text = _strip_text(legend)
        # Action labels from hint_legend_vm _ACTION_LABELS.
        for label in (
            "open",
            "switch",
            "copy",
            "delete",
            "refresh",
            "themes",
            "cycle",
            "swap src",
            "help",
            "quit",
        ):
            assert label in text, f"hint legend missing chip: {label!r}"


@pytest.mark.asyncio
async def test_hint_legend_chips_use_themable_css_classes(
    app_context_factory: AppContextBuilder,
) -> None:
    """Each hint chip is split into ``.hint-key`` and ``.hint-label``
    Statics so theme tcss can color them. Verify the CSS classes are
    actually applied."""
    ctx = app_context_factory()
    app = AwsTuiApp(ctx)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        await pilot.pause()
        legend = app.query_one(HintLegend)
        statics = list(legend.query(Static))
        assert statics, "legend should compose into Static chips"
        # At least one of each role should exist (each chip = key + label).
        has_key = any("hint-key" in (s.classes or "") for s in statics)
        has_label = any("hint-label" in (s.classes or "") for s in statics)
        assert has_key, "no .hint-key Statics in HintLegend"
        assert has_label, "no .hint-label Statics in HintLegend"
