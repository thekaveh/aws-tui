"""Tests for the custom message envelopes."""

from __future__ import annotations

import pytest
from vmx.messages.protocols import Message

from aws_tui.domain.data_catalog import TableRef
from aws_tui.infra.aws_session import TokenState
from aws_tui.infra.connection_resolver import Connection
from aws_tui.vm.messages import (
    ConnectionChangedMessage,
    CopyTableReferenceRequest,
    FocusChangedMessage,
    OpenAthenaTableRequest,
    OpenGlueTableRequest,
    OpenS3LocationRequest,
    ServiceOperationFailedMessage,
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
        lambda: TransferProgressMessage(
            transfer_id="t1",
            bytes_transferred=10,
            bytes_total=100,
            state="running",
        ),
        lambda: FocusChangedMessage(focused_vm_id="pane.left"),
        lambda: OpenS3LocationRequest(
            connection_name="prod-west",
            region="us-west-2",
            uri="s3://lake/events/",
        ),
        lambda: OpenAthenaTableRequest(
            table_ref=TableRef(
                "AwsDataCatalog",
                "analytics",
                "events",
                "prod-west",
                "us-west-2",
            ),
            snapshot_id=42,
        ),
        lambda: CopyTableReferenceRequest(
            table_ref=TableRef(
                "AwsDataCatalog",
                "analytics",
                "events",
                "prod-west",
                "us-west-2",
            )
        ),
        lambda: OpenGlueTableRequest(
            table_ref=TableRef(
                "AwsDataCatalog",
                "analytics",
                "events",
                "prod-west",
                "us-west-2",
            )
        ),
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


def test_service_failure_retains_redacted_traceback_text() -> None:
    try:
        raise RuntimeError("Authorization: Bearer TRACE_SECRET")
    except RuntimeError as error:
        message = ServiceOperationFailedMessage.from_error(
            service="athena",
            operation="list_catalogs",
            error=error,
        )

    assert "test_service_failure_retains_redacted_traceback_text" in message.safe_traceback
    assert "RuntimeError" in message.safe_traceback
    assert "TRACE_SECRET" not in message.safe_traceback
    assert "Authorization: Bearer [REDACTED]" in message.safe_traceback


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


def test_focus_changed_round_trip() -> None:
    msg = FocusChangedMessage(focused_vm_id="pane.left")
    assert msg.focused_vm_id == "pane.left"


def test_open_s3_request_carries_source_identity() -> None:
    request = OpenS3LocationRequest(
        connection_name="prod-west",
        region="us-west-2",
        uri="s3://lake/results/query.csv",
        preferred_pane="left",
        reveal_object=True,
    )

    assert request.connection_name == "prod-west"
    assert request.region == "us-west-2"
    assert request.uri == "s3://lake/results/query.csv"
    assert request.preferred_pane == "left"
    assert request.reveal_object is True
    assert request.sender_name == "service_navigation"
    assert request.sender_object is request


def test_cross_service_messages_preserve_table_security_context() -> None:
    table = TableRef(
        "AwsDataCatalog",
        "analytics",
        "events",
        "prod-west",
        "us-west-2",
    )

    athena = OpenAthenaTableRequest(table_ref=table, snapshot_id=42)
    glue = OpenGlueTableRequest(table_ref=table)
    copied = CopyTableReferenceRequest(table_ref=table)

    assert athena.table_ref == glue.table_ref == copied.table_ref == table
    assert athena.snapshot_id == 42
    assert athena.sender_name == glue.sender_name == "service_navigation"
    assert athena.sender_object is athena
    assert glue.sender_object is glue
    assert copied.sender_name == "service_navigation"
    assert copied.sender_object is copied


@pytest.mark.parametrize(
    "message",
    [
        OpenAthenaTableRequest(
            TableRef(
                "AwsDataCatalog",
                "analytics",
                "events",
                "prod-west",
                "us-west-2",
            )
        ),
        OpenGlueTableRequest(
            TableRef(
                "AwsDataCatalog",
                "analytics",
                "events",
                "prod-west",
                "us-west-2",
            )
        ),
        CopyTableReferenceRequest(
            TableRef(
                "AwsDataCatalog",
                "analytics",
                "events",
                "prod-west",
                "us-west-2",
            )
        ),
    ],
)
def test_cross_service_messages_are_frozen_and_slot_backed(message: object) -> None:
    with pytest.raises((AttributeError, TypeError)):
        message.table_ref = None  # type: ignore[attr-defined]
    with pytest.raises((AttributeError, TypeError)):
        message.unplanned_field = "value"  # type: ignore[attr-defined]


@pytest.mark.parametrize(
    "factory",
    [
        lambda value: OpenAthenaTableRequest(value),
        lambda value: OpenGlueTableRequest(value),
        lambda value: CopyTableReferenceRequest(value),
    ],
)
@pytest.mark.parametrize(
    "invalid_ref",
    [
        None,
        object(),
        {
            "catalog_name": "AwsDataCatalog",
            "database_name": "analytics",
            "table_name": "events",
            "connection_name": "prod-west",
            "region": "us-west-2",
        },
    ],
)
def test_cross_service_messages_require_exact_table_ref_type(
    factory: object,
    invalid_ref: object,
) -> None:
    with pytest.raises(ValueError, match=r"^table reference is invalid$"):
        factory(invalid_ref)  # type: ignore[operator]


@pytest.mark.parametrize(
    "field",
    [
        "catalog_name",
        "database_name",
        "table_name",
        "connection_name",
        "region",
    ],
)
@pytest.mark.parametrize("value", ["", "   ", None, 7, True])
@pytest.mark.parametrize(
    "message_type",
    [OpenAthenaTableRequest, OpenGlueTableRequest, CopyTableReferenceRequest],
)
def test_cross_service_messages_require_nonempty_plain_string_identity_fields(
    field: str,
    value: object,
    message_type: object,
) -> None:
    values: dict[str, object] = {
        "catalog_name": "AwsDataCatalog",
        "database_name": "analytics",
        "table_name": "events",
        "connection_name": "prod-west",
        "region": "us-west-2",
    }
    values[field] = value
    ref = TableRef(**values)  # type: ignore[arg-type]

    with pytest.raises(ValueError, match=r"^table reference is invalid$"):
        message_type(ref)  # type: ignore[operator]


@pytest.mark.parametrize("snapshot_id", [-1, True, 1.5, "42", object()])
def test_open_athena_table_request_requires_exact_non_negative_snapshot_id(
    snapshot_id: object,
) -> None:
    ref = TableRef(
        "AwsDataCatalog",
        "analytics",
        "events",
        "prod-west",
        "us-west-2",
    )

    with pytest.raises(ValueError, match=r"^snapshot ID is invalid$"):
        OpenAthenaTableRequest(ref, snapshot_id=snapshot_id)  # type: ignore[arg-type]


def test_messages_are_immutable() -> None:
    msg = ThemeChangedMessage(name="carbon")
    with pytest.raises(AttributeError):
        msg.name = "voidline"  # type: ignore[misc]


def test_messages_use_slots() -> None:
    # Slots dataclasses reject arbitrary attribute assignment.
    msg = FocusChangedMessage(focused_vm_id="x")
    with pytest.raises((AttributeError, TypeError)):
        msg.random_attr = "y"  # type: ignore[attr-defined]


def test_connection_list_changed_message_shape():
    from aws_tui.vm.messages import ConnectionListChangedMessage

    msg = ConnectionListChangedMessage(
        names=("minio-local", "ceph-staging"),
        change="updated",
    )
    assert msg.names == ("minio-local", "ceph-staging")
    assert msg.change == "updated"
    assert msg.sender_name == "s3_connections"
    assert msg.sender_object is msg


def test_connection_list_changed_message_is_frozen():
    import dataclasses

    from aws_tui.vm.messages import ConnectionListChangedMessage

    msg = ConnectionListChangedMessage(names=("x",), change="added")
    with pytest.raises(dataclasses.FrozenInstanceError):
        msg.change = "deleted"  # type: ignore[misc]
