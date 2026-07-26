from __future__ import annotations

import pytest
from vmx import NULL_DISPATCHER, MessageHub
from vmx.messages.protocols import Message

from aws_tui.infra.aws_session import AwsSession
from aws_tui.infra.connection_resolver import Connection
from aws_tui.services.glue import GlueService
from aws_tui.vm.services_protocol import ServiceDescriptor
from tests.unit.vm.glue._fake_glue import seeded_glue


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


def test_glue_service_is_aws_only() -> None:
    hub: MessageHub[Message] = MessageHub()
    service = GlueService(
        hub=hub,
        dispatcher=NULL_DISPATCHER,
        aws_session=AwsSession(),
    )

    assert service.supports(_connection("dev"))
    assert not service.supports(_connection("minio", kind="s3-compatible"))


def test_glue_service_descriptor_matches_navigation_contract() -> None:
    assert GlueService.descriptor == ServiceDescriptor(id="glue", label="Glue", icon="🔗")


@pytest.mark.asyncio
async def test_glue_service_reuses_profile_scoped_selections_across_page_rebuilds() -> None:
    hub: MessageHub[Message] = MessageHub()
    fake = seeded_glue()
    service = GlueService(
        hub=hub,
        dispatcher=NULL_DISPATCHER,
        aws_session=AwsSession(),
        glue_client_factory=lambda _connection: fake,
    )
    connection = _connection("dev")

    first_page = service.build_vm(connection)
    first_page.construct()
    await first_page.setup()
    await first_page.select_view("jobs")
    first_page.dispose()

    replacement_page = service.build_vm(connection)
    replacement_page.construct()
    await replacement_page.setup()

    assert replacement_page.client is fake
    assert replacement_page.active_view == "jobs"
    replacement_page.dispose()
