"""The EMR detail focus slot exposes all overflowing configuration rows."""

from __future__ import annotations

import math
from typing import ClassVar

import pytest
from textual.app import ComposeResult
from textual.binding import BindingType
from textual.containers import VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button

from aws_tui.app import AwsTuiApp
from aws_tui.composition import build_app_context
from aws_tui.demo.in_memory_emr import InMemoryEmr
from aws_tui.services.emr_serverless.service import EmrServerlessService
from aws_tui.ui.widgets.emr_serverless.application_picker import ApplicationPicker
from aws_tui.ui.widgets.emr_serverless.job_run_detail_pane import JobRunDetailPane
from aws_tui.ui.widgets.emr_serverless.job_run_logs_pane import JobRunLogsPane
from aws_tui.ui.widgets.emr_serverless.job_runs_pane import JobRunsPane
from aws_tui.ui.widgets.emr_serverless.page import EmrServerlessPage
from aws_tui.ui.widgets.nav_menu import NavMenu
from aws_tui.ui.widgets.service_source_header import ServiceSourceHeader
from tests.helpers import drain_workers, focus_and_settle, wait_until


@pytest.fixture(params=[(80, 24), (120, 40)], ids=["80x24", "120x40"])
async def overflowing_detail(tmp_path, request):
    ctx = build_app_context(config_dir=tmp_path / "config", cache_dir=tmp_path / "cache", demo=True)
    fake = InMemoryEmr()
    fake.add_application(app_id="overflow", name="overflow")
    fake.add_job_run(application_id="overflow", job_run_id="selected")
    fake.add_job_run_detail(
        application_id="overflow",
        job_run_id="selected",
        entry_point_arguments=tuple(f"arg-{index:03}" for index in range(70)),
        spark_submit_parameters="--conf last=bottom",
    )
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
            await pilot.pause()
            await wait_until(
                lambda: len(app.query(".detail-row")) >= 77,
                what="all controlled overflowing detail rows mounted",
            )
            page = app.query_one(EmrServerlessPage)
            detail = page.query_one(JobRunDetailPane)
            body = detail.query_one("#detail-body", VerticalScroll)
            await wait_until(
                lambda: body.max_scroll_y > body.scrollable_content_region.height,
                what="overflowing detail completed layout",
            )
            assert body.max_scroll_y > body.scrollable_content_region.height
            # Positive control: the existing body really can expose overflow.
            body.scroll_end(animate=False, immediate=True)
            await pilot.pause()
            assert body.scroll_y == body.max_scroll_y
            body.scroll_home(animate=False, immediate=True)
            await pilot.pause()
            await focus_and_settle(page.query_one(JobRunsPane))
            await pilot.press("tab")
            await pilot.pause()
            assert detail.has_focus or detail.has_focus_within
            yield app, pilot, page, body
    finally:
        ctx.root_vm.dispose()
        ctx.log_sink.close()


def _assert_selected_run_unchanged(page):
    assert page.vm.job_runs.selected_id == "selected"
    assert page.vm.job_run_detail.detail.job_run_id == "selected"


async def test_arrows_and_vi_keys_scroll_detail_without_changing_run(overflowing_detail):
    _app, pilot, page, body = overflowing_detail
    for key in ["down", "j"]:
        before = body.scroll_y
        await pilot.press(key)
        await wait_until(
            lambda before=before: body.scroll_y > before, what=f"detail scrolled for {key}"
        )
        _assert_selected_run_unchanged(page)
    for key in ["up", "k"]:
        before = body.scroll_y
        await pilot.press(key)
        await wait_until(
            lambda before=before: body.scroll_y < before, what=f"detail scrolled for {key}"
        )
        _assert_selected_run_unchanged(page)


