"""Service-protocol tests for EmrServerlessService.

Pins the ⚡ icon + supports(connection.kind == 'aws') contract so
the nav rail correctly filters on s3-compatible connections."""

from __future__ import annotations

from vmx import NULL_DISPATCHER, MessageHub
from vmx.messages.protocols import Message

from aws_tui.infra.connection_resolver import Connection
from aws_tui.services.emr_serverless.service import EmrServerlessService
from aws_tui.vm.services_protocol import ServiceDescriptor


def test_descriptor_icon_is_fire_smp_emoji_label_is_emr() -> None:
    # 🔥 = U+1F525 FIRE — SMP single-codepoint emoji. Picked over
    # ⚡ / ⚡️ (U+26A1 / U+26A1 + VS-16) after PR #77's VS-16 fix
    # ALSO failed: BMP codepoints with VS-16 still fall back to a
    # 1-cell text glyph in many monospace fonts, mis-aligning the
    # nav-rail's 2-cell emoji column and garbling the whole row.
    # SMP emojis ship as 2-cell colour reliably because there's no
    # text-presentation alternative. See ``service.py:descriptor``
    # docstring for the general rule.
    assert EmrServerlessService.descriptor == ServiceDescriptor(
        id="emr-serverless", label="EMR", icon="🔥"
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
