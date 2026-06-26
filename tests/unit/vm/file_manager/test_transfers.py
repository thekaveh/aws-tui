"""Tests for TransferVM + TransfersVM."""

from __future__ import annotations

from typing import cast

from vmx import NULL_DISPATCHER, MessageHub
from vmx.lifecycle.status import ConstructionStatus
from vmx.messages.protocols import Message

from aws_tui.vm.file_manager.transfer_vm import TransferModel, TransferState, TransferVM
from aws_tui.vm.file_manager.transfers_vm import TransfersVM
from aws_tui.vm.messages import TransferProgressMessage


def _hub() -> MessageHub[Message]:
    return cast("MessageHub[Message]", MessageHub())


def _model(
    *,
    id: str = "t1",
    state: TransferState = TransferState.PENDING,
    bytes_done: int = 0,
    bytes_total: int | None = 1000,
) -> TransferModel:
    return TransferModel(
        id=id,
        direction="upload",
        source_label=f"local:///{id}",
        destination_label=f"s3://bucket/{id}",
        bytes_done=bytes_done,
        bytes_total=bytes_total,
        state=state,
    )


def test_transfer_vm_construct_dispose() -> None:
    vm = TransferVM(_model(), hub=_hub(), dispatcher=NULL_DISPATCHER)
    vm.construct()
    assert vm.is_constructed
    vm.dispose()
    assert vm.status == ConstructionStatus.DISPOSED


def test_transfer_vm_apply_update_publishes() -> None:
    hub = _hub()
    received: list[str] = []
    hub.messages.subscribe(
        on_next=lambda m: received.append(getattr(m, "property_name", "")) if m else None
    )
    vm = TransferVM(_model(), hub=hub, dispatcher=NULL_DISPATCHER)
    vm.construct()
    vm.apply_update(bytes_done=500, bytes_total=1000, state=TransferState.RUNNING)
    assert vm.state == TransferState.RUNNING
    assert "state" in received
    vm.dispose()


def test_transfer_vm_cancel_command() -> None:
    vm = TransferVM(_model(state=TransferState.RUNNING), hub=_hub(), dispatcher=NULL_DISPATCHER)
    vm.construct()
    assert vm.cancel_command.can_execute()
    vm.cancel_command.execute()
    assert vm.state == TransferState.CANCELLED
    assert not vm.cancel_command.can_execute()
    vm.dispose()


def test_transfer_vm_retry_command_from_failed() -> None:
    vm = TransferVM(_model(state=TransferState.FAILED), hub=_hub(), dispatcher=NULL_DISPATCHER)
    vm.construct()
    assert vm.retry_command.can_execute()
    vm.retry_command.execute()
    assert vm.state == TransferState.PENDING
    vm.dispose()


def test_transfer_vm_is_finished_property_covers_three_terminal_states() -> None:
    """``is_finished`` is True for COMPLETED / FAILED / CANCELLED and
    False for PENDING / RUNNING / PAUSED — the contract used by the
    Pass-1 terminal-stickiness guard in ``apply_update``."""
    for terminal in (TransferState.COMPLETED, TransferState.FAILED, TransferState.CANCELLED):
        vm = TransferVM(_model(state=terminal), hub=_hub(), dispatcher=NULL_DISPATCHER)
        vm.construct()
        assert vm.is_finished, f"{terminal} must be finished"
        vm.dispose()
    for active in (TransferState.PENDING, TransferState.RUNNING, TransferState.PAUSED):
        vm = TransferVM(_model(state=active), hub=_hub(), dispatcher=NULL_DISPATCHER)
        vm.construct()
        assert not vm.is_finished, f"{active} must NOT be finished"
        vm.dispose()


