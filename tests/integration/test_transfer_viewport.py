"""Compact real-app transfer cards stay inspectable and individually clickable."""

from __future__ import annotations

import pytest
from textual import events
from textual.widgets import Button, Static

from aws_tui.app import AwsTuiApp
from aws_tui.ui.widgets.transfer_history_modal import TransferHistoryModal
from aws_tui.ui.widgets.transfers_overlay import TransferRowWidget, TransfersOverlay
from aws_tui.vm.file_manager.transfer_vm import TransferModel, TransferState
from tests.helpers import drain_workers, wait_until
from tests.integration.test_copy_delete_actions import _use_injected_s3_connection
from tests.integration.test_keyboard_selection import seeded
from tests.integration.test_transfer_keyboard import open_controls


def _register(ctx, state, *, count=5):
    return [
        ctx.transfers_vm.register(
            TransferModel(
                id=f"viewport-{i}",
                direction="upload",
                source_label=f"file-{i}.bin",
                destination_label=f"s3://fixture/file-{i}.bin",
                bytes_done=0,
                bytes_total=100,
                state=state,
            )
        )
        for i in range(count)
    ]


def _chrome_is_visible(app, overlay):
    assert app.screen.region.contains_region(overlay.region)
    assert overlay.query_one("#transfers-overlay-title").content_region.height >= 1
    for control in [
        overlay.query_one("#transfers-overlay-title"),
        *overlay.query("#transfers-history-controls Button"),
    ]:
        assert overlay.content_region.contains_region(control.region)
        assert app.screen.region.contains_region(control.region)


@pytest.mark.parametrize("size", [(80, 24), (120, 40)])
@pytest.mark.parametrize("state", [TransferState.RUNNING, TransferState.PENDING])
async def test_every_card_is_inspectable_in_bounded_viewport(
    app_context_factory, tmp_path, size, state
):
    ctx = app_context_factory(fs=await seeded())
    _use_injected_s3_connection(ctx)
    app = AwsTuiApp(ctx)
    async with app.run_test(size=size) as pilot:
        await drain_workers(app)
        await pilot.pause()
        transfers = _register(ctx, state)
        await pilot.pause()
        overlay = app.query_one(TransfersOverlay)
        viewport = overlay.query_one("#transfers-overlay-inner")
        rows = list(overlay.query(TransferRowWidget))
        assert len(rows) == 5
        assert viewport.max_scroll_y > 0
        _chrome_is_visible(app, overlay)
        for index, row in enumerate(rows):
            viewport.scroll_to_widget(row, animate=False, top=True, force=True)
            await pilot.pause()
            assert viewport.content_region.contains_region(row.region), (
                index,
                row.region,
                viewport.content_region,
            )
            cancel = row.query_one(Button)
            assert viewport.content_region.contains_region(cancel.region)
            assert app.get_widget_at(*cancel.region.center)[0] is cancel
            assert str(index) in str(row.query_one(".transfer-name", Static).render())
        app.save_screenshot(str(tmp_path / f"transfers-{size[0]}-{state.value}.svg"))
        assert all(t.state is state for t in transfers)
        _chrome_is_visible(app, overlay)


@pytest.mark.parametrize("size", [(80, 24), (120, 40)])
@pytest.mark.parametrize("state", [TransferState.RUNNING, TransferState.PENDING])
async def test_wheel_and_pointer_cancel_fifth_transfer_only(app_context_factory, size, state):
    ctx = app_context_factory(fs=await seeded())
    _use_injected_s3_connection(ctx)
    app = AwsTuiApp(ctx)
    async with app.run_test(size=size) as pilot:
        await drain_workers(app)
        await pilot.pause()
        transfers = _register(ctx, state)
        await pilot.pause()
        overlay = app.query_one(TransfersOverlay)
        viewport = overlay.query_one("#transfers-overlay-inner")
        # Pilot 8.2 has no public wheel helper. Its version-matched mouse
        # injector delivers physical scroll events through screen hit testing.
        point = (viewport.content_region.x + 2, viewport.content_region.y + 1)
        await pilot._post_mouse_events([events.MouseScrollDown], offset=point, times=12)
        await pilot.pause()
        assert viewport.scroll_y > 0
        row = next(
            row for row in overlay.query(TransferRowWidget) if row.transfer_vm is transfers[4]
        )
        cancel = row.query_one(Button)
        assert viewport.content_region.contains_region(cancel.region)
        assert await pilot.click(cancel, offset=(2, 0))
        await wait_until(
            lambda: transfers[4].state is TransferState.CANCELLED,
            what="fifth pointer-selected transfer cancellation",
        )
        assert [t.state for t in transfers[:4]] == [state] * 4
        _chrome_is_visible(app, overlay)


