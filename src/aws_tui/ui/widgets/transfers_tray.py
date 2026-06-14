"""TransfersTray widget bound to :class:`TransfersVM`.

Renders an inline panel docked to the bottom of the screen showing each
active transfer's source/destination + a progress bar.
"""

from __future__ import annotations

from reactivex.abc import DisposableBase
from textual.app import ComposeResult
from textual.containers import Vertical
from textual.widget import Widget
from textual.widgets import ProgressBar, Static
from vmx import Message, MessageHub

from aws_tui.ui.widgets._subscriber import HubSubscriberMixin
from aws_tui.vm.file_manager.transfer_vm import TransferState, TransferVM
from aws_tui.vm.file_manager.transfers_vm import TransfersVM


class TransferRow(Widget):
    """One row in the transfers tray."""

    DEFAULT_CSS = """
    TransferRow {
        height: 1;
        layout: horizontal;
    }
    """

    def __init__(
        self,
        transfer_vm: TransferVM,
        *,
        id: str | None = None,
        classes: str | None = None,
    ) -> None:
        state_class = self._state_class(transfer_vm.state)
        merged = " ".join(c for c in (classes, state_class) if c)
        super().__init__(id=id, classes=merged)
        self._transfer_vm = transfer_vm

    @property
    def transfer_vm(self) -> TransferVM:
        return self._transfer_vm

    @staticmethod
    def _state_class(state: TransferState) -> str:
        return {
            TransferState.RUNNING: "-running",
            TransferState.PENDING: "-running",
            TransferState.PAUSED: "-running",
            TransferState.COMPLETED: "-completed",
            TransferState.FAILED: "-failed",
            TransferState.CANCELLED: "-cancelled",
        }.get(state, "")

    def compose(self) -> ComposeResult:
        model = self._transfer_vm.model
        yield Static(f"{model.source_label} -> {model.destination_label}", classes="row-label")
        progress = ProgressBar(
            total=model.bytes_total if model.bytes_total else None,
            show_eta=False,
            show_percentage=True,
            classes="-accent",
        )
        if model.bytes_total:
            progress.advance(model.bytes_done)
        yield progress


class TransfersTray(HubSubscriberMixin, Widget):
    """Transfers panel widget."""

    DEFAULT_CSS = """
    TransfersTray {
        height: auto;
        max-height: 12;
    }
    """

    def __init__(
        self,
        vm: TransfersVM,
        *,
        hub: MessageHub[Message],
        id: str | None = None,
        classes: str | None = None,
    ) -> None:
        super().__init__(id=id, classes=classes)
        self._vm: TransfersVM = vm
        self._hub: MessageHub[Message] = hub
        self._collection_sub: DisposableBase | None = None

    @property
    def vm(self) -> TransfersVM:
        return self._vm

    def compose(self) -> ComposeResult:
        yield Static("transfers", classes="tray-title")
        yield Vertical(id="transfers-list")

    def on_mount(self) -> None:
        self._rebuild_rows()
        self.subscribe_to_vm(
            hub=self._hub,
            vm=self._vm,
            on_property_changed=self._on_vm_property_changed,
        )

    def on_unmount(self) -> None:
        self.unsubscribe_from_vm()

    # ── Internal ────────────────────────────────────────────────────────────

    def _on_vm_property_changed(self, property_name: str) -> None:
        if property_name == "transfers":
            self.call_after_refresh(self._rebuild_rows)

    def _rebuild_rows(self) -> None:
        try:
            container = self.query_one("#transfers-list", Vertical)
        except Exception:
            return
        for child in list(container.children):
            child.remove()
        for transfer in self._vm.transfers:
            container.mount(TransferRow(transfer))


__all__ = ["TransferRow", "TransfersTray"]
