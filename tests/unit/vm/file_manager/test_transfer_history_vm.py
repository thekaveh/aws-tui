"""Durable lifecycle and endpoint-bound recovery contracts."""

from __future__ import annotations

import asyncio
import threading
from dataclasses import replace

import pytest
from vmx import NULL_DISPATCHER, MessageHub

from aws_tui.domain.cross_fs import ConflictResolution
from aws_tui.domain.filesystem import ConflictError, NotFoundError, PermissionDeniedError
from aws_tui.domain.local_fs import LocalFS
from aws_tui.domain.transfer_history import TransferHistoryDescriptor
from aws_tui.domain.transfer_journal import TransferJournal
from aws_tui.infra.connection_resolver import Connection
from aws_tui.vm.file_manager.dual_pane_vm import DualPaneVM
from aws_tui.vm.file_manager.pane_vm import PaneVM
from aws_tui.vm.file_manager.transfers_vm import TransfersVM
from aws_tui.vm.messages import (
    TransferProgressMessage,
    TransferState,
)


def identity(provider, connection=None):
    from aws_tui.vm.credential_recovery import connection_history_identity

    return connection_history_identity(connection, provider)


async def make_dual(tmp_path):
    source = tmp_path / "source"
    destination = tmp_path / "destination"
    source.mkdir()
    destination.mkdir()
    (source / "a?#%.txt").write_bytes(b"payload")
    hub = MessageHub()
    left_fs, right_fs = LocalFS(root=source), LocalFS(root=destination)
    left = PaneVM(
        provider=left_fs, hub=hub, dispatcher=NULL_DISPATCHER, transfer_connection=identity(left_fs)
    )
    right = PaneVM(
        provider=right_fs,
        hub=hub,
        dispatcher=NULL_DISPATCHER,
        transfer_connection=identity(right_fs),
    )
    journal = TransferJournal(base_dir=tmp_path / "journal")
    dual = DualPaneVM(
        left=left, right=right, hub=hub, dispatcher=NULL_DISPATCHER, transfer_journal=journal
    )
    dual.construct()
    await dual.setup()
    left.enter_multiselect_command.execute()
    left.select_all_command.execute()
    return dual, hub, journal, source, destination


@pytest.mark.parametrize(
    ("operation", "status"),
    [
        ("copy", "completed"),
        ("move", "completed"),
        ("skip", "skipped"),
        ("fail", "failed"),
        ("delete", "completed"),
    ],
)
async def test_real_operations_persist_truthful_history(tmp_path, operation, status):
    dual, _, journal, source, destination = await make_dual(tmp_path)
    try:
        if operation in {"skip", "fail"}:
            (destination / "a?#%.txt").write_bytes(b"existing")
        if operation == "delete":
            await dual.delete_in_focused()
        elif operation == "move":
            await dual.move_across()
        elif operation == "fail":
            with pytest.raises(ConflictError):
                await dual.copy_across()
        else:
            await dual.copy_across(
                on_conflict=ConflictResolution.SKIP
                if operation == "skip"
                else ConflictResolution.ERROR
            )
        records = journal.load_history()
        assert len(records) == 1
        record = records[0]
        assert record.status == status
        assert record.operation == (operation if operation in {"move", "delete"} else "copy")
        assert record.source_uri == "/a?#%.txt"
        assert record.bytes_total == 7
        assert record.bytes_done == (7 if status == "completed" and operation != "delete" else 0)
        assert record.failure_reason == ("conflict" if operation == "fail" else None)
        if operation in {"move", "delete"}:
            assert not (source / "a?#%.txt").exists()
    finally:
        await dual.shutdown()
        dual.dispose()


