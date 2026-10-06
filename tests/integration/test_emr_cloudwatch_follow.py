"""Real compact app journey and durable teardown, with AWS creation forbidden."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

import pytest
from textual.widgets import TextArea

from aws_tui.app import AwsTuiApp
from aws_tui.composition import build_app_context
from aws_tui.domain.emr_cloudwatch_logs import CloudWatchLogEvent
from aws_tui.ui.widgets.emr_serverless.job_run_logs_pane import JobRunLogsPane
from aws_tui.ui.widgets.emr_serverless.job_runs_pane import JobRunsPane
from aws_tui.ui.widgets.emr_serverless.log_filter_modal import LogFilterModal
from aws_tui.ui.widgets.modal_button import ModalButton
from aws_tui.ui.widgets.nav_row import NavRow
from aws_tui.vm.emr_serverless.job_run_logs_vm import LogsState
from tests.helpers import focus_and_settle, wait_until


async def _drain(app):
    async with asyncio.timeout(5):
        while app.workers._workers:
            await app.workers.wait_for_complete(list(app.workers._workers))


async def _open_demo(tmp_path, monkeypatch, pilot, app):
    await _drain(app)
    await pilot.pause()
    row = next(row for row in app.query(NavRow) if row.descriptor_id == "emr-serverless")
    await pilot.click(row)
    await _drain(app)
    await pilot.pause()
    page = app.app_ctx.root_vm.content_host.current
    target = next(
        run.job_run_id for run in page.job_runs.runs if run.job_run_id.endswith("cloudwatch-only")
    )
    pane = app.query_one(JobRunsPane)
    await focus_and_settle(pane)
    for _ in range(len(page.job_runs.runs)):
        if page.job_runs.selected_id == target:
            break
        await pilot.press("down")
        await _drain(app)
        await pilot.pause()
    assert page.job_runs.selected_id == target
    await _drain(app)
    logs = page.job_run_logs
    fake = app.app_ctx.demo_emrs["demo-dev"]
    now = [logs._run_created_at_ms + 100_000]
    monkeypatch.setattr("aws_tui.vm.emr_serverless.job_run_logs_vm._now_ms", lambda: now[0])
    await focus_and_settle(app.query_one(JobRunLogsPane))
    return page, logs, fake, now


def _forbid_aws(monkeypatch):
    def forbidden(*_args, **_kwargs):
        raise AssertionError("real AWS session/client construction in demo journey")

    monkeypatch.setattr("aioboto3.Session", forbidden)


async def test_compact_real_demo_load_attempt_filter_follow_stop(tmp_path: Path, monkeypatch):
    _forbid_aws(monkeypatch)
    ctx = build_app_context(config_dir=tmp_path / "config", cache_dir=tmp_path / "cache", demo=True)
    app = AwsTuiApp(ctx)
    ticks = asyncio.Queue()

    async def sleep(delay):
        assert delay == 2.0
        await ticks.get()

    monkeypatch.setattr("aws_tui.vm.emr_serverless.job_run_logs_vm._sleep", sleep)
    try:
        async with app.run_test(size=(80, 24)) as pilot:
            _page, logs, fake, now = await _open_demo(tmp_path, monkeypatch, pilot, app)
            await pilot.press("enter")
            await _drain(app)
            await pilot.pause()
            assert logs.state is LogsState.READY
            assert logs.lines == ("ERROR CloudWatch demo failure",)
            pane = app.query_one(JobRunLogsPane)
            assert "ERROR CloudWatch demo failure" in str(pane.query_one("#logs-content").render())
            await wait_until(
                lambda: not app.query("Toast"), what="startup demo toast to expire", timeout=8
            )
            await pilot.pause()
            artifact_dir = os.environ.get("AWS_TUI_TASK2_VISUAL_DIR")
            if artifact_dir:
                app.save_screenshot(filename="real-app-80x24-cw-ready.svg", path=artifact_dir)
            assert pane.query_one("#logs-body").region.height >= 1
            assert pane.query_one("#logs-sources").region.height == 1
            assert pane.query_one("#logs-filter").region.height == 1
            for _ in range(4):
                if logs.current_stream.attempt == 2:
                    break
                await pilot.press("right")
                await _drain(app)
                await pilot.pause()
            assert logs.current_stream.attempt == 2
            assert "/attempts/2/" in logs.current_stream.name
            calls = len(fake.calls)
            await pilot.press("f")
            await wait_until(lambda: isinstance(app.screen, LogFilterModal), what="filter modal")
            app.screen.query_one("#log-patterns", TextArea).load_text("ERROR")
            await pilot.click(
                next(
                    button
                    for button in app.screen.query(ModalButton)
                    if button.button_id == "apply"
                )
            )
            await _drain(app)
            await pilot.pause()
            assert logs.filter.patterns == ("ERROR",)
            assert len(fake.calls) == calls
            assert "filter: loaded data only" in str(pane.query_one("#logs-filter").render())
            await focus_and_settle(pane)
            await pilot.press("ctrl+alt+l")
            await wait_until(
                lambda: logs.following and logs.state is LogsState.READY, what="follow initial read"
            )
            await pilot.press("f")
            await wait_until(
                lambda: isinstance(app.screen, LogFilterModal), what="follow filter modal"
            )
            app.screen.query_one("#log-patterns", TextArea).load_text("ERROR|appended")
            now[0] += 2_000
            await ticks.put(None)
            await wait_until(
                lambda: logs.last_successful_read_at_ms == now[0], what="poll while filter is open"
            )
            calls = len(fake.calls)
            await pilot.click(
                next(
                    button
                    for button in app.screen.query(ModalButton)
                    if button.button_id == "apply"
                )
            )
            await wait_until(
                lambda: logs.filter.patterns == ("ERROR|appended",), what="filter while following"
            )
            assert logs.following
            assert len(fake.calls) == calls
            await focus_and_settle(pane)
            stamp = now[0]
            fake.append_cloudwatch_event(
                log_group_name=logs.cloudwatch_configuration.log_group_name,
                stream_name=logs.current_stream.name,
                event=CloudWatchLogEvent("appended", stamp, stamp, "ERROR appended once"),
            )
            now[0] += 2_000
            await ticks.put(None)
            await wait_until(
                lambda: "ERROR appended once" in logs.lines, what="follow appended event"
            )
            assert logs.lines.count("ERROR appended once") == 1
            assert logs.last_successful_read_at_ms == now[0]
            await pilot.press("ctrl+alt+l")
            await _drain(app)
            assert not logs.following
            assert not logs._operations.tasks
            assert logs.lines.count("ERROR appended once") == 1
    finally:
        await ctx.root_vm.content_host.shutdown()
        ctx.root_vm.dispose()
        ctx.log_sink.close()


async def test_leaving_service_waits_for_blocked_follow_cleanup(tmp_path: Path, monkeypatch):
    _forbid_aws(monkeypatch)
    ctx = build_app_context(config_dir=tmp_path / "config", cache_dir=tmp_path / "cache", demo=True)
    app = AwsTuiApp(ctx)
    entered, cleanup_started, release, closed = (asyncio.Event() for _ in range(4))
    try:
        async with app.run_test(size=(80, 24)) as pilot:
            page, old_logs, fake, _now = await _open_demo(tmp_path, monkeypatch, pilot, app)
            original = fake.read_cloudwatch_events

            class BlockedClientRead:
                closed = False

                async def __call__(self, **kwargs):
                    entered.set()
                    try:
                        await asyncio.Event().wait()
                    finally:
                        cleanup_started.set()
                        await release.wait()
                        self.closed = True
                        closed.set()
                    return await original(**kwargs)

            blocked_client = BlockedClientRead()
            monkeypatch.setattr(fake, "read_cloudwatch_events", blocked_client)
            await pilot.press("ctrl+alt+l")
            await asyncio.wait_for(entered.wait(), 2)
            assert old_logs.following
            rows = {
                r.id: r for r in app._project_discovery_actions(app._capture_discovery_origin())
            }
            assert rows["emr.logs.follow"].available
            assert "Stop follow" in str(app.query_one("#logs-status").render())
            # Navigate away while cleanup deliberately awaits an event.
            row = next(row for row in app.query(NavRow) if row.descriptor_id == "s3")
            await pilot.click(row)
            await asyncio.wait_for(cleanup_started.wait(), 2)
            assert not closed.is_set()
            release.set()
            await _drain(app)
            await pilot.pause()
            assert not old_logs.following
            assert not old_logs._operations.tasks
            assert closed.is_set()
            assert blocked_client.closed
            replacement = ctx.root_vm.content_host.current
            assert replacement is not page
            assert "old-run-sentinel" not in old_logs.lines
    finally:
        release.set()
        await ctx.root_vm.content_host.shutdown()
        ctx.root_vm.dispose()
        ctx.log_sink.close()


@pytest.mark.parametrize("rebound", [False, True])
async def test_log_commands_actual_keys_palette_hints_and_focus(tmp_path, monkeypatch, rebound):
    from textual.widgets import Input

    from aws_tui.infra.keymap_store import KeymapStore
    from aws_tui.ui.widgets.command_palette import CommandPalette
    from aws_tui.vm.emr_serverless.job_run_logs_vm import LogSource

    _forbid_aws(monkeypatch)
    ctx = build_app_context(config_dir=tmp_path / "config", cache_dir=tmp_path / "cache", demo=True)
    if rebound:
        ctx.keymap_store = KeymapStore(
            overlay={"emr.logs.source": ["ctrl+g"], "emr.logs.follow": ["ctrl+h"]}
        )
    app = AwsTuiApp(ctx)
    waiting = asyncio.Event()

    async def sleep(_delay):
        await waiting.wait()

    monkeypatch.setattr("aws_tui.vm.emr_serverless.job_run_logs_vm._sleep", sleep)
    try:
        async with app.run_test(size=(80, 24)) as pilot:
            page, logs, _fake, _now = await _open_demo(tmp_path, monkeypatch, pilot, app)
            both = next(
                run.job_run_id for run in page.job_runs.runs if run.job_run_id.endswith("both-logs")
            )
            await page.select_job_run(both)
            await focus_and_settle(app.query_one(JobRunLogsPane))
            app._populate_command_palette()
            rows = {
                r.id: r for r in app._project_discovery_actions(app._capture_discovery_origin())
            }
            assert rows["emr.logs.source"].available
            assert not rows["emr.logs.follow"].available
            assert rows["emr.logs.follow"].availability_reason == "selection_required"
            await pilot.press("ctrl+g" if rebound else "ctrl+s")
            await _drain(app)
            await pilot.pause()
            assert logs.selected_source is LogSource.CLOUDWATCH
            rows = {
                r.id: r for r in app._project_discovery_actions(app._capture_discovery_origin())
            }
            assert rows["emr.logs.follow"].available
            assert rows["emr.logs.follow"].effective_keys == (
                ("ctrl+h",) if rebound else ("ctrl+alt+l",)
            )
            await pilot.press("ctrl+h" if rebound else "ctrl+alt+l")
            await wait_until(
                lambda: logs.following and logs.state is LogsState.READY, what="bound follow key"
            )
            pane = app.query_one(JobRunLogsPane)
            assert ("ctrl+h" if rebound else "ctrl+alt+l") in str(
                pane.query_one("#logs-status").render()
            )
            # Stop through the real palette, restoring the captured logs focus.
            await pilot.press("ctrl+k")
            await wait_until(
                lambda: isinstance(app.screen, CommandPalette), what="real command palette"
            )
            app.screen.query_one(
                "#palette-input", Input
            ).value = "Start or stop following CloudWatch logs"
            await pilot.pause()
            assert app.screen.vm.filtered_entries[0].id == "emr.logs.follow"
            await pilot.press("enter")
            await wait_until(lambda: not logs.following, what="palette stop follow")
            await _drain(app)
            await focus_and_settle(app.query_one(JobRunsPane))
            rows = {
                r.id: r for r in app._project_discovery_actions(app._capture_discovery_origin())
            }
            assert rows["emr.logs.follow"].availability_reason == "focus_required"
            assert rows["emr.logs.source"].availability_reason == "focus_required"
            await pilot.press("ctrl+h" if rebound else "ctrl+alt+l")
            assert not logs.following
            await focus_and_settle(pane)
            # Mouse control dispatches the same follow path.
            await pilot.click("#logs-status")
            await wait_until(lambda: logs.following, what="mouse follow control")
            await pilot.click("#logs-status")
            await wait_until(lambda: not logs.following, what="mouse stop control")
            await _drain(app)
            # The resolved Commands hint invokes the same registered follow action.
            await pilot.resize_terminal(240, 40)
            await pilot.pause()
            from aws_tui.ui.widgets.hint_legend import _HintChip

            chips = [
                chip
                for chip in app.query(_HintChip)
                if chip.action.action_id == "emr.logs.follow" and chip.display
            ]
            assert len(chips) == 1
            assert chips[0].action.enabled
            await pilot.click(chips[0])
            await wait_until(lambda: logs.following, what="Commands mouse hint starts follow")
            await pilot.press("ctrl+h" if rebound else "ctrl+alt+l")
            await wait_until(lambda: not logs.following, what="bound key stops hint follow")
            await _drain(app)
            await pilot.click(next(r for r in app.query(NavRow) if r.descriptor_id == "s3"))
            await _drain(app)
            await pilot.press("ctrl+h" if rebound else "ctrl+alt+l")
            assert not logs.following
    finally:
        await ctx.root_vm.content_host.shutdown()
        ctx.root_vm.dispose()
        ctx.log_sink.close()
