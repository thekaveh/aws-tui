from __future__ import annotations

import asyncio
from dataclasses import replace
from threading import Event

import pytest

from aws_tui.domain.query import QueryContext
from aws_tui.infra.athena_draft_store import DraftPermit, DraftStoreResult, draft_id
from aws_tui.vm.service_source_vm import ServiceSelectionStore
from tests.athena_drafts_helpers import CTX, record, runtime_at
from tests.helpers import wait_until
from tests.unit.vm.athena.test_draft_recovery import yes
from tests.unit.vm.athena.test_page_vm import PageClient, make_page_vm


@pytest.mark.parametrize("staged", [False, True])
async def test_replacement_preserves_foreign_draft_origin_and_run_guard(tmp_path, staged):
    runtime, _store = runtime_at(tmp_path)
    client = PageClient()
    client.databases["primary", "AwsDataCatalog"].append("other_database")
    selections = ServiceSelectionStore()
    source = make_page_vm(client, selection_store=selections, drafts=runtime, source_is_current=yes)
    replacement = None
    try:
        await source.setup()
        origin = source.context
        sql = "SELECT * FROM unqualified_table"
        source.query.set_sql(sql)
        await source.select_database("other_database")
        assert source.query.draft_execution_blocked
        snapshot = source.export_snapshot()
        await source.shutdown()
        source.dispose()

        replacement = make_page_vm(
            client,
            selection_store=selections,
            drafts=runtime,
            source_is_current=yes,
            drafts_active=not staged,
        )
        await replacement.setup()
        await replacement.restore_snapshot(snapshot)
        if staged:
            replacement.activate_drafts()

        assert replacement.query.sql == sql
        assert replacement.context.database == "other_database"
        assert replacement._draft_session.bound_context == origin
        assert replacement.query.draft_execution_blocked
        assert replacement.query.draft_state == "context_required"
        assert not replacement.query.execute_command.can_execute()
        await replacement.query.execute()
        assert client.start_calls == []

        await replacement.select_database(origin.database)
        assert not replacement.query.draft_execution_blocked
        await replacement.query.execute()
        assert len(client.start_calls) == 1
        assert client.start_calls[0][1] == origin
    finally:
        await source.shutdown()
        source.dispose()
        if replacement is not None:
            await replacement.shutdown()
            replacement.dispose()
        await runtime.shutdown()
        runtime.dispose()


@pytest.mark.parametrize("state", ["saved", "pending", "error", "deleted"])
@pytest.mark.parametrize("staged", [False, True])
async def test_replacement_preserves_acknowledgement_and_unsaved_state(
    tmp_path, monkeypatch, state, staged
):
    runtime, store = runtime_at(tmp_path)
    source = make_page_vm(PageClient(), drafts=runtime, source_is_current=yes)
    target = make_page_vm(
        PageClient(), drafts=runtime, source_is_current=yes, drafts_active=not staged
    )
    try:
        await source.setup()
        source.query.set_sql("SELECT 'ACKNOWLEDGED'")
        await source._draft_session.flush(deadline=asyncio.get_running_loop().time() + 2)
        assert source.query.draft_state == "saved"
        monkeypatch.setattr("aws_tui.vm.athena.drafts_vm.DEBOUNCE_SECONDS", 3600)
        if state in {"pending", "error"}:
            source.query.set_sql("SELECT 'UNSAVED'")
            if state == "error":
                monkeypatch.setattr(
                    store, "save", lambda record, *, permit: DraftStoreResult(code="io")
                )
                await source._draft_session.flush(deadline=asyncio.get_running_loop().time() + 2)
        elif state == "deleted":
            assert await runtime.delete(draft_id(source.context.cache_key))
        snapshot = source.export_snapshot()
        session = source._draft_session
        before_saved = session._saved_sql, session._saved_context
        before_unsaved = session.has_unsaved_text(source.query.sql, source.context)
        before_records = store.list().records
        old_capture = session._capture

        await target.setup()
        await target.restore_snapshot(snapshot)

        restored = target._draft_session
        assert target.query.sql == source.query.sql
        assert restored.bound_context == session.bound_context
        assert (restored._saved_sql, restored._saved_context) == before_saved
        assert restored.has_unsaved_text(target.query.sql, target.context) == before_unsaved
        assert restored.editor_revision == snapshot.query.draft.editor_revision
        assert target.query.draft_state == ("empty" if state == "deleted" else state)
        assert store.list().records == before_records
        if staged and state == "pending":
            assert restored._capture is None
            assert old_capture is not None
            assert not old_capture.permit.cancelled
        if staged:
            target.activate_drafts()
        if state == "pending":
            assert restored._capture is not None
            assert restored._capture is not old_capture
            assert restored._capture.captured_editor_revision == restored.editor_revision
            assert old_capture.permit.cancelled
        assert store.list().records == before_records
    finally:
        await source.shutdown()
        source.dispose()
        await target.shutdown()
        target.dispose()
        await runtime.shutdown()
        runtime.dispose()


