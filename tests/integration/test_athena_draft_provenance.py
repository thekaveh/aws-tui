from __future__ import annotations

import asyncio
from dataclasses import replace

import pytest
from textual.app import App, ComposeResult
from textual.widgets import Button, TextArea

from aws_tui.domain.filesystem import AuthRequiredError
from aws_tui.ui.widgets.athena.page import AthenaPage
from aws_tui.ui.widgets.athena.query_view import AthenaQueryView
from aws_tui.vm.athena.page_vm import AthenaPageVM
from tests.athena_drafts_helpers import runtime_at
from tests.helpers import drain_workers, seed_athena_sql, wait_until
from tests.integration.test_athena_drafts import mounted_draft_app
from tests.integration.test_credential_recovery import _connected_probe
from tests.integration.test_glue_page import open_service
from tests.unit.vm.athena.test_draft_recovery import yes
from tests.unit.vm.athena.test_page_vm import PageClient, make_page_vm


async def test_staged_editor_label_changes_when_its_saved_snapshot_is_superseded(tmp_path):
    runtime, _store = runtime_at(tmp_path)
    source = make_page_vm(PageClient(), drafts=runtime, source_is_current=yes)
    target = make_page_vm(PageClient(), drafts=runtime, source_is_current=yes, drafts_active=False)
    newer = runtime.open_session()

    class EditorApp(App[None]):
        def compose(self) -> ComposeResult:
            yield AthenaQueryView(target)

    try:
        await source.setup()
        source.query.set_sql("SELECT 'OLD_SAVED'")
        await source._draft_session.flush(deadline=asyncio.get_running_loop().time() + 2)
        await target.setup()
        target.install_editor_snapshot(source.export_editor_snapshot())
        app = EditorApp()
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            editor = app.query_one("#athena-editor", TextArea)
            assert editor.border_title == "query editor · Draft saved"
            newer.edited("SELECT 'NEW_DURABLE'", source.context)
            await newer.flush(deadline=asyncio.get_running_loop().time() + 2)
            await pilot.pause()
            target.activate_drafts()
            await wait_until(
                lambda: editor.border_title == "query editor · Draft not saved",
                what="adopted editor publishes its unconfirmed save state",
            )
            assert editor.text == "SELECT 'OLD_SAVED'"
    finally:
        await source.shutdown()
        source.dispose()
        await target.shutdown()
        target.dispose()
        newer.detach()
        await runtime.shutdown()
        runtime.dispose()


async def test_failed_glue_handoff_restores_mounted_draft_origin_and_run_guard(
    tmp_path, monkeypatch
):
    async with mounted_draft_app(tmp_path, monkeypatch, size=(120, 40)) as (
        app,
        ctx,
        runtime,
        _store,
        _config,
        client,
        pilot,
    ):
        assert await runtime.set_enabled(True)
        client.databases["primary", "AwsDataCatalog"].append("other_database")
        await open_service(ctx, pilot, "athena")
        await drain_workers(app)
        source = ctx.root_vm.content_host.current
        assert isinstance(source, AthenaPageVM)
        origin = source.context
        sql = "SELECT * FROM unqualified_table"
        editor = app.query_one("#athena-editor", TextArea)
        await seed_athena_sql(pilot, source.query, editor, sql)
        await source.select_database("other_database")
        assert source.query.draft_execution_blocked

        await app.action_open_athena_table_in_glue()
        await drain_workers(app)
        await pilot.pause()

        replacement = ctx.root_vm.content_host.current
        assert isinstance(replacement, AthenaPageVM)
        assert replacement is not source
        assert app.query_one(AthenaPage).vm is replacement
        assert replacement.context.database == "other_database"
        assert replacement.query.sql == sql
        assert app.query_one("#athena-editor", TextArea).text == sql
        assert replacement._draft_session.bound_context == origin
        assert replacement.query.draft_execution_blocked
        assert app.query_one("#athena-execute", Button).disabled
        await replacement.query.execute()
        assert client.start_calls == []

        await replacement.select_database(origin.database)
        await wait_until(
            lambda: not app.query_one("#athena-execute", Button).disabled,
            what="returning to the draft origin enabled Run",
        )
        await pilot.click("#athena-execute")
        await wait_until(lambda: bool(client.start_calls), what="original-context query started")
        assert client.start_calls[0][1] == origin


