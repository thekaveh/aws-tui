"""Blank job authoring uses the real app and controlled composition providers."""

from __future__ import annotations

import contextlib

import pytest
from textual.widgets import Input, TextArea

from aws_tui.app import AwsTuiApp
from aws_tui.composition import build_app_context
from aws_tui.demo.in_memory_emr import InMemoryEmr
from aws_tui.demo.in_memory_fs import InMemoryFS
from aws_tui.infra.aws_session import TokenProbeResult, TokenState
from aws_tui.ui.widgets.emr_serverless.clone_modal import JobRunCloneModal
from aws_tui.vm.chrome.action_catalog import ACTION_SPECS
from tests.helpers import drain_workers, focus_and_settle, wait_until
from tests.integration.test_glue_page import open_service


def context(
    tmp_path,
    monkeypatch,
    *,
    app_type="SPARK",
    remap=False,
    protected=False,
    include_application=True,
):
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    text = '[connections.synthetic]\nkind="aws"\nprofile="synthetic"\nregion="us-east-1"\n'
    text += f'protected={str(protected).lower()}\n[defaults]\nconnection="synthetic"\n'
    if remap:
        text += '[keybindings]\n"emr.new_job"="B"\n'
    (config_dir / "config.toml").write_text(text)
    aws = tmp_path / "aws-config"
    aws.write_text("[profile synthetic]\nregion=us-east-1\n")
    monkeypatch.setenv("AWS_CONFIG_FILE", str(aws))
    monkeypatch.setenv("AWS_SHARED_CREDENTIALS_FILE", str(tmp_path / "no-credentials"))
    fake = InMemoryEmr()
    if include_application:
        fake.add_application(
            app_id="application001", name="zero-run application", app_type=app_type
        )
    ctx = build_app_context(config_dir=config_dir, cache_dir=tmp_path / "cache")
    ctx.registry.get("s3")._s3_fs_factory = lambda _: InMemoryFS()
    ctx.registry.get("emr-serverless")._client_factory = lambda _: fake
    monkeypatch.setattr(
        ctx.aws_session, "probe_token", lambda _: TokenProbeResult(TokenState.CONNECTED)
    )
    return ctx, fake


async def start_form(app, pilot, ctx, route):
    if route == "palette":
        await pilot.press("colon", *"New Spark job")
        await pilot.pause()
        assert [row.id for row in ctx.command_palette_vm.filtered_entries] == ["emr.new_job"]
        await pilot.press("enter")
    else:
        await pilot.press("B" if route == "remap" else "N")
    await wait_until(
        lambda: isinstance(app.screen, JobRunCloneModal), timeout=2, what="blank Spark form"
    )
    await pilot.pause()


@pytest.mark.parametrize("route", ["key", "remap", "palette"])
async def test_zero_run_application_can_review_and_submit_new_spark_job(
    tmp_path, monkeypatch, route
):
    ctx, fake = context(tmp_path, monkeypatch, remap=route == "remap")
    app = AwsTuiApp(ctx)
    try:
        assert app._actions.has("emr.new_job")
        assert next(
            row for row in ACTION_SPECS if row.id == "emr.new_job"
        ).service_ids == frozenset({"emr-serverless"})
        async with app.run_test(size=(80, 24)) as pilot:
            await open_service(ctx, pilot, "emr-serverless")
            page = ctx.root_vm.content_host.current
            assert page.job_runs.runs == ()
            assert page.job_run_detail.detail is None
            await start_form(app, pilot, ctx, route)
            modal = app.screen
            assert modal.vm.is_new_job
            assert modal.vm.source_detail is None
            assert modal.query_one("#clone-role", Input).value == ""
            assert modal.query_one("#clone-entry", Input).value == ""
            modal.query_one("#clone-role", Input).value = "arn:aws:iam::123456789012:role/Synthetic"
            modal.query_one("#clone-entry", Input).value = "s3://synthetic/job.py"
            modal.query_one("#clone-args", TextArea).load_text(
                '["a b", "日本語\\nline", "\\"quoted\\""]'
            )
            await focus_and_settle(modal.query_one("#clone-review-button"))
            await pilot.press("enter")
            await wait_until(lambda: modal.reviewing, what="review new Spark request")
            assert not [call for call in fake.calls if call[0] == "start_job_run"]
            await focus_and_settle(modal.query_one("#clone-submit"))
            await pilot.press("enter")
            await wait_until(lambda: app.screen is not modal, what="new job submitted")
            await drain_workers(app)
            calls = [call[1] for call in fake.calls if call[0] == "start_job_run"]
            assert len(calls) == 1
            assert calls[0][0] == "application001"
            assert calls[0][3] == ("a b", "日本語\nline", '"quoted"')
            assert page.job_runs.selected_id in {run.job_run_id for run in page.job_runs.runs}
            assert len(page.job_runs.runs) == 1
            assert app._crash_report is None
    finally:
        with contextlib.suppress(Exception):
            ctx.root_vm.dispose()
        fake.dispose()


