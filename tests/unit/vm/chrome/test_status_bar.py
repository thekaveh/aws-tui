"""Tests for the StatusBarVM."""

from __future__ import annotations

from typing import cast

from vmx import NULL_DISPATCHER, MessageHub
from vmx.messages.protocols import Message

from aws_tui.infra.aws_session import TokenState
from aws_tui.infra.connection_resolver import Connection
from aws_tui.vm.chrome.status_bar_vm import StatusBarVM
from aws_tui.vm.messages import ConnectionChangedMessage, TransferProgressMessage


def _hub() -> MessageHub[Message]:
    return cast("MessageHub[Message]", MessageHub())


def _aws_conn() -> Connection:
    return Connection(
        name="kaveh-dev",
        kind="aws",
        region="us-east-1",
        source="config",
        profile="kaveh-dev",
    )


def _minio_conn() -> Connection:
    return Connection(
        name="minio-local",
        kind="s3-compatible",
        region="us-east-1",
        source="config",
        endpoint_url="http://localhost:9000",
    )


def _build() -> tuple[StatusBarVM, MessageHub[Message]]:
    hub = _hub()
    vm = StatusBarVM(hub=hub, dispatcher=NULL_DISPATCHER)
    vm.construct()
    return vm, hub


def test_initial_state_has_placeholders() -> None:
    vm, _hub = _build()
    assert vm.connection_label == "no connection"
    assert vm.region == ""
    assert vm.auth_indicator == "no session"
    assert vm.transfers_summary == "transfers idle"
    vm.dispose()


def test_update_connection_aws() -> None:
    vm, _hub = _build()
    vm.update_connection(_aws_conn(), TokenState.CONNECTED)
    assert vm.connection_label == "kaveh-dev (aws)"
    assert vm.region == "us-east-1"
    assert vm.auth_indicator == "sso ok"
    vm.dispose()


def test_update_connection_aws_expired() -> None:
    vm, _ = _build()
    vm.update_connection(_aws_conn(), TokenState.EXPIRED)
    assert vm.auth_indicator == "login needed"
    vm.dispose()


def test_update_connection_s3_compatible() -> None:
    vm, _ = _build()
    vm.update_connection(_minio_conn(), TokenState.CONNECTED)
    assert vm.connection_label == "minio-local (s3-compatible)"
    assert vm.auth_indicator == "keys"
    vm.dispose()


def test_update_transfers_idle() -> None:
    vm, _ = _build()
    vm.update_transfers(active_count=0, bytes_done=0, bytes_total=0)
    assert vm.transfers_summary == "transfers idle"
    vm.dispose()


def test_update_transfers_with_active() -> None:
    vm, _ = _build()
    vm.update_transfers(active_count=2, bytes_done=12_400_000, bytes_total=18_000_000)
    # human-readable mega-byte rounding to one decimal.
    assert vm.transfers_summary == "2 active . 12.4 M / 18.0 M"
    vm.dispose()


def test_update_transfers_unknown_total() -> None:
    vm, _ = _build()
    vm.update_transfers(active_count=1, bytes_done=500_000, bytes_total=None)
    assert vm.transfers_summary == "1 active . 500.0 k / ?"
    vm.dispose()


def test_subscribes_to_connection_changed_message() -> None:
    vm, hub = _build()
    hub.send(ConnectionChangedMessage(connection=_aws_conn(), auth_state=TokenState.CONNECTED))
    assert vm.connection_label == "kaveh-dev (aws)"
    assert vm.auth_indicator == "sso ok"
    vm.dispose()


def test_subscribes_to_transfer_progress_message() -> None:
    vm, hub = _build()
    hub.send(
        TransferProgressMessage(
            transfer_id="t1",
            bytes_transferred=1_500_000,
            bytes_total=3_000_000,
            state="running",
        )
    )
    assert "1 active" in vm.transfers_summary
    vm.dispose()


def test_completed_transfer_decrements_count() -> None:
    vm, hub = _build()
    hub.send(
        TransferProgressMessage(
            transfer_id="t1",
            bytes_transferred=1_500_000,
            bytes_total=3_000_000,
            state="running",
        )
    )
    hub.send(
        TransferProgressMessage(
            transfer_id="t1",
            bytes_transferred=3_000_000,
            bytes_total=3_000_000,
            state="completed",
        )
    )
    assert vm.transfers_summary == "transfers idle"
    vm.dispose()


def test_dispose_cancels_subscriptions() -> None:
    vm, hub = _build()
    vm.dispose()
    # Sending another message after dispose must not crash.
    hub.send(ConnectionChangedMessage(connection=_aws_conn(), auth_state=TokenState.CONNECTED))
