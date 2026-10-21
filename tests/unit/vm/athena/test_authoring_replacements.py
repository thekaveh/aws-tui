"""Whole-document table/draft loads retain confirmation and provenance."""

from __future__ import annotations

import asyncio
from dataclasses import replace

import pytest

from aws_tui.domain.data_catalog import TableRef
from aws_tui.domain.query import QueryContext
from aws_tui.vm.athena.authoring_vm import AuthoringDocumentSnapshot
from aws_tui.vm.athena.drafts_vm import valid_draft_session_snapshot
from tests.athena_drafts_helpers import record, runtime_at
from tests.unit.vm.athena.test_authoring_vm import yes
from tests.unit.vm.athena.test_page_authoring import close, make


def target(database="default"):
    return TableRef("AwsDataCatalog", database, "events", "analytics", "us-west-2")


@pytest.mark.parametrize("answer", [False, True])
async def test_direct_table_open_confirms_before_replacing_a_local_file(tmp_path, answer):
    page = make(tmp_path)
    try:
        await page.setup()
        path = tmp_path / "query.sql"
        path.write_text("SELECT 'original'")
        assert await page.authoring.open_sql(path, confirm=yes)
        page.query.set_sql("SELECT 'modified'")
        before = page.export_editor_snapshot()
        asked = []

        async def confirm(message):
            asked.append(message)
            return answer

        await page.open_table(target(), confirm=confirm)
        assert len(asked) == 1
        if answer:
            assert page.authoring.path is None
            assert page.authoring.export_snapshot().origin == "Table query"
            assert not page.authoring.dirty
            assert page.query.sql.endswith('"events" LIMIT 5')
        else:
            assert page.export_editor_snapshot() == before
        assert page.client.start_calls == []
    finally:
        await close(page)


async def test_unconfirmed_synchronous_prime_cannot_replace_modified_text(tmp_path):
    page = make(tmp_path)
    try:
        await page.setup()
        page.query.set_sql("SELECT 'modified'")
        before = page.export_snapshot()
        with pytest.raises(ValueError, match=r"modified|Modified"):
            page.prime_table_query(target())
        assert page.export_snapshot() == before
    finally:
        await close(page)


@pytest.mark.parametrize("change", ["edit-roundtrip", "equal-document", "context-choice"])
async def test_direct_table_confirmation_cannot_authorize_a_newer_editor(tmp_path, change):
    page = make(tmp_path)
    try:
        await page.setup()
        page.query.set_sql("SELECT 'modified'")

        async def stale(_):
            if change == "edit-roundtrip":
                page.query.set_sql("SELECT 'intermediate'")
                page.query.set_sql("SELECT 'modified'")
            elif change == "equal-document":
                page.authoring.install_snapshot(AuthoringDocumentSnapshot(baseline=page.query.sql))
            else:
                await page.select_workgroup("primary")
            return True

        await page.open_table(target(), confirm=stale)
        assert page.query.sql == "SELECT 'modified'"
        assert not page.query.is_context_resolving
    finally:
        await close(page)


@pytest.mark.parametrize("with_drafts", [False, True])
async def test_direct_table_failure_restores_file_document_and_editor_provenance(
    tmp_path, with_drafts
):
    runtime = runtime_at(tmp_path)[0] if with_drafts else None
    page = make(tmp_path, drafts=runtime)
    try:
        await page.setup()
        path = tmp_path / "query.sql"
        path.write_text("SELECT 'original'")
        assert await page.authoring.open_sql(path, confirm=yes)
        page.query.set_sql("SELECT 'modified'")
        before = page.export_editor_snapshot()
        with pytest.raises(ValueError, match="unavailable"):
            await page.open_table(target("missing"), confirm=yes)
        after = page.export_editor_snapshot()
        assert after.document == before.document
        assert after.editor.sql == before.editor.sql
        assert page.authoring.path == path
        assert page.authoring.dirty
        if with_drafts:
            assert after.editor.draft.bound_context == before.editor.draft.bound_context
        assert not page.query.is_context_resolving
        assert page.client.start_calls == []
    finally:
        await close(page)
        if runtime is not None:
            await runtime.shutdown()
            runtime.dispose()


