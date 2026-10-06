import asyncio
import threading
import time
from dataclasses import replace
from datetime import UTC, datetime

import pytest

from aws_tui.domain.query import QueryContext
from aws_tui.infra.athena_draft_store import DraftPermit, DraftStoreResult
from tests.athena_drafts_helpers import CTX, record, runtime_at, store_at
from tests.helpers import wait_until


async def test_latest_edit_is_saved_under_its_captured_context(tmp_path):
    runtime, store = runtime_at(tmp_path)
    session = runtime.open_session()
    context = QueryContext(*CTX)
    session.edited("SELECT 1", context)
    assert session.state == "pending"
    session.edited("SELECT 2", context)
    assert session.state == "pending"
    await wait_until(lambda: session.state == "saved", what="draft acknowledged")
    assert not session.has_unsaved_text("SELECT 2", context)
    report = await runtime.shutdown()
    assert report.unpersisted == 0
    assert store.list().records[0].sql == "SELECT 2"


async def test_shutdown_deadline_retains_and_observes_stalled_store(tmp_path, monkeypatch):
    runtime, store = runtime_at(tmp_path)
    started, release = threading.Event(), threading.Event()
    original = store.save

    def stalled(record, *, permit):
        started.set()
        release.wait()
        return original(record, permit=permit)

    monkeypatch.setattr(store, "save", stalled)
    session = runtime.open_session()
    session.edited("SELECT 'FLUSH_SECRET'", QueryContext(*CTX))
    begin = time.monotonic()
    try:
        report = await runtime.shutdown()
        assert started.is_set()
        assert report.timed_out is True
        assert report.unpersisted == 1
        assert time.monotonic() - begin < 2.5
        assert runtime._worker.pending_count == 1
        assert session.state != "saved"
        assert await runtime.shutdown() is report
        assert await session.flush(deadline=asyncio.get_running_loop().time() + 10) is report
    finally:
        release.set()
    await wait_until(lambda: runtime._worker.pending_count == 0, what="owned draft worker drained")
    await asyncio.sleep(0)
    assert session.state != "saved"
    assert not runtime._coordinator.futures
    assert "FLUSH_SECRET" not in repr(report)
    assert not [t for t in asyncio.all_tasks() if t is not asyncio.current_task() and not t.done()]


async def test_cancellation_retains_worker_and_terminal_report(tmp_path, monkeypatch):
    runtime, store = runtime_at(tmp_path)
    started, release = threading.Event(), threading.Event()

    def entered(record, *, permit):
        started.set()
        release.wait()
        return DraftStoreResult()

    monkeypatch.setattr(store, "save", entered)
    session = runtime.open_session()
    notifications = []
    session.on_property_changed.subscribe(notifications.append)
    session.edited("SELECT 'CANCEL_SECRET'", QueryContext(*CTX))
    task = asyncio.create_task(runtime.shutdown())
    await wait_until(started.is_set, what="store entered")
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert runtime._worker.pending_count == 1
    report = await runtime.shutdown()
    assert report.unpersisted == 1
    assert report.timed_out
    count = len(notifications)
    release.set()
    await wait_until(lambda: runtime._worker.pending_count == 0, what="cancelled flush drained")
    await asyncio.sleep(0)
    assert len(notifications) == count
    assert session.state != "saved"
    assert not runtime._coordinator.futures
    assert not [t for t in asyncio.all_tasks() if t is not asyncio.current_task() and not t.done()]


async def test_one_deadline_for_multiple_contexts_and_late_success(tmp_path, monkeypatch):
    runtime, store = runtime_at(tmp_path)
    started, release = threading.Event(), threading.Event()

    def entered(record, *, permit):
        started.set()
        release.wait()
        return DraftStoreResult()

    monkeypatch.setattr(store, "save", entered)
    sessions = [runtime.open_session() for _ in range(3)]
    for index, session in enumerate(sessions):
        session.edited(f"SELECT {index}", QueryContext(*(*CTX[:4], f"db{index}")))
    start = time.monotonic()
    try:
        report = await runtime.shutdown()
        assert started.is_set()
        assert report.unpersisted == 3
        assert report.timed_out
        assert time.monotonic() - start < 2.5
        runtime.dispose()
        runtime.dispose()
    finally:
        release.set()
    await wait_until(lambda: runtime._worker.pending_count == 0, what="late successful writes")
    await asyncio.sleep(0)
    assert all(session.state != "saved" for session in sessions)


async def test_older_save_newer_edit_never_acknowledges_old_text(tmp_path, monkeypatch):
    runtime, store = runtime_at(tmp_path)
    started, release = threading.Event(), threading.Event()
    original = store.save

    def stalled(record, *, permit):
        if record.sql == "SELECT 1":
            started.set()
            release.wait()
        return original(record, permit=permit)

    monkeypatch.setattr(store, "save", stalled)
    session = runtime.open_session()
    session.edited("SELECT 1", QueryContext(*CTX))
    await wait_until(started.is_set, what="first save entered")
    session.edited("SELECT 2", QueryContext(*CTX))
    release.set()
    await asyncio.sleep(0.05)
    assert session.state == "pending"
    assert (await runtime.shutdown()).unpersisted == 0
    assert store.list().records[0].sql == "SELECT 2"


async def test_context_change_cannot_rebase_captured_sql(tmp_path):
    runtime, store = runtime_at(tmp_path)
    session = runtime.open_session()
    session.edited("SELECT 1", QueryContext(*CTX))
    other = QueryContext(*(*CTX[:4], "different"))
    session.context_changed(other)
    assert session.state == "context_required"
    await wait_until(
        lambda: runtime._worker.pending_count == 0 and bool(store.list().records),
        what="captured save",
    )
    assert store.list().records[0].context == CTX
    session.edited("SELECT 2", other)
    assert session.state == "context_required"
    await runtime.shutdown()
    assert store.list().records[0].sql == "SELECT 1"


async def test_old_page_flush_cannot_overwrite_new_page_same_id(tmp_path):
    runtime, store = runtime_at(tmp_path)
    old, new = runtime.open_session(), runtime.open_session()
    old.edited("SELECT 1", QueryContext(*CTX))
    new.edited("SELECT 2", QueryContext(*CTX))
    assert (await old.flush(deadline=asyncio.get_running_loop().time() + 1)).unpersisted == 0
    old.detach()
    assert (await runtime.shutdown()).unpersisted == 0
    assert store.list().records[0].sql == "SELECT 2"


