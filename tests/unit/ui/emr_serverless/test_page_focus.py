from __future__ import annotations

import asyncio

import pytest
from textual.containers import Horizontal
from textual.widgets import OptionList

from aws_tui.ui.widgets.context_picker import ContextPicker
from aws_tui.ui.widgets.emr_serverless.application_picker import ApplicationPicker
from aws_tui.ui.widgets.emr_serverless.job_run_detail_pane import JobRunDetailPane
from aws_tui.ui.widgets.emr_serverless.job_runs_pane import JobRunsPane
from aws_tui.ui.widgets.emr_serverless.page import EmrServerlessPage
from aws_tui.ui.widgets.service_source_header import ServiceSourceHeader
from tests.snapshot.apps.emr import EmrPageApp, EmrPageOpenSourcePickerApp


@pytest.mark.asyncio
async def test_tab_cycle_closes_departed_application_picker() -> None:
    app = EmrPageApp(theme="carbon")

    async with app.run_test() as pilot:
        await pilot.pause()
        page = app.query_one(EmrServerlessPage)
        picker = app.query_one(ApplicationPicker)
        picker.toggle_open()
        await pilot.pause()
        assert picker.has_class("-open")
        assert app.focused is not None
        assert isinstance(app.focused, OptionList)

        page.action_cycle_panes_forward()
        await pilot.pause()

        assert not picker.has_class("-open")
        assert app.query_one("#emr-runs-pane").has_focus


@pytest.mark.asyncio
async def test_application_picker_overlay_preserves_page_geometry_through_escape() -> None:
    app = EmrPageApp(theme="carbon")

    async with app.run_test() as pilot:
        await pilot.pause()
        picker = app.query_one(ApplicationPicker)
        widgets = (
            picker,
            app.query_one("#emr-app-box"),
            app.query_one(".emr-context-row", Horizontal),
            app.query_one(ServiceSourceHeader),
            app.query_one(JobRunsPane),
            app.query_one(JobRunDetailPane),
            app.query_one("#content-host"),
        )
        closed_regions = tuple(widget.region for widget in widgets)

        picker.toggle_open()
        await pilot.pause()

        assert tuple(widget.region for widget in widgets) == closed_regions

        await pilot.press("escape")
        await pilot.pause()

        assert not picker.is_open
        assert tuple(widget.region for widget in widgets) == closed_regions


@pytest.mark.asyncio
@pytest.mark.parametrize("size", [(100, 30), (80, 24)], ids=("wide", "narrow"))
async def test_source_picker_overlay_preserves_every_page_region(
    size: tuple[int, int],
) -> None:
    app = EmrPageApp(theme="carbon")

    async with app.run_test(size=size) as pilot:
        await pilot.pause()
        source_picker = app.query_one("#emr-source-header-picker", ContextPicker)
        widgets = (
            source_picker,
            app.query_one(ApplicationPicker),
            app.query_one("#emr-app-box"),
            app.query_one(".emr-context-row", Horizontal),
            app.query_one(ServiceSourceHeader),
            app.query_one(JobRunsPane),
            app.query_one(JobRunDetailPane),
            app.query_one("#emr-logs-pane"),
            app.query_one("#content-host"),
        )
        closed_regions = tuple(widget.region for widget in widgets)

        source_picker.open()
        await pilot.pause()
        assert source_picker.is_open
        assert app.focused is source_picker.query_one(OptionList)
        assert tuple(widget.region for widget in widgets) == closed_regions

        source_picker.close()
        await pilot.pause()
        assert not source_picker.is_open
        assert tuple(widget.region for widget in widgets) == closed_regions


@pytest.mark.asyncio
async def test_application_picker_removal_closes_without_deferred_refocus() -> None:
    app = EmrPageApp(theme="carbon")

    async with app.run_test() as pilot:
        await pilot.pause()
        picker = app.query_one(ApplicationPicker)
        refocus_calls = 0

        def record_refocus() -> None:
            nonlocal refocus_calls
            refocus_calls += 1

        picker._refocus = record_refocus  # type: ignore[method-assign]
        picker.toggle_open()
        await pilot.pause()
        assert picker.is_open

        await picker.remove()
        await pilot.pause()

        assert not picker.is_open
        assert not picker.is_running
        assert refocus_calls == 0