async def test_restored_foreign_editor_can_be_explicitly_cleared_and_retyped(tmp_path):
    runtime, _store = runtime_at(tmp_path)
    client = PageClient()
    client.databases["primary", "AwsDataCatalog"].append("other_database")
    source = make_page_vm(client, drafts=runtime, source_is_current=yes)
    target = make_page_vm(client, drafts=runtime, source_is_current=yes)
    try:
        await source.setup()
        source.query.set_sql("SELECT * FROM original_table")
        await source.select_database("other_database")
        snapshot = source.export_snapshot()
        await target.setup()
        await target.restore_snapshot(snapshot)
        assert target.query.draft_execution_blocked

        target.query.set_sql("")
        target.query.set_sql("SELECT 1")

        assert not target.query.draft_execution_blocked
        assert target._draft_session.bound_context == target.context
        await target.query.execute()
        assert client.start_calls[0][1].database == "other_database"
    finally:
        await source.shutdown()
        source.dispose()
        await target.shutdown()
        target.dispose()
        await runtime.shutdown()
        runtime.dispose()


async def test_replacement_preserves_recovery_decision_and_explanation(tmp_path):
    runtime, _store = runtime_at(tmp_path)
    source = make_page_vm(PageClient(), drafts=runtime, source_is_current=yes)
    replacement = make_page_vm(PageClient(), drafts=runtime, source_is_current=yes)
    try:
        await source.setup()
        source.query.set_sql("SELECT 1")
        source.query.set_draft_recovery_guard(True)
        source._draft_recovery_error = "Draft context is unavailable or changed."
        snapshot = source.export_snapshot()
        await replacement.setup()
        await replacement.restore_snapshot(snapshot)
        assert replacement.query.draft_recovery_guard
        assert replacement.query.draft_execution_blocked
        assert replacement.draft_recovery_error == source.draft_recovery_error
        assert not replacement.query.execute_command.can_execute()
    finally:
        await source.shutdown()
        source.dispose()
        await replacement.shutdown()
        replacement.dispose()
        await runtime.shutdown()
        runtime.dispose()


@pytest.mark.parametrize(
    "defect",
    [
        "missing",
        "wrong_sql",
        "bad_revision",
        "unconfirmed_saved",
        "invalid_origin",
        "bad_pending_context",
    ],
)
async def test_invalid_provenance_is_rejected_before_any_restore_mutation(tmp_path, defect):
    runtime, _store = runtime_at(tmp_path)
    source = make_page_vm(PageClient(), drafts=runtime, source_is_current=yes)
    target = make_page_vm(PageClient(), drafts=runtime, source_is_current=yes)
    try:
        await source.setup()
        source.query.set_sql("SELECT 'SOURCE_SECRET'")
        await source._draft_session.flush(deadline=asyncio.get_running_loop().time() + 2)
        snapshot = source.export_snapshot()
        draft = snapshot.query.draft
        if defect == "missing":
            draft = None
        elif defect == "wrong_sql":
            draft = replace(draft, sql="SELECT 'OTHER_SECRET'")
        elif defect == "bad_revision":
            draft = replace(draft, editor_revision=True)
        elif defect == "unconfirmed_saved":
            draft = replace(draft, saved_sql="SELECT 'UNACKNOWLEDGED_SECRET'")
        elif defect == "invalid_origin":
            draft = replace(draft, origin_revision=("unrelated-origin", 1))
        else:
            draft = replace(
                draft, pending_context=replace(source.context, database="other_database")
            )
        snapshot = replace(snapshot, query=replace(snapshot.query, draft=draft))
        await target.setup()
        target.query.set_sql("SELECT 'CURRENT_SECRET'")
        before = (
            target.export_snapshot(),
            target._snapshot_restore_token(),
            target.query.snapshot_generation,
        )

        with pytest.raises(ValueError, match=r"Athena .* (invalid|unavailable)") as exc:
            await target.restore_snapshot(snapshot)

        assert "SECRET" not in str(exc.value)
        assert (
            target.export_snapshot(),
            target._snapshot_restore_token(),
            target.query.snapshot_generation,
        ) == before
    finally:
        await source.shutdown()
        source.dispose()
        await target.shutdown()
        target.dispose()
        await runtime.shutdown()
        runtime.dispose()


