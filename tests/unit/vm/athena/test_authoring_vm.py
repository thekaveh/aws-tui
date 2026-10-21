"""Document ownership, confirmation and local I/O before UI integration."""

from __future__ import annotations

import asyncio
import importlib
import importlib.util
import subprocess
import sys
import textwrap
import threading
from dataclasses import replace

import pytest

from aws_tui.infra.sql_authoring_store import SqlFile, SqlFileStore
from aws_tui.infra.sql_snippet_store import SqlSnippetStore
from tests.helpers import wait_until
from tests.unit.vm.athena.test_query_vm import InMemoryAthena, make_query_vm


def api():
    assert importlib.util.find_spec("aws_tui.vm.athena.authoring_vm") is not None, (
        "The authoring document owner is missing"
    )
    return importlib.import_module("aws_tui.vm.athena.authoring_vm")


def make_authoring(tmp_path, *, files=None, snippets=None, **kwargs):
    m = api()
    client = InMemoryAthena()
    query = make_query_vm(client)
    vm = m.AthenaAuthoringVM(
        query=query,
        client=client,
        files=files,
        snippets=snippets or SqlSnippetStore(path=tmp_path / "snippets.json"),
        **kwargs,
    )
    return vm, query, client


async def yes(_message):
    return True


async def no(_message):
    return False


async def test_format_canceled_by_final_notification_cannot_authorize_an_edit(tmp_path):
    vm = query = None
    applied = []

    def cancel_on_status():
        if vm.status == "SQL layout formatted":
            asyncio.current_task().cancel()

    vm, query, _ = make_authoring(tmp_path, notify=cancel_on_status)
    query.set_sql("SELECT amount FROM events")

    async def apply():
        result = await vm.format_document()
        if result is not None:
            applied.append(result.sql)

    try:
        await asyncio.gather(asyncio.create_task(apply()), return_exceptions=True)
        assert not applied
        assert query.sql == "SELECT amount FROM events"
    finally:
        await close(vm, query)


async def close(vm, query):
    await vm.shutdown()
    await query.shutdown()
    query.dispose()


async def test_dirty_state_does_not_depend_on_draft_autosave(tmp_path):
    vm, query, _ = make_authoring(tmp_path)
    try:
        assert vm.path is None
        assert not vm.dirty
        query.set_sql("SELECT 1")
        assert vm.dirty
        path = tmp_path / "query.sql"
        path.write_text("SELECT 2")
        assert await vm.open_sql(path, confirm=yes)
        assert not vm.dirty
        query.set_sql("")
        assert vm.dirty
    finally:
        await close(vm, query)


async def test_open_decline_preserves_text_path_and_baseline(tmp_path):
    vm, query, _ = make_authoring(tmp_path)
    path = tmp_path / "query.sql"
    path.write_text("SELECT 2")
    try:
        query.set_sql("SELECT 1")
        before = vm.export_snapshot()
        calls = []

        async def decline(message):
            calls.append(message)
            return False

        assert not await vm.open_sql(path, confirm=decline)
        assert len(calls) == 1
        assert query.sql == "SELECT 1"
        assert vm.export_snapshot() == before
        assert vm.dirty
    finally:
        await close(vm, query)


async def test_edit_during_confirmation_invalidates_open_answer(tmp_path):
    vm, query, _ = make_authoring(tmp_path)
    path = tmp_path / "query.sql"
    path.write_text("SELECT 2")
    try:
        query.set_sql("SELECT 1")

        async def edit_and_accept(_message):
            query.set_sql("SELECT 3")
            return True

        assert not await vm.open_sql(path, confirm=edit_and_accept)
        assert query.sql == "SELECT 3"
        assert vm.path is None
        assert vm.dirty
    finally:
        await close(vm, query)


async def test_failed_open_is_recoverable_without_mutating_document(tmp_path):
    vm, query, _ = make_authoring(tmp_path)
    try:
        query.set_sql("SELECT 'private'")
        assert not await vm.open_sql(tmp_path / "missing.sql", confirm=yes)
        assert query.sql == "SELECT 'private'"
        assert vm.path is None
        assert "missing" in vm.status.lower()
        assert "private" not in vm.status
    finally:
        await close(vm, query)


