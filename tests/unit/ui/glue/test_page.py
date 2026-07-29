from __future__ import annotations

import pytest
from textual.app import App, ComposeResult
from textual.containers import VerticalScroll
from textual.widgets import OptionList, Select, Static
from vmx import NULL_DISPATCHER, MessageHub
from vmx.messages.protocols import Message

from aws_tui.infra.connection_resolver import Connection
from aws_tui.infra.keymap_store import KeymapStore
from aws_tui.ui.widgets.glue.catalog_view import GlueCatalogView
from aws_tui.ui.widgets.glue.crawlers_view import GlueCrawlersView
from aws_tui.ui.widgets.glue.detail_rows import DetailRows, ResourceListPane
from aws_tui.ui.widgets.glue.jobs_view import GlueJobsView
from aws_tui.ui.widgets.glue.page import GluePage
from aws_tui.vm.file_manager.pane_vm import PaneState
from aws_tui.vm.glue.page_vm import GluePageVM
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
    def __init__(self, vm: GluePageVM, *, keymap: KeymapStore | None = None) -> None:
        super().__init__()
        self._vm = vm
        self._keymap = keymap

    def compose(self) -> ComposeResult:
        if self._keymap is None:
            yield GluePage(self._vm, hub=self._vm.hub)
        else:
            yield GluePage(self._vm, hub=self._vm.hub, keymap=self._keymap)


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
        app.query_one("#glue-tab-jobs").focus()

        await pilot.press(key)
        await pilot.pause()

        assert vm.active_view == "jobs"
        assert app.query_one(GlueJobsView).display


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
async def test_jobs_and_crawlers_use_select_filters() -> None:
    vm, fake = _build_vm()
    await vm.setup()
    app = _GlueApp(vm)

    async with app.run_test() as pilot:
        await pilot.pause()
        page = app.query_one(GluePage)
        await page.action_select_view("jobs")
        await pilot.pause()
        jobs = page.query_one(GlueJobsView)
        run_filter = jobs.query_one("#glue-run-state-filter", Select)
        run_filter.value = "RUNNING"
        await pilot.pause()
        assert vm.jobs.run_state_filter == frozenset({"RUNNING"})
        assert fake.run_requests[-1] == ("nightly", None, ("RUNNING",))

        await page.action_select_view("crawlers")
        await pilot.pause()
        crawlers = page.query_one(GlueCrawlersView)
        crawler_filter = crawlers.query_one("#glue-crawler-state-filter", Select)
        crawler_filter.value = "RUNNING"
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
        jobs.query_one("#glue-run-state-filter", Select).value = "FAILED"
        await pilot.pause()

        detail = app.query_one("#glue-job-detail-pane", DetailRows)
        runs = app.query_one("#glue-runs-pane", ResourceListPane)
        detail_text = " ".join(str(static.render()) for static in detail.query(".glue-detail-row"))
        run_placeholder = runs.option_list.get_option_at_index(0)

        assert "Job             nightly" in detail_text
        assert "Script          s3://scripts/nightly.py" in detail_text
        assert run_placeholder.id == "__placeholder__"
        assert str(run_placeholder.prompt) == "no runs"