async def test_error_preserves_text_and_final_flush_retries(tmp_path, monkeypatch, caplog):
    runtime, store = runtime_at(tmp_path)
    original = store.save

    def failure(record, *, permit):
        raise OSError("FAILURE_SQL_SECRET")

    monkeypatch.setattr(store, "save", failure)
    session = runtime.open_session()
    context = QueryContext(*CTX)
    session.edited("SELECT 'FAILURE_SQL_SECRET'", context)
    await wait_until(lambda: session.state == "error", what="save failure")
    assert session.has_unsaved_text("SELECT 'FAILURE_SQL_SECRET'", context)
    assert (
        "FAILURE_SQL_SECRET" not in repr(session) + repr(runtime) + session.error_text + caplog.text
    )
    monkeypatch.setattr(store, "save", original)
    assert (await runtime.shutdown()).unpersisted == 0


async def test_incomplete_context_writes_nothing_and_counts_unconfirmed(tmp_path):
    runtime, store = runtime_at(tmp_path)
    session = runtime.open_session()
    session.edited("SELECT 1", QueryContext(*(*CTX[:4], "")))
    assert session.state == "context_required"
    assert (await runtime.shutdown()).unpersisted == 1
    assert not store.list().records


def test_disabled_construction_edit_dispose_never_accesses_store(tmp_path, monkeypatch):
    runtime, store = runtime_at(tmp_path, enabled=False)

    def forbidden(*args, **kwargs):
        raise AssertionError("Disabled store access")

    for name in ("list", "save", "clear", "delete"):
        monkeypatch.setattr(store, name, forbidden)
    session = runtime.open_session()
    session.edited("SELECT 1", QueryContext(*CTX))
    session.detach()
    runtime.dispose()
    assert runtime._coordinator is None
    assert runtime._worker._thread is None
    assert not (tmp_path / "athena-drafts").exists()


async def test_disabled_async_operations_do_not_read_or_create(tmp_path, monkeypatch):
    runtime, store = runtime_at(tmp_path, enabled=False)
    monkeypatch.setattr(store, "list", lambda: pytest.fail("disabled read"))
    await runtime.refresh()
    assert not await runtime.delete(record().id)
    assert not await runtime.clear()
    assert (await runtime.shutdown()).unpersisted == 0
    assert runtime._worker._thread is None


async def test_staged_and_activation_are_inert_but_recovery_is_exact_baseline(tmp_path):
    runtime, store = runtime_at(tmp_path)
    session = runtime.open_session(active=False)
    context = QueryContext(*CTX)
    session.edited("SELECT 1", context)
    assert session.state == "off"
    session.activate("SELECT 2", context)
    assert session.has_unsaved_text("SELECT 2", context)
    session.recovered(record(sql="SELECT 3"))
    assert session.state == "saved"
    assert not session.has_unsaved_text("SELECT 3", context)
    await runtime.shutdown()
    assert not store.list().records


@pytest.mark.parametrize("mutation", ["delete", "clear", "disable"])
async def test_pending_save_mutation_tombstones_unchanged_editor(tmp_path, mutation):
    runtime, store = runtime_at(tmp_path)
    session = runtime.open_session()
    session.edited("SELECT 1", QueryContext(*CTX))
    if mutation == "delete":
        assert await runtime.delete(record().id)
    elif mutation == "clear":
        assert await runtime.clear()
    else:
        assert await runtime.set_enabled(False)
    assert (await runtime.shutdown()).unpersisted == 0
    assert not store.list().records
    assert not runtime.items


@pytest.mark.parametrize("mutation", ["clear", "disable"])
async def test_inflight_save_mutation_is_fifo_and_cannot_resurrect(tmp_path, monkeypatch, mutation):
    runtime, store = runtime_at(tmp_path)
    started, release = threading.Event(), threading.Event()
    original = store.save

    def stalled(record, *, permit):
        started.set()
        release.wait()
        return original(record, permit=permit)

    monkeypatch.setattr(store, "save", stalled)
    session = runtime.open_session()
    session.edited("SELECT 1", QueryContext(*CTX))
    await wait_until(started.is_set, what="save in flight")
    operation = runtime.clear() if mutation == "clear" else runtime.set_enabled(False)
    task = asyncio.create_task(operation)
    await wait_until(lambda: session._deleted_revision is not None, what="mutation fence")
    release.set()
    assert await task
    assert (await runtime.shutdown()).unpersisted == 0
    assert not store.list().records


async def test_delete_then_new_edit_saves_again(tmp_path):
    runtime, store = runtime_at(tmp_path)
    session = runtime.open_session()
    session.edited("SELECT 1", QueryContext(*CTX))
    assert await runtime.delete(record().id)
    session.edited("SELECT 2", QueryContext(*CTX))
    assert (await runtime.shutdown()).unpersisted == 0
    assert store.list().records[0].sql == "SELECT 2"


async def test_enable_disable_overlap_and_failed_cleanup_retry(tmp_path, monkeypatch):
    runtime, store = runtime_at(tmp_path, enabled=False)
    session = runtime.open_session()
    session.edited("SELECT 1", QueryContext(*CTX))
    original = store.set_enabled
    entered, release = threading.Event(), threading.Event()

    def held(enabled):
        if enabled:
            entered.set()
            release.wait()
        return original(enabled)

    monkeypatch.setattr(store, "set_enabled", held)
    enable = asyncio.create_task(runtime.set_enabled(True))
    await wait_until(entered.is_set, what="enable entered")
    disable = asyncio.create_task(runtime.set_enabled(False))
    release.set()
    assert await enable
    assert await disable
    assert not runtime.enabled
    assert not store.list().records
    assert await runtime.set_enabled(True)
    monkeypatch.setattr(
        store, "set_enabled", lambda enabled: DraftStoreResult(code="io", enabled=False)
    )
    assert not await runtime.set_enabled(False)
    assert runtime.cleanup_required
    assert runtime.error_text
    assert not runtime.enabled
    monkeypatch.setattr(store, "set_enabled", original)
    assert await runtime.set_enabled(False)
    assert not runtime.cleanup_required
    await runtime.shutdown()


async def test_refresh_is_explicit_and_failed_mutation_keeps_listing(tmp_path, monkeypatch):
    runtime, store = runtime_at(tmp_path)
    seeded = record()
    from aws_tui.infra.athena_draft_store import DraftPermit

    assert store.save(seeded, permit=DraftPermit()).code is None
    assert runtime.items == ()
    await runtime.refresh()
    assert runtime.items == (seeded,)
    monkeypatch.setattr(store, "delete", lambda identity: DraftStoreResult(code="io"))
    assert not await runtime.delete(seeded.id)
    assert runtime.items == (seeded,)
    assert runtime.error_text
    await runtime.shutdown()


