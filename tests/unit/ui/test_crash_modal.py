"""Smoke tests for the CrashModal widget."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from textual.app import App, ComposeResult
from vmx import MessageHub, RxDispatcher

from aws_tui.ui.widgets.crash_modal import CrashModal
from aws_tui.ui.widgets.modal_button import ModalButton
from aws_tui.vm.chrome.crash_vm import CrashReport, CrashVM


def _report(*, can_continue: bool) -> CrashReport:
    return CrashReport(
        timestamp=datetime(2026, 6, 14, 10, 0, 0, tzinfo=UTC),
        exception_type="TypeError",
        exception_message="bad operands",
        traceback_short=(
            'Traceback (most recent call last):\n  File "x.py", line 1\nTypeError: bad operands'
        ),
        dump_path=Path("/tmp/aws-tui/crash/2026-06-14T10-00-00.txt"),
        can_continue=can_continue,
    )


@pytest.mark.asyncio
async def test_crash_modal_continue_button_disabled_when_unsafe() -> None:
    hub: MessageHub = MessageHub()
    dispatcher = RxDispatcher.immediate()
    vm = CrashVM(_report(can_continue=False), hub=hub, dispatcher=dispatcher)
    vm.construct()
    try:

        class _App(App[None]):
            def compose(self) -> ComposeResult:
                yield from ()

            async def on_mount(self) -> None:
                await self.push_screen(CrashModal(vm, hub=hub))

        app = _App()
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.pause()
            modal = app.screen
            assert isinstance(modal, CrashModal)
            assert "-danger" in modal.classes
            buttons = list(modal.query(ModalButton))
            cont = next(b for b in buttons if b.button_id == "crash-continue-btn")
            # When can_continue=False the button gets the ``-disabled``
            # class instead of Textual's stock ``Button.disabled`` —
            # ModalButton is a structural Static with no built-in
            # disabled state; ``action_continue`` guards via the VM.
            assert "-disabled" in cont.classes
    finally:
        vm.dispose()
        hub.dispose()


@pytest.mark.asyncio
async def test_crash_modal_continue_button_enabled_when_safe() -> None:
    hub: MessageHub = MessageHub()
    dispatcher = RxDispatcher.immediate()
    vm = CrashVM(_report(can_continue=True), hub=hub, dispatcher=dispatcher)
    vm.construct()
    try:

        class _App(App[None]):
            def compose(self) -> ComposeResult:
                yield from ()

            async def on_mount(self) -> None:
                await self.push_screen(CrashModal(vm, hub=hub))

        app = _App()
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.pause()
            modal = app.screen
            assert isinstance(modal, CrashModal)
            buttons = list(modal.query(ModalButton))
            cont = next(b for b in buttons if b.button_id == "crash-continue-btn")
            # Safe-side ``continue`` gets ``-primary`` (accent
            # styling); the ``-disabled`` class must NOT be present.
            assert "-primary" in cont.classes
            assert "-disabled" not in cont.classes
    finally:
        vm.dispose()
        hub.dispose()
