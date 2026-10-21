"""The real application's command palette and modal Enter route own Info."""

from dataclasses import replace

from textual.containers import Container
from textual.widgets import TextArea
from vmx import NULL_DISPATCHER

from aws_tui.app import AwsTuiApp
from aws_tui.domain.data_catalog import TableFormat
from aws_tui.infra.connection_resolver import Connection
from aws_tui.ui.widgets.glue.iceberg_info import IcebergInfoModal
from aws_tui.ui.widgets.glue.page import GluePage
from aws_tui.vm.glue.page_vm import GluePageVM
from tests.helpers import drain_workers, wait_until
from tests.unit.vm.glue._fake_glue import seeded_glue
from tests.unit.vm.glue.test_iceberg_vm import RecordingInspector


async def test_real_app_palette_info_and_modal_enter_pin_without_an_extra_query(
    app_context_factory,
):
    ctx = app_context_factory()
    fake = seeded_glue()
    ref = fake.tables["analytics"][0].ref
    fake.table_details[ref] = replace(fake.table_details[ref], table_format=TableFormat.ICEBERG)
    source = RecordingInspector()
    vm = GluePageVM(
        client=fake,
        iceberg_inspector=source,
        connection=Connection(
            name="dev", kind="aws", region="us-east-1", source="test", profile="dev"
        ),
        hub=ctx.hub,
        dispatcher=NULL_DISPATCHER,
    )
    vm.construct()
    await vm.setup()
    app = AwsTuiApp(ctx)
    try:
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            await ctx.root_vm.content_host.set_content(vm, service_id="glue", already_prepared=True)
            host = app.query_one("#content-host", Container)
            await host.remove_children()
            await host.mount(
                GluePage(
                    vm, hub=ctx.hub, focus_coordinator=ctx.focus_coordinator, id="content-glue-page"
                )
            )
            ctx.root_vm.chrome.hint_legend.set_current_service("glue")
            await pilot.pause()
            await pilot.click("#glue-iceberg-tab-snapshots")
            await drain_workers(app)
            await pilot.pause()
            await wait_until(
                lambda: vm.catalog.iceberg.selected_snapshot_id is not None,
                what="visible snapshot selected after metadata paint",
            )
            selected = vm.catalog.iceberg.selected_snapshot_id
            assert selected is not None
            calls = list(source.calls)
            await pilot.press("ctrl+k")
            await pilot.press(*"Explain Iceberg")
            await pilot.press("enter")
            await wait_until(
                lambda: isinstance(app.screen, IcebergInfoModal), what="palette opened Info"
            )
            assert "Historical snapshot summary" in app.screen.query_one(TextArea).text
            await pilot.press("down", "enter")
            assert vm.catalog.iceberg.comparison_snapshot_id == selected
            await pilot.press("escape")
            assert source.calls == calls
    finally:
        await vm.shutdown()
        vm.dispose()