async def test_pending_foreign_origin_clear_keeps_its_exact_deletion_target(tmp_path, monkeypatch):
    runtime, store = runtime_at(tmp_path)
    client = PageClient()
    client.databases["primary", "AwsDataCatalog"].append("other_database")
    source = make_page_vm(client, drafts=runtime, source_is_current=yes)
    target = make_page_vm(client, drafts=runtime, source_is_current=yes)
    try:
        await source.setup()
        origin = source.context
        source.query.set_sql("SELECT 'ORIGINAL'")
        await source._draft_session.flush(deadline=asyncio.get_running_loop().time() + 2)
        await source.select_database("other_database")
        other = record(source.context.cache_key, "SELECT 'PROTECTED_OTHER'")
        assert store.save(other, permit=DraftPermit()).code is None
        monkeypatch.setattr("aws_tui.vm.athena.drafts_vm.DEBOUNCE_SECONDS", 3600)
        source.query.set_sql("")
        assert source._draft_session._capture.record.context == origin.cache_key
        snapshot = source.export_snapshot()
        await target.setup()
        await target.restore_snapshot(snapshot)

        assert target.query.sql == ""
        assert target._draft_session._capture.record.context == origin.cache_key
        await target.shutdown()
        records = store.list().records
        assert len(records) == 1
        assert records[0].context == other.context
        assert records[0].sql == other.sql
    finally:
        await source.shutdown()
        source.dispose()
        await target.shutdown()
        target.dispose()
        await runtime.shutdown()
        runtime.dispose()


async def test_transfer_does_not_clear_a_newer_recovery_guard(tmp_path):
    runtime, _store = runtime_at(tmp_path)
    source = make_page_vm(PageClient(), drafts=runtime, source_is_current=yes)
    target = make_page_vm(PageClient(), drafts=runtime, source_is_current=yes)
    try:
        await source.setup()
        source.query.set_sql("SELECT 1")
        snapshot = source.export_snapshot()
        await target.setup()
        target.query.set_draft_recovery_guard(True)
        target._draft_recovery_error = "Draft context is unavailable or changed."

        await target.restore_snapshot(snapshot)

        assert target.query.draft_recovery_guard
        assert target.query.draft_execution_blocked
        assert target.draft_recovery_error == "Draft context is unavailable or changed."
    finally:
        await source.shutdown()
        source.dispose()
        await target.shutdown()
        target.dispose()
        await runtime.shutdown()
        runtime.dispose()


@pytest.mark.parametrize("staged", [False, True])
async def test_late_save_callback_cannot_acknowledge_a_replacement_edit(
    tmp_path, monkeypatch, staged
):
    runtime, store = runtime_at(tmp_path)
    source = make_page_vm(PageClient(), drafts=runtime, source_is_current=yes)
    target = make_page_vm(
        PageClient(), drafts=runtime, source_is_current=yes, drafts_active=not staged
    )
    entered, release = Event(), Event()
    try:
        await source.setup()
        source.query.set_sql("SELECT 'ACKNOWLEDGED'")
        await source._draft_session.flush(deadline=asyncio.get_running_loop().time() + 2)
        original_save = store.save

        def delayed_acknowledgement(record, *, permit):
            result = original_save(record, permit=permit)
            if record.sql == "SELECT 'OLD_COMMITTED'":
                entered.set()
                release.wait()
            return result

        monkeypatch.setattr(store, "save", delayed_acknowledgement)
        source.query.set_sql("SELECT 'OLD_COMMITTED'")
        await wait_until(entered.is_set, what="old SQL committed before delayed acknowledgement")
        snapshot = source.export_snapshot()
        await target.setup()
        await target.restore_snapshot(snapshot)
        if staged:
            target.activate_drafts()
        target.query.set_sql("SELECT 'NEW_EDITOR'")
        assert target._draft_session._saved_sql == "SELECT 'ACKNOWLEDGED'"
        assert target.query.draft_state == "pending"
        observed = []
        subscription = target.query.on_property_changed.subscribe(
            lambda _name: observed.append(target._draft_session._saved_sql)
        )
        try:
            release.set()
            await target._draft_session.flush(deadline=asyncio.get_running_loop().time() + 2)
            assert target.query.draft_state == "saved"
            assert target._draft_session._saved_sql == "SELECT 'NEW_EDITOR'"
            assert "SELECT 'OLD_COMMITTED'" not in observed
            assert store.list().records[0].sql == "SELECT 'NEW_EDITOR'"
        finally:
            subscription.dispose()
    finally:
        release.set()
        await source.shutdown()
        source.dispose()
        await target.shutdown()
        target.dispose()
        await runtime.shutdown()
        runtime.dispose()


