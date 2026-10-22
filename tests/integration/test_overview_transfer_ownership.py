import pytest
from vmx import NULL_DISPATCHER, MessageHub

from aws_tui.domain.scoped_overview import WatchScope
from aws_tui.ui.widgets.transfers_overlay import TransferRowWidget, TransfersOverlay
from aws_tui.vm.file_manager.transfer_vm import TransferModel, TransferState
from aws_tui.vm.file_manager.transfers_vm import TransfersVM
from aws_tui.vm.messages import TransferCancelRequestedMessage
from aws_tui.vm.overview_transfers import project_session_transfers
from tests.helpers import drain_workers, wait_until
from tests.transfer_history_helpers import history_app


def model(name, state=TransferState.COMPLETED, direction="upload"):
    return TransferModel(name, direction, "synthetic-source", "synthetic-destination", 5, 5, state)


def test_projection_preserves_native_states_global_retention_and_original_models():
    hub = MessageHub()
    transfers = TransfersVM(hub=hub, dispatcher=NULL_DISPATCHER)
    transfers.construct()
    try:
        for i in range(105):
            direction = ("upload", "download", "local-copy", "s3-copy")[i % 4]
            transfers.register(model(str(i), direction=direction))
        for i, state in enumerate(
            (
                TransferState.PENDING,
                TransferState.RUNNING,
                TransferState.FAILED,
                TransferState.CANCELLED,
                TransferState.SKIPPED,
            )
        ):
            transfers.register(model("native-" + str(i), state))
        original = tuple(t.model for t in transfers.transfers)
        page = project_session_transfers(WatchScope("transfers", "session", "local", ""), transfers)
        assert len(transfers.finished) == 100
        assert len(page.entries) == 102
        assert {r.status for r in page.entries} >= {
            "pending",
            "running",
            "failed",
            "cancelled",
            "skipped",
            "completed",
        }
        assert all(r.times == () for r in page.entries)
        assert tuple(t.model for t in transfers.transfers) == original
        assert "across all connections" in page.cap
        assert "not durable history" in page.cap
    finally:
        transfers.dispose()


@pytest.mark.asyncio
async def test_reveal_refuses_transfer_evicted_during_awaited_rebuild(tmp_path, monkeypatch):
    import aws_tui.ui.widgets.transfers_overlay as overlay_module
    import aws_tui.vm.file_manager.transfers_vm as transfers_module

    monkeypatch.setattr(overlay_module, "_LINGER_SECONDS", 0)
    monkeypatch.setattr(transfers_module, "_MAX_FINISHED_TRANSFERS", 1)
    app, _, _, _, _ = history_app(tmp_path)
    async with app.run_test(size=(80, 24)):
        await drain_workers(app)
        ctx = app.app_ctx
        ctx.transfers_vm.register(model("chosen"))
        overlay = app.query_one(TransfersOverlay)
        await wait_until(lambda: "chosen" in overlay._expired_ids, what="chosen expired")
        await wait_until(lambda: not overlay.query(TransferRowWidget), what="expired hidden")
        rebuild = overlay._rebuild
        evicted = False

        async def rebuild_and_evict():
            nonlocal evicted
            await rebuild()
            if not evicted:
                evicted = True
                ctx.transfers_vm.register(model("replacement"))
                assert not any(t.id == "chosen" for t in ctx.transfers_vm.transfers)

        monkeypatch.setattr(overlay, "_rebuild", rebuild_and_evict)
        revealed = await overlay.reveal_session_transfer("chosen")
        assert not revealed, (
            "reveal returned success for a transfer evicted during its awaited rebuild"
        )
        assert not any(t.id == "chosen" for t in ctx.transfers_vm.transfers)


@pytest.mark.asyncio
async def test_inspected_active_row_never_cancels_on_enter_or_space_and_unpins_on_escape(
    tmp_path, monkeypatch
):
    import aws_tui.ui.widgets.transfers_overlay as overlay_module

    monkeypatch.setattr(overlay_module, "_LINGER_SECONDS", 0)
    app, _, _, _, _ = history_app(tmp_path)
    async with app.run_test(size=(80, 24)) as pilot:
        await drain_workers(app)
        ctx = app.app_ctx
        transfer = ctx.transfers_vm.register(model("running", TransferState.RUNNING))
        overlay = app.query_one(TransfersOverlay)
        cancels = []
        subscription = ctx.hub.messages.subscribe(
            lambda msg: (
                cancels.append(msg) if isinstance(msg, TransferCancelRequestedMessage) else None
            )
        )
        try:
            assert await overlay.reveal_session_transfer("running")
            await pilot.pause()
            assert isinstance(app.focused, TransferRowWidget)
            await pilot.press("enter", "space")
            await pilot.pause()
            assert not cancels
            assert transfer.state is TransferState.RUNNING
            ctx.transfers_vm.update(
                "running", bytes_done=5, bytes_total=5, state=TransferState.COMPLETED
            )
            await wait_until(
                lambda: "running" in overlay._expired_ids, what="inspected row expired"
            )
            assert any(r.transfer_vm is transfer for r in overlay.query(TransferRowWidget))
            await pilot.press("escape")
            await wait_until(
                lambda: not overlay.query(TransferRowWidget), what="inspection dismissed"
            )
            assert not cancels
        finally:
            subscription.dispose()