async def test_detach_submits_final_edit_without_blocking_shared_runtime(tmp_path):
    runtime, store = runtime_at(tmp_path)
    first = runtime.open_session()
    first.edited("SELECT 1", QueryContext(*CTX))
    first.detach()
    second = runtime.open_session()
    second.edited("SELECT 2", QueryContext(*(*CTX[:4], "db2")))
    assert (await runtime.shutdown()).unpersisted == 0
    assert {r.sql for r in store.list().records} == {"SELECT 1", "SELECT 2"}


async def test_session_flush_is_nonterminal_and_waiter_cancel_does_not_cancel_worker(
    tmp_path, monkeypatch
):
    runtime, store = runtime_at(tmp_path)
    entered, release = threading.Event(), threading.Event()
    original = store.save

    def held(record, *, permit):
        entered.set()
        release.wait()
        return original(record, permit=permit)

    monkeypatch.setattr(store, "save", held)
    session = runtime.open_session()
    session.edited("SELECT 1", QueryContext(*CTX))
    task = asyncio.create_task(session.flush(deadline=asyncio.get_running_loop().time() + 5))
    await wait_until(entered.is_set, what="navigation save")
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert runtime._worker.pending_count == 1
    release.set()
    await wait_until(lambda: session.state == "saved", what="navigation acknowledgement")
    session.edited("SELECT 2", QueryContext(*CTX))
    assert (await runtime.shutdown()).unpersisted == 0
    assert store.list().records[0].sql == "SELECT 2"


@pytest.mark.parametrize("mutation", ["enable", "disable", "delete", "clear"])
async def test_abandoned_mutation_reconciles_confirmed_disk_state(tmp_path, monkeypatch, mutation):
    runtime, store = runtime_at(tmp_path, enabled=mutation != "enable")
    session = runtime.open_session()
    session.edited("SELECT 1", QueryContext(*CTX))
    if mutation != "enable":
        await session.flush(deadline=asyncio.get_running_loop().time() + 2)
        await runtime.refresh()
        assert runtime.items
    entered, release = threading.Event(), threading.Event()
    name = "set_enabled" if mutation in {"enable", "disable"} else mutation
    original = getattr(store, name)

    def held(*args):
        entered.set()
        release.wait()
        return original(*args)

    monkeypatch.setattr(store, name, held)
    if mutation in {"enable", "disable"}:
        operation = runtime.set_enabled(mutation == "enable")
    elif mutation == "delete":
        operation = runtime.delete(record().id)
    else:
        operation = runtime.clear()
    task = asyncio.create_task(operation)
    await wait_until(entered.is_set, what="mutation accepted")
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert runtime._worker.pending_count == 1
    release.set()
    await wait_until(lambda: runtime._worker.pending_count == 0, what="abandoned mutation drained")
    await asyncio.sleep(0)
    if mutation == "enable":
        assert runtime.enabled
        assert session.state == "pending"
    elif mutation == "disable":
        assert not runtime.enabled
        assert not runtime.items
        assert not runtime.cleanup_required
    else:
        assert not runtime.items
    await runtime.shutdown()


async def test_recovered_baseline_revokes_pending_editor_capture(tmp_path):
    runtime, store = runtime_at(tmp_path)
    session = runtime.open_session()
    session.edited("SELECT old", QueryContext(*CTX))
    session.recovered(record(sql="SELECT recovered"))
    assert (await runtime.shutdown()).unpersisted == 0
    assert not store.list().records


async def test_empty_edit_deletes_and_new_context_can_bind(tmp_path):
    runtime, store = runtime_at(tmp_path)
    session = runtime.open_session()
    session.edited("SELECT 1", QueryContext(*CTX))
    await session.flush(deadline=asyncio.get_running_loop().time() + 2)
    session.edited("  ", QueryContext(*CTX))
    await wait_until(lambda: session.state == "empty", what="empty edit deleted")
    assert not store.list().records
    assert session.bound_context is None
    other = QueryContext(*(*CTX[:4], "other"))
    session.edited("SELECT 2", other)
    assert (await runtime.shutdown()).unpersisted == 0
    assert store.list().records[0].context == other.cache_key


async def test_clear_after_selecting_another_context_deletes_only_bound_origin(tmp_path):
    runtime, store = runtime_at(tmp_path)
    origin = QueryContext(*CTX)
    selected = QueryContext(*(*CTX[:4], "other"))
    assert store.save(record(origin.cache_key, "SELECT origin"), permit=DraftPermit()).code is None
    assert (
        store.save(record(selected.cache_key, "SELECT selected"), permit=DraftPermit()).code is None
    )
    session = runtime.open_session()
    session.recovered(record(origin.cache_key, "SELECT origin"))

    session.context_changed(selected)
    assert session.state == "context_required"
    session.edited("  ", selected)

    await wait_until(lambda: session.state == "empty", what="origin clear acknowledged")
    records = {item.context: item.sql for item in store.list().records}
    assert records == {selected.cache_key: "SELECT selected"}
    assert session.bound_context is None
    await runtime.shutdown()


async def test_clear_with_incomplete_selection_still_deletes_bound_origin(tmp_path):
    runtime, store = runtime_at(tmp_path)
    origin = QueryContext(*CTX)
    selected_record = QueryContext(*(*CTX[:4], "other"))
    incomplete = QueryContext(CTX[0], "", CTX[2], CTX[3], CTX[4])
    assert store.save(record(origin.cache_key, "SELECT origin"), permit=DraftPermit()).code is None
    assert (
        store.save(record(selected_record.cache_key, "SELECT selected"), permit=DraftPermit()).code
        is None
    )
    session = runtime.open_session()
    session.recovered(record(origin.cache_key, "SELECT origin"))

    session.context_changed(incomplete)
    session.edited("", incomplete)

    await wait_until(lambda: session.state == "empty", what="origin clear acknowledged")
    records = {item.context: item.sql for item in store.list().records}
    assert records == {selected_record.cache_key: "SELECT selected"}
    await runtime.shutdown()


