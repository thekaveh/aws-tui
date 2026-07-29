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
from aws_tui.vm.glue.page_vm import GluePageVM
from tests.unit.domain._fake_aws_client import FakeAwsClient, FakeAwsSession
from tests.unit.vm.glue._fake_glue import InMemoryGlue, seeded_glue


async def _active_metadata_page(
    query_id: str,
) -> tuple[GluePageVM, InMemoryGlue, FakeAwsClient, asyncio.Task[bool]]:
    glue = seeded_glue()
    first = glue.tables["analytics"][0]
    glue.table_details[first.ref] = replace(
        glue.table_details[first.ref],
        table_format=TableFormat.ICEBERG,
    )
    athena = FakeAwsClient()
    athena.list_work_groups.return_value = {
        "WorkGroups": [{"Name": "primary", "State": "ENABLED"}],
    }
    athena.start_query_execution.return_value = {"QueryExecutionId": query_id}
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
    return page, glue, athena, load


@pytest.mark.asyncio
async def test_rebind_during_provider_metadata_query_stops_once_without_stale_publish() -> None:
    page, glue, athena, load = await _active_metadata_page("metadata-query-1")
    second = glue.tables["analytics"][1]

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


@pytest.mark.asyncio
@pytest.mark.parametrize("termination_order", ["dispose-first", "concurrent"])
async def test_terminal_race_durably_drains_provider_metadata_query(
    termination_order: str,
) -> None:
    query_id = f"metadata-{termination_order}"
    page, _glue, athena, load = await _active_metadata_page(query_id)
    iceberg = page.catalog.iceberg
    runner = iceberg._inspector._runner  # type: ignore[attr-defined]

    if termination_order == "dispose-first":
        page.dispose()
        await page.shutdown()
    else:
        shutdown = asyncio.create_task(page.shutdown())
        page.dispose()
        await shutdown
    drained_by_shutdown = (
        load.done()
        and not iceberg._metadata_tasks  # type: ignore[attr-defined]
        and not runner._cleanup_operations  # type: ignore[attr-defined]
    )
    if not load.done():
        await load

    await page.shutdown()
    page.dispose()

    assert drained_by_shutdown
    assert not await load
    athena.stop_query_execution.assert_awaited_once_with(QueryExecutionId=query_id)
    athena.get_query_results.assert_not_awaited()
    assert iceberg.table_ref is None
    assert iceberg.snapshots == ()
    assert not iceberg._metadata_tasks  # type: ignore[attr-defined]
    assert not runner._cleanup_operations  # type: ignore[attr-defined]
