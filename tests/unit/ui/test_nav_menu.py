"""Tests for NavMenu (OptionList-based vertical nav)."""

from __future__ import annotations

from pathlib import Path
from typing import cast

import pytest
from textual.app import App, ComposeResult
from vmx import NULL_DISPATCHER, MessageHub
from vmx.messages.protocols import Message

from aws_tui.ui.widgets.nav_menu import NavMenu
from aws_tui.vm.messages import ConnectionListChangedMessage
from aws_tui.vm.nav_menu_vm import NavMenuVM
from aws_tui.vm.services_protocol import ServiceRegistry


def _hub() -> MessageHub[Message]:
    return cast("MessageHub[Message]", MessageHub())


def _make_vm(tmp_path: Path) -> NavMenuVM:
    hub = _hub()
    registry = ServiceRegistry()
    vm = NavMenuVM(registry=registry, hub=hub, dispatcher=NULL_DISPATCHER)
    vm.construct()
    # Trigger _rebuild_items() so the hard-coded Settings item is populated.
    # Without a connection change the VM starts with an empty items list;
    # sending ConnectionListChangedMessage mimics what the app does at
    # startup when S3 connections are loaded.
    hub.send(ConnectionListChangedMessage(names=(), change="added"))
    return vm


def test_nav_menu_can_be_constructed(tmp_path: Path) -> None:
    vm = _make_vm(tmp_path)
    try:
        widget = NavMenu(vm=vm, hub=_hub())
        assert widget is not None
        assert widget.is_collapsed is True  # default starts collapsed
        assert len(vm.items) >= 1  # Settings is always present
    finally:
        vm.dispose()


@pytest.mark.asyncio
async def test_nav_menu_renders_settings_item_in_options(tmp_path: Path) -> None:
    """The Settings nav item must be visible in the OptionList prompts."""
    vm = _make_vm(tmp_path)

    class _Host(App[None]):
        def __init__(self, w: NavMenu) -> None:
            super().__init__()
            self._w = w

        def compose(self) -> ComposeResult:
            yield self._w

    nav = NavMenu(vm=vm, hub=_hub())
    app = _Host(nav)
    try:
        async with app.run_test() as pilot:
            await pilot.pause()
            from textual.widgets import OptionList

            ol = nav.query_one(OptionList)
            # Force-expand so labels (not just icons) are present.
            nav.toggle_collapsed()
            await pilot.pause()
            prompts = [str(opt.prompt) for opt in ol._options]
            # The Settings prompt should contain "Settings" when expanded.
            assert any("Settings" in p for p in prompts), prompts
    finally:
        vm.dispose()


@pytest.mark.asyncio
async def test_nav_menu_collapsed_shows_icon_only(tmp_path: Path) -> None:
    """In collapsed mode the OptionList prompts are icon glyphs only."""
    vm = _make_vm(tmp_path)

    class _Host(App[None]):
        def __init__(self, w: NavMenu) -> None:
            super().__init__()
            self._w = w

        def compose(self) -> ComposeResult:
            yield self._w

    nav = NavMenu(vm=vm, hub=_hub())
    app = _Host(nav)
    try:
        async with app.run_test() as pilot:
            await pilot.pause()
            # NavMenu starts collapsed by default.
            assert nav.is_collapsed is True
            from textual.widgets import OptionList

            ol = nav.query_one(OptionList)
            prompts = [str(opt.prompt) for opt in ol._options]
            # In collapsed mode, "Settings" should NOT appear; "⚙" SHOULD.
            assert not any("Settings" in p for p in prompts), prompts
            assert any("⚙" in p for p in prompts), prompts
    finally:
        vm.dispose()


@pytest.mark.asyncio
async def test_nav_menu_selection_updates_vm(tmp_path: Path) -> None:
    """Selecting an option routes through switch_service_command and updates selected_id."""
    vm = _make_vm(tmp_path)

    class _Host(App[None]):
        def __init__(self, w: NavMenu) -> None:
            super().__init__()
            self._w = w

        def compose(self) -> ComposeResult:
            yield self._w

    nav = NavMenu(vm=vm, hub=_hub())
    app = _Host(nav)
    try:
        async with app.run_test() as pilot:
            await pilot.pause()
            from textual.widgets import OptionList

            ol = nav.query_one(OptionList)
            # No connection is set so only the Settings item is present (index 0).
            settings_option = ol.get_option_at_index(0)
            ol.post_message(OptionList.OptionSelected(ol, settings_option, 0))
            await pilot.pause()
            # selected_id reflects the item's descriptor.id after command runs.
            assert vm.selected_id == "settings"
    finally:
        vm.dispose()