async def test_binding_hash_captures_original_namespace_and_ignores_rotating_credentials(tmp_path):
    conn = Connection("original", "s3-compatible", "r", "config", endpoint_url="https://one")
    provider = LocalFS(root=tmp_path)
    bound = identity(provider, conn)
    assert bound == identity(provider, replace(conn, access_key_id="rotated"))
    assert bound != identity(LocalFS(root=tmp_path / "other"), conn)
    assert bound != identity(provider, replace(conn, endpoint_url="https://two"))
    pane = PaneVM(
        provider=provider,
        hub=MessageHub(),
        dispatcher=NULL_DISPATCHER,
        connection_key=(conn.kind, conn.name),
        transfer_connection=bound,
    )
    assert pane.transfer_connection == bound
    replacement = LocalFS(root=tmp_path / "other")
    await pane.swap_provider(replacement, connection_key=(conn.kind, conn.name))
    assert pane.transfer_connection is None
    pane.dispose()


async def history_case(tmp_path, *, operation="copy", attempted=False):
    from aws_tui.vm.file_manager.transfer_history_vm import (
        ResolvedTransferEndpoint,
        TransferHistoryVM,
    )

    dual, hub, journal, source, destination = await make_dual(tmp_path)
    src_identity, dst_identity = identity(dual.left.provider), identity(dual.right.provider)
    descriptor = TransferHistoryDescriptor(
        operation,
        src_identity,
        dst_identity if operation != "delete" else None,
        "/a?#%.txt",
        "/a?#%.txt" if operation != "delete" else None,
        7,
    )
    tid = journal.begin(
        source_uri=descriptor.source_uri,
        destination_uri=descriptor.destination_uri or "",
        bytes_total=7,
        descriptor=descriptor,
    )
    if attempted:
        journal.mark_attempted(tid)
    resolutions = []
    providers = {src_identity: dual.left.provider, dst_identity: dual.right.provider}

    async def resolve(endpoint):
        resolutions.append(endpoint)
        return ResolvedTransferEndpoint(providers[endpoint], endpoint)

    vm = TransferHistoryVM(
        journal=journal,
        resolve_endpoint=resolve,
        hub=hub,
        dispatcher=NULL_DISPATCHER,
        runtime=dual.transfer_runtime,
    )
    await vm.load()
    return vm, dual, tid, source, destination, providers, resolutions


async def choose_error(plan):
    assert plan.source_uri == "/a?#%.txt"
    return ConflictResolution.ERROR


async def test_recheck_keeps_outcome_unknown_and_retry_creates_new_progress_id(tmp_path):
    vm, dual, tid, _, destination, _, resolutions = await history_case(tmp_path, attempted=True)
    transfers = TransfersVM(hub=dual._hub, dispatcher=NULL_DISPATCHER)
    transfers.construct()
    try:
        from aws_tui.vm.file_manager.transfer_history_vm import RecoveryRefused

        with pytest.raises(RecoveryRefused, match="Recheck"):
            await vm.retry(tid, choose_error)
        inspection = await vm.recheck(tid)
        assert inspection.source is not None
        assert inspection.destination is None
        assert inspection.retry_eligible
        assert vm.records[0].status == "outcome_unknown"
        assert vm.records[0].publication == "possibly_published"
        new_id = await vm.retry(tid, choose_error)
        assert new_id != tid
        assert (destination / "a?#%.txt").read_bytes() == b"payload"
        assert transfers.transfers[0].id == new_id
        assert transfers.transfers[0].state == TransferState.COMPLETED
        assert len(resolutions) == 6
    finally:
        await vm.shutdown()
        dual.dispose()
        transfers.dispose()


@pytest.mark.parametrize("operation", ["move", "delete"])
async def test_noncopy_retry_refused_without_mutation(tmp_path, operation):
    from aws_tui.vm.file_manager.transfer_history_vm import RecoveryRefused

    vm, dual, tid, source, destination, _, _ = await history_case(tmp_path, operation=operation)
    try:
        with pytest.raises(RecoveryRefused):
            await vm.retry(tid, choose_error)
        assert (source / "a?#%.txt").exists()
        assert not (destination / "a?#%.txt").exists()
    finally:
        await vm.shutdown()
        dual.dispose()