async def test_draft_runtime_retirement_during_preflight_is_atomic(tmp_path):
    entered, release = asyncio.Event(), asyncio.Event()

    class GatedClient(PageClient):
        gate = False

        async def get_workgroup(self, name):
            if self.gate and name == "analysts":
                entered.set()
                await release.wait()
            return await super().get_workgroup(name)

    runtime, _store = runtime_at(tmp_path)
    client = GatedClient()
    source = make_page_vm(client, drafts=runtime, source_is_current=yes)
    target = make_page_vm(client, drafts=runtime, source_is_current=yes)
    restoring = None
    try:
        await source.setup()
        await source.select_workgroup("analysts")
        source.query.set_sql("SELECT 1")
        await source._draft_session.flush(deadline=asyncio.get_running_loop().time() + 2)
        snapshot = source.export_snapshot()
        await target.setup()
        before = (
            target.export_snapshot(),
            target._snapshot_restore_token(),
            target.query.snapshot_generation,
        )
        client.gate = True
        restoring = asyncio.create_task(target.restore_snapshot(snapshot))
        await asyncio.wait_for(entered.wait(), timeout=1)
        await runtime.shutdown()
        release.set()

        with pytest.raises(ValueError, match=r"Athena .* unavailable"):
            await restoring

        assert (
            target.export_snapshot(),
            target._snapshot_restore_token(),
            target.query.snapshot_generation,
        ) == before
    finally:
        release.set()
        if restoring is not None:
            await asyncio.gather(restoring, return_exceptions=True)
        await source.shutdown()
        source.dispose()
        await target.shutdown()
        target.dispose()
        await runtime.shutdown()
        runtime.dispose()


async def test_editor_import_rejects_hostile_metadata_without_invoking_it(tmp_path):
    class Hostile:
        def __eq__(self, _other):
            raise RuntimeError("PRIVATE_METADATA_SECRET")

        def __repr__(self):
            raise RuntimeError("PRIVATE_METADATA_SECRET")

    runtime, _store = runtime_at(tmp_path)
    source = make_page_vm(PageClient(), drafts=runtime, source_is_current=yes)
    target = make_page_vm(PageClient(), drafts=runtime, source_is_current=yes)
    try:
        await source.setup()
        source.query.set_sql("SELECT 'PRIVATE_SQL_SECRET'")
        snapshot = source.export_editor_snapshot()
        bad_draft = replace(snapshot.editor.draft, origin_revision=(Hostile(), 1))
        snapshot = replace(snapshot, editor=replace(snapshot.editor, draft=bad_draft))
        await target.setup()
        before = target.export_snapshot()

        with pytest.raises(ValueError, match="Athena editor provenance is unavailable"):
            target.install_editor_snapshot(snapshot)

        assert target.export_snapshot() == before
        assert "PRIVATE_" not in repr(snapshot)
    finally:
        await source.shutdown()
        source.dispose()
        await target.shutdown()
        target.dispose()
        await runtime.shutdown()
        runtime.dispose()


