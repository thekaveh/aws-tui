"""Active transfers must be cancellable through the real application's keys."""

from __future__ import annotations

import pytest
from textual.widgets import Button

from aws_tui.app import AwsTuiApp
from aws_tui.domain.filesystem import PathRef
from aws_tui.ui.widgets.transfers_overlay import TransferRowWidget, TransfersOverlay
from aws_tui.vm.chrome.focus_coordinator_vm import FocusSlot
from aws_tui.vm.file_manager.transfer_vm import TransferModel, TransferState
from aws_tui.vm.messages import TransferCancelRequestedMessage
from tests.helpers import drain_workers, wait_until
from tests.integration.test_copy_delete_actions import _use_injected_s3_connection
from tests.integration.test_keyboard_selection import seeded


def register_transfers(ctx, count=5):
    return [
        ctx.transfers_vm.register(
            TransferModel(
                id=f"transfer-{i}",
                direction="upload",
                source_label=f"file-{i}",
                destination_label=f"s3://fixture/file-{i}",
                bytes_done=10,
                bytes_total=100,
                state=TransferState.RUNNING,
            )
        )
        for i in range(count)
    ]


async def open_controls(app, ctx, pilot):
    await pilot.press("ctrl+k", *"Focus active transfers")
    await pilot.pause()
    assert [row.id for row in ctx.command_palette_vm.filtered_entries] == ["app.focus_transfers"]
    await pilot.press("enter")
    await wait_until(
        lambda: isinstance(app.focused, Button) and app.focused.has_class("transfer-cancel"),
        what="keyboard enters active transfer controls",
    )


@pytest.mark.parametrize("key", ["enter", "space"])
async def test_physical_palette_tab_and_cancel_keys_target_one_transfer(app_context_factory, key):
    fs = await seeded()
    await fs.mkdir(PathRef(("folder",)))
    ctx = app_context_factory(fs=fs)
    _use_injected_s3_connection(ctx)
    app = AwsTuiApp(ctx)
    async with app.run_test(size=(120, 40)) as pilot:
        await drain_workers(app)
        await pilot.pause()
        dual = ctx.root_vm.content_host.current
        source = dual.focused_pane
        assert source.selected_entry.name == "folder"
        original_path, original_cursor = source.path, source.cursor_index
        transfers = register_transfers(ctx)
        requests = []
        subscription = ctx.hub.messages.subscribe(
            lambda msg: (
                requests.append(msg.transfer_id)
                if isinstance(msg, TransferCancelRequestedMessage)
                else None
            )
        )
        try:
            await open_controls(app, ctx, pilot)
            await pilot.press("tab")
            await pilot.pause()
            owner = next(
                node
                for node in app.focused.ancestors_with_self
                if isinstance(node, TransferRowWidget)
            )
            assert owner.transfer_vm is transfers[1]
            await pilot.press(key)
            await wait_until(
                lambda: transfers[1].state is TransferState.CANCELLED,
                what="chosen transfer receives cancellation",
            )
            await pilot.pause()
            assert requests == [transfers[1].id]
            assert [t.state for t in transfers] == [
                TransferState.RUNNING,
                TransferState.CANCELLED,
                TransferState.RUNNING,
                TransferState.RUNNING,
                TransferState.RUNNING,
            ]
            assert source.path == original_path
            assert source.cursor_index == original_cursor
            assert not [call for call in fs.calls if call[0] == "delete"]
            await pilot.press("escape")
            await pilot.pause()
            assert app.focused is None
            assert ctx.focus_coordinator.focused_slot is FocusSlot.S3_LEFT
            await pilot.press("tab")
            assert ctx.focus_coordinator.focused_slot is FocusSlot.S3_RIGHT
        finally:
            subscription.dispose()


@pytest.mark.parametrize("key", ["enter", "space"])
async def test_cancel_button_activation_is_contained_in_actual_app(app_context_factory, key):
    fs = await seeded()
    await fs.mkdir(PathRef(("folder",)))
    ctx = app_context_factory(fs=fs)
    _use_injected_s3_connection(ctx)
    app = AwsTuiApp(ctx)
    async with app.run_test(size=(120, 40)) as pilot:
        await drain_workers(app)
        await pilot.pause()
        pane = app._focused_file_pane()
        transfers = register_transfers(ctx, 2)
        await pilot.pause()
        row = next(row for row in app.query(TransferRowWidget) if row.transfer_vm is transfers[1])
        row.query_one(Button).focus()
        await pilot.press(key)
        await pilot.pause()
        assert transfers[1].state is TransferState.CANCELLED
        assert transfers[0].state is TransferState.RUNNING
        assert pane.path == PathRef(())
        assert pane.selected_entry.name == "folder"


