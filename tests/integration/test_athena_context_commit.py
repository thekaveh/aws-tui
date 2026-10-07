"""User-driven Athena selector changes, rather than recommitting defaults."""

from __future__ import annotations

import contextlib
from pathlib import Path

import pytest
from textual.widgets import OptionList, Static, TextArea

from aws_tui.app import AwsTuiApp
from aws_tui.composition import build_app_context
from aws_tui.domain.data_catalog import TableRef
from aws_tui.ui.widgets.context_picker import ContextPicker
from aws_tui.ui.widgets.hint_legend import HintLegend
from aws_tui.ui.widgets.service_source_header import ServiceSourceHeader
from aws_tui.ui.widgets.toast import Toast
from aws_tui.vm.athena.page_vm import AthenaPageVM
from aws_tui.vm.glue.page_vm import GluePageVM
from tests.helpers import wait_until
from tests.integration.test_glue_athena_navigation import (
    _activate_handoff,
    _athena_client,
    _open_service,
    _wait_for_service_setup,
)


@pytest.mark.asyncio
@pytest.mark.parametrize("size", [(80, 24), (120, 40)])
@pytest.mark.parametrize("route", ["bottom-bar", "palette-service"])
@pytest.mark.parametrize("field", ["workgroup", "catalog", "database", "source"])
async def test_glue_iceberg_handoff_can_commit_a_different_athena_context(
    tmp_path: Path, size: tuple[int, int], field: str, route: str
) -> None:
    ctx = build_app_context(config_dir=tmp_path / "config", cache_dir=tmp_path / "cache", demo=True)
    client = _athena_client(ctx, "demo-dev")
    client.add_workgroup("review-workgroup", output_location="s3://demo-results/review/")
    client.add_catalog("review-workgroup", "AwsDataCatalog")
    client.add_database("review-workgroup", "AwsDataCatalog", "review_database")
    client.add_catalog("dev-analytics", "review-catalog")
    client.add_database("dev-analytics", "review-catalog", "review_database")
    client.add_database("dev-analytics", "AwsDataCatalog", "review_database")
    app = AwsTuiApp(ctx)
    try:
        async with app.run_test(size=size) as pilot:
            glue = await _open_service(ctx, app, pilot, "glue")
            assert isinstance(glue, GluePageVM)
            ref = TableRef(
                "AwsDataCatalog", "dev_analytics", "dev_events_iceberg", "demo-dev", "us-east-1"
            )
            await glue.open_table(ref)
            assert glue.catalog.table_detail is not None
            assert glue.catalog.table_detail.summary.ref == ref
            if route == "bottom-bar":
                legend = app.query_one(HintLegend)

                def athena_label() -> Static | None:
                    for chip in legend.query(".hint-chip"):
                        if chip.action.action_id != "glue.query_in_athena":
                            continue
                        label = chip.query_one(".hint-label", Static)
                        if (
                            chip.action.enabled
                            and label.region.width
                            and (app.get_widget_at(*label.region.offset)[0] is label)
                        ):
                            return label
                    return None

                await wait_until(lambda: athena_label() is not None, what="bottom Athena label")
                label = athena_label()
                assert label is not None
                assert await pilot.click(label)
            else:
                await _activate_handoff(pilot, key=None, label="Go to Athena")
            await _wait_for_service_setup(ctx, app, pilot)
            vm = ctx.root_vm.content_host.current
            assert isinstance(vm, AthenaPageVM)
            sql = 'SELECT * FROM "dev_analytics"."dev_events_iceberg" LIMIT 5'
            assert vm.query.sql == sql
            assert app.query_one("#athena-editor", TextArea).text == sql
            assert vm.context.connection_name == "demo-dev"
            assert vm.context.region == "us-east-1"
            assert vm.context.catalog == ref.catalog_name
            assert vm.context.database == ref.database_name
            for name in ("workgroup", "catalog", "database"):
                picker = app.query_one(f"#athena-{name}", ContextPicker)
                assert picker.value == getattr(vm.context, name)
                assert (
                    str(picker.query_one(".context-picker-value", Static).render()) == picker.value
                )

            # The demo startup warning deliberately overlays the header for eight
            # seconds. Wait for its normal expiry, retaining click hit-test assertions.
            await wait_until(
                lambda: (
                    not any(
                        "Demo mode active" in toast.toast_vm.model.text
                        for toast in app.query(Toast)
                    )
                ),
                what="demo startup warning to expire before selector interaction",
                timeout=10,
            )
            await pilot.pause()
            target = {
                "workgroup": "review-workgroup",
                "catalog": "review-catalog",
                "database": "review_database",
                "source": "1",
            }[field]
            picker = app.query_one(
                "#athena-source-header-picker" if field == "source" else f"#athena-{field}",
                ContextPicker,
            )
            assert picker.value != target
            assert await pilot.click(picker)
            await wait_until(
                lambda: picker.is_open and isinstance(app.focused, OptionList),
                what="clicked Athena selector to open and focus its options",
            )
            options = picker.query_one(OptionList)
            index = options.get_option_index(target)
            if size[0] == 120:
                row_y = options.content_region.y - options.region.y + index
                assert await pilot.click(options, offset=(2, row_y))
            else:
                await pilot.press("home", *("down" for _ in range(index)))
                assert options.highlighted == index
                await pilot.press("enter")
            await _wait_for_service_setup(ctx, app, pilot)
            if field == "source":
                current = ctx.root_vm.content_host.current
                assert isinstance(current, AthenaPageVM)
                assert current is not vm
                assert current.context.connection_name == "demo-prod"
                assert current.context.region == "us-east-1"
                assert app.query_one(ServiceSourceHeader).tooltip == current.source.label
                assert current.query.sql == ""
                assert app.query_one("#athena-editor", TextArea).text == current.query.sql
                for name in ("workgroup", "catalog", "database"):
                    assert app.query_one(f"#athena-{name}", ContextPicker).value == getattr(
                        current.context, name
                    )
                assert current.query.execution_ref is None
                assert not any(
                    call.method == "start_query" for call in _athena_client(ctx, "demo-prod").calls
                )
                assert not any(call.method == "start_query" for call in client.calls)
                return
            assert getattr(vm.context, field) == target
            assert picker.value == target
            assert not picker.is_open
            assert vm.query.sql == sql
            assert app.query_one("#athena-editor", TextArea).text == sql
            assert vm.query.execution_ref is None
            assert not any(call.method == "start_query" for call in client.calls)
    finally:
        with contextlib.suppress(Exception):
            await ctx.root_vm.content_host.shutdown()
        ctx.root_vm.dispose()
        ctx.log_sink.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("origin", ["s3", "glue-jobs", "athena"])
