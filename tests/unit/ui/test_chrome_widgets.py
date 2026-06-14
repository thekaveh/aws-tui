"""Smoke tests for chrome widgets.

We mount each widget inside a tiny test ``App`` driven by ``run_test``, then
assert the widget renders without error and reacts to VM state changes.
"""

from __future__ import annotations

import pytest
from textual.app import App, ComposeResult
from vmx import MessageHub, RxDispatcher

from aws_tui.infra.aws_session import TokenState
from aws_tui.infra.connection_resolver import Connection
from aws_tui.infra.keymap_store import KeymapStore
from aws_tui.ui.widgets.hint_legend import HintLegend
from aws_tui.ui.widgets.services_menu import ServicesMenu
from aws_tui.ui.widgets.status_bar import StatusBar
from aws_tui.ui.widgets.toast import ToastStack
from aws_tui.vm.chrome.hint_legend_vm import HintLegendVM
from aws_tui.vm.chrome.status_bar_vm import StatusBarVM
from aws_tui.vm.chrome.toast_stack_vm import ToastStackVM
from aws_tui.vm.chrome.toast_vm import ToastLevel, ToastModel
from aws_tui.vm.services_menu_vm import ServicesMenuVM
from aws_tui.vm.services_protocol import ServiceDescriptor, ServiceRegistry


class _S3Stub:
    descriptor = ServiceDescriptor(id="s3", label="S3", icon="S3")

    def supports(self, conn: object) -> bool:
        return True

    def build_vm(self, conn: object) -> object:
        return object()


class _EC2Stub:
    descriptor = ServiceDescriptor(id="ec2", label="EC2", icon="EC2")

    def supports(self, conn: object) -> bool:
        return getattr(conn, "kind", None) == "aws"

    def build_vm(self, conn: object) -> object:
        return object()


def _make_connection(kind: str = "aws") -> Connection:
    return Connection(
        name="kaveh-dev",
        kind=kind,
        region="us-east-1",
        source="config",
        profile="kaveh-dev" if kind == "aws" else None,
    )


# ── StatusBar ───────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_status_bar_mounts_and_reacts_to_connection_update() -> None:
    hub: MessageHub = MessageHub()
    dispatcher = RxDispatcher.immediate()
    vm = StatusBarVM(hub=hub, dispatcher=dispatcher)
    vm.construct()
    try:

        class _App(App[None]):
            def compose(self) -> ComposeResult:
                yield StatusBar(vm, hub=hub)

        app = _App()
        async with app.run_test() as pilot:
            await pilot.pause()
            assert app.query_one(StatusBar) is not None
            vm.update_connection(_make_connection(), TokenState.CONNECTED)
            await pilot.pause()
            assert "kaveh-dev" in vm.connection_label
    finally:
        vm.dispose()
        hub.dispose()


# ── HintLegend ──────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_hint_legend_renders_with_registered_actions() -> None:
    hub: MessageHub = MessageHub()
    dispatcher = RxDispatcher.immediate()
    keymap = KeymapStore()
    vm = HintLegendVM(hub=hub, dispatcher=dispatcher, keymap=keymap)
    vm.register_focusable("pane.left", ("pane.copy", "pane.move", "pane.delete"))
    vm.construct()
    try:

        class _App(App[None]):
            def compose(self) -> ComposeResult:
                yield HintLegend(vm, hub=hub)

        app = _App()
        async with app.run_test() as pilot:
            await pilot.pause()
            widget = app.query_one(HintLegend)
            text = widget.render()
            assert "cmd" in text.plain  # fallback
            from aws_tui.vm.messages import FocusChangedMessage

            hub.send(FocusChangedMessage(focused_vm_id="pane.left"))
            await pilot.pause()
            text2 = app.query_one(HintLegend).render()
            assert "copy" in text2.plain
    finally:
        vm.dispose()
        hub.dispose()


# ── ToastStack ──────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_toast_stack_renders_three_toasts_with_levels() -> None:
    hub: MessageHub = MessageHub()
    dispatcher = RxDispatcher.immediate()
    vm = ToastStackVM(hub=hub, dispatcher=dispatcher)
    vm.construct()
    try:

        class _App(App[None]):
            def compose(self) -> ComposeResult:
                yield ToastStack(vm, hub=hub)

        app = _App()
        async with app.run_test() as pilot:
            await pilot.pause()
            vm.raise_toast(
                ToastModel(
                    id="t1",
                    text="info one",
                    level=ToastLevel.INFO,
                    sticky=True,
                    timeout_seconds=None,
                    action_label=None,
                    action_action=None,
                )
            )
            vm.raise_toast(
                ToastModel(
                    id="t2",
                    text="warning two",
                    level=ToastLevel.WARNING,
                    sticky=True,
                    timeout_seconds=None,
                    action_label="sign in",
                    action_action="auth.authenticate",
                )
            )
            vm.raise_toast(
                ToastModel(
                    id="t3",
                    text="error three",
                    level=ToastLevel.ERROR,
                    sticky=True,
                    timeout_seconds=None,
                    action_label=None,
                    action_action=None,
                )
            )
            await pilot.pause()
            from aws_tui.ui.widgets.toast import Toast

            toasts = app.query(Toast)
            assert len(toasts) == 3
            classes = {tuple(t.classes) for t in toasts}
            assert any("-info" in c for c in classes)
            assert any("-warning" in c for c in classes)
            assert any("-error" in c for c in classes)
    finally:
        vm.dispose()
        hub.dispose()


# ── ServicesMenu ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_services_menu_renders_items_and_marks_selected() -> None:
    hub: MessageHub = MessageHub()
    dispatcher = RxDispatcher.immediate()
    registry = ServiceRegistry()
    registry.register(_S3Stub())
    registry.register(_EC2Stub())

    vm = ServicesMenuVM(registry=registry, hub=hub, dispatcher=dispatcher)
    vm.construct()
    vm.update_connection(_make_connection())
    try:

        class _App(App[None]):
            def compose(self) -> ComposeResult:
                yield ServicesMenu(vm, hub=hub)

        app = _App()
        async with app.run_test() as pilot:
            await pilot.pause()
            from aws_tui.ui.widgets.services_menu import ServiceItemView

            views = app.query(ServiceItemView)
            assert len(views) == 2
            vm.switch_service_command.execute("s3")
            await pilot.pause()
            await pilot.pause()
            views2 = app.query(ServiceItemView)
            selected = [v for v in views2 if "-selected" in v.classes]
            assert len(selected) == 1
            assert selected[0].item_vm.descriptor.id == "s3"
    finally:
        vm.dispose()
        hub.dispose()
