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
from aws_tui.vm.chrome.focus_coordinator_vm import FocusSlot
from tests.helpers import focus_and_settle, wait_until
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
        await wait_until(
            lambda: not picker.has_class("-open") and app.query_one("#emr-runs-pane").has_focus,
            what="forward pane cycle closed application picker and focused runs",
        )

        assert not picker.has_class("-open")
        assert app.query_one("#emr-runs-pane").has_focus


@pytest.mark.asyncio
async def test_reprojecting_the_application_slot_leaves_its_picker_open(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``EMR_APPLICATION`` is a slot whose target hosts its own overlay.

    Focusing the target while the overlay holds the focus moves focus up out of
    the overlay, and ``OverlayOptionList.on_blur`` posts that as a dismissal --
    so a deferred projection landing after the picker opened would close it.
    That is the #235 defect; EMR shares the projection shape, so it is pinned
    here rather than left latent.
    """
    app = EmrPageApp(theme="carbon")

    async with app.run_test() as pilot:
        await pilot.pause()
        page = app.query_one(EmrServerlessPage)
        picker = app.query_one(ApplicationPicker)
        focus_complete = _track_picker_focus(picker, monkeypatch)
        picker.toggle_open()
        await _wait_for_completions(focus_complete)
        overlay = picker.query_one(OptionList)
        assert picker.is_open
        assert app.focused is overlay

        page.project_focus_slot(FocusSlot.EMR_APPLICATION)
        # Drain focus projection events before asserting the open overlay retained focus.
        await pilot.pause()

        assert picker.is_open
        assert app.focused is overlay


@pytest.mark.asyncio
async def test_reprojecting_the_source_slot_leaves_its_picker_open() -> None:
    app = EmrPageOpenSourcePickerApp(theme="carbon")

    async with app.run_test() as pilot:
        await pilot.pause()
        page = app.query_one(EmrServerlessPage)
        source_picker = await _opened_source_picker(pilot, app)
        overlay = source_picker.query_one(OptionList)
        await wait_until(
            lambda: app.focused is overlay,
            what="the EMR source picker's overlay took focus",
        )

        page.project_focus_slot(FocusSlot.EMR_SOURCE)
        # Drain source projection events before asserting the open overlay retained focus.
        await pilot.pause()

        assert source_picker.is_open
        assert app.focused is overlay


@pytest.mark.asyncio
async def test_application_picker_overlay_preserves_page_geometry_through_escape() -> None:
    app = EmrPageApp(theme="carbon")

    async with app.run_test() as pilot:
        await pilot.pause()
        picker = app.query_one(ApplicationPicker)
        widgets = (
            picker,
            app.query_one(".emr-context-row", Horizontal),
            app.query_one(ServiceSourceHeader),
            app.query_one(JobRunsPane),
            app.query_one(JobRunDetailPane),
            app.query_one("#content-host"),
        )
        closed_regions = tuple(widget.region for widget in widgets)

        picker.toggle_open()
        await wait_until(
            lambda: picker.is_open and app.focused is picker.query_one(OptionList),
            what="application overlay opened before geometry comparison",
        )
        # Drain layout after opening the overlay before checking geometry remained unchanged.
        await pilot.pause()

        assert tuple(widget.region for widget in widgets) == closed_regions

        await pilot.press("escape")
        await wait_until(
            lambda: not picker.is_open,
            what="Escape closed the application overlay",
        )

        assert not picker.is_open
        assert tuple(widget.region for widget in widgets) == closed_regions


@pytest.mark.asyncio
async def test_activation_readiness_tracks_custom_targets_without_dispatch(monkeypatch) -> None:
    from textual.widgets import Input, TextArea

    from aws_tui.domain.emr_serverless import JobRunState
    from aws_tui.ui.widgets.emr_serverless.job_run_logs_pane import JobRunLogsPane
    from aws_tui.vm.emr_serverless.job_run_logs_vm import LogsState

    app = EmrPageApp(theme="carbon")
    async with app.run_test() as pilot:
        await pilot.pause()
        page = app.query_one(EmrServerlessPage)
        picker = app.query_one(ApplicationPicker)
        runs = app.query_one(JobRunsPane)
        logs = app.query_one(JobRunLogsPane)
        detail = app.query_one(JobRunDetailPane)
        calls = []
        monkeypatch.setattr(picker, "action_activate", lambda: calls.append("picker"))
        monkeypatch.setattr(runs, "action_commit_selection", lambda: calls.append("run"))
        monkeypatch.setattr(logs, "action_load", lambda: calls.append("logs"))
        assert page.can_activate_focused(picker)
        assert page.can_activate_focused(runs)
        # The actual run Enter route falls back to the first visible row even
        # when its stored selection has been filtered out.
        page.vm.job_runs.set_state_filter(frozenset({JobRunState.RUNNING}))
        assert page.can_activate_focused(runs)
        page.vm.job_runs.set_state_filter(frozenset({JobRunState.FAILED}))
        assert not page.can_activate_focused(runs)
        assert not page.can_activate_focused(detail)
        assert not page.can_activate_focused(None)
        assert not page.can_activate_focused(Input())
        assert not page.can_activate_focused(TextArea())
        for state in LogsState:
            page.vm.job_run_logs._set_state(state)
            assert page.can_activate_focused(logs) is (
                state in (LogsState.IDLE, LogsState.NO_FILES)
            )
        assert calls == []
        picker.open()
        await pilot.pause()
        options = picker.query_one(OptionList)
        options.highlighted = 0
        assert page.can_activate_focused(options)
        options.highlighted = None
        assert not page.can_activate_focused(options)
        assert not page.can_activate_focused(picker)
        options.highlighted = 0
        options.get_option_at_index(0).disabled = True
        assert not page.can_activate_focused(options)
        picker.close()
        await pilot.pause()
        detail.focus()
        await pilot.pause()
        assert page.activate_focused(), "inert detail Enter must still be consumed"


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
        # Drain close/refocus layout before checking page geometry remained unchanged.
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
        await wait_until(
            lambda: picker.is_open and picker.has_focus_within,
            what="application picker opened before removal",
        )
        assert picker.is_open

        await picker.remove()
        # Awaited removal completes teardown; drain queued callbacks to detect forbidden refocus.
        await pilot.pause()

        assert not picker.is_open
        assert not picker.is_running
        assert refocus_calls == 0


@pytest.mark.asyncio
async def test_application_picker_overlay_closes_when_focus_leaves(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = EmrPageApp(theme="carbon")
    async with app.run_test():
        picker = app.query_one(ApplicationPicker)
        focus_complete = _track_picker_focus(picker, monkeypatch)
        picker.toggle_open()
        await _wait_for_completions(focus_complete)

        await focus_and_settle(app.query_one(JobRunsPane))
        await wait_until(
            lambda: not picker.is_open and app.query_one(JobRunsPane).has_focus,
            what="leaving the application overlay closed it",
        )

        assert not picker.is_open


async def _opened_source_picker(pilot: object, app: EmrPageOpenSourcePickerApp) -> ContextPicker:
    """Return the source picker, open, without depending on mount timing.

    ``EmrPageOpenSourcePickerApp`` auto-opens the list from a mount-time
    ``call_after_refresh`` that races page setup: while the page is still
    settling ``ContextPicker.open`` is a silent no-op (the picker reports
    loading) and a queued open-intent reconcile can close it again. Sibling
    tests in this file already call ``picker.open()`` themselves for exactly
    that reason.

    The product path is a keystroke and is synchronous -- only the mount-time
    auto-open is racy -- and these tests are about what happens *after* the
    list is open, so they should establish that precondition rather than
    inherit it from how fast the runner settles. Observed on windows-latest
    py3.11 and py3.12, where the fixture's single attempt lost the race and
    the first assertion failed before the behaviour under test ever ran.
    """
    picker = app.query_one("#emr-source-header-picker", ContextPicker)
    for _ in range(50):
        if picker.is_open:
            return picker
        app.query_one(ServiceSourceHeader).open()
        await pilot.pause()  # type: ignore[attr-defined]
    raise AssertionError("source picker never opened")


@pytest.mark.asyncio
async def test_keyboard_opening_application_picker_closes_source_picker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = EmrPageOpenSourcePickerApp(theme="carbon")

    async with app.run_test() as pilot:
        await pilot.pause()
        page = app.query_one(EmrServerlessPage)
        source_picker = await _opened_source_picker(pilot, app)
        application_picker = app.query_one(ApplicationPicker)
        app.set_focus(source_picker.query_one(OptionList))
        await wait_until(
            lambda: app.focused is source_picker.query_one(OptionList),
            what="source options took focus",
        )
        assert app.focused is source_picker.query_one(OptionList)

        page.action_cycle_panes_forward()
        await wait_until(
            lambda: app.focused is application_picker and not source_picker.is_open,
            what="pane cycle focused application and closed source",
        )
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
        # Awaited page removal completes teardown; drain callbacks before checking no picker reopened.
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
        await wait_until(
            lambda: not picker.has_class("-open") and app.query_one("#emr-source-header").has_focus,
            what="reverse pane cycle closed application picker and focused source",
        )

        assert not picker.has_class("-open")
        assert app.query_one("#emr-source-header").has_focus


@pytest.mark.asyncio
async def test_tab_cycle_closes_departed_source_picker() -> None:
    app = EmrPageOpenSourcePickerApp(theme="carbon")

    async with app.run_test() as pilot:
        await pilot.pause()
        page = app.query_one(EmrServerlessPage)
        picker = await _opened_source_picker(pilot, app)
        app.set_focus(picker.query_one(OptionList))
        await pilot.pause()

        page.action_cycle_panes_forward()
        await wait_until(
            lambda: not picker.is_open and app.query_one(ApplicationPicker).has_focus,
            what="pane cycle closed source picker and focused application",
        )

        assert not picker.is_open
        assert app.query_one(ApplicationPicker).has_focus


@pytest.mark.parametrize("transition", ["unchanged", "aba", "uri", "unmount"])
async def test_owned_log_filter_result_is_bound_to_monotonic_target(transition):
    from textual.widgets import TextArea

    from aws_tui.domain.emr_logs import DEFAULT_LOG_FILTER
    from aws_tui.ui.widgets.emr_serverless.job_run_logs_pane import JobRunLogsPane
    from aws_tui.ui.widgets.emr_serverless.log_filter_modal import LogFilterModal
    from aws_tui.ui.widgets.modal_button import ModalButton

    app = EmrPageApp("carbon")
    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.pause()
        page = app.query_one(EmrServerlessPage)
        logs = page.vm.job_run_logs
        logs.set_target("00abc", "r-001", "s3://bucket-a/logs")
        pane = app.query_one(JobRunLogsPane)
        await focus_and_settle(pane)
        pane.action_open_filter()
        await wait_until(lambda: isinstance(app.screen, LogFilterModal), what="owned log form")
        modal = app.screen
        modal.query_one("#log-patterns", TextArea).load_text("FATAL")
        if transition == "aba":
            logs.set_target("00abc", "other", "s3://bucket-a/logs")
            logs.set_target("00abc", "r-001", "s3://bucket-a/logs")
        elif transition == "uri":
            logs.set_target("00abc", "r-001", "s3://bucket-b/logs")
        elif transition == "unmount":
            await page.remove()
            await wait_until(
                lambda: not isinstance(app.screen, LogFilterModal),
                what="unmount dismisses owned form",
            )
            assert logs.filter == DEFAULT_LOG_FILTER
            return
        await pilot.click(next(b for b in modal.query(ModalButton) if b.button_id == "apply"))
        await wait_until(
            lambda: not isinstance(app.screen, LogFilterModal), what="apply returns owned form"
        )
        await app.workers.wait_for_complete(list(app.workers._workers))
        assert logs.filter.patterns == (
            ("FATAL",) if transition == "unchanged" else DEFAULT_LOG_FILTER.patterns
        )