async def test_palette_athena_without_an_active_glue_table_keeps_normal_navigation(
    tmp_path: Path, origin: str
) -> None:
    ctx = build_app_context(config_dir=tmp_path / "config", cache_dir=tmp_path / "cache", demo=True)
    app = AwsTuiApp(ctx)
    try:
        async with app.run_test(size=(120, 40)) as pilot:
            previous = await _open_service(
                ctx, app, pilot, "glue" if origin == "glue-jobs" else origin
            )
            if origin == "glue-jobs":
                assert isinstance(previous, GluePageVM)
                assert previous.catalog.selected_table_name is not None
                await previous.select_view("jobs")
                await pilot.pause()
                assert not previous.can_query_in_athena
            if origin == "athena":
                assert isinstance(previous, AthenaPageVM)
                previous.query.set_sql("SELECT 7")
                await pilot.pause()
            await _activate_handoff(pilot, key=None, label="Go to Athena")
            await _wait_for_service_setup(ctx, app, pilot)
            current = ctx.root_vm.content_host.current
            assert isinstance(current, AthenaPageVM)
            assert current.query.sql == ("SELECT 7" if origin == "athena" else "")
            assert app.query_one("#athena-editor", TextArea).text == current.query.sql
            if origin == "athena":
                assert current is previous
            assert current.query.execution_ref is None
            assert not any(
                call.method == "start_query" for call in _athena_client(ctx, "demo-dev").calls
            )
    finally:
        with contextlib.suppress(Exception):
            await ctx.root_vm.content_host.shutdown()
        ctx.root_vm.dispose()
        ctx.log_sink.close()