async def test_save_and_save_as_adopt_exact_file_baseline(tmp_path):
    vm, query, _ = make_authoring(tmp_path)
    try:
        query.set_sql("SELECT 1")
        first = tmp_path / "one.sql"
        assert await vm.save_sql(first, confirm=yes)
        assert vm.path == first
        assert not vm.dirty
        query.set_sql("SELECT 2")
        assert vm.dirty
        assert await vm.save_sql(confirm=yes)
        assert first.read_text() == "SELECT 2"
        assert not vm.dirty
        second = tmp_path / "two.sql"
        assert await vm.save_sql(second, confirm=yes)
        assert vm.path == second
        assert first.read_text() == second.read_text() == "SELECT 2"
    finally:
        await close(vm, query)


async def test_save_as_needs_path_and_existing_target_confirmation(tmp_path):
    vm, query, _ = make_authoring(tmp_path)
    path = tmp_path / "query.sql"
    path.write_text("original")
    try:
        query.set_sql("SELECT 1")
        assert not await vm.save_sql(confirm=yes)
        assert "Save As" in vm.status
        assert not await vm.save_sql(path, confirm=no)
        assert path.read_text() == "original"
        assert vm.path is None
        assert vm.dirty
        assert await vm.save_sql(path, confirm=yes)
        assert path.read_text() == "SELECT 1"
    finally:
        await close(vm, query)


class GatedFiles(SqlFileStore):
    def __init__(self, *, after_publish=False):
        self.entered, self.release = threading.Event(), threading.Event()
        self.after_publish = after_publish

    def write(self, path, sql, **kwargs):
        result = super().write(path, sql, **kwargs) if self.after_publish else None
        self.entered.set()
        assert self.release.wait(5)
        return result if result is not None else super().write(path, sql, **kwargs)


async def test_edit_during_save_keeps_new_text_dirty_against_written_text(tmp_path):
    files = GatedFiles()
    vm, query, _ = make_authoring(tmp_path, files=files)
    saving = None
    path = tmp_path / "query.sql"
    try:
        query.set_sql("SELECT 1")
        saving = asyncio.create_task(vm.save_sql(path, confirm=yes))
        await wait_until(files.entered.is_set, what="SQL file worker started")
        query.set_sql("SELECT 2")
        files.release.set()
        assert await saving
        assert path.read_text() == "SELECT 1"
        assert query.sql == "SELECT 2"
        assert vm.path == path
        assert vm.dirty
    finally:
        files.release.set()
        if saving is not None:
            await asyncio.gather(saving, return_exceptions=True)
        await close(vm, query)


@pytest.mark.parametrize("after_publish", [False, True])
async def test_cancelled_save_drains_and_acknowledges_actual_publication(tmp_path, after_publish):
    files = GatedFiles(after_publish=after_publish)
    vm, query, _ = make_authoring(tmp_path, files=files)
    saving = None
    path = tmp_path / "query.sql"
    try:
        query.set_sql("SELECT 1")
        saving = asyncio.create_task(vm.save_sql(path, confirm=yes))
        await wait_until(files.entered.is_set, what="SQL file worker started")
        saving.cancel()
        await asyncio.sleep(0)
        assert not saving.done(), "Cancelled thread work was abandoned"
        files.release.set()
        result = await saving
        if after_publish:
            assert result
            assert path.read_text() == "SELECT 1"
            assert vm.path == path
            assert not vm.dirty
            assert "saved" in vm.status.lower()
        else:
            assert not result
            assert not path.exists()
            assert vm.path is None
            assert vm.dirty
            assert "cancelled" in vm.status.lower()
    finally:
        files.release.set()
        if saving is not None:
            await asyncio.gather(saving, return_exceptions=True)
        await close(vm, query)


async def test_source_context_change_during_save_does_not_attach_old_document(tmp_path):
    files = GatedFiles()
    vm, query, _ = make_authoring(tmp_path, files=files)
    saving = None
    path = tmp_path / "query.sql"
    try:
        query.set_sql("SELECT 1")
        saving = asyncio.create_task(vm.save_sql(path, confirm=yes))
        await wait_until(files.entered.is_set, what="SQL file worker started")
        await query.set_context(replace(query.context, database="different"))
        files.release.set()
        assert await saving
        assert path.read_text() == "SELECT 1"
        assert vm.path is None
        assert vm.dirty
    finally:
        files.release.set()
        if saving is not None:
            await asyncio.gather(saving, return_exceptions=True)
        await close(vm, query)


