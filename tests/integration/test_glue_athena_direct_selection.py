"""Exercise the reported Glue-to-Athena flow through actual visible controls."""

from __future__ import annotations

import asyncio
import contextlib
from dataclasses import replace
from pathlib import Path

import pytest
from textual.pilot import Pilot
from textual.widgets import OptionList, Static, TextArea

from aws_tui.app import AwsTuiApp
from aws_tui.composition import AppContext, build_app_context
from aws_tui.demo.in_memory_glue import InMemoryGlue
from aws_tui.domain.athena import AthenaWorkgroupSummary
from aws_tui.domain.data_catalog import TableFormat, TableRef
from aws_tui.services.glue.service import GlueService
from aws_tui.ui.widgets.context_picker import ContextPicker
from aws_tui.ui.widgets.hint_legend import HintLegend
from aws_tui.ui.widgets.toast import Toast
from aws_tui.vm.athena.page_vm import AthenaPageVM
from aws_tui.vm.glue.page_vm import GluePageVM
from aws_tui.vm.messages import OpenAthenaTableRequest
from tests.helpers import drain_workers, wait_until
from tests.integration.test_glue_athena_navigation import _athena_client, _open_service


async def _wait_for_startup_warning(app: AwsTuiApp, pilot: Pilot[None]) -> None:
    await wait_until(
        lambda: (
            not any("Demo mode active" in toast.toast_vm.model.text for toast in app.query(Toast))
        ),
        what="normal expiry of the demo startup warning before clicking controls",
        timeout=10,
    )
    await pilot.pause()


async def _choose_visible_option(
    pilot: Pilot[None], options: OptionList, value: str, mode: str
) -> None:
    index = options.get_option_index(value)
    if mode == "mouse":
        offset = options.content_region.offset - options.region.offset
        assert await pilot.click(options, offset=(offset.x, offset.y + index))
    else:
        assert await pilot.click(options)
        await pilot.press("home", *("down" for _ in range(index)), "enter")


def _seed_nondefault_glue_table(ctx: AppContext) -> TableRef:
    service = ctx.registry.get("glue")
    assert isinstance(service, GlueService)
    factory = service._client_factory
    assert factory is not None
    client = factory(ctx.connection_resolver.resolve("demo-dev"))
    assert isinstance(client, InMemoryGlue)
    template = next(
        detail
        for detail in client.table_details.values()
        if detail.table_format is TableFormat.ICEBERG
    )
    database = "review_glue_database"
    client.add_table(database, "review_first_table")
    summary = client.add_iceberg_table(
        database,
        "review_selected_iceberg",
        columns=template.columns,
        partition_columns=tuple(column.name for column in template.partition_keys),
        metadata_version=2,
    )
    # Reuse actual seeded demo metadata objects, so the diagnostic exercises
    # selection and projection rather than a nonexistent fixture location.
    detail = client.table_details[summary.ref]
    client.table_details[summary.ref] = replace(
        detail,
        storage=replace(detail.storage, location=template.storage.location),
        parameters=template.parameters,
    )
    athena = _athena_client(ctx, "demo-dev")
    athena.add_database("dev-analytics", "AwsDataCatalog", database)
    athena.add_table("dev-analytics", "AwsDataCatalog", database, summary.ref.table_name)
    return summary.ref


