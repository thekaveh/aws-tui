from __future__ import annotations

import json
from collections.abc import Callable

import pytest
from textual.app import App, ComposeResult
from textual.color import Color
from textual.containers import Horizontal
from textual.css.query import NoMatches
from textual.geometry import Size
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
from tests.helpers import focus_and_settle, seed_athena_sql, wait_until
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
        ("query", ("athena-editor", "athena-query-status", "athena-query-detail")),
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
        await wait_until(
            lambda: (
                _athena_target_ids(page)[:8]
                == (
                    "athena-source-header",
                    "athena-workgroup",
                    "athena-more-workgroups",
                    "athena-catalog",
                    "athena-more-catalogs",
                    "athena-database",
                    "athena-more-databases",
                    "athena-view-tabs",
                )
            ),
            what="Athena context pager controls entered the focus ring",
        )

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
        await wait_until(
            lambda: (
                {
                    "athena-history-pane-options",
                    "athena-more-history",
                    "athena-history-results",
                    "athena-history-detail-scroll",
                }
                <= set(_athena_target_ids(page))
            ),
            what="Athena history controls entered the focus ring",
        )
        assert {
            "athena-history-pane-options",
            "athena-more-history",
            "athena-history-results",
            "athena-history-detail-scroll",
        } <= set(_athena_target_ids(page))

        await page.action_select_view("results")
        vm.results._pager._current_token = "results-next"  # type: ignore[attr-defined]
        page.query_one(AthenaResultsView)._refresh()  # type: ignore[attr-defined]
        await wait_until(
            lambda: (
                {"athena-results-table", "athena-more-results"} <= set(_athena_target_ids(page))
            ),
            what="Athena results controls entered the focus ring",
        )
        assert {
            "athena-results-table",
            "athena-more-results",
        } <= set(_athena_target_ids(page))

        await page.action_select_view("saved")
        vm.saved._named_pager._current_token = "named-next"  # type: ignore[attr-defined]
        vm.saved._prepared_pager._current_token = "prepared-next"  # type: ignore[attr-defined]
        await vm.select_named_query("named-1")
        page.query_one(AthenaSavedView)._refresh()  # type: ignore[attr-defined]
        await wait_until(
            lambda: (
                {
                    "athena-named-pane-options",
                    "athena-more-named",
                    "athena-prepared-pane-options",
                    "athena-more-prepared",
                    "athena-saved-detail-scroll",
                    "athena-open-editor",
                }
                <= set(_athena_target_ids(page))
            ),
            what="Athena saved-query controls entered the focus ring",
        )
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
        await focus_and_settle(catalog)
        await wait_until(
            lambda: (
                catalog.has_focus and app.focus_coordinator.focused_slot is FocusSlot.ATHENA_CATALOG
            ),
            what="Athena catalog focus reached the coordinator",
        )

        assert app.focus_coordinator.focused_slot is FocusSlot.ATHENA_CATALOG
        page.cycle_focus(reverse=False)
        await wait_until(
            lambda: app.query_one("#athena-database", ContextPicker).has_focus,
            what="Athena database took focus",
        )
        assert app.query_one("#athena-database", ContextPicker).has_focus

        await pilot.click("#athena-tab-history")
        await wait_until(
            lambda: (
                app.query_one("#athena-view-tabs", ServiceTabStrip).has_focus
                and app.focus_coordinator.focused_slot is FocusSlot.ATHENA_TABS
            ),
            what="clicked Athena tab received coordinated focus",
        )
        assert app.query_one("#athena-view-tabs", ServiceTabStrip).has_focus
        assert app.focus_coordinator.focused_slot is FocusSlot.ATHENA_TABS

        app.query_one("#athena-source-header").focus()
        await pilot.pause()
        page.cycle_focus(reverse=True)
        await wait_until(
            lambda: (
                app.query_one("#nav-menu", NavMenu).has_focus
                and app.focus_coordinator.focused_slot is FocusSlot.NAV_MENU
            ),
            what="Athena reverse cycle focused navigation",
        )
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
        await wait_until(
            lambda: (
                cancel.disabled
                and app.focus_coordinator.focused_slot is FocusSlot.ATHENA_STATUS
                and app.query_one("#athena-query-status").has_focus
            ),
            what="disabled Athena cancel reconciled focus to query status",
        )

        assert cancel.disabled
        assert app.focus_coordinator.focused_slot is FocusSlot.ATHENA_STATUS
        assert app.query_one("#athena-query-status").has_focus


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
        await wait_until(
            lambda: (
                load_more.disabled
                and app.focus_coordinator.focused_slot is FocusSlot.ATHENA_CATALOG
                and app.query_one("#athena-catalog", ContextPicker).has_focus
            ),
            what="disabled workgroup pager reconciled focus to catalog",
        )

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
        await wait_until(
            lambda: (
                load_more.disabled
                and app.focus_coordinator.focused_slot is FocusSlot.ATHENA_SECONDARY
                and app.query_one("#athena-prepared-pane-options", OptionList).has_focus
            ),
            what="disabled saved-query pager reconciled focus to prepared queries",
        )

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
async def test_athena_context_uses_an_unframed_row_of_bordered_selectors() -> None:
    vm, _client = _build_vm()
    await vm.setup()
    app = _AthenaApp(vm)

    async with app.run_test() as pilot:
        await pilot.pause()
        row = app.query_one("#athena-context-row", Horizontal)

        assert row.border_title is None
        assert row.styles.border_top[0] in {"", "none"}
        assert app.query_one("#athena-source-header") in row.children
        for selector in ("#athena-workgroup", "#athena-catalog", "#athena-database"):
            picker = app.query_one(selector, ContextPicker)
            assert picker in row.children
            assert picker.styles.border_top[0] in {"solid", "heavy"}


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
@pytest.mark.parametrize("picker_id", ["athena-workgroup", "athena-catalog", "athena-database"])
async def test_populated_athena_context_picker_opens_by_mouse_and_keyboard(
    picker_id: str,
) -> None:
    vm, _client = _build_vm()
    await vm.setup()
    app = _AthenaApp(vm)

    async with app.run_test(size=(245, 62)) as pilot:
        picker = app.query_one(f"#{picker_id}", ContextPicker)
        assert picker.disabled is False

        await pilot.click(f"#{picker_id}")
        await wait_until(
            lambda: picker.is_open and picker.has_focus_within,
            what="mouse-opened Athena context picker took focus",
        )
        assert picker.is_open

        await pilot.press("escape")
        await focus_and_settle(picker)
        await pilot.press("enter")
        await wait_until(
            lambda: picker.is_open and picker.has_focus_within,
            what="Enter opened Athena context picker",
        )
        assert picker.is_open

        await pilot.press("escape")
        await focus_and_settle(picker)
        await pilot.press("space")
        await wait_until(
            lambda: picker.is_open and picker.has_focus_within,
            what="Space opened Athena context picker",
        )
        assert picker.is_open

        selected = picker.value
        await pilot.press("enter")
        await wait_until(
            lambda: not picker.is_open,
            what="Enter committed and closed Athena context picker",
        )
        assert not picker.is_open
        assert picker.value == selected


