"""Smoke tests for the first-run + s3-compat form modals."""

from __future__ import annotations

import pytest
from textual.app import App, ComposeResult
from vmx import MessageHub, RxDispatcher

from aws_tui.ui.widgets.first_run_modal import FirstRunModal, S3CompatFormModal
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
            }
    finally:
        vm.dispose()
        hub.dispose()


@pytest.mark.asyncio
async def test_s3_compat_form_modal_has_inputs() -> None:
    hub: MessageHub = MessageHub()
    try:

        class _App(App[None]):
            def compose(self) -> ComposeResult:
                yield from ()

            async def on_mount(self) -> None:
                await self.push_screen(S3CompatFormModal(hub=hub))

        app = _App()
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            modal = app.screen
            assert isinstance(modal, S3CompatFormModal)
            # Ensure all five input fields are present.
            from textual.widgets import Input

            inputs = modal.query(Input)
            assert len(inputs) == 5
    finally:
        hub.dispose()
