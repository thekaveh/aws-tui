from __future__ import annotations

import pytest
from textual.app import App, ComposeResult
from textual.containers import Horizontal, VerticalScroll
from textual.css.query import NoMatches
from textual.widgets import OptionList, Static
from vmx import NULL_DISPATCHER, MessageHub
from vmx.messages.protocols import Message

from aws_tui.infra.connection_resolver import Connection
from aws_tui.infra.keymap_store import KeymapStore
from aws_tui.infra.theme_store import ThemeStore
from aws_tui.ui.widgets.context_picker import ContextPicker
from aws_tui.ui.widgets.glue.catalog_view import GlueCatalogView
from aws_tui.ui.widgets.glue.crawlers_view import GlueCrawlersView
from aws_tui.ui.widgets.glue.detail_rows import DetailRows, ResourceListPane
from aws_tui.ui.widgets.glue.jobs_view import GlueJobsView
from aws_tui.ui.widgets.glue.page import GluePage
from aws_tui.ui.widgets.nav_menu import NavMenu
from aws_tui.ui.widgets.service_source_header import ServiceSourceHeader
from aws_tui.ui.widgets.service_tab_strip import ServiceTabStrip
from aws_tui.vm.chrome.focus_coordinator_vm import FocusCoordinatorVM, FocusSlot
from aws_tui.vm.file_manager.pane_vm import PaneState
from aws_tui.vm.glue.page_vm import GluePageVM
from aws_tui.vm.nav_menu_vm import NavMenuVM
from aws_tui.vm.services_protocol import ServiceRegistry
from tests.unit.vm.glue._fake_glue import InMemoryGlue, seeded_glue


def _build_vm(fake: InMemoryGlue | None = None) -> tuple[GluePageVM, InMemoryGlue]:
    client = fake or seeded_glue()
    hub: MessageHub[Message] = MessageHub()
    vm = GluePageVM(
        client=client,
        connection=Connection(
            name="analytics-dev",
            kind="aws",
            region="us-east-1",
            source="test",
            profile="analytics-dev",
        ),
        hub=hub,
        dispatcher=NULL_DISPATCHER,
    )
    vm.construct()
    return vm, client


class _GlueApp(App[None]):
    CSS = """
    Screen { layout: horizontal; }
    GluePage { width: 1fr; }
    #nav-menu { width: 1; min-width: 1; }
    """

    def __init__(self, vm: GluePageVM, *, keymap: KeymapStore | None = None) -> None:
        super().__init__()
        self._vm = vm
        self._keymap = keymap
        self.focus_coordinator = FocusCoordinatorVM(
            hub=vm.hub,
            dispatcher=NULL_DISPATCHER,
        )
        self.focus_coordinator.construct()
        self.nav_vm = NavMenuVM(
            registry=ServiceRegistry(),
            hub=vm.hub,
            dispatcher=NULL_DISPATCHER,
        )
        self.nav_vm.construct()

    def compose(self) -> ComposeResult:
        if self._keymap is None:
            yield GluePage(
                self._vm,
                hub=self._vm.hub,
                focus_coordinator=self.focus_coordinator,
            )
        else:
            yield GluePage(
                self._vm,
                hub=self._vm.hub,
                keymap=self._keymap,
                focus_coordinator=self.focus_coordinator,
            )
        yield NavMenu(
            vm=self.nav_vm,
            hub=self._vm.hub,
            focus_coordinator=self.focus_coordinator,
            id="nav-menu",
        )

    def on_unmount(self) -> None:
        self.focus_coordinator.dispose()
        self.nav_vm.dispose()


def _focus_target_ids(page: GluePage) -> tuple[str, ...]:
    return tuple(widget.id or "" for _slot, widget in page._focus_targets())


