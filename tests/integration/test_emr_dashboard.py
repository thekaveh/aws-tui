"""Dashboard commands exercise actual composition with synthetic delivery ports."""

from __future__ import annotations

import pytest
from textual.widgets import Input, Static, Switch

from aws_tui.app import AwsTuiApp
from tests.helpers import focus_and_settle, wait_until
from tests.integration.test_emr_new_job import context
from tests.integration.test_glue_page import open_service


class Browser:
    def __init__(self, result=True):
        self.urls = []
        self.result = result

    def open(self, url):
        self.urls.append(url)
        return self.result


async def test_browser_admitted_before_close_reports_completion_for_reviewed_run(
    tmp_path, monkeypatch
):
    import threading

    ctx, fake = context(tmp_path, monkeypatch)
    fake.add_job_run(application_id="application001", job_run_id="run001")
    entered, release = threading.Event(), threading.Event()

    class BlockingBrowser:
        def open(self, url):
            entered.set()
            assert release.wait(10)
            return True

    ctx.browser = BlockingBrowser()
    app = AwsTuiApp(ctx)
    try:
        async with app.run_test() as pilot:
            await open_service(ctx, pilot, "emr-serverless")
            page = ctx.root_vm.content_host.current
            await pilot.press("U")
            await wait_until(lambda: bool(app.screen.query("#emr-dashboard-open")), what="review")
            modal = app.screen
            modal.action_open()
            await wait_until(entered.is_set, what="browser admission")
            await pilot.press("escape")
            await wait_until(lambda: app.screen is not modal, what="prompt close")
            release.set()
            await wait_until(lambda: not page.dashboards.busy, what="admitted browser drain")
            await wait_until(
                lambda: any(
                    "browser open request sent" in toast.model.text
                    and "run001" in toast.model.text
                    and "application001" in toast.model.text
                    and "us-east-1" in toast.model.text
                    for toast in ctx.root_vm.chrome.toast_stack.toasts
                ),
                what="truthful closed-review completion",
                timeout=2,
            )
            assert page.dashboards.outcome.status == "opened"
            assert app._crash_report is None
    finally:
        release.set()
        ctx.root_vm.dispose()
        fake.dispose()


async def test_dashboard_clipboard_does_not_retain_textual_clipboard_cache(tmp_path, monkeypatch):
    from aws_tui.infra.clipboard import InMemoryClipboard

    ctx, fake = context(tmp_path, monkeypatch)
    clipboard = InMemoryClipboard()
    ctx.clipboard_vm._clipboard = clipboard
    app = AwsTuiApp(ctx)
    url = "https://private-host.amazonaws.com/private-path?private-query#private-fragment"
    try:
        await app._put_dashboard_on_clipboard(url)
        assert app.clipboard != url
        assert url not in repr(ctx.clipboard_vm.last_write)
    finally:
        ctx.root_vm.dispose()
        fake.dispose()


@pytest.mark.parametrize("app_type", ["SPARK", "HIVE"])
@pytest.mark.parametrize("route", ["key", "remap", "palette"])
async def test_reviewed_dashboard_uses_selected_run_and_options(
    tmp_path, monkeypatch, app_type, route
):
    ctx, fake = context(tmp_path, monkeypatch, app_type=app_type, protected=True)
    fake.add_job_run(application_id="application001", job_run_id="run001")
    if route == "remap":
        from aws_tui.infra.keymap_store import KeymapStore

        ctx.keymap_store = KeymapStore(overlay={"emr.dashboard.open": "B"})
    app = AwsTuiApp(ctx)
    try:
        assert app._actions.has("emr.dashboard.open")
        browser = Browser()
        ctx.browser = browser
        async with app.run_test(size=(80, 24)) as pilot:
            await open_service(ctx, pilot, "emr-serverless")
            if route == "palette":
                await pilot.press("colon", *"Open EMR dashboard", "enter")
            else:
                await pilot.press("B" if route == "remap" else "U")
            await wait_until(
                lambda: bool(app.screen.query("#emr-dashboard-open")), what="dashboard review"
            )
            modal = app.screen
            summary = str(modal.query_one("#emr-dashboard-summary", Static).render())
            assert "application001" in summary
            assert "run001" in summary
            assert "us-east-1" in summary
            assert not [c for c in fake.calls if c[0] == "get_dashboard_for_job_run"]
            assert not modal.query_one("#emr-dashboard-system-logs", Switch).value
            modal.query_one("#emr-dashboard-attempt", Input).value = "2"
            modal.query_one("#emr-dashboard-system-logs", Switch).value = True
            await focus_and_settle(modal.query_one("#emr-dashboard-open"))
            await pilot.press("enter")
            await wait_until(lambda: len(browser.urls) == 1, what="browser request")
            calls = [c[1] for c in fake.calls if c[0] == "get_dashboard_for_job_run"]
            assert calls == [("application001", "run001", 2, True)]
            await wait_until(lambda: not modal._working, what="delivery outcome")
            assert "Browser open request sent" in str(
                modal.query_one("#emr-dashboard-status", Static).render()
            )
            assert browser.urls[0] not in repr(ctx.root_vm.content_host.current.dashboards.outcome)
            assert app._crash_report is None
    finally:
        ctx.root_vm.dispose()
        fake.dispose()


