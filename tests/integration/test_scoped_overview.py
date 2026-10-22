"""Scoped overview entered through the real bottom command palette."""

import asyncio
from dataclasses import replace

import pytest
from textual.widgets import OptionList, Static, TextArea

from aws_tui.ui.widgets.transfers_overlay import TransferRowWidget
from aws_tui.vm.file_manager.transfer_vm import TransferModel, TransferState
from aws_tui.vm.messages import ConnectionListChangedMessage
from tests.helpers import drain_workers, wait_until
from tests.integration.test_workspace_manager import focus_with_tabs, make_workspace_app
from tests.unit.domain.test_overview_client import CountedSession, execution


def install_services(ctx):
    from aws_tui.demo.in_memory_emr import InMemoryEmr
    from aws_tui.services.athena import AthenaService
    from aws_tui.services.emr_serverless import EmrServerlessService
    from aws_tui.services.glue import GlueService
    from tests.unit.vm.athena.test_page_vm import PageClient
    from tests.unit.vm.glue._fake_glue import InMemoryGlue

    athena = PageClient(connection_name="dev", region="us-east-1")
    glue = InMemoryGlue()
    glue.add_job("chosen")
    glue.add_run("chosen", "chosen-run")
    glue.add_crawler("chosen")
    emr = InMemoryEmr()
    emr.add_application(app_id="chosen", name="Chosen")
    emr.add_job_run(application_id="chosen", job_run_id="chosen-run")
    emr.add_job_run_detail(application_id="chosen", job_run_id="chosen-run")
    ports = dict(hub=ctx.hub, dispatcher=ctx.dispatcher)
    for service in (
        AthenaService(**ports, aws_session=ctx.aws_session, athena_client_factory=lambda _: athena),
        GlueService(**ports, aws_session=ctx.aws_session, glue_client_factory=lambda _: glue),
        EmrServerlessService(**ports, emr_client_factory=lambda _: emr),
    ):
        ctx.registry.register(service)
    return athena, glue, emr


async def choose_scope(app, ctx, pilot, kind):
    service_id = "glue" if kind.startswith("glue-") else kind
    ctx.root_vm.services_menu.switch_service_command.execute(service_id)
    await drain_workers(app)
    await wait_until(
        lambda: ctx.root_vm.content_host.current_id == service_id, what="service selection"
    )
    await pilot.pause()
    page = ctx.root_vm.content_host.current
    if kind == "athena":
        await page.select_workgroup("analysts")
    elif kind == "glue-job":
        await page.select_view("jobs")
        await page.select_job("chosen")
    elif kind == "glue-crawler":
        await page.select_view("crawlers")
        await page.select_crawler("chosen")
    else:
        await page.select_application("chosen")
    await drain_workers(app)
    await pilot.pause()
    return page


def wire_session():
    return CountedSession(
        list_query_executions={"QueryExecutionIds": ["history-analysts"]},
        batch_get_query_execution={
            "QueryExecutions": [execution("history-analysts", "FAILED", "analysts")]
        },
        get_job_runs={
            "JobRuns": [{"Id": "chosen-run", "JobName": "chosen", "JobRunState": "FAILED"}]
        },
        get_crawler={"Crawler": {"Name": "chosen", "State": "READY"}},
        list_job_runs={
            "jobRuns": [{"id": "chosen-run", "applicationId": "chosen", "state": "FAILED"}]
        },
    )


async def ready_entry(app, pilot):
    await open_overview(app, pilot)
    vm = app.screen.vm
    await focus_with_tabs(app, pilot, "overview-add")
    await pilot.press("enter")
    await focus_with_tabs(app, pilot, "overview-refresh")
    await pilot.press("enter")
    await wait_until(lambda: vm.sections[0].state == "ready", what="ready watched scope")
    entry = vm.sections[0].page.entries[0]
    await pilot.press("escape")
    await wait_until(lambda: len(app.screen_stack) == 1, what="overview closed")
    return vm, entry


async def test_invalidated_source_is_visible_and_cannot_open_its_ready_entry(tmp_path, monkeypatch):
    from aws_tui.ui.widgets.modal_button import ModalButton

    app, ctx = make_workspace_app(tmp_path, monkeypatch)
    try:
        async with app.run_test() as pilot:
            await drain_workers(app)
            ctx.transfers_vm.register(
                TransferModel(
                    "chosen", "upload", "fixture", "s3://fixture", 5, 5, TransferState.COMPLETED
                )
            )
            vm, _entry = await ready_entry(app, pilot)
            await open_overview(app, pilot)
            await focus_with_tabs(app, pilot, "overview-list")
            await pilot.press("down")
            assert not app.screen.query_one("#overview-open", ModalButton).disabled
            ctx.hub.send(ConnectionListChangedMessage((vm.connection.name,), "updated"))
            await pilot.pause()
            assert app.screen.query_one("#overview-open", ModalButton).disabled
            assert (
                "reopen" in str(app.screen.query_one("#overview-status", Static).render()).lower()
            )
            await pilot.press("enter")
            assert bool(app.screen.query("#overview-list"))
            assert ctx.root_vm.content_host.current_id == "s3"
    finally:
        ctx.root_vm.dispose()
        ctx.log_sink.close()


