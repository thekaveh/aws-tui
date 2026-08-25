"""Chrome composition + hint legend content. Locks in the current
visual decisions:

- ``BrandBanner`` is mounted at the top
- The old top-strip ``StatusBar`` widget is NOT present (identity is
  surfaced via the pane border subtitle instead)
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
from aws_tui.ui.widgets.hint_legend import HintLegend, _fit_actions
from tests.integration.conftest import AppContextBuilder


def _strip_text(host: HintLegend) -> str:
    return " ".join(str(s.render()) for s in host.query(Static))


def test_glue_and_athena_selector_actions_are_discoverable(
    app_context_factory: AppContextBuilder,
) -> None:
    ctx = app_context_factory()
    legend = ctx.root_vm.chrome.hint_legend

    legend.set_current_service("glue")
    glue = {action.action_id: action for action in legend.actions}
    assert glue["glue.choose_run_state"].action_label == "run state"
    assert glue["glue.choose_crawler_state"].action_label == "crawler state"
    assert glue["glue.copy_table_ref"].action_label == "copy"

    legend.set_current_service("athena")
    athena = {action.action_id: action for action in legend.actions}
    assert athena["athena.choose_workgroup"].action_label == "group"
    assert athena["athena.choose_catalog"].action_label == "catalog"
    assert athena["athena.choose_database"].action_label == "database"
    assert athena["athena.insert_table_ref"].action_label == "table"


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
async def test_app_unmount_disposes_table_clipboard_subscription(
    app_context_factory: AppContextBuilder,
) -> None:
    app = AwsTuiApp(app_context_factory())

    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        assert app._table_clipboard_sub is not None

    assert app._table_clipboard_sub is None


@pytest.mark.asyncio
async def test_hint_legend_contains_all_expected_action_chips(
    app_context_factory: AppContextBuilder,
) -> None:
    """The compact row projects visible commands and routes hidden ones to More."""
    ctx = app_context_factory()
    app = AwsTuiApp(ctx)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        await pilot.pause()
        legend = app.query_one(HintLegend)
        visible_ids = tuple(chip.action.action_id for chip in legend.query(".hint-chip"))
        actions = (*legend.vm.actions, *legend.vm.global_actions)
        expected_ids = tuple(
            action.action_id for action in _fit_actions(actions, legend.content_region.width)
        )

        assert visible_ids == expected_ids
        assert "app.command_palette" in visible_ids
        assert "app.quit" in visible_ids
        assert {"app.cycle_theme", "app.help"}.isdisjoint(visible_ids)

        for action_id in ("app.cycle_theme", "app.help"):
            assert ctx.keymap_store.resolve(action_id)
            assert app._actions.has(action_id)

        await pilot.press("colon")
        await pilot.pause()
        palette_ids = {entry.id for entry in ctx.command_palette_vm.filtered_entries}
        assert {"app.cycle_theme", "app.help"} <= palette_ids


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
