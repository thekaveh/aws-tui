"""Explicit table choices are scoped and never submit SQL."""

from __future__ import annotations

import pytest

from aws_tui.domain.data_catalog import TableRef, TableSummary
from aws_tui.domain.filesystem import ProviderError
from aws_tui.vm.file_manager.pane_vm import PaneState
from tests.unit.vm.athena.test_page_vm import PageClient, make_page_vm


class TablesPageClient(PageClient):
    def __init__(self) -> None:
        super().__init__()
        self.table_error = None
        self.table_calls = []

    async def list_tables_page(self, catalog, database, *, workgroup=None, start_token=None):
        self.table_calls.append((workgroup, catalog, database, start_token))
        if self.table_error:
            raise self.table_error
        return [
            TableSummary(
                TableRef(catalog, database, 'orders "quoted"', self.connection_name, self.region),
                None,
                None,
                None,
                None,
                None,
            )
        ], None


@pytest.mark.asyncio
async def test_query_refresh_retries_failed_tables_without_replacing_sql() -> None:
    client = TablesPageClient()
    page = make_page_vm(client)
    try:
        await page.setup()
        page.query.set_sql("SELECT 42")
        client.table_error = ProviderError("temporary")
        await page.tables.refresh()
        assert page.tables.state is PaneState.ERROR
        client.table_error = None
        await page.refresh_query_context()
        assert page.tables.state is PaneState.IDLE
        assert len(client.table_calls) == 2
        assert page.query.sql == "SELECT 42"
        assert page.selected_table_ref is None
        assert client.start_calls == []
    finally:
        await page.shutdown()
        page.dispose()


@pytest.mark.asyncio
async def test_explicit_table_selection_quotes_names_and_retires_old_database_choices() -> None:
    client = TablesPageClient()
    page = make_page_vm(client)
    try:
        await page.setup()
        await page.tables.refresh()
        await page.select_table("unknown")
        assert page.query.sql == ""
        await page.select_table('orders "quoted"')
        ref = TableRef("AwsDataCatalog", "default", 'orders "quoted"', "analytics", "us-west-2")
        assert page.selected_table_ref == ref
        assert page.query.sql == 'SELECT * FROM "default"."orders ""quoted""" LIMIT 5'
        await page.select_workgroup("analysts")
        assert page.selected_table_ref is None
        assert page.tables.items == ()
        assert page.tables.context == page.context == page.query.context
        page.query.set_sql("SELECT 42")
        await page.select_table('orders "quoted"')
        assert page.query.sql == "SELECT 42"
        assert client.start_calls == []
    finally:
        await page.shutdown()
        page.dispose()