async def test_keyboard_cancel_stops_actual_controlled_copy_worker(app_context_factory):
    import asyncio

    from tests.integration.test_copy_delete_actions import _BlockingReadFS, _stream

    fs = _BlockingReadFS()
    await fs.write_stream(PathRef(("alpha.txt",)), _stream(b"original"))
    ctx = app_context_factory(fs=fs)
    _use_injected_s3_connection(ctx)
    app = AwsTuiApp(ctx)
    async with app.run_test(size=(120, 40)) as pilot:
        await drain_workers(app)
        await pilot.pause()
        await pilot.press("c", "enter")
        await asyncio.wait_for(fs.read_started.wait(), 2)
        target = next(
            t
            for t in ctx.transfers_vm.transfers
            if t.state in {TransferState.PENDING, TransferState.RUNNING}
        )
        spectator = register_transfers(ctx, 1)[0]
        await open_controls(app, ctx, pilot)
        await pilot.press("enter")
        await asyncio.wait_for(fs.read_cancelled.wait(), 2)
        await wait_until(
            lambda: target.state is TransferState.CANCELLED, what="real transfer cancels"
        )
        assert spectator.state is TransferState.RUNNING
        destination = ctx.root_vm.content_host.current.other_pane
        assert not any(
            entry.name == "alpha.txt" for entry in await destination.provider.list(destination.path)
        )
        await pilot.press("escape")
        assert app.focused is None


async def test_retired_control_does_not_redirect_repeated_activation(app_context_factory):
    fs = await seeded()
    await fs.mkdir(PathRef(("folder",)))
    ctx = app_context_factory(fs=fs)
    _use_injected_s3_connection(ctx)
    app = AwsTuiApp(ctx)
    async with app.run_test(size=(120, 40)) as pilot:
        await drain_workers(app)
        await pilot.pause()
        transfers = register_transfers(ctx, 2)
        pane = app._focused_file_pane()
        await open_controls(app, ctx, pilot)
        await pilot.press("enter")
        await wait_until(
            lambda: transfers[0].state is TransferState.CANCELLED, what="first control retires"
        )
        await pilot.pause()
        assert isinstance(app.focused, TransfersOverlay)
        await pilot.press("enter", "space", "enter")
        await pilot.pause()
        assert transfers[1].state is TransferState.RUNNING
        assert pane.path == PathRef(())
        assert pane.selected_entry.name == "folder"
        await pilot.press("tab")
        await pilot.pause()
        owner = next(
            node for node in app.focused.ancestors_with_self if isinstance(node, TransferRowWidget)
        )
        assert owner.transfer_vm is transfers[1]
        await pilot.press("shift+tab")  # first enabled control exits backward
        await pilot.pause()
        assert app.focused is None
        assert ctx.focus_coordinator.focused_slot is FocusSlot.S3_LEFT


async def test_transfer_command_obeys_custom_binding_and_discovery(app_context_factory):
    from aws_tui.infra.keymap_store import KeymapStore

    ctx = app_context_factory(fs=await seeded())
    _use_injected_s3_connection(ctx)
    ctx.keymap_store = KeymapStore(overlay={"app.focus_transfers": "f8"})
    app = AwsTuiApp(ctx)
    async with app.run_test(size=(120, 40)) as pilot:
        await drain_workers(app)
        await pilot.pause()
        register_transfers(ctx, 1)
        await pilot.pause()
        row = next(
            row
            for row in app._project_discovery_actions(app._capture_discovery_origin())
            if row.id == "app.focus_transfers"
        )
        assert row.effective_keys == ("f8",)
        await pilot.press("f8")
        await pilot.pause()
        assert isinstance(app.focused, Button)
        assert app.focused.has_class("transfer-cancel")
        await pilot.press("escape")
        assert app.focused is None


async def test_palette_removes_transfer_focus_command_after_last_completion(app_context_factory):
    ctx = app_context_factory(fs=await seeded())
    _use_injected_s3_connection(ctx)
    app = AwsTuiApp(ctx)
    async with app.run_test(size=(120, 40)) as pilot:
        await drain_workers(app)
        await pilot.pause()
        row = next(
            row
            for row in app._project_discovery_actions(app._capture_discovery_origin())
            if row.id == "app.focus_transfers"
        )
        assert not row.available
        target = register_transfers(ctx, 1)[0]
        await pilot.press("ctrl+k", *"Focus active transfers")
        await pilot.pause()
        assert [row.id for row in ctx.command_palette_vm.filtered_entries] == [
            "app.focus_transfers"
        ]
        ctx.transfers_vm.update(
            target.id, bytes_done=100, bytes_total=100, state=TransferState.COMPLETED
        )
        await pilot.pause()
        assert ctx.command_palette_vm.filtered_entries == ()
        await pilot.press("enter", "escape")
        await pilot.pause()
        assert not isinstance(app.focused, TransfersOverlay)


