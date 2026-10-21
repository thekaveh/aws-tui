"""Filters recover retained EMR runs after manual and periodic refresh."""

from __future__ import annotations

import pytest

from aws_tui.app import AwsTuiApp
from aws_tui.composition import build_app_context
from aws_tui.demo.in_memory_emr import InMemoryEmr
from aws_tui.domain.emr_serverless import JobRunState
from aws_tui.services.emr_serverless.service import EmrServerlessService
from aws_tui.ui.widgets.emr_serverless.job_runs_pane import JobRunsPane, _JobRunRow
from aws_tui.ui.widgets.emr_serverless.page import EmrServerlessPage
from aws_tui.vm.file_manager.pane_vm import PaneState
from tests.helpers import drain_workers, focus_and_settle, wait_until


@pytest.mark.parametrize(("state", "key"), [(JobRunState.SUCCESS, "1"), (JobRunState.RUNNING, "2")])
@pytest.mark.parametrize("refresh", ["keyboard", "periodic"])
async def test_restored_filter_reveals_retained_run_without_provider_request(
    tmp_path, monkeypatch, state, key, refresh
):
    # Deliver registered periodic callbacks explicitly so unrelated wall-clock
    # ticks cannot contaminate the provider-call assertion on a slow machine.
    poll_callbacks = {}

    def capture_interval(_page, _interval, callback, *, name):
        poll_callbacks[name] = callback

    monkeypatch.setattr(EmrServerlessPage, "set_interval", capture_interval)
    ctx = build_app_context(config_dir=tmp_path / "config", cache_dir=tmp_path / "cache", demo=True)
    fake = InMemoryEmr()
    fake.add_application(app_id="filtered", name="filtered")
    fake.add_job_run(application_id="filtered", job_run_id="retained", state=state)
    fake.add_job_run_detail(application_id="filtered", job_run_id="retained")
    for service in ctx.root_vm._registry.all():
        if isinstance(service, EmrServerlessService):
            service._client_factory = lambda _connection: fake
    app = AwsTuiApp(ctx)
    try:
        async with app.run_test(size=(80, 24)) as pilot:
            await drain_workers(app)
            ctx.root_vm.services_menu.switch_service_command.execute("emr-serverless")
            await drain_workers(app)
            await wait_until(
                lambda: len(app.query(_JobRunRow)) == 1,
                what="the sole controlled EMR run mounted",
            )
            page = app.query_one(EmrServerlessPage)
            pane = page.query_one(JobRunsPane)
            await focus_and_settle(pane)
            await pilot.pause()
            assert page.vm.job_run_detail.detail.job_run_id == "retained"
            await pilot.press(key)
            await pilot.pause()
            assert page.vm.job_runs.runs == ()
            assert not pane.query(_JobRunRow)
            before_refresh = len(fake.calls)
            if refresh == "keyboard":
                await pilot.press("r")
            else:
                for _ in range(6 if state is JobRunState.SUCCESS else 1):
                    poll_callbacks["emr-poll-runs"]()
            await pilot.pause()
            await drain_workers(app)
            await pilot.pause()
            assert len(fake.calls) > before_refresh
            calls_after_refresh = tuple(fake.calls)
            await pilot.press(key)
            await pilot.pause()
            rows = list(pane.query(_JobRunRow))
            assert len(rows) == 1
            assert tuple(fake.calls) == calls_after_refresh
            assert page.vm.job_runs.state is PaneState.IDLE
            assert rows[0].run_id == page.vm.job_runs.selected_id == "retained"
            assert page.vm.job_run_detail.detail is not None
            assert page.vm.job_run_detail.detail.job_run_id == "retained"
    finally:
        ctx.root_vm.dispose()
        ctx.log_sink.close()
