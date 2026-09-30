"""Snapshot preconditions must observe rendered state, not just its model."""

from __future__ import annotations

import asyncio
import contextlib

import pytest
from textual.pilot import Pilot

from aws_tui.app import AwsTuiApp
from aws_tui.ui.widgets.toast import Toast, ToastStack
from aws_tui.vm.chrome.toast_stack_vm import ToastStackVM
from aws_tui.vm.chrome.toast_vm import ToastLevel, ToastModel, ToastVM
from tests.helpers import wait_until
from tests.snapshot import test_demo_mode as demo
from tests.snapshot.apps.demo_mode import DemoModeApp


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
