"""Mounted table paging survives restoration and uses the shared focus action."""

from __future__ import annotations

import contextlib
from contextlib import asynccontextmanager

import pytest
from textual.widget import Widget
from textual.widgets import TextArea

from aws_tui.app import AwsTuiApp
from aws_tui.composition import build_app_context
from aws_tui.domain.data_catalog import TableRef, TableSummary
from aws_tui.services.athena import AthenaService
from aws_tui.ui.widgets.athena.page import AthenaPage
from aws_tui.ui.widgets.context_picker import ContextPicker
from aws_tui.vm.athena.page_vm import AthenaPageVM
from tests.helpers import drain_workers, focus_and_settle, wait_until
from tests.integration.test_glue_page import open_service
from tests.unit.vm.athena.test_page_vm import PageClient


class PagingTablesClient(PageClient):
    def __init__(self):
        super().__init__(connection_name="demo-dev", region="us-east-1")
        self.table_calls = []

    async def list_tables_page(self, catalog, database, *, workgroup=None, start_token=None):
        self.table_calls.append((workgroup, catalog, database, start_token))
        assert start_token in {None, "tables-next"}
        name = "first_table" if start_token is None else 'next "table" [literal]'
        row = TableSummary(
            TableRef(catalog, database, name, self.connection_name, self.region),
            None,
            None,
            None,
            None,
            None,
        )
        return [row], "tables-next" if start_token is None else None


@asynccontextmanager
async def mounted_tables(tmp_path):
    ctx = build_app_context(config_dir=tmp_path / "config", cache_dir=tmp_path / "cache", demo=True)
    service = ctx.registry.get("athena")
    assert isinstance(service, AthenaService)
    client = PagingTablesClient()
    service._client_factory = lambda _connection: client
    app = AwsTuiApp(ctx)
    try:
        async with app.run_test(size=(120, 40)) as pilot:
            await open_service(ctx, pilot, "athena")
            vm = ctx.root_vm.content_host.current
            assert isinstance(vm, AthenaPageVM)
            page = app.query_one("#content-athena-page", AthenaPage)
            await wait_until(lambda: bool(vm.tables.items), what="first table page loaded")
            await pilot.pause()
            await drain_workers(app)
            yield app, page, vm, client, pilot
    finally:
        with contextlib.suppress(Exception):
            await ctx.root_vm.content_host.shutdown()
        ctx.root_vm.dispose()
        ctx.log_sink.close()


@pytest.mark.asyncio
async def test_same_context_snapshot_restore_reloads_visible_tables_without_changing_sql(tmp_path):
    async with mounted_tables(tmp_path) as (app, _page, vm, client, pilot):
        await vm.select_table("first_table")
        await pilot.pause()
        snapshot = vm.export_snapshot()
        previous_calls = len(client.table_calls)
        previous_sql = vm.query.sql
        previous_ref = vm.selected_table_ref

        await vm.restore_snapshot(snapshot)
        await pilot.pause()
        await drain_workers(app)
        await pilot.pause()

        assert vm.active_view == "query"
        assert vm.context == snapshot.context
        assert len(client.table_calls) == previous_calls + 1
        assert tuple(row.ref.table_name for row in vm.tables.items) == ("first_table",)
        picker = app.query_one("#athena-table", ContextPicker)
        assert not picker.disabled
        assert picker.value == "first_table"
        assert vm.selected_table_ref == previous_ref
        assert vm.query.sql == previous_sql
        assert app.query_one("#athena-editor", TextArea).text == previous_sql
        assert vm.query.execution_ref is None
        assert client.start_calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("target_id", ["athena-table", "athena-more-tables"])
async def test_shared_load_more_routes_table_focus_to_real_token_paging(tmp_path, target_id):
    async with mounted_tables(tmp_path) as (app, page, vm, client, pilot):
        vm.query.set_sql("SELECT 42")
        await pilot.pause()
        target = app.query_one(f"#{target_id}", Widget)
        await focus_and_settle(target)
        assert target.has_focus
        assert vm.tables.has_more
        assert not vm.has_more_databases
        assert not vm.has_more_catalogs
        assert not vm.has_more_workgroups

        assert page.can_load_more()
        assert "athena.load_more" not in app._readiness_disabled()
        await app.action_load_more_athena()
        await pilot.pause()
        await drain_workers(app)

        assert tuple(row.ref.table_name for row in vm.tables.items) == (
            "first_table",
            'next "table" [literal]',
        )
        assert [call[-1] for call in client.table_calls] == [None, "tables-next"]
        assert not vm.tables.has_more
        assert vm.query.sql == "SELECT 42"
        assert vm.selected_table_ref is None
        assert app.query_one("#athena-editor", TextArea).text == "SELECT 42"
        assert vm.query.execution_ref is None
        assert client.start_calls == []


@pytest.mark.asyncio
async def test_primed_table_beyond_first_metadata_page_stays_selected_without_loading_more(
    tmp_path,
):
    async with mounted_tables(tmp_path) as (app, _page, vm, client, pilot):
        name = 'next "table" [literal]'
        ref = TableRef(
            vm.context.catalog,
            vm.context.database,
            name,
            vm.context.connection_name,
            vm.context.region,
        )
        await vm.open_table(ref)
        await pilot.pause()
        await drain_workers(app)
        await pilot.pause()

        sql = 'SELECT * FROM "default"."next ""table"" [literal]" LIMIT 5'
        assert vm.selected_table_ref == ref
        assert vm.query.sql == sql
        assert tuple(row.ref.table_name for row in vm.tables.items) == ("first_table",)
        assert [call[-1] for call in client.table_calls] == [None]
        assert app.query_one("#athena-table", ContextPicker).value == name
        assert app.query_one("#athena-editor", TextArea).text == sql
        assert vm.query.execution_ref is None
        assert client.start_calls == []