@pytest.mark.asyncio
async def test_open_athena_context_picker_preserves_page_regions() -> None:
    vm, _client = _build_vm()
    await vm.setup()
    app = _AthenaApp(vm)

    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        page = app.query_one(AthenaPage)
        row = page.query_one("#athena-context-row", Horizontal)
        tabs = page.query_one("#athena-view-tabs", ServiceTabStrip)
        view_host = page.query_one("#athena-view-host")
        before = (row.region, tabs.region, view_host.region)

        page.action_choose_catalog()
        await wait_until(
            lambda: (
                page.query_one("#athena-catalog", ContextPicker).is_open
                and app.focused
                is page.query_one("#athena-catalog", ContextPicker).query_one(OptionList)
            ),
            what="Athena catalog overlay opened before geometry comparison",
        )
        # Drain picker-open layout before asserting page regions did not move.
        await pilot.pause()

        assert (row.region, tabs.region, view_host.region) == before

        await pilot.press("escape")
        # Drain Escape and layout before asserting page regions did not move.
        await pilot.pause()

        assert (row.region, tabs.region, view_host.region) == before


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
        workgroup = app.query_one("#athena-workgroup", ContextPicker)
        editor = app.query_one("#athena-editor", TextArea)
        await focus_and_settle(editor)
        await wait_until(
            lambda: (
                editor.has_focus
                and source.styles.border_top == ("solid", Color.parse("#2a2d33"))
                and workgroup.styles.border_top == ("solid", Color.parse("#2a2d33"))
                and editor.styles.border_top == ("solid", Color.parse("#6fb8ff"))
            ),
            what="focused Athena editor rendered accent with dim context pickers",
        )

        assert source.styles.border_top == ("solid", Color.parse("#2a2d33"))
        assert workgroup.styles.border_top == ("solid", Color.parse("#2a2d33"))
        assert editor.styles.border_top == ("solid", Color.parse("#6fb8ff"))

        await focus_and_settle(workgroup)
        await wait_until(
            lambda: (
                workgroup.has_focus
                and workgroup.styles.border_top == ("heavy", Color.parse("#6fb8ff"))
            ),
            what="focused Athena workgroup rendered its accent border",
        )

        assert workgroup.styles.border_top == ("heavy", Color.parse("#6fb8ff"))


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

        # Shared picker intent is an immediate exclusivity contract.  The
        # deferred reconciliation remains a safety net, but the UI must never
        # render two context dropdowns open while Textual drains messages.
        assert not first.is_open
        await wait_until(
            lambda: (
                newest.is_open and not first.is_open and app.focused is newest.query_one(OptionList)
            ),
            what="newest Athena context picker received overlay focus",
        )

        assert newest.is_open
        assert not first.is_open
        assert app.focused is newest.query_one(OptionList)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("action_name", "picker_id", "slot"),
    [
        ("action_choose_workgroup", "athena-workgroup", FocusSlot.ATHENA_WORKGROUP),
        ("action_choose_catalog", "athena-catalog", FocusSlot.ATHENA_CATALOG),
        ("action_choose_database", "athena-database", FocusSlot.ATHENA_DATABASE),
    ],
)
async def test_reprojecting_an_open_pickers_slot_leaves_it_open(
    action_name: str,
    picker_id: str,
    slot: FocusSlot,
) -> None:
    """A slot whose picker is already open must survive being re-projected.

    These pickers are focus-slot *targets* that host their own overlay, so
    focusing the target moves focus up out of the overlay and the overlay
    reports that blur as a dismissal. Athena queues slot projections through
    ``call_after_refresh`` exactly as Glue does, so a projection landing after
    the picker opened would close it -- the #235 defect, pinned here for the
    page where it was never reproduced rather than left latent.
    """
    vm, _client = _build_vm()
    await vm.setup()
    app = _AthenaApp(vm)

    async with app.run_test() as pilot:
        await pilot.pause()
        page = app.query_one(AthenaPage)
        getattr(page, action_name)()
        picker = app.query_one(f"#{picker_id}", ContextPicker)
        overlay = picker.query_one(OptionList)
        await wait_until(
            lambda: app.focused is overlay,
            what=f"{picker_id}'s overlay took focus",
        )

        page.project_focus_slot(slot)
        # Drain projection events before checking the open Athena picker retained focus.
        await pilot.pause()

        assert picker.is_open
        assert app.focused is overlay
        assert app.focus_coordinator.focused_slot is slot


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
        await wait_until(
            lambda: (
                not picker.is_open and app.focused is not None and app.focused.id == expected_id
            ),
            what="Athena pane cycle closed picker and focused destination",
        )

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
            await wait_until(lambda: bool(calls), what="explicit load-more route completed")
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
        await wait_until(
            lambda: page.query_one(AthenaHistoryView).display,
            what="lazy Athena history view rendered",
        )
        assert client.history_calls == [("primary", None)]
        assert page.query_one(AthenaHistoryView).display

        await page.action_select_view("results")
        await wait_until(
            lambda: page.query_one(AthenaResultsView).display,
            what="Athena results view rendered",
        )
        assert page.query_one(AthenaResultsView).display
        assert page.query_one(AthenaResultsView).query_one(DataTable)

        await page.action_select_view("saved")
        await wait_until(
            lambda: page.query_one(AthenaSavedView).display,
            what="lazy Athena saved-query view rendered",
        )
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
        await wait_until(
            lambda: (
                vm.query.sql == "SELECT count(*) FROM events"
                and not app.query_one("#athena-execute", Button).disabled
            ),
            what="editor change reached query VM and enabled execution",
        )

        assert vm.query.sql == "SELECT count(*) FROM events"
        await pilot.click("#athena-execute")
        await wait_until(
            lambda: bool(client.start_calls) and vm.results.rows == (("1",),),
            what="executed Athena editor query returned its result row",
        )

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
        await seed_athena_sql(pilot, vm.query, editor, "SELECT  LIMIT 10")
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
        await seed_athena_sql(pilot, vm.query, editor, "SELECT old_table WHERE enabled")
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
        await seed_athena_sql(
            pilot, vm.query, editor, "SELECT\n  old_catalog.\n  old_table\nWHERE enabled"
        )
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
        await seed_athena_sql(pilot, vm.query, editor, "SELECT 1")
        editor.selection = type(editor.selection).cursor((0, 4))
        # Drain selection messages before testing the synchronous empty-identifier no-op.
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
        await wait_until(
            lambda: (
                app.query_one(AthenaQueryView).display
                and vm.query.sql == '"AwsDataCatalog"."analytics"."events"'
            ),
            what="inserted table reference rendered in the query view",
        )

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

    async with app.run_test():
        page = app.query_one(AthenaPage)
        await page.remove()

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
        await wait_until(
            lambda: (
                vm.active_view == "query"
                and app.query_one(AthenaQueryView).display
                and app.query_one("#athena-editor", TextArea).text == "SELECT count(*) FROM events"
            ),
            what="saved query opened with its SQL rendered in the editor",
        )

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
        await wait_until(
            lambda: app.query_one("#athena-execute", Button).has_focus,
            what="Tab moved Athena editor focus to Execute",
        )
        assert app.query_one("#athena-execute", Button).has_focus


