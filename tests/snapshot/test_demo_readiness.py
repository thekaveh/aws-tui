"""Snapshot preconditions must observe rendered state, not just its model."""

from __future__ import annotations

import asyncio
import contextlib

import pytest
from textual.pilot import Pilot

from aws_tui.ui.widgets.toast import Toast, ToastStack
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
