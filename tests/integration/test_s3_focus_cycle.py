"""Regression tests for singular S3/nav visual focus selection."""

from __future__ import annotations

from pathlib import Path

import pytest

from aws_tui.app import AwsTuiApp
from aws_tui.composition import AppContext, build_app_context
from aws_tui.infra.aws_session import TokenState
from aws_tui.ui.widgets.nav_menu import NavMenu
from aws_tui.ui.widgets.nav_row import NavRow
from aws_tui.ui.widgets.pane import Pane
from aws_tui.vm.chrome.focus_coordinator_vm import FocusSlot
from tests.helpers import wait_until
from tests.integration.test_settings_flow import (
    _MINIO_LOCAL_TOML,
    _await_boot,
    _dispose,
    _prep,
)


def _make_fast_focus_app(tmp_path: Path) -> tuple[AppContext, AwsTuiApp]:
    """Build the real app chrome but bypass live S3 boot for focus tests."""
    config_dir = _prep(tmp_path, _MINIO_LOCAL_TOML + '\n[defaults]\nconnection = "minio-local"\n')
    ctx = build_app_context(config_dir=config_dir, cache_dir=tmp_path / "cache")
    app = AwsTuiApp(ctx)

    async def _fast_try_connection(conn: object, *, timeout: float = 90.0) -> str:
        del timeout
        await ctx.root_vm.switch_connection_with(conn, TokenState.CONNECTED)  # type: ignore[arg-type]
        ctx.root_vm.services_menu.switch_service_command.execute("s3")
        await app._mount_local_only_dual_pane(  # type: ignore[arg-type]
            initial_conn=conn,
            reason="test-focus",
        )
        ctx.focus_coordinator.set_focused_slot(FocusSlot.S3_LEFT)
        return "ok"

    app._try_connection = _fast_try_connection  # type: ignore[method-assign]
    return ctx, app


def _selected_nav_rows(app: AwsTuiApp) -> list[NavRow]:
    return [row for row in app.query(NavRow) if "-selected" in row.classes]


def _focused_panes(app: AwsTuiApp) -> list[Pane]:
    return [pane for pane in app.query(Pane) if "-focused" in pane.classes]


def _assert_one_visual_focus(app: AwsTuiApp, *, slot: FocusSlot) -> None:
    selected_nav = _selected_nav_rows(app)
    focused_panes = _focused_panes(app)
    assert [row.descriptor_id for row in selected_nav] == ["s3"]
    if slot is FocusSlot.NAV_MENU:
        assert "-rail-active" in app.screen.classes
        assert focused_panes == []
    elif slot is FocusSlot.S3_LEFT:
        assert "-rail-active" not in app.screen.classes
        assert [pane.id for pane in focused_panes] == ["pane-left"]
    elif slot is FocusSlot.S3_RIGHT:
        assert "-rail-active" not in app.screen.classes
        assert [pane.id for pane in focused_panes] == ["pane-right"]
    else:  # pragma: no cover - helper is S3-only by design
        raise AssertionError(f"unexpected S3 focus slot {slot!r}")


@pytest.mark.asyncio
async def test_s3_launch_and_tab_cycle_have_one_visual_focus(
    tmp_path,
) -> None:
    ctx, app = _make_fast_focus_app(tmp_path)
    try:
        async with app.run_test(size=(120, 40)) as pilot:
            await _await_boot(pilot, app)

            assert ctx.root_vm.services_menu.selected_id == "s3"
            assert ctx.focus_coordinator.focused_slot is FocusSlot.S3_LEFT
            _assert_one_visual_focus(app, slot=FocusSlot.S3_LEFT)

            await pilot.press("tab")
            await wait_until(
                lambda: (
                    (ctx.focus_coordinator.focused_slot is FocusSlot.S3_RIGHT)
                    and (
                        "-rail-active" not in app.screen.classes
                        and [row.descriptor_id for row in _selected_nav_rows(app)] == ["s3"]
                        and [pane.id for pane in _focused_panes(app)] == ["pane-right"]
                    )
                ),
                what="S3 right focus slot and visible border",
            )
            assert ctx.focus_coordinator.focused_slot is FocusSlot.S3_RIGHT
            _assert_one_visual_focus(app, slot=FocusSlot.S3_RIGHT)

            await pilot.press("tab")
            await wait_until(
                lambda: (
                    (ctx.focus_coordinator.focused_slot is FocusSlot.NAV_MENU)
                    and ("-rail-active" in app.screen.classes and not _focused_panes(app))
                    and [row.descriptor_id for row in _selected_nav_rows(app)] == ["s3"]
                ),
                what="navigation focus slot and visible rail",
            )
            assert ctx.focus_coordinator.focused_slot is FocusSlot.NAV_MENU
            _assert_one_visual_focus(app, slot=FocusSlot.NAV_MENU)

            await pilot.press("tab")
            await wait_until(
                lambda: (
                    (ctx.focus_coordinator.focused_slot is FocusSlot.S3_LEFT)
                    and (
                        "-rail-active" not in app.screen.classes
                        and [row.descriptor_id for row in _selected_nav_rows(app)] == ["s3"]
                        and [pane.id for pane in _focused_panes(app)] == ["pane-left"]
                    )
                ),
                what="S3 left focus slot and visible border",
            )
            assert ctx.focus_coordinator.focused_slot is FocusSlot.S3_LEFT
            _assert_one_visual_focus(app, slot=FocusSlot.S3_LEFT)
    finally:
        _dispose(ctx)