async def test_clear_supersedes_pending_origin_save_and_preserves_newer_selected_writer(
    tmp_path, monkeypatch
):
    runtime, store = runtime_at(tmp_path)
    origin = QueryContext(*CTX)
    selected = QueryContext(*(*CTX[:4], "other"))
    assert store.save(record(origin.cache_key, "SELECT origin"), permit=DraftPermit()).code is None
    selected_existing = replace(
        record(selected.cache_key, "SELECT selected old"),
        created_at=datetime(2000, 1, 1, tzinfo=UTC),
        updated_at=datetime(2000, 1, 1, tzinfo=UTC),
    )
    assert store.save(selected_existing, permit=DraftPermit()).code is None
    outcomes = []
    original_save = store.save

    def observe_save(draft, *, permit):
        result = original_save(draft, permit=permit)
        outcomes.append((draft.context, draft.sql, result.code, permit.cancelled))
        return result

    monkeypatch.setattr(store, "save", observe_save)
    origin_session = runtime.open_session()
    selected_session = runtime.open_session()
    origin_session.edited("SELECT stale pending origin", origin)
    selected_session.edited("SELECT selected newer", selected)
    selected_capture = selected_session._capture
    assert selected_capture is not None
    origin_session.context_changed(selected)

    origin_session.edited("", selected)
    assert runtime._coordinator.is_current(
        selected_capture.record.id,
        selected_capture.captured_write_revision,
        selected_capture.permit,
    )
    assert not selected_capture.permit.cancelled
    await selected_session.flush(deadline=asyncio.get_running_loop().time() + 2)
    await origin_session.flush(deadline=asyncio.get_running_loop().time() + 2)
    assert outcomes == [(selected.cache_key, "SELECT selected newer", None, False)]
    assert selected_session.state == "saved"
    await runtime.shutdown()
    records = {item.context: item.sql for item in store.list().records}
    assert records == {selected.cache_key: "SELECT selected newer"}


async def test_clear_without_bound_origin_preserves_selected_context_record(tmp_path):
    runtime, store = runtime_at(tmp_path)
    selected = QueryContext(*CTX)
    assert (
        store.save(record(selected.cache_key, "SELECT unrelated"), permit=DraftPermit()).code
        is None
    )
    session = runtime.open_session()
    session.activate("", selected)

    session.edited("  ", selected)

    assert session.state == "empty"
    assert store.list().records[0].sql == "SELECT unrelated"
    await runtime.shutdown()


@pytest.mark.parametrize("selection", ["same", "different", "incomplete"])
async def test_successive_blank_edits_delete_origin_and_preserve_selected_writer(
    tmp_path, selection
):
    runtime, store = runtime_at(tmp_path)
    origin = QueryContext(*CTX)
    other = QueryContext(*(*CTX[:4], "other"))
    selected = {
        "same": origin,
        "different": other,
        "incomplete": QueryContext(CTX[0], "", CTX[2], CTX[3], CTX[4]),
    }[selection]
    past = datetime(2000, 1, 1, tzinfo=UTC)
    origin_record = replace(record(CTX, "SELECT origin"), created_at=past, updated_at=past)
    other_record = replace(
        record(other.cache_key, "SELECT other old"), created_at=past, updated_at=past
    )
    assert store.save(origin_record, permit=DraftPermit()).code is None
    assert store.save(other_record, permit=DraftPermit()).code is None
    session, writer = runtime.open_session(), runtime.open_session()
    session.recovered(origin_record)
    writer.edited("SELECT other newer", other)
    writer_capture = writer._capture
    assert writer_capture is not None
    try:
        session.edited("", selected)
        first_clear = session._capture
        assert first_clear is not None
        session.edited(" \n\t", selected)
        latest_revision = session.editor_revision
        await writer.flush(deadline=asyncio.get_running_loop().time() + 2)
        await session.flush(deadline=asyncio.get_running_loop().time() + 2)

        assert {item.context: item.sql for item in store.list().records} == {
            other.cache_key: "SELECT other newer"
        }
        assert session.state == "empty"
        assert session.editor_revision == latest_revision
        assert session._saved_sql == " \n\t"
        assert session._saved_context == origin
        assert session.bound_context is None
        assert writer.state == "saved"
        assert not writer_capture.permit.cancelled
    finally:
        await runtime.shutdown()


async def test_superseded_origin_deletion_is_not_revived_by_another_blank_edit(tmp_path):
    runtime, store = runtime_at(tmp_path)
    origin = QueryContext(*CTX)
    other = QueryContext(*(*CTX[:4], "other"))
    past = datetime(2000, 1, 1, tzinfo=UTC)
    origin_record = replace(record(CTX, "SELECT origin"), created_at=past, updated_at=past)
    assert store.save(origin_record, permit=DraftPermit()).code is None
    old, writer = runtime.open_session(), runtime.open_session()
    old.recovered(origin_record)
    old.edited("", other)
    deletion = old._capture
    assert deletion is not None
    writer.edited("SELECT origin newer", origin)
    assert deletion.permit.cancelled
    old.edited(" \t", other)

    assert (await runtime.shutdown()).unpersisted == 0
    assert {item.context: item.sql for item in store.list().records} == {
        origin.cache_key: "SELECT origin newer"
    }
    assert old.state == "empty"
    assert writer.state == "saved"


async def test_completed_origin_deletion_releases_ownership_before_later_blank_edit(tmp_path):
    runtime, store = runtime_at(tmp_path)
    origin = QueryContext(*CTX)
    other = QueryContext(*(*CTX[:4], "other"))
    session = runtime.open_session()
    session.edited("SELECT origin", origin)
    await session.flush(deadline=asyncio.get_running_loop().time() + 2)
    session.edited("", other)
    await session.flush(deadline=asyncio.get_running_loop().time() + 2)
    assert session.state == "empty"
    assert not store.list().records
    past = datetime(2000, 1, 1, tzinfo=UTC)
    external_record = replace(record(CTX, "SELECT origin newer"), created_at=past, updated_at=past)
    assert store.save(external_record, permit=DraftPermit()).code is None
    session.edited(" \t", other)

    assert (await runtime.shutdown()).unpersisted == 0
    assert store.list().records[0].sql == "SELECT origin newer"
    assert session.state == "empty"


@pytest.mark.parametrize("fence", ["delete", "clear", "disable", "recover", "dispose"])
async def test_fenced_origin_deletion_is_not_revived_by_successive_blank_edits(tmp_path, fence):
    runtime, store = runtime_at(tmp_path)
    origin = QueryContext(*CTX)
    other = QueryContext(*(*CTX[:4], "other"))
    past = datetime(2000, 1, 1, tzinfo=UTC)
    origin_record = replace(record(CTX, "SELECT origin"), created_at=past, updated_at=past)
    assert store.save(origin_record, permit=DraftPermit()).code is None
    session = runtime.open_session()
    session.recovered(origin_record)
    session.edited("", other)
    deletion = session._capture
    assert deletion is not None
    if fence == "delete":
        assert await runtime.delete(origin_record.id)
    elif fence == "clear":
        assert await runtime.clear()
    elif fence == "disable":
        assert await runtime.set_enabled(False)
        assert await runtime.set_enabled(True)
    elif fence == "recover":
        session.recovered(record(other.cache_key, "SELECT other"))
    else:
        runtime.dispose()
    assert deletion.permit.cancelled
    external_record = replace(origin_record, sql="SELECT external newer")
    assert store.save(external_record, permit=DraftPermit()).code is None

    session.edited(" \t", other)
    await runtime.shutdown()

    assert {item.context: item.sql for item in store.list().records} == {
        origin.cache_key: "SELECT external newer"
    }


