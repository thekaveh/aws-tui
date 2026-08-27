from __future__ import annotations

from collections.abc import Callable

import pytest
from textual.app import App, ComposeResult
from textual.color import Color
from textual.containers import Horizontal
from textual.css.query import NoMatches
from textual.widgets import Button, DataTable, OptionList, Static, TextArea
from vmx import NULL_DISPATCHER

from aws_tui.domain.query import ResultColumn, ResultPage
from aws_tui.infra.theme_store import ThemeStore
from aws_tui.ui.widgets.athena.history_view import AthenaHistoryView
from aws_tui.ui.widgets.athena.page import AthenaPage
from aws_tui.ui.widgets.athena.query_view import AthenaQueryView
from aws_tui.ui.widgets.athena.results_view import AthenaResultsView
from aws_tui.ui.widgets.athena.saved_view import AthenaSavedView
from aws_tui.ui.widgets.context_picker import ContextPicker
from aws_tui.ui.widgets.nav_menu import NavMenu
from aws_tui.ui.widgets.service_tab_strip import ServiceTabStrip
from aws_tui.vm.athena.page_vm import AthenaPageVM
from aws_tui.vm.chrome.focus_coordinator_vm import FocusCoordinatorVM, FocusSlot
from aws_tui.vm.nav_menu_vm import NavMenuVM
from aws_tui.vm.services_protocol import ServiceRegistry
from tests.unit.vm.athena.test_page_vm import PageClient, make_page_vm


class _AthenaApp(App[None]):
    CSS = """
    Screen { layout: horizontal; }
    AthenaPage { width: 1fr; }
    #nav-menu { width: 1; min-width: 1; }
    """

    def __init__(self, vm: AthenaPageVM) -> None:
        super().__init__()
        self._vm = vm
        self.focus_coordinator = FocusCoordinatorVM(
            hub=vm._hub,  # type: ignore[attr-defined]
            dispatcher=NULL_DISPATCHER,
        )
        self.focus_coordinator.construct()
        self.nav_vm = NavMenuVM(
            registry=ServiceRegistry(),
            hub=vm._hub,  # type: ignore[attr-defined]
            dispatcher=NULL_DISPATCHER,
        )
        self.nav_vm.construct()

    def compose(self) -> ComposeResult:
        yield AthenaPage(
            self._vm,
            hub=self._vm._hub,  # type: ignore[attr-defined]
            focus_coordinator=self.focus_coordinator,
        )
        yield NavMenu(
            vm=self.nav_vm,
            hub=self._vm._hub,  # type: ignore[attr-defined]
            focus_coordinator=self.focus_coordinator,
            id="nav-menu",
        )

    def on_unmount(self) -> None:
        self.focus_coordinator.dispose()
        self.nav_vm.dispose()


def _athena_target_ids(page: AthenaPage) -> tuple[str, ...]:
    return tuple(widget.id or "" for _slot, widget in page._focus_targets())