@pytest.mark.asyncio
async def test_s3_shift_tab_uses_reverse_visual_focus_cycle(
    tmp_path,
) -> None:
    ctx, app = _make_fast_focus_app(tmp_path)
    try:
        async with app.run_test(size=(120, 40)) as pilot:
            await _await_boot(pilot, app)

            assert ctx.focus_coordinator.focused_slot is FocusSlot.S3_LEFT
            _assert_one_visual_focus(app, slot=FocusSlot.S3_LEFT)

            await pilot.press("shift+tab")
            await wait_until(
                lambda: (
                    (ctx.focus_coordinator.focused_slot is FocusSlot.NAV_MENU)
                    and ("-rail-active" in app.screen.classes and not _focused_panes(app))
                    and [row.descriptor_id for row in _selected_nav_rows(app)] == ["s3"]
                ),
                what="reverse Tab to focus navigation and its rail",
            )
            assert ctx.focus_coordinator.focused_slot is FocusSlot.NAV_MENU
            _assert_one_visual_focus(app, slot=FocusSlot.NAV_MENU)

            await pilot.press("shift+tab")
            await wait_until(
                lambda: (
                    (ctx.focus_coordinator.focused_slot is FocusSlot.S3_RIGHT)
                    and (
                        "-rail-active" not in app.screen.classes
                        and [row.descriptor_id for row in _selected_nav_rows(app)] == ["s3"]
                        and [pane.id for pane in _focused_panes(app)] == ["pane-right"]
                    )
                ),
                what="reverse Tab to focus the S3 right pane and border",
            )
            assert ctx.focus_coordinator.focused_slot is FocusSlot.S3_RIGHT
            _assert_one_visual_focus(app, slot=FocusSlot.S3_RIGHT)

            await pilot.press("shift+tab")
            await wait_until(
                lambda: (
                    (ctx.focus_coordinator.focused_slot is FocusSlot.S3_LEFT)
                    and (
                        "-rail-active" not in app.screen.classes
                        and [row.descriptor_id for row in _selected_nav_rows(app)] == ["s3"]
                        and [pane.id for pane in _focused_panes(app)] == ["pane-left"]
                    )
                ),
                what="reverse Tab to focus the S3 left pane and border",
            )
            assert ctx.focus_coordinator.focused_slot is FocusSlot.S3_LEFT
            _assert_one_visual_focus(app, slot=FocusSlot.S3_LEFT)
    finally:
        _dispose(ctx)


@pytest.mark.asyncio
async def test_arrow_walking_back_to_s3_keeps_visual_focus_on_nav(
    tmp_path,
) -> None:
    ctx, app = _make_fast_focus_app(tmp_path)
    try:
        async with app.run_test(size=(120, 40)) as pilot:
            await _await_boot(pilot, app)

            await pilot.press("tab")
            await pilot.pause()
            await pilot.press("tab")
            await wait_until(
                lambda: (
                    (ctx.focus_coordinator.focused_slot is FocusSlot.NAV_MENU)
                    and ("-rail-active" in app.screen.classes and not _focused_panes(app))
                    and [row.descriptor_id for row in _selected_nav_rows(app)] == ["s3"]
                ),
                what="navigation focus slot and visible rail",
            )
            assert ctx.focus_coordinator.focused_slot is FocusSlot.NAV_MENU

            await pilot.press("down")
            await wait_until(
                lambda: (
                    (ctx.root_vm.services_menu.selected_id == "settings")
                    and (ctx.focus_coordinator.focused_slot is FocusSlot.NAV_MENU)
                    and (isinstance(app.focused, NavMenu))
                    and ("-rail-active" in app.screen.classes and not _focused_panes(app))
                ),
                what="Down to select Settings while retaining navigation focus",
            )
            assert ctx.root_vm.services_menu.selected_id == "settings"
            assert ctx.focus_coordinator.focused_slot is FocusSlot.NAV_MENU
            assert isinstance(app.focused, NavMenu)
            await pilot.press("up")
            await wait_until(
                lambda: (
                    (ctx.root_vm.services_menu.selected_id == "s3")
                    and (ctx.focus_coordinator.focused_slot is FocusSlot.NAV_MENU)
                    and ("-rail-active" in app.screen.classes and not _focused_panes(app))
                    and [row.descriptor_id for row in _selected_nav_rows(app)] == ["s3"]
                ),
                what="Up to select S3 while retaining the navigation rail",
            )

            assert ctx.root_vm.services_menu.selected_id == "s3"
            assert ctx.focus_coordinator.focused_slot is FocusSlot.NAV_MENU
            _assert_one_visual_focus(app, slot=FocusSlot.NAV_MENU)
    finally:
        _dispose(ctx)