@pytest.mark.parametrize("change", ["edit-roundtrip", "equal-document"])
async def test_prime_ownership_is_not_revived_by_equal_sql(tmp_path, change):
    page = make(tmp_path)
    try:
        await page.setup()
        token = page.prime_table_query(target())
        assert page.owns_table_query_input(token)
        sql = page.query.sql
        if change == "edit-roundtrip":
            page.query.set_sql("SELECT 99")
            page.query.set_sql(sql)
        else:
            page.authoring.install_snapshot(page.authoring.export_snapshot())
        assert not page.owns_table_query_input(token)
    finally:
        await close(page)


async def test_draft_restore_confirms_cleared_file_and_adopts_untitled_provenance(tmp_path):
    runtime, store = runtime_at(tmp_path)
    page = make(tmp_path, drafts=runtime, source_is_current=lambda: _true())
    try:
        await page.setup()
        path = tmp_path / "query.sql"
        path.write_text("SELECT 'file baseline'")
        assert await page.authoring.open_sql(path, confirm=yes)
        page.query.set_sql("")
        saved = replace(record(context=page.context.cache_key), sql="SELECT 'recovered'")
        from aws_tui.infra.athena_draft_store import DraftPermit

        assert store.save(saved, permit=DraftPermit()).code is None
        asked = []

        async def decline():
            asked.append(True)
            return False

        assert not await page.restore_draft(saved.id, decline)
        assert len(asked) == 1
        assert page.authoring.path == path
        assert page.query.sql == ""
        assert await page.restore_draft(saved.id, _true)
        assert page.query.sql == saved.sql
        assert page.authoring.path is None
        assert page.authoring.export_snapshot().origin == "Local draft"
        assert page.authoring.dirty
        assert page.client.start_calls == []
    finally:
        await close(page)
        await runtime.shutdown()
        runtime.dispose()


async def _true():
    return True


async def test_accepted_table_document_binds_its_resolved_context_with_drafts(tmp_path):
    runtime, store = runtime_at(tmp_path)
    page = make(tmp_path, drafts=runtime)
    try:
        page.client.databases[("primary", "AwsDataCatalog")].append("analytics")
        await page.setup()
        previous = record(context=page.context.cache_key, sql="SELECT 'previous draft'")
        from aws_tui.infra.athena_draft_store import DraftPermit

        assert store.save(previous, permit=DraftPermit()).code is None
        page.query.set_sql("SELECT 'modified'")
        await page.open_table(target("analytics"), confirm=yes)
        assert page.context.database == "analytics"
        snapshot = page.export_editor_snapshot()
        assert snapshot.editor.draft.bound_context == page.context
        assert snapshot.editor.draft.saved_sql is None
        assert not page.query.draft_execution_blocked
        assert page.query.execute_command.can_execute()
        assert not page.authoring.dirty
        assert page.client.start_calls == []
        assert previous in store.list().records
    finally:
        await close(page)
        await runtime.shutdown()
        runtime.dispose()


async def test_draft_install_does_not_clear_a_newer_recovery_guard(tmp_path):
    runtime, store = runtime_at(tmp_path)
    page = make(tmp_path, drafts=runtime, source_is_current=lambda: _true())
    subscription = None
    try:
        await page.setup()
        saved = record(context=page.context.cache_key, sql="SELECT 'recover me'")
        from aws_tui.infra.athena_draft_store import DraftPermit

        assert store.save(saved, permit=DraftPermit()).code is None
        raised = False

        def observe(name):
            nonlocal raised
            if name == "sql" and page.query.sql == saved.sql and not raised:
                raised = True
                page.query.set_draft_recovery_guard(True)

        subscription = page.query.on_property_changed.subscribe(observe)
        assert not await page.restore_draft(saved.id, _true)
        assert raised
        assert page.query.draft_recovery_guard
        assert page.query.draft_execution_blocked
        assert page.client.start_calls == []
    finally:
        if subscription is not None:
            subscription.dispose()
        await close(page)
        await runtime.shutdown()
        runtime.dispose()