@pytest.mark.asyncio
async def test_query_controls_are_compact_above_editor_and_inside_their_frame() -> None:
    vm, _client = _build_vm()
    await vm.setup()
    app = _AthenaApp(vm)

    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        controls = app.query_one("#athena-query-controls")
        editor = app.query_one("#athena-editor", TextArea)
        execute = app.query_one("#athena-execute", Button)
        cancel = app.query_one("#athena-cancel", Button)

        assert controls.border_title == "query controls"
        assert controls.region.bottom <= editor.region.y
        assert controls.region.x <= execute.region.x < execute.region.right <= controls.region.right
        assert controls.region.x <= cancel.region.x < cancel.region.right <= controls.region.right
        assert (
            controls.region.y <= execute.region.y < execute.region.bottom <= controls.region.bottom
        )
        assert controls.region.y <= cancel.region.y < cancel.region.bottom <= controls.region.bottom
        assert execute.region.y == cancel.region.y
        assert execute.region.size == Size(5, 3)
        assert cancel.region.size == Size(5, 3)
        assert execute.content_region.height >= 1
        assert cancel.content_region.height >= 1
        assert controls.content_region.contains_region(execute.region)
        assert controls.content_region.contains_region(cancel.region)

        vm.query.set_sql("SELECT 1")
        for _ in range(10):
            await pilot.pause(0.01)
            if not execute.disabled:
                break
        await focus_and_settle(execute)
        await wait_until(
            lambda: execute.has_focus,
            what="Athena Execute button took focus for geometry checks",
        )

        assert execute.has_focus
        assert execute.region.size == Size(5, 3)
        assert cancel.region.size == Size(5, 3)
        assert controls.content_region.contains_region(execute.region)
        assert controls.content_region.contains_region(cancel.region)


@pytest.mark.asyncio
async def test_query_buttons_follow_vmx_command_state() -> None:
    vm, _client = _build_vm()
    await vm.setup()
    app = _AthenaApp(vm)

    async with app.run_test() as pilot:
        execute = app.query_one("#athena-execute", Button)
        cancel = app.query_one("#athena-cancel", Button)
        assert execute.disabled
        assert cancel.disabled

        vm.query.set_sql("SELECT 1")
        for _ in range(10):
            await pilot.pause(0.01)
            if not execute.disabled:
                break
        assert not execute.disabled
        assert cancel.disabled

        vm.query._busy = True  # type: ignore[attr-defined]
        vm.query._is_submitting = True  # type: ignore[attr-defined]
        vm.query._notify("is_submitting")  # type: ignore[attr-defined]
        for _ in range(10):
            await pilot.pause(0.01)
            if execute.disabled and not cancel.disabled:
                break
        assert execute.disabled
        assert not cancel.disabled


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


async def test_drafts_off_preserves_editor_and_focus_targets() -> None:
    vm, _ = _build_vm()
    await vm.setup()
    async with _AthenaApp(vm).run_test() as pilot:
        await pilot.pause()
        page = pilot.app.query_one(AthenaPage)
        assert not page.query_one("#athena-drafts", Button).display
        assert "athena-drafts" not in _athena_target_ids(page)
        assert page.query_one("#athena-editor", TextArea).border_title == "query editor"
        assert page.query_one("#athena-query-status", Static).content == "Enter a read-only query"
    vm.dispose()