async def test_transferred_unsaved_editor_can_recover_exact_original_draft(tmp_path):
    runtime, _store = runtime_at(tmp_path)
    client = PageClient()
    client.databases["primary", "AwsDataCatalog"].append("other_database")
    source = make_page_vm(client, drafts=runtime, source_is_current=yes)
    target = make_page_vm(client, drafts=runtime, source_is_current=yes)
    try:
        await source.setup()
        origin = source.context
        source.query.set_sql("SELECT 'SAVED_ORIGINAL'")
        await source._draft_session.flush(deadline=asyncio.get_running_loop().time() + 2)
        await source.select_database("other_database")
        source.query.set_sql("SELECT 'UNSAVED_EDITOR'")
        snapshot = source.export_snapshot()
        await target.setup()
        await target.restore_snapshot(snapshot)
        assert target.query.draft_execution_blocked
        await target.select_database(origin.database)

        assert await target.restore_draft(draft_id(origin.cache_key), yes)

        assert target.query.sql == "SELECT 'SAVED_ORIGINAL'"
        assert target._draft_session.bound_context == origin
        assert target.query.draft_state == "saved"
        assert not target._draft_session.has_unsaved_text(target.query.sql, target.context)
        assert client.start_calls == []
        await target.query.execute()
        assert client.start_calls[0][1] == origin
    finally:
        await source.shutdown()
        source.dispose()
        await target.shutdown()
        target.dispose()
        await runtime.shutdown()
        runtime.dispose()


async def test_discarding_staged_editor_preserves_live_save_ownership(tmp_path, monkeypatch):
    runtime, store = runtime_at(tmp_path)
    source = make_page_vm(PageClient(), drafts=runtime, source_is_current=yes)
    target = make_page_vm(PageClient(), drafts=runtime, source_is_current=yes, drafts_active=False)
    try:
        await source.setup()
        monkeypatch.setattr("aws_tui.vm.athena.drafts_vm.DEBOUNCE_SECONDS", 3600)
        source.query.set_sql("SELECT 'LIVE_EDITOR'")
        capture = source._draft_session._capture
        snapshot = source.export_snapshot()
        await target.setup()
        await target.restore_snapshot(snapshot)
        await target.shutdown()
        target.dispose()

        assert not capture.permit.cancelled
        assert runtime._coordinator.current[capture.record.id] is capture
        assert not store.list().records
        await source._draft_session.flush(deadline=asyncio.get_running_loop().time() + 2)
        assert source.query.draft_state == "saved"
        assert store.list().records[0].sql == "SELECT 'LIVE_EDITOR'"
    finally:
        await source.shutdown()
        source.dispose()
        await target.shutdown()
        target.dispose()
        await runtime.shutdown()
        runtime.dispose()


@pytest.mark.parametrize("staged", [False, True])
@pytest.mark.parametrize("foreign", [False, True])
@pytest.mark.parametrize("newer_pending", [False, True])
async def test_superseded_saved_transfer_is_unsaved_without_replacing_newer_writer(
    tmp_path, monkeypatch, staged, foreign, newer_pending
):
    runtime, store = runtime_at(tmp_path)
    origin = QueryContext(*CTX)
    selected = replace(origin, database="other_database") if foreign else origin
    source = runtime.open_session()
    target = runtime.open_session(active=not staged)
    newer = runtime.open_session()
    try:
        source.edited("SELECT 'OLD_SAVED'", origin)
        await source.flush(deadline=asyncio.get_running_loop().time() + 2)
        snapshot = source.export_snapshot()
        assert snapshot.state == "saved"
        if staged:
            target.install_snapshot(snapshot, context=selected)

        monkeypatch.setattr("aws_tui.vm.athena.drafts_vm.DEBOUNCE_SECONDS", 3600)
        newer.edited("SELECT 'NEW_DURABLE'", origin)
        capture = newer._capture
        if not newer_pending:
            await newer.flush(deadline=asyncio.get_running_loop().time() + 2)
        revision = runtime._coordinator.latest_revision[capture.record.id]
        before_records = store.list().records

        if staged:
            target.activate(snapshot.sql, selected)
        else:
            target.install_snapshot(snapshot, context=selected)

        assert target._sql == snapshot.sql
        assert target.bound_context == origin
        assert target.state == ("context_required" if foreign else "error")
        assert target.has_unsaved_text(snapshot.sql, selected)
        assert target._saved_sql is None
        assert target._capture is None
        assert not capture.permit.cancelled
        assert runtime._coordinator.current[capture.record.id] is capture
        assert runtime._coordinator.latest_revision[capture.record.id] == revision
        assert store.list().records == before_records

        # A newer writer's successful callback cannot acknowledge retained old SQL.
        await newer.flush(deadline=asyncio.get_running_loop().time() + 2)
        assert store.list().records[0].sql == "SELECT 'NEW_DURABLE'"
        report = await target.flush(deadline=asyncio.get_running_loop().time() + 2)
        assert report.unpersisted == 1
        if foreign:
            target.context_changed(origin)
            assert target.state == "error"
        target.detach()
        source.detach()
        newer.detach()
        assert (await runtime.shutdown()).unpersisted == 1
        assert store.list().records[0].sql == "SELECT 'NEW_DURABLE'"
    finally:
        source.detach()
        target.detach()
        newer.detach()
        await runtime.shutdown()
        runtime.dispose()