async def test_readonly_demo_never_invokes_store(tmp_path, monkeypatch):
    from vmx import NULL_DISPATCHER, MessageHub

    from aws_tui.vm.athena.drafts_vm import AthenaDraftsVM

    store = store_at(tmp_path)
    runtime = AthenaDraftsVM(
        store=store,
        enabled=True,
        read_only=True,
        directory=tmp_path / "athena-drafts",
        hub=MessageHub(),
        dispatcher=NULL_DISPATCHER,
    )
    for name in ("list", "save", "delete", "clear", "set_enabled"):
        monkeypatch.setattr(store, name, lambda *args: pytest.fail("demo I/O"))
    session = runtime.open_session()
    session.edited("SELECT 1", QueryContext(*CTX))
    assert session.state == "off"
    assert not await runtime.set_enabled(True)
    await runtime.refresh()
    assert (await runtime.shutdown()).unpersisted == 0
    assert runtime._coordinator is None


async def test_superseded_same_context_counts_only_current_writer(tmp_path, monkeypatch):
    runtime, store = runtime_at(tmp_path)
    entered, release = threading.Event(), threading.Event()

    def held(record, *, permit):
        entered.set()
        release.wait()
        return DraftStoreResult(code="io")

    monkeypatch.setattr(store, "save", held)
    first, second = runtime.open_session(), runtime.open_session()
    first.edited("SELECT 1", QueryContext(*CTX))
    second.edited("SELECT 2", QueryContext(*CTX))
    try:
        assert (await runtime.shutdown()).unpersisted == 1
    finally:
        release.set()
    await wait_until(lambda: runtime._worker.pending_count == 0, what="superseded writes drained")


async def test_terminal_shutdown_detaches_observers_and_rejects_editor_intake(tmp_path):
    runtime, store = runtime_at(tmp_path)
    session = runtime.open_session()
    names = []
    session.on_property_changed.subscribe(names.append)
    session.edited("SELECT 1", QueryContext(*CTX))
    await runtime.shutdown()
    before = len(names)
    session.edited("SELECT 2", QueryContext(*CTX))
    assert len(names) == before
    assert store.list().records[0].sql == "SELECT 1"


async def test_canceled_cleanup_failure_reconciles_retry_state(tmp_path, monkeypatch):
    runtime, store = runtime_at(tmp_path)
    entered, release = threading.Event(), threading.Event()

    def failed(enabled):
        entered.set()
        release.wait()
        return DraftStoreResult(code="io", enabled=True)

    monkeypatch.setattr(store, "set_enabled", failed)
    task = asyncio.create_task(runtime.set_enabled(False))
    await wait_until(entered.is_set, what="failed cleanup entered")
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    release.set()
    await wait_until(lambda: runtime.cleanup_required, what="failed cleanup reconciled")
    assert runtime.enabled
    assert runtime._saving_suspended
    assert runtime.error_text
    await runtime.shutdown()


async def test_disabled_edits_after_preference_change_are_not_flush_obligations(tmp_path):
    runtime, store = runtime_at(tmp_path)
    session = runtime.open_session()
    assert await runtime.set_enabled(False)
    session.edited("SELECT off", QueryContext(*CTX))
    assert session.state == "off"
    assert (await runtime.shutdown()).unpersisted == 0
    assert not store.list().records


def test_completion_after_disposal_and_loop_close_only_releases_ownership(tmp_path, monkeypatch):
    runtime, store = runtime_at(tmp_path)
    entered, release = threading.Event(), threading.Event()

    def held(record, *, permit):
        entered.set()
        release.wait()
        return DraftStoreResult()

    monkeypatch.setattr(store, "save", held)
    loop = asyncio.new_event_loop()

    async def launch():
        session = runtime.open_session()
        session.edited("SELECT 'CLOSED_LOOP_SECRET'", QueryContext(*CTX))
        runtime._prepare_final(session)
        assert entered.wait(1)
        runtime.dispose()

    try:
        loop.run_until_complete(launch())
        loop.close()
        release.set()
        runtime._worker._thread.join(1)
        assert runtime._worker.pending_count == 0
        assert not runtime._coordinator.futures
        assert not runtime._coordinator.waiters
    finally:
        release.set()
        if not loop.is_closed():
            loop.close()


@pytest.mark.parametrize("incomplete", [False, True])
async def test_invalid_new_edit_revokes_only_its_own_pending_capture(tmp_path, incomplete):
    runtime, store = runtime_at(tmp_path)
    session = runtime.open_session()
    session.edited("SELECT old", QueryContext(*CTX))
    other = QueryContext(*(*CTX[:4], "" if incomplete else "other"))
    session.edited("SELECT newer", other)
    assert session.state == "context_required"
    assert (await runtime.shutdown()).unpersisted == 1
    assert not store.list().records


async def test_invalid_old_session_edit_cannot_revoke_new_session_writer(tmp_path):
    runtime, store = runtime_at(tmp_path)
    old, new = runtime.open_session(), runtime.open_session()
    old.edited("SELECT old", QueryContext(*CTX))
    new.edited("SELECT newer", QueryContext(*CTX))
    old.edited("SELECT invalid", QueryContext(*(*CTX[:4], "other")))
    await runtime.shutdown()
    assert store.list().records[0].sql == "SELECT newer"


async def test_original_context_save_acknowledgment_preserves_selection_guard(tmp_path):
    runtime, store = runtime_at(tmp_path)
    session = runtime.open_session()
    original = QueryContext(*CTX)
    session.edited("SELECT origin", original)
    session.context_changed(QueryContext(*(*CTX[:4], "other")))
    await wait_until(
        lambda: not session.has_unsaved_text("SELECT origin", original),
        what="original context acknowledged",
    )
    assert session.state == "context_required"
    assert (
        session.error_text
        == "Editor belongs to another context. Return to that context or clear the editor."
    )
    assert session.bound_context == original
    assert store.list().records[0].context == original.cache_key
    session.context_changed(original)
    assert session.state == "saved"
    assert session.error_text is None
    assert (await runtime.shutdown()).unpersisted == 0


