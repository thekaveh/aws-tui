from __future__ import annotations

import asyncio
from dataclasses import replace
from typing import cast

import pytest
from vmx import NULL_DISPATCHER, MessageHub
from vmx.messages.protocols import Message

from aws_tui.domain.data_catalog import TableFormat
from aws_tui.infra.aws_session import AwsSession
from aws_tui.infra.connection_resolver import Connection
from aws_tui.services.glue import GlueService
from tests.unit.domain._fake_aws_client import FakeAwsClient, FakeAwsSession
from tests.unit.vm.glue._fake_glue import seeded_glue


@pytest.mark.asyncio
async def test_rebind_during_provider_metadata_query_stops_once_without_stale_publish() -> None:
    glue = seeded_glue()
    first = glue.tables["analytics"][0]
    second = glue.tables["analytics"][1]
    glue.table_details[first.ref] = replace(
        glue.table_details[first.ref],
        table_format=TableFormat.ICEBERG,
    )
    athena = FakeAwsClient()
    athena.list_work_groups.return_value = {
        "WorkGroups": [{"Name": "primary", "State": "ENABLED"}],
    }
    athena.start_query_execution.return_value = {"QueryExecutionId": "metadata-query-1"}
    polling = asyncio.Event()

    async def active_execution(**_kwargs: object) -> object:
        polling.set()
        await asyncio.Event().wait()
        raise AssertionError("active metadata execution must be cancelled")

    athena.get_query_execution.side_effect = active_execution
    athena.stop_query_execution.return_value = {}
    session = FakeAwsSession({"athena": athena})
    connection = Connection(
        name="dev",
        kind="aws",
        region="us-east-1",
        source="test",
        profile="dev",
    )
    hub: MessageHub[Message] = MessageHub()
    service = GlueService(
        hub=hub,
        dispatcher=NULL_DISPATCHER,
        aws_session=cast(AwsSession, session),
        glue_client_factory=lambda _connection: glue,
    )
    page = service.build_vm(connection)
    page.construct()
    await page.setup()
    load = asyncio.create_task(page.catalog.iceberg.select_view("snapshots"))
    await polling.wait()

    rebind = asyncio.create_task(
        page.catalog.iceberg.bind_table(
            second.ref,
            table_format=TableFormat.ICEBERG,
        )
    )
    await asyncio.sleep(0)
    if not hasattr(page, "shutdown"):
        load.cancel()
        await asyncio.gather(load, return_exceptions=True)
        await rebind
        pytest.fail("GluePageVM.shutdown is missing")
    shutdown = asyncio.create_task(page.shutdown())
    await asyncio.gather(rebind, shutdown)

    assert not await load
    athena.stop_query_execution.assert_awaited_once_with(
        QueryExecutionId="metadata-query-1",
    )
    athena.get_query_results.assert_not_awaited()
    assert page.catalog.iceberg.table_ref is None
    assert page.catalog.iceberg.snapshots == ()

    page.dispose()
    athena.stop_query_execution.assert_awaited_once()