async def test_enabled_drafts_native_and_manual_focus(tmp_path) -> None:
    from tests.athena_drafts_helpers import runtime_at

    drafts, _ = runtime_at(tmp_path)
    vm = make_page_vm(PageClient(), drafts=drafts)
    await vm.setup()
    app = _AthenaApp(vm)
    try:
        async with app.run_test() as pilot:
            await pilot.pause()
            page = app.query_one(AthenaPage)
            button = page.query_one("#athena-drafts", Button)
            assert button.display
            assert (FocusSlot.ATHENA_DRAFTS, button) in page._focus_targets()
            await focus_and_settle(page.query_one("#athena-editor", TextArea))
            page.query_one(AthenaQueryView).action_focus_next()
            await pilot.pause()
            assert app.focused is button
            assert app.focus_coordinator.focused_slot is FocusSlot.ATHENA_DRAFTS
            button.disabled = True
            assert "athena-drafts" not in _athena_target_ids(page)
    finally:
        await vm.shutdown()
        vm.dispose()
        await drafts.shutdown()
        drafts.dispose()


async def test_drafts_button_label_is_fully_rendered_at_80_columns(tmp_path):
    from tests.athena_drafts_helpers import runtime_at

    drafts, _ = runtime_at(tmp_path)
    vm = make_page_vm(PageClient(), drafts=drafts)
    await vm.setup()
    app = _AthenaApp(vm)
    try:
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause()
            button = app.query_one("#athena-drafts", Button)
            strips = app.screen._compositor.render_strips()
            painted = "".join(
                strip.crop(button.content_region.x, button.content_region.right).text
                for strip in strips[button.content_region.y : button.content_region.bottom]
            )
            assert "Drafts" in painted
    finally:
        await vm.shutdown()
        vm.dispose()
        await drafts.shutdown()
        drafts.dispose()


@pytest.mark.parametrize("dismissal", ["escape", "close", "default"])
async def test_manager_close_result_restores_drafts_and_native_slot(
    tmp_path, monkeypatch, dismissal
):
    from aws_tui.ui.widgets.athena.drafts_modal import AthenaDraftsModal
    from tests.athena_drafts_helpers import runtime_at
    from tests.helpers import drain_workers

    drafts, _ = runtime_at(tmp_path)
    vm = make_page_vm(PageClient(), drafts=drafts)
    await vm.setup()
    app = _AthenaApp(vm)
    results = []
    push = app.push_screen

    def capture(screen, callback=None, **kwargs):
        def record_result(result):
            results.append(result)
            if callback is not None:
                callback(result)

        return push(screen, record_result, **kwargs)

    monkeypatch.setattr(app, "push_screen", capture)
    try:
        async with app.run_test() as pilot:
            button = app.query_one("#athena-drafts", Button)
            await focus_and_settle(button)
            await pilot.press("enter")
            await wait_until(
                lambda: isinstance(app.screen, AthenaDraftsModal), what="draft manager"
            )
            await drain_workers(app)
            await pilot.pause()
            if dismissal == "escape":
                await pilot.press("escape")
            elif dismissal == "default":
                app.screen.dismiss()
            else:
                await focus_and_settle(app.screen.query_one("#athena-drafts-close", Button))
                await pilot.press("enter")
            await pilot.pause()
            assert results == [None if dismissal == "default" else "closed"]
            assert app.focused is button
            assert app.focus_coordinator.focused_slot is FocusSlot.ATHENA_DRAFTS
    finally:
        await vm.shutdown()
        vm.dispose()
        await drafts.shutdown()
        drafts.dispose()


@pytest.mark.parametrize("unavailable", ["disabled", "hidden", "detached", "another-modal"])
async def test_draft_focus_callback_checks_current_attachment_and_target(
    tmp_path, monkeypatch, unavailable
):
    from textual.screen import ModalScreen

    from aws_tui.ui.widgets.athena.drafts_modal import AthenaDraftsModal
    from tests.athena_drafts_helpers import runtime_at
    from tests.helpers import drain_workers

    drafts, _ = runtime_at(tmp_path)
    vm = make_page_vm(PageClient(), drafts=drafts)
    await vm.setup()
    app = _AthenaApp(vm)
    try:
        async with app.run_test() as pilot:
            page = app.query_one(AthenaPage)
            button = page.query_one("#athena-drafts", Button)
            await focus_and_settle(button)
            await pilot.press("enter")
            await wait_until(
                lambda: isinstance(app.screen, AthenaDraftsModal), what="draft manager"
            )
            await drain_workers(app)
            await pilot.pause()
            if unavailable == "disabled":
                button.disabled = True
            elif unavailable == "hidden":
                button.display = False
            elif unavailable == "detached":
                await page.remove()
            else:
                # The result callback will defer to a refresh; another modal owns that refresh.
                app.screen.dismiss("closed")
                next_modal = ModalScreen()
                app.push_screen(next_modal)
                await pilot.pause()
                assert app.screen is next_modal
                assert app.focused is None or page not in app.focused.ancestors_with_self
                return
            await pilot.press("escape")
            await pilot.pause()
            if unavailable == "detached":
                assert not page.is_attached
                assert app.focused is None or page not in app.focused.ancestors_with_self
            else:
                assert app.focused is page.query_one("#athena-editor", TextArea)
                assert app.focus_coordinator.focused_slot is FocusSlot.ATHENA_PRIMARY
    finally:
        await vm.shutdown()
        vm.dispose()
        await drafts.shutdown()
        drafts.dispose()


async def _loaded_result_controls_vm():
    client = PageClient()
    calls = []
    columns = (
        ResultColumn("duplicate", "varchar", "NULLABLE"),
        ResultColumn("duplicate", "varchar", "NULLABLE"),
        ResultColumn("third", "varchar", "NULLABLE"),
    )
    rows = ((None, "", "NULL"), ("10", "line one\n[bold]é[/bold]" * 30, "z"), ("2", "tail", "a"))

    async def results(execution_id, *, start_token=None):
        calls.append((execution_id, start_token))
        assert execution_id == "controls"
        if start_token is None:
            return ResultPage(columns, rows, "explicit-next")
        assert start_token == "explicit-next"
        return ResultPage(columns, (("1", "tail more", "b"),), None)

    async def forbidden_start(*args, **kwargs):
        raise AssertionError("Local result controls must not start queries")

    client.get_results_page = results
    client.start_query_execution = forbidden_start
    vm, _ = _build_vm(client)
    await vm.setup()
    await vm.results.load("controls")
    await vm.select_view("results")
    return vm, calls