@pytest.mark.parametrize("change", ["source-aba", "config-aba"])
async def test_source_event_latches_even_when_original_identity_returns(
    tmp_path, monkeypatch, change
):
    from aws_tui.infra.aws_session import TokenState

    app, ctx = make_workspace_app(tmp_path, monkeypatch)
    try:
        async with app.run_test() as pilot:
            await drain_workers(app)
            await open_overview(app, pilot)
            old = app.screen.vm
            await pilot.press("escape")
            await wait_until(lambda: len(app.screen_stack) == 1, what="overview closed")
            connection = ctx.root_vm.active_connection
            if change == "source-aba":
                await ctx.root_vm.switch_connection_with(
                    replace(connection, region="eu-west-1"), TokenState.CONNECTED
                )
                await ctx.root_vm.switch_connection_with(connection, TokenState.CONNECTED)
            else:
                ctx.hub.send(ConnectionListChangedMessage((connection.name,), "updated"))
                ctx.hub.send(ConnectionListChangedMessage((connection.name,), "updated"))
            await open_overview(app, pilot)
            assert app.screen.vm is not old
            assert app.screen.vm.sections == ()
            assert not await old.source_is_current()
    finally:
        ctx.root_vm.dispose()
        ctx.log_sink.close()


@pytest.mark.parametrize("change", ["glue-view-aba", "glue-run-aba", "native-sql-aba", "busy"])
async def test_detail_preflight_preserves_newer_input_and_accepted_work(
    tmp_path, monkeypatch, change
):
    import aws_tui.app as module

    app, ctx = make_workspace_app(tmp_path, monkeypatch)
    _athena, glue, emr = install_services(ctx)
    glue.add_run("chosen", "other-run")
    session = wire_session()
    monkeypatch.setattr(ctx.aws_session, "client", session.client)
    started, release = asyncio.Event(), asyncio.Event()
    original = module.prepare_overview_entry

    async def blocked(*args):
        started.set()
        await release.wait()
        return await original(*args)

    monkeypatch.setattr(module, "prepare_overview_entry", blocked)
    task = None
    try:
        async with app.run_test(size=(120, 40)) as pilot:
            await drain_workers(app)
            kind = "athena" if change == "native-sql-aba" else "glue-job"
            outgoing = await choose_scope(app, ctx, pilot, kind)
            vm, entry = await ready_entry(app, pilot)
            task = asyncio.create_task(app._open_overview_entry(vm, entry))
            await wait_until(started.is_set, what="detail preflight")
            if change == "glue-view-aba":
                await outgoing.select_view("crawlers")
                await outgoing.select_view("jobs")
            elif change == "glue-run-aba":
                run = outgoing.jobs.selected_run_id
                outgoing.select_job_run("other-run")
                outgoing.select_job_run(run)
            elif change == "native-sql-aba":
                editor = app.query_one("#athena-editor", TextArea)
                before = editor.text
                editor.insert("x")
                editor.undo()
                assert editor.text == before
            else:
                monkeypatch.setattr(app, "_workspace_has_active_work", lambda: True)
            release.set()
            await asyncio.wait_for(task, 4)
            await pilot.pause()
            assert ctx.root_vm.content_host.current is outgoing
            assert not app.crash_report
    finally:
        release.set()
        if task is not None:
            await asyncio.gather(task, return_exceptions=True)
        ctx.root_vm.dispose()
        ctx.log_sink.close()
        emr.dispose()