async def test_saved_publication_without_version_uses_fresh_inspection_next_time(tmp_path):
    class MissingAckVersion(SqlFileStore):
        def write(self, path, sql, **kwargs):
            saved = super().write(path, sql, **kwargs)
            return SqlFile(saved.path, saved.sql, None)

    vm, query, _ = make_authoring(tmp_path, files=MissingAckVersion())
    path = tmp_path / "query.sql"
    try:
        query.set_sql("SELECT 1")
        assert await vm.save_sql(path, confirm=yes)
        assert not vm.dirty
        query.set_sql("SELECT 2")
        assert await vm.save_sql(confirm=yes)
        assert path.read_text() == "SELECT 2"
        assert not vm.dirty
    finally:
        await close(vm, query)


async def test_common_replace_guard_rejects_decline_and_stale_target(tmp_path):
    vm, query, _ = make_authoring(tmp_path)
    try:
        query.set_sql("SELECT 1")
        assert not await vm.replace_sql("SELECT 2", confirm=no, origin="AWS named query")
        assert query.sql == "SELECT 1"
        assert not await vm.replace_sql(
            "SELECT 2", confirm=yes, origin="AWS named query", still_selected=lambda: False
        )
        assert query.sql == "SELECT 1"
        assert await vm.replace_sql("SELECT 2", confirm=yes, origin="AWS named query")
        assert query.sql == "SELECT 2"
        assert not vm.dirty
    finally:
        await close(vm, query)


async def test_document_snapshot_preserves_path_and_dirty_without_loading_sql(tmp_path):
    vm, query, _ = make_authoring(tmp_path)
    path = tmp_path / "query.sql"
    path.write_text("SELECT 1")
    try:
        assert await vm.open_sql(path, confirm=yes)
        query.set_sql("SELECT 2")
        snapshot = vm.export_snapshot()
        assert "SELECT" not in repr(snapshot)
        assert await vm.replace_sql("SELECT 3", confirm=yes, origin="AWS named query")
        query.set_sql("SELECT 2")
        vm.install_snapshot(snapshot)
        assert vm.path == path
        assert vm.dirty
        before = vm.export_snapshot()
        with pytest.raises(ValueError, match="snapshot is unavailable"):
            vm.install_snapshot(replace(snapshot, path="invalid\x00.sql"))
        assert vm.export_snapshot() == before
        assert query.sql == "SELECT 2"
    finally:
        await close(vm, query)


async def test_snippets_have_provenance_and_do_not_replace_or_execute_sql(tmp_path):
    vm, query, client = make_authoring(tmp_path)
    try:
        query.set_sql("SELECT 1")
        assert await vm.save_snippet("Example")
        assert await vm.load_snippets()
        assert len(vm.snippets) == 1
        saved = vm.snippets[0]
        assert saved.name == "Example"
        assert "Local editor" in saved.provenance
        query.set_sql("SELECT 2")
        assert vm.snippet_sql(saved.id) == "SELECT 1"
        assert query.sql == "SELECT 2"
        assert client.start_calls == []
    finally:
        await close(vm, query)


async def test_closed_authoring_rejects_late_requests_without_writes(tmp_path):
    vm, query, client = make_authoring(tmp_path)
    try:
        query.set_sql("SELECT 1")
        vm.close_input_admission()
        assert not await vm.save_sql(tmp_path / "query.sql", confirm=yes)
        assert not await vm.save_snippet("Example")
        assert not await vm.replace_sql("SELECT 2", confirm=yes, origin="AWS named query")
        assert query.sql == "SELECT 1"
        assert not (tmp_path / "query.sql").exists()
        assert client.start_calls == []
    finally:
        await close(vm, query)


