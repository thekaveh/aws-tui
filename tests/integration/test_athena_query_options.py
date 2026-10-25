from dataclasses import replace

import pytest
from textual.widgets import Input, Static, Switch

from aws_tui.domain.query import QueryState
from aws_tui.ui.widgets.athena.query_view import AthenaQueryView
from tests.helpers import focus_and_settle, wait_until
from tests.integration.test_athena_page import _mounted_athena_app


async def open_options(app, pilot):
    await focus_and_settle(app.query_one("#athena-query-options"))
    await pilot.press("enter")
    await wait_until(lambda: bool(app.screen.query("#athena-reuse-enabled")), what="reuse form")
    await pilot.pause()
    return app.screen


async def test_query_reuse_modal_validates_age_applies_explicitly_and_cancels(tmp_path):
    async with _mounted_athena_app(tmp_path) as (app, _ctx, page, client, pilot):
        modal = await open_options(app, pilot)
        toggle = modal.query_one("#athena-reuse-enabled", Switch)
        await focus_and_settle(toggle)
        await pilot.press("space")
        modal.query_one("#athena-reuse-age", Input).value = "10081"
        await focus_and_settle(modal.query_one("#athena-reuse-apply"))
        await pilot.press("enter")
        assert "0 through 10080" in str(modal.query_one("#athena-reuse-error", Static).render())
        assert not page.query.reuse_enabled
        modal.query_one("#athena-reuse-age", Input).value = "0"
        await focus_and_settle(modal.query_one("#athena-reuse-apply"))
        await pilot.press("enter")
        await wait_until(lambda: app.screen is not modal, what="reuse applied")
        assert page.query.reuse_enabled
        assert page.query.reuse_max_age_minutes == 0
        modal = await open_options(app, pilot)
        modal.query_one("#athena-reuse-age", Input).value = "60"
        await pilot.press("escape")
        assert page.query.reuse_max_age_minutes == 0
        assert client.start_calls == []


async def test_managed_workgroup_disables_reuse_with_explanation(tmp_path):
    async with _mounted_athena_app(tmp_path) as (app, _ctx, page, client, pilot):
        await page.select_workgroup("analysts")
        await pilot.pause()
        modal = await open_options(app, pilot)
        assert modal.query_one("#athena-reuse-enabled", Switch).disabled
        assert "managed" in str(modal.query_one("#athena-reuse-reason", Static).render()).lower()
        assert client.start_calls == []


@pytest.mark.parametrize(
    ("cutoff", "expected"), [(10000000, "10000000 bytes"), (None, "Not reported"), (0, "Invalid")]
)
async def test_query_details_show_truthful_cutoff_states(tmp_path, cutoff, expected):
    async with _mounted_athena_app(tmp_path) as (app, _ctx, page, _client, _pilot):
        page._workgroup_detail = replace(page.workgroup_detail, bytes_scanned_cutoff=cutoff)
        view = app.query_one(AthenaQueryView)
        assert expected in view._detail_text()
        assert "Scan cutoff" in view._detail_text()


async def test_context_change_during_options_cannot_apply_to_other_workgroup(tmp_path):
    async with _mounted_athena_app(tmp_path) as (app, _ctx, page, client, pilot):
        modal = await open_options(app, pilot)
        modal.query_one("#athena-reuse-enabled", Switch).value = True
        await page.select_workgroup("analysts")
        await focus_and_settle(modal.query_one("#athena-reuse-apply"))
        await pilot.press("enter")
        await wait_until(
            lambda: (
                "context changed" in str(modal.query_one("#athena-reuse-error", Static).render())
            ),
            what="stale options refusal",
        )
        assert not page.query.reuse_enabled
        assert client.start_calls == []
        await pilot.press("escape")