async def test_abandoned_enable_completion_cannot_resume_saves_during_newer_disable(
    tmp_path, monkeypatch
):
    runtime, store = runtime_at(tmp_path, enabled=False)
    session = runtime.open_session()
    session.edited("SELECT 1", QueryContext(*CTX))
    enable_entered, enable_release = threading.Event(), threading.Event()
    disable_entered, disable_release = threading.Event(), threading.Event()
    original = store.set_enabled

    def held(enabled):
        entered, release = (
            (enable_entered, enable_release) if enabled else (disable_entered, disable_release)
        )
        entered.set()
        release.wait()
        return original(enabled)

    monkeypatch.setattr(store, "set_enabled", held)
    enabling = asyncio.create_task(runtime.set_enabled(True))
    await wait_until(enable_entered.is_set, what="old enable accepted")
    enabling.cancel()
    with pytest.raises(asyncio.CancelledError):
        await enabling
    disabling = asyncio.create_task(runtime.set_enabled(False))
    await wait_until(lambda: runtime._saving_suspended, what="new disable suspended intake")
    try:
        enable_release.set()
        await wait_until(disable_entered.is_set, what="disable entered")
        await asyncio.sleep(0)
        assert runtime.enabled  # Last confirmed disk preference, cleanup still pending.
        assert runtime._saving_suspended
        assert not runtime._coordinator.current
    finally:
        enable_release.set()
        disable_release.set()
        await disabling
    assert not runtime.enabled
    assert (await runtime.shutdown()).unpersisted == 0
    assert not store.list().records


async def test_failed_original_save_preserves_context_guard_and_final_retry(tmp_path, monkeypatch):
    runtime, store = runtime_at(tmp_path)
    session = runtime.open_session()
    original = store.save
    failures = []

    def failed(record, *, permit):
        failures.append(True)
        return DraftStoreResult(code="io")

    monkeypatch.setattr(store, "save", failed)
    session.edited("SELECT origin", QueryContext(*CTX))
    session.context_changed(QueryContext(*(*CTX[:4], "other")))
    await wait_until(
        lambda: failures and not runtime._coordinator.futures, what="failed original save"
    )
    await asyncio.sleep(0)
    assert session.state == "context_required"
    monkeypatch.setattr(store, "save", original)
    await runtime.shutdown()
    assert store.list().records[0].sql == "SELECT origin"
    assert store.list().records[0].context == CTX


async def test_dispose_cancels_observation_waiter_without_canceling_accepted_work(
    tmp_path, monkeypatch
):
    runtime, store = runtime_at(tmp_path)
    entered, release = threading.Event(), threading.Event()

    def held():
        entered.set()
        release.wait()
        return DraftStoreResult()

    monkeypatch.setattr(store, "clear", held)
    task = asyncio.create_task(runtime.clear())
    await wait_until(entered.is_set, what="clear accepted before dispose")
    try:
        runtime.dispose()
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        assert task.done()
        assert task.cancelled()
        assert runtime._worker.pending_count == 1
    finally:
        task.cancel()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
    await wait_until(lambda: runtime._worker.pending_count == 0, what="disposed mutation drained")


async def test_preference_waiting_mutation_lock_cannot_reopen_terminal_intake(
    tmp_path, monkeypatch
):
    runtime, store = runtime_at(tmp_path)
    entered, release = threading.Event(), threading.Event()

    def held():
        entered.set()
        release.wait()
        return DraftStoreResult()

    monkeypatch.setattr(store, "clear", held)
    submissions = []
    submit = runtime._worker.submit

    def counted(operation):
        submissions.append(True)
        return submit(operation)

    monkeypatch.setattr(runtime._worker, "submit", counted)
    notifications = []
    runtime.on_property_changed.subscribe(notifications.append)
    clearing = asyncio.create_task(runtime.clear())
    await wait_until(entered.is_set, what="mutation holds coordination lock")
    enabling = asyncio.create_task(runtime.set_enabled(True))
    await asyncio.sleep(0)
    await runtime.shutdown()
    count = len(notifications)
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await clearing
    assert not await enabling
    await wait_until(lambda: runtime._worker.pending_count == 0, what="terminal mutation drains")
    await asyncio.sleep(0)
    assert len(notifications) == count
    assert len(submissions) == 1  # No operation is admitted after terminal shutdown.


async def test_disabled_terminal_shutdown_detaches_observers(tmp_path):
    runtime, _ = runtime_at(tmp_path, enabled=False)
    session = runtime.open_session()
    names = []
    session.on_property_changed.subscribe(names.append)
    session.edited("SELECT 1", QueryContext(*CTX))
    await runtime.shutdown()
    count = len(names)
    session.edited("SELECT 2", QueryContext(*CTX))
    assert len(names) == count
    assert session._detached


@pytest.mark.parametrize("outcome", ["failure", "success", "timeout", "late_success"])
async def test_retired_page_keeps_current_write_obligation(tmp_path, monkeypatch, outcome):
    from tests.unit.vm.athena.test_page_vm import PageClient, make_page_vm

    runtime, store = runtime_at(tmp_path)
    entered, release = threading.Event(), threading.Event()
    save = store.save

    def held(record, *, permit):
        result = save(record, permit=permit) if outcome == "late_success" else None
        entered.set()
        assert release.wait(10)
        if outcome == "failure":
            return DraftStoreResult(code="io")
        return result if result is not None else save(record, permit=permit)

    monkeypatch.setattr(store, "save", held)
    page = make_page_vm(PageClient(), drafts=runtime)
    await page.setup()
    notifications = []
    page._draft_session.on_property_changed.subscribe(notifications.append)
    page.query.set_sql("SELECT 'RETIRED_PRIVATE'")
    try:
        await page.shutdown()
        assert entered.is_set()
        page.dispose()
        assert not runtime._sessions
        notification_count = len(notifications)
        if outcome in {"failure", "success"}:
            release.set()
        begin = time.monotonic()
        report = await runtime.shutdown()
        assert report.unpersisted == (0 if outcome == "success" else 1)
        assert report.timed_out == (outcome in {"timeout", "late_success"})
        assert time.monotonic() - begin < 2.5
        if report.timed_out:
            assert runtime._worker.pending_count == 1
        release.set()
        await wait_until(lambda: runtime._worker.pending_count == 0, what="retired writer drained")
        await asyncio.sleep(0)
        assert await runtime.shutdown() is report
        assert len(notifications) == notification_count
        assert "RETIRED_PRIVATE" not in repr(report) + repr(runtime._coordinator)
        assert bool(store.list().records) == (outcome in {"success", "late_success"})
    finally:
        release.set()
        await page.shutdown()
        page.dispose()
        await runtime.shutdown()
        runtime.dispose()