@pytest.mark.parametrize(("initial", "target"), [((120, 40), (80, 24)), ((80, 24), (120, 40))])
async def test_resize_keeps_focused_fifth_transfer_and_scroll_position(
    app_context_factory, initial, target
):
    ctx = app_context_factory(fs=await seeded())
    _use_injected_s3_connection(ctx)
    app = AwsTuiApp(ctx)
    async with app.run_test(size=initial) as pilot:
        await drain_workers(app)
        await pilot.pause()
        transfers = _register(ctx, TransferState.RUNNING)
        await open_controls(app, ctx, pilot)
        await pilot.press("tab", "tab", "tab", "tab")
        await pilot.pause()
        overlay = app.query_one(TransfersOverlay)
        viewport = overlay.query_one("#transfers-overlay-inner")
        button = app.focused
        row = next(
            node for node in button.ancestors_with_self if isinstance(node, TransferRowWidget)
        )
        assert row.transfer_vm is transfers[4]
        assert viewport.content_region.contains_region(button.region)
        assert viewport.scroll_y > 0
        for size in [target, initial]:
            await pilot.resize_terminal(*size)
            await pilot.pause()
            assert app.focused is button
            assert row.transfer_vm is transfers[4]
            assert viewport.content_region.contains_region(button.region)
            assert viewport.scroll_y > 0
            _chrome_is_visible(app, overlay)
        await pilot.press("space")
        await wait_until(
            lambda: transfers[4].state is TransferState.CANCELLED,
            what="same transfer cancels after resize",
        )
        assert [t.state for t in transfers[:4]] == [TransferState.RUNNING] * 4


@pytest.mark.parametrize("size", [(80, 24), (120, 40)])
async def test_single_card_keeps_compact_intrinsic_height(app_context_factory, size):
    ctx = app_context_factory(fs=await seeded())
    _use_injected_s3_connection(ctx)
    app = AwsTuiApp(ctx)
    async with app.run_test(size=size) as pilot:
        await drain_workers(app)
        await pilot.pause()
        ctx.transfers_vm.register(
            TransferModel(
                id="one",
                direction="upload",
                source_label="one.bin",
                destination_label="s3://fixture/one.bin",
                bytes_done=0,
                bytes_total=100,
                state=TransferState.RUNNING,
            )
        )
        await pilot.pause()
        overlay = app.query_one(TransfersOverlay)
        viewport = overlay.query_one("#transfers-overlay-inner")
        row = overlay.query_one(TransferRowWidget)
        assert viewport.content_region.height == row.region.height
        assert viewport.max_scroll_y == 0
        _chrome_is_visible(app, overlay)


@pytest.mark.parametrize("size", [(80, 24), (120, 40)])
async def test_history_only_overlay_shrinks_after_last_card_expires(tmp_path, monkeypatch, size):
    import aws_tui.ui.widgets.transfers_overlay as module
    from tests.transfer_history_helpers import history_app

    app, _, _, _, _ = history_app(tmp_path)
    monkeypatch.setattr(module, "_LINGER_SECONDS", 0)
    async with app.run_test(size=size) as pilot:
        await drain_workers(app)
        app.app_ctx.transfers_vm.register(
            TransferModel(
                id="finished",
                direction="upload",
                source_label="finished.bin",
                destination_label="s3://fixture/finished.bin",
                bytes_done=100,
                bytes_total=100,
                state=TransferState.COMPLETED,
            )
        )
        overlay = app.query_one(TransfersOverlay)
        await wait_until(
            lambda: "finished" in overlay._expired_ids, what="finished transfer linger expires"
        )
        await wait_until(lambda: not overlay.query(TransferRowWidget), what="last card removed")
        await pilot.pause()
        assert not overlay.has_class("-hidden")
        viewport = overlay.query_one("#transfers-overlay-inner")
        assert viewport.content_region.height <= 1
        assert viewport.max_scroll_y == 0
        _chrome_is_visible(app, overlay)
        assert await pilot.click("#transfer-history-open")
        assert isinstance(app.screen, TransferHistoryModal)
        await pilot.press("escape")
        assert await pilot.click("#transfer-recovery-open")
        assert isinstance(app.screen, TransferHistoryModal)
        await pilot.press("escape")
        app.app_ctx.transfers_vm.register(
            TransferModel(
                id="new-active",
                direction="upload",
                source_label="new.bin",
                destination_label="s3://fixture/new.bin",
                bytes_done=0,
                bytes_total=100,
                state=TransferState.RUNNING,
            )
        )
        await pilot.pause()
        assert viewport.content_region.height == 5
        cancel = overlay.query_one(TransferRowWidget).query_one(Button)
        assert viewport.content_region.contains_region(cancel.region)
        assert app.get_widget_at(*cancel.region.center)[0] is cancel
        _chrome_is_visible(app, overlay)


