"""Running-app history accessibility and owned disk operations."""

from __future__ import annotations

import threading

import pytest
from textual.worker import get_current_worker

from aws_tui.ui.widgets.transfers_overlay import TransfersOverlay
from aws_tui.vm.chrome.focus_coordinator_vm import FocusSlot
from tests.helpers import drain_workers, wait_until
from tests.transfer_history_helpers import NAME, history_app


async def test_startup_load_is_owned_and_never_replays(tmp_path, monkeypatch):
    app, tid, source, destination, resolutions = history_app(tmp_path, attempted=True)
    ctx = app.app_ctx
    calls = []
    original = ctx.transfer_journal.load_history

    def probe():
        calls.append((threading.get_ident(), get_current_worker()))
        return original()

    monkeypatch.setattr(ctx.transfer_journal, "load_history", probe)
    async with app.run_test(size=(80, 24)) as pilot:
        await drain_workers(app)
        assert ctx.transfer_history_vm.load_state == "ready"
        assert ctx.transfer_history_vm.records[0].id == tid
        assert ctx.transfer_history_vm.records[0].status == "outcome_unknown"
        assert calls
        assert all(thread != threading.get_ident() for thread, _ in calls)
        assert all(worker.group == "transfer-history-load" for _, worker in calls)
        assert resolutions == []
        assert list(destination.iterdir()) == []
        assert (source / NAME).read_bytes() == b"payload"
        await pilot.press("ctrl+t")
        await pilot.pause()
        assert type(app.screen).__name__ == "TransferHistoryModal"


@pytest.mark.parametrize("seeded", [False, True])
async def test_overlay_bridge_and_modal_focus_containment(tmp_path, seeded):
    app, _, _, _, _ = history_app(tmp_path, seeded=seeded)
    async with app.run_test(size=(80, 24)) as pilot:
        await drain_workers(app)
        overlay = app.query_one(TransfersOverlay)
        assert hasattr(overlay, "open_history")
        await pilot.press("tab")
        previous_slot = app.app_ctx.focus_coordinator.focused_slot
        dual = app._dual_pane()
        previous_paths = (dual.left.path, dual.right.path)
        await pilot.press("ctrl+t")
        await pilot.pause()
        assert type(app.screen).__name__ == "TransferHistoryModal"
        assert app.screen.mode == "history"
        await pilot.press("tab", "r")
        assert app.screen.mode == "recovery"
        await pilot.press("enter", "up", "down")
        assert (dual.left.path, dual.right.path) == previous_paths
        assert app.app_ctx.focus_coordinator.focused_slot == FocusSlot.MODAL
        assert app.focused in app.screen.query("*")
        await pilot.press("escape")
        await pilot.pause()
        assert len(app.screen_stack) == 1
        assert app.app_ctx.focus_coordinator.focused_slot == previous_slot
        assert not app.app_ctx.focus_coordinator.is_modal
        if seeded:
            await pilot.click("#transfer-recovery-open")
            assert app.screen.mode == "recovery"


async def test_history_details_are_literal_and_clear_requires_confirmation(tmp_path, monkeypatch):
    app, tid, source, destination, _ = history_app(tmp_path)
    calls = []
    original = app.app_ctx.transfer_journal.clear_history

    def clear_probe(**kwargs):
        calls.append((threading.get_ident(), get_current_worker()))
        return original(**kwargs)

    monkeypatch.setattr(app.app_ctx.transfer_journal, "clear_history", clear_probe)
    async with app.run_test(size=(120, 40)) as pilot:
        await drain_workers(app)
        await pilot.press("ctrl+t")
        await pilot.pause()
        assert type(app.screen).__name__ == "TransferHistoryModal"
        details = app.screen.query_one("#transfer-history-details")
        await wait_until(lambda: NAME in details.text, what="full transfer details rendered")
        assert NAME in details.text
        from textual.widgets import OptionList

        assert NAME in str(app.screen.query_one(OptionList).get_option_at_index(0).prompt)
        assert "never attempted" in details.text
        assert tid in details.text
        await pilot.click("#history-clear")
        await pilot.pause()
        assert type(app.screen).__name__ == "ConfirmModal"
        await pilot.press("enter")
        await drain_workers(app)
        assert len(app.app_ctx.transfer_history_vm.records) == 1
        await pilot.click("#history-clear")
        await pilot.press("right", "enter")
        await drain_workers(app)
        assert app.app_ctx.transfer_history_vm.records == ()
        assert calls
        assert all(thread != threading.get_ident() for thread, _ in calls)
        assert all(worker.group == "transfer-history-operation" for _, worker in calls)
        assert (source / NAME).exists()
        assert not list(destination.iterdir())


