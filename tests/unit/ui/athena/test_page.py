from __future__ import annotations

import pytest
from textual.app import App, ComposeResult
from textual.widgets import Button, DataTable, OptionList, Select, Static, TextArea
from vmx import NULL_DISPATCHER

from aws_tui.domain.query import ResultColumn, ResultPage
from aws_tui.ui.widgets.athena.history_view import AthenaHistoryView
from aws_tui.ui.widgets.athena.page import AthenaPage
from aws_tui.ui.widgets.athena.query_view import AthenaQueryView
from aws_tui.ui.widgets.athena.results_view import AthenaResultsView
from aws_tui.ui.widgets.athena.saved_view import AthenaSavedView
from aws_tui.ui.widgets.service_tab_strip import ServiceTabStrip
from aws_tui.vm.athena.page_vm import AthenaPageVM
from aws_tui.vm.chrome.focus_coordinator_vm import FocusCoordinatorVM, FocusSlot
from tests.unit.vm.athena.test_page_vm import PageClient, make_page_vm


class _AthenaApp(App[None]):
    def __init__(self, vm: AthenaPageVM) -> None:
        super().__init__()
        self._vm = vm
        self.focus_coordinator = FocusCoordinatorVM(
            hub=vm._hub,  # type: ignore[attr-defined]
            dispatcher=NULL_DISPATCHER,
        )
        self.focus_coordinator.construct()

    def compose(self) -> ComposeResult:
        yield AthenaPage(
            self._vm,
            hub=self._vm._hub,  # type: ignore[attr-defined]
            focus_coordinator=self.focus_coordinator,
        )

    def on_unmount(self) -> None:
        self.focus_coordinator.dispose()


def _athena_target_ids(page: AthenaPage) -> tuple[str, ...]:
    return tuple(widget.id or "" for _slot, widget in page._focus_targets())


def _cycle_athena_target_ids(
    app: _AthenaApp,
    page: AthenaPage,
    *,
    reverse: bool,
) -> tuple[str, ...]:
    expected_count = len(page._focus_targets())
    app.focus_coordinator.set_focused_slot(FocusSlot.NAV_MENU)
    visited: list[str] = []
    for _ in range(expected_count):
        page.cycle_focus(reverse=reverse)
        focused = app.focused
        assert focused is not None
        visited.append(focused.id or "")
    page.cycle_focus(reverse=reverse)
    assert app.focused is not None
    assert app.focused.id == visited[0]
    return tuple(visited)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("view", "surface_ids"),
    [
        ("query", ("athena-editor", "athena-query-detail")),
        (
            "history",
            ("athena-history-pane-options", "athena-history-results", ""),
        ),
        ("results", ("athena-results-table",)),
        (
            "saved",
            ("athena-named-pane-options", "athena-prepared-pane-options", ""),
        ),
    ],
)
async def test_athena_views_have_complete_deterministic_focus_rings(
    view: str,
    surface_ids: tuple[str, ...],
) -> None:
    vm, _client = _build_vm()
    await vm.setup()
    await vm.select_view(view)  # type: ignore[arg-type]
    app = _AthenaApp(vm)
    context_ids = (
        "athena-source-header",
        "athena-workgroup",
        "athena-catalog",
        "athena-database",
        "athena-view-tabs",
    )
    expected_ids = (*context_ids, *surface_ids)

    async with app.run_test() as pilot:
        await pilot.pause()
        page = app.query_one(AthenaPage)
        assert _athena_target_ids(page) == expected_ids
        assert _cycle_athena_target_ids(app, page, reverse=False) == expected_ids
        assert _cycle_athena_target_ids(app, page, reverse=True) == (
            expected_ids[0],
            *reversed(expected_ids[1:]),
        )


