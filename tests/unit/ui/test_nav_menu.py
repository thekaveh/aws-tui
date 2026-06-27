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


# ── Highlight-driven tooltip lookup ───────────────────────────────────────


@pytest.mark.asyncio
async def test_highlight_tooltip_maps_via_option_id_not_index(tmp_path: Path) -> None:
    """Pin the per-row tooltip lookup against the PR #81 spacer
    offset.

    PR #81 inserts a blank disabled spacer ``Option`` between
    consecutive nav-rail rows for vertical breathing room. Pre-fix,
    ``on_option_list_option_highlighted`` mapped the OptionList
    cursor index back to ``self._vm.items[idx]`` — but the OptionList
    layout is ``[item, spacer, item, spacer, …]`` while the VM list
    is ``[item, item, …]``. So highlighting the second service row
    (OptionList idx=2) looked up VM idx=2 → IndexError → tooltip
    cleared; and worse, highlighting the spacer between them
    (idx=1) showed the SECOND service's label. The user-visible
    symptom: hovering the EMR row showed "S3" (or no tooltip),
    never "EMR".

    The fix maps via ``event.option_id`` (each non-spacer Option
    has its descriptor id) and looks the NavItemVM up by id. This
    test pins that mapping with TWO registered services so the
    spacer is actually present in the rendered OptionList.
    """
    from textual.widgets import OptionList

    from aws_tui.vm.services_protocol import ServiceDescriptor

    class _S3Stub:
        descriptor = ServiceDescriptor(id="s3", label="S3", icon="🪣")

        def supports(self, conn: object) -> bool:
            return True

        def build_vm(self, conn: object) -> object:
            return object()

    class _EmrStub:
        descriptor = ServiceDescriptor(id="emr-serverless", label="EMR", icon="🔥")

        def supports(self, conn: object) -> bool:
            return True

        def build_vm(self, conn: object) -> object:
            return object()

    hub = _hub()
    registry = ServiceRegistry()
    registry.register(_S3Stub())
    registry.register(_EmrStub())
    vm = NavMenuVM(registry=registry, hub=hub, dispatcher=NULL_DISPATCHER)
    vm.construct()
    # Drive a connection broadcast so both services land in the
    # nav rail.
    from aws_tui.infra.connection_resolver import Connection

    vm.update_connection(
        Connection(
            name="dev",
            kind="aws",
            region="us-east-1",
            source="config",
            profile="dev",
        )
    )

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
            services = nav.query_one("#menu-services", OptionList)
            # OptionList layout for two services: [s3, spacer, emr]
            # = 3 rows. The spacer is the PR #81 breathing-room
            # ``Option(" ", disabled=True)``.
            assert services.option_count == 3, (
                f"Expected [s3, spacer, emr] = 3 rows; got {services.option_count}."
            )

            # Highlight each non-spacer row and verify the tooltip
            # lookup resolves to the right label. ``highlighted = idx``
            # is the public Textual API; setting it triggers the
            # OptionHighlighted message which fires our handler.
            services.highlighted = 0  # s3 row
            await pilot.pause()
            assert services.tooltip == "S3", (
                f"Expected 'S3' tooltip on the s3 row; got {services.tooltip!r}."
            )

            services.highlighted = 2  # emr row (idx=1 is the spacer)
            await pilot.pause()
            assert services.tooltip == "EMR", (
                f"Expected 'EMR' tooltip on the emr row; got {services.tooltip!r}. "
                "Pre-fix the index-based lookup mapped this back to "
                "self._vm.items[2] (out of range) → cleared the tooltip, "
                "OR the user saw the s3 'S3' tooltip stuck from the prior "
                "highlight."
            )
    finally:
        vm.dispose()
