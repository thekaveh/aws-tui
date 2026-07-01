"""Regression tests for singular S3/nav visual focus selection."""

from __future__ import annotations

import pytest

from aws_tui.app import AwsTuiApp
from aws_tui.composition import build_app_context
from aws_tui.ui.widgets.nav_row import NavRow
from aws_tui.ui.widgets.pane import Pane
from aws_tui.vm.chrome.focus_coordinator_vm import FocusSlot
from tests.integration.test_settings_flow import (
    _MINIO_LOCAL_TOML,
    _await_boot,
    _dispose,
    _prep,
)


def _selected_nav_rows(app: AwsTuiApp) -> list[NavRow]:
    return [row for row in app.query(NavRow) if "-selected" in row.classes]


def _focused_panes(app: AwsTuiApp) -> list[Pane]:
    return [pane for pane in app.query(Pane) if "-focused" in pane.classes]


def _assert_one_visual_focus(app: AwsTuiApp, *, slot: FocusSlot) -> None:
    selected_nav = _selected_nav_rows(app)
    focused_panes = _focused_panes(app)
    assert len(selected_nav) + len(focused_panes) == 1
    if slot is FocusSlot.NAV_MENU:
        assert "-rail-active" in app.screen.classes
        assert [row.descriptor_id for row in selected_nav] == ["s3"]
        assert focused_panes == []
    elif slot is FocusSlot.S3_LEFT:
        assert "-rail-active" not in app.screen.classes
        assert selected_nav == []
        assert [pane.id for pane in focused_panes] == ["pane-left"]
    elif slot is FocusSlot.S3_RIGHT:
        assert "-rail-active" not in app.screen.classes
        assert selected_nav == []
        assert [pane.id for pane in focused_panes] == ["pane-right"]
    else:  # pragma: no cover - helper is S3-only by design
        raise AssertionError(f"unexpected S3 focus slot {slot!r}")


@pytest.mark.asyncio
async def test_s3_launch_and_tab_cycle_have_one_visual_focus(
    tmp_path,
) -> None:
    config_dir = _prep(tmp_path, _MINIO_LOCAL_TOML + '\n[defaults]\nconnection = "minio-local"\n')
    ctx = build_app_context(config_dir=config_dir, cache_dir=tmp_path / "cache")
    app = AwsTuiApp(ctx)
    try:
        async with app.run_test(size=(120, 40)) as pilot:
            await _await_boot(pilot, app)

            assert ctx.root_vm.services_menu.selected_id == "s3"
            assert ctx.focus_coordinator.focused_slot is FocusSlot.S3_LEFT
            _assert_one_visual_focus(app, slot=FocusSlot.S3_LEFT)

            await pilot.press("tab")
            await pilot.pause()
            assert ctx.focus_coordinator.focused_slot is FocusSlot.S3_RIGHT
            _assert_one_visual_focus(app, slot=FocusSlot.S3_RIGHT)

            await pilot.press("tab")
            await pilot.pause()
            assert ctx.focus_coordinator.focused_slot is FocusSlot.NAV_MENU
            _assert_one_visual_focus(app, slot=FocusSlot.NAV_MENU)

            await pilot.press("tab")
            await pilot.pause()
            assert ctx.focus_coordinator.focused_slot is FocusSlot.S3_LEFT
            _assert_one_visual_focus(app, slot=FocusSlot.S3_LEFT)
    finally:
        _dispose(ctx)


@pytest.mark.asyncio
async def test_s3_shift_tab_uses_reverse_visual_focus_cycle(
    tmp_path,
) -> None:
    config_dir = _prep(tmp_path, _MINIO_LOCAL_TOML + '\n[defaults]\nconnection = "minio-local"\n')
    ctx = build_app_context(config_dir=config_dir, cache_dir=tmp_path / "cache")
    app = AwsTuiApp(ctx)
    try:
        async with app.run_test(size=(120, 40)) as pilot:
            await _await_boot(pilot, app)

            assert ctx.focus_coordinator.focused_slot is FocusSlot.S3_LEFT
            _assert_one_visual_focus(app, slot=FocusSlot.S3_LEFT)

            await pilot.press("shift+tab")
            await pilot.pause()
            assert ctx.focus_coordinator.focused_slot is FocusSlot.NAV_MENU
            _assert_one_visual_focus(app, slot=FocusSlot.NAV_MENU)

            await pilot.press("shift+tab")
            await pilot.pause()
            assert ctx.focus_coordinator.focused_slot is FocusSlot.S3_RIGHT
            _assert_one_visual_focus(app, slot=FocusSlot.S3_RIGHT)

            await pilot.press("shift+tab")
            await pilot.pause()
            assert ctx.focus_coordinator.focused_slot is FocusSlot.S3_LEFT
            _assert_one_visual_focus(app, slot=FocusSlot.S3_LEFT)
    finally:
        _dispose(ctx)


