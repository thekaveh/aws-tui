"""Snapshot preconditions must observe rendered state, not just its model."""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Callable

import pytest
from textual.events import Resize
from textual.message import Message
from textual.pilot import Pilot
from textual.widgets import Static

from aws_tui.app import AwsTuiApp
from aws_tui.ui.widgets.pane import EntryRow, Pane, _name_width_for
from aws_tui.ui.widgets.toast import Toast, ToastStack
from aws_tui.vm.chrome.toast_stack_vm import ToastStackVM
from aws_tui.vm.chrome.toast_vm import ToastLevel, ToastModel, ToastVM
from tests.helpers import wait_until
from tests.snapshot import test_demo_mode as demo
from tests.snapshot.apps.demo_mode import DemoModeApp


@pytest.mark.asyncio
@pytest.mark.parametrize("pending_stage", ["resize", "reflow"])
async def test_demo_capture_waits_for_responsive_columns(
    monkeypatch: pytest.MonkeyPatch,
    pending_stage: str,
) -> None:
    """Hold real layout messages/callbacks beyond the old drain's last barrier."""
    original_post = Pane.post_message
    original_after_refresh = Pane.call_after_refresh
    original_animations = Pilot.wait_for_scheduled_animations
    held_resizes: dict[Pane, Resize] = {}
    held_reflows: list[tuple[Pane, Callable[[], None]]] = []
    animations_drained = asyncio.Event()
    released = False

    def hold_resize(pane: Pane, message: Message) -> bool:
        if not released and isinstance(message, Resize):
            held_resizes[pane] = message
            return True
        return original_post(pane, message)

    def hold_reflow(pane: Pane, callback, *args, **kwargs) -> bool:  # type: ignore[no-untyped-def]
        if not released and callback.__name__ == "_reflow_columns":
            held_reflows.append((pane, callback))
            return True
        return original_after_refresh(pane, callback, *args, **kwargs)

    async def observe_animations(pilot: Pilot[None]) -> None:
        await original_animations(pilot)
        animations_drained.set()

    def release() -> None:
        nonlocal released
        released = True
        for pane, event in held_resizes.items():
            assert original_post(pane, event)
        for pane, callback in held_reflows:
            assert original_after_refresh(pane, callback)
        held_resizes.clear()
        held_reflows.clear()

    app = DemoModeApp(theme="github-light")
    async with app.run_test(size=(140, 30)) as pilot:
        await demo._drain_workers(pilot)
        panes = list(app.query(Pane))
        assert len(panes) == 2
        previous_headers = [
            str(pane.query_one(".column-header", Static).render()) for pane in panes
        ]
        if pending_stage == "resize":
            monkeypatch.setattr(Pane, "post_message", hold_resize)
        else:
            monkeypatch.setattr(Pane, "call_after_refresh", hold_reflow)
        monkeypatch.setattr(Pilot, "wait_for_scheduled_animations", observe_animations)

        await pilot.resize_terminal(*demo.TERMINAL_SIZE)
        await wait_until(
            lambda: len(held_resizes if pending_stage == "resize" else held_reflows) >= 2,
            what=f"real pane {pending_stage} work to be held",
        )
        assert all(pane.region.width == 53 for pane in panes)
        assert [
            str(pane.query_one(".column-header", Static).render()) for pane in panes
        ] == previous_headers
        if pending_stage == "resize":
            assert all(
                pane.name_column_width != _name_width_for(pane.region.width) for pane in panes
            )
        else:
            assert all(
                pane.name_column_width == _name_width_for(pane.region.width) for pane in panes
            )
        # This is the same user-visible clipping as the failed golden, not
        # just a different internal value or SVG background segmentation.
        local_rows = list(app.query_one("#pane-right", Pane).query(EntryRow))
        assert local_rows
        assert all("12:00" not in row.render_line(0).text for row in local_rows)
        capture_ready = asyncio.create_task(demo._drain_workers(pilot))
        try:
            await asyncio.wait_for(animations_drained.wait(), timeout=15)
            assert not capture_ready.done(), "capture accepted stale responsive header/row columns"
            release()
            await asyncio.wait_for(capture_ready, timeout=15)
            assert all(pane.name_column_width == 18 for pane in panes)
            assert all("12:00" in row.render_line(0).text for row in local_rows)
            svg = app.export_screenshot()
            assert "2026-06-30&#160;12:00" in svg
        finally:
            release()
            if not capture_ready.done():
                capture_ready.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await capture_ready