def _cycle_athena_target_ids(
    app: _AthenaApp,
    page: AthenaPage,
    *,
    reverse: bool,
) -> tuple[str, ...]:
    expected_count = len(page._focus_targets())
    app.set_focus(app.query_one("#nav-menu", NavMenu))
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
            (
                "athena-history-pane-options",
                "athena-history-results",
                "athena-history-detail-scroll",
            ),
        ),
        ("results", ("athena-results-table",)),
        (
            "saved",
            (
                "athena-named-pane-options",
                "athena-prepared-pane-options",
                "athena-saved-detail-scroll",
            ),
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
    expected_ids = (*context_ids, *surface_ids, "nav-menu")

    async with app.run_test() as pilot:
        await pilot.pause()
        page = app.query_one(AthenaPage)
        assert _athena_target_ids(page) == expected_ids
        assert _cycle_athena_target_ids(app, page, reverse=False) == expected_ids
        assert _cycle_athena_target_ids(app, page, reverse=True) == (
            *reversed(expected_ids[:-1]),
            expected_ids[-1],
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


@pytest.mark.asyncio
async def test_athena_ring_includes_enabled_context_load_more_controls() -> None:
    vm, _client = _build_vm()
    await vm.setup()
    vm._workgroup_pager._current_token = "workgroups-next"  # type: ignore[attr-defined]
    vm._catalog_pager._current_token = "catalogs-next"  # type: ignore[attr-defined]
    vm._database_pager._current_token = "databases-next"  # type: ignore[attr-defined]
    app = _AthenaApp(vm)

    async with app.run_test() as pilot:
        await pilot.pause()
        page = app.query_one(AthenaPage)
        page._refresh_page()  # type: ignore[attr-defined]
        await pilot.pause()

        assert _athena_target_ids(page)[:8] == (
            "athena-source-header",
            "athena-workgroup",
            "athena-more-workgroups",
            "athena-catalog",
            "athena-more-catalogs",
            "athena-database",
            "athena-more-databases",
            "athena-view-tabs",
        )


@pytest.mark.asyncio
async def test_athena_rings_include_enabled_query_history_results_and_saved_controls() -> None:
    client = PageClient()
    client.workgroups.reverse()
    vm, _client = _build_vm(client)
    await vm.setup()
    vm.query.set_sql("SELECT 1")
    vm.history._pager._current_token = "history-next"  # type: ignore[attr-defined]
    vm.results._execution_id = "q-results"  # type: ignore[attr-defined]
    vm.results._pager._current_token = "results-next"  # type: ignore[attr-defined]
    vm.saved._named_pager._current_token = "named-next"  # type: ignore[attr-defined]
    vm.saved._prepared_pager._current_token = "prepared-next"  # type: ignore[attr-defined]
    app = _AthenaApp(vm)

    async with app.run_test() as pilot:
        await pilot.pause()
        page = app.query_one(AthenaPage)

        cancel = app.query_one("#athena-cancel", Button)
        cancel.disabled = False
        assert {
            "athena-editor",
            "athena-execute",
            "athena-cancel",
            "athena-query-detail",
        }.issubset(_athena_target_ids(page))

        await page.action_select_view("history")
        vm.history._pager._current_token = "history-next"  # type: ignore[attr-defined]
        page.query_one(AthenaHistoryView)._refresh()  # type: ignore[attr-defined]
        await pilot.pause()
        assert {
            "athena-history-pane-options",
            "athena-more-history",
            "athena-history-results",
            "athena-history-detail-scroll",
        } <= set(_athena_target_ids(page))

        await page.action_select_view("results")
        vm.results._pager._current_token = "results-next"  # type: ignore[attr-defined]
        page.query_one(AthenaResultsView)._refresh()  # type: ignore[attr-defined]
        await pilot.pause()
        assert {
            "athena-results-table",
            "athena-more-results",
        } <= set(_athena_target_ids(page))

        await page.action_select_view("saved")
        vm.saved._named_pager._current_token = "named-next"  # type: ignore[attr-defined]
        vm.saved._prepared_pager._current_token = "prepared-next"  # type: ignore[attr-defined]
        await vm.select_named_query("named-1")
        page.query_one(AthenaSavedView)._refresh()  # type: ignore[attr-defined]
        await pilot.pause()
        assert {
            "athena-named-pane-options",
            "athena-more-named",
            "athena-prepared-pane-options",
            "athena-more-prepared",
            "athena-saved-detail-scroll",
            "athena-open-editor",
        } <= set(_athena_target_ids(page))


@pytest.mark.asyncio
async def test_athena_ring_syncs_direct_focus_and_projects_to_nav() -> None:
    vm, _client = _build_vm()
    await vm.setup()
    app = _AthenaApp(vm)

    async with app.run_test() as pilot:
        await pilot.pause()
        page = app.query_one(AthenaPage)
        catalog = app.query_one("#athena-catalog", ContextPicker)
        catalog.focus()
        await pilot.pause()

        assert app.focus_coordinator.focused_slot is FocusSlot.ATHENA_CATALOG
        page.cycle_focus(reverse=False)
        await pilot.pause()
        assert app.query_one("#athena-database", ContextPicker).has_focus

        await pilot.click("#athena-tab-history")
        await pilot.pause()
        assert app.query_one("#athena-view-tabs", ServiceTabStrip).has_focus
        assert app.focus_coordinator.focused_slot is FocusSlot.ATHENA_TABS

        app.query_one("#athena-source-header").focus()
        await pilot.pause()
        page.cycle_focus(reverse=True)
        await pilot.pause()
        assert app.query_one("#nav-menu", NavMenu).has_focus
        assert app.focus_coordinator.focused_slot is FocusSlot.NAV_MENU


@pytest.mark.asyncio
async def test_athena_refresh_falls_back_to_the_nearest_available_slot() -> None:
    vm, _client = _build_vm()
    await vm.setup()
    vm.query.set_sql("SELECT 1")
    app = _AthenaApp(vm)

    async with app.run_test() as pilot:
        await pilot.pause()
        cancel = app.query_one("#athena-cancel", Button)
        cancel.disabled = False
        app.focus_coordinator.set_focused_slot(FocusSlot.ATHENA_CANCEL)
        app.set_focus(cancel)
        await pilot.pause()

        vm.query.set_sql("SELECT 2")
        await pilot.pause()

        assert cancel.disabled
        assert app.focus_coordinator.focused_slot is FocusSlot.ATHENA_DETAIL
        assert app.query_one("#athena-query-detail").has_focus


@pytest.mark.asyncio
async def test_context_refresh_reconciles_an_unavailable_pager_to_its_nearest_slot() -> None:
    vm, _client = _build_vm()
    await vm.setup()
    vm._workgroup_pager._current_token = "workgroups-next"  # type: ignore[attr-defined]
    app = _AthenaApp(vm)

    async with app.run_test() as pilot:
        await pilot.pause()
        page = app.query_one(AthenaPage)
        page._refresh_page()  # type: ignore[attr-defined]
        await pilot.pause()
        load_more = app.query_one("#athena-more-workgroups", Button)
        app.focus_coordinator.set_focused_slot(FocusSlot.ATHENA_WORKGROUP_MORE)
        app.set_focus(load_more)
        await pilot.pause()

        vm._workgroup_pager._current_token = None  # type: ignore[attr-defined]
        page._refresh_page()  # type: ignore[attr-defined]
        await pilot.pause()

        assert load_more.disabled
        assert app.focus_coordinator.focused_slot is FocusSlot.ATHENA_CATALOG
        assert app.query_one("#athena-catalog", ContextPicker).has_focus


@pytest.mark.asyncio
async def test_saved_refresh_uses_current_ring_forward_tie_for_disappearing_control() -> None:
    client = PageClient()
    client.workgroups.reverse()
    vm, _client = _build_vm(client)
    await vm.setup()
    await vm.select_view("saved")
    vm.saved._named_pager._current_token = "named-next"  # type: ignore[attr-defined]
    app = _AthenaApp(vm)

    async with app.run_test() as pilot:
        await pilot.pause()
        saved = app.query_one(AthenaSavedView)
        saved._refresh()  # type: ignore[attr-defined]
        await pilot.pause()
        load_more = app.query_one("#athena-more-named", Button)
        app.focus_coordinator.set_focused_slot(FocusSlot.ATHENA_SAVED_NAMED_MORE)
        app.set_focus(load_more)
        await pilot.pause()

        vm.saved._named_pager._current_token = None  # type: ignore[attr-defined]
        vm.saved._notify("has_more_named_queries")  # type: ignore[attr-defined]
        await pilot.pause()

        assert load_more.disabled
        assert app.focus_coordinator.focused_slot is FocusSlot.ATHENA_SECONDARY
        assert app.query_one("#athena-prepared-pane-options", OptionList).has_focus


def _build_vm(client: PageClient | None = None) -> tuple[AthenaPageVM, PageClient]:
    fake = client or PageClient()
    return make_page_vm(fake), fake


def test_results_view_coalesces_property_bursts_into_one_refresh() -> None:
    vm, _client = _build_vm()
    view = AthenaResultsView(vm)
    scheduled: list[Callable[[], None]] = []
    refreshes: list[None] = []
    view.call_after_refresh = scheduled.append  # type: ignore[method-assign]
    view._refresh = lambda: refreshes.append(None)  # type: ignore[method-assign]

    view._on_vm_changed("rows")
    view._on_vm_changed("rendered_rows")
    view._on_vm_changed("has_more")

    assert len(scheduled) == 1
    scheduled[0]()
    assert refreshes == [None]

    view._on_vm_changed("state")
    assert len(scheduled) == 2


@pytest.mark.asyncio
async def test_athena_retains_its_grouped_context_header() -> None:
    vm, _client = _build_vm()
    await vm.setup()
    app = _AthenaApp(vm)

    async with app.run_test() as pilot:
        await pilot.pause()
        header = app.query_one("#athena-context-header", Horizontal)

        assert header.border_title == "AWS context"
        assert app.query_one("#athena-source-header") in header.children
        assert app.query_one("#athena-workgroup") in header.children
        assert app.query_one("#athena-catalog") in header.children
        assert app.query_one("#athena-database") in header.children


@pytest.mark.asyncio
async def test_page_composes_context_tabs_and_all_operational_views() -> None:
    vm, _client = _build_vm()
    await vm.setup()
    app = _AthenaApp(vm)

    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        page = app.query_one(AthenaPage)

        assert page.query_one("#athena-source-header")
        assert page.query_one("#athena-workgroup", ContextPicker)
        assert page.query_one("#athena-catalog", ContextPicker)
        assert page.query_one("#athena-database", ContextPicker)
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
@pytest.mark.parametrize(
    ("action_name", "picker_id", "slot"),
    [
        ("action_choose_workgroup", "athena-workgroup", FocusSlot.ATHENA_WORKGROUP),
        ("action_choose_catalog", "athena-catalog", FocusSlot.ATHENA_CATALOG),
        ("action_choose_database", "athena-database", FocusSlot.ATHENA_DATABASE),
    ],
)
async def test_named_context_action_focuses_and_opens_picker(
    action_name: str,
    picker_id: str,
    slot: FocusSlot,
) -> None:
    vm, _client = _build_vm()
    await vm.setup()
    app = _AthenaApp(vm)

    async with app.run_test() as pilot:
        await pilot.pause()
        page = app.query_one(AthenaPage)
        getattr(page, action_name)()
        await pilot.pause()

        picker = app.query_one(f"#{picker_id}", ContextPicker)
        assert picker.is_open
        assert app.focus_coordinator.focused_slot is slot


@pytest.mark.asyncio
async def test_open_athena_context_picker_preserves_page_regions() -> None:
    vm, _client = _build_vm()
    await vm.setup()
    app = _AthenaApp(vm)

    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        page = app.query_one(AthenaPage)
        header = page.query_one("#athena-context-header", Horizontal)
        tabs = page.query_one("#athena-view-tabs", ServiceTabStrip)
        view_host = page.query_one("#athena-view-host")
        before = (header.region, tabs.region, view_host.region)

        page.action_choose_catalog()
        await pilot.pause()

        assert (header.region, tabs.region, view_host.region) == before

        await pilot.press("escape")
        await pilot.pause()

        assert (header.region, tabs.region, view_host.region) == before


@pytest.mark.asyncio
async def test_unfocused_source_picker_is_dim_while_focused_content_uses_accent() -> None:
    vm, _client = _build_vm()
    await vm.setup()

    class _BuiltinThemeAthenaApp(_AthenaApp):
        CSS = _AthenaApp.CSS + "\n" + ThemeStore().load_builtin("carbon")

    app = _BuiltinThemeAthenaApp(vm)

    async with app.run_test() as pilot:
        await pilot.pause()
        source = app.query_one("#athena-source-header-picker", ContextPicker)
        editor = app.query_one("#athena-editor", TextArea)
        editor.focus()
        await pilot.pause()

        assert source.styles.border_top == ("solid", Color.parse("#2a2d33"))
        assert editor.styles.border_top == ("solid", Color.parse("#6fb8ff"))


@pytest.mark.asyncio
async def test_named_context_actions_close_previously_open_picker() -> None:
    vm, _client = _build_vm()
    await vm.setup()
    app = _AthenaApp(vm)

    async with app.run_test() as pilot:
        await pilot.pause()
        page = app.query_one(AthenaPage)

        page.action_choose_workgroup()
        await pilot.pause()
        workgroup = app.query_one("#athena-workgroup", ContextPicker)
        assert workgroup.is_open

        page.action_choose_catalog()
        await pilot.pause()
        catalog = app.query_one("#athena-catalog", ContextPicker)
        assert not workgroup.is_open
        assert catalog.is_open


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("first_id", "newest_id"),
    [("athena-workgroup", "athena-catalog"), ("athena-catalog", "athena-workgroup")],
)
async def test_same_turn_context_opens_keep_only_newest_picker_focused(
    first_id: str,
    newest_id: str,
) -> None:
    vm, _client = _build_vm()
    await vm.setup()
    app = _AthenaApp(vm)

    async with app.run_test() as pilot:
        await pilot.pause()
        first = app.query_one(f"#{first_id}", ContextPicker)
        newest = app.query_one(f"#{newest_id}", ContextPicker)

        first.open()
        newest.open()
        await pilot.pause()

        assert newest.is_open
        assert not first.is_open
        assert app.focused is newest.query_one(OptionList)


@pytest.mark.asyncio
async def test_live_athena_picker_open_surfaces_missing_child(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    vm, _client = _build_vm()
    await vm.setup()
    app = _AthenaApp(vm)

    async with app.run_test() as pilot:
        await pilot.pause()
        page = app.query_one(AthenaPage)
        query_one = page.query_one

        def missing_picker(selector: str, *args: object, **kwargs: object) -> object:
            if selector == "#athena-workgroup":
                raise NoMatches("live missing Athena picker")
            return query_one(selector, *args, **kwargs)

        monkeypatch.setattr(page, "query_one", missing_picker)
        with pytest.raises(NoMatches, match="live missing Athena picker"):
            page._focus_and_open_picker(  # type: ignore[attr-defined]
                FocusSlot.ATHENA_WORKGROUP,
                "#athena-workgroup",
            )


@pytest.mark.asyncio
async def test_detached_athena_picker_open_is_safe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    vm, _client = _build_vm()
    await vm.setup()
    app = _AthenaApp(vm)

    async with app.run_test() as pilot:
        await pilot.pause()
        page = app.query_one(AthenaPage)
        removal = app.screen.remove_children(AthenaPage)
        assert not page.display

        def unexpected_query(*_args: object, **_kwargs: object) -> object:
            raise AssertionError("detached Athena page queried its picker")

        monkeypatch.setattr(page, "query_one", unexpected_query)
        page._focus_and_open_picker(  # type: ignore[attr-defined]
            FocusSlot.ATHENA_WORKGROUP,
            "#athena-workgroup",
        )
        await removal


@pytest.mark.asyncio
async def test_hidden_removing_athena_page_ignores_queued_refresh(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    vm, _client = _build_vm()
    await vm.setup()
    app = _AthenaApp(vm)

    async with app.run_test() as pilot:
        await pilot.pause()
        page = app.query_one(AthenaPage)
        callbacks: list[Callable[[], None]] = []
        monkeypatch.setattr(page, "call_after_refresh", callbacks.append)
        page._on_page_changed("active_view")  # type: ignore[attr-defined]
        assert len(callbacks) == 1

        removal = app.screen.remove_children(AthenaPage)
        assert not page.display

        def unexpected_query(*_args: object, **_kwargs: object) -> object:
            raise AssertionError("hidden removing Athena page queried its controls")

        monkeypatch.setattr(page, "query_one", unexpected_query)
        callbacks[0]()
        await removal


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("reverse", "expected_id"),
    [(False, "athena-database"), (True, "athena-workgroup")],
)
async def test_tab_cycle_closes_departed_context_picker(
    reverse: bool,
    expected_id: str,
) -> None:
    vm, _client = _build_vm()
    await vm.setup()
    app = _AthenaApp(vm)

    async with app.run_test() as pilot:
        await pilot.pause()
        page = app.query_one(AthenaPage)
        page.action_choose_catalog()
        await pilot.pause()
        picker = app.query_one("#athena-catalog", ContextPicker)
        assert picker.is_open

        page.cycle_focus(reverse=reverse)
        await pilot.pause()

        assert not picker.is_open
        assert app.focused is not None
        assert app.focused.id == expected_id


@pytest.mark.asyncio
async def test_context_picker_changed_routes_through_page_vm() -> None:
    vm, client = _build_vm()
    await vm.setup()
    app = _AthenaApp(vm)

    async with app.run_test() as pilot:
        await pilot.pause()
        page = app.query_one(AthenaPage)
        picker = page.query_one("#athena-workgroup", ContextPicker)
        event = ContextPicker.Changed(picker, "analysts")
        page.on_context_picker_changed(event)
        await page.workers.wait_for_complete()
        await pilot.pause()

        assert vm.context.workgroup == "analysts"
        assert client.catalog_calls[-1] == ("analysts", None)


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
        workgroup = page.query_one("#athena-workgroup", ContextPicker)
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
                    await pilot.pause(0.05)
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
async def test_query_view_inserts_at_cursor_and_synchronizes_vm_without_execution() -> None:
    vm, client = _build_vm()
    await vm.setup()
    app = _AthenaApp(vm)

    async with app.run_test() as pilot:
        editor = app.query_one("#athena-editor", TextArea)
        editor.text = "SELECT  LIMIT 10"
        editor.selection = type(editor.selection).cursor((0, 7))
        await pilot.pause()

        inserted = app.query_one(AthenaQueryView).insert_table_reference(
            '"AwsDataCatalog"."analytics"."events"'
        )
        await pilot.pause()

        expected = 'SELECT "AwsDataCatalog"."analytics"."events" LIMIT 10'
        assert inserted is True
        assert editor.text == expected
        assert vm.query.sql == expected
        assert client.start_calls == []


@pytest.mark.asyncio
async def test_query_view_replaces_active_selection_and_preserves_surrounding_text() -> None:
    vm, client = _build_vm()
    await vm.setup()
    app = _AthenaApp(vm)

    async with app.run_test() as pilot:
        editor = app.query_one("#athena-editor", TextArea)
        editor.text = "SELECT old_table WHERE enabled"
        editor.selection = type(editor.selection)((0, 7), (0, 16))
        await pilot.pause()

        inserted = app.query_one(AthenaQueryView).insert_table_reference(
            '"AwsDataCatalog"."analytics"."events"'
        )
        await pilot.pause()

        expected = 'SELECT "AwsDataCatalog"."analytics"."events" WHERE enabled'
        assert inserted is True
        assert editor.text == expected
        assert vm.query.sql == expected
        assert client.start_calls == []


@pytest.mark.asyncio
async def test_query_view_replaces_reversed_multiline_selection_and_syncs_vm() -> None:
    vm, client = _build_vm()
    await vm.setup()
    app = _AthenaApp(vm)

    async with app.run_test() as pilot:
        editor = app.query_one("#athena-editor", TextArea)
        editor.text = "SELECT\n  old_catalog.\n  old_table\nWHERE enabled"
        editor.selection = type(editor.selection)((2, 11), (1, 2))
        await pilot.pause()

        inserted = app.query_one(AthenaQueryView).insert_table_reference(
            '"AwsDataCatalog"."analytics"."events"'
        )
        await pilot.pause()

        expected = 'SELECT\n  "AwsDataCatalog"."analytics"."events"\nWHERE enabled'
        assert inserted is True
        assert editor.text == expected
        assert vm.query.sql == expected
        assert client.start_calls == []


@pytest.mark.asyncio
async def test_query_view_rejects_empty_identifier_without_mutation() -> None:
    vm, client = _build_vm()
    await vm.setup()
    app = _AthenaApp(vm)

    async with app.run_test() as pilot:
        editor = app.query_one("#athena-editor", TextArea)
        editor.text = "SELECT 1"
        editor.selection = type(editor.selection).cursor((0, 4))
        await pilot.pause()

        assert app.query_one(AthenaQueryView).insert_table_reference("") is False
        assert editor.text == "SELECT 1"
        assert vm.query.sql == "SELECT 1"
        assert client.start_calls == []


@pytest.mark.asyncio
async def test_page_selects_query_view_before_inserting_table_reference() -> None:
    vm, client = _build_vm()
    await vm.setup()
    await vm.select_view("history")
    app = _AthenaApp(vm)

    async with app.run_test() as pilot:
        await pilot.pause()

        inserted = await app.query_one(AthenaPage).insert_table_reference(
            '"AwsDataCatalog"."analytics"."events"'
        )
        await pilot.pause()

        assert inserted is True
        assert vm.active_view == "query"
        assert app.query_one(AthenaQueryView).display is True
        assert vm.query.sql == '"AwsDataCatalog"."analytics"."events"'
        assert client.start_calls == []


@pytest.mark.asyncio
async def test_page_refresh_surfaces_missing_required_control_while_live() -> None:
    vm, _client = _build_vm()
    await vm.setup()
    app = _AthenaApp(vm)

    async with app.run_test() as pilot:
        page = app.query_one(AthenaPage)
        await pilot.pause()
        original_query_one = page.query_one

        def missing_cancel(
            selector: object,
            expect_type: object | None = None,
        ) -> object:
            if selector == "#athena-cancel":
                raise NoMatches("No nodes match '#athena-cancel' on AthenaPage()")
            if expect_type is None:
                return original_query_one(selector)  # type: ignore[arg-type]
            return original_query_one(selector, expect_type)  # type: ignore[arg-type]

        assert page.is_running
        assert page.is_attached
        try:
            page.query_one = missing_cancel  # type: ignore[method-assign]
            with pytest.raises(NoMatches, match="#athena-cancel"):
                page._refresh_page()
        finally:
            page.query_one = original_query_one  # type: ignore[method-assign]


@pytest.mark.asyncio
async def test_page_refresh_tolerates_genuine_page_teardown() -> None:
    vm, _client = _build_vm()
    await vm.setup()
    app = _AthenaApp(vm)

    async with app.run_test() as pilot:
        page = app.query_one(AthenaPage)
        await page.remove()
        await pilot.pause()

        assert not page.is_running
        assert not page.is_attached
        page._refresh_page()


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
        page.focus_default()
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