@pytest.mark.asyncio
async def test_application_picker_overlay_closes_when_focus_leaves() -> None:
    app = EmrPageApp(theme="carbon")
    async with app.run_test() as pilot:
        picker = app.query_one(ApplicationPicker)
        picker.toggle_open()
        async with asyncio.timeout(1.0):
            while not isinstance(app.focused, OptionList):
                await pilot.pause()

        app.query_one(JobRunsPane).focus()
        await pilot.pause()

        assert not picker.is_open


@pytest.mark.asyncio
async def test_keyboard_opening_application_picker_closes_source_picker() -> None:
    app = EmrPageOpenSourcePickerApp(theme="carbon")

    async with app.run_test() as pilot:
        await pilot.pause()
        page = app.query_one(EmrServerlessPage)
        source_picker = app.query_one("#emr-source-header-picker", ContextPicker)
        application_picker = app.query_one(ApplicationPicker)
        assert source_picker.is_open
        app.set_focus(source_picker.query_one(OptionList))
        await pilot.pause()
        assert app.focused is source_picker.query_one(OptionList)

        page.action_cycle_panes_forward()
        await pilot.pause()
        assert app.focused is application_picker
        assert not source_picker.is_open

        await pilot.press("enter")
        async with asyncio.timeout(1.0):
            while source_picker.is_open or not application_picker.is_open:
                await asyncio.sleep(0.01)

        assert not source_picker.is_open
        assert application_picker.is_open


@pytest.mark.asyncio
async def test_programmatic_picker_opens_are_exclusive_in_both_directions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = EmrPageApp(theme="carbon")

    async with app.run_test() as pilot:
        await pilot.pause()
        source = app.query_one("#emr-source-header-picker", ContextPicker)
        application = app.query_one(ApplicationPicker)
        monkeypatch.setattr(source, "call_after_refresh", lambda _callback: None)
        monkeypatch.setattr(application, "call_after_refresh", lambda _callback: None)

        application.toggle_open()
        await pilot.pause()
        source.open()
        await pilot.pause()
        assert source.is_open
        assert not application.is_open

        application.toggle_open()
        await pilot.pause()
        assert application.is_open
        assert not source.is_open


@pytest.mark.asyncio
async def test_page_removal_closes_every_picker_without_refocus() -> None:
    app = EmrPageApp(theme="carbon")

    async with app.run_test() as pilot:
        await pilot.pause()
        page = app.query_one(EmrServerlessPage)
        source = app.query_one("#emr-source-header-picker", ContextPicker)
        application = app.query_one(ApplicationPicker)
        source.open()
        await pilot.pause()

        await page.remove()
        await pilot.pause()

        assert not source.is_open
        assert not application.is_open


@pytest.mark.asyncio
async def test_shift_tab_cycle_closes_departed_application_picker() -> None:
    app = EmrPageApp(theme="carbon")

    async with app.run_test() as pilot:
        await pilot.pause()
        page = app.query_one(EmrServerlessPage)
        picker = app.query_one(ApplicationPicker)
        picker.toggle_open()
        await pilot.pause()
        assert picker.has_class("-open")

        page.action_cycle_panes_back()
        await pilot.pause()

        assert not picker.has_class("-open")
        assert app.query_one("#emr-source-header").has_focus


@pytest.mark.asyncio
async def test_tab_cycle_closes_departed_source_picker() -> None:
    app = EmrPageOpenSourcePickerApp(theme="carbon")

    async with app.run_test() as pilot:
        await pilot.pause()
        page = app.query_one(EmrServerlessPage)
        picker = app.query_one("#emr-source-header-picker", ContextPicker)
        assert picker.is_open
        app.set_focus(picker.query_one(OptionList))
        await pilot.pause()

        page.action_cycle_panes_forward()
        await pilot.pause()

        assert not picker.is_open
        assert app.query_one(ApplicationPicker).has_focus