def _cycle_target_ids(
    app: _GlueApp,
    page: GluePage,
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
    ("view", "expected_ids"),
    [
        (
            "catalog",
            (
                "glue-source-header",
                "glue-view-tabs",
                "glue-databases-pane-options",
                "glue-tables-pane-options",
                "glue-table-detail-pane-scroll",
                "nav-menu",
            ),
        ),
        (
            "jobs",
            (
                "glue-source-header",
                "glue-run-state-filter",
                "glue-view-tabs",
                "glue-jobs-pane-options",
                "glue-runs-pane-options",
                "glue-job-detail-pane-scroll",
                "nav-menu",
            ),
        ),
        (
            "crawlers",
            (
                "glue-source-header",
                "glue-crawler-state-filter",
                "glue-view-tabs",
                "glue-crawlers-pane-options",
                "glue-crawler-detail-pane-scroll",
                "nav-menu",
            ),
        ),
    ],
)
async def test_glue_views_have_complete_deterministic_focus_rings(
    view: str,
    expected_ids: tuple[str, ...],
) -> None:
    vm, _fake = _build_vm()
    await vm.setup()
    await vm.select_view(view)  # type: ignore[arg-type]
    app = _GlueApp(vm)

    async with app.run_test() as pilot:
        await pilot.pause()
        page = app.query_one(GluePage)
        assert _focus_target_ids(page) == expected_ids
        assert _cycle_target_ids(app, page, reverse=False) == expected_ids
        assert _cycle_target_ids(app, page, reverse=True) == (
            *reversed(expected_ids[:-1]),
            expected_ids[-1],
        )
        active_id = f"glue-{view}-view"
        assert all(
            active_id in {ancestor.id for ancestor in widget.ancestors_with_self}
            or slot
            in {
                FocusSlot.GLUE_SOURCE,
                FocusSlot.GLUE_FILTER,
                FocusSlot.GLUE_TABS,
                FocusSlot.NAV_MENU,
            }
            for slot, widget in page._focus_targets()
        )


@pytest.mark.asyncio
async def test_glue_ring_projects_to_nav_and_direct_focus_resumes_from_typed_slot() -> None:
    vm, _fake = _build_vm()
    await vm.setup()
    app = _GlueApp(vm)

    async with app.run_test() as pilot:
        await pilot.pause()
        page = app.query_one(GluePage)
        tables = app.query_one("#glue-tables-pane-options", OptionList)
        tables.focus()
        await pilot.pause()

        assert app.focus_coordinator.focused_slot is FocusSlot.GLUE_SECONDARY
        page.cycle_focus(reverse=False)
        await pilot.pause()
        assert app.focused is not None
        assert app.focused.id == "glue-table-detail-pane-scroll"

        app.query_one("#glue-source-header").focus()
        await pilot.pause()
        page.cycle_focus(reverse=True)
        await pilot.pause()
        assert app.query_one("#nav-menu", NavMenu).has_focus
        assert app.focus_coordinator.focused_slot is FocusSlot.NAV_MENU


@pytest.mark.asyncio
async def test_glue_refresh_falls_back_to_the_nearest_available_slot() -> None:
    vm, _fake = _build_vm()
    await vm.setup()
    await vm.select_view("jobs")
    app = _GlueApp(vm)

    async with app.run_test() as pilot:
        await pilot.pause()
        runs = app.query_one("#glue-runs-pane-options", OptionList)
        app.focus_coordinator.set_focused_slot(FocusSlot.GLUE_SECONDARY)
        app.set_focus(runs)
        await pilot.pause()

        await app.query_one(GluePage).action_select_view("crawlers")
        await pilot.pause()

        assert app.focus_coordinator.focused_slot is FocusSlot.GLUE_DETAIL
        assert app.focused is not None
        assert app.focused.id == "glue-crawler-detail-pane-scroll"


@pytest.mark.asyncio
async def test_glue_view_switch_reprojects_focus_before_action_returns() -> None:
    vm, _fake = _build_vm()
    await vm.setup()
    await vm.select_view("jobs")
    app = _GlueApp(vm)

    async with app.run_test() as pilot:
        await pilot.pause()
        runs = app.query_one("#glue-runs-pane-options", OptionList)
        runs.focus()
        await pilot.pause()
        page = app.query_one(GluePage)

        await page.action_select_view("crawlers")

        assert app.focus_coordinator.focused_slot is FocusSlot.GLUE_DETAIL
        assert app.focused is not None
        assert app.focused.id == "glue-crawler-detail-pane-scroll"


