"""Smoke tests for chrome widgets.

We mount each widget inside a tiny test ``App`` driven by ``run_test``, then
assert the widget renders without error and reacts to VM state changes.
"""

from __future__ import annotations

import pytest
from rich.cells import cell_len
from textual.app import App, ComposeResult
from textual.widgets import Static
from vmx import MessageHub, RxDispatcher

from aws_tui.infra.aws_session import TokenState
from aws_tui.infra.connection_resolver import Connection
from aws_tui.infra.keymap_store import KeymapStore
from aws_tui.ui.widgets.hint_legend import HintLegend, _action_width, _fit_actions
from aws_tui.ui.widgets.nav_menu import NavMenu
from aws_tui.ui.widgets.status_bar import StatusBar
from aws_tui.ui.widgets.toast import ToastStack
from aws_tui.vm.chrome.hint_legend_vm import HintAction, HintLegendVM
from aws_tui.vm.chrome.status_bar_vm import StatusBarVM
from aws_tui.vm.chrome.toast_stack_vm import ToastStackVM
from aws_tui.vm.chrome.toast_vm import ToastLevel, ToastModel
from aws_tui.vm.nav_menu_vm import NavMenuVM as ServicesMenuVM
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
    vm.register_focusable("pane.left", ("pane.copy", "pane.delete"))
    vm.construct()
    try:

        class _App(App[None]):
            def compose(self) -> ComposeResult:
                yield HintLegend(vm, hub=hub)

        app = _App()
        async with app.run_test() as pilot:
            await pilot.pause()
            widget = app.query_one(HintLegend)

            # Each chip is its own Static child now (so theme tcss can
            # color it). Aggregate via .render() against the chip strip.
            def _strip_text(host: HintLegend) -> str:
                return " ".join(str(s.render()) for s in host.query(Static))

            strip = _strip_text(widget)
            assert "more" in strip
            assert "quit" in strip
            assert "cmd" not in strip
            from aws_tui.vm.messages import FocusChangedMessage

            hub.send(FocusChangedMessage(focused_vm_id="pane.left"))
            await pilot.pause()
            assert "copy" in _strip_text(app.query_one(HintLegend))
    finally:
        vm.dispose()
        hub.dispose()


def _athena_hint_vm() -> tuple[HintLegendVM, MessageHub]:
    hub: MessageHub = MessageHub()
    vm = HintLegendVM(hub=hub, dispatcher=RxDispatcher.immediate(), keymap=KeymapStore())
    vm.set_current_service("athena")
    vm.construct()
    return vm, hub


class _HintApp(App[None]):
    def __init__(self, vm: HintLegendVM, hub: MessageHub) -> None:
        super().__init__()
        self._hint_vm = vm
        self._hint_hub = hub

    def compose(self) -> ComposeResult:
        yield HintLegend(self._hint_vm, hub=self._hint_hub)


@pytest.mark.asyncio
async def test_hint_legend_is_one_compact_row_at_wide_athena_width() -> None:
    vm, hub = _athena_hint_vm()
    try:
        async with _HintApp(vm, hub).run_test(size=(245, 62)) as pilot:
            await pilot.pause()
            legend = pilot.app.query_one(HintLegend)
            chips = list(legend.query(".hint-chip"))
            assert {chip.region.y for chip in chips} == {legend.content_region.y}
            assert legend.region.height == 3
            assert not any(chip.action.overflow_only for chip in chips)
            assert all(chip.tooltip == chip.action.tooltip for chip in chips)
            assert all(not chip.can_focus for chip in chips)
    finally:
        vm.dispose()
        hub.dispose()


@pytest.mark.asyncio
async def test_hint_legend_uses_more_instead_of_wrapping_when_narrow() -> None:
    vm, hub = _athena_hint_vm()
    try:
        async with _HintApp(vm, hub).run_test(size=(80, 24)) as pilot:
            await pilot.pause()
            chips = list(pilot.app.query(".hint-chip"))
            assert len({chip.region.y for chip in chips}) == 1
            assert {chip.action.action_id for chip in chips} >= {
                "app.command_palette",
                "app.quit",
            }
            assert all(
                chip.region.right <= pilot.app.query_one(HintLegend).content_region.right
                for chip in chips
            )
    finally:
        vm.dispose()
        hub.dispose()


def _hint(
    action_id: str,
    key_label: str,
    action_label: str,
    *,
    priority: int = 50,
    overflow_only: bool = False,
    enabled: bool = True,
) -> HintAction:
    return HintAction(
        action_id=action_id,
        key_label=key_label,
        action_label=action_label,
        tooltip=action_id,
        priority=priority,
        overflow_only=overflow_only,
        enabled=enabled,
    )


