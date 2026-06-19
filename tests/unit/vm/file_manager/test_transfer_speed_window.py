"""Tests for TransferVM rolling speed window + ETA derivation."""

from __future__ import annotations

from typing import cast

from vmx import NULL_DISPATCHER, MessageHub
from vmx.messages.protocols import Message

from aws_tui.vm.file_manager.transfer_vm import TransferModel, TransferState, TransferVM


def _hub() -> MessageHub[Message]:
    return cast("MessageHub[Message]", MessageHub())


def _model(*, bytes_done: int = 0, bytes_total: int | None = 1_000_000) -> TransferModel:
    return TransferModel(
        id="t1",
        direction="upload",
        source_label="/src/file",
        destination_label="s3://bucket/file",
        bytes_done=bytes_done,
        bytes_total=bytes_total,
        state=TransferState.PENDING,
    )


def test_speed_is_none_with_fewer_than_two_samples() -> None:
    clock_values = iter([0.0])

    def fake_clock() -> float:
        return next(clock_values)

    vm = TransferVM(_model(), hub=_hub(), dispatcher=NULL_DISPATCHER, clock=fake_clock)
    vm.construct()
    try:
        vm.apply_update(bytes_done=100, bytes_total=1_000_000, state=TransferState.RUNNING)
        assert vm.current_speed is None
    finally:
        vm.dispose()


def test_speed_computed_after_two_samples() -> None:
    ticks = [0.0, 1.0]  # 1 second elapsed between samples
    clock_values = iter(ticks)
    vm = TransferVM(
        _model(), hub=_hub(), dispatcher=NULL_DISPATCHER, clock=lambda: next(clock_values)
    )
    vm.construct()
    try:
        vm.apply_update(bytes_done=100, bytes_total=1_000_000, state=TransferState.RUNNING)
        vm.apply_update(bytes_done=100_100, bytes_total=1_000_000, state=TransferState.RUNNING)
        # 100_000 bytes over 1 second
        assert vm.current_speed == 100_000.0
    finally:
        vm.dispose()


def test_speed_window_prunes_samples_older_than_5_seconds() -> None:
    # Three samples; the first (t=0.0) is older than 5s relative to the
    # third (t=5.5) and must be pruned. Cutoff = 5.5 - 5.0 = 0.5; t=0.0
    # < 0.5 → pruned; t=1.0 > 0.5 → survives; t=5.5 → survives.
    ticks = [0.0, 1.0, 5.5]
    clock_values = iter(ticks)
    vm = TransferVM(
        _model(), hub=_hub(), dispatcher=NULL_DISPATCHER, clock=lambda: next(clock_values)
    )
    vm.construct()
    try:
        vm.apply_update(bytes_done=0, bytes_total=10_000_000, state=TransferState.RUNNING)
        vm.apply_update(bytes_done=1_000_000, bytes_total=10_000_000, state=TransferState.RUNNING)
        vm.apply_update(bytes_done=2_000_000, bytes_total=10_000_000, state=TransferState.RUNNING)
        # Window: (1.0, 1_000_000) and (5.5, 2_000_000) -> 1_000_000 bytes
        # over 4.5 s = ~222_222.22 B/s
        assert vm.current_speed is not None
        assert abs(vm.current_speed - 222_222.222_222_222) < 0.01
    finally:
        vm.dispose()


def test_eta_computed_from_speed_and_remaining_bytes() -> None:
    ticks = [0.0, 1.0]
    clock_values = iter(ticks)
    vm = TransferVM(
        _model(bytes_total=1_000_000),
        hub=_hub(),
        dispatcher=NULL_DISPATCHER,
        clock=lambda: next(clock_values),
    )
    vm.construct()
    try:
        vm.apply_update(bytes_done=0, bytes_total=1_000_000, state=TransferState.RUNNING)
        vm.apply_update(bytes_done=100_000, bytes_total=1_000_000, state=TransferState.RUNNING)
        # 100_000 B/s, 900_000 B remaining -> 9 seconds
        assert vm.current_eta == 9.0
    finally:
        vm.dispose()


def test_eta_is_none_with_no_bytes_total() -> None:
    ticks = [0.0, 1.0]
    clock_values = iter(ticks)
    vm = TransferVM(
        _model(bytes_total=None),
        hub=_hub(),
        dispatcher=NULL_DISPATCHER,
        clock=lambda: next(clock_values),
    )
    vm.construct()
    try:
        vm.apply_update(bytes_done=0, bytes_total=None, state=TransferState.RUNNING)
        vm.apply_update(bytes_done=100_000, bytes_total=None, state=TransferState.RUNNING)
        assert vm.current_speed == 100_000.0
        assert vm.current_eta is None
    finally:
        vm.dispose()


def test_speed_window_requires_minimum_250ms_span() -> None:
    ticks = [0.0, 0.1]  # 100 ms span — too short
    clock_values = iter(ticks)
    vm = TransferVM(
        _model(), hub=_hub(), dispatcher=NULL_DISPATCHER, clock=lambda: next(clock_values)
    )
    vm.construct()
    try:
        vm.apply_update(bytes_done=0, bytes_total=1_000_000, state=TransferState.RUNNING)
        vm.apply_update(bytes_done=10_000, bytes_total=1_000_000, state=TransferState.RUNNING)
        # 100 ms window is below the 250 ms minimum
        assert vm.current_speed is None
    finally:
        vm.dispose()