@pytest.mark.asyncio
async def test_glue_page_composes_source_tabs_and_three_views() -> None:
    vm, _fake = _build_vm()
    await vm.setup()
    app = _GlueApp(vm)

    async with app.run_test() as pilot:
        await pilot.pause()
        page = app.query_one(GluePage)
        assert page.query_one("#glue-source-header")
        assert page.query_one("#glue-view-tabs")
        assert page.query_one(GlueCatalogView)
        assert page.query_one(GlueJobsView)
        assert page.query_one(GlueCrawlersView)
        assert page.query_one(GlueCatalogView).display
        assert not page.query_one(GlueJobsView).display
        assert not page.query_one(GlueCrawlersView).display


@pytest.mark.asyncio
async def test_glue_context_controls_keep_source_border_inside_themed_header() -> None:
    vm, _fake = _build_vm()
    await vm.setup()

    class _BuiltinThemeGlueApp(_GlueApp):
        CSS = _GlueApp.CSS + "\n" + ThemeStore().load_builtin("carbon")

    app = _BuiltinThemeGlueApp(vm)

    async with app.run_test() as pilot:
        await pilot.pause()
        page = app.query_one(GluePage)
        row = page.query_one("#glue-context-row", Horizontal)
        header = page.query_one("#glue-source-header", ServiceSourceHeader)
        source = page.query_one("#glue-source-header-picker", ContextPicker)

        assert row.border_title is None
        assert row.styles.border_top[0] in {"", "none"}
        assert header.styles.border_left[0] in {"", "none"}
        assert source.border_title == "AWS source"
        assert source.styles.border_top[0] in {"solid", "heavy"}
        assert source.styles.border_left[0] in {"solid", "heavy"}
        with pytest.raises(NoMatches):
            page.query_one("#glue-context-pane")


@pytest.mark.asyncio
async def test_open_glue_filter_stays_inside_layout_flow_at_narrow_width() -> None:
    vm, _fake = _build_vm()
    await vm.setup()
    await vm.select_view("jobs")
    app = _GlueApp(vm)

    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.pause()
        page = app.query_one(GluePage)
        row = page.query_one("#glue-context-row", Horizontal)
        source = page.query_one("#glue-source-header")
        run_filter = page.query_one("#glue-run-state-filter", ContextPicker)
        tabs = page.query_one("#glue-view-tabs", ServiceTabStrip)
        view_host = page.query_one("#glue-view-host")

        run_filter.open()
        await pilot.pause()

        assert source.region.right <= run_filter.region.x
        assert row.region.bottom <= tabs.region.y
        assert tabs.region.bottom <= view_host.region.y


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("keymap", "labels"),
    [
        (None, ("1 catalog", "2 jobs", "3 crawlers")),
        (
            KeymapStore(
                overlay={
                    "glue.catalog": "7",
                    "glue.jobs": "8",
                    "glue.crawlers": "9",
                }
            ),
            ("7 catalog", "8 jobs", "9 crawlers"),
        ),
    ],
)
async def test_glue_tab_labels_resolve_active_keymap(
    keymap: KeymapStore | None,
    labels: tuple[str, str, str],
) -> None:
    vm, _fake = _build_vm()
    await vm.setup()
    app = _GlueApp(vm, keymap=keymap)

    async with app.run_test() as pilot:
        await pilot.pause()
        assert (
            tuple(
                str(app.query_one(f"#glue-tab-{view}", Static).render())
                for view in ("catalog", "jobs", "crawlers")
            )
            == labels
        )


