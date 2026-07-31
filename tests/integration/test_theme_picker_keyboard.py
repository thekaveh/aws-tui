"""Regression: arrows in ThemePickerModal must NOT be eaten by App.

The app declares ``Binding('up,k', 'move_up', priority=True)`` so the
file-manager cursor reacts even when nothing is focused. When the theme
picker modal is on top of the screen stack, the modal's bindings must
win the race — otherwise pressing ↑/↓ moves the dual-pane cursor
silently and the picker never moves.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from aws_tui.app import AwsTuiApp
from aws_tui.infra.theme_store import ThemeStore
from aws_tui.ui.widgets.theme_picker_modal import ThemePickerModal
from aws_tui.vm.messages import ThemeChangedMessage
from tests.integration.conftest import AppContextBuilder


@pytest.mark.asyncio
async def test_theme_picker_enter_applies_selection(
    app_context_factory: AppContextBuilder,
) -> None:
    """Enter inside the picker should fire ``action_apply`` even though
    App.BINDINGS declares ``enter`` as a priority binding for descend.
    """
    ctx = app_context_factory()
    app = AwsTuiApp(ctx)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        await pilot.pause()

        initial_theme = ctx.initial_theme
        await pilot.press("t")
        await pilot.pause()
        modal = app.screen
        assert isinstance(modal, ThemePickerModal)
        # Move cursor to the next theme then hit Enter.
        await pilot.press("down")
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()
        # Modal closed.
        assert not isinstance(app.screen, ThemePickerModal), "Enter didn't close the theme picker"
        # Theme actually changed.
        assert ctx.initial_theme != initial_theme, "Theme didn't change after Enter"


@pytest.mark.asyncio
async def test_theme_picker_arrows_move_cursor(
    app_context_factory: AppContextBuilder,
) -> None:
    ctx = app_context_factory()
    app = AwsTuiApp(ctx)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        await pilot.pause()

        await pilot.press("t")
        await pilot.pause()

        modal = app.screen
        assert isinstance(modal, ThemePickerModal), f"expected theme picker, got {modal}"
        initial = modal._cursor  # type: ignore[attr-defined]

        await pilot.press("down")
        await pilot.pause()
        assert modal._cursor == initial + 1, (  # type: ignore[attr-defined]
            f"Down arrow didn't advance cursor: {modal._cursor} vs {initial + 1}"  # type: ignore[attr-defined]
        )

        await pilot.press("up")
        await pilot.pause()
        assert modal._cursor == initial, (  # type: ignore[attr-defined]
            f"Up arrow didn't reverse cursor: {modal._cursor} vs {initial}"  # type: ignore[attr-defined]
        )


@pytest.mark.asyncio
async def test_theme_picker_applies_custom_theme_through_modal(
    app_context_factory: AppContextBuilder,
    tmp_path: Path,
) -> None:
    user_themes = tmp_path / "themes"
    user_themes.mkdir()
    (user_themes / "midnight.tcss").write_text(
        "/* custom-midnight */\nScreen { background: #101010; }\n",
        encoding="utf-8",
    )
    ctx = app_context_factory()
    ctx.theme_store = ThemeStore(
        user_themes_dir=user_themes,
        user_overlay=tmp_path / "theme.tcss",
    )
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
            await pilot.press("t")
            await pilot.pause()

            modal = app.screen
            assert isinstance(modal, ThemePickerModal)
            for _ in ThemeStore.BUILTIN_NAMES:
                await pilot.press("down")
                await pilot.pause()

            await pilot.press("enter")
            await pilot.pause()

            assert not isinstance(app.screen, ThemePickerModal)
            assert ctx.initial_theme == "midnight"
            assert changed[-1] == "midnight"
            assert sum(key == app._THEME_SOURCE_KEY for key in app.stylesheet.source) == 1
            assert "custom-midnight" in app.stylesheet.source[app._THEME_SOURCE_KEY].content
            assert ctx.root_vm.chrome.toast_stack.toasts[-1].model.id == ("theme-changed-midnight")
    finally:
        subscription.dispose()


@pytest.mark.asyncio
async def test_invalid_custom_theme_keeps_previous_theme_and_app_usable(
    app_context_factory: AppContextBuilder,
    tmp_path: Path,
) -> None:
    user_themes = tmp_path / "themes"
    user_themes.mkdir()
    (user_themes / "broken.tcss").write_text(
        "Screen { color: definitely-not-a-color; }\n",
        encoding="utf-8",
    )
    ctx = app_context_factory()
    ctx.theme_store = ThemeStore(
        user_themes_dir=user_themes,
        user_overlay=tmp_path / "theme.tcss",
    )
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
            await pilot.press("t")
            await pilot.pause()

            for _ in range(len(ThemeStore.BUILTIN_NAMES) - 1):
                await pilot.press("down")
                await pilot.pause()

            previous_theme = ctx.initial_theme
            previous_source = app.stylesheet.source[app._THEME_SOURCE_KEY].content
            changed.clear()

            await pilot.press("down")
            await pilot.pause()

            assert ctx.initial_theme == previous_theme
            assert app.stylesheet.source[app._THEME_SOURCE_KEY].content == previous_source
            assert changed == []
            assert [
                toast.model.id
                for toast in ctx.root_vm.chrome.toast_stack.toasts
                if toast.model.id == "theme-switch-failed-broken"
            ] == ["theme-switch-failed-broken"]

            await pilot.press("enter")
            await pilot.pause()

            assert not isinstance(app.screen, ThemePickerModal)
            assert ctx.initial_theme == previous_theme
            assert app.stylesheet.source[app._THEME_SOURCE_KEY].content == previous_source
            assert changed == []
            toast_ids = [toast.model.id for toast in ctx.root_vm.chrome.toast_stack.toasts]
            assert "theme-changed-broken" not in toast_ids
            assert toast_ids.count("theme-switch-failed-broken") == 1
            ctx.log_sink.flush()
            events = [
                json.loads(line)["event"]
                for line in ctx.log_sink.path.read_text(encoding="utf-8").splitlines()
            ]
            assert events.count("app.theme.switch_failed") == 1

            await pilot.press("T")
            await pilot.pause()
            assert ctx.initial_theme == ThemeStore.BUILTIN_NAMES[0]
    finally:
        subscription.dispose()


@pytest.mark.asyncio
async def test_escape_restores_theme_after_valid_custom_preview(
    app_context_factory: AppContextBuilder,
    tmp_path: Path,
) -> None:
    user_themes = tmp_path / "themes"
    user_themes.mkdir()
    (user_themes / "midnight.tcss").write_text(
        "/* custom-midnight */\nScreen { background: #101010; }\n",
        encoding="utf-8",
    )
    ctx = app_context_factory()
    ctx.theme_store = ThemeStore(
        user_themes_dir=user_themes,
        user_overlay=tmp_path / "theme.tcss",
    )
    app = AwsTuiApp(ctx)

    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        original_source = app.stylesheet.source[app._THEME_SOURCE_KEY].content
        await pilot.press("t")
        await pilot.pause()

        for _ in ThemeStore.BUILTIN_NAMES:
            await pilot.press("down")
            await pilot.pause()

        assert ctx.initial_theme == "midnight"
        await pilot.press("escape")
        await pilot.pause()

        assert not isinstance(app.screen, ThemePickerModal)
        assert ctx.initial_theme == "carbon"
        assert app.stylesheet.source[app._THEME_SOURCE_KEY].content == original_source


@pytest.mark.asyncio
async def test_cycle_theme_remains_limited_to_builtins(
    app_context_factory: AppContextBuilder,
    tmp_path: Path,
) -> None:
    user_themes = tmp_path / "themes"
    user_themes.mkdir()
    (user_themes / "midnight.tcss").write_text(
        "Screen { background: #101010; }\n",
        encoding="utf-8",
    )
    ctx = app_context_factory(initial_theme=ThemeStore.BUILTIN_NAMES[-1])
    ctx.theme_store = ThemeStore(
        user_themes_dir=user_themes,
        user_overlay=tmp_path / "theme.tcss",
    )
    app = AwsTuiApp(ctx)

    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        await pilot.press("T")
        await pilot.pause()

        assert ctx.initial_theme == ThemeStore.BUILTIN_NAMES[0]