@pytest.mark.asyncio
async def test_athena_focus_ring_omits_hidden_views_and_disabled_load_more() -> None:
    vm, _client = _build_vm()
    await vm.setup()
    app = _AthenaApp(vm)

    async with app.run_test() as pilot:
        await pilot.pause()
        page = app.query_one(AthenaPage)
        target_ids = set(_athena_target_ids(page))

        assert not target_ids & {
            "athena-more-workgroups",
            "athena-more-catalogs",
            "athena-more-databases",
            "athena-history-pane-options",
            "athena-results-table",
            "athena-named-pane-options",
            "athena-prepared-pane-options",
        }
        assert page.query_one("#athena-view-tabs", ServiceTabStrip)


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
        assert page.query_one("#athena-more-workgroups", Button)
        assert page.query_one("#athena-more-catalogs", Button)
        assert page.query_one("#athena-more-databases", Button)
        assert page.query_one("#athena-view-tabs")
        assert page.query_one(AthenaQueryView).display
        assert not page.query_one(AthenaHistoryView).display
        assert not page.query_one(AthenaResultsView).display
        assert not page.query_one(AthenaSavedView).display
        assert page.query_one("#athena-editor", TextArea)
        assert page.query_one("#athena-execute", Button)
        assert page.query_one("#athena-cancel", Button)
        assert page.query_one("#athena-more-history", Button)
        assert page.query_one("#athena-more-results", Button)
        assert page.query_one("#athena-more-named", Button)
        assert page.query_one("#athena-more-prepared", Button)


@pytest.mark.asyncio
async def test_queued_page_refresh_is_safe_after_descendants_are_removed() -> None:
    vm, _client = _build_vm()
    await vm.setup()
    app = _AthenaApp(vm)

    async with app.run_test() as pilot:
        page = app.query_one(AthenaPage)
        await page.remove_children()
        await pilot.pause()

        page._refresh_page()  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_page_refresh_is_safe_during_partial_descendant_teardown() -> None:
    vm, _client = _build_vm()
    await vm.setup()
    app = _AthenaApp(vm)

    async with app.run_test() as pilot:
        await pilot.pause()
        page = app.query_one(AthenaPage)
        workgroup = page.query_one("#athena-workgroup", Select)
        await page.query_one("#athena-more-workgroups", Button).remove()

        assert page.is_mounted
        assert workgroup.is_mounted
        page._sync_context()  # type: ignore[attr-defined]
        page._refresh_page()  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_live_page_context_query_errors_are_not_masked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    vm, _client = _build_vm()
    await vm.setup()
    app = _AthenaApp(vm)

    async with app.run_test() as pilot:
        await pilot.pause()
        page = app.query_one(AthenaPage)
        original_query_one = page.query_one

        def fail_workgroup_query(
            selector: object,
            expect_type: object | None = None,
        ) -> object:
            if selector == "#athena-workgroup":
                raise RuntimeError("live context query failed")
            if expect_type is None:
                return original_query_one(selector)  # type: ignore[arg-type]
            return original_query_one(selector, expect_type)  # type: ignore[arg-type]

        monkeypatch.setattr(page, "query_one", fail_workgroup_query)

        with pytest.raises(RuntimeError, match="live context query failed"):
            page._sync_context()  # type: ignore[attr-defined]
        monkeypatch.undo()
        await pilot.pause()