@pytest.mark.parametrize("incomplete", [False, True])
async def test_actual_credential_recovery_preserves_draft_execution_authority(
    tmp_path, monkeypatch, incomplete
):
    async with mounted_draft_app(tmp_path, monkeypatch, size=(120, 40)) as (
        app,
        ctx,
        runtime,
        _store,
        _config,
        initial,
        pilot,
    ):
        assert await runtime.set_enabled(True)
        initial.databases["primary", "AwsDataCatalog"].append("other_database")
        if incomplete:
            initial.workgroup_error = AuthRequiredError("controlled expired credentials")
        await open_service(ctx, pilot, "athena")
        await drain_workers(app)
        source = ctx.root_vm.content_host.current
        assert isinstance(source, AthenaPageVM)
        origin = source.context
        sql = "SELECT * FROM unqualified_table"
        await seed_athena_sql(pilot, source.query, app.query_one("#athena-editor", TextArea), sql)
        if not incomplete:
            await source._draft_session.flush(deadline=asyncio.get_running_loop().time() + 2)
            await source.select_database("other_database")
        assert source.query.draft_execution_blocked
        original_connection = ctx.root_vm.active_connection
        assert original_connection is not None
        refreshed = replace(original_connection, session_token="synthetic-rotated-token")
        monkeypatch.setattr(ctx.aws_session, "probe_token", _connected_probe)
        monkeypatch.setattr(ctx.connection_resolver, "resolve", lambda _name: refreshed)
        candidate = PageClient(connection_name="demo-dev", region="us-east-1")
        candidate.databases["primary", "AwsDataCatalog"].append("other_database")
        service = ctx.registry.get("athena")
        service._client_factory = lambda _connection: candidate

        await app.action_authenticate()
        await drain_workers(app)
        await pilot.pause()

        recovered = ctx.root_vm.content_host.current
        assert isinstance(recovered, AthenaPageVM)
        assert recovered is not source
        assert app.query_one("#athena-editor", TextArea).text == sql
        assert recovered.query.draft_execution_blocked
        assert app.query_one("#athena-execute", Button).disabled
        assert recovered._draft_session.bound_context == (None if incomplete else origin)
        assert candidate.start_calls == []
        if incomplete:
            recovered.query.set_sql("")
            recovered.query.set_sql("SELECT 1")
        else:
            await recovered.select_database(origin.database)
        await recovered.query.execute()
        assert len(candidate.start_calls) == 1
        assert candidate.start_calls[0][1] == recovered.context


async def test_failed_s3_handoff_preserves_mounted_foreign_draft_guard(tmp_path, monkeypatch):
    async with mounted_draft_app(tmp_path, monkeypatch, size=(120, 40)) as (
        app,
        ctx,
        runtime,
        store,
        _config,
        client,
        pilot,
    ):
        assert await runtime.set_enabled(True)
        client.databases["primary", "AwsDataCatalog"].append("other_database")
        get_execution = client.get_query_execution

        async def history_in_selected_database(execution_id):
            detail = await get_execution(execution_id)
            return replace(detail, context=replace(detail.context, database="other_database"))

        monkeypatch.setattr(client, "get_query_execution", history_in_selected_database)
        await open_service(ctx, pilot, "athena")
        await drain_workers(app)
        source = ctx.root_vm.content_host.current
        assert isinstance(source, AthenaPageVM)
        origin = source.context
        sql = "SELECT * FROM unqualified_table"
        await seed_athena_sql(pilot, source.query, app.query_one("#athena-editor", TextArea), sql)
        await source.select_database("other_database")
        await source.select_view("history")
        await source.select_history_execution("history-primary")
        assert source.query.draft_execution_blocked
        mount = app._mount_service_view
        failed_s3_mounts = []

        async def fail_s3_mount(service_id, *args, **kwargs):
            if service_id == "s3":
                failed_s3_mounts.append(service_id)
                return False
            return await mount(service_id, *args, **kwargs)

        monkeypatch.setattr(app, "_mount_service_view", fail_s3_mount)
        await app.action_open_athena_result_location()
        await drain_workers(app)
        await pilot.pause()

        assert failed_s3_mounts == ["s3"]
        restored = ctx.root_vm.content_host.current
        assert isinstance(restored, AthenaPageVM)
        assert restored is not source
        assert restored.query.sql == sql
        assert app.query_one("#athena-editor", TextArea).text == sql
        assert restored._draft_session.bound_context == origin
        assert restored.query.draft_execution_blocked
        assert app.query_one("#athena-execute", Button).disabled
        assert not restored.query.owns_active_query
        assert not restored.query.cancel_command.can_execute()
        assert client.start_calls == []
        assert all(row.context == origin.cache_key for row in store.list().records)
