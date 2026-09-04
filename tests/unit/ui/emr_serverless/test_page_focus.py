from __future__ import annotations

import asyncio
from collections.abc import Callable

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


def _track_picker_focus(
    picker: ContextPicker | ApplicationPicker,
    monkeypatch: pytest.MonkeyPatch,
) -> asyncio.Event:
    focused = asyncio.Event()
    options = picker.query_one(OptionList)
    if isinstance(picker, ContextPicker):
        focus_callback = picker._focus_options
        callback_name = "_focus_options"
    else:
        focus_callback = picker._prepare_open_dropdown
        callback_name = "_prepare_open_dropdown"

    def track_focus(epoch: int) -> None:
        focus_callback(epoch)
        if picker.is_open and picker.app.focused is options:
            focused.set()

    monkeypatch.setattr(picker, callback_name, track_focus)
    return focused


def _track_picker_reconcile(
    page: EmrServerlessPage,
    settled: Callable[[], bool],
    monkeypatch: pytest.MonkeyPatch,
) -> asyncio.Event:
    reconciled = asyncio.Event()
    reconcile = page._reconcile_open_pickers

    def track_reconcile(epoch: int) -> None:
        reconcile(epoch)
        if page._picker_open_intent.is_current(epoch) and settled():
            reconciled.set()

    monkeypatch.setattr(page, "_reconcile_open_pickers", track_reconcile)
    return reconciled


async def _wait_for_completions(*completions: asyncio.Event) -> None:
    async with asyncio.timeout(2):
        await asyncio.gather(*(completion.wait() for completion in completions))


@pytest.mark.asyncio
async def test_tab_cycle_closes_departed_application_picker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = EmrPageApp(theme="carbon")

    async with app.run_test() as pilot:
        await pilot.pause()
        page = app.query_one(EmrServerlessPage)
        picker = app.query_one(ApplicationPicker)
        focus_complete = _track_picker_focus(picker, monkeypatch)
        picker.toggle_open()
        await _wait_for_completions(focus_complete)
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
    monkeypatch: pytest.MonkeyPatch,
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

        focus_complete = _track_picker_focus(source_picker, monkeypatch)
        source_picker.open()
        await _wait_for_completions(focus_complete)
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
async def test_application_picker_overlay_closes_when_focus_leaves(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = EmrPageApp(theme="carbon")
    async with app.run_test() as pilot:
        picker = app.query_one(ApplicationPicker)
        focus_complete = _track_picker_focus(picker, monkeypatch)
        picker.toggle_open()
        await _wait_for_completions(focus_complete)

        app.query_one(JobRunsPane).focus()
        await pilot.pause()

        assert not picker.is_open


@pytest.mark.asyncio
async def test_keyboard_opening_application_picker_closes_source_picker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
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

        focus_complete = _track_picker_focus(application_picker, monkeypatch)
        reconcile_complete = _track_picker_reconcile(
            page,
            lambda: application_picker.is_open and not source_picker.is_open,
            monkeypatch,
        )
        await pilot.press("enter")
        await _wait_for_completions(focus_complete, reconcile_complete)

        assert not source_picker.is_open
        assert application_picker.is_open


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("first", "newest"),
    [("source", "application"), ("application", "source")],
)
async def test_same_turn_programmatic_opens_keep_newest_picker_focused(
    first: str,
    newest: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = EmrPageApp(theme="carbon")

    async with app.run_test() as pilot:
        await pilot.pause()
        page = app.query_one(EmrServerlessPage)
        source = app.query_one("#emr-source-header-picker", ContextPicker)
        application = app.query_one(ApplicationPicker)
        pickers = {"source": source, "application": application}

        focus_complete = _track_picker_focus(pickers[newest], monkeypatch)
        reconcile_complete = _track_picker_reconcile(
            page,
            lambda: pickers[newest].is_open and not pickers[first].is_open,
            monkeypatch,
        )
        pickers[first].open()
        pickers[newest].open()
        await _wait_for_completions(focus_complete, reconcile_complete)

        assert pickers[newest].is_open
        assert not pickers[first].is_open
        assert app.focused is pickers[newest].query_one(OptionList)


@pytest.mark.asyncio
async def test_same_turn_application_close_reopen_keeps_reopened_picker_focused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = EmrPageApp(theme="carbon")

    async with app.run_test() as pilot:
        await pilot.pause()
        source = app.query_one("#emr-source-header-picker", ContextPicker)
        application = app.query_one(ApplicationPicker)

        focus_complete = _track_picker_focus(application, monkeypatch)
        application.open()
        application.close()
        application.open()
        await _wait_for_completions(focus_complete)

        assert application.is_open
        assert not source.is_open
        assert app.focused is application.query_one(OptionList)


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
async def test_shift_tab_cycle_closes_departed_application_picker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = EmrPageApp(theme="carbon")

    async with app.run_test() as pilot:
        await pilot.pause()
        page = app.query_one(EmrServerlessPage)
        picker = app.query_one(ApplicationPicker)
        focus_complete = _track_picker_focus(picker, monkeypatch)
        picker.toggle_open()
        await _wait_for_completions(focus_complete)
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