@pytest.mark.parametrize("route", ["copy", "copy-remap", "copy-palette", "fallback"])
async def test_copy_routes_and_browser_fallback_use_real_clipboard_pipeline(
    tmp_path, monkeypatch, route
):
    from aws_tui.infra.clipboard import InMemoryClipboard
    from aws_tui.infra.crash_dump import CrashDump
    from aws_tui.infra.keymap_store import KeymapStore
    from aws_tui.vm.clipboard_vm import ClipboardChannel

    ctx, fake = context(tmp_path, monkeypatch)
    fake.add_job_run(application_id="application001", job_run_id="run001")
    if route == "copy-remap":
        ctx.keymap_store = KeymapStore(overlay={"emr.dashboard.copy": "B"})
    clipboard = InMemoryClipboard()
    ctx.clipboard_vm._clipboard = clipboard
    browser = Browser(False)
    ctx.browser = browser
    app = AwsTuiApp(ctx)
    try:
        async with app.run_test(size=(80, 24)) as pilot:
            await open_service(ctx, pilot, "emr-serverless")
            if route == "copy-palette":
                await pilot.press("colon", *"Copy EMR dashboard link", "enter")
            else:
                await pilot.press({"copy": "alt+u", "copy-remap": "B", "fallback": "U"}[route])
            await wait_until(
                lambda: bool(app.screen.query("#emr-dashboard-copy")), what="copy review"
            )
            modal = app.screen
            button = modal.query_one(
                "#emr-dashboard-open" if route == "fallback" else "#emr-dashboard-copy"
            )
            for count in (1, 2):
                await focus_and_settle(button)
                await pilot.press("enter")
                await wait_until(
                    lambda count=count: (
                        len([c for c in fake.calls if c[0] == "get_dashboard_for_job_run"]) == count
                        and not modal._working
                    ),
                    what="fresh clipboard delivery",
                )
            assert ctx.clipboard_vm.last_write.channel is ClipboardChannel.NATIVE
            assert len(browser.urls) == (2 if route == "fallback" else 0)
            if route == "fallback":
                assert browser.urls[0] != browser.urls[1]
            assert app.clipboard == ""
            calls = [c[1] for c in fake.calls if c[0] == "get_dashboard_for_job_run"]
            assert calls == [("application001", "run001", None, False)] * 2
            assert not [c for c in fake.calls if c[0] in {"start_job_run", "cancel_job_run"}]
            assert "delivery requested" in str(
                modal.query_one("#emr-dashboard-status", Static).render()
            )
            dump = (
                CrashDump(base_dir=tmp_path / "dump")
                .write(
                    exc=RuntimeError("unrelated failure"),
                    log_path=ctx.log_sink.path,
                    action_ring=app._action_ring,
                )
                .read_text()
            )
            for url in browser.urls:
                assert url not in dump
                assert url.split("?", 1)[0] not in dump
            assert app._crash_report is None
    finally:
        ctx.root_vm.dispose()
        fake.dispose()