@pytest.mark.parametrize("resolution", ["edit", "recover", "delete"])
async def test_superseded_transfer_obligation_can_be_explicitly_resolved(tmp_path, resolution):
    runtime, store = runtime_at(tmp_path)
    context = QueryContext(*CTX)
    source, newer, target = (runtime.open_session() for _ in range(3))
    try:
        source.edited("SELECT 'OLD_SAVED'", context)
        await source.flush(deadline=asyncio.get_running_loop().time() + 2)
        snapshot = source.export_snapshot()
        newer.edited("SELECT 'NEW_DURABLE'", context)
        await newer.flush(deadline=asyncio.get_running_loop().time() + 2)
        target.install_snapshot(snapshot, context=context)
        assert runtime._unconfirmed_count() == 1

        # Another presentation replacement must retain the unsaved disposition.
        target.install_snapshot(target.export_snapshot(), context=context)
        assert runtime._unconfirmed_count() == 1
        assert target.state == "error"
        if resolution == "edit":
            target.edited("", context)
            target.edited("SELECT 'EXPLICIT_EDIT'", context)
            await target.flush(deadline=asyncio.get_running_loop().time() + 2)
            assert store.list().records[0].sql == "SELECT 'EXPLICIT_EDIT'"
            assert target.state == "saved"
        elif resolution == "recover":
            target.recovered(store.list().records[0])
            assert target._sql == "SELECT 'NEW_DURABLE'"
            assert target.state == "saved"
        else:
            assert await runtime.delete(draft_id(context.cache_key))
            assert not store.list().records
            assert target.state == "empty"
        assert runtime._unconfirmed_count() == 0
        assert (await runtime.shutdown()).unpersisted == 0
    finally:
        source.detach()
        newer.detach()
        target.detach()
        await runtime.shutdown()
        runtime.dispose()


@pytest.mark.parametrize("retry_on_shutdown", [False, True])
async def test_retained_transfer_survives_later_writer_edits_and_shutdown_retry(
    tmp_path, monkeypatch, retry_on_shutdown
):
    runtime, store = runtime_at(tmp_path)
    context = QueryContext(*CTX)
    source, newer, target = (runtime.open_session() for _ in range(3))
    original_save = store.save
    try:
        source.edited("SELECT 'RETAINED_OLD'", context)
        await source.flush(deadline=asyncio.get_running_loop().time() + 2)
        snapshot = source.export_snapshot()
        if retry_on_shutdown:
            monkeypatch.setattr(
                store, "save", lambda record, *, permit: DraftStoreResult(code="io")
            )
        newer.edited("SELECT 'NEW_DURABLE'", context)
        await newer.flush(deadline=asyncio.get_running_loop().time() + 2)
        target.install_snapshot(snapshot, context=context)
        assert runtime._unconfirmed_count() == 1
        monkeypatch.setattr(store, "save", original_save)
        if not retry_on_shutdown:
            newer.edited("SELECT 'NEWER_AGAIN'", context)
            await newer.flush(deadline=asyncio.get_running_loop().time() + 2)
            assert (
                await target.flush(deadline=asyncio.get_running_loop().time() + 2)
            ).unpersisted == 1
            target.detach()
        report = await runtime.shutdown()
        assert report.unpersisted == 1
        assert target._sql == "SELECT 'RETAINED_OLD'"
        assert target.has_unsaved_text(target._sql, context)
        assert store.list().records[0].sql == (
            "SELECT 'NEW_DURABLE'" if retry_on_shutdown else "SELECT 'NEWER_AGAIN'"
        )
    finally:
        monkeypatch.setattr(store, "save", original_save)
        source.detach()
        newer.detach()
        target.detach()
        await runtime.shutdown()
        runtime.dispose()