@pytest.mark.parametrize("reentrant", ["document", "text_round_trip", "context_revision", "closed"])
async def test_reentrant_sql_notification_cannot_overwrite_newer_document(tmp_path, reentrant):
    revision = [0]
    vm, query, _ = make_authoring(tmp_path, context_revision=lambda: revision[0])
    subscription = None
    try:
        query.set_sql("SELECT 1")
        before = vm.export_snapshot()
        replacement = replace(
            before, path=str(tmp_path / "new.sql"), baseline="SELECT 99", origin="Local file"
        )
        handled = False

        def changed(property_name):
            nonlocal handled
            if property_name != "sql" or handled:
                return
            handled = True
            if reentrant == "document":
                vm.install_snapshot(replacement)
            elif reentrant == "text_round_trip":
                query.set_sql("SELECT 3")
                query.set_sql("SELECT 2")
            elif reentrant == "context_revision":
                revision[0] += 1
            else:
                vm.close_input_admission()

        subscription = query.on_property_changed.subscribe(changed)
        assert not await vm.replace_sql("SELECT 2", confirm=yes, origin="AWS named query")
        assert handled
        assert vm.export_snapshot() == (replacement if reentrant == "document" else before)
        assert vm.dirty
    finally:
        if subscription is not None:
            subscription.dispose()
        await close(vm, query)


async def test_queued_save_as_cannot_save_a_newer_replacement_document(tmp_path):
    files = GatedFiles()
    vm, query, _ = make_authoring(tmp_path, files=files)
    saving = queued = None
    first, second = tmp_path / "one.sql", tmp_path / "queued.sql"
    try:
        query.set_sql("SELECT 1")
        saving = asyncio.create_task(vm.save_sql(first, confirm=yes))
        await wait_until(files.entered.is_set, what="first SQL Save worker")
        queued = asyncio.create_task(vm.save_sql(second, confirm=yes))
        await asyncio.sleep(0)
        assert await vm.replace_sql("SELECT 9", confirm=yes, origin="AWS named query")
        files.release.set()
        assert await saving
        assert not await queued
        assert first.read_text() == "SELECT 1"
        assert not second.exists()
        assert query.sql == "SELECT 9"
        assert vm.path is None
    finally:
        files.release.set()
        await asyncio.gather(
            *(task for task in (saving, queued) if task is not None), return_exceptions=True
        )
        await close(vm, query)


async def test_shutdown_retires_confirmation_wait_and_prevents_late_notification(tmp_path):
    notifications = []
    vm, query, _ = make_authoring(tmp_path, notify=lambda: notifications.append("changed"))
    entered, release = asyncio.Event(), asyncio.Event()
    path = tmp_path / "query.sql"
    path.write_text("SELECT 2")
    opening = None

    async def confirm(_message):
        entered.set()
        await release.wait()
        return True

    try:
        query.set_sql("SELECT 1")
        opening = asyncio.create_task(vm.open_sql(path, confirm=confirm))
        await wait_until(entered.is_set, what="Open replacement confirmation")
        await vm.shutdown()
        assert opening.done()
        before = len(notifications)
        release.set()
        await asyncio.gather(opening, return_exceptions=True)
        assert len(notifications) == before
        assert query.sql == "SELECT 1"
        assert vm.path is None
    finally:
        release.set()
        if opening is not None:
            await asyncio.gather(opening, return_exceptions=True)
        await close(vm, query)


@pytest.mark.parametrize("operation", ["open", "save", "replace"])
async def test_swallowed_confirmation_cancellation_cannot_admit_fresh_mutation(tmp_path, operation):
    vm, query, _ = make_authoring(tmp_path)
    path = tmp_path / "query.sql"
    path.write_text("SELECT 2")

    async def cancelled_confirmation(_message):
        asyncio.current_task().cancel()
        try:
            await asyncio.sleep(0)
        except asyncio.CancelledError:
            return True
        raise AssertionError("Cancellation was not delivered")

    try:
        query.set_sql("SELECT 1")
        if operation == "open":
            action = vm.open_sql(path, confirm=cancelled_confirmation)
        elif operation == "save":
            action = vm.save_sql(path, confirm=cancelled_confirmation)
        else:
            action = vm.replace_sql(
                "SELECT 2", confirm=cancelled_confirmation, origin="AWS named query"
            )
        task = asyncio.create_task(action)
        assert not await task
        assert query.sql == "SELECT 1"
        assert path.read_text() == "SELECT 2"
        assert vm.path is None
        assert vm.dirty
    finally:
        await close(vm, query)