@pytest.mark.parametrize(
    "resolution", ["supersede", "supersede_failure", "delete", "clear", "disable"]
)
async def test_retired_page_obligation_is_superseded_or_fenced(tmp_path, monkeypatch, resolution):
    from tests.unit.vm.athena.test_page_vm import PageClient, make_page_vm

    runtime, store = runtime_at(tmp_path)
    save = store.save
    monkeypatch.setattr(store, "save", lambda record, *, permit: DraftStoreResult(code="io"))
    page = make_page_vm(PageClient(), drafts=runtime)
    await page.setup()
    context = page.query.context
    page.query.set_sql("SELECT old")
    await page.shutdown()
    page.dispose()
    await wait_until(lambda: runtime._worker.pending_count == 0, what="retired failed retry")
    if resolution != "supersede_failure":
        monkeypatch.setattr(store, "save", save)
    if resolution in {"supersede", "supersede_failure"}:
        newer = make_page_vm(PageClient(), drafts=runtime)
        await newer.setup()
        newer.query.set_sql("SELECT newer")
        await newer.shutdown()
        newer.dispose()
    elif resolution == "delete":
        assert await runtime.delete(record(context.cache_key).id)
    elif resolution == "clear":
        assert await runtime.clear()
    else:
        assert await runtime.set_enabled(False)
    assert (await runtime.shutdown()).unpersisted == (1 if resolution == "supersede_failure" else 0)
    assert [row.sql for row in store.list().records] == (
        ["SELECT newer"] if resolution == "supersede" else []
    )
    runtime.dispose()


@pytest.mark.parametrize("unknown", [False, True])
@pytest.mark.parametrize("retry", [False, True])
async def test_failed_enable_suspends_real_config_until_explicit_success(
    tmp_path, monkeypatch, unknown, retry
):
    from aws_tui.infra import athena_draft_store as module

    runtime, store = runtime_at(tmp_path, enabled=False)
    config = store._config
    setting, load, directory = config.set_athena_sql_drafts, config.load, module._private_directory
    rolled_back = False

    def broken_setting(enabled):
        nonlocal rolled_back
        if not enabled:
            rolled_back = True
            raise OSError("PRIVATE_ROLLBACK_PAYLOAD")
        setting(enabled)

    def broken_load():
        if unknown and rolled_back:
            raise OSError("PRIVATE_READBACK_PAYLOAD")
        return load()

    def broken_directory(*args, **kwargs):
        raise OSError("PRIVATE_DIRECTORY_PAYLOAD")

    monkeypatch.setattr(config, "set_athena_sql_drafts", broken_setting)
    monkeypatch.setattr(config, "load", broken_load)
    monkeypatch.setattr(module, "_private_directory", broken_directory)
    try:
        assert not await runtime.set_enabled(True)
        assert load().athena_sql_drafts
        assert runtime.enabled == (not unknown)
        assert runtime._saving_suspended
        assert runtime.preference_confirmed == (not unknown)
        assert runtime.enable_required
        assert "saving is suspended" in runtime.error_text
        if unknown:
            assert "could not be confirmed" in runtime.error_text
        assert "PRIVATE_" not in runtime.error_text + repr(runtime)
        session = runtime.open_session()
        session.edited("SELECT private", QueryContext(*CTX))
        assert session._capture is None
        assert session.state != "pending"
        monkeypatch.setattr(config, "set_athena_sql_drafts", setting)
        monkeypatch.setattr(config, "load", load)
        monkeypatch.setattr(module, "_private_directory", directory)
        await runtime.refresh()
        assert "saving is suspended" in runtime.error_text
        session.edited("SELECT latest", QueryContext(*CTX))
        assert session._capture is None
        if retry:
            assert await runtime.set_enabled(True)
            assert not runtime._saving_suspended
            assert not runtime.enable_required
            assert runtime.preference_confirmed
        await runtime.shutdown()
        assert [row.sql for row in store.list().records] == (["SELECT latest"] if retry else [])
    finally:
        runtime.dispose()


async def test_failed_reenable_keeps_retired_obligation_without_queued_save(tmp_path, monkeypatch):
    runtime, store = runtime_at(tmp_path)
    session = runtime.open_session()
    session.edited("SELECT pending", QueryContext(*CTX))
    monkeypatch.setattr(
        store, "set_enabled", lambda enabled: DraftStoreResult(code="io", enabled=True)
    )
    assert not await runtime.set_enabled(True)
    session.detach()
    assert (await runtime.shutdown()).unpersisted == 1
    assert not store.list().records
    runtime.dispose()


async def _unschedulable_page(runtime, *, incomplete):
    from tests.unit.vm.athena.test_page_vm import PageClient, make_page_vm

    client = PageClient()
    page = make_page_vm(client, drafts=runtime)
    if not incomplete:
        await page.setup()
        page.query.set_sql("SELECT 1")
        await page._draft_session.flush(deadline=asyncio.get_running_loop().time() + 2)
        await page.select_workgroup("analysts")
    page.query.set_sql("SELECT 'UNSCHEDULED_PRIVATE'")
    assert page.query.draft_state == "context_required"
    return page, client


@pytest.mark.parametrize("incomplete", [False, True])
async def test_unschedulable_page_retirement_keeps_latest_obligation(tmp_path, incomplete):
    import gc
    import weakref

    runtime, store = runtime_at(tmp_path)
    page, client = await _unschedulable_page(runtime, incomplete=incomplete)
    assert runtime._unconfirmed_count() == 1
    session_ref = weakref.ref(page._draft_session)
    await page.shutdown()
    page.dispose()
    del page
    gc.collect()
    assert session_ref() is None
    assert not runtime._sessions
    report = await runtime.shutdown()
    assert report.unpersisted == 1
    assert not report.timed_out
    assert client.start_calls == []
    assert [row.sql for row in store.list().records] == ([] if incomplete else ["SELECT 1"])
    assert "UNSCHEDULED_PRIVATE" not in repr(runtime._coordinator.unconfirmed) + repr(report)
    assert await runtime.shutdown() is report
    runtime.dispose()


@pytest.mark.parametrize("incomplete", [False, True])
@pytest.mark.parametrize("resolution", ["blank", "delete", "clear", "disable"])
async def test_unschedulable_obligation_obeys_editor_and_destructive_fences(
    tmp_path, incomplete, resolution
):
    runtime, store = runtime_at(tmp_path)
    page, _ = await _unschedulable_page(runtime, incomplete=incomplete)
    origin = page._draft_session.bound_context or page.query.context
    if resolution == "blank":
        page.query.set_sql("  ")
    await page.shutdown()
    page.dispose()
    if resolution == "delete":
        assert await runtime.delete(record(origin.cache_key).id)
    elif resolution == "clear":
        assert await runtime.clear()
    elif resolution == "disable":
        assert await runtime.set_enabled(False)
    assert (await runtime.shutdown()).unpersisted == 0
    assert not store.list().records
    runtime.dispose()