async def test_finished_row_expiry_restores_service_focus(app_context_factory, monkeypatch):
    import aws_tui.ui.widgets.transfers_overlay as module

    ctx = app_context_factory(fs=await seeded())
    _use_injected_s3_connection(ctx)
    app = AwsTuiApp(ctx)
    monkeypatch.setattr(module, "_LINGER_SECONDS", 0)
    async with app.run_test(size=(120, 40)) as pilot:
        await drain_workers(app)
        await pilot.pause()
        target = register_transfers(ctx, 1)[0]
        await open_controls(app, ctx, pilot)
        ctx.transfers_vm.update(
            target.id, bytes_done=100, bytes_total=100, state=TransferState.COMPLETED
        )
        overlay = app.query_one(TransfersOverlay)
        await wait_until(lambda: overlay.has_class("-hidden"), what="finished row expires")
        await pilot.pause()
        assert app.focused is None
        assert ctx.focus_coordinator.focused_slot is FocusSlot.S3_LEFT


async def test_progress_refreshes_discovery_only_when_transfer_availability_changes(
    app_context_factory, monkeypatch
):
    ctx = app_context_factory(fs=await seeded())
    _use_injected_s3_connection(ctx)
    app = AwsTuiApp(ctx)
    async with app.run_test(size=(120, 40)) as pilot:
        await drain_workers(app)
        await pilot.pause()
        refreshes = []
        original = app._refresh_discovery_surfaces

        def refresh():
            refreshes.append(True)
            original()

        monkeypatch.setattr(app, "_refresh_discovery_surfaces", refresh)
        target = register_transfers(ctx, 1)[0]
        assert refreshes == [True]
        refreshes.clear()
        for done in range(11, 31):
            ctx.transfers_vm.update(
                target.id, bytes_done=done, bytes_total=100, state=TransferState.RUNNING
            )
        assert refreshes == []
        ctx.transfers_vm.update(
            target.id, bytes_done=100, bytes_total=100, state=TransferState.COMPLETED
        )
        assert refreshes == [True]


@pytest.mark.parametrize("settled", [False, True])
async def test_completion_during_focus_change_cannot_activate_underlying_folder(
    app_context_factory, settled
):
    fs = await seeded()
    await fs.mkdir(PathRef(("folder",)))
    ctx = app_context_factory(fs=fs)
    _use_injected_s3_connection(ctx)
    app = AwsTuiApp(ctx)
    async with app.run_test(size=(120, 40)) as pilot:
        await drain_workers(app)
        await pilot.pause()
        pane = app._focused_file_pane()
        transfers = register_transfers(ctx, 2)
        await pilot.pause()
        row = next(row for row in app.query(TransferRowWidget) if row.transfer_vm is transfers[0])
        button = row.query_one(Button)
        app.set_focus(button)
        if settled:
            await pilot.pause()
            assert button.has_focus
        else:
            assert app.focused is button
            assert not button.has_focus
        ctx.transfers_vm.update(
            transfers[0].id, bytes_done=100, bytes_total=100, state=TransferState.COMPLETED
        )
        if settled:
            # A queued global key can dispatch before deferred Widget.focus().
            await app.action_descend()
        else:
            await pilot.press("enter")
        await drain_workers(app)
        await pilot.pause()
        assert pane.path == PathRef(())
        assert pane.selected_entry.name == "folder"
        assert transfers[1].state is TransferState.RUNNING
        assert isinstance(app.focused, TransfersOverlay)
        await pilot.press("escape")
        assert app.focused is None


async def test_background_completion_keeps_active_modal_focus(app_context_factory):
    from aws_tui.ui.widgets.help_modal import HelpModal

    ctx = app_context_factory(fs=await seeded())
    _use_injected_s3_connection(ctx)
    app = AwsTuiApp(ctx)
    async with app.run_test(size=(120, 40)) as pilot:
        await drain_workers(app)
        await pilot.pause()
        target = register_transfers(ctx, 1)[0]
        await open_controls(app, ctx, pilot)
        await pilot.press("question_mark")
        await wait_until(
            lambda: isinstance(app.screen, HelpModal), what="Help above transfer controls"
        )
        modal, focused = app.screen, app.focused
        ctx.transfers_vm.update(
            target.id, bytes_done=100, bytes_total=100, state=TransferState.COMPLETED
        )
        await pilot.pause()
        assert app.screen is modal
        assert app.focused is focused
        assert ctx.focus_coordinator.is_modal
        await pilot.press("escape")
        await pilot.pause()
        assert len(app.screen_stack) == 1
