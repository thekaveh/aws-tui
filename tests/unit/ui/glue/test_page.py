from __future__ import annotations

import pytest
from textual.app import App, ComposeResult
from textual.widgets import OptionList, Select
from vmx import NULL_DISPATCHER, MessageHub
from vmx.messages.protocols import Message

from aws_tui.infra.connection_resolver import Connection
from aws_tui.ui.widgets.glue.catalog_view import GlueCatalogView
from aws_tui.ui.widgets.glue.crawlers_view import GlueCrawlersView
from aws_tui.ui.widgets.glue.jobs_view import GlueJobsView
from aws_tui.ui.widgets.glue.page import GluePage
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
    def __init__(self, vm: GluePageVM) -> None:
        super().__init__()
        self._vm = vm

    def compose(self) -> ComposeResult:
        yield GluePage(self._vm, hub=self._vm.hub)


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
async def test_number_actions_switch_views_and_load_them_lazily() -> None:
    vm, fake = _build_vm()
    await vm.setup()
    app = _GlueApp(vm)

    async with app.run_test() as pilot:
        await pilot.pause()
        page = app.query_one(GluePage)
        page.query_one(GlueCatalogView).query_one(OptionList).focus()
        await pilot.press("2")
        await pilot.pause()
        assert vm.active_view == "jobs"
        assert page.query_one(GlueJobsView).display
        assert fake.job_tokens == [None]

        await pilot.press("3")
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
async def test_refresh_action_refreshes_only_the_active_view() -> None:
    vm, fake = _build_vm()
    await vm.setup()
    app = _GlueApp(vm)

    async with app.run_test() as pilot:
        await pilot.pause()
        page = app.query_one(GluePage)
        before = len(fake.database_tokens)
        page.query_one(GlueCatalogView).query_one(OptionList).focus()
        await pilot.press("r")
        await pilot.pause()

        assert len(fake.database_tokens) == before + 1
        assert fake.job_tokens == []
        assert fake.crawler_requests == []


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