@pytest.mark.parametrize("mutation", ["delete", "clear"])
async def test_saved_snapshot_from_before_draft_deletion_is_no_longer_acknowledged(
    tmp_path, mutation
):
    runtime, store = runtime_at(tmp_path)
    context = QueryContext(*CTX)
    source, target = (runtime.open_session() for _ in range(2))
    try:
        source.edited("SELECT 'DELETED_DURABLE'", context)
        await source.flush(deadline=asyncio.get_running_loop().time() + 2)
        snapshot = source.export_snapshot()
        if mutation == "delete":
            assert await runtime.delete(draft_id(context.cache_key))
        else:
            assert await runtime.clear()
        target.install_snapshot(snapshot, context=context)
        assert target.state == "error"
        assert target.has_unsaved_text(target._sql, context)
        assert not store.list().records
        target.detach()
        assert (await runtime.shutdown()).unpersisted == 1
        assert not store.list().records
    finally:
        source.detach()
        target.detach()
        await runtime.shutdown()
        runtime.dispose()


async def test_staged_activation_publishes_a_changed_draft_acknowledgement(tmp_path):
    runtime, _store = runtime_at(tmp_path)
    source = make_page_vm(PageClient(), drafts=runtime, source_is_current=yes)
    target = make_page_vm(PageClient(), drafts=runtime, source_is_current=yes, drafts_active=False)
    newer = runtime.open_session()
    subscription = None
    try:
        await source.setup()
        source.query.set_sql("SELECT 'OLD_SAVED'")
        await source._draft_session.flush(deadline=asyncio.get_running_loop().time() + 2)
        await target.setup()
        await target.restore_snapshot(source.export_snapshot())
        events = []
        subscription = target.query.on_property_changed.subscribe(
            lambda name: events.append((name, target.query.draft_state))
        )
        newer.edited("SELECT 'NEW_DURABLE'", source.context)
        await newer.flush(deadline=asyncio.get_running_loop().time() + 2)
        target.activate_drafts()
        assert target.query.draft_state == "error"
        assert ("draft_state", "error") in events
        assert ("draft_error_text", "error") in events
    finally:
        if subscription is not None:
            subscription.dispose()
        await source.shutdown()
        source.dispose()
        await target.shutdown()
        target.dispose()
        newer.detach()
        await runtime.shutdown()
        runtime.dispose()


@pytest.mark.parametrize("retained", [False, True])
async def test_reinstalling_an_inactive_snapshot_never_releases_its_live_owner(
    tmp_path, monkeypatch, retained
):
    runtime, store = runtime_at(tmp_path)
    context = QueryContext(*CTX)
    source, newer, owner = (runtime.open_session() for _ in range(3))
    candidate = runtime.open_session(active=False)
    try:
        source.edited("SELECT 'ORIGINAL'", context)
        if retained:
            await source.flush(deadline=asyncio.get_running_loop().time() + 2)
            saved = source.export_snapshot()
            newer.edited("SELECT 'NEW_DURABLE'", context)
            await newer.flush(deadline=asyncio.get_running_loop().time() + 2)
            owner.install_snapshot(saved, context=context)
            snapshot = owner.export_snapshot()
        else:
            snapshot = source.export_snapshot()
        monkeypatch.setattr("aws_tui.vm.athena.drafts_vm.DEBOUNCE_SECONDS", 3600)
        coordinator = runtime._coordinator
        captures = dict(coordinator.current)
        tokens = {
            identity: set(values) for identity, values in coordinator.retained_unconfirmed.items()
        }
        for _ in range(2):
            candidate.install_snapshot(snapshot, context=context)
            assert coordinator.current == captures
            assert coordinator.retained_unconfirmed == tokens
            assert all(not capture.permit.cancelled for capture in captures.values())
            assert runtime._unconfirmed_count() == 1
        candidate.detach()
        if retained:
            owner.detach()
            assert (await runtime.shutdown()).unpersisted == 1
            assert store.list().records[0].sql == "SELECT 'NEW_DURABLE'"
        else:
            await source.flush(deadline=asyncio.get_running_loop().time() + 2)
            assert source.state == "saved"
            assert store.list().records[0].sql == "SELECT 'ORIGINAL'"
    finally:
        source.detach()
        newer.detach()
        owner.detach()
        candidate.detach()
        await runtime.shutdown()
        runtime.dispose()