@pytest.mark.parametrize("size", [(80, 24), (120, 40)])
async def test_result_cell_inspector_preserves_literal_full_value_and_original_coordinate(size):
    vm, calls = await _loaded_result_controls_vm()
    app = _AthenaApp(vm)
    async with app.run_test(size=size) as pilot:
        await pilot.pause()
        table = app.query_one("#athena-results-table", DataTable)
        assert table.cursor_type == "cell"
        table.move_cursor(row=1, column=1)
        await focus_and_settle(table)
        assert vm.results.selection == (1, 1)
        app.query_one(AthenaResultsView).action_inspect_cell()
        await pilot.pause()
        body = app.screen.query_one("#athena-cell-value", TextArea)
        assert body.read_only
        assert body.text == vm.results.rows[1][1]
        assert "[bold]" in body.text
        await pilot.press("enter")
        assert body.text == vm.results.rows[1][1]
        await pilot.press("escape")
        await pilot.pause()
        assert app.focused is table
        assert (table.cursor_row, table.cursor_column) == (1, 1)
        assert vm.results.selection == (1, 1)
        assert calls == [("controls", None)]


async def test_result_copy_uses_original_null_empty_literal_and_duplicate_indexed_columns():
    vm, calls = await _loaded_result_controls_vm()
    app = _AthenaApp(vm)
    copies = []
    app.copy_value = lambda value, label: copies.append((value, label))
    async with app.run_test() as pilot:
        await pilot.pause()
        view = app.query_one(AthenaResultsView)
        for column in range(3):
            assert vm.results.select_cell(0, column)
            view.action_copy_cell()
        view.action_copy_row()
        assert [value for value, _ in copies] == ["null", '""', '"NULL"', '[null,"","NULL"]']
        assert all(label in {"Athena cell", "Athena row"} for _, label in copies)
        vm.results.set_filter("no matching cells")
        await pilot.pause()
        view.action_copy_cell()
        view.action_copy_row()
        view.action_inspect_cell()
        assert len(copies) == 4
        assert len(app.screen_stack) == 1
        assert vm.results.selection is None
        assert calls == [("controls", None)]


async def test_local_result_filter_sort_reset_preserve_selection_and_explicit_paging():
    vm, calls = await _loaded_result_controls_vm()
    app = _AthenaApp(vm)
    async with app.run_test() as pilot:
        await pilot.pause()
        view = app.query_one(AthenaResultsView)
        vm.results.select_cell(2, 0)
        view.action_sort_results()
        await pilot.pause()
        assert vm.results.visible_row_indices == (1, 2, 0)
        assert vm.results.selection == (2, 0)
        view.action_sort_results()
        await pilot.pause()
        assert vm.results.visible_row_indices == (2, 1, 0)
        view.action_sort_results()
        await pilot.pause()
        assert vm.results.visible_row_indices == (0, 1, 2)
        vm.results.set_filter("tail")
        view.action_sort_results()
        await pilot.pause()
        assert vm.results.visible_row_indices == (2,)
        await vm.results.load_more()
        await pilot.pause()
        assert vm.results.visible_row_indices == (3, 2)
        assert vm.results.selection == (2, 0)
        assert (app.query_one(DataTable).cursor_row, app.query_one(DataTable).cursor_column) == (
            1,
            0,
        )
        view.action_reset_results()
        await pilot.pause()
        assert vm.results.visible_row_indices == (0, 1, 2, 3)
        assert vm.results.selection == (2, 0)
        assert calls == [("controls", None), ("controls", "explicit-next")]


async def test_result_footer_states_loaded_only_scope_for_zero_matches_and_limit():
    vm, calls = await _loaded_result_controls_vm()
    app = _AthenaApp(vm)
    async with app.run_test() as pilot:
        await pilot.pause()
        vm.results.set_filter("no matching cells")
        await pilot.pause()
        footer = app.query_one("#athena-results-footer", Static)
        assert footer.content == "0 visible / 3 loaded · local · more available"
        vm.results._pager._limit_reached = True
        app.query_one(AthenaResultsView)._refresh()
        assert footer.content == "0 visible / 3 loaded · local · safety limit"
        assert calls == [("controls", None)]


@pytest.mark.parametrize("operation", ["cancel", "apply", "clear"])
async def test_result_filter_modal_has_explicit_semantics_and_restores_table(operation):
    from textual.widgets import Input

    from aws_tui.ui.widgets.modal_button import ModalButton

    vm, calls = await _loaded_result_controls_vm()
    vm.results.set_filter("tail")
    app = _AthenaApp(vm)
    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.pause()
        view = app.query_one(AthenaResultsView)
        table = app.query_one(DataTable)
        await focus_and_settle(table)
        view.action_filter_results()
        await pilot.pause()
        field = app.screen.query_one(Input)
        assert field.value == "tail"
        field.value = "missing"
        if operation == "cancel":
            await pilot.press("escape")
        elif operation == "apply":
            await pilot.press("enter")
        else:
            button = next(b for b in app.screen.query(ModalButton) if b.button_id == "clear")
            await focus_and_settle(button)
            await pilot.press("enter")
        await pilot.pause()
        assert (
            vm.results.filter_text == {"cancel": "tail", "apply": "missing", "clear": ""}[operation]
        )
        assert app.focused is table
        assert calls == [("controls", None)]


