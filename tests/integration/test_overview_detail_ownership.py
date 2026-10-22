"""App ownership at watch supersession, dirty replacement and the final input claim."""

import asyncio

import pytest
from textual.widgets import TextArea

from aws_tui.vm.messages import ConnectionListChangedMessage
from tests.helpers import drain_workers, wait_until
from tests.integration.test_scoped_overview import (
    choose_scope,
    install_services,
    ready_entry,
    wire_session,
)
from tests.integration.test_workspace_manager import make_workspace_app


async def settle_task(task):
    return await asyncio.wait_for(task, 6)


@pytest.mark.parametrize("change", ["watch-remove", "config-file", "source-event", "cancel"])
async def test_preparation_supersession(tmp_path, monkeypatch, change):
    import aws_tui.app as module

    app, ctx = make_workspace_app(tmp_path, monkeypatch)
    athena, _glue, emr = install_services(ctx)
    monkeypatch.setattr(ctx.aws_session, "client", wire_session().client)
    started, release = asyncio.Event(), asyncio.Event()
    original = module.prepare_overview_entry
    prepared = []

    async def gated(*args):
        value = await original(*args)
        prepared.append(value)
        started.set()
        try:
            await release.wait()
        except BaseException:
            # The artificial gate owns a result not delivered to the App.
            from aws_tui.workspace_restore import retire_workspace_candidate

            await retire_workspace_candidate(value.recovery.vm)
            raise
        return value

    monkeypatch.setattr(module, "prepare_overview_entry", gated)
    task = None
    try:
        async with app.run_test(size=(120, 40)) as pilot:
            await drain_workers(app)
            outgoing = await choose_scope(app, ctx, pilot, "glue-job")
            vm, entry = await ready_entry(app, pilot)
            task = asyncio.create_task(app._open_overview_entry(vm, entry))
            await wait_until(started.is_set, what="prepared candidate held")
            if change == "watch-remove":
                vm.remove(entry.scope)
            elif change == "config-file":
                path = ctx.config_store.path
                path.write_text(path.read_text().replace("us-east-1", "eu-west-1"))
            elif change == "source-event":
                ctx.hub.send(ConnectionListChangedMessage((vm.connection.name,), "updated"))
            else:
                task.cancel()
            release.set()
            if change == "cancel":
                with pytest.raises(asyncio.CancelledError):
                    await settle_task(task)
            else:
                await settle_task(task)
            await pilot.pause()
            assert ctx.root_vm.content_host.current is outgoing, (change, vm.sections)
            assert prepared
            assert not app.crash_report
            assert athena.start_calls == athena.stop_calls == athena.result_calls == []
            assert all(
                name not in {"start_job_run", "cancel_job_run", "list_files", "stream"}
                for name, _ in emr.calls
            )
    finally:
        release.set()
        if task:
            await asyncio.gather(task, return_exceptions=True)
        ctx.root_vm.dispose()
        ctx.log_sink.close()
        emr.dispose()


@pytest.mark.parametrize("change", ["source-event", "busy", "native-aba"])
async def test_progress_observer_cannot_cross_claim_with_retired_intent(
    tmp_path, monkeypatch, change
):
    app, ctx = make_workspace_app(tmp_path, monkeypatch)
    athena, _glue, emr = install_services(ctx)
    monkeypatch.setattr(ctx.aws_session, "client", wire_session().client)
    seen = []
    busy = False
    subscription = None
    try:
        async with app.run_test(size=(120, 40)) as pilot:
            await drain_workers(app)
            outgoing = await choose_scope(
                app, ctx, pilot, "athena" if change == "native-aba" else "glue-job"
            )
            vm, entry = await ready_entry(app, pilot)
            if change == "busy":
                monkeypatch.setattr(app, "_workspace_has_active_work", lambda: busy)

            def on_toast(_):
                nonlocal busy
                if seen or not any(
                    t.model.id == "progress-workspace-open"
                    for t in ctx.root_vm.chrome.toast_stack.toasts
                ):
                    return
                seen.append(change)
                if change == "source-event":
                    ctx.hub.send(ConnectionListChangedMessage((vm.connection.name,), "updated"))
                elif change == "busy":
                    busy = True
                else:
                    editor = app.query_one("#athena-editor", TextArea)
                    original = editor.text
                    editor.insert("x")
                    editor.undo()
                    assert editor.text == original

            subscription = ctx.root_vm.chrome.toast_stack.on_collection_changed.subscribe(on_toast)
            await settle_task(asyncio.create_task(app._open_overview_entry(vm, entry)))
            await pilot.pause()
            assert seen == [change]
            assert ctx.root_vm.content_host.current is outgoing, (
                change,
                ctx.root_vm.content_host.current,
            )
            assert not app.crash_report
            assert athena.start_calls == athena.stop_calls == athena.result_calls == []
    finally:
        if subscription:
            subscription.dispose()
        ctx.root_vm.dispose()
        ctx.log_sink.close()
        emr.dispose()


