"""Service-protocol tests for EmrServerlessService.

Pins the 🔥 descriptor icon + supports(connection.kind == 'aws') contract
so the nav rail correctly filters out s3-compatible connections."""

from __future__ import annotations

from vmx import NULL_DISPATCHER, MessageHub
from vmx.messages.protocols import Message

from aws_tui.infra.connection_resolver import Connection
from aws_tui.services.emr_serverless import service as service_module
from aws_tui.services.emr_serverless.service import EmrServerlessService
from aws_tui.vm.services_protocol import ServiceDescriptor


def test_package_facade_exports_service_contract() -> None:
    from aws_tui.services.emr_serverless import (
        EmrClientFactory,
    )
    from aws_tui.services.emr_serverless import (
        EmrServerlessService as FacadeService,
    )

    assert FacadeService is EmrServerlessService
    assert EmrClientFactory is service_module.EmrClientFactory


def test_descriptor_icon_is_fire_smp_label_is_emr() -> None:
    # 🔥 = U+1F525 FIRE — SMP single-codepoint, renders as 2-cell
    # colour emoji reliably AND draws to the full bounding box.
    # Fifth icon attempt (back to PR #79's known-good pick after
    # the PR #83 💥 COLLISION glyph rendered with a tighter
    # bounding box than the 🪣 nav peer; user feedback). Trail:
    #   PR #77 ⚡    BMP, 1-cell fallback  — broke nav-rail layout
    #   PR #79 🔥    SMP, 2-cell, full box — worked
    #   PR #81 ⚡️   BMP+VS-16, fallback   — broke layout again
    #   PR #83 💥    SMP, tight box        — looked tiny vs 🪣
    #         🔥    SMP, 2-cell, full box  — here, back to known good
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
