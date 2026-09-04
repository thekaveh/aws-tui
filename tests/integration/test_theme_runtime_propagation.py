"""Theme runtime swap — full propagation across the chrome.

Locks in:
- ``switch_theme`` broadcasts ``ThemeChangedMessage`` on the hub
- The banner widget repaints in the new theme's palette
- Multiple swaps don't accumulate stylesheet sources (read_from key
  is reused so the source is REPLACED, not appended)
- Cycle binding (``Shift+T``) advances to the next theme
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from textual.color import Color

from aws_tui.app import AwsTuiApp
from aws_tui.infra.theme_store import ThemeStore
from aws_tui.ui.widgets.brand_banner import _THEME_PALETTES, BrandBanner
from aws_tui.vm.messages import ThemeChangedMessage
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
async def test_user_overlay_styles_athena_context_row(
    tmp_path: Path,
) -> None:
    overlay = tmp_path / "theme.tcss"
    overlay.write_text(
        "AthenaPage > #athena-context-row { border: solid #ff00ff; }\n",
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
        row = app.query_one("#athena-context-row")
        assert row.styles.border_top == ("solid", Color.parse("#ff00ff"))


@pytest.mark.asyncio
async def test_invalid_configured_theme_starts_with_packaged_carbon_fallback(
    app_context_factory: AppContextBuilder,
    tmp_path: Path,
) -> None:
    user_themes = tmp_path / "themes"
    user_themes.mkdir()
    (user_themes / "broken.tcss").write_text(
        "Screen { color: definitely-not-a-color; }\n",
        encoding="utf-8",
    )
    store = ThemeStore(
        user_themes_dir=user_themes,
        user_overlay=tmp_path / "theme.tcss",
    )
    ctx = app_context_factory(initial_theme="broken")
    ctx.theme_store = store
    app = AwsTuiApp(ctx)
    changed: list[str] = []
    subscription = ctx.hub.messages.subscribe(
        on_next=lambda message: (
            changed.append(message.name) if isinstance(message, ThemeChangedMessage) else None
        )
    )

    try:
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()

            assert ctx.initial_theme == ThemeStore.DEFAULT_NAME
            assert app.stylesheet.source[app._THEME_SOURCE_KEY].content == store.load_builtin(
                ThemeStore.DEFAULT_NAME
            )
            assert (
                "definitely-not-a-color" not in app.stylesheet.source[app._THEME_SOURCE_KEY].content
            )
            assert changed == []
            assert not any(
                toast.model.id.startswith("theme-")
                for toast in ctx.root_vm.chrome.toast_stack.toasts
            )
            ctx.log_sink.flush()
            events = [
                json.loads(line)["event"]
                for line in ctx.log_sink.path.read_text(encoding="utf-8").splitlines()
            ]
            assert events.count("app.theme.initial_failed") == 1
            assert events.count("app.theme.fallback_applied") == 1
            assert "app.theme.switch_failed" not in events

            assert app.switch_theme("amber") is True
            await pilot.pause()
            assert ctx.initial_theme == "amber"
    finally:
        subscription.dispose()


@pytest.mark.asyncio
async def test_invalid_overlay_starts_with_packaged_carbon_fallback(
    app_context_factory: AppContextBuilder,
    tmp_path: Path,
) -> None:
    overlay = tmp_path / "theme.tcss"
    overlay.write_text("Screen { color: definitely-not-a-color; }\n", encoding="utf-8")
    store = ThemeStore(user_themes_dir=tmp_path / "themes", user_overlay=overlay)
    ctx = app_context_factory(initial_theme="amber")
    ctx.theme_store = store
    app = AwsTuiApp(ctx)
    changed: list[str] = []
    subscription = ctx.hub.messages.subscribe(
        on_next=lambda message: (
            changed.append(message.name) if isinstance(message, ThemeChangedMessage) else None
        )
    )

    try:
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()

            assert ctx.initial_theme == ThemeStore.DEFAULT_NAME
            assert app.stylesheet.source[app._THEME_SOURCE_KEY].content == store.load_builtin(
                ThemeStore.DEFAULT_NAME
            )
            assert (
                "definitely-not-a-color" not in app.stylesheet.source[app._THEME_SOURCE_KEY].content
            )
            assert changed == []
            assert app.query_one(BrandBanner).palette == _THEME_PALETTES[ThemeStore.DEFAULT_NAME]
            ctx.log_sink.flush()
            events = [
                json.loads(line)["event"]
                for line in ctx.log_sink.path.read_text(encoding="utf-8").splitlines()
            ]
            assert events.count("app.theme.initial_failed") == 1
            assert events.count("app.theme.fallback_applied") == 1

            overlay.unlink()
            assert app.switch_theme("amber") is True
            await pilot.pause()
            assert ctx.initial_theme == "amber"
    finally:
        subscription.dispose()


@pytest.mark.asyncio
async def test_initial_live_apply_failure_uses_packaged_fallback(
    app_context_factory: AppContextBuilder,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ctx = app_context_factory(initial_theme="amber")
    app = AwsTuiApp(ctx)
    original_refresh = app.refresh_css
    refresh_calls = 0

    def fail_first_refresh(animate: bool = True) -> Any:
        nonlocal refresh_calls
        refresh_calls += 1
        if refresh_calls == 1:
            raise RuntimeError("forced startup apply failure")
        return original_refresh(animate)

    monkeypatch.setattr(app, "refresh_css", fail_first_refresh)

    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()

        assert refresh_calls >= 3
        assert ctx.initial_theme == ThemeStore.DEFAULT_NAME
        assert app.stylesheet.source[app._THEME_SOURCE_KEY].content == ctx.theme_store.load_builtin(
            ThemeStore.DEFAULT_NAME
        )
        ctx.log_sink.flush()
        records = [
            json.loads(line) for line in ctx.log_sink.path.read_text(encoding="utf-8").splitlines()
        ]
        initial_failures = [
            record for record in records if record["event"] == "app.theme.initial_failed"
        ]
        assert len(initial_failures) == 1
        assert initial_failures[0]["stage"] == "apply"
        assert sum(record["event"] == "app.theme.fallback_applied" for record in records) == 1


@pytest.mark.asyncio
async def test_live_apply_failure_rolls_back_and_next_switch_succeeds(
    app_context_factory: AppContextBuilder,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ctx = app_context_factory()
    app = AwsTuiApp(ctx)
    changed: list[str] = []
    subscription = ctx.hub.messages.subscribe(
        on_next=lambda message: (
            changed.append(message.name) if isinstance(message, ThemeChangedMessage) else None
        )
    )

    try:
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            previous_stylesheet = app.stylesheet
            previous_sources = previous_stylesheet.source
            previous_theme_source = previous_sources[app._THEME_SOURCE_KEY]
            previous_theme = ctx.initial_theme
            original_refresh = app.refresh_css
            refresh_calls = 0

            def fail_first_refresh(animate: bool = True) -> Any:
                nonlocal refresh_calls
                refresh_calls += 1
                if refresh_calls == 1:
                    raise RuntimeError("forced live apply failure")
                return original_refresh(animate)

            monkeypatch.setattr(app, "refresh_css", fail_first_refresh)

            assert app.switch_theme("amber") is False
            await pilot.pause()

            assert app.stylesheet is previous_stylesheet
            assert app.stylesheet.source is previous_sources
            assert app.stylesheet.source[app._THEME_SOURCE_KEY] is previous_theme_source
            assert ctx.initial_theme == previous_theme
            assert changed == []
            toast_ids = [toast.model.id for toast in ctx.root_vm.chrome.toast_stack.toasts]
            assert toast_ids.count("theme-switch-failed-amber") == 1
            assert "theme-changed-amber" not in toast_ids
            ctx.log_sink.flush()
            events = [
                json.loads(line)["event"]
                for line in ctx.log_sink.path.read_text(encoding="utf-8").splitlines()
            ]
            assert events.count("app.theme.switch_failed") == 1

            assert app.switch_theme("voidline") is True
            await pilot.pause()
            assert ctx.initial_theme == "voidline"
            assert changed == ["voidline"]
            assert app.query_one(BrandBanner).palette == _THEME_PALETTES["voidline"]
    finally:
        subscription.dispose()
