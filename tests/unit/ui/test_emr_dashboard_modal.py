"""Dashboard review interaction without AWS, clipboard, or native browser I/O."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import replace
from typing import Literal

import pytest
from textual.app import App
from textual.widgets import Input, Static, Switch

from aws_tui.ui.widgets.emr_serverless.dashboard_modal import DashboardModal
from aws_tui.vm.emr_serverless.dashboard_vm import DashboardOutcome, DashboardTarget
from aws_tui.vm.service_source_vm import ServiceSourceContext
from tests.helpers import wait_until


class _Browser:
    def open(self, url: str) -> bool:
        raise AssertionError("The modal must delegate browser delivery to its coordinator")


class _Coordinator:
    def __init__(self, page: _Page) -> None:
        self.page = page
        self.calls: list[tuple[DashboardTarget, dict[str, object]]] = []
        self.cancelled = 0
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.release.set()
        self.fail = False
        self.drain_on_cancel = False
        self.finished = asyncio.Event()
        self.outcome = DashboardOutcome(
            "copy_requested", "Link delivery requested; check clipboard outcome."
        )

    def available(self, target: DashboardTarget) -> bool:
        return self.page.target == target

    async def perform(self, target: DashboardTarget, **kwargs: object) -> DashboardOutcome:
        self.calls.append((target, kwargs))
        self.started.set()
        while True:
            try:
                await self.release.wait()
                break
            except asyncio.CancelledError:
                if not self.drain_on_cancel:
                    raise
        self.finished.set()
        if self.fail:
            raise RuntimeError("https://private.amazonaws.com/private?secret=private")
        return self.outcome

    def cancel(self) -> None:
        self.cancelled += 1


class _Page:
    def __init__(self) -> None:
        self.target = DashboardTarget(
            ServiceSourceContext("[source]", "profile", "us-east-1", protected=True),
            "app-123",
            "run-123",
            "[bold]ETL[/bold]",
            "SPARK",
            1,
        )
        self.dashboards = _Coordinator(self)

    def dashboard_target(self) -> DashboardTarget:
        return self.target


class _Host(App[None]):
    def __init__(
        self,
        page: _Page,
        mode: Literal["open", "copy"] = "open",
        *,
        on_closed_open: Callable[[DashboardTarget], None] | None = None,
    ) -> None:
        super().__init__()
        self.page = page
        self.browser = _Browser()
        self.copied: list[str] = []
        self.modal = DashboardModal(
            page,
            browser=self.browser,
            copy_link=self.copy,
            mode=mode,
            on_closed_open=on_closed_open,
        )

    async def copy(self, url: str) -> None:
        self.copied.append(url)

    async def on_mount(self) -> None:
        await self.push_screen(self.modal)


@pytest.mark.parametrize("size", [(80, 24), (120, 40)])
@pytest.mark.parametrize("mode", ["open", "copy"])
async def test_review_captured_identity_defaults_focus_and_fixed_footer(size, mode) -> None:
    page = _Page()
    app = _Host(page, mode)
    async with app.run_test(size=size) as pilot:
        await pilot.pause()
        modal = app.modal
        summary = modal.query_one("#emr-dashboard-summary", Static)
        for value in ("[source]", "us-east-1", "app-123", "run-123", "[bold]ETL[/bold]", "SPARK"):
            assert value in str(summary.content)
        assert not summary._render_markup
        assert "hour" in str(summary.content).lower()
        assert "fresh" in str(summary.content).lower()
        assert modal.query_one("#emr-dashboard-attempt", Input).value == ""
        assert not modal.query_one("#emr-dashboard-system-logs", Switch).value
        assert app.focused is modal.query_one(f"#emr-dashboard-{mode}")
        assert not page.dashboards.calls
        for name in ("open", "copy", "cancel"):
            button = modal.query_one(f"#emr-dashboard-{name}")
            assert button.region.height > 0
            assert 0 <= button.region.y < button.region.bottom <= size[1]
            assert 0 <= button.region.x < button.region.right <= size[0]


async def test_each_review_action_delegates_options_and_keeps_review_for_fresh_retry() -> None:
    page = _Page()
    app = _Host(page)
    async with app.run_test() as pilot:
        await pilot.pause()
        modal = app.modal
        modal.query_one("#emr-dashboard-open").press()
        await wait_until(
            lambda: len(page.dashboards.calls) == 1 and not modal._working,
            what="first dashboard request",
        )
        modal.query_one("#emr-dashboard-attempt", Input).value = "2"
        modal.query_one("#emr-dashboard-system-logs", Switch).value = True
        modal.query_one("#emr-dashboard-copy").press()
        await wait_until(
            lambda: len(page.dashboards.calls) == 2 and not modal._working,
            what="second fresh dashboard request",
        )
        assert app.screen is modal
        for index, (target, options) in enumerate(page.dashboards.calls):
            assert target == page.target
            assert options["mode"] == ("open" if index == 0 else "copy")
            assert options["attempt"] == (None if index == 0 else 2)
            assert options["access_system_profile_logs"] == bool(index)
            assert options["browser"] is app.browser
            assert options["copy_link"] == app.copy
        assert "clipboard outcome" in str(modal.query_one("#emr-dashboard-status", Static).content)
        assert not app.copied


@pytest.mark.parametrize("value", ["0", "-1", "1.5", "True", "private-attempt", "9" * 5000])
async def test_invalid_attempt_is_fixed_safe_validation_without_dispatch(value: str) -> None:
    page = _Page()
    app = _Host(page)
    async with app.run_test() as pilot:
        await pilot.pause()
        modal = app.modal
        modal.query_one("#emr-dashboard-attempt", Input).value = value
        modal.query_one("#emr-dashboard-open").press()
        await pilot.pause()
        assert not page.dashboards.calls
        assert str(modal.query_one("#emr-dashboard-status", Static).content) == (
            "Attempt must be a whole number greater than or equal to 1, or blank for latest."
        )
        assert not modal._working


async def test_duplicate_press_is_single_flight_and_escape_closes_pending_review() -> None:
    page = _Page()
    page.dashboards.release.clear()
    app = _Host(page)
    async with app.run_test() as pilot:
        await pilot.pause()
        modal = app.modal
        try:
            modal.query_one("#emr-dashboard-open").press()
            modal.query_one("#emr-dashboard-copy").press()
            await asyncio.wait_for(page.dashboards.started.wait(), 5)
            assert len(page.dashboards.calls) == 1
            assert modal._working
            await asyncio.wait_for(pilot.press("escape"), 5)
            assert app.screen is not modal
            assert page.dashboards.cancelled
        finally:
            page.dashboards.release.set()


async def test_stale_capture_cannot_be_retargeted_or_delivered() -> None:
    page = _Page()
    app = _Host(page)
    original = page.target
    async with app.run_test() as pilot:
        await pilot.pause()
        page.target = replace(page.target, revision=3, job_run_id="run-new")
        app.modal.query_one("#emr-dashboard-open").press()
        await pilot.pause()
        assert not page.dashboards.calls
        assert "no longer current" in str(
            app.modal.query_one("#emr-dashboard-status", Static).content
        )
        assert app.modal.query_one("#emr-dashboard-open").disabled
        assert app.modal.query_one("#emr-dashboard-copy").disabled
        assert original.job_run_id in str(
            app.modal.query_one("#emr-dashboard-summary", Static).content
        )


async def test_invalidate_cancels_and_disables_active_review() -> None:
    page = _Page()
    app = _Host(page)
    async with app.run_test() as pilot:
        await pilot.pause()
        app.modal.invalidate()
        await pilot.pause()
        assert page.dashboards.cancelled
        assert app.modal.query_one("#emr-dashboard-open").disabled
        assert app.modal.query_one("#emr-dashboard-copy").disabled
        await pilot.click("#emr-dashboard-cancel")
        assert app.screen is not app.modal


async def test_unexpected_coordinator_failure_is_safe_and_allows_retry() -> None:
    page = _Page()
    page.dashboards.fail = True
    app = _Host(page)
    async with app.run_test() as pilot:
        await pilot.pause()
        app.modal.query_one("#emr-dashboard-open").press()
        await wait_until(
            lambda: len(page.dashboards.calls) == 1 and not app.modal._working,
            what="safe coordinator failure",
        )
        status = str(app.modal.query_one("#emr-dashboard-status", Static).content)
        assert "failed" in status.lower()
        assert "private" not in status
        assert not app.modal.query_one("#emr-dashboard-open").disabled
        assert not app.modal.query_one("#emr-dashboard-copy").disabled


@pytest.mark.parametrize("close", [True, False])
async def test_close_or_invalidation_before_worker_entry_prevents_delivery(close: bool) -> None:
    page = _Page()
    app = _Host(page)
    async with app.run_test() as pilot:
        await pilot.pause()
        app.modal.action_open()
        assert app.modal._working
        if close:
            app.modal.action_cancel()
        else:
            app.modal.invalidate()
        await pilot.pause()
        assert not page.dashboards.calls
        assert not app.modal._working


async def test_admitted_open_is_reported_truthfully_after_review_invalidation() -> None:
    page = _Page()
    page.dashboards.outcome = DashboardOutcome(
        "opened", "Browser open request sent for the reviewed run."
    )
    page.dashboards.release.clear()
    app = _Host(page)
    async with app.run_test() as pilot:
        await pilot.pause()
        app.modal.action_open()
        try:
            await asyncio.wait_for(page.dashboards.started.wait(), 5)
            page.target = replace(page.target, revision=2, job_run_id="new-run")
            app.modal.invalidate()
            page.dashboards.release.set()
            await wait_until(lambda: not app.modal._working, what="admitted browser outcome")
            status = str(app.modal.query_one("#emr-dashboard-status", Static).content)
            assert "Browser open request sent for the reviewed run." in status
            assert "no longer current" in status
            assert app.modal.query_one("#emr-dashboard-open").disabled
            assert app.modal.query_one("#emr-dashboard-copy").disabled
        finally:
            page.dashboards.release.set()


async def test_admitted_open_after_close_reports_captured_target_once_without_link() -> None:
    page = _Page()
    reviewed = page.target
    page.dashboards.outcome = DashboardOutcome("opened", "Browser open request sent.")
    page.dashboards.release.clear()
    page.dashboards.drain_on_cancel = True
    reported: list[DashboardTarget] = []
    app = _Host(page, on_closed_open=reported.append)
    async with app.run_test() as pilot:
        await pilot.pause()
        app.modal.action_open()
        try:
            await asyncio.wait_for(page.dashboards.started.wait(), 5)
            await asyncio.wait_for(pilot.press("escape"), 5)
            assert app.screen is not app.modal
            assert not page.dashboards.release.is_set()
            assert not reported
            page.target = replace(reviewed, revision=2, job_run_id="other-run")
            page.dashboards.release.set()
            await wait_until(lambda: bool(reported), what="closed reviewed browser completion")
            await pilot.pause()
            assert reported == [reviewed]
            assert reported[0] is reviewed
            assert "https://" not in repr(reported)
            assert "url" not in DashboardTarget.__dataclass_fields__
            assert not app.copied
        finally:
            page.dashboards.release.set()


async def test_closed_open_callback_failure_cannot_crash_or_leak(capsys, caplog) -> None:
    page = _Page()
    page.dashboards.outcome = DashboardOutcome("opened", "Browser open request sent.")
    page.dashboards.release.clear()
    page.dashboards.drain_on_cancel = True
    reported: list[DashboardTarget] = []

    def fail(target: DashboardTarget) -> None:
        reported.append(target)
        raise RuntimeError("https://private.amazonaws.com/private?secret=callback")

    app = _Host(page, on_closed_open=fail)
    async with app.run_test() as pilot:
        await pilot.pause()
        app.modal.action_open()
        try:
            await asyncio.wait_for(page.dashboards.started.wait(), 5)
            app.modal.action_cancel()
            await pilot.pause()
            page.dashboards.release.set()
            await wait_until(lambda: bool(reported), what="guarded completion callback")
            await pilot.pause()
            assert len(reported) == 1
            assert app.screen is not app.modal
        finally:
            page.dashboards.release.set()
    captured = capsys.readouterr()
    assert "private.amazonaws.com" not in captured.out + captured.err + caplog.text
    assert "secret=callback" not in captured.out + captured.err + caplog.text


@pytest.mark.parametrize("status", ["cancelled", "stale", "error", "copy_requested"])
async def test_closed_non_open_outcome_does_not_report_open(status: str) -> None:
    page = _Page()
    page.dashboards.outcome = DashboardOutcome(status, "Safe controlled outcome.")
    page.dashboards.release.clear()
    page.dashboards.drain_on_cancel = True
    reported: list[DashboardTarget] = []
    app = _Host(page, on_closed_open=reported.append)
    async with app.run_test() as pilot:
        await pilot.pause()
        app.modal.action_open()
        try:
            await asyncio.wait_for(page.dashboards.started.wait(), 5)
            app.modal.action_cancel()
            await pilot.pause()
            page.dashboards.release.set()
            await asyncio.wait_for(page.dashboards.finished.wait(), 5)
            await pilot.pause()
            assert not reported
        finally:
            page.dashboards.release.set()