@pytest.mark.parametrize("overlay", ["inspect", "filter"])
@pytest.mark.parametrize("retirement", ["clear", "dispose", "remove"])
async def test_result_overlays_close_and_stale_callbacks_cannot_restore_retired_data(
    overlay, retirement
):
    vm, _ = await _loaded_result_controls_vm()
    app = _AthenaApp(vm)
    async with app.run_test() as pilot:
        await pilot.pause()
        view = app.query_one(AthenaResultsView)
        vm.results.select_cell(1, 1)
        if overlay == "inspect":
            view.action_inspect_cell()
        else:
            view.action_filter_results()
        await pilot.pause()
        assert len(app.screen_stack) == 2
        if retirement == "remove":
            await app.query_one(AthenaPage).remove()
        elif retirement == "clear":
            vm.results.clear()
        else:
            vm.results.dispose()
        await pilot.pause()
        assert len(app.screen_stack) == 1
        assert vm.results.selection is None or retirement == "remove"
        assert app.focused is None or not isinstance(app.focused, DataTable)


async def test_result_refresh_surfaces_live_missing_control_and_tolerates_teardown():
    vm, _ = await _loaded_result_controls_vm()
    app = _AthenaApp(vm)
    async with app.run_test() as pilot:
        await pilot.pause()
        view = app.query_one(AthenaResultsView)
        query = view.query_one

        def missing(selector, expect_type=None):
            if selector == "#athena-results-footer":
                raise NoMatches("Missing required footer")
            return query(selector, expect_type) if expect_type else query(selector)

        view.query_one = missing
        with pytest.raises(NoMatches, match="Missing required footer"):
            view._refresh()
        view.query_one = query
        await view.remove()
        view._refresh()


async def test_delayed_result_highlight_cannot_reselect_a_different_projection_or_restored_cell(
    monkeypatch,
):
    vm, _ = await _loaded_result_controls_vm()
    app = _AthenaApp(vm)
    async with app.run_test() as pilot:
        await pilot.pause()
        view = app.query_one(AthenaResultsView)
        table = app.query_one(DataTable)
        assert table.cursor_type == "cell"
        await focus_and_settle(table)
        captured = []
        post = table.post_message

        def capture(message):
            if isinstance(message, DataTable.CellHighlighted):
                captured.append(message)
                return True
            return post(message)

        monkeypatch.setattr(table, "post_message", capture)
        table.move_cursor(row=1, column=1)
        assert captured
        stale = captured[-1]
        monkeypatch.setattr(table, "post_message", post)
        vm.results.select_cell(2, 2)
        vm.results.set_sort(0)
        await pilot.pause()
        view.on_data_table_cell_highlighted(stale)
        assert vm.results.selection == (2, 2)
        assert (table.cursor_row, table.cursor_column) == (1, 2)
        vm.results.set_filter("missing")
        await pilot.pause()
        vm.results.reset_projection()
        await pilot.pause()
        assert vm.results.selection is None
        view.on_data_table_cell_highlighted(stale)
        assert vm.results.selection is None


async def test_results_refresh_when_activated_and_after_background_modal_changes():
    from textual.screen import ModalScreen

    vm, calls = await _loaded_result_controls_vm()
    await vm.select_view("query")
    app = _AthenaApp(vm)
    async with app.run_test() as pilot:
        await pilot.pause()
        await vm.select_view("results")
        await pilot.pause()
        table = app.query_one(DataTable)
        assert table.row_count == 3
        modal = ModalScreen()
        app.push_screen(modal)
        await pilot.pause()
        vm.results.set_filter("tail")
        await pilot.pause()
        assert table.row_count == 3
        modal.dismiss()
        await pilot.pause()
        assert table.row_count == 1
        assert vm.results.selection is None
        assert calls == [("controls", None)]


@pytest.mark.parametrize("overlay", ["inspect", "filter"])
async def test_result_overlay_closes_when_results_view_is_hidden(overlay):
    vm, _ = await _loaded_result_controls_vm()
    app = _AthenaApp(vm)
    async with app.run_test() as pilot:
        await pilot.pause()
        vm.results.select_cell(1, 1)
        view = app.query_one(AthenaResultsView)
        if overlay == "inspect":
            view.action_inspect_cell()
        else:
            view.action_filter_results()
        await pilot.pause()
        await vm.select_view("query")
        await pilot.pause()
        assert len(app.screen_stack) == 1
        assert not view.display
        assert app.focused is None or not isinstance(app.focused, DataTable)


@pytest.mark.parametrize(
    ("column", "status", "text"), [(0, "null", ""), (1, "empty string", ""), (2, "string", "NULL")]
)
async def test_result_inspector_distinguishes_null_empty_and_literal_null(column, status, text):
    vm, _ = await _loaded_result_controls_vm()
    app = _AthenaApp(vm)
    async with app.run_test() as pilot:
        await pilot.pause()
        vm.results.select_cell(0, column)
        app.query_one(AthenaResultsView).action_inspect_cell()
        await pilot.pause()
        assert app.screen.query_one("#athena-cell-value", TextArea).text == text
        metadata = app.screen.query_one("#athena-cell-metadata", Static)
        assert f"column {column + 1} · {status}" in str(metadata.content)
        await pilot.press("escape")


async def test_stale_result_filter_callback_and_inspector_restore_guard_changed_generation(
    monkeypatch,
):
    vm, _ = await _loaded_result_controls_vm()
    app = _AthenaApp(vm)
    callbacks = []
    push = app.push_screen

    def capture(screen, callback=None, **kwargs):
        callbacks.append(callback)
        return push(screen, callback, **kwargs)

    monkeypatch.setattr(app, "push_screen", capture)
    async with app.run_test() as pilot:
        await pilot.pause()
        view = app.query_one(AthenaResultsView)
        vm.results.select_cell(1, 1)
        old_generation = vm.results.projection_generation
        view.action_filter_results()
        await pilot.pause()
        stale_filter = callbacks[-1]
        vm.results.clear()
        await pilot.pause()
        await vm.results.load("controls")
        await pilot.pause()
        vm.results.select_cell(2, 2)
        stale_filter("retired filter")
        view._restore_table(old_generation, (1, 1))
        await pilot.pause()
        assert vm.results.filter_text == ""
        assert vm.results.selection == (2, 2)


