from __future__ import annotations

import pytest
from vmx import NULL_DISPATCHER, MessageHub
from vmx.messages.protocols import Message

from aws_tui.domain.sql_policy import ReadOnlySqlPolicy
from aws_tui.infra.aws_session import AwsSession
from aws_tui.infra.connection_resolver import Connection
from aws_tui.services.athena import AthenaService
from aws_tui.vm.services_protocol import ServiceDescriptor
from tests.unit.vm.athena.test_page_vm import PageClient


def _connection(
    name: str,
    *,
    kind: str = "aws",
    region: str = "us-east-1",
) -> Connection:
    return Connection(
        name=name,
        kind=kind,
        region=region,
        source="test",
        profile=name if kind == "aws" else None,
        endpoint_url="http://localhost:9000" if kind != "aws" else None,
        access_key_id="key" if kind != "aws" else None,
        secret_access_key="secret" if kind != "aws" else None,
    )


def _service(**kwargs: object) -> AthenaService:
    hub: MessageHub[Message] = MessageHub()
    return AthenaService(
        hub=hub,
        dispatcher=NULL_DISPATCHER,
        aws_session=AwsSession(),
        **kwargs,  # type: ignore[arg-type]
    )


def test_athena_service_is_aws_only() -> None:
    service = _service()

    assert service.supports(_connection("dev"))
    assert not service.supports(_connection("minio", kind="s3-compatible"))


def test_athena_service_descriptor_matches_navigation_contract() -> None:
    descriptor = AthenaService.descriptor

    assert descriptor == ServiceDescriptor(
        id="athena",
        label="Athena",
        icon=descriptor.icon,
    )
    assert descriptor.icon


@pytest.mark.asyncio
async def test_service_owns_selections_but_builds_disposable_page_dependencies() -> None:
    clients: list[PageClient] = []
    policies: list[ReadOnlySqlPolicy] = []

    def client_factory(connection: Connection) -> PageClient:
        client = PageClient(
            connection_name=connection.name,
            region=connection.region,
        )
        clients.append(client)
        return client

    def policy_factory() -> ReadOnlySqlPolicy:
        policy = ReadOnlySqlPolicy()
        policies.append(policy)
        return policy

    service = _service(
        athena_client_factory=client_factory,
        sql_policy_factory=policy_factory,
    )
    connection = _connection("analytics", region="us-west-2")

    first = service.build_vm(connection)
    first.construct()
    await first.setup()
    await first.select_view("saved")
    await first.shutdown()
    first.dispose()

    replacement = service.build_vm(connection)
    replacement.construct()
    await replacement.setup()

    assert replacement.active_view == "saved"
    assert replacement.client is clients[1]
    assert len(clients) == 2
    assert len(policies) == 2
    assert policies[0] is not policies[1]

    await replacement.shutdown()
    replacement.dispose()
