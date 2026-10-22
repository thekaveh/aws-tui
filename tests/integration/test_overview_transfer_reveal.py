import pytest

from aws_tui.ui.widgets.transfers_overlay import TransferRowWidget, TransfersOverlay
from aws_tui.vm.file_manager.transfer_vm import TransferModel, TransferState
from aws_tui.vm.messages import TransferCancelRequestedMessage
from tests.helpers import drain_workers, wait_until
from tests.transfer_history_helpers import history_app


@pytest.mark.parametrize("size", [(80, 24), (120, 40)])
async def test_exact_retained_expired_transfer_is_readable_until_escape(
    tmp_path, monkeypatch, size
):
    import aws_tui.ui.widgets.transfers_overlay as module

    monkeypatch.setattr(module, "_LINGER_SECONDS", 0)
    app, _, _, _, _ = history_app(tmp_path)
    async with app.run_test(size=size) as pilot:
        await drain_workers(app)
        ctx = app.app_ctx
        for name in ("first", "chosen"):
            ctx.transfers_vm.register(
                TransferModel(name, "upload", name, "s3://fixture", 5, 5, TransferState.COMPLETED)
            )
        overlay = app.query_one(TransfersOverlay)
        await wait_until(lambda: len(overlay._expired_ids) == 2, what="session rows expired")
        await wait_until(lambda: not overlay.query(TransferRowWidget), what="expired rows hidden")
        cancels = []
        subscription = ctx.hub.messages.subscribe(
            lambda message: (
                cancels.append(message)
                if isinstance(message, TransferCancelRequestedMessage)
                else None
            )
        )
        try:
            assert await overlay.reveal_session_transfer("chosen")
            await pilot.pause()
            assert isinstance(app.focused, TransferRowWidget)
            assert app.focused.transfer_vm.id == "chosen"
            assert [row.transfer_vm.id for row in overlay.query(TransferRowWidget)] == ["chosen"]
            await pilot.press("enter", "space")
            await pilot.pause()
            assert isinstance(app.focused, TransferRowWidget)
            assert cancels == []
            assert all(item.state is TransferState.COMPLETED for item in ctx.transfers_vm.transfers)
            focused = app.focused
            assert not await overlay.reveal_session_transfer("evicted")
            assert app.focused is focused
            await pilot.press("escape")
            await wait_until(
                lambda: not overlay.query(TransferRowWidget), what="inspection dismissed"
            )
            assert app.focused is not focused
        finally:
            subscription.dispose()