@pytest.mark.asyncio
async def test_view_actions_switch_views_and_load_them_lazily() -> None:
    vm, fake = _build_vm()
    await vm.setup()
    app = _GlueApp(vm)

    async with app.run_test() as pilot:
        await pilot.pause()
        page = app.query_one(GluePage)
        page.query_one(GlueCatalogView).query_one(OptionList).focus()
        await page.action_select_view("jobs")
        await pilot.pause()
        assert vm.active_view == "jobs"
        assert page.query_one(GlueJobsView).display
        assert fake.job_tokens == [None]

        await page.action_select_view("crawlers")
        await pilot.pause()
        assert vm.active_view == "crawlers"
        assert page.query_one(GlueCrawlersView).display
        assert fake.crawler_requests == [(None, None)]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("view", "action_name", "picker_id"),
    [
        ("jobs", "action_choose_run_state", "glue-run-state-filter"),
        ("crawlers", "action_choose_crawler_state", "glue-crawler-state-filter"),
    ],
)
async def test_named_filter_action_focuses_and_opens_picker(
    view: str,
    action_name: str,
    picker_id: str,
) -> None:
    vm, _fake = _build_vm()
    await vm.setup()
    await vm.select_view(view)  # type: ignore[arg-type]
    app = _GlueApp(vm)

    async with app.run_test() as pilot:
        await pilot.pause()
        page = app.query_one(GluePage)
        await getattr(page, action_name)()
        await pilot.pause()

        picker = app.query_one(f"#{picker_id}", ContextPicker)
        assert picker.is_open
        assert app.focus_coordinator.focused_slot is FocusSlot.GLUE_FILTER


@pytest.mark.asyncio
async def test_named_filter_action_closes_hidden_filter_from_previous_view() -> None:
    vm, _fake = _build_vm()
    await vm.setup()
    app = _GlueApp(vm)

    async with app.run_test() as pilot:
        await pilot.pause()
        page = app.query_one(GluePage)

        await page.action_choose_run_state()
        await pilot.pause()
        run_filter = app.query_one("#glue-run-state-filter", ContextPicker)
        assert run_filter.is_open

        await page.action_choose_crawler_state()
        await pilot.pause(0.05)
        crawler_filter = app.query_one("#glue-crawler-state-filter", ContextPicker)
        assert not run_filter.is_open
        assert crawler_filter.is_open


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("reverse", "expected_id"),
    [(False, "glue-view-tabs"), (True, "glue-source-header")],
)
async def test_tab_cycle_closes_departed_filter_picker(
    reverse: bool,
    expected_id: str,
) -> None:
    vm, _fake = _build_vm()
    await vm.setup()
    await vm.select_view("jobs")
    app = _GlueApp(vm)

    async with app.run_test() as pilot:
        await pilot.pause()
        page = app.query_one(GluePage)
        await page.action_choose_run_state()
        await pilot.pause()
        picker = app.query_one("#glue-run-state-filter", ContextPicker)
        assert picker.is_open

        page.cycle_focus(reverse=reverse)
        await pilot.pause()

        assert not picker.is_open
        assert app.focused is not None
        assert app.focused.id == expected_id


@pytest.mark.asyncio
async def test_clicking_tab_switches_the_active_view() -> None:
    vm, _fake = _build_vm()
    await vm.setup()
    app = _GlueApp(vm)

    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.click("#glue-tab-jobs")
        await pilot.pause()

        assert vm.active_view == "jobs"
        assert app.query_one(GlueJobsView).display


@pytest.mark.asyncio
@pytest.mark.parametrize("key", ["enter", "space"])
async def test_focused_tab_activates_with_keyboard(key: str) -> None:
    vm, _fake = _build_vm()
    await vm.setup()
    app = _GlueApp(vm)

    async with app.run_test() as pilot:
        await pilot.pause()
        page = app.query_one(GluePage)
        page._maybe_focus_active()  # type: ignore[attr-defined]
        await pilot.pause(0.05)
        tab = app.query_one("#glue-view-tabs", ServiceTabStrip)
        tab.focus()
        await pilot.pause()
        assert tab.has_focus
        tab._highlighted = "jobs"

        await pilot.press(key)
        await pilot.pause()

        assert vm.active_view == "jobs"
        assert app.query_one(GlueJobsView).display


