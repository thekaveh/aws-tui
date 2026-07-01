"""Service-protocol tests for EmrServerlessService.

Pins the 🔥 descriptor icon + supports(connection.kind == 'aws') contract
so the nav rail correctly filters out s3-compatible connections."""

from __future__ import annotations

from vmx import NULL_DISPATCHER, MessageHub
from vmx.messages.protocols import Message

from aws_tui.domain.emr_logs import EmrServerlessLogsClient
from aws_tui.domain.emr_serverless import EMR_BOTO_CONFIG
from aws_tui.infra.connection_resolver import Connection
from aws_tui.services.emr_serverless import service as service_module
from aws_tui.services.emr_serverless.service import EmrServerlessService
from aws_tui.vm.services_protocol import ServiceDescriptor


def test_package_facade_exports_service_contract() -> None:
    from aws_tui.services.emr_serverless import (
        EmrClientFactory,
        EmrLogsClientFactory,
    )
    from aws_tui.services.emr_serverless import (
        EmrServerlessService as FacadeService,
    )

    assert FacadeService is EmrServerlessService
    assert EmrClientFactory is service_module.EmrClientFactory
    assert EmrLogsClientFactory is service_module.EmrLogsClientFactory


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


def test_build_vm_threads_explicit_logs_client_factory() -> None:
    hub: MessageHub[Message] = MessageHub()
    client = object()
    logs_client = EmrServerlessLogsClient(
        session=object(),  # type: ignore[arg-type]
        region_name="us-east-1",
    )
    svc = EmrServerlessService(
        hub=hub,
        dispatcher=NULL_DISPATCHER,
        emr_client_factory=lambda _conn: client,
        emr_logs_client_factory=lambda _conn: logs_client,
    )
    page = svc.build_vm(
        Connection(name="dev", kind="aws", region="us-east-1", source="config", profile="dev")
    )

    assert page.client is client
    assert page.job_run_logs._client is logs_client  # type: ignore[attr-defined]


def test_build_vm_uses_injected_client_logs_factory() -> None:
    class _InjectedClient:
        def __init__(self, logs_client: EmrServerlessLogsClient) -> None:
            self.logs_client = logs_client

        def make_logs_client(self) -> EmrServerlessLogsClient:
            return self.logs_client

    hub: MessageHub[Message] = MessageHub()
    logs_client = EmrServerlessLogsClient(
        session=object(),  # type: ignore[arg-type]
        region_name="us-east-1",
    )
    client = _InjectedClient(logs_client)
    svc = EmrServerlessService(
        hub=hub,
        dispatcher=NULL_DISPATCHER,
        emr_client_factory=lambda _conn: client,
    )
    page = svc.build_vm(
        Connection(name="dev", kind="aws", region="us-east-1", source="config", profile="dev")
    )

    assert page.client is client
    assert page.job_run_logs._client is logs_client  # type: ignore[attr-defined]


def test_build_vm_default_logs_client_uses_connection_session(monkeypatch: object) -> None:
    sessions: list[object] = []
    calls: list[dict[str, str | None]] = []

    class _Session:
        def __init__(self, *, profile_name: str | None, region_name: str | None) -> None:
            calls.append({"profile_name": profile_name, "region_name": region_name})
            sessions.append(self)

    monkeypatch.setattr(service_module.aioboto3, "Session", _Session)  # type: ignore[attr-defined]
    hub: MessageHub[Message] = MessageHub()
    svc = EmrServerlessService(hub=hub, dispatcher=NULL_DISPATCHER)

    page = svc.build_vm(
        Connection(name="dev", kind="aws", region="us-west-2", source="config", profile="dev")
    )

    assert calls == [
        {"profile_name": "dev", "region_name": "us-west-2"},
        {"profile_name": "dev", "region_name": "us-west-2"},
    ]
    assert page.client._session is sessions[0]  # type: ignore[attr-defined]
    assert page.job_run_logs._client.session is sessions[1]  # type: ignore[attr-defined]
    assert page.job_run_logs._client.region_name == "us-west-2"  # type: ignore[attr-defined]
    assert page.job_run_logs._client.boto_config is EMR_BOTO_CONFIG  # type: ignore[attr-defined]