def test_transfer_vm_terminal_state_is_sticky() -> None:
    """A late RUNNING progress event after CANCELLED must not clobber
    the terminal flag — otherwise the row reverts to ``... 73 %`` after
    the user clicked cancel."""
    vm = TransferVM(_model(state=TransferState.RUNNING), hub=_hub(), dispatcher=NULL_DISPATCHER)
    vm.construct()
    # User cancels — TransferVM transitions to CANCELLED.
    vm.cancel_command.execute()
    assert vm.state == TransferState.CANCELLED
    # A stale in-flight progress event arrives a moment later.
    vm.apply_update(bytes_done=730, bytes_total=1000, state=TransferState.RUNNING)
    # State must STAY cancelled.
    assert vm.state == TransferState.CANCELLED
    # Also: a non-terminal PAUSED/PENDING must not revive the row.
    vm.apply_update(bytes_done=730, bytes_total=1000, state=TransferState.PAUSED)
    assert vm.state == TransferState.CANCELLED
    vm.dispose()


def test_transfer_vm_terminal_to_terminal_is_allowed() -> None:
    """A terminal → terminal transition (e.g. FAILED arriving while
    already CANCELLED) is allowed so the final error can be recorded.
    """
    vm = TransferVM(_model(state=TransferState.CANCELLED), hub=_hub(), dispatcher=NULL_DISPATCHER)
    vm.construct()
    vm.apply_update(
        bytes_done=0,
        bytes_total=1000,
        state=TransferState.FAILED,
        error="boom",
    )
    assert vm.state == TransferState.FAILED
    assert vm.model.error == "boom"
    vm.dispose()


def test_transfers_vm_register_and_active_count() -> None:
    hub = _hub()
    tvms = TransfersVM(hub=hub, dispatcher=NULL_DISPATCHER)
    tvms.construct()
    tvms.register(_model(id="a", state=TransferState.RUNNING))
    tvms.register(_model(id="b", state=TransferState.RUNNING))
    tvms.register(_model(id="c", state=TransferState.PENDING))
    assert tvms.active_count == 3
    # Complete one — active_count drops to 2.
    tvms.update("a", bytes_done=1000, bytes_total=1000, state=TransferState.COMPLETED)
    assert tvms.active_count == 2
    assert len(tvms.finished) == 1
    tvms.dispose()


def test_transfers_vm_subscribes_to_progress_messages() -> None:
    hub = _hub()
    tvms = TransfersVM(hub=hub, dispatcher=NULL_DISPATCHER)
    tvms.construct()
    tvms.register(_model(id="a", state=TransferState.RUNNING))
    hub.send(
        TransferProgressMessage(
            transfer_id="a",
            bytes_transferred=750,
            bytes_total=1000,
            state=TransferState.RUNNING,
        )
    )
    a = tvms.transfers[0]
    assert a.model.bytes_done == 750
    hub.send(
        TransferProgressMessage(
            transfer_id="a",
            bytes_transferred=1000,
            bytes_total=1000,
            state=TransferState.COMPLETED,
        )
    )
    assert a.state == TransferState.COMPLETED
    tvms.dispose()


def test_transfers_vm_auto_registers_unknown_id() -> None:
    hub = _hub()
    tvms = TransfersVM(hub=hub, dispatcher=NULL_DISPATCHER)
    tvms.construct()
    hub.send(
        TransferProgressMessage(
            transfer_id="new",
            bytes_transferred=10,
            bytes_total=100,
            state=TransferState.RUNNING,
        )
    )
    assert tvms.active_count == 1
    assert tvms.transfers[0].id == "new"
    tvms.dispose()


def test_transfers_cancel_all_command() -> None:
    hub = _hub()
    tvms = TransfersVM(hub=hub, dispatcher=NULL_DISPATCHER)
    tvms.construct()
    tvms.register(_model(id="a", state=TransferState.RUNNING))
    tvms.register(_model(id="b", state=TransferState.PENDING))
    tvms.register(_model(id="c", state=TransferState.COMPLETED))
    assert tvms.cancel_all_command.can_execute()
    tvms.cancel_all_command.execute()
    states = {t.id: t.state for t in tvms.transfers}
    assert states["a"] == TransferState.CANCELLED
    assert states["b"] == TransferState.CANCELLED
    assert states["c"] == TransferState.COMPLETED  # untouched
    tvms.dispose()