@pytest.mark.asyncio
async def test_load_more_routes_by_focused_context_or_active_surface() -> None:
    vm, _client = _build_vm()
    await vm.setup()
    app = _AthenaApp(vm)
    calls: list[str] = []

    async def record(name: str) -> None:
        calls.append(name)

    vm.load_more_workgroups = lambda: record("workgroups")  # type: ignore[method-assign]
    vm.load_more_catalogs = lambda: record("catalogs")  # type: ignore[method-assign]
    vm.load_more_databases = lambda: record("databases")  # type: ignore[method-assign]
    vm.history.load_more = lambda: record("history")  # type: ignore[method-assign]
    vm.results.load_more = lambda: record("results")  # type: ignore[method-assign]
    vm.saved.load_more_named_queries = lambda: record("named")  # type: ignore[method-assign]
    vm.saved.load_more_prepared_statements = lambda: record("prepared")  # type: ignore[method-assign]
    vm._workgroup_pager._current_token = "workgroups-next"  # type: ignore[attr-defined]
    vm._catalog_pager._current_token = "catalogs-next"  # type: ignore[attr-defined]
    vm._database_pager._current_token = "databases-next"  # type: ignore[attr-defined]
    vm.history._pager._current_token = "history-next"  # type: ignore[attr-defined]
    vm.results._execution_id = "q-results"  # type: ignore[attr-defined]
    vm.results._pager._current_token = "results-next"  # type: ignore[attr-defined]
    vm.saved._named_pager._current_token = "named-next"  # type: ignore[attr-defined]
    vm.saved._prepared_pager._current_token = "prepared-next"  # type: ignore[attr-defined]

    async with app.run_test() as pilot:
        await pilot.pause()
        page = app.query_one(AthenaPage)
        page._refresh_page()  # type: ignore[attr-defined]
        await pilot.pause()
        routes = (
            ("#athena-more-workgroups", "workgroups"),
            ("#athena-more-catalogs", "catalogs"),
            ("#athena-more-databases", "databases"),
            ("#athena-history-pane-options", "history"),
            ("#athena-results-table", "results"),
            ("#athena-named-pane-options", "named"),
            ("#athena-prepared-pane-options", "prepared"),
        )
        for selector, expected in routes:
            if expected in {"history", "results", "named", "prepared"}:
                view = {
                    "history": "history",
                    "results": "results",
                    "named": "saved",
                    "prepared": "saved",
                }[expected]
                if page.vm.active_view != view:
                    await page.action_select_view(view)
            target = app.query_one(selector)
            target.focus()
            await pilot.pause(0.05)
            assert target.has_focus
            await page.action_load_more()
            assert calls.pop() == expected

        assert calls == []


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
async def test_results_allow_duplicate_aws_column_aliases_without_losing_cells() -> None:
    client = PageClient()

    async def results(
        execution_id: str,
        *,
        start_token: str | None = None,
    ) -> ResultPage:
        assert execution_id == "duplicate-aliases"
        assert start_token is None
        return ResultPage(
            (
                ResultColumn("total", "bigint", "NULLABLE"),
                ResultColumn("total", "varchar", "NULLABLE"),
            ),
            (("7", "seven"),),
            None,
        )

    client.get_results_page = results  # type: ignore[method-assign]
    vm, _client = _build_vm(client)
    await vm.setup()
    await vm.results.load("duplicate-aliases")
    await vm.select_view("results")
    app = _AthenaApp(vm)

    async with app.run_test() as pilot:
        await pilot.pause()
        table = app.query_one("#athena-results-table", DataTable)
        svg = app.export_screenshot()

        assert tuple(key.value for key in table.columns) == (
            "athena-result-column-0",
            "athena-result-column-1",
        )
        assert [str(cell) for cell in table.get_row_at(0)] == ["7", "seven"]
        assert svg.count("total") >= 2


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
        page = app.query_one(AthenaPage)
        page._maybe_focus_active()  # type: ignore[attr-defined]
        await pilot.pause()
        editor = app.query_one("#athena-editor", TextArea)
        assert editor.has_focus

        await pilot.press("tab")
        await pilot.pause()
        assert app.query_one("#athena-execute", Button).has_focus


@pytest.mark.asyncio
async def test_query_view_shows_enforced_managed_workgroup_output_before_execution() -> None:
    client = PageClient()
    vm, _client = _build_vm(client)
    await vm.setup()
    await vm.select_workgroup("analysts")
    app = _AthenaApp(vm)

    async with app.run_test() as pilot:
        await pilot.pause()
        detail = str(app.query_one("#athena-query-detail-text", Static).render())

        assert "Workgroup mode  managed results" in detail
        assert "Configuration   enforced" in detail
        assert "Workgroup output Athena managed" in detail
        assert "No execution yet" not in detail


@pytest.mark.asyncio
async def test_context_and_aws_text_are_rendered_without_markup() -> None:
    client = PageClient()
    client.workgroups[0] = client.workgroups[0].__class__(
        "primary[prod]",
        "ENABLED",
        None,
        None,
    )
    primary_detail = client.workgroup_details.pop("primary")
    client.workgroup_details["primary[prod]"] = primary_detail.__class__(
        client.workgroups[0],
        primary_detail.output_location,
        primary_detail.enforce_workgroup_configuration,
        primary_detail.publish_cloudwatch_metrics,
        primary_detail.bytes_scanned_cutoff,
        primary_detail.engine_version,
        primary_detail.managed_query_results_enabled,
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
        app.query_one(AthenaPage)._refresh_page()  # type: ignore[attr-defined]
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
