"""Smoke tests for the CrashModal widget."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from textual.app import App, ComposeResult
from vmx import MessageHub, RxDispatcher

from aws_tui.ui.widgets.crash_modal import CrashChoice, CrashModal
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
@pytest.mark.parametrize("key", ["c", "enter"])
async def test_crash_modal_keyboard_cannot_continue_after_an_unsafe_crash(key: str) -> None:
    """The ``c`` binding must respect ``can_continue``, not just the button.

    ``ModalButton.can_focus = not disabled`` keeps the mouse and Tab away from a
    disabled Continue button, and that IS tested. But ``c`` is bound directly to
    ``action_continue`` on the modal, so it bypasses the button entirely.
    Neutralising the guard inside ``action_continue`` left 125 tests green while
    ``c`` dismissed the modal after a crash inside a write command — dropping
    the user back into a corrupted app AND stranding the crash-recovery
    coroutine, since the modal that offered ``q`` was gone.
    """
    hub: MessageHub = MessageHub()
    dispatcher = RxDispatcher.immediate()
    vm = CrashVM(_report(can_continue=False), hub=hub, dispatcher=dispatcher)
    vm.construct()
    try:
        answered: list[object] = []

        class _App(App[None]):
            def compose(self) -> ComposeResult:
                yield from ()

            async def on_mount(self) -> None:
                self.push_screen(CrashModal(vm, hub=hub), answered.append)

        app = _App()
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.pause()
            modal = app.screen
            assert isinstance(modal, CrashModal)
            assert not vm.can_continue

            await pilot.press(key)
            await pilot.pause()

            if key == "c":
                # ``c`` is a no-op: there is nothing safe to continue into.
                assert app.screen is modal, "c dismissed the crash modal"
            # ``enter`` legitimately falls through to View trace, which does
            # dismiss. What neither key may ever do is CONTINUE.
            assert CrashChoice.CONTINUE not in answered
    finally:
        vm.dispose()
        hub.dispose()


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
            assert "-disabled" in cont.classes
            assert cont.disabled is True
            assert cont.can_focus is False
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
            assert cont.disabled is False
    finally:
        vm.dispose()
        hub.dispose()