async def test_dirty_editor_decline_never_prepares_or_retires(tmp_path, monkeypatch):
    import aws_tui.app as module

    app, ctx = make_workspace_app(tmp_path, monkeypatch)
    athena, _glue, emr = install_services(ctx)
    monkeypatch.setattr(ctx.aws_session, "client", wire_session().client)
    calls = []
    original = module.prepare_overview_entry

    async def counted(*args):
        calls.append(args)
        return await original(*args)

    monkeypatch.setattr(module, "prepare_overview_entry", counted)
    task = None
    try:
        async with app.run_test(size=(120, 40)) as pilot:
            await drain_workers(app)
            outgoing = await choose_scope(app, ctx, pilot, "athena")
            vm, entry = await ready_entry(app, pilot)
            editor = app.query_one("#athena-editor", TextArea)
            editor.insert("SELECT 42")
            await wait_until(lambda: outgoing.query.sql == editor.text, what="native SQL sync")
            assert outgoing.authoring.dirty
            task = asyncio.create_task(app._open_overview_entry(vm, entry))
            await wait_until(
                lambda: len(app.screen_stack) == 2, what="dirty replacement confirmation"
            )
            await pilot.press("escape")
            await settle_task(task)
            assert not calls
            assert ctx.root_vm.content_host.current is outgoing
            assert outgoing.query.sql == editor.text == "SELECT 42"
            assert not editor.read_only
            assert athena.start_calls == athena.stop_calls == athena.result_calls == []
            assert not app.crash_report
    finally:
        if task:
            await asyncio.gather(task, return_exceptions=True)
        ctx.root_vm.dispose()
        ctx.log_sink.close()
        emr.dispose()


@pytest.mark.parametrize("change", ["missing", "canceled-provider", "cancel-after-publication"])
async def test_cleanup_and_acknowledgement(tmp_path, monkeypatch, change):
    from vmx import PropertyChangedMessage

    from aws_tui.ui.widgets.glue.page import GluePage

    app, ctx = make_workspace_app(tmp_path, monkeypatch)
    athena, glue, emr = install_services(ctx)
    monkeypatch.setattr(ctx.aws_session, "client", wire_session().client)
    candidates = []
    service = ctx.registry.get("glue")
    build = service.build_overview_vm

    def capture(*args):
        recovery = build(*args)
        candidates.append(recovery.vm)
        return recovery

    monkeypatch.setattr(service, "build_overview_vm", capture)
    task = None
    subscription = None
    started, canceled, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
    try:
        async with app.run_test(size=(120, 40)) as pilot:
            await drain_workers(app)
            outgoing = await choose_scope(app, ctx, pilot, "glue-job")
            vm, entry = await ready_entry(app, pilot)
            if change == "missing":
                glue.runs["chosen"] = []
            elif change == "canceled-provider":
                original = glue.list_job_runs_page

                async def resistant(*args, **kwargs):
                    started.set()
                    try:
                        await release.wait()
                    except asyncio.CancelledError:
                        canceled.set()
                        await release.wait()
                    return await original(*args, **kwargs)

                monkeypatch.setattr(glue, "list_job_runs_page", resistant)
            else:

                def published(message):
                    if (
                        isinstance(message, PropertyChangedMessage)
                        and message.sender_object is ctx.root_vm.content_host
                        and message.property_name == "current"
                        and candidates
                        and ctx.root_vm.content_host.current is candidates[-1]
                    ):
                        task.cancel()

                subscription = ctx.hub.messages.subscribe(published)
            task = asyncio.create_task(app._open_overview_entry(vm, entry))
            if change == "canceled-provider":
                await wait_until(started.is_set, what="actual provider read")
                task.cancel()
                await wait_until(canceled.is_set, what="actual provider cancellation")
                task.cancel()
                await asyncio.sleep(0)
                assert not task.done(), "provider cleanup must remain owned"
                release.set()
            if change == "missing":
                await settle_task(task)
            else:
                with pytest.raises(asyncio.CancelledError):
                    await settle_task(task)
            await pilot.pause()
            assert candidates
            if change == "cancel-after-publication":
                candidate = candidates[-1]
                assert ctx.root_vm.content_host.current is candidate
                views = list(app.query(GluePage))
                assert len(views) == 1
                assert views[0].vm is candidate
                assert all(node.display for node in views[0].ancestors_with_self)
                assert candidate.jobs.selected_job_name == "chosen"
                assert candidate.jobs.selected_run_id == entry.identifier
                assert not candidate._disposed
                assert not app.query_one("#main-area").disabled
                assert not app._workspace_input_claimed
            else:
                assert ctx.root_vm.content_host.current is outgoing
                assert all(page._disposed for page in candidates)
            assert athena.start_calls == athena.stop_calls == athena.result_calls == []
            assert not app.crash_report
    finally:
        release.set()
        if task:
            await asyncio.gather(task, return_exceptions=True)
        if subscription:
            subscription.dispose()
        ctx.root_vm.dispose()
        ctx.log_sink.close()
        emr.dispose()