async def test_pending_table_discovery_never_saves_generated_sql_in_old_origin(
    tmp_path, monkeypatch
):
    runtime, store = runtime_at(tmp_path)
    page = make(tmp_path, drafts=runtime)
    entered, release = asyncio.Event(), asyncio.Event()
    task = None
    try:
        page.client.databases[("primary", "AwsDataCatalog")].append("analytics")
        await page.setup()
        previous = record(context=page.context.cache_key, sql="SELECT 'stored old document'")
        from aws_tui.infra.athena_draft_store import DraftPermit

        assert store.save(previous, permit=DraftPermit()).code is None
        original = page._ensure_database_loaded

        async def gated(ref):
            entered.set()
            await release.wait()
            return await original(ref)

        monkeypatch.setattr(page, "_ensure_database_loaded", gated)
        task = asyncio.create_task(page.open_table(target("analytics"), confirm=yes))
        await asyncio.wait_for(entered.wait(), timeout=2)
        # A real flush forces pending autosave captures without a timing guess.
        await page._draft_session.flush(deadline=asyncio.get_running_loop().time() + 2)
        assert previous in store.list().records
        assert page.query.draft_execution_blocked
        release.set()
        await task
        assert previous in store.list().records
        assert not page.query.draft_execution_blocked
        assert page.client.start_calls == []
    finally:
        release.set()
        if task is not None:
            await task
        await close(page)
        await runtime.shutdown()
        runtime.dispose()


async def test_table_sql_notification_cannot_authorize_a_new_recovery_guard(tmp_path):
    runtime, _ = runtime_at(tmp_path)
    page = make(tmp_path, drafts=runtime)
    subscription = None
    try:
        await page.setup()
        raised = False

        def observe(name):
            nonlocal raised
            if name == "sql" and page.query.sql.startswith("SELECT *") and not raised:
                raised = True
                page.query.set_draft_recovery_guard(True)

        subscription = page.query.on_property_changed.subscribe(observe)
        await page.open_table(target(), confirm=yes)
        assert raised
        assert page.query.draft_recovery_guard
        assert page.query.draft_execution_blocked
        assert not page.query.execute_command.can_execute()
        assert page.client.start_calls == []
    finally:
        if subscription is not None:
            subscription.dispose()
        await close(page)
        await runtime.shutdown()
        runtime.dispose()


@pytest.mark.parametrize("transition", ["context", "edit", "snapshot", "delete"])
async def test_unconfirmed_table_document_stays_unsaved_through_lifecycle(tmp_path, transition):
    runtime, store = runtime_at(tmp_path)
    context = QueryContext("analytics", "us-west-2", "primary", "AwsDataCatalog", "default")
    resolved = replace(context, database="analytics")
    previous = record(context=context.cache_key, sql="SELECT 'previous document'")
    from aws_tui.infra.athena_draft_store import DraftPermit

    assert store.save(previous, permit=DraftPermit()).code is None
    session = runtime.open_session()
    try:
        session.activate(previous.sql, context)
        session.replace_document("SELECT * FROM analytics.events LIMIT 5", context, pending=True)
        if transition == "snapshot":
            snapshot = session.export_snapshot()
            candidate = runtime.open_session(active=False)
            candidate.install_snapshot(snapshot, context=resolved)
            session.detach()
            candidate.activate(snapshot.sql, resolved)
            session = candidate
        elif transition == "edit":
            session.edited("SELECT * FROM analytics.events LIMIT 1", resolved)
        elif transition == "delete":
            session.deleted(None)
        intermediate = session.export_snapshot()
        assert valid_draft_session_snapshot(intermediate, sql=intermediate.sql)
        session.context_changed(resolved)
        snapshot = session.export_snapshot()
        assert snapshot.document_pending
        assert valid_draft_session_snapshot(snapshot, sql=snapshot.sql)
        assert session.state == "context_required"
        await session.flush(deadline=asyncio.get_running_loop().time() + 2)
        assert store.list().records == (previous,)
        session.detach()
        report = await runtime.shutdown()
        assert report.unpersisted >= 1
        assert store.list().records == (previous,)
    finally:
        session.detach()
        await runtime.shutdown()
        runtime.dispose()


