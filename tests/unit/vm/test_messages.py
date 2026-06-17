"""Tests for the custom message envelopes."""

from __future__ import annotations

import pytest
from vmx.messages.protocols import Message

from aws_tui.infra.aws_session import TokenState
from aws_tui.infra.connection_resolver import Connection
from aws_tui.vm.messages import (
    AuthExpiredMessage,
    ConnectionChangedMessage,
    FocusChangedMessage,
    KeymapChangedMessage,
    ThemeChangedMessage,
    TransferProgressMessage,
)


def _connection() -> Connection:
    return Connection(
        name="kaveh-dev",
        kind="aws",
        region="us-east-1",
        source="config",
        profile="kaveh-dev",
    )


@pytest.mark.parametrize(
    "factory",
    [
        lambda: ConnectionChangedMessage(
            connection=_connection(),
            auth_state=TokenState.CONNECTED,
        ),
        lambda: ThemeChangedMessage(name="voidline"),
        lambda: AuthExpiredMessage(
            connection_name="kaveh-prod",
            reason="expired",
        ),
        lambda: TransferProgressMessage(
            transfer_id="t1",
            bytes_transferred=10,
            bytes_total=100,
            state="running",
        ),
        lambda: KeymapChangedMessage(action="pane.copy", new_keys=("c",)),
        lambda: FocusChangedMessage(focused_vm_id="pane.left"),
    ],
)
def test_message_satisfies_vmx_message_protocol(factory: object) -> None:
    msg = factory()  # type: ignore[operator]
    assert isinstance(msg, Message)
    # Both protocol attributes resolve.
    assert isinstance(msg.sender_name, str)
    _ = msg.sender_object


def test_connection_changed_round_trip() -> None:
    conn = _connection()
    msg = ConnectionChangedMessage(connection=conn, auth_state=TokenState.CONNECTED)
    assert msg.connection is conn
    assert msg.auth_state is TokenState.CONNECTED
    assert msg.sender_name == "root"
    assert msg.sender_object is msg


def test_theme_changed_round_trip() -> None:
    msg = ThemeChangedMessage(name="amber")
    assert msg.name == "amber"
    assert msg.sender_name == "root"


def test_auth_expired_round_trip() -> None:
    msg = AuthExpiredMessage(connection_name="kaveh-prod", reason="expired")
    assert msg.connection_name == "kaveh-prod"
    assert msg.reason == "expired"
    assert msg.sender_name == "aws_session"


def test_transfer_progress_round_trip() -> None:
    msg = TransferProgressMessage(
        transfer_id="t1", bytes_transferred=10, bytes_total=100, state="running"
    )
    assert msg.transfer_id == "t1"
    assert msg.bytes_transferred == 10
    assert msg.bytes_total == 100
    assert msg.state == "running"


def test_transfer_progress_allows_unknown_total() -> None:
    msg = TransferProgressMessage(
        transfer_id="t1", bytes_transferred=10, bytes_total=None, state="running"
    )
    assert msg.bytes_total is None


def test_keymap_changed_round_trip() -> None:
    msg = KeymapChangedMessage(action="pane.copy", new_keys=("c",))
    assert msg.action == "pane.copy"
    assert msg.new_keys == ("c",)


def test_focus_changed_round_trip() -> None:
    msg = FocusChangedMessage(focused_vm_id="pane.left")
    assert msg.focused_vm_id == "pane.left"


def test_messages_are_immutable() -> None:
    msg = ThemeChangedMessage(name="carbon")
    with pytest.raises(AttributeError):
        msg.name = "voidline"  # type: ignore[misc]


def test_messages_use_slots() -> None:
    # Slots dataclasses reject arbitrary attribute assignment.
    msg = FocusChangedMessage(focused_vm_id="x")
    with pytest.raises((AttributeError, TypeError)):
        msg.random_attr = "y"  # type: ignore[attr-defined]