@pytest.mark.parametrize("change", ["identity", "source", "destination"])
async def test_retry_revalidates_after_fresh_decision(tmp_path, change):
    from aws_tui.vm.file_manager.transfer_history_vm import (
        RecoveryRefused,
        ResolvedTransferEndpoint,
    )

    vm, dual, tid, source, destination, _, _ = await history_case(tmp_path)

    async def decide(plan):
        if change == "identity":

            async def redirected(endpoint):
                return ResolvedTransferEndpoint(
                    dual.left.provider, identity(LocalFS(root=tmp_path / "changed"))
                )

            vm._resolve_endpoint = redirected
        elif change == "source":
            (source / "a?#%.txt").write_bytes(b"changed")
        else:
            (destination / "a?#%.txt").write_bytes(b"unrelated")
        return ConflictResolution.OVERWRITE

    try:
        with pytest.raises(RecoveryRefused):
            await vm.retry(tid, decide)
        assert (
            not destination.joinpath("a?#%.txt").exists()
            or destination.joinpath("a?#%.txt").read_bytes() == b"unrelated"
        )
        assert len(vm.records) == 1
    finally:
        await vm.shutdown()
        dual.dispose()


async def test_recheck_existing_destination_never_claims_success(tmp_path):
    vm, dual, tid, _, destination, _, _ = await history_case(tmp_path, attempted=True)
    (destination / "a?#%.txt").write_bytes(b"payload")
    try:
        observed = await vm.recheck(tid)
        assert observed.destination.size == 7
        assert not observed.retry_eligible
        assert vm.records[0].status == "outcome_unknown"
    finally:
        await vm.shutdown()
        dual.dispose()


@pytest.mark.parametrize("error", [NotFoundError("secret"), PermissionDeniedError("secret")])
async def test_source_errors_are_fixed_safe_refusals(tmp_path, error):
    from aws_tui.vm.file_manager.transfer_history_vm import RecoveryRefused

    vm, dual, tid, _, _, _, _ = await history_case(tmp_path)

    async def failed_stat(path):
        raise error

    dual.left.provider.stat = failed_stat
    try:
        with pytest.raises(RecoveryRefused) as caught:
            await vm.retry(tid, choose_error)
        assert "secret" not in str(caught.value)
        assert caught.value.reason in {"not_found", "permission_denied"}
    finally:
        await vm.shutdown()
        dual.dispose()


@pytest.mark.parametrize("stage", ["begin", "mark_attempted"])
async def test_begin_and_attempt_failures_refuse_provider_mutation(tmp_path, monkeypatch, stage):
    dual, _, journal, _, destination = await make_dual(tmp_path)

    def fail(*args, **kwargs):
        raise OSError("secret disk details")

    monkeypatch.setattr(journal, stage, fail)
    try:
        with pytest.raises(Exception, match="history"):
            await dual.copy_across()
        assert not (destination / "a?#%.txt").exists()
    finally:
        await dual.shutdown()
        dual.dispose()


async def test_terminal_write_failure_preserves_live_provider_success(tmp_path, monkeypatch):
    dual, hub, journal, _, destination = await make_dual(tmp_path)
    states = []
    hub.messages.subscribe(
        lambda m: states.append(m.state) if isinstance(m, TransferProgressMessage) else None
    )

    def fail(*args, **kwargs):
        raise OSError("secret disk details")

    monkeypatch.setattr(journal, "mark_terminal", fail)
    try:
        await dual.copy_across()
        assert (destination / "a?#%.txt").read_bytes() == b"payload"
        assert states[-1] == TransferState.COMPLETED
        assert dual.transfer_runtime.error_text == "Transfer history could not be saved."
        assert journal.load_history()[0].status == "outcome_unknown"
    finally:
        await dual.shutdown()
        dual.dispose()