async def test_result_modal_invalidates_under_nested_overlay_before_resuming():
    from textual.screen import ModalScreen

    vm, _ = await _loaded_result_controls_vm()
    app = _AthenaApp(vm)
    async with app.run_test() as pilot:
        await pilot.pause()
        vm.results.select_cell(1, 1)
        app.query_one(AthenaResultsView).action_inspect_cell()
        await pilot.pause()
        inspector = app.screen
        next_modal = ModalScreen()
        app.push_screen(next_modal)
        await pilot.pause()
        vm.results.clear()
        await pilot.pause()
        assert app.screen is next_modal
        assert inspector.query_one(TextArea).text == ""
        next_modal.dismiss()
        await pilot.pause()
        assert len(app.screen_stack) == 1
        assert vm.results.selection is None


async def test_result_table_cell_messages_do_not_expose_rendered_values_in_repr(monkeypatch):
    vm, _ = await _loaded_result_controls_vm()
    app = _AthenaApp(vm)
    messages = []
    post = DataTable.post_message

    def capture(table, message):
        if isinstance(message, (DataTable.CellHighlighted, DataTable.CellSelected)):
            messages.append(message)
        return post(table, message)

    monkeypatch.setattr(DataTable, "post_message", capture)
    async with app.run_test() as pilot:
        await pilot.pause()
        table = app.query_one(DataTable)
        table.move_cursor(row=1, column=1)
        table.action_select_cursor()
        await pilot.pause()
        assert any(isinstance(message, DataTable.CellSelected) for message in messages)
        assert all(message.value is None for message in messages)
        assert all("[bold]" not in repr(message) for message in messages)
        assert vm.results.selected_cell == vm.results.rows[1][1]


@pytest.mark.parametrize("overlay", ["inspect", "filter"])
async def test_result_overlay_retirement_before_modal_mount_does_not_leave_stale_overlay(overlay):
    vm, _ = await _loaded_result_controls_vm()
    app = _AthenaApp(vm)
    async with app.run_test() as pilot:
        await pilot.pause()
        vm.results.select_cell(1, 1)
        view = app.query_one(AthenaResultsView)
        if overlay == "inspect":
            view.action_inspect_cell()
        else:
            view.action_filter_results()
        vm.results.clear()
        await pilot.pause()
        assert len(app.screen_stack) == 1
        assert vm.results.selection is None


@pytest.mark.parametrize("control", ["#athena-results-table", "#athena-results-footer"])
async def test_result_refresh_preflights_partial_control_unmount(control):
    vm, _ = await _loaded_result_controls_vm()
    app = _AthenaApp(vm)
    async with app.run_test() as pilot:
        await pilot.pause()
        view = app.query_one(AthenaResultsView)
        await view.query_one(control).remove()
        assert view.is_attached
        assert view.is_running
        view._refresh()


@pytest.mark.parametrize("control", ["#athena-results-table", "#athena-results-footer"])
async def test_result_refresh_recovers_after_real_control_replacement(control):
    vm, _ = await _loaded_result_controls_vm()
    app = _AthenaApp(vm)
    async with app.run_test() as pilot:
        await pilot.pause()
        vm.results.select_cell(1, 1)
        await pilot.pause()
        view = app.query_one(AthenaResultsView)
        old = view.query_one(control)
        parent = old.parent
        await old.remove()
        assert parent is not None
        if isinstance(old, DataTable):
            replacement = type(old)(id=old.id, cursor_type="cell")
        else:
            replacement = type(old)("", id=old.id)
        await parent.mount(replacement)
        await pilot.pause()
        view._refresh()
        table = view.query_one(DataTable)
        assert table.row_count == 3
        assert (table.cursor_row, table.cursor_column) == (1, 1)
        assert vm.results.selection == (1, 1)
        assert (
            view.query_one("#athena-results-footer", Static).content
            == "3 visible / 3 loaded · local · more available"
        )


@pytest.mark.parametrize("gesture", ["enter", "click"])
@pytest.mark.parametrize("value", [None, "", "[bold]é[/bold]\noriginal string"])
async def test_explicit_current_sole_result_cell_admits_original_selection(gesture, value):
    client = PageClient()
    calls = []

    async def results(execution_id, *, start_token=None):
        calls.append((execution_id, start_token))
        return ResultPage((ResultColumn("one", "varchar", "NULLABLE"),), ((value,),), None)

    client.get_results_page = results
    vm, _ = _build_vm(client)
    await vm.setup()
    await vm.results.load("sole")
    await vm.select_view("results")
    app = _AthenaApp(vm)
    copies = []
    app.copy_value = lambda payload, label: copies.append(payload)
    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.pause()
        table = app.query_one(DataTable)
        view = app.query_one(AthenaResultsView)
        await focus_and_settle(table)
        assert table.row_count == len(table.columns) == 1
        assert vm.results.selection is None
        view.action_copy_cell()
        view.action_inspect_cell()
        assert copies == []
        assert len(app.screen_stack) == 1
        await pilot.press("right", "down", "left", "up")
        assert vm.results.selection is None
        if gesture == "enter":
            await pilot.press("enter")
        else:
            assert await pilot.click(table, offset=(2, 2))
        await pilot.pause()
        assert vm.results.selection == (0, 0)
        view.action_copy_cell()
        view.action_copy_row()
        assert copies == [
            json.dumps(value, ensure_ascii=False),
            json.dumps([value], ensure_ascii=False, separators=(",", ":")),
        ]
        view.action_inspect_cell()
        await pilot.pause()
        assert app.screen.query_one(TextArea).text == ("" if value is None else value)
        await pilot.press("escape")
        await pilot.pause()
        assert table.has_focus
        assert (table.cursor_row, table.cursor_column) == (0, 0)
        assert vm.results.selection == (0, 0)
        assert calls == [("sole", None)]