async def open_history(app, pilot, *, recovery=False):
    await drain_workers(app)
    await pilot.press("ctrl+t")
    await wait_until(
        lambda: type(app.screen).__name__ == "TransferHistoryModal", what="history screen mounted"
    )
    await wait_until(
        lambda: bool(app.screen.query_one("#transfer-history-details").text),
        what="history details mounted",
    )
    if recovery:
        await pilot.press("r")
    return app.screen


@pytest.mark.parametrize("decision", ["error", "skip", "rename", "overwrite"])
async def test_retry_explicit_conflict_decisions_use_registered_progress_worker(tmp_path, decision):
    from aws_tui.ui.widgets.modal_button import ModalButton
    from aws_tui.vm.messages import TransferState

    app, original_id, source, destination, resolutions = history_app(tmp_path)
    (destination / NAME).write_bytes(b"existing")
    async with app.run_test(size=(80, 24)) as pilot:
        modal = await open_history(app, pilot, recovery=True)
        await pilot.click("#history-retry")
        await wait_until(
            lambda: type(app.screen).__name__ == "RetryConflictModal",
            what="fresh retry conflict decision",
        )
        assert app.focused.button_id == "error"
        assert NAME in app.screen.query_one("#retry-endpoints").text
        assert len(resolutions) == 2
        choice = next(
            button for button in app.screen.query(ModalButton) if button.button_id == decision
        )
        choice.focus()
        await pilot.press("enter")
        await drain_workers(app)
        assert app.screen is modal
        assert len(resolutions) == 4
        if decision == "error":
            assert (destination / NAME).read_bytes() == b"existing"
            assert "could not be completed" in str(modal.query_one("#history-status").content)
        elif decision == "skip":
            assert (destination / NAME).read_bytes() == b"existing"
            assert app.app_ctx.transfers_vm.transfers[-1].state == TransferState.SKIPPED
        else:
            assert app.app_ctx.transfers_vm.transfers[-1].state == TransferState.COMPLETED
            assert any(path.read_bytes() == b"payload" for path in destination.iterdir())
        records = app.app_ctx.transfer_journal.load_history()
        assert len(records) == 2
        assert any(record.id != original_id for record in records)
        assert (source / NAME).read_bytes() == b"payload"


@pytest.mark.parametrize(
    ("operation", "attempted"), [("move", False), ("delete", False), ("copy", True)]
)
async def test_refused_recovery_never_mutates_endpoints(tmp_path, operation, attempted):
    app, _, source, destination, resolutions = history_app(
        tmp_path, operation=operation, attempted=attempted
    )
    if attempted:
        (destination / NAME).write_bytes(b"possibly published")
    async with app.run_test(size=(120, 40)) as pilot:
        modal = await open_history(app, pilot, recovery=True)
        await pilot.click("#history-retry")
        await drain_workers(app)
        assert app.screen is modal
        assert app.app_ctx.transfers_vm.transfers == ()
        if attempted:
            await pilot.click("#history-recheck")
            await drain_workers(app)
            assert "Destination present" in str(modal.query_one("#history-status").content)
            await pilot.click("#history-retry")
            await drain_workers(app)
            assert app.screen is modal
            assert (destination / NAME).read_bytes() == b"possibly published"
        else:
            assert resolutions == []
            assert not list(destination.iterdir())
        assert (source / NAME).read_bytes() == b"payload"