async def test_owned_disk_begin_drains_cancellation_and_excludes_active_metadata(
    tmp_path, monkeypatch
):
    from aws_tui.vm.file_manager.transfer_history_vm import TransferHistoryVM

    dual, hub, journal, _, destination = await make_dual(tmp_path)
    entered, release = threading.Event(), threading.Event()
    original = journal.begin

    def blocked_begin(**kwargs):
        tid = original(**kwargs)
        entered.set()
        assert release.wait(5)
        return tid

    monkeypatch.setattr(journal, "begin", blocked_begin)
    vm = TransferHistoryVM(
        journal=journal,
        resolve_endpoint=lambda _: None,
        hub=hub,
        dispatcher=NULL_DISPATCHER,
        runtime=dual.transfer_runtime,
    )
    transfer = asyncio.create_task(dual.copy_across())
    assert await asyncio.to_thread(entered.wait, 2)
    transfer.cancel()
    shutdown = asyncio.create_task(dual.shutdown())
    await vm.load()
    assert vm.records == ()
    assert not transfer.done()
    assert not shutdown.done()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(transfer, 3)
    await asyncio.wait_for(shutdown, 3)
    assert not destination.joinpath("a?#%.txt").exists()
    await vm.load()
    assert vm.records[0].status == "cancelled"
    await vm.shutdown()
    dual.dispose()


async def test_clear_preserves_active_journal_and_corrupt_metadata(tmp_path):
    vm, dual, tid, source, destination, _, _ = await history_case(tmp_path)
    corrupt = tmp_path / "journal" / "not-owned.jsonl"
    corrupt.write_text("garbage")
    dual.transfer_runtime.owned_ids.add(tid)
    try:
        await vm.clear()
        assert dual._journal.load_history()[0].id == tid
        assert corrupt.read_text() == "garbage"
        assert source.joinpath("a?#%.txt").exists()
        assert not destination.joinpath("a?#%.txt").exists()
        dual.transfer_runtime.owned_ids.remove(tid)
        await vm.clear()
        assert dual._journal.load_history() == ()
        assert corrupt.exists()
    finally:
        await vm.shutdown()
        dual.dispose()


async def test_clear_preserves_terminal_summary_while_publication_purge_drains(
    tmp_path, monkeypatch
):
    from aws_tui.vm.file_manager.transfer_history_vm import TransferHistoryVM

    dual, hub, journal, _, destination = await make_dual(tmp_path)
    entered, release = threading.Event(), threading.Event()
    original = journal.purge

    def blocked_purge(tid):
        entered.set()
        assert release.wait(5)
        original(tid)

    monkeypatch.setattr(journal, "purge", blocked_purge)
    vm = TransferHistoryVM(
        journal, lambda _: None, hub, NULL_DISPATCHER, runtime=dual.transfer_runtime
    )
    transfer = asyncio.create_task(dual.copy_across())
    assert await asyncio.to_thread(entered.wait, 2)
    clearing = asyncio.create_task(vm.clear())
    assert journal.history_store.load()[0].status == "completed"
    assert not clearing.done()
    release.set()
    await asyncio.wait_for(transfer, 3)
    await asyncio.wait_for(clearing, 3)
    assert destination.joinpath("a?#%.txt").read_bytes() == b"payload"
    await vm.shutdown()
    dual.dispose()


async def test_domain_clear_excludes_owned_terminal_summary_and_journal(tmp_path):
    vm, dual, tid, _, _, _, _ = await history_case(tmp_path)
    other = dual._journal.begin(
        source_uri="/a?#%.txt",
        destination_uri="/b",
        bytes_total=7,
        descriptor=TransferHistoryDescriptor(
            "copy",
            vm.records[0].source_connection,
            vm.records[0].destination_connection,
            "/a?#%.txt",
            "/b",
            7,
        ),
    )
    dual._journal.mark_terminal(tid, status="completed", bytes_done=7, bytes_total=7)
    dual._journal.clear_history(exclude_ids=frozenset({tid, other}))
    assert {record.id for record in dual._journal.load_history()} == {tid, other}
    dual._journal.clear_history(exclude_ids=frozenset({tid}))
    assert [record.id for record in dual._journal.load_history()] == [tid]
    await vm.shutdown()
    dual.dispose()