@pytest.mark.asyncio
async def test_arrow_walking_back_to_s3_keeps_visual_focus_on_nav(
    tmp_path,
) -> None:
    config_dir = _prep(tmp_path, _MINIO_LOCAL_TOML + '\n[defaults]\nconnection = "minio-local"\n')
    ctx = build_app_context(config_dir=config_dir, cache_dir=tmp_path / "cache")
    app = AwsTuiApp(ctx)
    try:
        async with app.run_test(size=(120, 40)) as pilot:
            await _await_boot(pilot, app)

            await pilot.press("tab")
            await pilot.pause()
            await pilot.press("tab")
            await pilot.pause()
            assert ctx.focus_coordinator.focused_slot is FocusSlot.NAV_MENU

            await pilot.press("down")
            await pilot.pause()
            assert ctx.root_vm.services_menu.selected_id == "settings"
            await pilot.press("up")
            await pilot.pause()

            assert ctx.root_vm.services_menu.selected_id == "s3"
            assert ctx.focus_coordinator.focused_slot is FocusSlot.NAV_MENU
            _assert_one_visual_focus(app, slot=FocusSlot.NAV_MENU)
    finally:
        _dispose(ctx)


@pytest.mark.asyncio
async def test_enter_on_active_s3_from_nav_highlights_left_pane(
    tmp_path,
) -> None:
    config_dir = _prep(tmp_path, _MINIO_LOCAL_TOML + '\n[defaults]\nconnection = "minio-local"\n')
    ctx = build_app_context(config_dir=config_dir, cache_dir=tmp_path / "cache")
    app = AwsTuiApp(ctx)
    try:
        async with app.run_test(size=(120, 40)) as pilot:
            await _await_boot(pilot, app)

            await pilot.press("tab")
            await pilot.pause()
            await pilot.press("tab")
            await pilot.pause()
            assert ctx.root_vm.services_menu.selected_id == "s3"
            assert ctx.focus_coordinator.focused_slot is FocusSlot.NAV_MENU
            _assert_one_visual_focus(app, slot=FocusSlot.NAV_MENU)

            await pilot.press("enter")
            await pilot.pause()

            assert ctx.root_vm.services_menu.selected_id == "s3"
            assert ctx.focus_coordinator.focused_slot is FocusSlot.S3_LEFT
            _assert_one_visual_focus(app, slot=FocusSlot.S3_LEFT)
    finally:
        _dispose(ctx)


@pytest.mark.asyncio
async def test_enter_on_s3_from_nav_when_vm_already_left_repaints_left_pane(
    tmp_path,
) -> None:
    config_dir = _prep(tmp_path, _MINIO_LOCAL_TOML + '\n[defaults]\nconnection = "minio-local"\n')
    ctx = build_app_context(config_dir=config_dir, cache_dir=tmp_path / "cache")
    app = AwsTuiApp(ctx)
    try:
        async with app.run_test(size=(120, 40)) as pilot:
            await _await_boot(pilot, app)

            assert ctx.focus_coordinator.focused_slot is FocusSlot.S3_LEFT
            await pilot.press("shift+tab")
            await pilot.pause()
            assert ctx.root_vm.services_menu.selected_id == "s3"
            assert ctx.focus_coordinator.focused_slot is FocusSlot.NAV_MENU
            _assert_one_visual_focus(app, slot=FocusSlot.NAV_MENU)

            await pilot.press("enter")
            await pilot.pause()

            assert ctx.root_vm.services_menu.selected_id == "s3"
            assert ctx.focus_coordinator.focused_slot is FocusSlot.S3_LEFT
            _assert_one_visual_focus(app, slot=FocusSlot.S3_LEFT)
    finally:
        _dispose(ctx)
