"""Service-protocol tests for EmrServerlessService.

Pins the ⚡ icon + supports(connection.kind == 'aws') contract so
the nav rail correctly filters on s3-compatible connections."""

from __future__ import annotations

from vmx import NULL_DISPATCHER, MessageHub
from vmx.messages.protocols import Message

from aws_tui.infra.connection_resolver import Connection
from aws_tui.services.emr_serverless.service import EmrServerlessService
from aws_tui.vm.services_protocol import ServiceDescriptor


def test_descriptor_icon_is_high_voltage_label_is_emr() -> None:
    assert EmrServerlessService.descriptor == ServiceDescriptor(
        id="emr-serverless", label="EMR", icon="⚡"
    )


def test_supports_aws_connection() -> None:
    hub: MessageHub[Message] = MessageHub()
    svc = EmrServerlessService(hub=hub, dispatcher=NULL_DISPATCHER)
    assert svc.supports(
        Connection(name="dev", kind="aws", region="us-east-1", source="config", profile="dev")
    )


def test_does_not_support_s3_compatible_connection() -> None:
    hub: MessageHub[Message] = MessageHub()
    svc = EmrServerlessService(hub=hub, dispatcher=NULL_DISPATCHER)
    assert not svc.supports(
        Connection(
            name="minio",
            kind="s3-compatible",
            region="us-east-1",
            source="config",
            endpoint_url="http://localhost:9000",
            access_key_id="x",
            secret_access_key="y",
        )
    )