@pytest.mark.parametrize("size", [(80, 24), (120, 40)])
@pytest.mark.parametrize("mode", ["mouse", "keyboard"])
async def test_glue_ui_nondefault_database_and_table_bottom_athena_handoff(
    tmp_path: Path, size: tuple[int, int], mode: str
) -> None:
    ctx = build_app_context(config_dir=tmp_path / "config", cache_dir=tmp_path / "cache", demo=True)
    target = _seed_nondefault_glue_table(ctx)
    athena_client = _athena_client(ctx, "demo-dev")
    app = AwsTuiApp(ctx)
    try:
        async with app.run_test(size=size) as pilot:
            glue = await _open_service(ctx, app, pilot, "glue")
            assert isinstance(glue, GluePageVM)
            await _wait_for_startup_warning(app, pilot)
            assert glue.catalog.selected_database_name != target.database_name
            databases = app.query_one("#glue-databases-pane-options", OptionList)
            await _choose_visible_option(pilot, databases, target.database_name, mode)
            await wait_until(
                lambda: (
                    glue.catalog.selected_database_name == target.database_name
                    and app.query_one("#glue-tables-pane-options", OptionList).option_count == 2
                ),
                what="actual Glue database selection loaded the nondefault tables",
            )
            await drain_workers(app)
            await pilot.pause()
            tables = app.query_one("#glue-tables-pane-options", OptionList)
            assert glue.catalog.selected_table_name != target.table_name
            await _choose_visible_option(pilot, tables, target.table_name, mode)
            await wait_until(
                lambda: (
                    glue.catalog.selected_table_name == target.table_name
                    and glue.catalog.table_detail is not None
                    and glue.catalog.table_detail.summary.ref == target
                ),
                what="actual Glue table selection projected the nondefault Iceberg detail",
            )
            await drain_workers(app)
            await pilot.pause()
            assert glue.catalog.selected_database_name == target.database_name
            assert glue.catalog.selected_table_name == target.table_name
            assert glue.catalog.table_detail is not None
            assert glue.catalog.table_detail.table_format is TableFormat.ICEBERG
            assert databases.highlighted is not None
            assert databases.get_option_at_index(databases.highlighted).id == target.database_name
            assert tables.highlighted is not None
            assert tables.get_option_at_index(tables.highlighted).id == target.table_name

            def athena_label() -> Static | None:
                legend = app.query_one(HintLegend)
                for chip in legend.query(".hint-chip"):
                    if chip.action.action_id != "glue.query_in_athena" or not chip.action.enabled:
                        continue
                    label = chip.query_one(".hint-label", Static)
                    if label.region.width and app.get_widget_at(*label.region.offset)[0] is label:
                        return label
                return None

            await wait_until(lambda: athena_label() is not None, what="real bottom Athena label")
            label = athena_label()
            assert label is not None
            assert await pilot.click(label)
            await wait_until(
                lambda: (
                    isinstance(ctx.root_vm.content_host.current, AthenaPageVM)
                    and not app._table_navigation_tasks
                ),
                what="bottom Athena click finished the typed table handoff",
            )
            await drain_workers(app)
            await pilot.pause()
            athena = ctx.root_vm.content_host.current
            assert isinstance(athena, AthenaPageVM)
            sql = 'SELECT * FROM "review_glue_database"."review_selected_iceberg" LIMIT 5'
            assert athena.context.connection_name == target.connection_name
            assert athena.context.region == target.region
            assert athena.context.catalog == target.catalog_name
            assert athena.context.database == target.database_name
            assert app.query_one("#athena-database", ContextPicker).value == target.database_name
            assert athena.query.sql == sql
            assert app.query_one("#athena-editor", TextArea).text == sql
            assert athena.query.execution_ref is None
            assert not any(call.method == "start_query" for call in athena_client.calls)
    finally:
        with contextlib.suppress(Exception):
            await ctx.root_vm.content_host.shutdown()
        ctx.root_vm.dispose()
        ctx.log_sink.close()


