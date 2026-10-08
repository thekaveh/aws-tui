"""Visible Athena database/table selection must generate, but never execute, SQL."""

from __future__ import annotations

import contextlib
from pathlib import Path

import pytest
from textual.widgets import OptionList, TextArea

from aws_tui.app import AwsTuiApp
from aws_tui.composition import build_app_context
from aws_tui.domain.data_catalog import TableRef
from aws_tui.ui.widgets.context_picker import ContextPicker
from aws_tui.ui.widgets.toast import Toast
from aws_tui.vm.athena.page_vm import AthenaPageVM
from tests.helpers import wait_until
from tests.integration.test_glue_athena_navigation import (
    _athena_client,
    _open_service,
    _wait_for_service_setup,
)


async def _choose(pilot, picker: ContextPicker, value: str, method: str) -> None:
    assert await pilot.click(picker)
    await wait_until(
        lambda: picker.is_open and picker.query_one(OptionList).has_focus,
        what="Athena options to receive focus",
    )
    await pilot.pause()
    options = picker.query_one(OptionList)
    index = options.get_option_index(value)
    if method == "mouse":
        offset = next(
            (
                (x, y)
                for y in range(options.region.height)
                for x in range(options.region.width)
                if options.get_style_at(x, y).meta.get("option") == index
            ),
            None,
        )
        assert offset is not None, f"target option {value!r} must be visible"
        assert await pilot.click(options, offset=offset)
    else:
        await pilot.press("home", *("down" for _ in range(index)), "enter")
    await wait_until(
        lambda: picker.value == value and not picker.is_open, what="chosen option committed"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("size", [(80, 24), (120, 40)])
@pytest.mark.parametrize("method", ["mouse", "keyboard"])
async def test_athena_selects_database_then_table_and_primes_visible_query(
    tmp_path: Path, size: tuple[int, int], method: str
) -> None:
    ctx = build_app_context(config_dir=tmp_path / "config", cache_dir=tmp_path / "cache", demo=True)
    client = _athena_client(ctx, "demo-dev")
    database = 'other"database'
    table = 'orders "quoted" [v2]'
    client.add_database("dev-analytics", "AwsDataCatalog", database)
    client.add_table("dev-analytics", "AwsDataCatalog", database, "a_default_table")
    client.add_table("dev-analytics", "AwsDataCatalog", database, table)
    app = AwsTuiApp(ctx)
    try:
        async with app.run_test(size=size) as pilot:
            vm = await _open_service(ctx, app, pilot, "athena")
            assert isinstance(vm, AthenaPageVM)
            table_picker = app.query_one("#athena-table", ContextPicker)
            assert vm.query.sql == ""
            assert table_picker.value is None
            await wait_until(
                lambda: (
                    not any(
                        "Demo mode active" in toast.toast_vm.model.text
                        for toast in app.query(Toast)
                    )
                ),
                what="demo warning expiry",
                timeout=10,
            )
            await pilot.pause()
            await _choose(
                pilot, app.query_one("#athena-catalog", ContextPicker), "AwsDataCatalog", method
            )
            await _wait_for_service_setup(ctx, app, pilot)
            assert vm.context.catalog == "AwsDataCatalog"
            await _choose(pilot, app.query_one("#athena-database", ContextPicker), database, method)
            await _wait_for_service_setup(ctx, app, pilot)
            assert vm.context.database == database
            assert table_picker.value is None
            assert vm.query.sql == ""
            await wait_until(lambda: not table_picker.disabled, what="selected database tables")
            assert tuple(row.ref.table_name for row in vm.tables.items) == (
                "a_default_table",
                table,
            )
            await _choose(pilot, table_picker, table, method)
            await _wait_for_service_setup(ctx, app, pilot)
            ref = TableRef("AwsDataCatalog", database, table, "demo-dev", "us-east-1")
            sql = 'SELECT * FROM "other""database"."orders ""quoted"" [v2]" LIMIT 5'
            assert vm.selected_table_ref == ref
            assert table_picker.value == table
            assert vm.query.sql == sql
            assert app.query_one("#athena-editor", TextArea).text == sql
            assert vm.query.execution_ref is None
            assert not any(call.method == "start_query" for call in client.calls)
            assert app.query_one("#athena-editor", TextArea).region.height >= 3
    finally:
        with contextlib.suppress(Exception):
            await ctx.root_vm.content_host.shutdown()
        ctx.root_vm.dispose()
        ctx.log_sink.close()