@pytest.mark.parametrize("size", [(80, 24), (120, 40)])
async def test_progress_preserves_manual_scroll_with_focused_later_control(
    app_context_factory, size
):
    ctx = app_context_factory(fs=await seeded())
    _use_injected_s3_connection(ctx)
    app = AwsTuiApp(ctx)
    async with app.run_test(size=size) as pilot:
        await drain_workers(app)
        await pilot.pause()
        transfers = _register(ctx, TransferState.RUNNING)
        await open_controls(app, ctx, pilot)
        await pilot.press("tab", "tab", "tab", "tab")
        await pilot.pause()
        overlay = app.query_one(TransfersOverlay)
        viewport = overlay.query_one("#transfers-overlay-inner")
        button = app.focused
        assert viewport.scroll_y > 0
        point = (viewport.content_region.x + 2, viewport.content_region.y + 1)
        await pilot._post_mouse_events([events.MouseScrollUp], offset=point, times=12)
        await pilot.pause()
        assert viewport.scroll_y == 0
        assert app.focused is button
        for amount in range(1, 21):
            ctx.transfers_vm.update(
                transfers[4].id, bytes_done=amount, bytes_total=100, state=TransferState.RUNNING
            )
        await pilot.pause()
        assert viewport.scroll_y == 0
        assert app.focused is button


@pytest.mark.parametrize(("size", "expected_height"), [((80, 24), 8), ((120, 40), 18)])
async def test_growing_and_expiring_batch_remeasures_after_rows_are_laid_out(
    app_context_factory, monkeypatch, size, expected_height
):
    import aws_tui.ui.widgets.transfers_overlay as module

    monkeypatch.setattr(module, "_LINGER_SECONDS", 0)
    ctx = app_context_factory(fs=await seeded())
    _use_injected_s3_connection(ctx)
    app = AwsTuiApp(ctx)
    async with app.run_test(size=size) as pilot:
        await drain_workers(app)
        await pilot.pause()
        _register(ctx, TransferState.RUNNING, count=1)
        await pilot.pause()
        overlay = app.query_one(TransfersOverlay)
        viewport = overlay.query_one("#transfers-overlay-inner")
        assert viewport.content_region.height == 5
        transfers = _register(ctx, TransferState.RUNNING)
        await pilot.pause()
        await pilot.pause()
        assert viewport.content_region.height == expected_height
        await open_controls(app, ctx, pilot)
        await pilot.press("tab", "tab", "tab", "tab")
        await pilot.pause()
        button = app.focused
        point = (viewport.content_region.x + 2, viewport.content_region.y + 1)
        await pilot._post_mouse_events([events.MouseScrollUp], offset=point, times=12)
        await pilot.pause()
        assert viewport.scroll_y == 0
        for amount in range(1, 101):
            ctx.transfers_vm.update(
                transfers[0].id, bytes_done=amount, bytes_total=100, state=TransferState.RUNNING
            )
        await pilot.pause()
        assert viewport.scroll_y == 0
        assert app.focused is button
        assert viewport.content_region.height == expected_height
        for transfer in transfers[:4]:
            ctx.transfers_vm.update(
                transfer.id, bytes_done=100, bytes_total=100, state=TransferState.COMPLETED
            )
        await wait_until(
            lambda: len(overlay.query(TransferRowWidget)) == 1, what="earlier cards expire"
        )
        await pilot.pause()
        assert viewport.content_region.height == 5
        assert app.focused is button
        assert viewport.content_region.contains_region(button.region)
        _chrome_is_visible(app, overlay)