async def test_source_change_after_decision_refuses_and_escape_cancels_decision(tmp_path):
    app, _, source, destination, _ = history_app(tmp_path)
    async with app.run_test(size=(120, 40)) as pilot:
        modal = await open_history(app, pilot)
        await pilot.click("#history-retry")
        await wait_until(
            lambda: type(app.screen).__name__ == "RetryConflictModal", what="decision screen"
        )
        await pilot.press("escape")
        await drain_workers(app)
        assert app.screen is modal
        assert not list(destination.iterdir())
        await pilot.click("#history-retry")
        await wait_until(
            lambda: type(app.screen).__name__ == "RetryConflictModal", what="second fresh decision"
        )
        (source / NAME).write_bytes(b"changed size")
        await pilot.press("enter")
        await drain_workers(app)
        assert "endpoints changed" in str(modal.query_one("#history-status").content)
        assert not list(destination.iterdir())
        assert app.app_ctx.transfers_vm.transfers == ()


async def test_startup_blocked_disk_remains_responsive_and_shutdown_drains(tmp_path, monkeypatch):
    import asyncio

    app, _, _, _, _ = history_app(tmp_path)
    entered, release, finished = threading.Event(), threading.Event(), threading.Event()
    original = app.app_ctx.transfer_journal.load_history

    def blocked():
        entered.set()
        try:
            assert release.wait(10), "startup disk barrier released"
            return original()
        finally:
            finished.set()

    monkeypatch.setattr(app.app_ctx.transfer_journal, "load_history", blocked)
    async with app.run_test(size=(80, 24)) as pilot:
        try:
            await wait_until(entered.is_set, what="owned startup scan entered disk thread")
            assert any(worker.group == "transfer-history-load" for worker in app.workers._workers)
            await pilot.press("ctrl+t")
            await wait_until(
                lambda: type(app.screen).__name__ == "TransferHistoryModal",
                what="responsive loading history modal",
            )
            assert "Loading" in str(app.screen.query_one("#history-status").content)
            await pilot.press("escape")
            await wait_until(
                lambda: len(app.screen_stack) == 1, what="modal closed during blocked scan"
            )
            shutdown = asyncio.create_task(app._aws_tui_shutdown())
            await wait_until(
                lambda: app.app_ctx.transfer_history_vm._closed,
                what="history shutdown closes intake",
            )
            assert not shutdown.done()
            assert not finished.is_set()
        finally:
            release.set()
        await shutdown
        assert finished.is_set()
        await wait_until(
            lambda: not app.workers._workers, what="all Textual startup workers drained"
        )
        assert app._shutdown_errors == ()


async def test_closed_modal_suppresses_late_recheck_and_drains_worker(tmp_path, monkeypatch):
    import asyncio

    app, _, _, _, _ = history_app(tmp_path)
    vm = app.app_ctx.transfer_history_vm
    entered, release = asyncio.Event(), asyncio.Event()
    original = vm._resolve_endpoint

    async def blocked(identity):
        entered.set()
        await release.wait()
        return await original(identity)

    monkeypatch.setattr(vm, "_resolve_endpoint", blocked)
    async with app.run_test(size=(80, 24)) as pilot:
        modal = await open_history(app, pilot)
        await pilot.click("#history-recheck")
        await wait_until(entered.is_set, what="recheck endpoint resolution entered")
        await pilot.press("escape")
        release.set()
        await drain_workers(app)
        assert len(app.screen_stack) == 1
        assert modal._history_closed
        assert vm.records[0].status == "outcome_unknown"
        assert not vm._operations


async def test_load_and_clear_errors_are_safe_and_details_copy_losslessly(tmp_path, monkeypatch):
    app, _, _, _, _ = history_app(tmp_path)
    ctx = app.app_ctx
    async with app.run_test(size=(120, 40)) as pilot:
        modal = await open_history(app, pilot)
        text = modal.query_one("#transfer-history-details").text
        await pilot.click("#history-copy-details")
        await drain_workers(app)
        assert ctx.clipboard_vm._clipboard.writes == [text]
        assert NAME in text

        def fail(**kwargs):
            raise OSError("seeded-secret-NEVER-SHOW")

        monkeypatch.setattr(ctx.transfer_journal, "load_history", fail)
        await pilot.click("#history-reload")
        await drain_workers(app)
        assert ctx.transfer_history_vm.load_state == "error"
        await wait_until(
            lambda: "could not be loaded" in str(modal.query_one("#history-status").content),
            what="fixed history load error rendered",
        )
        from html import unescape

        rendered = unescape(app.export_screenshot()).replace("\xa0", " ")
        assert "could not be loaded" in rendered
        assert "seeded-secret-NEVER-SHOW" not in rendered
        monkeypatch.setattr(ctx.transfer_journal, "clear_history", fail)
        await pilot.click("#history-clear")
        await wait_until(
            lambda: type(app.screen).__name__ == "ConfirmModal", what="clear error confirmation"
        )
        await pilot.press("right", "enter")
        await drain_workers(app)
        assert "could not be cleared" in str(modal.query_one("#history-status").content)
        assert "seeded-secret-NEVER-SHOW" not in app.export_screenshot()


