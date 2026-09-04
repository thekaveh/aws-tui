"""Behavior tests for the one-stop service tab strip."""

from __future__ import annotations

import pytest
from textual.app import App, ComposeResult
from textual.widgets import Button

from aws_tui.ui.widgets.service_tab_strip import ServiceTabStrip


class TabHost(App[None]):
    def __init__(self, tabs: ServiceTabStrip) -> None:
        super().__init__()
        self.tabs = tabs
        self.changes: list[str] = []

    def compose(self) -> ComposeResult:
        yield self.tabs
        yield Button("After", id="after-tabs")

    def on_service_tab_strip_changed(self, event: ServiceTabStrip.Changed) -> None:
        self.changes.append(event.value)


def _tabs() -> ServiceTabStrip:
    return ServiceTabStrip(
        (("catalog", "Catalog"), ("jobs", "Jobs"), ("crawlers", "Crawlers")),
        active="catalog",
        id="glue-tabs",
    )


@pytest.mark.asyncio
async def test_service_tab_strip_is_one_focus_target() -> None:
    tabs = _tabs()

    async with TabHost(tabs).run_test() as pilot:
        tabs.focus()
        await pilot.pause()

        assert pilot.app.focused is tabs
        assert all(not child.can_focus for child in tabs.children)


@pytest.mark.asyncio
async def test_service_tab_strip_arrows_change_active_tab_and_emit() -> None:
    tabs = _tabs()

    async with TabHost(tabs).run_test() as pilot:
        tabs.focus()
        await pilot.press("right")

        assert tabs.active == "jobs"
        assert pilot.app.changes == ["jobs"]


@pytest.mark.asyncio
async def test_service_tab_strip_enter_selects_the_highlighted_tab() -> None:
    tabs = _tabs()

    async with TabHost(tabs).run_test() as pilot:
        tabs.focus()
        await pilot.press("right", "enter")

        assert tabs.active == "jobs"
        assert pilot.app.changes == ["jobs", "jobs"]


@pytest.mark.asyncio
async def test_service_tab_strip_space_selects_the_highlighted_tab() -> None:
    tabs = _tabs()

    async with TabHost(tabs).run_test() as pilot:
        tabs.focus()
        tabs._highlighted = "jobs"
        await pilot.press("space")

        assert tabs.active == "catalog"
        assert pilot.app.changes == ["jobs"]


@pytest.mark.asyncio
async def test_service_tab_strip_set_active_updates_without_emitting() -> None:
    tabs = _tabs()

    async with TabHost(tabs).run_test() as pilot:
        tabs.set_active("crawlers")
        await pilot.pause()

        assert tabs.active == "crawlers"
        assert pilot.app.changes == []


@pytest.mark.asyncio
async def test_service_tab_strip_renders_one_stable_segmented_frame() -> None:
    tabs = _tabs()

    async with TabHost(tabs).run_test(size=(90, 12)) as pilot:
        await pilot.pause()
        children = list(tabs.query(".service-tab"))

        assert tabs.border_title is None
        assert tabs.styles.border_top[0] in {"solid", "heavy"}
        assert tabs.styles.border_right[0] in {"solid", "heavy"}
        assert tabs.styles.border_bottom[0] in {"solid", "heavy"}
        assert tabs.styles.border_left[0] in {"solid", "heavy"}
        assert [child.has_class("-divided") for child in children] == [False, True, True]
        assert all(child.region.height == 1 for child in children)
        assert len({child.region.width for child in children}) <= 2


@pytest.mark.asyncio
async def test_service_tab_strip_keeps_selection_and_adds_soft_focus_fill() -> None:
    tabs = _tabs()

    async with TabHost(tabs).run_test() as pilot:
        after = pilot.app.query_one("#after-tabs", Button)
        active = tabs.query_one("#service-tab-catalog")
        inactive = tabs.query_one("#service-tab-jobs")
        after.focus()
        await pilot.pause()

        resting_size = tabs.region.size
        assert tabs.border_title is None
        assert active.has_class("-active")
        assert active.styles.background == inactive.styles.background

        tabs.focus()
        await pilot.pause()

        assert active.styles.background != inactive.styles.background
        assert tabs.region.size == resting_size

        after.focus()
        await pilot.pause()

        assert active.has_class("-active")
        assert active.styles.background == inactive.styles.background
        assert tabs.region.size == resting_size