async def test_old_snippet_read_cannot_erase_newer_saved_library(tmp_path):
    class GatedSnippets(SqlSnippetStore):
        def __init__(self):
            super().__init__(path=tmp_path / "snippets.json")
            self.entered, self.release = threading.Event(), threading.Event()
            self.first = True

        def list(self):
            captured = super().list()
            if self.first:
                self.first = False
                self.entered.set()
                assert self.release.wait(5)
            return captured

    snippets = GatedSnippets()
    vm, query, _ = make_authoring(tmp_path, snippets=snippets)
    loading = None
    try:
        query.set_sql("SELECT 1")
        loading = asyncio.create_task(vm.load_snippets())
        await wait_until(snippets.entered.is_set, what="old local snippet read")
        assert await vm.save_snippet("New")
        assert [r.name for r in vm.snippets] == ["New"]
        snippets.release.set()
        assert not await loading
        assert [r.name for r in vm.snippets] == ["New"]
    finally:
        snippets.release.set()
        if loading is not None:
            await asyncio.gather(loading, return_exceptions=True)
        await close(vm, query)


async def test_shutdown_from_confirmation_does_not_join_its_own_action(tmp_path):
    vm, query, _ = make_authoring(tmp_path)

    async def close_and_accept(_message):
        await vm.shutdown()
        return True

    try:
        query.set_sql("SELECT 1")
        action = vm.replace_sql("SELECT 2", confirm=close_and_accept, origin="AWS named query")
        assert not await asyncio.wait_for(action, timeout=2)
        assert query.sql == "SELECT 1"
        assert vm.path is None
    finally:
        await close(vm, query)


async def test_shutdown_cancellation_still_drains_already_published_save(tmp_path):
    files = GatedFiles(after_publish=True)
    vm, query, _ = make_authoring(tmp_path, files=files)
    saving = shutting_down = None
    path = tmp_path / "query.sql"
    try:
        query.set_sql("SELECT 1")
        saving = asyncio.create_task(vm.save_sql(path, confirm=yes))
        await wait_until(files.entered.is_set, what="published SQL Save awaiting acknowledgement")
        shutting_down = asyncio.create_task(vm.shutdown())
        await asyncio.sleep(0)
        shutting_down.cancel()
        await asyncio.sleep(0)
        assert not shutting_down.done()
        files.release.set()
        await shutting_down
        assert saving.done()
        assert await saving
        assert path.read_text() == "SELECT 1"
        # Closed documents cannot attach late acknowledgements as a clean file.
        assert vm.path is None
        assert vm.dirty
    finally:
        files.release.set()
        await asyncio.gather(
            *(task for task in (saving, shutting_down) if task is not None), return_exceptions=True
        )
        await close(vm, query)


def test_owned_actions_cannot_form_a_shutdown_join_cycle(tmp_path):
    # A deadlocked implementation shields its drain indefinitely, including
    # against test-loop cleanup. Bound this actual two-action flow in an owned
    # subprocess; there are no filesystem workers, AWS calls or shared resources.
    code = textwrap.dedent("""
        import asyncio, os, sys
        from pathlib import Path
        from aws_tui.vm.athena.authoring_vm import AthenaAuthoringVM
        from aws_tui.infra.sql_snippet_store import SqlSnippetStore
        from tests.unit.vm.athena.test_query_vm import InMemoryAthena, make_query_vm

        async def main():
            client = InMemoryAthena()
            query = make_query_vm(client)
            vm = AthenaAuthoringVM(query=query, client=client, snippets=SqlSnippetStore(path=Path(sys.argv[1])))
            query.set_sql("SELECT 1")
            entered = asyncio.Event()
            never = asyncio.Event()
            async def first_confirmation(_):
                entered.set()
                try:
                    await never.wait()
                finally:
                    await vm.shutdown()
                return True
            async def second_confirmation(_):
                await vm.shutdown()
                return True
            first = asyncio.create_task(vm.replace_sql("SELECT 2", confirm=first_confirmation, origin="AWS named query"))
            await entered.wait()
            second = asyncio.create_task(vm.replace_sql("SELECT 3", confirm=second_confirmation, origin="AWS named query"))
            done, pending = await asyncio.wait({first, second}, timeout=1)
            if pending:
                os._exit(3)
            await asyncio.gather(*done, return_exceptions=True)
            await vm.shutdown()
            assert query.sql == "SELECT 1"
            await query.shutdown()
            query.dispose()
        asyncio.run(main())
    """)
    result = subprocess.run(
        [sys.executable, "-c", code, str(tmp_path / "snippets.json")],
        capture_output=True,
        text=True,
        timeout=5,
        check=False,
    )
    assert result.returncode == 0, result.stderr or "Owned shutdown actions did not drain"