async def review_valid(app, pilot):
    modal = app.screen
    modal.query_one("#clone-role", Input).value = "arn:aws:iam::123456789012:role/Synthetic"
    modal.query_one("#clone-entry", Input).value = "s3://synthetic/job.py"
    await focus_and_settle(modal.query_one("#clone-review-button"))
    await pilot.press("enter")
    await wait_until(lambda: modal.reviewing, what="blank request reviewed")
    return modal


@pytest.mark.parametrize("app_type", ["HIVE", "unknown"])
async def test_non_spark_application_has_no_new_job_action(tmp_path, monkeypatch, app_type):
    ctx, fake = context(tmp_path, monkeypatch, app_type=app_type)
    app = AwsTuiApp(ctx)
    try:
        async with app.run_test() as pilot:
            await open_service(ctx, pilot, "emr-serverless")
            rows = {
                row.id: row
                for row in app._project_discovery_actions(app._capture_discovery_origin())
            }
            assert not rows["emr.new_job"].available
            await pilot.press("N")
            await app._actions.invoke("emr.new_job")
            await pilot.pause()
            assert len(app.screen_stack) == 1
            assert not [call for call in fake.calls if call[0] == "start_job_run"]
    finally:
        ctx.root_vm.dispose()
        fake.dispose()


@pytest.mark.parametrize("stage", ["input", "review"])
async def test_cancel_new_job_never_submits(tmp_path, monkeypatch, stage):
    ctx, fake = context(tmp_path, monkeypatch)
    app = AwsTuiApp(ctx)
    try:
        async with app.run_test(size=(80, 24)) as pilot:
            await open_service(ctx, pilot, "emr-serverless")
            await start_form(app, pilot, ctx, "key")
            modal = app.screen
            if stage == "review":
                await review_valid(app, pilot)
            await pilot.press("escape")
            await drain_workers(app)
            assert not [call for call in fake.calls if call[0] == "start_job_run"]
            assert modal.vm._disposed
    finally:
        ctx.root_vm.dispose()
        fake.dispose()


async def test_protection_enabled_after_review_blocks_start_and_retains_values(
    tmp_path, monkeypatch
):
    from dataclasses import replace

    from textual.widgets import Static

    from aws_tui.domain.mutation_guard import PROTECTED_MESSAGE

    ctx, fake = context(tmp_path, monkeypatch)
    app = AwsTuiApp(ctx)
    try:
        async with app.run_test(size=(80, 24)) as pilot:
            await open_service(ctx, pilot, "emr-serverless")
            await start_form(app, pilot, ctx, "key")
            modal = await review_valid(app, pilot)
            entry = ctx.config_store.load().connections["synthetic"]
            ctx.config_store.update_connection("synthetic", replace(entry, protected=True))
            await modal.action_submit()
            assert not [call for call in fake.calls if call[0] == "start_job_run"]
            assert PROTECTED_MESSAGE in str(modal.query_one("#clone-error", Static).render())
            assert modal.query_one("#clone-entry", Input).value == "s3://synthetic/job.py"
            ctx.config_store.update_connection("synthetic", entry)
            await pilot.pause()
            assert not [call for call in fake.calls if call[0] == "start_job_run"]
            await pilot.press("escape")
    finally:
        ctx.root_vm.dispose()
        fake.dispose()


