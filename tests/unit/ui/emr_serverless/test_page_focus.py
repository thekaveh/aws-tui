from __future__ import annotations

import pytest
from textual.widgets import OptionList

from aws_tui.ui.widgets.context_picker import ContextPicker
from aws_tui.ui.widgets.emr_serverless.application_picker import ApplicationPicker
from aws_tui.ui.widgets.emr_serverless.page import EmrServerlessPage
from tests.snapshot.apps.emr import EmrPageApp, EmrPageOpenSourcePickerApp


@pytest.mark.asyncio
async def test_tab_cycle_closes_departed_application_picker() -> None:
    app = EmrPageApp(theme="carbon")

    async with app.run_test() as pilot:
        await pilot.pause()
        page = app.query_one(EmrServerlessPage)
        picker = app.query_one(ApplicationPicker)
        picker.toggle_open()
        await pilot.pause(0.05)
        assert picker.has_class("-open")
        assert page.has_class("-application-picker-open")
        assert app.focused is not None
        assert isinstance(app.focused, OptionList)

        page.action_cycle_panes_forward()
        await pilot.pause()

        assert not picker.has_class("-open")
        assert app.query_one("#emr-runs-pane").has_focus


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