async def test_pending_draft_snapshot_rejects_non_string_state_without_using_it(tmp_path):
    runtime, _ = runtime_at(tmp_path)
    session = runtime.open_session()
    try:
        context = QueryContext("analytics", "us-west-2", "primary", "AwsDataCatalog", "default")
        session.replace_document("SELECT 1", context, pending=True)
        malformed = replace(session.export_snapshot(), state=["context_required"])
        assert not valid_draft_session_snapshot(malformed, sql="SELECT 1")
    finally:
        session.detach()
        await runtime.shutdown()
        runtime.dispose()


async def test_accepted_table_picker_document_is_runnable_with_drafts_enabled(tmp_path):
    from tests.unit.vm.athena.test_completion_vm import client

    runtime, _ = runtime_at(tmp_path)
    page = make(tmp_path, client=client(), drafts=runtime)
    try:
        await page.setup()
        page.query.set_sql("SELECT 'previous SQL'")
        await page.tables.refresh()
        await page.select_table("events", confirm=yes)
        assert page.selected_table_ref.table_name == "events"
        assert not page.export_editor_snapshot().editor.draft.document_pending
        assert not page.query.draft_execution_blocked
        assert page.query.execute_command.can_execute()
        assert not any(call.method == "start_query" for call in page.client.calls)
    finally:
        await close(page)
        await runtime.shutdown()
        runtime.dispose()


async def test_table_confirmation_does_not_clear_a_guard_raised_while_asking(tmp_path):
    runtime, _ = runtime_at(tmp_path)
    page = make(tmp_path, drafts=runtime)
    try:
        await page.setup()
        page.query.set_sql("SELECT 'keep me'")

        async def guarded(_message):
            page.query.set_draft_recovery_guard(True)
            return True

        await page.open_table(target(), confirm=guarded)
        assert page.query.sql == "SELECT 'keep me'"
        assert page.query.draft_recovery_guard
        assert page.query.draft_execution_blocked
        assert page.client.start_calls == []
    finally:
        await close(page)
        await runtime.shutdown()
        runtime.dispose()


async def test_clearing_unconfirmed_table_sql_does_not_delete_the_previous_draft(tmp_path):
    runtime, store = runtime_at(tmp_path)
    context = QueryContext("analytics", "us-west-2", "primary", "AwsDataCatalog", "default")
    previous = record(context=context.cache_key, sql="SELECT 'previous document'")
    from aws_tui.infra.athena_draft_store import DraftPermit

    assert store.save(previous, permit=DraftPermit()).code is None
    session = runtime.open_session()
    try:
        session.activate(previous.sql, context)
        session.replace_document("SELECT * FROM events LIMIT 5", context, pending=True)
        session.edited("", context)
        await session.flush(deadline=asyncio.get_running_loop().time() + 2)
        assert store.list().records == (previous,)
        assert session.state == "empty"
        assert not session.export_snapshot().document_pending
        assert valid_draft_session_snapshot(session.export_snapshot(), sql="")
        session.detach()
        assert (await runtime.shutdown()).unpersisted == 0
    finally:
        session.detach()
        await runtime.shutdown()
        runtime.dispose()
