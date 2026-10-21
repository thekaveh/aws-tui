"""Compact EMR rows preserve readable identity and keyboard inspection."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from textual.containers import VerticalScroll
from textual.geometry import Region

from aws_tui.app import AwsTuiApp
from aws_tui.composition import build_app_context
from aws_tui.demo.in_memory_emr import InMemoryEmr
from aws_tui.domain.emr_serverless import JobRunState
from aws_tui.domain.filesystem import ProviderError
from aws_tui.services.emr_serverless.service import EmrServerlessService
from aws_tui.ui.widgets.emr_serverless.job_run_detail_pane import JobRunDetailPane
from aws_tui.ui.widgets.emr_serverless.job_runs_pane import JobRunsPane, _JobRunRow
from aws_tui.ui.widgets.emr_serverless.page import EmrServerlessPage
from tests.helpers import drain_workers, focus_and_settle, wait_until

_NAMES = (
    "Alpha-nightly-[orders]-pipeline-with-a-long-name",
    "Beta-納品-[sales]-pipeline-with-a-long-name",
    None,
)
_IDS = tuple(f"run-{index:02}-complete-identity-123456789" for index in range(3))


@pytest.fixture(params=[(80, 24), (120, 40), (180, 40)], ids=["80x24", "120x40", "180x40"])
async def identity_page(tmp_path, request):
    ctx = build_app_context(config_dir=tmp_path / "config", cache_dir=tmp_path / "cache", demo=True)
    fake = InMemoryEmr()
    fake.add_application(app_id="identity", name="identity")
    for index, name in enumerate(_NAMES):
        fake.add_job_run(
            application_id="identity",
            job_run_id=_IDS[index],
            name=name,
            state=JobRunState.SUCCESS,
            created_at=datetime(2026, 6, 25, 12, 0, tzinfo=UTC) - timedelta(minutes=index),
        )
        fake.add_job_run_detail(application_id="identity", job_run_id=_IDS[index])
    for service in ctx.root_vm._registry.all():
        if isinstance(service, EmrServerlessService):
            service._client_factory = lambda _connection: fake
    app = AwsTuiApp(ctx)
    try:
        async with app.run_test(size=request.param) as pilot:
            await drain_workers(app)
            ctx.root_vm.services_menu.switch_service_command.execute("emr-serverless")
            await drain_workers(app)
            setup = ctx.root_vm.content_host._setup_task
            if setup is not None:
                await setup
            await wait_until(lambda: len(app.query(_JobRunRow)) == 3, what="controlled run rows")
            await pilot.pause()
            page = app.query_one(EmrServerlessPage)
            await focus_and_settle(page.query_one(JobRunsPane))
            yield app, pilot, page
    finally:
        ctx.root_vm.dispose()
        ctx.log_sink.close()


def _rendered(widget):
    # Textual render strips include padding; size is the content box.
    # Cropping to size.width would discard visible characters at the edge.
    return "".join(
        strip.text.strip()
        for strip in widget.render_lines(Region(0, 0, widget.region.width, widget.region.height))
    )


def _assert_readable_rows(page):
    rows = list(page.query(_JobRunRow))
    assert len(rows) == 3
    for index, row in enumerate(rows):
        name = row.query_one(".runs-cell-name")
        assert name.content_region.width >= 8, (row.region, name.content_region)
        expected = _NAMES[index] or _IDS[index]
        assert expected[:6] in _rendered(name)
        assert row.region.height == 1
        assert row.region.x <= name.region.x < name.region.right <= row.region.right
        date = row.query_one(".runs-cell-datetime")
        if date.display:
            assert name.region.right <= date.region.x
            assert date.region.right <= row.region.right
            assert date.content_region.width == 16


async def test_names_and_id_fallback_remain_readable_in_actual_rows(identity_page):
    _app, _pilot, page = identity_page
    _assert_readable_rows(page)


async def test_keyboard_detail_exposes_full_literal_identity_state_and_datetime(identity_page):
    app, pilot, page = identity_page
    await pilot.press("down")
    await drain_workers(app)
    await pilot.pause()
    assert page.vm.job_runs.selected_id == _IDS[1]
    await pilot.press("tab", "home")
    await pilot.pause()
    detail = page.query_one(JobRunDetailPane)
    assert detail.has_focus_within
    body = detail.query_one("#detail-body", VerticalScroll)
    rows = list(body.query(".detail-row"))
    name = next((r for r in rows if str(r.render()).startswith("Name ")), None)
    run_id = next((r for r in rows if str(r.render()).startswith("Run ID ")), None)
    assert name is not None
    assert run_id is not None
    assert _NAMES[1] in _rendered(name)
    assert _IDS[1] in _rendered(run_id)
    assert body.scrollable_content_region.contains_region(name.region)
    assert body.scrollable_content_region.contains_region(run_id.region)
    assert all(r.region.right <= body.scrollable_content_region.right for r in (name, run_id))
    # Reach every detail row through the ordinary key route, not hover or VM state.
    seen = set()
    for _ in range(int(body.max_scroll_y) + 2):
        for row in rows:
            if body.scrollable_content_region.contains_region(row.region):
                seen.add(_rendered(row))
        await pilot.press("down")
        await pilot.pause()
    assert any("SUCCESS" in text for text in seen)
    assert any("2026-06-25 11:59:00" in text for text in seen)
    assert page.vm.job_runs.selected_id == _IDS[1]


async def test_resize_preserves_selection_and_recovers_wide_datetime(identity_page):
    app, pilot, page = identity_page
    await pilot.press("down")
    await drain_workers(app)
    await pilot.pause()
    rows = tuple(page.query(_JobRunRow))
    for width, height in [(80, 24), (120, 40), (180, 40), (80, 24)]:
        await pilot.resize_terminal(width, height)
        await pilot.pause()
        _assert_readable_rows(page)
        assert tuple(page.query(_JobRunRow)) == rows
        assert page.vm.job_runs.selected_id == _IDS[1]
        assert page.vm.job_run_detail.detail.job_run_id == _IDS[1]
        if width == 180:
            date = rows[1].query_one(".runs-cell-datetime")
            assert date.display
            assert "2026-06-25 11:59" in _rendered(date)


async def test_detail_failure_keeps_loaded_identity_and_metadata_keyboard_accessible(identity_page):
    app, pilot, page = identity_page

    async def denied(_application_id, _job_run_id):
        raise ProviderError("detail access denied")

    page.vm.client.get_job_run = denied
    await pilot.press("down")
    await drain_workers(app)
    await pilot.pause()
    assert page.vm.job_run_detail.detail is None
    await pilot.press("tab", "home")
    await pilot.pause()
    detail = page.query_one(JobRunDetailPane)
    assert detail.has_focus_within
    rows = list(detail.query(".detail-row"))
    text = " ".join(_rendered(row) for row in rows)
    assert _NAMES[1] in text
    assert _IDS[1] in text
    assert "SUCCESS" in text
    assert "2026-06-25 11:59:00" in text
    placeholders = " ".join(str(row.render()) for row in detail.query(".detail-placeholder"))
    assert "From runs list" in placeholders
    assert "detail access denied" in placeholders
    assert page.vm.job_runs.selected_id == _IDS[1]


async def test_unnamed_detail_uses_full_id_as_identity(identity_page):
    app, pilot, page = identity_page
    await pilot.press("down", "down")
    await drain_workers(app)
    await pilot.pause()
    await pilot.press("tab", "home")
    await pilot.pause()
    rows = list(page.query_one(JobRunDetailPane).query(".detail-row"))
    name = next((row for row in rows if str(row.render()).startswith("Name ")), None)
    assert name is not None
    assert _IDS[2] in _rendered(name)


@pytest.mark.parametrize("missing_name", [None, ""])
async def test_detail_without_name_keeps_the_known_list_name(identity_page, missing_name):
    app, pilot, page = identity_page
    original = page.vm.client.get_job_run

    async def without_name(application_id, run_id):
        return replace(await original(application_id, run_id), name=missing_name)

    page.vm.client.get_job_run = without_name
    await pilot.press("down")
    await drain_workers(app)
    await pilot.pause()
    await pilot.press("tab", "home")
    await pilot.pause()
    rows = list(page.query_one(JobRunDetailPane).query(".detail-row"))
    name = next(row for row in rows if str(row.render()).startswith("Name "))
    assert _NAMES[1] in _rendered(name)