async def test_shutdown_cancels_retry_waiting_for_decision(tmp_path):
    vm, dual, tid, _, destination, _, _ = await history_case(tmp_path)
    deciding = asyncio.Event()

    async def decide(plan):
        deciding.set()
        await asyncio.Event().wait()

    task = asyncio.create_task(vm.retry(tid, decide))
    await asyncio.wait_for(deciding.wait(), 2)
    await asyncio.wait_for(vm.shutdown(), 2)
    assert task.done()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not destination.joinpath("a?#%.txt").exists()
    dual.dispose()


async def test_loading_failure_is_safe_and_clear_failure_is_reported(tmp_path, monkeypatch):
    from aws_tui.vm.file_manager.transfer_history_vm import RecoveryRefused

    vm, dual, _tid, _, _, _, _ = await history_case(tmp_path)

    def fail():
        raise OSError("secret diagnostic")

    monkeypatch.setattr(dual._journal, "load_history", fail)
    await vm.load()
    assert vm.load_state == "error"
    assert vm.error_text == "Transfer history could not be loaded."

    def failed_clear(**kwargs):
        raise OSError("secret diagnostic")

    monkeypatch.setattr(dual._journal, "clear_history", failed_clear)
    with pytest.raises(RecoveryRefused, match="could not be cleared"):
        await vm.clear()
    assert vm.error_text == "Transfer history could not be cleared."
    await vm.shutdown()
    dual.dispose()


async def test_staged_recovery_moves_binding_atomically_with_provider(tmp_path):
    dual, hub, _, _, destination = await make_dual(tmp_path)
    original = dual.left.transfer_connection
    replacement = LocalFS(root=destination)
    bound = identity(replacement)
    stage = await dual.left.stage_provider_recovery(
        replacement,
        path=dual.left.path,
        identity_label="recovered",
        path_protocol="",
        connection_key=("aws", "original"),
        transfer_connection=bound,
    )
    assert dual.left.transfer_connection == original
    seen = []
    hub.messages.subscribe(
        lambda _: seen.append((dual.left.provider, dual.left.transfer_connection))
    )
    dual.left.commit_provider_recovery(stage)
    assert seen
    assert all(provider is replacement and binding == bound for provider, binding in seen)
    dual.dispose()


@pytest.mark.parametrize("total", [None, 0])
async def test_unknown_and_zero_observed_totals_survive_terminal_storage(tmp_path, total):
    from aws_tui.domain.filesystem import TransferProgress

    dual, _, journal, _, _ = await make_dual(tmp_path)
    tids = await dual._pre_register_pending(list(dual.left.marked_entries), dual.left, dual.right)
    entry, tid = tids[0]

    async def operation(source, destination, *, progress, on_conflict):
        progress(TransferProgress(0, total))
        return True

    assert await dual._run_one_transfer(
        operation=operation,
        src_path=None,
        dst_path=None,
        on_conflict=ConflictResolution.ERROR,
        transfer_id=tid,
        entry=entry,
    )
    await dual._mark_transfer_completed(tid, entry)
    dual.transfer_runtime.release(tid)
    assert journal.load_history()[0].bytes_total == total
    assert journal.load_history()[0].bytes_done == 0
    dual.dispose()


