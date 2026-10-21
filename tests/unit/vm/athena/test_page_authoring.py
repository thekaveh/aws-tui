"""One coherent SQL document through Athena page rollback and retirement."""

from __future__ import annotations

import asyncio
from dataclasses import replace

import pytest

from aws_tui.infra.sql_snippet_store import SqlSnippetStore
from aws_tui.vm.athena.authoring_vm import AuthoringDocumentSnapshot
from aws_tui.vm.athena.saved_vm import SavedQueryKind
from tests.athena_drafts_helpers import runtime_at
from tests.helpers import wait_until
from tests.unit.vm.athena.test_authoring_vm import GatedFiles, yes
from tests.unit.vm.athena.test_page_vm import PageClient, make_page_vm


def make(tmp_path, *, client=None, **kwargs):
    return make_page_vm(
        client or PageClient(),
        sql_snippets=SqlSnippetStore(path=tmp_path / "snippets.json"),
        **kwargs,
    )


async def close(*pages):
    for page in pages:
        await page.shutdown()
        page.dispose()


@pytest.mark.parametrize("fallback", [False, True])
@pytest.mark.parametrize("with_drafts", [False, True])
async def test_snapshot_restores_file_dirty_state_atomically(tmp_path, fallback, with_drafts):
    runtime = runtime_at(tmp_path)[0] if with_drafts else None
    source = make(tmp_path, drafts=runtime)
    target = make(tmp_path, drafts=runtime)
    observed = []
    subscriptions = []
    try:
        await source.setup()
        await target.setup()
        path = tmp_path / "private-file.sql"
        path.write_text("SELECT 'baseline'")
        assert await source.authoring.open_sql(path, confirm=yes)
        source.query.set_sql("SELECT 'modified'")
        target.query.set_sql("SELECT 'other document'")
        snapshot = source.export_editor_snapshot() if fallback else source.export_snapshot()

        def observe(_):
            observed.append(
                (target.query.sql, target.authoring.export_snapshot(), target.authoring.dirty)
            )

        for owner in (target, target.query):
            subscriptions.append(owner.on_property_changed.subscribe(observe))
        if runtime is not None:
            subscriptions.append(runtime.on_property_changed.subscribe(observe))
        if fallback:
            target.install_editor_snapshot(snapshot)
        else:
            await target.restore_snapshot(snapshot)
        assert target.authoring.path == path
        assert target.authoring.dirty
        assert target.authoring.export_snapshot() == source.authoring.export_snapshot()
        assert target.query.sql == source.query.sql
        assert observed
        assert all(
            row == (source.query.sql, source.authoring.export_snapshot(), True) for row in observed
        )
        assert "private-file" not in repr(snapshot)
        assert "baseline" not in repr(snapshot)
        assert "modified" not in repr(snapshot)
        assert target.client.start_calls == []
    finally:
        for sub in subscriptions:
            sub.dispose()
        await close(source, target)
        if runtime is not None:
            await runtime.shutdown()
            runtime.dispose()


@pytest.mark.parametrize("fallback", [False, True])
@pytest.mark.parametrize(
    "bad",
    [
        object(),
        AuthoringDocumentSnapshot("relative.sql"),
        AuthoringDocumentSnapshot(baseline=None),
        AuthoringDocumentSnapshot(origin="arbitrary"),
    ],
)
async def test_invalid_document_snapshot_rejected_before_any_state_change(tmp_path, fallback, bad):
    page = make(tmp_path)
    try:
        await page.setup()
        page.query.set_sql("SELECT 1")
        original = page.export_editor_snapshot() if fallback else page.export_snapshot()
        assert hasattr(original, "document")
        forged = replace(original, document=bad)
        before = page.export_snapshot()
        revision = page.query.editor_revision
        changes = []
        sub = page.on_property_changed.subscribe(changes.append)
        try:
            if fallback:
                with pytest.raises(ValueError, match=r"unavailable|invalid"):
                    page.install_editor_snapshot(forged)
            else:
                with pytest.raises(ValueError, match=r"unavailable|invalid"):
                    await page.restore_snapshot(forged)
        finally:
            sub.dispose()
        assert page.export_snapshot() == before
        assert page.query.editor_revision == revision
        assert not changes
    finally:
        await close(page)