async def ordinary_copy(app, pilot, source, destination):
    from aws_tui.domain.local_fs import LocalFS
    from aws_tui.vm.credential_recovery import connection_history_identity

    await drain_workers(app)
    dual = app._dual_pane()
    assert dual is not None
    for pane, root in ((dual.left, source), (dual.right, destination)):
        provider = LocalFS(root=root)
        await pane.swap_provider(
            provider, transfer_connection=connection_history_identity(None, provider)
        )
    await pilot.press("tab", "c")
    await wait_until(
        lambda: type(app.screen).__name__ == "ConfirmModal", what="ordinary copy confirmation"
    )
    await pilot.press("enter")


async def test_actual_ordinary_copy_save_and_trim_worker_overlay_survives_linger(
    tmp_path, monkeypatch
):
    import aws_tui.ui.widgets.transfers_overlay as overlay_module
    from aws_tui.vm.messages import TransferState

    app, old_id, source, destination, _ = history_app(tmp_path)
    ctx = app.app_ctx
    ctx.transfer_journal.mark_terminal(old_id, status="completed", bytes_done=7, bytes_total=7)
    store = ctx.transfer_journal.history_store
    store.retention_limit = 1
    entered, release = threading.Event(), threading.Event()
    calls = []
    save, remove = store.save, store._remove_owned

    def save_probe(record):
        calls.append(("save", threading.get_ident(), get_current_worker()))
        entered.set()
        assert release.wait(10), "terminal save barrier released"
        save(record)

    def trim_probe(record):
        calls.append(("trim", threading.get_ident(), get_current_worker()))
        remove(record)

    monkeypatch.setattr(store, "save", save_probe)
    monkeypatch.setattr(store, "_remove_owned", trim_probe)
    monkeypatch.setattr(overlay_module, "_LINGER_SECONDS", 0)
    async with app.run_test(size=(80, 24)) as pilot:
        try:
            await ordinary_copy(app, pilot, source, destination)
            await wait_until(entered.is_set, what="ordinary copy terminal save thread entered")
            assert any(worker.group == "transfer-copy" for worker in app.workers._workers)
            await pilot.press("ctrl+t")
            assert type(app.screen).__name__ == "TransferHistoryModal"
            await pilot.press("escape")
            assert len(app.screen_stack) == 1
        finally:
            release.set()
        await drain_workers(app)
        await wait_until(
            lambda: (
                len(ctx.transfer_history_vm.records) == 1
                and ctx.transfer_history_vm.records[0].id != old_id
            ),
            what="retained history refreshed after durable settlement",
        )
        overlay = app.query_one(TransfersOverlay)
        await wait_until(
            lambda: not overlay.query("TransferRowWidget"),
            what="finished progress row linger expired",
        )
        assert not overlay.has_class("-hidden")
        assert overlay.query_one("#transfer-history-open").display
        assert ctx.transfers_vm.transfers[-1].state == TransferState.COMPLETED
        assert {kind for kind, _, _ in calls} == {"save", "trim"}
        assert all(thread != threading.get_ident() for _, thread, _ in calls)
        assert all(worker.group == "transfer-copy" for _, _, worker in calls)
        assert (destination / NAME).read_bytes() == b"payload"
        await pilot.click("#transfer-history-open")
        assert app.screen.mode == "history"


