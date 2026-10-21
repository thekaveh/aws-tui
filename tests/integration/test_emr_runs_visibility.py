"""Selected EMR runs stay visible through real application navigation."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from textual.containers import VerticalScroll

from aws_tui.app import AwsTuiApp
from aws_tui.composition import build_app_context
from aws_tui.demo.in_memory_emr import InMemoryEmr
from aws_tui.services.emr_serverless.service import EmrServerlessService
from aws_tui.ui.widgets.emr_serverless.job_runs_pane import JobRunsPane, _JobRunRow
from aws_tui.ui.widgets.emr_serverless.page import EmrServerlessPage
from tests.helpers import drain_workers, focus_and_settle, wait_until


@pytest.fixture(params=[(80, 24), (120, 40)], ids=["80x24", "120x40"])
async def mounted_runs(tmp_path, request):
    ctx = build_app_context(config_dir=tmp_path / "config", cache_dir=tmp_path / "cache", demo=True)
    fake = InMemoryEmr()
    fake.page_size = 30
    fake.add_application(app_id="visibility", name="visibility")
    now = datetime(2026, 1, 1, tzinfo=UTC)
    for index in range(45):
        run_id = f"audit-run-{index:03}"
        fake.add_job_run(
            application_id="visibility",
            job_run_id=run_id,
            name=run_id,
            created_at=now - timedelta(minutes=index),
        )
        fake.add_job_run_detail(application_id="visibility", job_run_id=run_id)
    for service in ctx.root_vm._registry.all():
        if isinstance(service, EmrServerlessService):
            service._client_factory = lambda _connection: fake
    app = AwsTuiApp(ctx)
    try:
        async with app.run_test(size=request.param) as pilot:
            await drain_workers(app)
            ctx.root_vm.services_menu.switch_service_command.execute("emr-serverless")
            await drain_workers(app)
            await wait_until(
                lambda: len(app.query(EmrServerlessPage)) == 1,
                what="actual EMR page mounted",
            )
            page = app.query_one(EmrServerlessPage)
            await wait_until(
                lambda: len(page.query(_JobRunRow)) == 30,
                what="first controlled page rendered",
            )
            await focus_and_settle(page.query_one(JobRunsPane))
            await pilot.pause()
            yield app, pilot, page, fake
    finally:
        ctx.root_vm.dispose()
        ctx.log_sink.close()


def _assert_visible_selection(page, expected_id):
    pane = page.query_one(JobRunsPane)
    selected = list(pane.query(".runs-row.-selected"))
    assert len(selected) == 1
    row = selected[0]
    assert row.run_id == expected_id == page.vm.job_runs.selected_id
    assert page.vm.job_run_detail.detail.job_run_id == expected_id
    body = pane.query_one("#runs-body", VerticalScroll)
    viewport = body.scrollable_content_region
    assert viewport.height > 0
    assert row.region.height == 1
    diagnostic = (
        f"selected {expected_id}: row={row.region}, viewport={viewport}, scroll_y={body.scroll_y}"
    )
    assert viewport.y <= row.region.y, diagnostic
    assert row.region.bottom <= viewport.bottom, diagnostic


async def test_real_arrows_keep_each_selected_run_visible_without_remount(mounted_runs):
    app, pilot, page, _fake = mounted_runs
    pane = page.query_one(JobRunsPane)
    rows = tuple(pane.query(_JobRunRow))
    _assert_visible_selection(page, "audit-run-000")
    for key, indices in [("down", range(1, 29)), ("up", range(27, -1, -1))]:
        for index in indices:
            await pilot.press(key)
            await drain_workers(app)
            await pilot.pause()
            _assert_visible_selection(page, f"audit-run-{index:03}")
            assert tuple(pane.query(_JobRunRow)) == rows


@pytest.mark.parametrize("operation", ["refresh", "more"])
async def test_refresh_and_pagination_restore_selected_run_visibility(mounted_runs, operation):
    app, pilot, page, _fake = mounted_runs
    await pilot.press(*(["down"] * 28))
    await drain_workers(app)
    await pilot.pause()
    assert page.vm.job_runs.selected_id == "audit-run-028"
    body = page.query_one("#runs-body", VerticalScroll)
    body.scroll_to(y=0, animate=False, immediate=True)
    await pilot.pause()
    if operation == "refresh":
        await pilot.press("r")
    else:
        page.query_one(JobRunsPane).action_load_more()
        await pilot.pause()
    await drain_workers(app)
    await pilot.pause()
    assert len(page.vm.job_runs.runs) == (30 if operation == "refresh" else 45)
    _assert_visible_selection(page, "audit-run-028")


async def test_resize_keeps_selected_run_visible_without_remount(mounted_runs):
    app, pilot, page, _fake = mounted_runs
    await pilot.press(*(["down"] * 28))
    await drain_workers(app)
    await pilot.pause()
    pane = page.query_one(JobRunsPane)
    rows = tuple(pane.query(_JobRunRow))
    width, height = (80, 24) if app.size.width == 120 else (120, 40)
    await pilot.resize_terminal(width, height)
    await pilot.pause()
    _assert_visible_selection(page, "audit-run-028")
    assert tuple(pane.query(_JobRunRow)) == rows