@pytest.mark.parametrize("gesture", ["enter", "click"])
async def test_explicit_first_result_cell_after_filter_and_reset_uses_original_ordinal(gesture):
    vm, calls = await _loaded_result_controls_vm()
    app = _AthenaApp(vm)
    copies = []
    app.copy_value = lambda value, label: copies.append(value)
    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.pause()
        table = app.query_one(DataTable)
        view = app.query_one(AthenaResultsView)
        await focus_and_settle(table)
        assert vm.results.selection is None
        for filter_text, ordinal, expected in (("", 0, "null"), ("tail", 2, '"2"')):
            vm.results.set_filter("no matching cells")
            await pilot.pause()
            assert table.row_count == 0
            await pilot.press("enter")
            assert vm.results.selection is None
            if filter_text:
                vm.results.set_filter(filter_text)
            else:
                view.action_reset_results()
            await pilot.pause()
            assert vm.results.selection is None
            assert (table.cursor_row, table.cursor_column) == (0, 0)
            if gesture == "enter":
                await pilot.press("enter")
            else:
                assert await pilot.click(table, offset=(2, 2))
            await pilot.pause()
            assert vm.results.selection == (ordinal, 0)
            view.action_copy_cell()
            assert copies[-1] == expected
            view.action_inspect_cell()
            await pilot.pause()
            assert app.screen.query_one(TextArea).text == ("" if ordinal == 0 else "2")
            await pilot.press("escape")
            await pilot.pause()
            assert table.has_focus
            assert vm.results.selection == (ordinal, 0)
            assert (table.cursor_row, table.cursor_column) == (0, 0)
        assert calls == [("controls", None)]


@pytest.mark.parametrize("retirement", ["revision", "generation", "table", "footer"])
async def test_stamped_selected_result_event_cannot_reselect_retired_data(retirement, monkeypatch):
    vm, _ = await _loaded_result_controls_vm()
    app = _AthenaApp(vm)
    selected = []
    post = DataTable.post_message

    def capture(table, message):
        if isinstance(message, DataTable.CellSelected):
            selected.append(message)
        return post(table, message)

    monkeypatch.setattr(DataTable, "post_message", capture)
    async with app.run_test() as pilot:
        await pilot.pause()
        table = app.query_one(DataTable)
        view = app.query_one(AthenaResultsView)
        await focus_and_settle(table)
        await pilot.press("enter")
        await pilot.pause()
        assert vm.results.selection == (0, 0)
        stale = selected[-1]
        assert stale.value is None
        assert stale.athena_revision == table.projection_revision
        assert stale.athena_generation == vm.results.projection_generation
        if retirement == "revision":
            vm.results.set_filter("missing")
            await pilot.pause()
            vm.results.reset_projection()
            await pilot.pause()
        elif retirement == "generation":
            await vm.results.load("controls")
            await pilot.pause()
        else:
            # Retire the control without asking the page to focus an incomplete ring.
            await focus_and_settle(app.query_one("#nav-menu", NavMenu))
            await view.query_one(
                "#athena-results-table" if retirement == "table" else "#athena-results-footer"
            ).remove()
            vm.results.set_filter("missing")
        assert vm.results.selection is None
        await view._on_message(stale)
        assert vm.results.selection is None


@pytest.mark.parametrize("operation", ["enter", "apply", "clear", "cancel"])
async def test_private_result_filter_events_are_scrubbed_before_post_and_event_logging(
    operation, monkeypatch
):
    import textual.message_pump as message_pump
    from textual.widgets import Input

    from aws_tui.ui.widgets.modal_button import ModalButton

    marker = "FILTER_PRIVATE_FINAL_FIX_MARKER"
    outgoing = []
    logged = []
    kinds = (Input.Changed, Input.Submitted, Input.Blurred)
    post = Input.post_message

    def capture(field, message):
        if isinstance(message, kinds) and field.id == "athena-result-filter":
            outgoing.append((type(message), message.value, repr(message)))
        return post(field, message)

    class EventLog:
        @property
        def event(self):
            return self

        def verbosity(self, verbose):
            return self

        def __call__(self, *args, **kwargs):
            for message in args:
                if isinstance(message, kinds) and message.input.id == "athena-result-filter":
                    logged.append((type(message), message.value, repr(message)))

    monkeypatch.setattr(Input, "post_message", capture)
    monkeypatch.setattr(message_pump, "log", EventLog())
    vm, calls = await _loaded_result_controls_vm()
    vm.results.set_filter("tail")
    app = _AthenaApp(vm)
    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.pause()
        table = app.query_one(DataTable)
        view = app.query_one(AthenaResultsView)
        await focus_and_settle(table)
        view.action_filter_results()
        await pilot.pause()
        field = app.screen.query_one(Input)
        assert field.value == "tail"
        await pilot.press("home", "shift+end", "backspace", *marker)
        assert field.value == marker
        if operation == "enter":
            await pilot.press("enter")
        elif operation == "cancel":
            await pilot.press("escape")
        else:
            button = next(b for b in app.screen.query(ModalButton) if b.button_id == operation)
            await focus_and_settle(button)
            await pilot.press("enter")
        await pilot.pause()
        expected = {"enter": marker, "apply": marker, "clear": "", "cancel": "tail"}[operation]
        assert vm.results.filter_text == expected
        assert table.has_focus
        view.action_filter_results()
        await pilot.pause()
        assert app.screen.query_one(Input).value == expected
        await pilot.press("escape")
        await pilot.pause()
        assert vm.results.filter_text == expected
        assert {kind for kind, _, _ in outgoing} >= {Input.Changed, Input.Blurred}
        assert {kind for kind, _, _ in logged} >= {Input.Changed, Input.Blurred}
        if operation == "enter":
            assert Input.Submitted in {kind for kind, _, _ in outgoing}
            assert Input.Submitted in {kind for kind, _, _ in logged}
        assert all(
            value == "" and marker not in representation for _, value, representation in outgoing
        )
        assert all(
            value == "" and marker not in representation for _, value, representation in logged
        )
        assert calls == [("controls", None)]