async def test_terminal_save_error_preserves_completed_copy_and_safe_separate_warning(
    tmp_path, monkeypatch
):
    from aws_tui.vm.messages import TransferState

    app, _, source, destination, _ = history_app(tmp_path, seeded=False)
    ctx = app.app_ctx

    def fail_save(record):
        assert get_current_worker().group == "transfer-copy"
        assert threading.get_ident() != main_thread
        raise OSError("seeded-secret-NEVER-SHOW")

    main_thread = threading.get_ident()
    monkeypatch.setattr(ctx.transfer_journal.history_store, "save", fail_save)
    async with app.run_test(size=(120, 40)) as pilot:
        await ordinary_copy(app, pilot, source, destination)
        await drain_workers(app)
        assert (destination / NAME).read_bytes() == b"payload"
        assert ctx.transfers_vm.transfers[-1].state == TransferState.COMPLETED
        assert ctx.transfer_history_vm.runtime.error_text == "Transfer history could not be saved."
        assert ctx.transfer_history_vm.records[0].status == "outcome_unknown"
        assert any(
            "Transfer history could not be saved." in toast.model.text
            for toast in ctx.root_vm.chrome.toast_stack.toasts
        )
        assert "seeded-secret-NEVER-SHOW" not in app.export_screenshot()


@pytest.mark.parametrize("status", ["cancelled", "failed"])
async def test_recheck_preserves_known_outcome_and_publication(tmp_path, status):
    app, tid, source, destination, _ = history_app(tmp_path, attempted=True)
    vm = app.app_ctx.transfer_history_vm
    app.app_ctx.transfer_journal.mark_terminal(tid, status=status, bytes_done=0, bytes_total=7)
    async with app.run_test(size=(80, 24)) as pilot:
        modal = await open_history(app, pilot)
        before = vm.records[0]
        assert before.status == status
        assert before.publication == "possibly_published"
        await pilot.click("#history-recheck")
        await drain_workers(app)
        assert vm.records == (before,)
        details = modal.query_one("#transfer-history-details").text
        assert f"Outcome: {status}" in details
        assert "Publication: possibly published" in details
        assert not app.app_ctx.transfers_vm.transfers
        assert not list(destination.iterdir())
        assert (source / NAME).read_bytes() == b"payload"
        assert str(modal.query_one("#history-status").content) == (
            "Destination absent. A new copy may be requested; prior outcome unchanged."
        )


async def test_uncertain_copy_recheck_absent_then_new_retry_and_cancel_progress(
    tmp_path, monkeypatch
):
    import asyncio

    from aws_tui.domain.local_fs import LocalFS
    from aws_tui.vm.messages import TransferState

    app, original_id, source, destination, _ = history_app(tmp_path, attempted=True)
    entered, release = asyncio.Event(), asyncio.Event()
    original = LocalFS.read_stream

    async def blocked_read(self, path, **kwargs):
        stream = await original(self, path, **kwargs)

        async def chunks():
            async for chunk in stream:
                yield chunk
                entered.set()
                await release.wait()

        return chunks()

    monkeypatch.setattr(LocalFS, "read_stream", blocked_read)
    async with app.run_test(size=(80, 24)) as pilot:
        try:
            modal = await open_history(app, pilot, recovery=True)
            before = app.app_ctx.transfer_history_vm.records[0]
            assert before.publication == "possibly_published"
            await pilot.click("#history-recheck")
            await drain_workers(app)
            assert str(modal.query_one("#history-status").content) == (
                "Destination absent. A new copy may be requested; prior outcome unchanged."
            )
            assert app.app_ctx.transfer_history_vm.records == (before,)
            assert app.app_ctx.transfer_history_vm.records[0].status == "outcome_unknown"
            await pilot.click("#history-retry")
            await wait_until(
                lambda: type(app.screen).__name__ == "RetryConflictModal",
                what="rechecked copy decision",
            )
            await pilot.press("enter")
            await wait_until(entered.is_set, what="retry streaming entered provider")
            rows = app.app_ctx.transfers_vm.transfers
            assert len(rows) == 1
            assert rows[0].id != original_id
            assert rows[0].state == TransferState.RUNNING
            await pilot.press("escape")
            await wait_until(
                lambda: len(app.screen_stack) == 1, what="history close cancels owned retry"
            )
        finally:
            release.set()
        await drain_workers(app)
        assert rows[0].state == TransferState.CANCELLED
        assert not list(destination.iterdir())
        assert (source / NAME).read_bytes() == b"payload"
        assert not app.app_ctx.transfer_history_vm.runtime.owned_ids
        assert any(
            record.id == rows[0].id and record.status == "cancelled"
            for record in app.app_ctx.transfer_journal.load_history()
        )