async def test_save_changes_document_ownership_but_not_whole_load_revision(tmp_path):
    page = make(tmp_path)
    try:
        await page.setup()
        page.query.set_sql("SELECT 1")
        load = page.authoring.replacement_revision
        owner = page.authoring.ownership_revision
        assert await page.authoring.save_sql(tmp_path / "one.sql", confirm=yes)
        assert page.authoring.replacement_revision == load
        assert page.authoring.ownership_revision > owner
        assert await page.authoring.open_sql(tmp_path / "one.sql", confirm=yes)
        assert page.authoring.replacement_revision > load
        load = page.authoring.replacement_revision
        page.install_editor_snapshot(page.export_editor_snapshot())
        assert page.authoring.replacement_revision > load
    finally:
        await close(page)


@pytest.mark.parametrize("action", ["save", "equal-load"])
async def test_document_change_during_preflight_retires_snapshot_restore(tmp_path, action):
    client = PageClient()
    page = make(tmp_path, client=client)
    pending = None
    try:
        await page.setup()
        page.query.set_sql("SELECT 1")
        before = page.export_snapshot()
        await page.select_workgroup("analysts")
        client.block_workgroup_detail_for = before.context.workgroup
        pending = asyncio.create_task(page.restore_snapshot(before))
        await wait_until(client.workgroup_detail_started.is_set, what="snapshot preflight started")
        path = tmp_path / "new.sql"
        if action == "save":
            assert await page.authoring.save_sql(path, confirm=yes)
        else:
            path.write_text(page.query.sql)
            assert await page.authoring.open_sql(path, confirm=yes)
        client.release_workgroup_detail.set()
        with pytest.raises(ValueError, match="restore is unavailable"):
            await pending
        assert page.authoring.path == path
    finally:
        client.release_workgroup_detail.set()
        if pending is not None:
            await asyncio.gather(pending, return_exceptions=True)
        await close(page)


async def test_external_page_shutdown_waits_for_owned_file_publication(tmp_path):
    files = GatedFiles(after_publish=True)
    page = make(tmp_path, sql_files=files)
    save = shutdown = None
    try:
        await page.setup()
        page.query.set_sql("SELECT 1")
        path = tmp_path / "saved.sql"
        save = asyncio.create_task(page.authoring.save_sql(path, confirm=yes))
        await wait_until(files.entered.is_set, what="file publication reached")
        shutdown = asyncio.create_task(page.shutdown())
        await asyncio.sleep(0)
        assert not shutdown.done()
        assert not await page.authoring.replace_sql("SELECT 2", confirm=yes, origin="Local editor")
        files.release.set()
        await shutdown
        assert save.done()
        assert path.read_text() == "SELECT 1"
        assert not page.authoring._actions
        assert not page.authoring._io_tasks
    finally:
        files.release.set()
        await asyncio.gather(
            *(t for t in (save, shutdown) if t is not None), return_exceptions=True
        )
        await close(page)


async def test_completion_context_fence_includes_same_context_metadata_refresh(tmp_path):
    page = make(tmp_path)
    try:
        await page.setup()
        page.query.set_sql("SEL")
        first = await page.completion.complete(3)
        assert page.completion.is_current(first)
        await page.tables.refresh()
        assert not page.completion.is_current(first)
        second = await page.completion.complete(3)
        assert page.completion.is_current(second)
        await page.refresh_query_context()
        assert not page.completion.is_current(second)
        third = await page.completion.complete(3)
        assert page.completion.is_current(third)
        page.close_input_admission()
        assert not page.completion.is_current(third)
        assert await page.completion.complete(3) is None
    finally:
        await close(page)