@pytest.mark.parametrize("resolution", ["saved", "failed", "recovered", "other_origin"])
async def test_unschedulable_retired_origin_supersession(tmp_path, monkeypatch, resolution):
    from tests.unit.vm.athena.test_draft_recovery import yes
    from tests.unit.vm.athena.test_page_vm import PageClient, make_page_vm

    runtime, store = runtime_at(tmp_path)
    old, _ = await _unschedulable_page(runtime, incomplete=False)
    origin = old._draft_session.bound_context
    await old.shutdown()
    old.dispose()
    client = PageClient()
    newer = make_page_vm(client, drafts=runtime, source_is_current=yes)
    await newer.setup()
    assert newer.query.context == origin
    if resolution == "failed":
        monkeypatch.setattr(store, "save", lambda record, *, permit: DraftStoreResult(code="io"))
    if resolution == "recovered":
        assert await newer.restore_draft(record(origin.cache_key).id, yes)
        assert newer.query.sql == "SELECT 1"
    else:
        if resolution == "other_origin":
            await newer.select_workgroup("analysts")
        newer.query.set_sql("SELECT 3")
    await newer.shutdown()
    newer.dispose()
    assert (await runtime.shutdown()).unpersisted == (
        1 if resolution in {"failed", "other_origin"} else 0
    )
    assert client.start_calls == []
    runtime.dispose()


async def test_superseded_unschedulable_page_cannot_revive_obligation(tmp_path):
    from tests.unit.vm.athena.test_page_vm import PageClient, make_page_vm

    runtime, store = runtime_at(tmp_path)
    old, _ = await _unschedulable_page(runtime, incomplete=False)
    newer = make_page_vm(PageClient(), drafts=runtime)
    await newer.setup()
    newer.query.set_sql("SELECT 3")
    old.query.set_sql("SELECT invalid again")
    await newer.shutdown()
    newer.dispose()
    await old.shutdown()
    old.dispose()
    assert (await runtime.shutdown()).unpersisted == 0
    assert [row.sql for row in store.list().records] == ["SELECT 3"]
    runtime.dispose()


async def test_unschedulable_incomplete_origin_can_be_completed_without_double_count(tmp_path):
    runtime, store = runtime_at(tmp_path)
    page, _ = await _unschedulable_page(runtime, incomplete=True)
    await page.setup()
    page.query.set_sql("SELECT completed")
    await page.shutdown()
    page.dispose()
    assert (await runtime.shutdown()).unpersisted == 0
    assert [row.sql for row in store.list().records] == ["SELECT completed"]
    runtime.dispose()


@pytest.mark.parametrize("mode", ["off", "demo", "staged"])
async def test_unschedulable_excluded_page_retirement_has_no_obligation(
    tmp_path, monkeypatch, mode
):
    from tests.unit.vm.athena.test_page_vm import PageClient, make_page_vm

    runtime, store = runtime_at(tmp_path, enabled=False)
    runtime._enabled = mode == "staged"
    runtime._read_only = mode == "demo"
    for name in ("list", "save", "delete", "clear"):
        monkeypatch.setattr(store, name, lambda *a, **kw: pytest.fail("excluded draft I/O"))
    page = make_page_vm(PageClient(), drafts=runtime, drafts_active=mode != "staged")
    page.query.set_sql("SELECT excluded")
    await page.shutdown()
    page.dispose()
    assert (await runtime.shutdown()).unpersisted == 0
    assert runtime._worker._thread is None
    assert not (tmp_path / "athena-drafts").exists()
    runtime.dispose()


async def test_unschedulable_suspended_edit_remains_reported_after_retirement(
    tmp_path, monkeypatch
):
    runtime, store = runtime_at(tmp_path)
    page, _ = await _unschedulable_page(runtime, incomplete=False)
    monkeypatch.setattr(
        store, "set_enabled", lambda enabled: DraftStoreResult(code="io", enabled=True)
    )
    assert not await runtime.set_enabled(True)
    page.query.set_sql("SELECT suspended")
    await page.shutdown()
    page.dispose()
    assert (await runtime.shutdown()).unpersisted == 1
    assert [row.sql for row in store.list().records] == ["SELECT 1"]
    runtime.dispose()


@pytest.mark.parametrize("outcome", ["success", "failure"])
async def test_unschedulable_revision_survives_older_physical_completion(
    tmp_path, monkeypatch, outcome
):
    from tests.unit.vm.athena.test_page_vm import PageClient, make_page_vm

    runtime, store = runtime_at(tmp_path)
    entered, release = threading.Event(), threading.Event()
    save = store.save

    def held(saved, *, permit):
        result = save(saved, permit=permit) if outcome == "success" else DraftStoreResult(code="io")
        entered.set()
        assert release.wait(10)
        return result

    monkeypatch.setattr(store, "save", held)
    page = make_page_vm(PageClient(), drafts=runtime)
    await page.setup()
    page.query.set_sql("SELECT older")
    runtime._prepare_final(page._draft_session)
    try:
        assert entered.wait(1)
        await page.select_workgroup("analysts")
        page.query.set_sql("SELECT unschedulable")
        await page.shutdown()
        page.dispose()
        release.set()
        report = await runtime.shutdown()
        assert report.unpersisted == 1
        assert not report.timed_out
        assert [row.sql for row in store.list().records] == (
            ["SELECT older"] if outcome == "success" else []
        )
    finally:
        release.set()
        await page.shutdown()
        page.dispose()
        await runtime.shutdown()
        runtime.dispose()


async def test_unschedulable_repeated_incomplete_edits_are_one_current_origin(tmp_path):
    from tests.unit.vm.athena.test_page_vm import PageClient, make_page_vm

    runtime, store = runtime_at(tmp_path)
    old, _ = await _unschedulable_page(runtime, incomplete=True)
    new = make_page_vm(PageClient(), drafts=runtime)
    new.query.set_sql("SELECT newer incomplete")
    old.query.set_sql("SELECT stale incomplete")
    for page in (new, old):
        await page.shutdown()
        page.dispose()
    assert (await runtime.shutdown()).unpersisted == 1
    assert runtime._worker._thread is None
    assert not store.list().records
    runtime.dispose()


@pytest.mark.parametrize("replacement", [False, True])
async def test_unschedulable_activated_baseline_obeys_later_writer(tmp_path, replacement):
    from tests.unit.vm.athena.test_page_vm import PageClient, make_page_vm

    runtime, store = runtime_at(tmp_path)
    old = make_page_vm(PageClient(), drafts=runtime, drafts_active=False)
    await old.setup()
    old.query.set_sql("SELECT baseline")
    old.activate_drafts()
    assert runtime._worker._thread is None
    if replacement:
        new = make_page_vm(PageClient(), drafts=runtime)
        await new.setup()
        new.query.set_sql("SELECT replacement")
        await new.shutdown()
        new.dispose()
    await old.shutdown()
    old.dispose()
    assert (await runtime.shutdown()).unpersisted == (0 if replacement else 1)
    assert [row.sql for row in store.list().records] == (
        ["SELECT replacement"] if replacement else []
    )
    runtime.dispose()