async def test_changed_identity_refuses_before_conflict_and_double_submit_is_guarded(
    tmp_path, monkeypatch
):
    from dataclasses import replace

    from aws_tui.vm.file_manager.transfer_history_vm import ResolvedTransferEndpoint

    app, _, _, destination, _ = history_app(tmp_path)
    vm = app.app_ctx.transfer_history_vm
    resolve = vm._resolve_endpoint

    async def changed(identity):
        endpoint = await resolve(identity)
        return ResolvedTransferEndpoint(endpoint.provider, replace(identity, fingerprint="f" * 64))

    monkeypatch.setattr(vm, "_resolve_endpoint", changed)
    async with app.run_test(size=(120, 40)) as pilot:
        modal = await open_history(app, pilot)
        await pilot.click("#history-retry")
        await drain_workers(app)
        assert app.screen is modal
        assert "connection" in str(modal.query_one("#history-status").content).lower()
        assert not list(destination.iterdir())
        assert app.app_ctx.transfers_vm.transfers == ()
        monkeypatch.setattr(vm, "_resolve_endpoint", resolve)
        button = modal.query_one("#history-retry")
        button.press()
        button.press()
        await wait_until(
            lambda: type(app.screen).__name__ == "RetryConflictModal",
            what="single guarded decision",
        )
        assert len(app.screen_stack) == 3
        assert (
            len([worker for worker in app.workers._workers if worker.group == "transfer-copy"]) == 1
        )
        await pilot.press("escape")
        await drain_workers(app)
        assert len(app.screen_stack) == 2
        assert not list(destination.iterdir())


async def test_palette_dispatch_and_remapped_history_action(tmp_path):
    from aws_tui.infra.keymap_store import KeymapStore

    app, _, _, _, _ = history_app(tmp_path, seeded=False)
    # The app materializes configured bindings in __init__, as production does.
    context = app.app_ctx
    context.keymap_store = KeymapStore(overlay={"app.transfer_history": ("ctrl+h",)})
    from aws_tui.app import AwsTuiApp

    app = AwsTuiApp(context)
    async with app.run_test(size=(80, 24)) as pilot:
        await drain_workers(app)
        await pilot.press("ctrl+t")
        assert len(app.screen_stack) == 1
        await pilot.press("ctrl+h")
        await wait_until(
            lambda: type(app.screen).__name__ == "TransferHistoryModal", what="remapped history key"
        )
        await pilot.press("escape", "ctrl+k")
        await wait_until(
            lambda: type(app.screen).__name__ == "CommandPalette", what="palette mounted"
        )
        await pilot.press(*"transfer history")
        await wait_until(
            lambda: (
                bool(context.command_palette_vm.filtered_entries)
                and context.command_palette_vm.filtered_entries[0].id == "app.transfer_history"
            ),
            what="history palette entry selected",
        )
        await pilot.press("enter")
        await wait_until(
            lambda: type(app.screen).__name__ == "TransferHistoryModal",
            what="palette history dispatch",
        )


def test_ctrl_t_history_collision_requires_explicit_migration():
    from aws_tui.infra.keymap_store import KeybindingCollision, KeymapStore

    with pytest.raises(KeybindingCollision):
        KeymapStore(overlay={"app.help": ("ctrl+t",)})
    assert KeymapStore(
        overlay={"app.help": ("ctrl+t",), "app.transfer_history": ("ctrl+h",)}
    ).resolve("app.transfer_history") == ("ctrl+h",)


