from __future__ import annotations

from dataclasses import replace

import pytest
from vmx import NULL_DISPATCHER, MessageHub
from vmx.messages.protocols import Message

from aws_tui.domain.athena import AthenaWorkgroupSummary
from aws_tui.domain.data_catalog import TableFormat
from aws_tui.domain.filesystem import PermissionDeniedError
from aws_tui.infra.aws_session import AwsSession
from aws_tui.infra.connection_resolver import Connection
from aws_tui.services.glue import GlueService
from aws_tui.vm.file_manager.pane_vm import PaneState
from aws_tui.vm.service_source_vm import SelectionScope, ServiceSelectionStore
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


class _IcebergAthena:
    def __init__(self, workgroups: tuple[str, ...]) -> None:
        self.workgroups = workgroups
        self.workgroup_calls = 0
        self.start_calls = 0

    async def list_workgroups_page(
        self,
        *,
        start_token: str | None = None,
    ) -> tuple[list[AthenaWorkgroupSummary], str | None]:
        self.workgroup_calls += 1
        return [
            AthenaWorkgroupSummary(name, "ENABLED", None, None) for name in self.workgroups
        ], None

    async def start_query(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        self.start_calls += 1
        raise PermissionDeniedError("athena metadata denied")

    async def get_query_execution(self, execution_id: str):  # type: ignore[no-untyped-def]
        raise AssertionError(execution_id)

    async def stop_query(self, execution_id: str) -> None:
        raise AssertionError(execution_id)

    async def get_results_page(
        self,
        execution_id: str,
        *,
        start_token: str | None = None,
    ):
        raise AssertionError((execution_id, start_token))


def _iceberg_glue():
    fake = seeded_glue()
    table = fake.tables["analytics"][0]
    fake.table_details[table.ref] = replace(
        fake.table_details[table.ref],
        table_format=TableFormat.ICEBERG,
    )
    return fake


@pytest.mark.asyncio
async def test_glue_service_prefers_profile_scoped_selected_athena_workgroup() -> None:
    hub: MessageHub[Message] = MessageHub()
    selections = ServiceSelectionStore()
    connection = _connection("dev")
    selections.set(
        SelectionScope("athena", connection.name, connection.region),
        "workgroup",
        "selected",
    )
    athena = _IcebergAthena(("first", "selected"))
    service = GlueService(
        hub=hub,
        dispatcher=NULL_DISPATCHER,
        aws_session=AwsSession(),
        glue_client_factory=lambda _connection: _iceberg_glue(),
        athena_client_factory=lambda _connection: athena,
        selection_store=selections,
    )

    page = service.build_vm(connection)
    page.construct()
    await page.setup()
    await page.catalog.iceberg.select_view("snapshots")

    assert athena.workgroup_calls == 0
    assert athena.start_calls == 1
    assert page.catalog.iceberg.state is PaneState.FORBIDDEN
    assert page.catalog.table_detail is not None
    page.dispose()


@pytest.mark.asyncio
async def test_glue_service_uses_first_resolved_workgroup_and_scopes_unavailability() -> None:
    hub: MessageHub[Message] = MessageHub()
    athena = _IcebergAthena(())
    glue = _iceberg_glue()
    service = GlueService(
        hub=hub,
        dispatcher=NULL_DISPATCHER,
        aws_session=AwsSession(),
        glue_client_factory=lambda _connection: glue,
        athena_client_factory=lambda _connection: athena,
    )

    page = service.build_vm(_connection("dev"))
    page.construct()
    await page.setup()
    await page.catalog.iceberg.select_view("snapshots")

    assert athena.workgroup_calls == 1
    assert athena.start_calls == 0
    assert page.catalog.iceberg.state is PaneState.ERROR
    assert page.catalog.table_detail is glue.table_details[page.catalog.table_detail.summary.ref]
    page.dispose()


@pytest.mark.asyncio
async def test_glue_service_persists_first_resolved_workgroup_for_athena() -> None:
    hub: MessageHub[Message] = MessageHub()
    selections = ServiceSelectionStore()
    connection = _connection("dev")
    athena = _IcebergAthena(("first", "second"))
    service = GlueService(
        hub=hub,
        dispatcher=NULL_DISPATCHER,
        aws_session=AwsSession(),
        glue_client_factory=lambda _connection: _iceberg_glue(),
        athena_client_factory=lambda _connection: athena,
        selection_store=selections,
    )

    page = service.build_vm(connection)
    page.construct()
    await page.setup()
    await page.catalog.iceberg.select_view("snapshots")

    assert (
        selections.get(
            SelectionScope("athena", connection.name, connection.region),
            "workgroup",
        )
        == "first"
    )
    page.dispose()
