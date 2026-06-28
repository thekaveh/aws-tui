"""Tests for NavMenu (NavRow-based vertical nav).

Post-PR-#94 NavMenu hosts :class:`NavRow` widgets directly — no
OptionList. The rail is itself the focusable widget; arrow keys
move a cursor index across the flat list of services + Settings,
and each cursor move executes ``switch_service_command``
(master-detail). These tests pin that contract.
"""

from __future__ import annotations

from pathlib import Path
from typing import cast

import pytest
from textual.app import App, ComposeResult
from vmx import NULL_DISPATCHER, MessageHub
from vmx.messages.protocols import Message

from aws_tui.infra.connection_resolver import Connection
from aws_tui.ui.widgets.nav_menu import NavMenu
from aws_tui.ui.widgets.nav_row import NavRow
from aws_tui.vm.messages import ConnectionListChangedMessage
from aws_tui.vm.nav_menu_vm import NavMenuVM
from aws_tui.vm.services_protocol import ServiceDescriptor, ServiceRegistry


def _hub() -> MessageHub[Message]:
    return cast("MessageHub[Message]", MessageHub())


def _make_vm_with_hub(tmp_path: Path) -> tuple[NavMenuVM, MessageHub[Message]]:
    hub = _hub()
    registry = ServiceRegistry()
    vm = NavMenuVM(registry=registry, hub=hub, dispatcher=NULL_DISPATCHER)
    vm.construct()
    hub.send(ConnectionListChangedMessage(names=(), change="added"))
    return vm, hub


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


def _vm_with_services() -> tuple[NavMenuVM, MessageHub[Message]]:
    hub = _hub()
    registry = ServiceRegistry()
    registry.register(_S3Stub())
    registry.register(_EmrStub())
    vm = NavMenuVM(registry=registry, hub=hub, dispatcher=NULL_DISPATCHER)
    vm.construct()
    vm.update_connection(
        Connection(name="dev", kind="aws", region="us-east-1", source="config", profile="dev")
    )
    return vm, hub


class _Host(App[None]):
    def __init__(self, w: NavMenu) -> None:
        super().__init__()
        self._w = w

    def compose(self) -> ComposeResult:
        yield self._w


def test_nav_menu_can_be_constructed(tmp_path: Path) -> None:
    vm, hub = _make_vm_with_hub(tmp_path)
    try:
        NavMenu(vm=vm, hub=hub)
        # Settings is always present.
        assert len(vm.items) >= 1
    finally:
        vm.dispose()


@pytest.mark.asyncio
async def test_nav_menu_renders_a_settings_navrow(tmp_path: Path) -> None:
    """The Settings entry must be mounted as a NavRow inside the
    Settings container (which the flex spacer pushes to the bottom).
    """
    vm, hub = _make_vm_with_hub(tmp_path)
    nav = NavMenu(vm=vm, hub=hub)
    app = _Host(nav)
    try:
        async with app.run_test() as pilot:
            await pilot.pause()
            rows = list(nav.query(NavRow))
            assert any(r.descriptor_id == "settings" for r in rows), (
                f"Settings NavRow missing; rows: {[r.descriptor_id for r in rows]}"
            )
    finally:
        vm.dispose()


@pytest.mark.asyncio
async def test_clicking_settings_executes_switch_service_command(tmp_path: Path) -> None:
    """Click a NavRow → cursor moves + switch_service_command fires."""
    vm, hub = _make_vm_with_hub(tmp_path)
    nav = NavMenu(vm=vm, hub=hub)
    app = _Host(nav)
    try:
        async with app.run_test() as pilot:
            await pilot.pause()
            settings_row = next(r for r in nav.query(NavRow) if r.descriptor_id == "settings")
            await pilot.click(settings_row)
            await pilot.pause()
            assert vm.selected_id == "settings"
    finally:
        vm.dispose()