async def test_repeated_cancellation_settles_every_unconsumed_pending_row(tmp_path, monkeypatch):
    dual, _, journal, source, _ = await make_dual(tmp_path)
    (source / "b").write_bytes(b"b")
    (source / "c").write_bytes(b"c")
    await dual.left.refresh()
    dual.left.enter_multiselect_command.execute()
    dual.left.select_all_command.execute()
    provider_started = asyncio.Event()

    async def blocked_read(*args, **kwargs):
        provider_started.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(dual.left.provider, "read_stream", blocked_read)
    entered, release = threading.Event(), threading.Event()
    original = journal.mark_terminal
    calls = 0

    def blocked_terminal(tid, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            entered.set()
            assert release.wait(5)
        original(tid, **kwargs)

    monkeypatch.setattr(journal, "mark_terminal", blocked_terminal)
    transfer = asyncio.create_task(dual.copy_across())
    await asyncio.wait_for(provider_started.wait(), 2)
    transfer.cancel()
    assert await asyncio.to_thread(entered.wait, 2)
    transfer.cancel()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(transfer, 3)
    records = journal.load_history()
    assert len(records) == 3
    assert all(record.status == "cancelled" for record in records)
    assert not dual.transfer_runtime.owned_ids
    dual.dispose()


async def test_cancelled_display_row_stays_owned_until_provider_cleanup_drains(
    tmp_path, monkeypatch
):
    from aws_tui.vm.file_manager.transfer_history_vm import TransferHistoryVM

    dual, hub, journal, _, destination = await make_dual(tmp_path)
    transfers = TransfersVM(hub=hub, dispatcher=NULL_DISPATCHER)
    transfers.construct()
    vm = TransferHistoryVM(
        journal, lambda _: None, hub, NULL_DISPATCHER, runtime=dual.transfer_runtime
    )
    started, draining, release = asyncio.Event(), asyncio.Event(), asyncio.Event()

    async def read(*args, **kwargs):
        started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            draining.set()
            await release.wait()
            raise

    monkeypatch.setattr(dual.left.provider, "read_stream", read)
    transfer = asyncio.create_task(dual.copy_across())
    await asyncio.wait_for(started.wait(), 2)
    tid = transfers.transfers[0].id
    transfers.cancel(tid)
    await asyncio.wait_for(draining.wait(), 2)
    assert transfers.transfers[0].state == TransferState.CANCELLED
    assert tid in dual.transfer_runtime.owned_ids
    await vm.load()
    assert vm.records == ()
    await vm.clear()
    assert journal.load_history()[0].id == tid
    assert not destination.joinpath("a?#%.txt").exists()
    release.set()
    await asyncio.wait_for(transfer, 3)
    await vm.load()
    assert vm.records[0].status == "cancelled"
    assert not dual.transfer_runtime.owned_ids
    await vm.shutdown()
    dual.dispose()
    transfers.dispose()


async def test_disk_calls_leave_loop_but_hub_updates_remain_on_loop(tmp_path, monkeypatch):
    dual, hub, journal, _, _ = await make_dual(tmp_path)
    loop_thread = threading.get_ident()
    writers, observers = [], []
    for name in ("begin", "mark_attempted", "mark_terminal"):
        original = getattr(journal, name)

        def wrapped(*args, _original=original, **kwargs):
            writers.append(threading.get_ident())
            return _original(*args, **kwargs)

        monkeypatch.setattr(journal, name, wrapped)
    hub.messages.subscribe(lambda _: observers.append(threading.get_ident()))
    await dual.copy_across()
    assert len(writers) == 3
    assert all(thread != loop_thread for thread in writers)
    assert observers
    assert set(observers) == {loop_thread}
    dual.dispose()


async def test_retry_provider_failure_has_safe_category_and_durable_new_outcome(
    tmp_path, monkeypatch
):
    from aws_tui.vm.file_manager.transfer_history_vm import RecoveryRefused

    vm, dual, tid, _, _, _, _ = await history_case(tmp_path)

    async def fail(*args, **kwargs):
        raise PermissionDeniedError("secret endpoint credential diagnostic")

    monkeypatch.setattr(dual.left.provider, "read_stream", fail)
    try:
        with pytest.raises(RecoveryRefused) as caught:
            await vm.retry(tid, choose_error)
        assert caught.value.reason == "permission_denied"
        assert "secret" not in str(caught.value)
        await vm.load()
        new_record = next(record for record in vm.records if record.id != tid)
        assert new_record.status == "failed"
        assert new_record.failure_reason == "permission_denied"
    finally:
        await vm.shutdown()
        dual.dispose()
