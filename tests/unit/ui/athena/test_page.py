from __future__ import annotations

import pytest
from textual.app import App, ComposeResult
from textual.widgets import Button, DataTable, OptionList, Select, Static, TextArea

from aws_tui.domain.query import ResultColumn, ResultPage
from aws_tui.ui.widgets.athena.history_view import AthenaHistoryView
from aws_tui.ui.widgets.athena.page import AthenaPage
from aws_tui.ui.widgets.athena.query_view import AthenaQueryView
from aws_tui.ui.widgets.athena.results_view import AthenaResultsView
from aws_tui.ui.widgets.athena.saved_view import AthenaSavedView
from aws_tui.vm.athena.page_vm import AthenaPageVM
from tests.unit.vm.athena.test_page_vm import PageClient, make_page_vm


class _AthenaApp(App[None]):
    def __init__(self, vm: AthenaPageVM) -> None:
        super().__init__()
        self._vm = vm

    def compose(self) -> ComposeResult:
        yield AthenaPage(self._vm, hub=self._vm._hub)  # type: ignore[attr-defined]


def _build_vm(client: PageClient | None = None) -> tuple[AthenaPageVM, PageClient]:
    fake = client or PageClient()
    return make_page_vm(fake), fake


@pytest.mark.asyncio
async def test_page_composes_context_tabs_and_all_operational_views() -> None:
    vm, _client = _build_vm()
    await vm.setup()
    app = _AthenaApp(vm)

    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        page = app.query_one(AthenaPage)

        assert page.query_one("#athena-source-header")
        assert page.query_one("#athena-workgroup", Select)
        assert page.query_one("#athena-catalog", Select)
        assert page.query_one("#athena-database", Select)
        assert page.query_one("#athena-view-tabs")
        assert page.query_one(AthenaQueryView).display
        assert not page.query_one(AthenaHistoryView).display
        assert not page.query_one(AthenaResultsView).display
        assert not page.query_one(AthenaSavedView).display
        assert page.query_one("#athena-editor", TextArea)
        assert page.query_one("#athena-execute", Button)
        assert page.query_one("#athena-cancel", Button)


@pytest.mark.asyncio
async def test_view_selection_is_lazy_and_results_mount_a_data_table() -> None:
    vm, client = _build_vm()
    await vm.setup()
    app = _AthenaApp(vm)

    async with app.run_test() as pilot:
        await pilot.pause()
        page = app.query_one(AthenaPage)

        assert client.history_calls == []
        assert client.named_calls == []
        await page.action_select_view("history")
        await pilot.pause()
        assert client.history_calls == [("primary", None)]
        assert page.query_one(AthenaHistoryView).display

        await page.action_select_view("results")
        await pilot.pause()
        assert page.query_one(AthenaResultsView).display
        assert page.query_one(AthenaResultsView).query_one(DataTable)

        await page.action_select_view("saved")
        await pilot.pause()
        assert client.named_calls == [("primary", None)]
        assert client.prepared_calls == [("primary", None)]


@pytest.mark.asyncio
async def test_editor_and_execute_button_drive_the_query_vm() -> None:
    vm, client = _build_vm()
    await vm.setup()
    app = _AthenaApp(vm)

    async with app.run_test() as pilot:
        editor = app.query_one("#athena-editor", TextArea)
        editor.text = "SELECT count(*) FROM events"
        await pilot.pause()

        assert vm.query.sql == "SELECT count(*) FROM events"
        await pilot.click("#athena-execute")
        await pilot.pause()

        assert client.start_calls
        assert client.start_calls[0][0] == "SELECT count(*) FROM events"
        assert vm.results.rows == (("1",),)


@pytest.mark.asyncio
async def test_results_preserve_null_empty_and_markup_like_values_literally() -> None:
    client = PageClient()

    async def results(
        execution_id: str,
        *,
        start_token: str | None = None,
    ) -> ResultPage:
        assert execution_id == "literal-results"
        assert start_token is None
        return ResultPage(
            (
                ResultColumn("aws[tag]", "varchar", "NULLABLE"),
                ResultColumn("empty", "varchar", "NULLABLE"),
                ResultColumn("nested", "array", "NULLABLE"),
            ),
            ((None, "", "array[1][/bold]"),),
            None,
        )

    client.get_results_page = results  # type: ignore[method-assign]
    vm, _client = _build_vm(client)
    await vm.setup()
    await vm.results.load("literal-results")
    await vm.select_view("results")
    app = _AthenaApp(vm)

    async with app.run_test() as pilot:
        await pilot.pause()
        svg = app.export_screenshot()

        assert "aws[tag]" in svg
        assert "array[1][/bold]" in svg
        assert "NULL" in svg


@pytest.mark.asyncio
async def test_saved_open_in_editor_copies_sql_without_executing() -> None:
    client = PageClient()
    client.workgroups.reverse()
    vm, _client = _build_vm(client)
    await vm.setup()
    await vm.select_view("saved")
    await vm.select_named_query("named-1")
    app = _AthenaApp(vm)

    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.click("#athena-open-editor")
        await pilot.pause()

        assert vm.active_view == "query"
        assert app.query_one(AthenaQueryView).display
        assert app.query_one("#athena-editor", TextArea).text == "SELECT count(*) FROM events"
        assert client.start_calls == []


@pytest.mark.asyncio
async def test_default_focus_and_tab_cycle_are_stable() -> None:
    vm, _client = _build_vm()
    await vm.setup()
    vm.query.set_sql("SELECT 1")
    app = _AthenaApp(vm)

    async with app.run_test() as pilot:
        await pilot.pause()
        editor = app.query_one("#athena-editor", TextArea)
        assert editor.has_focus

        await pilot.press("tab")
        await pilot.pause()
        assert app.query_one("#athena-execute", Button).has_focus


@pytest.mark.asyncio
async def test_context_and_aws_text_are_rendered_without_markup() -> None:
    client = PageClient()
    client.workgroups[0] = client.workgroups[0].__class__(
        "primary[prod]",
        "ENABLED",
        None,
        None,
    )
    client.catalogs["primary[prod]"] = client.catalogs.pop("primary")
    client.databases[("primary[prod]", "AwsDataCatalog")] = client.databases.pop(
        ("primary", "AwsDataCatalog")
    )
    vm, _client = _build_vm(client)
    await vm.setup()
    app = _AthenaApp(vm)

    async with app.run_test() as pilot:
        await pilot.pause()
        svg = app.export_screenshot()

        assert "primary[prod]" in svg
        for selector in (
            "#athena-history-pane-options",
            "#athena-named-pane-options",
            "#athena-prepared-pane-options",
        ):
            assert not app.query_one(selector, OptionList)._markup  # type: ignore[attr-defined]
        for selector in (
            "#athena-query-status",
            "#athena-query-detail-text",
            "#athena-tab-query",
            "#athena-tab-history",
            "#athena-tab-results",
            "#athena-tab-saved",
        ):
            assert not app.query_one(selector, Static)._render_markup  # type: ignore[attr-defined]
