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


def _make_vm_with_hub(tmp_path: Path) -> tuple[NavMenuVM, MessageHub[Message]]:
    """Build a NavMenuVM and return it together with its hub.

    The hub is shared with the widget under test (returned to the
    caller so the test can pass the SAME hub into ``NavMenu(...)``).
    Without sharing, the widget's hub-subscription would never observe
    the VM's PropertyChangedMessage broadcasts.
    """
    hub = _hub()
    registry = ServiceRegistry()
    vm = NavMenuVM(registry=registry, hub=hub, dispatcher=NULL_DISPATCHER)
    vm.construct()
    # Trigger _rebuild_items() so the hard-coded Settings item is populated.
    # Without a connection change the VM starts with an empty items list;
    # sending ConnectionListChangedMessage mimics what the app does at
    # startup when S3 connections are loaded.
    hub.send(ConnectionListChangedMessage(names=(), change="added"))
    return vm, hub


def test_nav_menu_can_be_constructed(tmp_path: Path) -> None:
    vm, hub = _make_vm_with_hub(tmp_path)
    try:
        widget = NavMenu(vm=vm, hub=hub)
        # Default state: rail is collapsed (icons-only); the synthetic
        # Settings nav peer is always present so a first-time user with
        # no connection configured can still reach Settings to add one.
        assert widget.is_collapsed is True
        assert len(vm.items) >= 1
    finally:
        vm.dispose()


@pytest.mark.asyncio
async def test_nav_menu_renders_settings_item_in_options(tmp_path: Path) -> None:
    """The Settings nav item must be visible in the pinned OptionList."""
    vm, hub = _make_vm_with_hub(tmp_path)

    class _Host(App[None]):
        def __init__(self, w: NavMenu) -> None:
            super().__init__()
            self._w = w

        def compose(self) -> ComposeResult:
            yield self._w

    nav = NavMenu(vm=vm, hub=hub)
    app = _Host(nav)
    try:
        async with app.run_test() as pilot:
            await pilot.pause()
            from textual.widgets import OptionList

            # Settings lives in the docked-bottom pinned list, not in
            # the services list. The split into two OptionLists is what
            # gives the rail its "Settings docked at the bottom" layout.
            pinned = nav.query_one("#menu-pinned", OptionList)
            # Force-expand so labels (not just icons) are present.
            nav.toggle_collapsed()
            await pilot.pause()
            prompts = [
                str(pinned.get_option_at_index(i).prompt) for i in range(pinned.option_count)
            ]
            assert any("Settings" in p for p in prompts), prompts
    finally:
        vm.dispose()


@pytest.mark.asyncio
async def test_nav_menu_collapsed_shows_icon_only(tmp_path: Path) -> None:
    """In collapsed mode the OptionList prompts are icon glyphs only."""
    vm, hub = _make_vm_with_hub(tmp_path)

    class _Host(App[None]):
        def __init__(self, w: NavMenu) -> None:
            super().__init__()
            self._w = w

        def compose(self) -> ComposeResult:
            yield self._w

    nav = NavMenu(vm=vm, hub=hub)
    app = _Host(nav)
    try:
        async with app.run_test() as pilot:
            await pilot.pause()
            # NavMenu starts collapsed by default.
            assert nav.is_collapsed is True
            from textual.widgets import OptionList

            pinned = nav.query_one("#menu-pinned", OptionList)
            prompts = [
                str(pinned.get_option_at_index(i).prompt) for i in range(pinned.option_count)
            ]
            # In collapsed mode, "Settings" should NOT appear; "⚙" SHOULD.
            assert not any("Settings" in p for p in prompts), prompts
            assert any("⚙" in p for p in prompts), prompts
    finally:
        vm.dispose()


@pytest.mark.asyncio
async def test_nav_menu_selection_updates_vm(tmp_path: Path) -> None:
    """Selecting an option routes through switch_service_command and updates selected_id."""
    vm, hub = _make_vm_with_hub(tmp_path)

    class _Host(App[None]):
        def __init__(self, w: NavMenu) -> None:
            super().__init__()
            self._w = w

        def compose(self) -> ComposeResult:
            yield self._w

    nav = NavMenu(vm=vm, hub=hub)
    app = _Host(nav)
    try:
        async with app.run_test() as pilot:
            await pilot.pause()
            from textual.widgets import OptionList

            # Settings lives in the pinned (bottom) list now.
            pinned = nav.query_one("#menu-pinned", OptionList)
            settings_option = pinned.get_option_at_index(0)
            pinned.post_message(OptionList.OptionSelected(pinned, settings_option, 0))
            await pilot.pause()
            # selected_id reflects the item's descriptor.id after command runs.
            assert vm.selected_id == "settings"
    finally:
        vm.dispose()


@pytest.mark.asyncio
async def test_nav_menu_rebuilds_options_when_vm_items_change(tmp_path: Path) -> None:
    """Regression: NavMenu must subscribe to NavMenuVM's PropertyChangedMessage
    broadcasts so the OptionList re-renders when items change (e.g., after a
    connection switch alters which services support the new connection).

    Without the subscription, the rail shows stale items — the docstring
    claimed to handle this but no subscription was actually wired up.
    """
    vm, hub = _make_vm_with_hub(tmp_path)

    class _Host(App[None]):
        def __init__(self, w: NavMenu) -> None:
            super().__init__()
            self._w = w

        def compose(self) -> ComposeResult:
            yield self._w

    nav = NavMenu(vm=vm, hub=hub)
    app = _Host(nav)
    # Spy on _rebuild_options so the assertion proves the
    # PropertyChangedMessage subscription actually invoked the
    # widget's rebuild handler. Counting calls is robust against
    # refactors that change option_count behaviour (e.g. caching
    # in the inner OptionList).
    rebuild_calls: list[None] = []
    original_rebuild = nav._rebuild_options

    def _spy() -> None:
        rebuild_calls.append(None)
        original_rebuild()

    nav._rebuild_options = _spy  # type: ignore[method-assign]
    try:
        async with app.run_test() as pilot:
            await pilot.pause()
            from textual.widgets import OptionList
            from vmx import PropertyChangedMessage

            # Settings lives in #menu-pinned; service items (none here
            # because no service is registered) would land in #menu-services.
            pinned = nav.query_one("#menu-pinned", OptionList)
            calls_before = len(rebuild_calls)
            # Fire the exact message NavMenuVM._rebuild_items emits when
            # its items collection changes. Calling _rebuild_items via
            # ConnectionListChangedMessage doesn't help in this setup
            # because no service is registered, so the desired vs current
            # diff is empty and _rebuild_items early-returns. Driving the
            # PropertyChangedMessage directly tests the SUBSCRIBER path
            # in isolation — which is what the regression is about.
            hub.send(PropertyChangedMessage.create(vm, vm.name, "items"))
            await pilot.pause()
            assert len(rebuild_calls) > calls_before, (
                "NavMenu._rebuild_options was not called after a "
                "PropertyChangedMessage('items') from the VM — the hub "
                "subscription wired in on_mount is missing or broken."
            )
            # Settings is still present in the pinned list (rebuild
            # was non-destructive — same VM items, same render).
            prompts = [
                str(pinned.get_option_at_index(i).prompt) for i in range(pinned.option_count)
            ]
            assert any("⚙" in p or "Settings" in p for p in prompts), prompts
    finally:
        vm.dispose()