async def test_glue_immediate_bottom_athena_uses_visible_table_during_metadata_drain(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ctx = build_app_context(config_dir=tmp_path / "config", cache_dir=tmp_path / "cache", demo=True)
    previous = _seed_nondefault_glue_table(ctx)
    target = replace(previous, table_name="review_first_table")
    athena_client = _athena_client(ctx, "demo-dev")
    athena_client.add_table(
        "dev-analytics", target.catalog_name, target.database_name, target.table_name
    )
    app = AwsTuiApp(ctx)
    entered = asyncio.Event()
    release = asyncio.Event()
    try:
        async with app.run_test(size=(120, 40)) as pilot:
            glue = await _open_service(ctx, app, pilot, "glue")
            assert isinstance(glue, GluePageVM)
            await _wait_for_startup_warning(app, pilot)
            databases = app.query_one("#glue-databases-pane-options", OptionList)
            await _choose_visible_option(pilot, databases, previous.database_name, "mouse")
            await wait_until(
                lambda: (
                    glue.catalog.selected_database_name == previous.database_name
                    and app.query_one("#glue-tables-pane-options", OptionList).option_count == 2
                ),
                what="visible database selection loaded both diagnostic tables",
            )
            await drain_workers(app)
            await pilot.pause()
            tables = app.query_one("#glue-tables-pane-options", OptionList)
            await _choose_visible_option(pilot, tables, previous.table_name, "mouse")
            await wait_until(
                lambda: (
                    glue.catalog.selected_table_name == previous.table_name
                    and glue.catalog.table_detail is not None
                    and glue.catalog.table_detail.summary.ref == previous
                ),
                what="previous Iceberg table loaded through the actual row click",
            )
            await drain_workers(app)
            await pilot.pause()
            assert glue.catalog.table_detail is not None
            assert glue.catalog.table_detail.table_format is TableFormat.ICEBERG
            original = glue.catalog.iceberg.cancel_metadata_loads_and_drain_silently

            async def delayed_metadata_drain() -> None:
                entered.set()
                await release.wait()
                await original()

            monkeypatch.setattr(
                glue.catalog.iceberg,
                "cancel_metadata_loads_and_drain_silently",
                delayed_metadata_drain,
            )
            requests: list[OpenAthenaTableRequest] = []
            subscription = glue.hub.messages.subscribe(
                on_next=lambda message: (
                    requests.append(message)
                    if isinstance(message, OpenAthenaTableRequest)
                    else None
                )
            )
            try:
                await _choose_visible_option(pilot, tables, target.table_name, "mouse")
                async with asyncio.timeout(2):
                    await entered.wait()
                assert tables.highlighted is not None
                assert tables.get_option_at_index(tables.highlighted).id == target.table_name
                assert glue.catalog.selected_table_name == previous.table_name

                def athena_label() -> Static | None:
                    legend = app.query_one(HintLegend)
                    for chip in legend.query(".hint-chip"):
                        if (
                            chip.action.action_id != "glue.query_in_athena"
                            or not chip.action.enabled
                        ):
                            continue
                        label = chip.query_one(".hint-label", Static)
                        if (
                            label.region.width
                            and app.get_widget_at(*label.region.offset)[0] is label
                        ):
                            return label
                    return None

                await wait_until(
                    lambda: athena_label() is not None, what="real bottom Athena label"
                )
                label = athena_label()
                assert label is not None
                assert await pilot.click(label)
                await wait_until(
                    lambda: bool(requests), what="actual typed handoff before drain release"
                )
                release.set()
                await wait_until(
                    lambda: (
                        isinstance(ctx.root_vm.content_host.current, AthenaPageVM)
                        and not app._table_navigation_tasks
                    ),
                    what="immediate bottom Athena handoff completed",
                )
                await drain_workers(app)
                await pilot.pause()
                athena = ctx.root_vm.content_host.current
                assert isinstance(athena, AthenaPageVM)
                sql = 'SELECT * FROM "review_glue_database"."review_first_table" LIMIT 5'
                assert athena.context.connection_name == target.connection_name
                assert athena.context.region == target.region
                assert athena.context.catalog == target.catalog_name
                assert athena.context.database == target.database_name
                assert athena.query.sql == sql, f"captured actual handoff: {requests!r}"
                assert app.query_one("#athena-editor", TextArea).text == sql
                assert requests[0].table_ref == target
                assert athena.query.execution_ref is None
                assert not any(call.method == "start_query" for call in athena_client.calls)
            finally:
                release.set()
                subscription.dispose()
    finally:
        release.set()
        with contextlib.suppress(Exception):
            await ctx.root_vm.content_host.shutdown()
        ctx.root_vm.dispose()
        ctx.log_sink.close()


@pytest.mark.parametrize("size", [(80, 24), (120, 40)])
async def test_athena_pending_nondefault_database_survives_slow_metadata_refresh(
    tmp_path: Path, size: tuple[int, int], monkeypatch: pytest.MonkeyPatch
) -> None:
    ctx = build_app_context(config_dir=tmp_path / "config", cache_dir=tmp_path / "cache", demo=True)
    client = _athena_client(ctx, "demo-dev")
    target = "review_pending_database"
    client.add_database("dev-analytics", "DevDataCatalog", target)
    app = AwsTuiApp(ctx)
    entered = asyncio.Event()
    release = asyncio.Event()
    metadata_task: asyncio.Task[None] | None = None
    try:
        async with app.run_test(size=size) as pilot:
            athena = await _open_service(ctx, app, pilot, "athena")
            assert isinstance(athena, AthenaPageVM)
            await _wait_for_startup_warning(app, pilot)
            assert athena.context.connection_name == "demo-dev"
            assert athena.context.region == "us-east-1"
            assert athena.context.workgroup == "dev-analytics"
            assert athena.context.catalog == "DevDataCatalog"
            assert athena.context.database != target
            assert target in {row.ref.database_name for row in athena.databases}
            editor = app.query_one("#athena-editor", TextArea)
            assert await pilot.click(editor)
            await pilot.press(*"SELECT 7")
            assert athena.query.sql == "SELECT 7"
            original = client.list_workgroups_page

            async def delayed_workgroups(
                *, start_token: str | None = None
            ) -> tuple[list[AthenaWorkgroupSummary], str | None]:
                entered.set()
                await release.wait()
                return await original(start_token=start_token)

            monkeypatch.setattr(client, "list_workgroups_page", delayed_workgroups)
            notifications: list[str] = []
            subscription = athena.on_property_changed.subscribe(notifications.append)
            try:
                picker = app.query_one("#athena-database", ContextPicker)
                assert await pilot.click(picker)
                await wait_until(
                    lambda: picker.is_open and app.focused is picker.query_one(OptionList),
                    what="Athena database options opened before asynchronous metadata refresh",
                )
                metadata_task = asyncio.create_task(athena.refresh_workgroups())
                async with asyncio.timeout(2):
                    await entered.wait()
                await pilot.pause()
                options = picker.query_one(OptionList)
                index = options.get_option_index(target)
                await pilot.press("home", *("down" for _ in range(index)))
                assert options.highlighted == index
                assert options.get_option_at_index(index).id == target
                assert picker.is_open
                before = len(notifications)
                release.set()
                await metadata_task
                await pilot.pause()
                assert len(notifications) - before >= 2
                assert picker.is_open
                # An unrelated metadata projection must preserve the pending
                # database choice until the user's explicit Enter commit.
                assert options.highlighted == index
                await pilot.press("enter")
                await drain_workers(app)
                await pilot.pause()
                assert athena.context.database == target
                assert picker.value == target
                assert not picker.is_open
                assert athena.query.sql == "SELECT 7"
                assert editor.text == "SELECT 7"
                assert athena.query.execution_ref is None
                assert not any(call.method == "start_query" for call in client.calls)
            finally:
                subscription.dispose()
    finally:
        release.set()
        if metadata_task is not None:
            with contextlib.suppress(Exception):
                await metadata_task
        with contextlib.suppress(Exception):
            await ctx.root_vm.content_host.shutdown()
        ctx.root_vm.dispose()
        ctx.log_sink.close()