@pytest.mark.asyncio
async def test_nav_menu_rebuilds_rows_when_vm_items_change(tmp_path: Path) -> None:
    """NavMenu must re-mount its rows when the VM's items list
    changes (typically after a connection swap drops or adds
    services). Without this the rail goes stale."""
    vm, hub = _make_vm_with_hub(tmp_path)
    nav = NavMenu(vm=vm, hub=hub)
    app = _Host(nav)
    rebuild_calls: list[None] = []
    original = nav._rebuild_rows  # type: ignore[attr-defined]

    def _spy() -> None:
        rebuild_calls.append(None)
        original()

    nav._rebuild_rows = _spy  # type: ignore[method-assign]
    try:
        async with app.run_test() as pilot:
            await pilot.pause()
            from vmx import PropertyChangedMessage

            before = len(rebuild_calls)
            hub.send(PropertyChangedMessage.create(vm, vm.name, "items"))
            await pilot.pause()
            assert len(rebuild_calls) > before, (
                "NavMenu._rebuild_rows must be called after a "
                "PropertyChangedMessage('items') from the VM."
            )
            # Settings still in the rail.
            assert any(r.descriptor_id == "settings" for r in nav.query(NavRow))
    finally:
        vm.dispose()


# ── Master-detail: arrow keys switch service immediately ──────────────────────


@pytest.mark.asyncio
async def test_cursor_down_executes_switch_service_command() -> None:
    """User feedback: "I should be able to up or down arrow key
    among the menu items and in doing so change the selected item
    which should result in changing the service as selected item".

    Two registered services + Settings = three rows. Sending the
    ``action_cursor_down`` action must move the cursor AND fire
    ``switch_service_command`` for the new row.
    """
    vm, hub = _vm_with_services()
    nav = NavMenu(vm=vm, hub=hub)
    app = _Host(nav)
    try:
        async with app.run_test() as pilot:
            await pilot.pause()
            # Three rows: s3 (sorted alpha by registry insert order),
            # emr-serverless, settings.
            rows = list(nav.query(NavRow))
            assert len(rows) >= 2, f"Expected ≥2 rows, got {len(rows)}"
            # Spy on the command.
            calls: list[object] = []
            original_execute = vm.switch_service_command.execute

            def _spy(*a: object, **kw: object) -> object:
                calls.append(a)
                return original_execute(*a, **kw)

            vm.switch_service_command.execute = _spy  # type: ignore[method-assign]
            # Start at row 0, move down to row 1 — the cursor change
            # should fire one switch_service_command execution.
            nav._cursor_index = 0  # type: ignore[attr-defined]
            nav.action_cursor_down()
            await pilot.pause()
            assert calls, "action_cursor_down should execute switch_service_command"
            # Sanity: no infinite cascade. One arrow press should
            # produce at most a small number of executes.
            assert len(calls) < 10
    finally:
        vm.dispose()


@pytest.mark.asyncio
async def test_cursor_can_reach_settings_row_via_arrow_keys() -> None:
    """User feedback: "I expect to be able to easily move between
    the menu items using arrow keys (including the settings item)".

    Pre-PR-#94 Settings lived in its own OptionList; arrow keys in
    one list never crossed into the other. The flat-cursor design
    means pressing Down from the last service must land on
    Settings.
    """
    vm, hub = _vm_with_services()
    nav = NavMenu(vm=vm, hub=hub)
    app = _Host(nav)
    try:
        async with app.run_test() as pilot:
            await pilot.pause()
            # The items list contains services + Settings. Cursor
            # walking from 0 to len-1 must reach the Settings row.
            items = nav._items  # type: ignore[attr-defined]
            assert items, "items must be populated after rebuild"
            settings_idx = next(
                (i for i, it in enumerate(items) if it.descriptor.id == "settings"),
                None,
            )
            assert settings_idx is not None, (
                f"Settings missing from cursor-navigable items: {items!r}"
            )
            # Walk down to Settings.
            nav._cursor_index = 0  # type: ignore[attr-defined]
            for _ in range(settings_idx):
                nav.action_cursor_down()
                await pilot.pause()
            assert vm.selected_id == "settings", (
                "Arrow-walking down should land on Settings and switch_service_command should fire."
            )
    finally:
        vm.dispose()
