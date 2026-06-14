"""TransferVM — single transfer facade.

A transfer is one of: upload (local → s3), download (s3 → local),
local-copy (local → local), or s3-copy (s3 → s3). The facade wraps a
:class:`ComponentVMOf[TransferModel]` so subscribers can bind to model
changes via :class:`PropertyChangedMessage` ("model").
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import StrEnum
from typing import Literal

from vmx import ComponentVMOf, Message, MessageHub, PropertyChangedMessage, RelayCommand
from vmx.lifecycle.status import ConstructionStatus
from vmx.services.dispatcher import Dispatcher

#: Direction discriminator on a :class:`TransferModel`.
TransferDirection = Literal["upload", "download", "local-copy", "s3-copy"]


class TransferState(StrEnum):
    """State machine values; mirrors the literal in :mod:`aws_tui.vm.messages`."""

    PENDING = "pending"
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass(frozen=True, slots=True)
class TransferModel:
    """Immutable description of a single transfer's progress."""

    id: str
    direction: TransferDirection
    source_label: str
    destination_label: str
    bytes_done: int
    bytes_total: int | None
    state: TransferState
    error: str | None = None


class TransferVM:
    """Facade for one transfer entry."""

    def __init__(
        self,
        model: TransferModel,
        *,
        hub: MessageHub[Message],
        dispatcher: Dispatcher,
    ) -> None:
        self._hub: MessageHub[Message] = hub
        self._dispatcher: Dispatcher = dispatcher

        self._inner: ComponentVMOf[TransferModel] = (
            ComponentVMOf[TransferModel]
            .builder()
            .name(f"transfer.{model.id}")
            .model(model)
            .services(hub, dispatcher)
            .build()
        )

        self._cancel_command: RelayCommand = (
            RelayCommand.builder().predicate(self._can_cancel).task(self._cancel).build()
        )
        self._retry_command: RelayCommand = (
            RelayCommand.builder().predicate(self._can_retry).task(self._retry).build()
        )

    # ── Properties ──────────────────────────────────────────────────────────

    @property
    def model(self) -> TransferModel:
        return self._inner.model

    @property
    def id(self) -> str:
        return self._inner.model.id

    @property
    def state(self) -> TransferState:
        return self._inner.model.state

    @property
    def is_active(self) -> bool:
        return self._inner.model.state in (TransferState.RUNNING, TransferState.PAUSED)

    @property
    def is_finished(self) -> bool:
        return self._inner.model.state in (
            TransferState.COMPLETED,
            TransferState.FAILED,
            TransferState.CANCELLED,
        )

    @property
    def cancel_command(self) -> RelayCommand:
        return self._cancel_command

    @property
    def retry_command(self) -> RelayCommand:
        return self._retry_command

    @property
    def status(self) -> ConstructionStatus:
        return self._inner.status

    @property
    def is_constructed(self) -> bool:
        return self._inner.is_constructed

    @property
    def name(self) -> str:
        return self._inner.name

    @property
    def inner(self) -> ComponentVMOf[TransferModel]:
        return self._inner

    # ── Lifecycle ───────────────────────────────────────────────────────────

    def construct(self) -> None:
        self._inner.construct()

    def destruct(self) -> None:
        self._inner.destruct()

    def dispose(self) -> None:
        self._cancel_command.dispose()
        self._retry_command.dispose()
        self._inner.dispose()

    # ── Mutators (driven by TransfersVM from the hub) ──────────────────────

    def apply_update(
        self,
        *,
        bytes_done: int,
        bytes_total: int | None,
        state: TransferState,
        error: str | None = None,
    ) -> None:
        new = replace(
            self._inner.model,
            bytes_done=bytes_done,
            bytes_total=bytes_total,
            state=state,
            error=error,
        )
        if new == self._inner.model:
            return
        self._inner.model = new
        self._hub.send(PropertyChangedMessage.create(self, self._inner.name, "state"))

    # ── Command predicates / handlers ──────────────────────────────────────

    def _can_cancel(self) -> bool:
        return self._inner.model.state in (
            TransferState.PENDING,
            TransferState.RUNNING,
            TransferState.PAUSED,
        )

    def _can_retry(self) -> bool:
        return self._inner.model.state in (TransferState.FAILED, TransferState.CANCELLED)

    def _cancel(self) -> None:
        self.apply_update(
            bytes_done=self._inner.model.bytes_done,
            bytes_total=self._inner.model.bytes_total,
            state=TransferState.CANCELLED,
        )

    def _retry(self) -> None:
        self.apply_update(
            bytes_done=0,
            bytes_total=self._inner.model.bytes_total,
            state=TransferState.PENDING,
            error=None,
        )


__all__ = ["TransferDirection", "TransferModel", "TransferState", "TransferVM"]
