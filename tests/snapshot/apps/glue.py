from __future__ import annotations

from dataclasses import replace
from typing import Literal

from textual.app import App, ComposeResult
from textual.containers import Container, Horizontal
from textual.pilot import Pilot
from textual.widgets import DataTable
from vmx import NULL_DISPATCHER, MessageHub
from vmx.messages.protocols import Message

from aws_tui.domain.data_catalog import TableFormat
from aws_tui.domain.filesystem import PermissionDeniedError
from aws_tui.infra.connection_resolver import Connection
from aws_tui.infra.keymap_store import KeymapStore
from aws_tui.infra.theme_store import ThemeStore
from aws_tui.ui.widgets.context_picker import ContextPicker
from aws_tui.ui.widgets.glue.page import GluePage
from aws_tui.ui.widgets.hint_legend import HintLegend
from aws_tui.ui.widgets.service_source_header import ServiceSourceHeader
from aws_tui.ui.widgets.service_tab_strip import ServiceTabStrip
from aws_tui.vm.chrome.hint_legend_vm import HintLegendVM
from aws_tui.vm.glue.iceberg_vm import IcebergView
from aws_tui.vm.glue.page_vm import GluePageVM, GlueView
from aws_tui.vm.service_source_vm import ServiceSourceContext
from tests.unit.vm.glue._fake_glue import InMemoryGlue
from tests.unit.vm.glue.test_iceberg_vm import RecordingInspector

GlueFixture = Literal["populated", "empty", "forbidden", "iceberg"]


class _ForbiddenGlue(InMemoryGlue):
    async def list_databases_page(
        self,
        *,
        start_token: str | None = None,
    ) -> tuple[list, str | None]:
        raise PermissionDeniedError("permission denied by Lake Formation")


def _client(fixture: GlueFixture) -> InMemoryGlue:
    if fixture == "forbidden":
        return _ForbiddenGlue(connection_name="analytics-prod", region="us-west-2")
    fake = InMemoryGlue(connection_name="analytics-prod", region="us-west-2")
    if fixture == "empty":
        return fake
    table = fake.add_table("analytics", "events")
    if fixture == "iceberg":
        fake.table_details[table.ref] = replace(
            fake.table_details[table.ref],
            table_format=TableFormat.ICEBERG,
        )
    fake.add_table("analytics", "sessions")
    fake.add_partition(table.ref, "dt=2026-07-25")
    fake.add_run("nightly", "jr-20260725", "RUNNING")
    fake.add_run("nightly", "jr-20260724", "SUCCEEDED")
    fake.add_crawler("ready-crawler", "READY")
    fake.add_crawler("running-crawler", "RUNNING")
    return fake


class GluePageApp(App[None]):
    def __init__(
        self,
        *,
        theme: str,
        view: GlueView = "catalog",
        fixture: GlueFixture = "populated",
        iceberg_view: IcebergView = "snapshots",
        open_picker: bool = False,
        focus_tabs: bool = False,
        show_legend: bool = False,
    ) -> None:
        super().__init__()
        self.CSS = ThemeStore().load(theme)
        if show_legend:
            self.CSS += "\n#content-host { width: 1fr; height: 1fr; }"
        self._hub: MessageHub[Message] = MessageHub()
        self._keymap = KeymapStore()
        self._vm = GluePageVM(
            client=_client(fixture),
            iceberg_inspector=RecordingInspector() if fixture == "iceberg" else None,
            connection=Connection(
                name="analytics-prod",
                kind="aws",
                region="us-west-2",
                source="test",
                profile="analytics-prod",
            ),
            hub=self._hub,
            dispatcher=NULL_DISPATCHER,
        )
        self._vm.construct()
        self._hint_vm = (
            HintLegendVM(
                hub=self._hub,
                dispatcher=NULL_DISPATCHER,
                keymap=self._keymap,
            )
            if show_legend
            else None
        )
        if self._hint_vm is not None:
            self._hint_vm.construct()
            self._hint_vm.set_current_service("glue")
        self._view = view
        self._iceberg_view = iceberg_view
        self._open_picker = open_picker
        self._focus_tabs = focus_tabs

    def compose(self) -> ComposeResult:
        yield Container(id="content-host")
        if self._hint_vm is not None:
            yield HintLegend(self._hint_vm, hub=self._hub, id="hint-legend")

    async def on_mount(self) -> None:
        await self._vm.setup()
        if self._view != "catalog":
            await self._vm.select_view(self._view)
        if self._vm.catalog.iceberg.available:
            await self._vm.catalog.iceberg.select_view(self._iceberg_view)
        await self.query_one("#content-host", Container).mount(
            GluePage(
                self._vm,
                hub=self._vm.hub,
                keymap=self._keymap,
                source_candidates=(
                    self._vm.source,
                    ServiceSourceContext("analytics-dev", "dev-sso", "us-east-1"),
                ),
                id="glue-page",
            )
        )
        if self._open_picker:
            self.query_one("#glue-run-state-filter").open()
        if self._focus_tabs:
            self.query_one("#glue-view-tabs", ServiceTabStrip).focus()

    async def focus_iceberg_table(self, pilot: Pilot) -> None:
        await pilot.pause()
        table = self.query_one("#glue-iceberg-table", DataTable)
        source = self.query_one("#glue-source-header", ServiceSourceHeader)
        table.focus()
        await pilot.pause()
        assert self.focused is table
        assert table.has_focus
        assert not source.has_focus_within

    async def open_run_state_picker_with_geometry_check(self, pilot: Pilot) -> None:
        await pilot.pause()
        row = self.query_one("#glue-context-row", Horizontal)
        source = self.query_one("#glue-source-header", ServiceSourceHeader)
        picker = self.query_one("#glue-run-state-filter", ContextPicker)
        tabs = self.query_one("#glue-view-tabs", ServiceTabStrip)
        view_host = self.query_one("#glue-view-host")
        widgets = (row, source, picker, tabs, view_host)
        closed_regions = tuple(widget.region for widget in widgets)

        picker.open()
        await pilot.pause()
        assert picker.is_open
        assert tuple(widget.region for widget in widgets) == closed_regions

        await pilot.press("escape")
        await pilot.pause()
        assert not picker.is_open
        assert tuple(widget.region for widget in widgets) == closed_regions

        picker.open()
        await pilot.pause()
        assert picker.is_open
        assert tuple(widget.region for widget in widgets) == closed_regions
        self._assert_one_row_legend()

    def _assert_one_row_legend(self) -> None:
        if self._hint_vm is None:
            return
        legend = self.query_one(HintLegend)
        chips = list(legend.query(".hint-chip"))
        assert legend.region.height == 3
        assert chips
        assert {chip.region.y for chip in chips} == {legend.content_region.y}
        assert all(chip.region.right <= legend.content_region.right for chip in chips)
        if self.size.width <= 80:
            assert {chip.action.action_id for chip in chips} >= {
                "app.command_palette",
                "app.quit",
            }


__all__ = ["GluePageApp"]
