from __future__ import annotations

from typing import cast

import pytest
from textual.app import App, ComposeResult
from textual.widgets import Button
from vmx import NULL_DISPATCHER, Message, MessageHub

from aws_tui.ui.widgets.transfers_overlay import TransferRowWidget, TransfersOverlay
from aws_tui.vm.file_manager.transfer_vm import TransferModel, TransferState, TransferVM
from aws_tui.vm.file_manager.transfers_vm import TransfersVM


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
async def test_cancel_button_activates_the_vm_command_when_focused(key: str) -> None:
    """Enter/Space on a FOCUSED cancel button runs the VM command.

    This host is a bare ``App``, where Textual's stock ``tab`` binding moves
    focus normally. It does NOT establish that the button is reachable in the
    real application: ``AwsTuiApp`` binds ``tab`` to ``pane.switch_focus`` with
    priority, ``_cycle_focus`` never visits the transfers overlay, and there is
    no transfers ``FocusSlot`` — so under the real app a keyboard-only user
    cannot reach this button at all. Naming this test "tab_reachable" implied a
    guarantee it never checked.
    """
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


def test_expired_ids_are_pruned_with_bounded_transfer_history() -> None:
    hub = cast("MessageHub[Message]", MessageHub())
    vm = TransfersVM(hub=hub, dispatcher=NULL_DISPATCHER)
    vm.construct()
    overlay = TransfersOverlay(vm, hub=hub)

    for index in range(125):
        vm.register(
            TransferModel(
                id=f"transfer-{index}",
                direction="upload",
                source_label=f"/tmp/{index}",
                destination_label=f"s3://bucket/{index}",
                bytes_done=1,
                bytes_total=1,
                state=TransferState.COMPLETED,
            )
        )
        overlay._expired_ids.add(f"transfer-{index}")
    overlay._rebuild()

    retained_ids = {transfer.id for transfer in vm.transfers}
    assert overlay._expired_ids == retained_ids
    assert len(overlay._expired_ids) == 100
    vm.dispose()
