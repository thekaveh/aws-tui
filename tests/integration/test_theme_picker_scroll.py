"""Keyboard selection remains visible with real, isolated custom themes."""

from __future__ import annotations

from importlib import resources

import pytest
from textual.containers import VerticalScroll
from textual.events import MouseScrollDown

from aws_tui.app import AwsTuiApp
from aws_tui.infra.theme_store import ThemeStore
from aws_tui.ui.widgets.theme_picker_modal import ThemePickerModal
from tests.helpers import wait_until


@pytest.fixture
def many_themes(app_context_factory, tmp_path):
    def build(initial="carbon"):
        directory = tmp_path / "themes"
        directory.mkdir(exist_ok=True)
        carbon = resources.files("aws_tui.ui.themes").joinpath("carbon.tcss").read_text()
        for index in range(10):
            name = f"custom-{index:02d}"
            (directory / f"{name}.tcss").write_text(f"/* {name} */\n{carbon}")
        ctx = app_context_factory(initial_theme=initial)
        ctx.theme_store = ThemeStore(
            user_themes_dir=directory, user_overlay=tmp_path / "theme.tcss"
        )
        assert len(ctx.theme_store.list_themes()) == 20
        return AwsTuiApp(ctx), ctx

    return build


def assert_cursor_visible(modal):
    row = modal._rows[modal._cursor]
    body = modal.query_one(VerticalScroll)
    assert row.has_class("-cursor")
    assert sum(item.has_class("-cursor") for item in modal._rows) == 1
    assert body.content_region.contains_region(row.region), (
        row.theme_name,
        row.region,
        body.content_region,
        body.scroll_y,
        body.max_scroll_y,
    )
    assert modal.region.contains_region(row.region)
    return row.theme_name


@pytest.mark.parametrize("size", [(80, 24), (120, 40)])
async def test_every_keyboard_selection_is_visible_and_enter_commits(many_themes, size):
    app, ctx = many_themes()
    async with app.run_test(size=size) as pilot:
        await pilot.press("t")
        await pilot.pause()
        modal = app.screen
        assert isinstance(modal, ThemePickerModal)
        assert_cursor_visible(modal)
        for keys, indices in ((("down", "j"), range(1, 20)), (("up", "k"), range(18, -1, -1))):
            for index in indices:
                await pilot.press(keys[index % 2])
                await pilot.pause()
                assert modal._cursor == index
                assert ctx.initial_theme == assert_cursor_visible(modal)
        # Boundaries clamp; a burst of queued navigation still reveals the final choice.
        await pilot.press("up", "k", *(["down"] * 22))
        await pilot.pause()
        assert modal._cursor == 19
        assert assert_cursor_visible(modal) == "custom-09"
        assert ctx.initial_theme == "custom-09"
        await pilot.press("enter")
        await wait_until(lambda: not isinstance(app.screen, ThemePickerModal), what="theme applied")
        assert ctx.initial_theme == "custom-09"
        assert ctx.root_vm.chrome.toast_stack.toasts[-1].model.id == "theme-changed-custom-09"
        assert app._crash_report is None


@pytest.mark.parametrize("size", [(80, 24), (120, 40)])
async def test_late_active_theme_is_revealed_and_escape_restores_it(many_themes, size):
    app, ctx = many_themes("custom-09")
    async with app.run_test(size=size) as pilot:
        original = app.stylesheet.source[app._THEME_SOURCE_KEY].content
        await pilot.press("t")
        await pilot.pause()
        modal = app.screen
        assert isinstance(modal, ThemePickerModal)
        assert modal._cursor == 19
        assert assert_cursor_visible(modal) == "custom-09"
        await pilot.press(*(["up"] * 19))
        await pilot.pause()
        assert assert_cursor_visible(modal) == "carbon"
        assert ctx.initial_theme == "carbon"
        await pilot.press("escape")
        await wait_until(
            lambda: (
                not isinstance(app.screen, ThemePickerModal) and ctx.initial_theme == "custom-09"
            ),
            what="late original theme restored",
        )
        assert app.stylesheet.source[app._THEME_SOURCE_KEY].content == original
        # Reopening uses the restored selection and fresh layout again.
        await pilot.press("t")
        await pilot.pause()
        assert assert_cursor_visible(app.screen) == "custom-09"
        await pilot.press("escape")
        assert app._crash_report is None


@pytest.mark.parametrize("size", [(80, 24), (120, 40)])
async def test_mouse_wheel_remains_independent_of_keyboard_cursor(many_themes, size):
    app, ctx = many_themes()
    async with app.run_test(size=size) as pilot:
        await pilot.press("t")
        await pilot.pause()
        modal = app.screen
        assert isinstance(modal, ThemePickerModal)
        body = modal.query_one(VerticalScroll)
        x, y = body.content_region.center
        driver = app._driver
        assert driver is not None
        for _ in range(4):
            driver.process_message(MouseScrollDown(None, x, y, 0, 1, 0, False, False, False))
        await wait_until(lambda: body.scroll_y > 0, what="mouse wheel scroll")
        await pilot.pause()
        assert body.scroll_y > 0
        assert modal._cursor == 0
        assert ctx.initial_theme == "carbon"
        # Keyboard input regains a visible selection after independent mouse scrolling.
        await pilot.press("down")
        await pilot.pause()
        assert modal._cursor == 1
        assert ctx.initial_theme == assert_cursor_visible(modal)
        await pilot.press("escape")
        assert app._crash_report is None


@pytest.mark.parametrize("size", [(80, 24), (120, 40)])
async def test_mouse_can_scroll_to_and_commit_a_custom_theme(many_themes, size):
    app, ctx = many_themes()
    async with app.run_test(size=size) as pilot:
        await pilot.press("t")
        await pilot.pause()
        modal = app.screen
        assert isinstance(modal, ThemePickerModal)
        body = modal.query_one(VerticalScroll)
        x, y = body.content_region.center
        driver = app._driver
        assert driver is not None
        for _ in range(4):
            driver.process_message(MouseScrollDown(None, x, y, 0, 1, 0, False, False, False))
        await wait_until(
            lambda: body.scroll_y == body.max_scroll_y, what="wheel reaches last theme"
        )
        await pilot.pause()
        row = modal._rows[-1]
        assert body.content_region.contains_region(row.region)
        assert await pilot.click(row, offset=(3, 0))
        await wait_until(
            lambda: (
                not isinstance(app.screen, ThemePickerModal) and ctx.initial_theme == "custom-09"
            ),
            what="mouse commits the clicked custom theme",
        )
        assert ctx.root_vm.chrome.toast_stack.toasts[-1].model.id == "theme-changed-custom-09"
        assert app._crash_report is None