def test_fit_actions_preserves_all_non_overflow_actions_that_fit() -> None:
    actions = (
        _hint("pane.copy", "c", "copy"),
        _hint("pane.delete", "d", "delete"),
        _hint("app.command_palette", ":", "more", overflow_only=True),
        _hint("app.quit", "q", "quit"),
    )

    assert _fit_actions(actions, width=80) == (actions[0], actions[1], actions[3])


def test_fit_actions_activates_overflow_one_cell_below_exact_width() -> None:
    actions = (
        _hint("pane.copy", "c", "copy"),
        _hint("pane.delete", "d", "delete"),
        _hint("app.command_palette", ":", "more", overflow_only=True),
        _hint("app.quit", "q", "quit"),
    )
    regular = tuple(action for action in actions if not action.overflow_only)
    exact_width = sum(_action_width(action) for action in regular)

    assert all(
        _action_width(action) == cell_len(f"[{action.key_label}] {action.action_label}") + 1
        for action in actions
    )
    assert _fit_actions(actions, width=exact_width) == regular

    overflowed = _fit_actions(actions, width=exact_width - 1)
    assert "app.command_palette" in {action.action_id for action in overflowed}
    assert sum(_action_width(action) for action in overflowed) <= exact_width - 1


def test_fit_actions_removes_later_duplicate_tab_hint_first() -> None:
    actions = (
        _hint("pane.first", "tab", "first", priority=1),
        _hint("pane.second", "tab", "second", priority=1),
        _hint("pane.keep", "a", "keep", priority=99),
        _hint("app.command_palette", ":", "more", overflow_only=True),
        _hint("app.quit", "q", "quit"),
    )

    assert [action.action_id for action in _fit_actions(actions, width=39)] == [
        "pane.first",
        "pane.keep",
        "app.command_palette",
        "app.quit",
    ]


def test_fit_actions_removes_disabled_action_before_enabled_action_at_same_priority() -> None:
    actions = (
        _hint("pane.enabled", "e", "enabled", priority=10),
        _hint("pane.disabled", "d", "disabled", priority=10, enabled=False),
        _hint("app.command_palette", ":", "more", overflow_only=True),
        _hint("app.quit", "q", "quit"),
    )

    assert [action.action_id for action in _fit_actions(actions, width=31)] == [
        "pane.enabled",
        "app.command_palette",
        "app.quit",
    ]


def test_fit_actions_keeps_more_and_quit_at_minimum_supported_width() -> None:
    actions = (
        _hint("pane.copy", "c", "copy longer", priority=99),
        _hint("app.command_palette", ":", "more", overflow_only=True),
        _hint("app.quit", "q", "quit"),
    )

    assert [action.action_id for action in _fit_actions(actions, width=18)] == [
        "app.command_palette",
        "app.quit",
    ]


@pytest.mark.asyncio
async def test_hint_legend_refits_after_resize_and_vm_action_change() -> None:
    vm, hub = _athena_hint_vm()
    try:
        async with _HintApp(vm, hub).run_test(size=(245, 62)) as pilot:
            await pilot.pause()
            legend = pilot.app.query_one(HintLegend)
            assert "app.command_palette" not in {
                chip.action.action_id for chip in legend.query(".hint-chip")
            }

            await pilot.resize_terminal(80, 24)
            assert "app.command_palette" in {
                chip.action.action_id for chip in legend.query(".hint-chip")
            }

            vm.set_current_service("settings")
            await pilot.pause()
            assert "app.command_palette" not in {
                chip.action.action_id for chip in legend.query(".hint-chip")
            }
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
                yield NavMenu(vm=vm, hub=hub)

        app = _App()
        async with app.run_test() as pilot:
            await pilot.pause()
            # Post-PR-#94: NavMenu hosts NavRow widgets directly.
            # Two service items + Settings = three rows total.
            from aws_tui.ui.widgets.nav_row import NavRow

            rows = list(app.query(NavRow))
            ids = [r.descriptor_id for r in rows]
            assert "s3" in ids, f"Expected s3 in {ids}"
            assert "ec2" in ids, f"Expected ec2 in {ids}"
            assert "settings" in ids, f"Expected settings in {ids}"
            # Settings row carries the ``-settings`` class so per-
            # theme CSS can apply a divider above it.
            settings_row = next(r for r in rows if r.descriptor_id == "settings")
            assert settings_row.has_class("-settings")
    finally:
        vm.dispose()
        hub.dispose()