async def test_dirty_editor_explicit_accept_opens_exact_history_without_execution(
    tmp_path, monkeypatch
):
    app, ctx = make_workspace_app(tmp_path, monkeypatch)
    athena, _glue, emr = install_services(ctx)
    monkeypatch.setattr(ctx.aws_session, "client", wire_session().client)
    task = None
    try:
        async with app.run_test(size=(120, 40)) as pilot:
            await drain_workers(app)
            outgoing = await choose_scope(app, ctx, pilot, "athena")
            vm, entry = await ready_entry(app, pilot)
            editor = app.query_one("#athena-editor", TextArea)
            editor.insert("SELECT 42")
            await wait_until(
                lambda: outgoing.query.sql == editor.text, what="dirty SQL synchronized"
            )
            assert outgoing.authoring.dirty
            task = asyncio.create_task(app._open_overview_entry(vm, entry))
            await wait_until(
                lambda: len(app.screen_stack) == 2, what="explicit replacement question"
            )
            await pilot.press("right", "enter")
            await settle_task(task)
            current = ctx.root_vm.content_host.current
            assert current is not outgoing
            assert current.active_view == "history"
            assert current.context.workgroup == "analysts"
            assert current.history.selected_execution_id == entry.identifier
            assert athena.start_calls == athena.stop_calls == athena.result_calls == []
            assert not app.crash_report
    finally:
        if task:
            await asyncio.gather(task, return_exceptions=True)
        ctx.root_vm.dispose()
        ctx.log_sink.close()
        emr.dispose()


async def test_actual_active_query_is_not_retired_or_stopped_by_drill(tmp_path, monkeypatch):
    app, ctx = make_workspace_app(tmp_path, monkeypatch)
    athena, _glue, emr = install_services(ctx)
    monkeypatch.setattr(ctx.aws_session, "client", wire_session().client)
    operation = None
    started, release = asyncio.Event(), asyncio.Event()
    original = athena.get_query_execution

    async def held(identity):
        if identity.startswith("q-app-"):
            started.set()
            await release.wait()
        return await original(identity)

    monkeypatch.setattr(athena, "get_query_execution", held)
    try:
        async with app.run_test(size=(120, 40)) as pilot:
            await drain_workers(app)
            outgoing = await choose_scope(app, ctx, pilot, "athena")
            vm, entry = await ready_entry(app, pilot)
            editor = app.query_one("#athena-editor", TextArea)
            editor.insert("SELECT 42")
            await wait_until(lambda: outgoing.query.sql == editor.text, what="SQL ready to execute")
            operation = asyncio.create_task(outgoing.query.execute())
            await wait_until(started.is_set, what="actual accepted query polling")
            assert outgoing.query.owns_active_query
            owner = app.service_navigation.owner
            await settle_task(asyncio.create_task(app._open_overview_entry(vm, entry)))
            assert ctx.root_vm.content_host.current is outgoing
            assert app.service_navigation.owner == owner
            assert not operation.done()
            assert len(athena.start_calls) == 1
            assert athena.stop_calls == athena.result_calls == []
            assert not app.crash_report
            release.set()
            await asyncio.wait_for(operation, 4)
    finally:
        release.set()
        if operation:
            await asyncio.gather(operation, return_exceptions=True)
        ctx.root_vm.dispose()
        ctx.log_sink.close()
        emr.dispose()