async def test_selection_away_and_back_fences_an_open_review(tmp_path, monkeypatch):
    ctx, fake = context(tmp_path, monkeypatch)
    for run_id in ("run001", "run002"):
        fake.add_job_run(application_id="application001", job_run_id=run_id)
    ctx.browser = Browser()
    app = AwsTuiApp(ctx)
    try:
        async with app.run_test() as pilot:
            await open_service(ctx, pilot, "emr-serverless")
            await pilot.press("U")
            await wait_until(lambda: bool(app.screen.query("#emr-dashboard-open")), what="review")
            modal = app.screen
            page = ctx.root_vm.content_host.current
            original = page.job_runs.selected_id
            other = "run001" if original == "run002" else "run002"
            page.job_runs.select(other)
            page.job_runs.select(original)
            assert modal._invalidated
            assert modal.query_one("#emr-dashboard-open").disabled
            modal.action_open()
            await pilot.pause()
            assert not [c for c in fake.calls if c[0] == "get_dashboard_for_job_run"]
            await pilot.press("escape")
    finally:
        ctx.root_vm.dispose()
        fake.dispose()


async def test_closed_modal_ignores_cancellation_resistant_provider(tmp_path, monkeypatch):
    import asyncio

    from aws_tui.domain.emr_dashboard import DashboardLink

    ctx, fake = context(tmp_path, monkeypatch)
    fake.add_job_run(application_id="application001", job_run_id="run001")
    started, release = asyncio.Event(), asyncio.Event()

    async def pending(*args, **kwargs):
        started.set()
        try:
            await release.wait()
        except asyncio.CancelledError:
            await release.wait()
        return DashboardLink("https://private-host.amazonaws.com/secret?token=secret#secret")

    fake.get_dashboard_for_job_run = pending
    browser = Browser()
    ctx.browser = browser
    app = AwsTuiApp(ctx)
    try:
        async with app.run_test() as pilot:
            await open_service(ctx, pilot, "emr-serverless")
            await pilot.press("U")
            await wait_until(lambda: bool(app.screen.query("#emr-dashboard-open")), what="review")
            modal = app.screen
            modal.action_open()
            await wait_until(started.is_set, what="pending retrieval")
            await pilot.press("escape")
            await wait_until(lambda: app.screen is not modal, what="closed review")
            release.set()
            await wait_until(
                lambda: not ctx.root_vm.content_host.current.dashboards.busy, what="drain"
            )
            assert browser.urls == []
            assert app.clipboard == ""
            assert app._crash_report is None
    finally:
        release.set()
        ctx.root_vm.dispose()
        fake.dispose()


async def test_global_shutdown_intake_fences_pending_dashboard_before_cleanup(
    tmp_path, monkeypatch
):
    import asyncio
    from unittest.mock import AsyncMock

    from aws_tui.domain.emr_dashboard import DashboardLink

    ctx, fake = context(tmp_path, monkeypatch)
    fake.add_job_run(application_id="application001", job_run_id="run001")
    started, release_provider = asyncio.Event(), asyncio.Event()
    shutdown_started, release_shutdown = asyncio.Event(), asyncio.Event()

    async def pending(*args, **kwargs):
        started.set()
        try:
            await release_provider.wait()
        except asyncio.CancelledError:
            await release_provider.wait()
        return DashboardLink("https://synthetic.amazonaws.com/review?synthetic=yes")

    async def hold_early_shutdown():
        shutdown_started.set()
        await release_shutdown.wait()

    fake.get_dashboard_for_job_run = pending
    browser = Browser()
    copy = AsyncMock()
    app = AwsTuiApp(ctx)
    shutdown = None
    try:
        async with app.run_test() as pilot:
            await open_service(ctx, pilot, "emr-serverless")
            page = ctx.root_vm.content_host.current
            operation = asyncio.create_task(
                page.dashboards.perform(
                    page.dashboard_target(), mode="open", browser=browser, copy_link=copy
                )
            )
            await started.wait()
            monkeypatch.setattr(
                app, "_cancel_transfer_workers_before_content_swap", hold_early_shutdown
            )
            shutdown = asyncio.create_task(app._aws_tui_shutdown())
            await shutdown_started.wait()
            try:
                assert app.service_navigation.closed
                release_provider.set()
                outcome = await asyncio.wait_for(operation, 3)
                assert browser.urls == []
                copy.assert_not_awaited()
                assert outcome.status in {"cancelled", "stale"}
                assert app.clipboard == ""
            finally:
                release_provider.set()
                release_shutdown.set()
                await asyncio.wait_for(shutdown, 10)
    finally:
        release_provider.set()
        release_shutdown.set()
        if shutdown is not None and not shutdown.done():
            await asyncio.wait_for(shutdown, 10)
        fake.dispose()