@pytest.mark.asyncio
async def test_deferred_focus_projection_ignores_empty_teardown_ring(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    vm, _fake = _build_vm()
    await vm.setup()
    app = _GlueApp(vm)

    async with app.run_test() as pilot:
        await pilot.pause()
        page = app.query_one(GluePage)
        monkeypatch.setattr(page, "_focus_targets", lambda: ())

        page._maybe_focus_active(FocusSlot.NAV_MENU)  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_refresh_action_refreshes_only_the_active_view() -> None:
    vm, fake = _build_vm()
    await vm.setup()
    app = _GlueApp(vm)

    async with app.run_test() as pilot:
        await pilot.pause()
        page = app.query_one(GluePage)
        before = len(fake.database_tokens)
        page.query_one(GlueCatalogView).query_one(OptionList).focus()
        await page.action_refresh_active()
        await pilot.pause()

        assert len(fake.database_tokens) == before + 1
        assert fake.job_tokens == []
        assert fake.crawler_requests == []


@pytest.mark.asyncio
async def test_list_placeholder_preserves_semantic_state_classes() -> None:
    vm, _fake = _build_vm()
    await vm.setup()
    app = _GlueApp(vm)

    async with app.run_test() as pilot:
        await pilot.pause()
        pane = app.query_one("#glue-databases-pane", ResourceListPane)
        options = pane.option_list

        pane.replace(
            (),
            selected_id=None,
            state=PaneState.FORBIDDEN,
            error_text="permission denied",
            has_more=False,
        )
        assert options.has_class("-warning")
        assert not options.has_class("-error")

        pane.replace(
            (),
            selected_id=None,
            state=PaneState.ERROR,
            error_text="request failed",
            has_more=False,
        )
        assert options.has_class("-error")
        assert not options.has_class("-warning")


@pytest.mark.asyncio
async def test_detail_pane_has_one_useful_scroll_focus_target() -> None:
    vm, _fake = _build_vm()
    await vm.setup()
    app = _GlueApp(vm)

    async with app.run_test() as pilot:
        await pilot.pause()
        detail = app.query_one("#glue-table-detail-pane", DetailRows)
        scroll = detail.query_one(VerticalScroll)
        detail_targets = [widget for widget in app.screen.focus_chain if widget in (detail, scroll)]

        assert detail_targets == [scroll]


@pytest.mark.asyncio
async def test_aws_controlled_text_is_rendered_without_markup() -> None:
    fake = InMemoryGlue()
    fake.add_table("analytics[prod]", "events[/bold]")
    vm, _fake = _build_vm(fake)
    await vm.setup()
    app = _GlueApp(vm)

    async with app.run_test() as pilot:
        await pilot.pause()
        lists = list(app.query(".glue-option-list").results(OptionList))
        assert lists
        assert all(not option_list._markup for option_list in lists)  # type: ignore[attr-defined]
        rendered = app.export_screenshot()
        assert "analytics[prod]" in rendered
        assert "events[/bold]" in rendered


@pytest.mark.asyncio
async def test_resource_lists_keep_each_option_on_one_visual_row_at_narrow_width() -> None:
    vm, _fake = _build_vm()
    await vm.setup()
    app = _GlueApp(vm)

    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.pause()
        options = app.query_one("#glue-tables-pane-options", OptionList)
        assert str(options.styles.text_wrap) == "nowrap"
        assert str(options.styles.text_overflow) == "ellipsis"


@pytest.mark.asyncio
async def test_jobs_and_crawlers_use_context_picker_filters() -> None:
    vm, fake = _build_vm()
    await vm.setup()
    app = _GlueApp(vm)

    async with app.run_test() as pilot:
        await pilot.pause()
        page = app.query_one(GluePage)
        await page.action_select_view("jobs")
        await pilot.pause()
        run_filter = page.query_one("#glue-run-state-filter", ContextPicker)
        run_filter._commit("RUNNING")  # type: ignore[attr-defined]
        await pilot.pause()
        assert vm.jobs.run_state_filter == frozenset({"RUNNING"})
        assert fake.run_requests[-1] == ("nightly", None, ("RUNNING",))

        await page.action_select_view("crawlers")
        await pilot.pause()
        crawler_filter = page.query_one("#glue-crawler-state-filter", ContextPicker)
        crawler_filter._commit("RUNNING")  # type: ignore[attr-defined]
        await pilot.pause()
        assert vm.crawlers.state_filter == "RUNNING"
        assert fake.crawler_requests[-1] == (None, "RUNNING")


@pytest.mark.asyncio
async def test_run_highlight_routes_through_page_selection_boundary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    vm, _fake = _build_vm()
    await vm.setup()
    await vm.select_view("jobs")
    selected = vm.jobs.selected_run_id
    calls: list[str] = []

    def select_job_run(run_id: str) -> None:
        calls.append(run_id)

    monkeypatch.setattr(vm, "select_job_run", select_job_run)
    app = _GlueApp(vm)

    async with app.run_test() as pilot:
        await pilot.pause()
        runs = app.query_one("#glue-runs-pane", ResourceListPane)
        runs.option_list.highlighted = 1
        await pilot.pause()

    assert calls == ["jr-2"]
    assert vm.jobs.selected_run_id == selected


@pytest.mark.asyncio
async def test_run_highlight_cannot_change_selection_after_shutdown() -> None:
    vm, _fake = _build_vm()
    await vm.setup()
    await vm.select_view("jobs")
    selected = vm.jobs.selected_run_id
    app = _GlueApp(vm)

    async with app.run_test() as pilot:
        await pilot.pause()
        await vm.shutdown()
        runs = app.query_one("#glue-runs-pane", ResourceListPane)
        runs.option_list.highlighted = 1
        await pilot.pause()

    assert vm.jobs.selected_run_id == selected


@pytest.mark.asyncio
async def test_selected_job_detail_is_retained_when_the_job_has_no_runs() -> None:
    fake = InMemoryGlue()
    fake.add_job("idle-job")
    vm, _fake = _build_vm(fake)
    await vm.setup()
    app = _GlueApp(vm)

    async with app.run_test() as pilot:
        await pilot.pause()
        page = app.query_one(GluePage)
        await page.action_select_view("jobs")
        await pilot.pause()

        detail = app.query_one("#glue-job-detail-pane", DetailRows)
        runs = app.query_one("#glue-runs-pane", ResourceListPane)
        detail_text = " ".join(str(static.render()) for static in detail.query(".glue-detail-row"))
        run_placeholder = runs.option_list.get_option_at_index(0)

        assert "Job             idle-job" in detail_text
        assert "Role            GlueRole" in detail_text
        assert run_placeholder.id == "__placeholder__"
        assert str(run_placeholder.prompt) == "no runs"


@pytest.mark.asyncio
async def test_selected_job_detail_is_retained_when_filter_has_no_matching_runs() -> None:
    fake = InMemoryGlue()
    fake.add_run("nightly", "jr-1", "SUCCEEDED")
    vm, _fake = _build_vm(fake)
    await vm.setup()
    app = _GlueApp(vm)

    async with app.run_test() as pilot:
        await pilot.pause()
        page = app.query_one(GluePage)
        await page.action_select_view("jobs")
        await pilot.pause()
        jobs = page.query_one(GlueJobsView)
        page.query_one("#glue-run-state-filter", ContextPicker)._commit(  # type: ignore[attr-defined]
            "FAILED"
        )
        await pilot.pause()
        await jobs.workers.wait_for_complete()
        await pilot.pause()

        detail = app.query_one("#glue-job-detail-pane", DetailRows)
        runs = app.query_one("#glue-runs-pane", ResourceListPane)
        detail_text = " ".join(str(static.render()) for static in detail.query(".glue-detail-row"))
        run_placeholder = runs.option_list.get_option_at_index(0)

        assert "Job             nightly" in detail_text
        assert "Script          s3://scripts/nightly.py" in detail_text
        assert run_placeholder.id == "__placeholder__"
        assert str(run_placeholder.prompt) == "no runs"
