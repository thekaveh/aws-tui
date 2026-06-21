"""Smoke tests for the first-run modal."""

from __future__ import annotations

import pytest
from textual.app import App, ComposeResult
from vmx import MessageHub, RxDispatcher

from aws_tui.ui.widgets.first_run_modal import FirstRunModal
from aws_tui.ui.widgets.modal_button import ModalButton
from aws_tui.vm.chrome.first_run_vm import FirstRunVM


@pytest.mark.asyncio
async def test_first_run_modal_has_three_buttons() -> None:
    hub: MessageHub = MessageHub()
    dispatcher = RxDispatcher.immediate()
    vm = FirstRunVM(hub=hub, dispatcher=dispatcher)
    vm.construct()
    try:

        class _App(App[None]):
            def compose(self) -> ComposeResult:
                yield from ()

            async def on_mount(self) -> None:
                await self.push_screen(FirstRunModal(vm, hub=hub))

        app = _App()
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            modal = app.screen
            assert isinstance(modal, FirstRunModal)
            buttons = modal.query(ModalButton)
            assert {b.button_id for b in buttons} == {
                "first-run-aws-btn",
                "first-run-s3-btn",
                "first-run-skip-btn",
                "form-cancel-btn",
                "form-save-btn",
            }
    finally:
        vm.dispose()
        hub.dispose()
