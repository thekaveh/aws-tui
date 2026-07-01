"""TransferVM — single transfer facade.

A transfer is one of: upload (local → s3), download (s3 → local),
local-copy (local → local), or s3-copy (s3 → s3). The facade wraps a
:class:`ComponentVMOf[TransferModel]` so subscribers can bind to model
changes via :class:`PropertyChangedMessage` ("model").
"""

from __future__ import annotations

import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, replace
from typing import Literal

from vmx import ComponentVMOf, Message, MessageHub, PropertyChangedMessage, RelayCommand
from vmx.lifecycle.status import ConstructionStatus
from vmx.services.dispatcher import Dispatcher

from aws_tui.vm.messages import (
    TransferCancelRequestedMessage,
    TransferState,  # canonical: re-exported for callers
)

#: Direction discriminator on a :class:`TransferModel`.
TransferDirection = Literal["upload", "download", "local-copy", "s3-copy"]


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
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._hub: MessageHub[Message] = hub
        self._clock: Callable[[], float] = clock
        self._speed_window: deque[tuple[float, int]] = deque()

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
    def current_speed(self) -> float | None:
        """Bytes-per-second over the last 5 seconds of samples.

        Returns ``None`` if fewer than 2 samples or the sample window
        spans less than 250 ms (too short for a stable speed estimate).
        """
        if len(self._speed_window) < 2:
            return None
        first_ts, first_bytes = self._speed_window[0]
        last_ts, last_bytes = self._speed_window[-1]
        span = last_ts - first_ts
        if span < 0.25:
            return None
        delta = last_bytes - first_bytes
        return delta / span

    @property
    def current_eta(self) -> float | None:
        """Seconds remaining at current speed, or ``None`` if unknowable."""
        speed = self.current_speed
        total = self._inner.model.bytes_total
        if speed is None or speed <= 0 or total is None:
            return None
        remaining = total - self._inner.model.bytes_done
        if remaining <= 0:
            return 0.0
        return remaining / speed

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
        # Terminal-state stickiness: once a transfer reaches
        # CANCELLED / COMPLETED / FAILED, a late RUNNING progress
        # event from the underlying provider must not clobber the
        # terminal flag — that's how the "I clicked cancel but the
        # row keeps reporting 73 %" bug would surface. We refuse the
        # update unless the new state is itself terminal (so a late
        # FAILED can still be recorded alongside a prior CANCELLED).
        #
        # User-initiated transitions out of a terminal state (the
        # retry button → PENDING) bypass this guard by going through
        # :meth:`_reset_to_pending`; do NOT add ``PENDING`` to the
        # allow-list below or stale progress events from a finished
        # transfer's lingering generator would silently revive it.
        if self.is_finished:
            new_is_terminal = state in (
                TransferState.COMPLETED,
                TransferState.FAILED,
                TransferState.CANCELLED,
            )
            if not new_is_terminal:
                return
        self._apply_update_unchecked(
            bytes_done=bytes_done,
            bytes_total=bytes_total,
            state=state,
            error=error,
        )

    def _apply_update_unchecked(
        self,
        *,
        bytes_done: int,
        bytes_total: int | None,
        state: TransferState,
        error: str | None = None,
    ) -> None:
        """Lower-level mutator that skips terminal-state stickiness.

        Used by :meth:`_retry` (which deliberately transitions out of a
        terminal state in response to a user command) and tests that
        need to drive arbitrary transitions.
        """
        self._record_sample(bytes_done)
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
        # Two-part cancel: (1) immediate VM-state transition so the
        # overlay row flips to CANCELLED right away (UI feedback);
        # (2) publish a TransferCancelRequestedMessage so DualPaneVM
        # (which owns the in-flight CrossFsCopy task) can actually
        # interrupt the copy. Without (2), the row reads CANCELLED but
        # bytes keep transferring until the copy finishes naturally —
        # the user-reported "cancel doesn't work" bug.
        self.apply_update(
            bytes_done=self._inner.model.bytes_done,
            bytes_total=self._inner.model.bytes_total,
            state=TransferState.CANCELLED,
        )
        self._hub.send(TransferCancelRequestedMessage(transfer_id=self._inner.model.id))

    def _retry(self) -> None:
        # Retry must bypass terminal-state stickiness — by definition
        # we're transitioning OUT of FAILED / CANCELLED back to
        # PENDING in response to a user command.
        self._apply_update_unchecked(
            bytes_done=0,
            bytes_total=self._inner.model.bytes_total,
            state=TransferState.PENDING,
            error=None,
        )

    def _record_sample(self, bytes_done: int) -> None:
        """Append a sample to the rolling 5-second speed window."""
        now = self._clock()
        self._speed_window.append((now, bytes_done))
        # Prune samples older than 5 s. Always keep at least the most
        # recent sample so the next call still has a window.
        cutoff = now - 5.0
        while len(self._speed_window) > 1 and self._speed_window[0][0] < cutoff:
            self._speed_window.popleft()


__all__ = ["TransferDirection", "TransferModel", "TransferState", "TransferVM"]