@pytest.mark.asyncio
async def test_iceberg_capture_waits_for_advisory_widget_removal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_rebuild = ToastStack._rebuild_toasts
    original_drain = demo._drain_workers
    held: list[ToastStack] = []
    drained_after_dismissal = asyncio.Event()
    released = False

    def advisory_widgets(app: DemoModeApp) -> list[Toast]:
        return [
            toast
            for toast in app.query(Toast)
            if demo._DEMO_STARTUP_ADVISORY in toast.toast_vm.model.text
        ]

    def hold_dismissal(stack: ToastStack) -> None:
        if (
            not released
            and not stack.vm.toasts
            and any(
                demo._DEMO_STARTUP_ADVISORY in toast.toast_vm.model.text
                for toast in stack.query(Toast)
            )
        ):
            held.append(stack)
            return
        original_rebuild(stack)

    async def observe_drain(pilot: Pilot[None]) -> None:
        await original_drain(pilot)
        if held:
            drained_after_dismissal.set()

    monkeypatch.setattr(ToastStack, "_rebuild_toasts", hold_dismissal)
    monkeypatch.setattr(demo, "_drain_workers", observe_drain)
    app = DemoModeApp(theme="voidline")
    async with app.run_test(size=(120, 40)) as pilot:
        capture_ready = asyncio.create_task(demo._show_demo_iceberg(pilot))
        try:
            await asyncio.wait_for(drained_after_dismissal.wait(), timeout=15)
            assert held
            assert not app.app_ctx.root_vm.chrome.toast_stack.toasts
            assert advisory_widgets(app)
            assert not capture_ready.done(), "capture accepted a still-mounted startup advisory"

            released = True
            for stack in held:
                original_rebuild(stack)
            await asyncio.wait_for(capture_ready, timeout=15)
            assert not advisory_widgets(app)
        finally:
            released = True
            for stack in held:
                original_rebuild(stack)
            if not capture_ready.done():
                capture_ready.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await capture_ready


@pytest.mark.asyncio
async def test_iceberg_capture_clears_slow_boot_success_but_preserves_other_toasts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Force the real boot worker's narrated path, then hold expiry so success
    # cannot disappear just because the test runner happens to be slow.
    monkeypatch.setattr(AwsTuiApp, "_ATTEMPT_TOAST_GRACE_SECONDS", 0.0)
    original_schedule = ToastStackVM._schedule_auto_dismiss
    successes: list[str] = []

    def hold_boot_success(stack: ToastStackVM, toast: ToastVM) -> None:
        if (
            toast.model.id == "boot-outcome-aws-demo-dev"
            and toast.model.level == ToastLevel.SUCCESS
        ):
            successes.append(toast.model.id)
            return
        original_schedule(stack, toast)

    monkeypatch.setattr(ToastStackVM, "_schedule_auto_dismiss", hold_boot_success)
    app = DemoModeApp(theme="carbon")
    async with app.run_test(size=(120, 40)) as pilot:
        await demo._drain_workers(pilot)
        stack = app.app_ctx.root_vm.chrome.toast_stack
        assert successes == ["boot-outcome-aws-demo-dev"]
        assert not any(toast.model.id in successes for toast in stack.toasts)
        assert not any(toast.toast_vm.model.id in successes for toast in app.query(Toast))
        assert any(demo._DEMO_STARTUP_ADVISORY in toast.model.text for toast in stack.toasts)
        preserved = {
            "boot-outcome-aws-warning": ToastLevel.WARNING,
            "boot-outcome-aws-error": ToastLevel.ERROR,
            "unrelated-success": ToastLevel.SUCCESS,
            "boot-outcome-aws-demo-prod": ToastLevel.SUCCESS,
        }
        for toast_id, level in preserved.items():
            stack.raise_toast(
                ToastModel(
                    id=toast_id,
                    text=toast_id,
                    level=level,
                    sticky=True,
                    timeout_seconds=None,
                    action_label=None,
                    action_action=None,
                )
            )
        await wait_until(
            lambda: set(preserved) <= {toast.toast_vm.model.id for toast in app.query(Toast)},
            what="notices that must survive capture preparation to be mounted",
        )
        await demo._show_profile_iceberg(
            pilot,
            profile="demo-prod",
            region="us-east-1",
            database="prod_warehouse",
            table="prod_sales_iceberg",
            view="files",
        )
        assert not any(toast.model.id in successes for toast in stack.toasts)
        assert not any(toast.toast_vm.model.id in successes for toast in app.query(Toast))
        assert set(preserved) <= {toast.model.id for toast in stack.toasts}
        assert set(preserved) <= {toast.toast_vm.model.id for toast in app.query(Toast)}


@pytest.mark.asyncio
@pytest.mark.parametrize("level", [ToastLevel.WARNING, ToastLevel.ERROR])
async def test_demo_readiness_preserves_failed_boot_outcome(level: ToastLevel) -> None:
    app = DemoModeApp(theme="carbon")
    async with app.run_test(size=(120, 40)) as pilot:
        await demo._drain_workers(pilot)
        stack = app.app_ctx.root_vm.chrome.toast_stack
        outcome = ToastModel(
            id="boot-outcome-aws-demo-dev",
            text="boot failure must remain visible",
            level=level,
            sticky=True,
            timeout_seconds=None,
            action_label=None,
            action_action=None,
        )
        stack.raise_toast(outcome)
        await wait_until(
            lambda: any(toast.toast_vm.model == outcome for toast in app.query(Toast)),
            what="failed boot outcome to be mounted before readiness cleanup",
        )
        await demo._drain_workers(pilot)
        assert any(toast.model == outcome for toast in stack.toasts)
        assert any(toast.toast_vm.model == outcome for toast in app.query(Toast))