@pytest.mark.parametrize("kind", ["athena", "glue-job", "glue-crawler", "emr-serverless"])
async def test_actual_palette_refresh_and_open_exact_existing_aws_detail(
    tmp_path, monkeypatch, kind
):
    app, ctx = make_workspace_app(tmp_path, monkeypatch)
    athena, _glue, emr = install_services(ctx)
    session = CountedSession(
        list_query_executions={"QueryExecutionIds": ["history-analysts"]},
        batch_get_query_execution={
            "QueryExecutions": [execution("history-analysts", "FAILED", "analysts")]
        },
        get_job_runs={
            "JobRuns": [{"Id": "chosen-run", "JobName": "chosen", "JobRunState": "FAILED"}]
        },
        get_crawler={"Crawler": {"Name": "chosen", "State": "READY"}},
        list_job_runs={
            "jobRuns": [{"id": "chosen-run", "applicationId": "chosen", "state": "FAILED"}]
        },
    )
    monkeypatch.setattr(ctx.aws_session, "client", session.client)
    try:
        async with app.run_test(size=(120, 40)) as pilot:
            await drain_workers(app)
            outgoing = await choose_scope(app, ctx, pilot, kind)
            await open_overview(app, pilot)
            vm = app.screen.vm
            assert not vm.sections
            assert session.calls == []
            await focus_with_tabs(app, pilot, "overview-add")
            await pilot.press("enter")
            assert vm.sections[0].scope.kind == kind
            assert session.calls == []
            await focus_with_tabs(app, pilot, "overview-refresh")
            await pilot.press("enter")
            await wait_until(lambda: vm.sections[0].state == "ready", what="scoped AWS read")
            expected = vm.sections[0].page.entries[0]
            await focus_with_tabs(app, pilot, "overview-list")
            await pilot.press("down", "enter")
            await wait_until(
                lambda: ctx.root_vm.content_host.current is not outgoing, what="existing AWS detail"
            )
            await drain_workers(app)
            current = ctx.root_vm.content_host.current
            assert current.connection == vm.connection
            if kind == "athena":
                assert current.context.workgroup == "analysts"
                assert current.history.selected_execution_id == expected.identifier
                assert current.active_view == "history"
            elif kind == "glue-job":
                assert current.active_view == "jobs"
                assert current.jobs.selected_job_name == "chosen"
                assert current.jobs.selected_run_id == expected.identifier
            elif kind == "glue-crawler":
                assert current.active_view == "crawlers"
                assert current.crawlers.selected_crawler_name == expected.identifier
            else:
                assert current.applications.selected_id == "chosen"
                assert current.job_run_detail.detail.job_run_id == expected.identifier
            assert athena.start_calls == athena.stop_calls == athena.result_calls == []
            assert all(
                name not in {"start_job_run", "cancel_job_run", "list_files", "stream"}
                for name, _ in emr.calls
            )
            assert len(session.calls) == (2 if kind == "athena" else 1)
            assert not app.crash_report
    finally:
        ctx.root_vm.dispose()
        ctx.log_sink.close()
        emr.dispose()


async def open_overview(app, pilot):
    await pilot.press("ctrl+k")
    await wait_until(lambda: bool(app.screen.query("#palette-input")), what="command palette")
    await pilot.press(*"scoped overview")
    await wait_until(
        lambda: len(app.app_ctx.command_palette_vm.filtered_entries) == 1,
        what="scoped overview palette command",
    )
    assert app.app_ctx.command_palette_vm.filtered_entries[0].id == "app.overview"
    await pilot.press("enter")
    await wait_until(lambda: bool(app.screen.query("#overview-list")), what="scoped overview")
    await pilot.pause()


@pytest.mark.parametrize("size", [(80, 24), (120, 40)])
async def test_actual_palette_session_watch_refresh_reopen_and_exact_transfer(
    tmp_path, monkeypatch, size
):
    app, ctx = make_workspace_app(tmp_path, monkeypatch)
    sdk_calls = []

    async def forbidden(*args, **kwargs):
        sdk_calls.append((args, kwargs))
        raise AssertionError("Session transfer overview must never request AWS")

    monkeypatch.setattr(ctx.aws_session, "client", forbidden)
    try:
        async with app.run_test(size=size) as pilot:
            await drain_workers(app)
            ctx.transfers_vm.register(
                TransferModel(
                    "chosen", "upload", "fixture", "s3://fixture", 5, 5, TransferState.COMPLETED
                )
            )
            await open_overview(app, pilot)
            vm = app.screen.vm
            assert vm.sections == ()
            await focus_with_tabs(app, pilot, "overview-add")
            await pilot.press("enter")
            assert len(vm.sections) == 1
            assert vm.sections[0].scope.kind == "transfers"
            assert vm.sections[0].state == "idle"
            await focus_with_tabs(app, pilot, "overview-refresh")
            await pilot.press("enter")
            await wait_until(lambda: vm.sections[0].state == "ready", what="session projection")
            assert sdk_calls == []
            await pilot.press("escape")
            await wait_until(lambda: len(app.screen_stack) == 1, what="overview dismissed")
            await open_overview(app, pilot)
            assert app.screen.vm is vm
            assert vm.sections[0].last_success is not None
            assert vm.sections[0].page.entries, vm.sections
            assert app.screen.query_one(OptionList).option_count == 2, (
                app.screen._rows,
                vm.sections,
            )
            assert app.screen.query_one(OptionList).highlighted == 0
            await focus_with_tabs(app, pilot, "overview-list")
            await pilot.press("down")
            assert app.screen.query_one(OptionList).highlighted == 1
            assert "Session-local" in str(
                app.screen.query_one("#overview-details", Static).render()
            )
            await pilot.press("enter")
            await wait_until(
                lambda: isinstance(app.focused, TransferRowWidget), what="existing transfer detail"
            )
            assert app.focused.transfer_vm.id == "chosen"
            assert sdk_calls == []
            assert not app.crash_report
    finally:
        ctx.root_vm.dispose()
        ctx.log_sink.close()