@pytest.mark.parametrize("with_drafts", [False, True])
async def test_whole_file_open_publishes_one_coherent_document(tmp_path, with_drafts):
    runtime = runtime_at(tmp_path)[0] if with_drafts else None
    page = make(tmp_path, drafts=runtime)
    subscriptions = []
    try:
        await page.setup()
        page.query.set_sql("SELECT 'old'")
        path = tmp_path / "current.sql"
        path.write_text("SELECT 'new'")
        observed = []

        def observe(_):
            observed.append((page.query.sql, page.authoring.path, page.authoring.dirty))

        subscriptions.append(page.query.on_property_changed.subscribe(observe))
        subscriptions.append(page.on_property_changed.subscribe(observe))
        if runtime is not None:
            subscriptions.append(runtime.on_property_changed.subscribe(observe))
        assert await page.authoring.open_sql(path, confirm=yes)
        assert observed
        assert all(row == ("SELECT 'new'", path, False) for row in observed)
    finally:
        for sub in subscriptions:
            sub.dispose()
        await close(page)
        if runtime is not None:
            await runtime.shutdown()
            runtime.dispose()


@pytest.mark.parametrize("kind", [SavedQueryKind.NAMED, SavedQueryKind.PREPARED])
@pytest.mark.parametrize("answer", [False, True])
async def test_saved_replacement_confirms_modified_text_and_records_origin(tmp_path, kind, answer):
    page = make(tmp_path)
    try:
        await page.setup()
        await page.select_workgroup("analysts")
        await page.select_view("saved")
        if kind is SavedQueryKind.NAMED:
            await page.select_named_query("named-1")
        else:
            await page.select_prepared_statement("prepared-1")
        page.query.set_sql("SELECT 'modified'")
        expected = page.saved.selected_sql()
        asked = []

        async def confirm(message):
            asked.append(message)
            return answer

        await page.open_saved_in_editor(confirm=confirm)
        assert len(asked) == 1
        if answer:
            assert page.query.sql == expected
            assert page.active_view == "query"
            assert page.authoring.export_snapshot().origin == (
                "AWS named query" if kind is SavedQueryKind.NAMED else "AWS prepared statement"
            )
            assert not page.authoring.dirty
        else:
            assert page.query.sql == "SELECT 'modified'"
            assert page.active_view == "saved"
            assert page.authoring.dirty
        assert not page.client.start_calls
    finally:
        await close(page)


@pytest.mark.parametrize("change", ["text", "same-sql-load", "selection", "context"])
async def test_saved_replacement_rechecks_confirmation_owner(tmp_path, change):
    page = make(tmp_path)
    try:
        await page.setup()
        await page.select_workgroup("analysts")
        await page.select_view("saved")
        await page.select_named_query("named-1")
        page.query.set_sql("SELECT 'modified'")

        async def stale(_):
            if change == "text":
                page.query.set_sql("SELECT 'newer'")
            elif change == "same-sql-load":
                page.authoring.install_snapshot(AuthoringDocumentSnapshot(baseline=page.query.sql))
            elif change == "selection":
                await page.select_prepared_statement("prepared-1")
            else:
                await page.select_workgroup("primary")
            return True

        await page.open_saved_in_editor(confirm=stale)
        assert page.query.sql == ("SELECT 'newer'" if change == "text" else "SELECT 'modified'")
        assert not page.client.start_calls
    finally:
        await close(page)


@pytest.mark.parametrize("answer", [False, True])
async def test_table_selection_confirms_before_publishing_sql_or_selection(tmp_path, answer):
    from tests.unit.vm.athena.test_completion_vm import client

    page = make(tmp_path, client=client())
    try:
        await page.setup()
        await page.tables.refresh()
        page.query.set_sql("SELECT 'modified'")
        asked = []

        async def confirm(message):
            asked.append(message)
            return answer

        await page.select_table("events", confirm=confirm)
        assert len(asked) == 1
        assert (page.selected_table_ref is not None) is answer
        if answer:
            assert page.query.sql.endswith('"events" LIMIT 5')
            assert page.authoring.export_snapshot().origin == "Table query"
            assert not page.authoring.dirty
        else:
            assert page.query.sql == "SELECT 'modified'"
        assert not any(call.method == "start_query" for call in page.client.calls)
    finally:
        await close(page)
