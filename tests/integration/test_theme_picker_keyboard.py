"""Regression: arrows in ThemePickerModal must NOT be eaten by App.

The app declares ``Binding('up,k', 'move_up', priority=True)`` so the
file-manager cursor reacts even when nothing is focused. When the theme
picker modal is on top of the screen stack, the modal's bindings must
win the race — otherwise pressing ↑/↓ moves the dual-pane cursor
silently and the picker never moves.
"""

from __future__ import annotations

import pytest

from aws_tui.app import AwsTuiApp
from aws_tui.ui.widgets.theme_picker_modal import ThemePickerModal
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
