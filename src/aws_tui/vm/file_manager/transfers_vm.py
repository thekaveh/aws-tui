"""TransfersVM — composite of :class:`TransferVM` instances.

Subscribes to :class:`TransferProgressMessage` on the hub and updates the
matching :class:`TransferVM`. Exposes derived totals (active_count,
total_bytes_done/total) that the status bar and transfer panel render.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import TYPE_CHECKING

from vmx import (
    ComponentVMOf,
    CompositeVM,
    Message,
    MessageHub,
    PropertyChangedMessage,
    RelayCommand,
)
from vmx.lifecycle.status import ConstructionStatus
from vmx.services.dispatcher import Dispatcher

from aws_tui.vm.file_manager.transfer_vm import (
    TransferModel,
    TransferState,
    TransferVM,
)
from aws_tui.vm.messages import TransferProgressMessage

if TYPE_CHECKING:
    from reactivex.abc import DisposableBase


# Mapping from message-layer literal to enum.
_STATE_FROM_LITERAL: dict[str, TransferState] = {
    "pending": TransferState.PENDING,
    "running": TransferState.RUNNING,
    "paused": TransferState.PAUSED,
    "completed": TransferState.COMPLETED,
    "failed": TransferState.FAILED,
    "cancelled": TransferState.CANCELLED,
}


class TransfersVM:
    """Composite + hub subscriber for active and finished transfers."""

    def __init__(
        self,
        *,
        hub: MessageHub[Message],
        dispatcher: Dispatcher,
        max_concurrent: int = 8,
    ) -> None:
        self._hub: MessageHub[Message] = hub
        self._dispatcher: Dispatcher = dispatcher
        self._max_concurrent: int = max_concurrent
        self._disposed: bool = False

        self._transfers: list[TransferVM] = []

        self._inner: CompositeVM[ComponentVMOf[TransferModel]] = (
            CompositeVM[ComponentVMOf[TransferModel]]
            .builder()
            .name("transfers")
            .services(hub, dispatcher)
            .children(self._initial_children)
            .auto_construct_on_add(True)
            .build()
        )

        self._cancel_all_command: RelayCommand = (
            RelayCommand.builder()
            .predicate(lambda: self.active_count > 0)
            .task(self._cancel_all)
            .build()
        )

        self._subscription: DisposableBase = hub.messages.subscribe(on_next=self._on_message)

    # ── Properties ──────────────────────────────────────────────────────────

    @property
    def transfers(self) -> tuple[TransferVM, ...]:
        return tuple(self._transfers)

    @property
    def active(self) -> tuple[TransferVM, ...]:
        return tuple(t for t in self._transfers if t.is_active or t.state == TransferState.PENDING)

    @property
    def finished(self) -> tuple[TransferVM, ...]:
        return tuple(t for t in self._transfers if t.is_finished)

    @property
    def active_count(self) -> int:
        return sum(1 for t in self._transfers if t.is_active or t.state == TransferState.PENDING)

    @property
    def total_bytes_done(self) -> int:
        return sum(t.model.bytes_done for t in self._transfers)

    @property
    def total_bytes_total(self) -> int | None:
        totals = [t.model.bytes_total for t in self._transfers]
        if any(t is None for t in totals):
            return None
        return sum(t for t in totals if t is not None)

    @property
    def cancel_all_command(self) -> RelayCommand:
        return self._cancel_all_command

    @property
    def status(self) -> ConstructionStatus:
        return self._inner.status

    @property
    def is_constructed(self) -> bool:
        return self._inner.is_constructed

    @property
    def name(self) -> str:
        return self._inner.name

    # ── Lifecycle ───────────────────────────────────────────────────────────

    def construct(self) -> None:
        self._inner.construct()

    def destruct(self) -> None:
        self._inner.destruct()

    def dispose(self) -> None:
        if self._disposed:
            return
        self._disposed = True
        self._subscription.dispose()
        self._cancel_all_command.dispose()
        for child in self._transfers:
            child.dispose()
        self._transfers.clear()
        self._inner.dispose()

    # ── Public API ──────────────────────────────────────────────────────────

    def register(self, model: TransferModel) -> TransferVM:
        """Add a new transfer; the caller-driven path."""
        existing = self._find(model.id)
        if existing is not None:
            return existing
        vm = TransferVM(model, hub=self._hub, dispatcher=self._dispatcher)
        self._transfers.append(vm)
        if self._inner.is_constructed:
            vm.construct()
        self._inner.append(vm.inner)
        self._hub.send(PropertyChangedMessage.create(self, self._inner.name, "transfers"))
        return vm

    def cancel(self, transfer_id: str) -> None:
        target = self._find(transfer_id)
        if target is None:
            return
        target.cancel_command.execute()
        self._hub.send(PropertyChangedMessage.create(self, self._inner.name, "transfers"))

    def retry(self, transfer_id: str) -> None:
        target = self._find(transfer_id)
        if target is None:
            return
        target.retry_command.execute()
        self._hub.send(PropertyChangedMessage.create(self, self._inner.name, "transfers"))

    def update(
        self,
        transfer_id: str,
        *,
        bytes_done: int,
        bytes_total: int | None,
        state: TransferState,
        error: str | None = None,
    ) -> None:
        target = self._find(transfer_id)
        if target is None:
            return
        target.apply_update(
            bytes_done=bytes_done,
            bytes_total=bytes_total,
            state=state,
            error=error,
        )
        self._hub.send(PropertyChangedMessage.create(self, self._inner.name, "transfers"))

    # ── Hub subscriber ──────────────────────────────────────────────────────

    def _on_message(self, msg: Message) -> None:
        if self._disposed:
            return
        if not isinstance(msg, TransferProgressMessage):
            return
        target = self._find(msg.transfer_id)
        new_state = _STATE_FROM_LITERAL.get(msg.state, TransferState.RUNNING)
        if target is None:
            # First sighting — auto-register a placeholder so the bookkeeping
            # is symmetric whether the caller pre-registered or not.
            placeholder = TransferModel(
                id=msg.transfer_id,
                direction="local-copy",
                source_label="",
                destination_label="",
                bytes_done=msg.bytes_transferred,
                bytes_total=msg.bytes_total,
                state=new_state,
            )
            self.register(placeholder)
            return
        target.apply_update(
            bytes_done=msg.bytes_transferred,
            bytes_total=msg.bytes_total,
            state=new_state,
        )
        self._hub.send(PropertyChangedMessage.create(self, self._inner.name, "transfers"))

    # ── Internal ────────────────────────────────────────────────────────────

    def _cancel_all(self) -> None:
        for t in list(self._transfers):
            if t.is_active or t.state == TransferState.PENDING:
                t.cancel_command.execute()
        self._hub.send(PropertyChangedMessage.create(self, self._inner.name, "transfers"))

    def _find(self, transfer_id: str) -> TransferVM | None:
        for t in self._transfers:
            if t.id == transfer_id:
                return t
        return None

    def _initial_children(self) -> Iterable[ComponentVMOf[TransferModel]]:
        return tuple(t.inner for t in self._transfers)


__all__ = ["TransfersVM"]
