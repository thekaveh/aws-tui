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
        NavMenu(vm=vm, hub=hub)
        # The synthetic Settings nav peer is always present so a
        # first-time user with no connection configured can still
        # reach Settings to add one.
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
            prompts = [
                str(pinned.get_option_at_index(i).prompt) for i in range(pinned.option_count)
            ]
            # Post-PR-#94: prompts are text labels only — "Settings"
            # appears verbatim; no icon prefix.
            assert any("Settings" in p for p in prompts), prompts
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
            assert any("Settings" in p for p in prompts), prompts
    finally:
        vm.dispose()


# ── Master-detail: arrow-key highlight switches service ──────────────────


@pytest.mark.asyncio
async def test_highlight_change_executes_switch_service_command(tmp_path: Path) -> None:
    """User feedback (post-PR-#93): "I should be able to up or down
    arrow key among the menu items and in doing so change the
    selected item which should result in changing the service".

    Pin the master-detail contract: moving the OptionList highlight
    to a different non-spacer row fires ``switch_service_command``
    immediately — not just on Enter / click. The
    ``_suppress_highlight_switch`` guard prevents the programmatic
    highlight restore inside ``_rebuild_options`` from re-firing
    the command (verified by the "loops forever" check below).
    """
    from textual.widgets import OptionList

    from aws_tui.vm.services_protocol import ServiceDescriptor

    class _S3Stub:
        descriptor = ServiceDescriptor(id="s3", label="S3", icon="")

        def supports(self, conn: object) -> bool:
            return True

        def build_vm(self, conn: object) -> object:
            return object()

    class _EmrStub:
        descriptor = ServiceDescriptor(id="emr-serverless", label="EMR", icon="")

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
            # Two services + 1 spacer = 3 rows.
            assert services.option_count == 3
            # Spy on the command so we can count executions.
            calls: list[object] = []
            original = vm.switch_service_command.execute

            def _spy(*a: object, **kw: object) -> object:
                calls.append((a, kw))
                return original(*a, **kw)

            vm.switch_service_command.execute = _spy  # type: ignore[method-assign]
            # Move highlight to the EMR row (idx=2; idx=1 is the
            # PR-#81 spacer). The handler must fire one execute() AND
            # NOT loop infinitely via the rebuild → highlight cascade.
            services.highlighted = 2
            await pilot.pause()
            assert any(call[0] == ("emr-serverless",) for call in calls), (
                f"Highlight change should execute switch_service_command "
                f"with 'emr-serverless'; got calls={calls!r}."
            )
            # No infinite cascade — the master-detail switch fires a
            # rebuild, which restores the highlight, which must NOT
            # re-fire the switch. One unique target service id should
            # have at most a few execute calls (one user-driven + at
            # most one from rebuild restoration, depending on
            # propagation order). Loose upper bound to keep the test
            # robust against benign re-broadcasts.
            assert len(calls) < 10, (
                f"Highlight cascade looped: {len(calls)} executes "
                f"(expected ≤ 1-2). The _suppress_highlight_switch "
                "guard must be in place."
            )
    finally:
        vm.dispose()