def test_transfers_total_bytes_aggregation() -> None:
    hub = _hub()
    tvms = TransfersVM(hub=hub, dispatcher=NULL_DISPATCHER)
    tvms.construct()
    tvms.register(_model(id="a", state=TransferState.RUNNING, bytes_done=100, bytes_total=200))
    tvms.register(_model(id="b", state=TransferState.RUNNING, bytes_done=300, bytes_total=500))
    assert tvms.total_bytes_done == 400
    assert tvms.total_bytes_total == 700
    # If any transfer has unknown total, aggregate becomes None.
    tvms.register(_model(id="c", state=TransferState.RUNNING, bytes_done=0, bytes_total=None))
    assert tvms.total_bytes_total is None
    tvms.dispose()


def test_auto_register_infers_direction_from_uri_schemes() -> None:
    """When a TransferProgressMessage arrives for an unknown transfer
    id, ``TransfersVM`` auto-registers a placeholder ``TransferModel``
    and must infer the ``direction`` from the ``source_label`` /
    ``destination_label`` URI schemes — not hard-code one direction.

    Locks in the V4-001 producer→consumer wiring fix:
    ``DualPaneVM._pane_uri`` now emits a scheme-prefixed URI
    (``s3://...`` for S3 panes, plain ``/...`` for local) so
    ``TransfersVM._infer_direction`` can correctly classify the
    transfer as upload / download / s3-copy / local-copy.
    """
    pairs = [
        ("upload", "/Users/kaveh/foo.txt", "s3://bucket/foo.txt"),
        ("download", "s3://bucket/foo.txt", "/Users/kaveh/foo.txt"),
        ("s3-copy", "s3://src-bucket/foo.txt", "s3://dst-bucket/foo.txt"),
        ("local-copy", "/Users/kaveh/foo.txt", "/Users/kaveh/bar.txt"),
    ]
    for expected, src, dst in pairs:
        hub = _hub()
        tvms = TransfersVM(hub=hub, dispatcher=NULL_DISPATCHER)
        tvms.construct()
        hub.send(
            TransferProgressMessage(
                transfer_id="x",
                bytes_transferred=0,
                bytes_total=100,
                state=TransferState.PENDING,
                source_label=src,
                destination_label=dst,
            )
        )
        assert tvms.transfers, f"placeholder not auto-registered for {src!r}→{dst!r}"
        actual = tvms.transfers[0].model.direction
        assert actual == expected, (
            f"{src!r} → {dst!r}: expected direction={expected!r}, got {actual!r}"
        )
        tvms.dispose()


def test_transfers_register_vm_accepts_prebuilt_transfer_vm() -> None:
    """register_vm(vm) lets a caller (notably the snapshot harness)
    construct a TransferVM with a custom clock and register it directly,
    bypassing register(model) which builds its own production-clock VM.
    """
    hub = _hub()
    tvms = TransfersVM(hub=hub, dispatcher=NULL_DISPATCHER)
    tvms.construct()
    # Pre-build a TransferVM with a fake clock; populate the speed window.
    ticks = iter([0.0, 1.0])
    vm = TransferVM(
        _model(id="custom", state=TransferState.RUNNING, bytes_done=0, bytes_total=1_000_000),
        hub=hub,
        dispatcher=NULL_DISPATCHER,
        clock=lambda: next(ticks),
    )
    vm.construct()
    vm.apply_update(bytes_done=0, bytes_total=1_000_000, state=TransferState.RUNNING)
    vm.apply_update(bytes_done=500_000, bytes_total=1_000_000, state=TransferState.RUNNING)

    returned = tvms.register_vm(vm)
    assert returned is vm  # identity preserved
    assert any(t.id == "custom" for t in tvms.transfers)
    # Speed survived because the pre-built VM kept its clock + samples.
    assert returned.current_speed == 500_000.0

    # Idempotent on id collision — re-registering returns the same VM, doesn't dupe.
    duplicate = TransferVM(
        _model(id="custom", state=TransferState.RUNNING),
        hub=hub,
        dispatcher=NULL_DISPATCHER,
    )
    duplicate.construct()
    assert tvms.register_vm(duplicate) is vm  # original wins
    assert sum(1 for t in tvms.transfers if t.id == "custom") == 1
    tvms.dispose()