@pytest.mark.asyncio
async def test_enter_on_active_s3_from_nav_highlights_left_pane(
    tmp_path,
) -> None:
    ctx, app = _make_fast_focus_app(tmp_path)
    try:
        async with app.run_test(size=(120, 40)) as pilot:
            await _await_boot(pilot, app)

            await pilot.press("tab")
            await pilot.pause()
            await pilot.press("tab")
            await wait_until(
                lambda: (
                    (ctx.root_vm.services_menu.selected_id == "s3")
                    and (ctx.focus_coordinator.focused_slot is FocusSlot.NAV_MENU)
                    and ("-rail-active" in app.screen.classes and not _focused_panes(app))
                    and [row.descriptor_id for row in _selected_nav_rows(app)] == ["s3"]
                ),
                what="navigation slot and rail after two Tabs",
            )
            assert ctx.root_vm.services_menu.selected_id == "s3"
            assert ctx.focus_coordinator.focused_slot is FocusSlot.NAV_MENU
            _assert_one_visual_focus(app, slot=FocusSlot.NAV_MENU)

            await pilot.press("enter")
            await wait_until(
                lambda: (
                    (ctx.root_vm.services_menu.selected_id == "s3")
                    and (ctx.focus_coordinator.focused_slot is FocusSlot.S3_LEFT)
                    and (
                        "-rail-active" not in app.screen.classes
                        and [row.descriptor_id for row in _selected_nav_rows(app)] == ["s3"]
                        and [pane.id for pane in _focused_panes(app)] == ["pane-left"]
                    )
                ),
                what="Enter on S3 to restore the left pane border",
            )

            assert ctx.root_vm.services_menu.selected_id == "s3"
            assert ctx.focus_coordinator.focused_slot is FocusSlot.S3_LEFT
            _assert_one_visual_focus(app, slot=FocusSlot.S3_LEFT)
    finally:
        _dispose(ctx)


@pytest.mark.asyncio
async def test_enter_on_s3_from_nav_when_vm_already_left_repaints_left_pane(
    tmp_path,
) -> None:
    ctx, app = _make_fast_focus_app(tmp_path)
    try:
        async with app.run_test(size=(120, 40)) as pilot:
            await _await_boot(pilot, app)

            assert ctx.focus_coordinator.focused_slot is FocusSlot.S3_LEFT
            await pilot.press("shift+tab")
            await wait_until(
                lambda: (
                    (ctx.root_vm.services_menu.selected_id == "s3")
                    and (ctx.focus_coordinator.focused_slot is FocusSlot.NAV_MENU)
                    and ("-rail-active" in app.screen.classes and not _focused_panes(app))
                    and [row.descriptor_id for row in _selected_nav_rows(app)] == ["s3"]
                ),
                what="reverse Tab to activate the navigation rail",
            )
            assert ctx.root_vm.services_menu.selected_id == "s3"
            assert ctx.focus_coordinator.focused_slot is FocusSlot.NAV_MENU
            _assert_one_visual_focus(app, slot=FocusSlot.NAV_MENU)

            await pilot.press("enter")
            await wait_until(
                lambda: (
                    (ctx.root_vm.services_menu.selected_id == "s3")
                    and (ctx.focus_coordinator.focused_slot is FocusSlot.S3_LEFT)
                    and (
                        "-rail-active" not in app.screen.classes
                        and [row.descriptor_id for row in _selected_nav_rows(app)] == ["s3"]
                        and [pane.id for pane in _focused_panes(app)] == ["pane-left"]
                    )
                ),
                what="Enter to repaint the S3 left pane border",
            )

            assert ctx.root_vm.services_menu.selected_id == "s3"
            assert ctx.focus_coordinator.focused_slot is FocusSlot.S3_LEFT
            _assert_one_visual_focus(app, slot=FocusSlot.S3_LEFT)
    finally:
        _dispose(ctx)
