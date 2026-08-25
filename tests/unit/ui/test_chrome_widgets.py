"""Smoke tests for chrome widgets.

We mount each widget inside a tiny test ``App`` driven by ``run_test``, then
assert the widget renders without error and reacts to VM state changes.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable

import pytest
from rich.cells import cell_len
from textual.app import App, ComposeResult
from textual.containers import Horizontal
from textual.css.query import NoMatches
from textual.events import Resize
from textual.geometry import Size
from textual.widget import Widget
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
async def test_hint_legend_initializes_before_test_driver_control(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    vm, hub = _athena_hint_vm()
    mount = Horizontal.mount
    mount_boundary_reached = asyncio.Event()
    release_mount = asyncio.Event()
    initial_rebuild_complete = asyncio.Event()
    context_entered = asyncio.Event()
    driver_control = asyncio.Event()
    allow_driver_exit = asyncio.Event()
    scheduled_after_refresh: list[object] = []
    ordering: list[str] = []
    observed_ids: tuple[str, ...] = ()
    expected_ids: tuple[str, ...] = ()
    rebuild_chips = HintLegend._rebuild_chips

    def capture_after_refresh(
        _legend: HintLegend,
        callback: object,
        *_args: object,
        **_kwargs: object,
    ) -> bool:
        scheduled_after_refresh.append(callback)
        return True

    def mount_with_boundary(
        host: Horizontal,
        *widgets: Widget,
        before: int | str | Widget | None = None,
        after: int | str | Widget | None = None,
    ) -> Awaitable[None]:
        pending = mount(host, *widgets, before=before, after=after)
        if host.id != "hint-strip":
            return pending
        ordering.append("initial-rebuild")

        async def wait_for_mount() -> None:
            mount_boundary_reached.set()
            await release_mount.wait()
            await pending

        return wait_for_mount()

    async def track_initial_rebuild(legend: HintLegend) -> None:
        await rebuild_chips(legend)
        initial_rebuild_complete.set()

    monkeypatch.setattr(HintLegend, "call_after_refresh", capture_after_refresh)
    monkeypatch.setattr(HintLegend, "_rebuild_chips", track_initial_rebuild)
    monkeypatch.setattr(Horizontal, "mount", mount_with_boundary)

    async def run_driver() -> None:
        nonlocal expected_ids, observed_ids
        async with _HintApp(vm, hub).run_test(size=(80, 24)) as pilot:
            ordering.append("driver-context")
            context_entered.set()
            await initial_rebuild_complete.wait()
            legend = pilot.app.query_one(HintLegend)
            observed_ids = _visible_hint_ids(legend)
            expected_ids = _expected_hint_ids(legend)
            driver_control.set()
            await allow_driver_exit.wait()

    driver_task = asyncio.create_task(run_driver())
    mount_waiter = asyncio.create_task(mount_boundary_reached.wait())
    context_waiter = asyncio.create_task(context_entered.wait())
    try:
        completed, _pending = await asyncio.wait(
            (mount_waiter, context_waiter),
            timeout=2,
            return_when=asyncio.FIRST_COMPLETED,
        )

        assert mount_waiter in completed
        assert ordering[0] == "initial-rebuild"
        assert not initial_rebuild_complete.is_set()
        assert not driver_control.is_set()
        assert not scheduled_after_refresh

        release_mount.set()
        await asyncio.wait_for(driver_control.wait(), timeout=2)

        assert observed_ids == expected_ids
        assert observed_ids
    finally:
        release_mount.set()
        initial_rebuild_complete.set()
        allow_driver_exit.set()
        for waiter in (mount_waiter, context_waiter):
            if not waiter.done():
                waiter.cancel()
        await asyncio.gather(mount_waiter, context_waiter, return_exceptions=True)
        await driver_task
        vm.dispose()
        hub.dispose()


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


def _visible_hint_ids(legend: HintLegend) -> tuple[str, ...]:
    return tuple(chip.action.action_id for chip in legend.query(".hint-chip"))


def _expected_hint_ids(legend: HintLegend) -> tuple[str, ...]:
    actions = (*legend.vm.actions, *legend.vm.global_actions)
    return tuple(action.action_id for action in _fit_actions(actions, legend.content_region.width))


@pytest.mark.asyncio
async def test_hint_legend_rebuild_is_never_observably_empty_or_duplicated(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    vm, hub = _athena_hint_vm()
    mount = Horizontal.mount
    remove_children = Horizontal.remove_children
    gated_rebuilds: dict[
        asyncio.Task[None],
        tuple[asyncio.Event, asyncio.Event, asyncio.Event, asyncio.Event],
    ] = {}

    class _ManualHintLegend(HintLegend):
        def on_mount(self) -> None:
            pass

        def on_resize(self, _event: Resize) -> None:
            pass

    def mount_at_observable_boundary(
        strip: Horizontal,
        *widgets: Widget,
        before: int | str | Widget | None = None,
        after: int | str | Widget | None = None,
    ) -> Awaitable[None]:
        pending = mount(strip, *widgets, before=before, after=after)
        gate = gated_rebuilds.get(asyncio.current_task())
        if gate is None:
            return pending
        mount_boundary_reached, _removal_boundary_reached, release_mount, _release_removal = gate

        async def wait_for_mount() -> None:
            mount_boundary_reached.set()
            await release_mount.wait()
            await pending

        return wait_for_mount()

    def remove_children_at_observable_boundary(
        strip: Horizontal, selector: str = "*"
    ) -> Awaitable[None]:
        pending = remove_children(strip, selector)
        gate = gated_rebuilds.get(asyncio.current_task())
        if gate is None:
            return pending
        _mount_boundary_reached, removal_boundary_reached, _release_mount, release_removal = gate

        async def wait_for_removal() -> None:
            removal_boundary_reached.set()
            await release_removal.wait()
            await pending

        return wait_for_removal()

    monkeypatch.setattr(Horizontal, "mount", mount_at_observable_boundary)
    monkeypatch.setattr(Horizontal, "remove_children", remove_children_at_observable_boundary)
    try:
        app = App[None]()
        async with app.run_test(size=(80, 24)) as pilot:
            legend = _ManualHintLegend(vm, hub=hub)
            unrelated_legend = _ManualHintLegend(vm, hub=hub)
            await pilot.app.mount(legend, unrelated_legend)

            await asyncio.wait_for(unrelated_legend._rebuild_chips(), timeout=2)

            def assert_exact_state() -> None:
                visible_ids = _visible_hint_ids(legend)
                assert visible_ids == _expected_hint_ids(legend)
                rendered = [
                    text for static in legend.query(Static) if (text := str(static.render()))
                ]
                assert len(rendered) == 2 * len(visible_ids)

            async def assert_boundary() -> None:
                mount_boundary_reached = asyncio.Event()
                removal_boundary_reached = asyncio.Event()
                release_mount = asyncio.Event()
                release_removal = asyncio.Event()
                rebuild = asyncio.create_task(legend._rebuild_chips())
                gated_rebuilds[rebuild] = (
                    mount_boundary_reached,
                    removal_boundary_reached,
                    release_mount,
                    release_removal,
                )
                try:
                    await asyncio.wait_for(mount_boundary_reached.wait(), timeout=2)
                    assert_exact_state()
                    release_mount.set()
                    await asyncio.wait_for(removal_boundary_reached.wait(), timeout=2)
                    assert_exact_state()
                finally:
                    release_mount.set()
                    release_removal.set()
                    await rebuild
                    gated_rebuilds.pop(rebuild)

            await assert_boundary()
            text = " ".join(str(static.render()) for static in legend.query(Static))
            assert "more" in text
            assert "quit" in text

            vm.set_current_service("settings")
            await assert_boundary()
            assert all("athena." not in action_id for action_id in _visible_hint_ids(legend))
    finally:
        vm.dispose()
        hub.dispose()


@pytest.mark.asyncio
async def test_hint_legend_holds_compositor_frame_until_replacement_finishes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    vm, hub = _athena_hint_vm()
    mount = Horizontal.mount
    mount_boundary_reached = asyncio.Event()
    release_mount = asyncio.Event()

    class _FrameHintApp(_HintApp):
        def __init__(self, vm: HintLegendVM, hub: MessageHub) -> None:
            super().__init__(vm, hub)
            self.display_count = 0
            self.probing_compositor = False
            self.probe_display_count = 0

        def post_display_hook(self) -> None:
            self.display_count += 1
            if self.probing_compositor:
                self.probe_display_count += 1

    try:
        app = _FrameHintApp(vm, hub)
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause()
            legend = app.query_one(HintLegend)
            strip = legend.query_one("#hint-strip", Horizontal)
            gate_available = True

            def mount_with_boundary(
                host: Horizontal,
                *widgets: Widget,
                before: int | str | Widget | None = None,
                after: int | str | Widget | None = None,
            ) -> Awaitable[None]:
                nonlocal gate_available
                pending = mount(host, *widgets, before=before, after=after)
                if host is not strip or not gate_available:
                    return pending
                gate_available = False

                async def wait_for_mount() -> None:
                    mount_boundary_reached.set()
                    await release_mount.wait()
                    await pending

                return wait_for_mount()

            monkeypatch.setattr(Horizontal, "mount", mount_with_boundary)

            vm.set_current_service("settings")
            await asyncio.wait_for(mount_boundary_reached.wait(), timeout=2)
            try:
                app.refresh(layout=True)
                # Textual exposes refresh scheduling publicly, but its Pilot
                # boundaries wait for this deliberately blocked callback. A
                # direct timer tick is the only deterministic, delay-free way
                # to offer the compositor a frame while AwaitMount is held.
                app.probing_compositor = True
                try:
                    app.screen._on_timer_update()
                finally:
                    app.probing_compositor = False

                assert app.probe_display_count == 0
                frame_count_before_release = app.display_count
            finally:
                release_mount.set()

            await pilot.pause()
            assert app.display_count > frame_count_before_release
            assert _visible_hint_ids(legend) == _expected_hint_ids(legend)
    finally:
        vm.dispose()
        hub.dispose()


@pytest.mark.asyncio
async def test_hint_legend_coalesces_resize_and_vm_rebuild_requests() -> None:
    vm, hub = _athena_hint_vm()
    try:
        async with _HintApp(vm, hub).run_test(size=(80, 24)) as pilot:
            legend = pilot.app.query_one(HintLegend)
            resize = Resize(Size(80, 24), Size(80, 24), Size(80, 24))

            for service_id in ("glue", "settings", "s3", "athena"):
                vm.set_current_service(service_id)
                await legend.on_resize(resize)

            await pilot.pause()

            assert _visible_hint_ids(legend) == _expected_hint_ids(legend)
            assert _visible_hint_ids(legend)
            assert {"app.command_palette", "app.quit"} <= set(_visible_hint_ids(legend))
    finally:
        vm.dispose()
        hub.dispose()


@pytest.mark.asyncio
async def test_hint_legend_stale_rebuild_cannot_replace_newer_vm_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    vm, hub = _athena_hint_vm()
    remove_children = Horizontal.remove_children
    changed_during_removal = False

    def remove_children_with_newer_state(strip: Horizontal, selector: str = "*") -> Awaitable[None]:
        pending = remove_children(strip, selector)

        async def wait_for_removal() -> None:
            nonlocal changed_during_removal
            if strip.id == "hint-strip" and not changed_during_removal:
                changed_during_removal = True
                vm.set_current_service("settings")
            await pending

        return wait_for_removal()

    monkeypatch.setattr(Horizontal, "remove_children", remove_children_with_newer_state)
    try:
        async with _HintApp(vm, hub).run_test(size=(80, 24)) as pilot:
            await pilot.pause()
            legend = pilot.app.query_one(HintLegend)

            assert changed_during_removal
            assert _visible_hint_ids(legend) == _expected_hint_ids(legend)
            assert _visible_hint_ids(legend)
            assert all("athena." not in action_id for action_id in _visible_hint_ids(legend))
    finally:
        vm.dispose()
        hub.dispose()


@pytest.mark.asyncio
async def test_hint_legend_yields_between_reentrant_rebuilds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    vm, hub = _athena_hint_vm()
    rebuild_chips = HintLegend._rebuild_chips
    remove_children = Horizontal.remove_children
    mount = Horizontal.mount
    remove_churn = {1: "glue", 3: "s3"}
    mount_churn = {2: "settings", 3: "glue"}
    callback_count = 0
    active_callback: int | None = None
    remove_counts: dict[int, int] = {}
    mount_counts: dict[int, int] = {}
    completed_ids: list[tuple[str, ...]] = []
    settled = asyncio.Event()

    async def counted_rebuild(legend: HintLegend) -> None:
        nonlocal active_callback, callback_count
        callback_count += 1
        active_callback = callback_count
        try:
            await rebuild_chips(legend)
        finally:
            active_callback = None
        completed_ids.append(_visible_hint_ids(legend))
        if callback_count == 4:
            settled.set()

    def remove_children_with_churn(strip: Horizontal, selector: str = "*") -> Awaitable[None]:
        pending = remove_children(strip, selector)
        if strip.id != "hint-strip":
            return pending
        assert active_callback is not None
        callback_id = active_callback
        remove_counts[callback_id] = remove_counts.get(callback_id, 0) + 1

        async def wait_for_removal() -> None:
            if service_id := remove_churn.pop(callback_id, None):
                vm.set_current_service(service_id)
            await pending

        return wait_for_removal()

    def mount_with_churn(
        strip: Horizontal,
        *widgets: Widget,
        before: int | str | Widget | None = None,
        after: int | str | Widget | None = None,
    ) -> Awaitable[None]:
        pending = mount(strip, *widgets, before=before, after=after)
        if strip.id != "hint-strip":
            return pending
        assert active_callback is not None
        callback_id = active_callback
        mount_counts[callback_id] = mount_counts.get(callback_id, 0) + 1

        async def wait_for_mount() -> None:
            if service_id := mount_churn.pop(callback_id, None):
                vm.set_current_service(service_id)
            await pending

        return wait_for_mount()

    monkeypatch.setattr(HintLegend, "_rebuild_chips", counted_rebuild)
    monkeypatch.setattr(Horizontal, "remove_children", remove_children_with_churn)
    monkeypatch.setattr(Horizontal, "mount", mount_with_churn)
    try:
        async with _HintApp(vm, hub).run_test(size=(80, 24)) as pilot:
            await asyncio.wait_for(settled.wait(), timeout=2)
            await pilot.pause()
            legend = pilot.app.query_one(HintLegend)

            assert callback_count == 4
            assert remove_counts == {1: 1, 2: 1, 3: 1, 4: 1}
            assert mount_counts == {1: 1, 2: 1, 3: 1, 4: 1}
            assert all(completed_ids)
            assert _visible_hint_ids(legend) == _expected_hint_ids(legend)
            assert all("athena." not in action_id for action_id in _visible_hint_ids(legend))
    finally:
        vm.dispose()
        hub.dispose()


@pytest.mark.asyncio
async def test_hint_legend_live_missing_strip_is_strict() -> None:
    vm, hub = _athena_hint_vm()

    class _MissingStripLegend(HintLegend):
        def compose(self) -> ComposeResult:
            yield Horizontal(id="not-hint-strip")

        def on_mount(self) -> None:
            pass

        def on_resize(self, _event: Resize) -> None:
            pass

    try:
        app = App[None]()
        async with app.run_test() as pilot:
            legend = _MissingStripLegend(vm, hub=hub)
            await pilot.app.mount(legend)

            with pytest.raises(NoMatches):
                await legend._rebuild_chips()
    finally:
        vm.dispose()
        hub.dispose()


@pytest.mark.asyncio
async def test_hint_legend_detached_rebuild_callback_is_safe() -> None:
    vm, hub = _athena_hint_vm()
    try:
        legend = HintLegend(vm, hub=hub)
        await legend._rebuild_chips()
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