async def test_page_and_boundary_keys_reach_first_and_last_detail_rows(overflowing_detail):
    _app, pilot, page, body = overflowing_detail
    await pilot.press("end")
    await wait_until(lambda: body.scroll_y == body.max_scroll_y, what="detail End")
    await pilot.pause()
    last = list(body.query(".detail-row"))[-1]
    assert "last=bottom" in str(last.render())
    assert last.region.bottom <= body.scrollable_content_region.bottom
    _assert_selected_run_unchanged(page)
    await pilot.press("home")
    await wait_until(lambda: body.scroll_y == 0, what="detail Home")
    steps = math.ceil(body.max_scroll_y / body.scrollable_content_region.height) + 1
    for _ in range(steps):
        await pilot.press("pagedown")
        await pilot.pause()
        _assert_selected_run_unchanged(page)
    assert body.scroll_y == body.max_scroll_y
    for _ in range(steps):
        await pilot.press("pageup")
        await pilot.pause()
        _assert_selected_run_unchanged(page)
    assert body.scroll_y == 0
    await pilot.pause()
    first = next(iter(body.query(".detail-row")))
    assert first.region.y >= body.scrollable_content_region.y


async def test_detail_scrolling_preserves_forward_and_reverse_six_slot_cycle(overflowing_detail):
    app, pilot, page, _body = overflowing_detail
    detail = page.query_one(JobRunDetailPane)
    runs = page.query_one(JobRunsPane)
    logs = page.query_one(JobRunLogsPane)
    source = page.query_one(ServiceSourceHeader)
    application = page.query_one(ApplicationPicker)
    nav = app.query_one(NavMenu)
    for key, targets in [
        ("tab", [logs, nav, source, application, runs, detail]),
        ("shift+tab", [runs, application, source, nav, logs, detail]),
    ]:
        for target in targets:
            await pilot.press(key)
            await pilot.pause()
            assert target.has_focus or target.has_focus_within, (
                f"{key}: target={target!r}, focused={app.focused!r}, "
                f"slot={app.app_ctx.focus_coordinator.focused_slot}"
            )
            _assert_selected_run_unchanged(page)


class _FocusModal(ModalScreen[None]):
    BINDINGS: ClassVar[list[BindingType]] = [("escape", "dismiss", "Close")]

    def compose(self) -> ComposeResult:
        yield Button("Stay", id="modal-focus-target")


@pytest.mark.parametrize("successor", ["logs", "modal", "unmount"])
async def test_queued_detail_focus_does_not_replace_newer_intent(overflowing_detail, successor):
    app, pilot, page, body = overflowing_detail
    detail = page.query_one(JobRunDetailPane)
    # Screen focus changes synchronously; its Focus/Blur messages are queued.
    app.set_focus(detail)
    if successor == "logs":
        target = page.query_one(JobRunLogsPane)
        app.set_focus(target)
        await pilot.pause()
        assert app.focused is target
    elif successor == "modal":
        modal = _FocusModal()
        await app.push_screen(modal)
        await pilot.pause()
        assert app.screen is modal
        assert app.focused is modal.query_one(Button)
        await pilot.press("escape")
        await pilot.pause()
        assert app.screen is not modal
    else:
        await page.remove()
        detail.on_focus()
        await pilot.pause()
        assert not detail.is_attached
        assert app.focused is not body


async def test_nonoverflow_detail_keys_do_not_move_the_run(overflowing_detail):
    _app, pilot, page, body = overflowing_detail
    page.vm.client.add_job_run_detail(
        application_id="overflow",
        job_run_id="selected",
        entry_point_arguments=(),
        spark_submit_parameters=None,
    )
    await page.vm.refresh_job_run_detail()
    await pilot.resize_terminal(120, 60)
    await pilot.pause()
    assert body.max_scroll_y == 0
    for key in ["down", "j", "up", "k", "pagedown", "pageup", "home", "end"]:
        await pilot.press(key)
        await pilot.pause()
        assert body.scroll_y == 0
        _assert_selected_run_unchanged(page)