async def test_access_denial_retains_request_and_hides_provider_echo(tmp_path, monkeypatch):
    from textual.widgets import Static

    from aws_tui.domain.filesystem import PermissionDeniedError

    ctx, fake = context(tmp_path, monkeypatch)
    fake.start_job_run_exc = PermissionDeniedError("PRIVATE_UNTRUSTED_REQUEST")
    app = AwsTuiApp(ctx)
    try:
        async with app.run_test(size=(80, 24)) as pilot:
            await open_service(ctx, pilot, "emr-serverless")
            await start_form(app, pilot, ctx, "key")
            modal = await review_valid(app, pilot)
            token = modal.vm.client_token
            await modal.action_submit()
            error = str(modal.query_one("#clone-error", Static).render())
            assert "Access denied" in error
            assert "PRIVATE_UNTRUSTED_REQUEST" not in error
            assert modal.reviewing
            assert modal.vm.client_token == token
            assert modal.query_one("#clone-entry", Input).value == "s3://synthetic/job.py"
            ctx.log_sink.flush()
            assert "PRIVATE_UNTRUSTED_REQUEST" not in ctx.log_sink.path.read_text()
            await pilot.press("escape")
    finally:
        ctx.root_vm.dispose()
        fake.dispose()


async def test_application_change_during_review_cannot_retarget_submit(tmp_path, monkeypatch):
    from textual.widgets import Static

    ctx, fake = context(tmp_path, monkeypatch)
    fake.add_application(app_id="application002", name="second")
    app = AwsTuiApp(ctx)
    try:
        async with app.run_test() as pilot:
            await open_service(ctx, pilot, "emr-serverless")
            page = ctx.root_vm.content_host.current
            await page.select_application("application001")
            await start_form(app, pilot, ctx, "key")
            modal = await review_valid(app, pilot)
            await page.select_application("application002")
            await modal.action_submit()
            assert "source changed" in str(modal.query_one("#clone-error", Static).render()).lower()
            assert not [call for call in fake.calls if call[0] == "start_job_run"]
            await pilot.press("escape")
    finally:
        ctx.root_vm.dispose()
        fake.dispose()


async def test_accepted_late_response_never_refreshes_or_selects_the_new_application(
    tmp_path, monkeypatch
):
    import asyncio

    ctx, fake = context(tmp_path, monkeypatch)
    fake.add_application(app_id="application002", name="second")
    original = fake.start_job_run
    entered, release = asyncio.Event(), asyncio.Event()
    accepted_ids = []

    async def accepted_then_delayed(*args, **kwargs):
        result = await original(*args, **kwargs)
        accepted_ids.append(result)
        entered.set()
        await release.wait()
        return result

    monkeypatch.setattr(fake, "start_job_run", accepted_then_delayed)
    app = AwsTuiApp(ctx)
    try:
        async with app.run_test(size=(80, 24)) as pilot:
            await open_service(ctx, pilot, "emr-serverless")
            page = ctx.root_vm.content_host.current
            await page.select_application("application001")
            await start_form(app, pilot, ctx, "key")
            modal = await review_valid(app, pilot)
            await focus_and_settle(modal.query_one("#clone-submit"))
            await pilot.press("enter")
            await asyncio.wait_for(entered.wait(), 2)
            await page.select_application("application002")
            calls_after_switch = len(fake.calls)
            release.set()
            await wait_until(lambda: app.screen is not modal, what="late accepted form closed")
            await drain_workers(app)
            assert page.applications.selected_id == "application002"
            assert page.job_runs.application_id == "application002"
            assert page.job_runs.runs == ()
            assert page.job_run_detail.detail is None
            assert not [
                call
                for call in fake.calls[calls_after_switch:]
                if call[0].startswith("list_job_runs")
            ]
            submits = [call[1] for call in fake.calls if call[0] == "start_job_run"]
            assert len(submits) == 1
            assert submits[0][0] == "application001"
            assert modal.vm.submitted_id is None
            assert any(
                accepted_ids[0] in toast.model.text and "application001" in toast.model.text
                for toast in ctx.root_vm.chrome.toast_stack.toasts
            )
            assert app._crash_report is None
    finally:
        release.set()
        ctx.root_vm.dispose()
        fake.dispose()