@pytest.mark.parametrize("reused", [True, False])
async def test_real_query_flow_sends_options_and_reports_actual_reuse(tmp_path, reused):
    async with _mounted_athena_app(tmp_path) as (app, _ctx, page, client, pilot):
        requests = []
        original_start = client.start_query
        original_detail = client.get_query_execution

        async def start(sql, context, *, request_token, result_reuse_max_age_minutes=None):
            requests.append((context, result_reuse_max_age_minutes))
            return await original_start(sql, context, request_token=request_token)

        async def detail(execution_id):
            value = await original_detail(execution_id)
            return replace(
                value,
                statistics=replace(
                    value.statistics, reused_previous_result=reused, bytes_scanned=0
                ),
            )

        client.start_query = start
        client.get_query_execution = detail
        modal = await open_options(app, pilot)
        modal.query_one("#athena-reuse-enabled", Switch).value = True
        modal.query_one("#athena-reuse-age", Input).value = "42"
        await focus_and_settle(modal.query_one("#athena-reuse-apply"))
        await pilot.press("enter")
        await wait_until(lambda: app.screen is not modal, what="options applied")
        page.query.set_sql("SELECT 1")
        await page.query.execute()
        assert requests == [(page.context, 42)]
        assert page.query.state is QueryState.SUCCEEDED
        assert page.query.statistics.reused_previous_result is reused
        view = app.query_one(AthenaQueryView)
        assert ("Reused result   Yes" if reused else "Reused result   No") in view._detail_text()
        assert "Bytes scanned   0" in view._detail_text()


async def test_cutoff_from_another_workgroup_is_not_displayed_as_current(tmp_path):
    async with _mounted_athena_app(tmp_path) as (app, _ctx, page, _client, _pilot):
        page._workgroup_detail = replace(
            page.workgroup_detail,
            summary=replace(page.workgroup_detail.summary, name="other"),
            bytes_scanned_cutoff=10000000,
        )
        text = app.query_one(AthenaQueryView)._detail_text()
        assert "Scan cutoff     Unavailable" in text
        assert "10000000 bytes" not in text


@pytest.mark.parametrize("size", [(80, 24), (120, 40)])
async def test_actual_demo_reuse_submission_and_modal_fit_at_supported_sizes(tmp_path, size):
    from pathlib import Path

    from textual.widgets import TextArea

    from aws_tui.app import AwsTuiApp
    from aws_tui.composition import build_app_context
    from tests.helpers import drain_workers
    from tests.integration.test_glue_page import open_service

    ctx = build_app_context(config_dir=tmp_path / "config", cache_dir=tmp_path / "cache", demo=True)
    app = AwsTuiApp(ctx)
    try:
        async with app.run_test(size=size) as pilot:
            await open_service(ctx, pilot, "athena")
            page = ctx.root_vm.content_host.current
            modal = await open_options(app, pilot)
            apply = modal.query_one("#athena-reuse-apply")
            assert apply.region.width > 0
            assert apply.region.bottom <= size[1]
            assert apply.region.x >= 0
            assert apply.region.right <= size[0]
            Path(f"/tmp/aws-tui-257-modal-{size[0]}.svg").write_text(app.export_screenshot())
            modal.query_one("#athena-reuse-enabled", Switch).value = True
            await focus_and_settle(apply)
            await pilot.press("enter")
            await wait_until(lambda: app.screen is not modal, what="demo reuse applied")
            page.query.set_sql("SELECT 1")
            await pilot.pause()
            editor = app.query_one("#athena-editor", TextArea)
            assert editor.region.height >= 3
            await focus_and_settle(editor)
            await pilot.press("ctrl+enter")
            await drain_workers(app)
            assert page.query.state is QueryState.SUCCEEDED
            assert not page.query.statistics.reused_previous_result
            requests = [call for call in page._client.calls if call.method == "start_query"]
            assert requests[-1].arguments[-1] == 60
            assert app._crash_report is None
    finally:
        await ctx.root_vm.content_host.shutdown()
        ctx.root_vm.dispose()
        ctx.log_sink.close()


async def test_missing_workgroup_metadata_is_unavailable_rather_than_unlimited(tmp_path):
    async with _mounted_athena_app(tmp_path) as (app, _ctx, page, _client, _pilot):
        page._workgroup_detail = None
        text = app.query_one(AthenaQueryView)._detail_text()
        assert "Scan cutoff     Unavailable" in text
        assert "Not reported" not in text
        assert "unlimited" not in text.lower()