async def test_clear_disk_started_before_modal_close_is_drained_by_shutdown(tmp_path, monkeypatch):
    import asyncio

    app, _, source, destination, _ = history_app(tmp_path)
    entered, release, finished = threading.Event(), threading.Event(), threading.Event()
    clear = app.app_ctx.transfer_journal.clear_history
    calls = []

    def blocked_clear(**kwargs):
        calls.append((threading.get_ident(), get_current_worker()))
        entered.set()
        try:
            assert release.wait(10), "clear disk barrier released"
            clear(**kwargs)
        finally:
            finished.set()

    monkeypatch.setattr(app.app_ctx.transfer_journal, "clear_history", blocked_clear)
    async with app.run_test(size=(80, 24)) as pilot:
        try:
            await open_history(app, pilot)
            await pilot.click("#history-clear")
            await wait_until(
                lambda: type(app.screen).__name__ == "ConfirmModal", what="clear disk confirmation"
            )
            await pilot.press("right", "enter")
            await wait_until(entered.is_set, what="owned clear entered disk thread")
            assert any(
                worker.group == "transfer-history-operation" for worker in app.workers._workers
            )
            await pilot.press("escape")
            assert len(app.screen_stack) == 1
            shutdown = asyncio.create_task(app._aws_tui_shutdown())
            await wait_until(
                lambda: app.app_ctx.transfer_history_vm._closed,
                what="shutdown drains cancelled clear",
            )
            assert not shutdown.done()
            assert not finished.is_set()
        finally:
            release.set()
        await shutdown
        await wait_until(
            lambda: not app.workers._workers, what="owned clear Textual worker drained"
        )
        assert finished.is_set()
        assert all(thread != threading.get_ident() for thread, _ in calls)
        assert all(worker.group == "transfer-history-operation" for _, worker in calls)
        assert app.app_ctx.transfer_journal.load_history() == ()
        assert (source / NAME).read_bytes() == b"payload"
        assert not list(destination.iterdir())
        assert app._shutdown_errors == ()


async def test_active_overlay_history_preserves_copy_then_cancel_chip_stops_it(
    tmp_path, monkeypatch
):
    import asyncio

    from aws_tui.domain.local_fs import LocalFS
    from aws_tui.vm.messages import TransferState

    app, _, source, destination, _ = history_app(tmp_path, seeded=False)
    entered, release = asyncio.Event(), asyncio.Event()
    original = LocalFS.read_stream

    async def blocked_read(self, path, **kwargs):
        stream = await original(self, path, **kwargs)

        async def chunks():
            async for chunk in stream:
                yield chunk
                entered.set()
                await release.wait()

        return chunks()

    monkeypatch.setattr(LocalFS, "read_stream", blocked_read)
    async with app.run_test(size=(80, 24)) as pilot:
        try:
            await ordinary_copy(app, pilot, source, destination)
            await wait_until(
                entered.is_set, what="ordinary copy progress reached active stream barrier"
            )
            transfer = app.app_ctx.transfers_vm.transfers[0]
            assert transfer.state == TransferState.RUNNING
            assert transfer.model.bytes_done == 7
            await pilot.click("#transfer-history-open")
            await wait_until(
                lambda: type(app.screen).__name__ == "TransferHistoryModal",
                what="active overlay opens history",
            )
            await pilot.press("escape")
            assert transfer.state == TransferState.RUNNING
            await pilot.click("#cancel-btn")
            await wait_until(
                lambda: transfer.state == TransferState.CANCELLED,
                what="actual app cancel chip stops active provider copy",
            )
        finally:
            release.set()
        await drain_workers(app)
        assert not list(destination.iterdir())
        assert (source / NAME).read_bytes() == b"payload"
        assert app.app_ctx.transfer_history_vm.records[0].status == "cancelled"
        assert not app.app_ctx.transfer_history_vm.runtime.owned_ids


async def test_recovery_terminal_save_error_uses_live_outcome_and_separate_warning(
    tmp_path, monkeypatch
):
    from aws_tui.vm.messages import TransferState

    app, _, _, destination, _ = history_app(tmp_path)

    def fail(record):
        raise OSError("seeded-secret-NEVER-SHOW")

    monkeypatch.setattr(app.app_ctx.transfer_journal.history_store, "save", fail)
    async with app.run_test(size=(80, 24)) as pilot:
        modal = await open_history(app, pilot)
        await pilot.click("#history-retry")
        await wait_until(
            lambda: type(app.screen).__name__ == "RetryConflictModal",
            what="recovery history error fresh decision",
        )
        await pilot.press("enter")
        await drain_workers(app)
        assert (destination / NAME).read_bytes() == b"payload"
        assert app.app_ctx.transfers_vm.transfers[-1].state == TransferState.COMPLETED
        text = str(modal.query_one("#history-status").content)
        assert "New copy: completed." in text
        assert "Transfer history could not be saved." in text
        assert "seeded-secret-NEVER-SHOW" not in app.export_screenshot()