async def test_blank_submission_works_in_actual_demo_composition(tmp_path):
    ctx = build_app_context(config_dir=tmp_path / "config", cache_dir=tmp_path / "cache", demo=True)
    app = AwsTuiApp(ctx)
    try:
        async with app.run_test(size=(80, 24)) as pilot:
            await open_service(ctx, pilot, "emr-serverless")
            page = ctx.root_vm.content_host.current
            client = page.client
            assert isinstance(client, InMemoryEmr)
            await start_form(app, pilot, ctx, "key")
            modal = await review_valid(app, pilot)
            assert modal.vm.source_detail is None
            await focus_and_settle(modal.query_one("#clone-submit"))
            await pilot.press("enter")
            await wait_until(lambda: app.screen is not modal, what="demo new job submitted")
            await drain_workers(app)
            assert len([call for call in client.calls if call[0] == "start_job_run"]) == 1
            assert app._crash_report is None
    finally:
        ctx.root_vm.dispose()


async def test_success_refresh_waiting_on_lock_cannot_read_new_application(tmp_path, monkeypatch):
    import asyncio

    ctx, fake = context(tmp_path, monkeypatch)
    fake.add_application(app_id="application002", name="second")
    fake.add_job_run_detail(application_id="application002", job_run_id="existing002")
    app = AwsTuiApp(ctx)
    switch = None
    lock = None
    try:
        async with app.run_test(size=(80, 24)) as pilot:
            await open_service(ctx, pilot, "emr-serverless")
            page = ctx.root_vm.content_host.current
            await page.select_application("application001")
            await start_form(app, pilot, ctx, "key")
            modal = await review_valid(app, pilot)
            lock = page.job_runs._paging_lock
            await lock.acquire()
            try:
                await focus_and_settle(modal.query_one("#clone-submit"))
                await pilot.press("enter")
                await wait_until(lambda: app.screen is not modal, what="submitted form dismissed")
                await wait_until(
                    lambda: bool(lock._waiters), what="original application refresh queued"
                )
                switch = asyncio.create_task(page.select_application("application002"))
                await wait_until(
                    lambda: page.job_runs.application_id == "application002",
                    what="new application bound",
                )
                before = len(fake.calls)
            finally:
                lock.release()
            await switch
            await drain_workers(app)
            reads = [call for call in fake.calls[before:] if call[0] == "list_job_runs_page"]
            assert len(reads) == 1
            assert reads[0][1][0] == "application002"
            assert page.applications.selected_id == "application002"
            assert page.job_runs.selected_id == "existing002"
            assert app._crash_report is None
    finally:
        if lock is not None and lock.locked():
            lock.release()
        if switch is not None:
            await asyncio.gather(switch, return_exceptions=True)
        ctx.root_vm.dispose()
        fake.dispose()


async def test_missing_application_cannot_open_new_job_form(tmp_path, monkeypatch):
    ctx, fake = context(tmp_path, monkeypatch, include_application=False)
    app = AwsTuiApp(ctx)
    try:
        async with app.run_test() as pilot:
            await open_service(ctx, pilot, "emr-serverless")
            assert ctx.root_vm.content_host.current.applications.selected_id is None
            rows = {
                row.id: row
                for row in app._project_discovery_actions(app._capture_discovery_origin())
            }
            assert not rows["emr.new_job"].available
            await pilot.press("N")
            await app._actions.invoke("emr.new_job")
            await pilot.pause()
            assert len(app.screen_stack) == 1
            assert not [call for call in fake.calls if call[0] == "start_job_run"]
    finally:
        ctx.root_vm.dispose()
        fake.dispose()
