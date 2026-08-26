from __future__ import annotations

import pytest
from textual.app import App, ComposeResult
from textual.widgets import Button
from vmx import NULL_DISPATCHER, Message, MessageHub

from aws_tui.ui.widgets.transfers_overlay import TransferRowWidget
from aws_tui.vm.file_manager.transfer_vm import TransferModel, TransferState, TransferVM


class _TransferRowApp(App[None]):
    def __init__(self, vm: TransferVM, *, hub: MessageHub[Message]) -> None:
        super().__init__()
        self._vm = vm
        self._hub = hub

    def compose(self) -> ComposeResult:
        yield Button("Before", id="before")
        yield TransferRowWidget(self._vm, hub=self._hub)


def _transfer(state: TransferState) -> tuple[TransferVM, MessageHub[Message]]:
    hub: MessageHub[Message] = MessageHub()
    vm = TransferVM(
        TransferModel(
            id="transfer-1",
            direction="upload",
            source_label="report.csv",
            destination_label="s3://reports/report.csv",
            bytes_done=10,
            bytes_total=100,
            state=state,
        ),
        hub=hub,
        dispatcher=NULL_DISPATCHER,
    )
    vm.construct()
    return vm, hub


@pytest.mark.asyncio
@pytest.mark.parametrize("key", ["enter", "space"])
async def test_cancel_button_is_tab_reachable_and_runs_vm_command(key: str) -> None:
    vm, hub = _transfer(TransferState.RUNNING)
    try:
        async with _TransferRowApp(vm, hub=hub).run_test(size=(60, 12)) as pilot:
            before = pilot.app.query_one("#before", Button)
            cancel = pilot.app.query_one("#cancel-btn", Button)
            before.focus()
            await pilot.press("tab")

            assert pilot.app.focused is cancel
            assert cancel.tooltip == "Cancel transfer"

            await pilot.press(key)
            await pilot.pause()

            assert vm.state is TransferState.CANCELLED
            assert cancel.disabled
    finally:
        vm.dispose()


@pytest.mark.asyncio
async def test_cancel_button_is_disabled_for_finished_transfer() -> None:
    vm, hub = _transfer(TransferState.COMPLETED)
    try:
        async with _TransferRowApp(vm, hub=hub).run_test(size=(60, 12)) as pilot:
            cancel = pilot.app.query_one("#cancel-btn", Button)

            assert cancel.disabled
    finally:
        vm.dispose()
