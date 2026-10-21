"""Historical actions through the running app, palette, dialogs and transfer UI."""

from html import unescape

import pytest
from textual.widgets import DataTable, Input, TextArea

from aws_tui.app import AwsTuiApp
from aws_tui.domain.filesystem import NotFoundError, PathRef
from aws_tui.ui.widgets.confirm_modal import ConfirmModal
from aws_tui.ui.widgets.modal_button import ModalButton
from aws_tui.ui.widgets.pane import EntryRow, Pane
from tests.helpers import drain_workers, wait_until
from tests.s3_versions_support import VersionsFS


async def make_app(factory):
    fs = VersionsFS()
    await fs.mkdir(PathRef(("bucket", "prefix")))

    async def content():
        yield b"current"

    await fs.write_stream(PathRef(("bucket", "prefix", "a.txt")), content())
    ctx = factory(fs=fs)
    ctx.config_store.path.write_text(
        '[defaults]\nconnection = "test"\n\n[connections.test]\nkind = "s3-compatible"\nendpoint_url = "http://localhost:9000"\ncredentials = "static"\naccess_key_id = "k"\nsecret_access_key = "s"\nregion = "us-east-1"\n'
    )
    return AwsTuiApp(ctx), fs, ctx


async def select_file(app, pilot):
    await drain_workers(app)
    pane = app.query_one("#pane-left", Pane)
    await pane.vm.navigate_to(PathRef(("bucket", "prefix")))
    await wait_until(
        lambda: any(r.entry_vm.name == "a.txt" for r in pane.query(EntryRow)), what="file row"
    )
    pane.vm.move_cursor_to(
        next(i for i, row in enumerate(pane.vm.filtered_entries) if row.name == "a.txt")
    )
    app.set_focus(None)
    await pilot.pause()
    return pane


async def open_browser(app, pilot):
    from aws_tui.ui.widgets.s3_versions import S3VersionsModal

    await pilot.press("ctrl+k")
    await pilot.press(*"S3 object versions")
    await pilot.press("enter")
    await wait_until(lambda: isinstance(app.screen, S3VersionsModal), what="version browser")
    modal = app.screen
    await wait_until(lambda: modal.vm.state == "ready", what="exact history")
    return modal


async def choose_button(app, pilot, identity):
    for _ in range(14):
        if isinstance(app.focused, ModalButton) and app.focused.button_id == identity:
            await pilot.press("enter")
            return
        await pilot.press("tab")
    raise AssertionError(f"keyboard could not reach {identity}")


@pytest.mark.parametrize("size", [(80, 24), (120, 40)])
async def test_palette_keyboard_historical_inspection_and_download(app_context_factory, size):
    from aws_tui.ui.widgets.s3_versions import VersionDestinationModal

    app, fs, ctx = await make_app(app_context_factory)
    async with app.run_test(size=size) as pilot:
        await select_file(app, pilot)
        modal = await open_browser(app, pilot)
        assert modal.query_one(DataTable).row_count == 2
        assert modal.vm.selected.is_delete_marker
        await pilot.press("down", "enter")
        await wait_until(lambda: modal.vm.details is not None, what="historical metadata")
        text = modal.query_one(TextArea).text
        assert "old +/=" in text
        assert "Historical" in text
        assert "prefix/a.txt" in text
        await choose_button(app, pilot, "download")
        await wait_until(
            lambda: isinstance(app.screen, VersionDestinationModal), what="download filename"
        )
        await pilot.press("enter")
        await wait_until(lambda: isinstance(app.screen, ConfirmModal), what="download confirmation")
        assert "historical" in app.screen.request.title.lower()
        await pilot.press("enter")
        await wait_until(lambda: len(app.screen_stack) == 1, what="accepted browser dismissed")
        await drain_workers(app)
        assert (ctx.config_store.path.parent / "a.txt").read_bytes() == b"payload"
        assert fs.stream_closed == 1


async def test_exact_key_entry_reaches_deleted_key_without_current_row(app_context_factory):
    app, fs, _ = await make_app(app_context_factory)
    async with app.run_test(size=(80, 24)) as pilot:
        await select_file(app, pilot)
        modal = await open_browser(app, pilot)
        for _ in range(14):
            if isinstance(app.focused, Input) and app.focused.id == "s3-versions-key":
                break
            await pilot.press("tab")
        await pilot.press("home", "shift+end", "backspace", *"deleted//../key", "enter")
        await wait_until(
            lambda: (
                modal.vm.listing is not None and modal.vm.listing.key == "prefix/deleted//../key"
            ),
            what="exact deleted key",
        )
        assert fs.reads[-1] == ("list", PathRef(("bucket", "prefix", "deleted", "", "..", "key")))
        await pilot.press("escape")


@pytest.mark.parametrize("invalidate", [False, True])
async def test_new_key_recovery_confirmation_and_expiry(app_context_factory, invalidate):
    from aws_tui.ui.widgets.s3_versions import VersionDestinationModal

    app, fs, _ = await make_app(app_context_factory)
    async with app.run_test(size=(80, 24)) as pilot:
        await select_file(app, pilot)
        modal = await open_browser(app, pilot)
        await pilot.press("down")
        await choose_button(app, pilot, "recover")
        await wait_until(
            lambda: isinstance(app.screen, VersionDestinationModal), what="new key entry"
        )
        await pilot.press("enter")
        await wait_until(lambda: isinstance(app.screen, ConfirmModal), what="new key review")
        request = app.screen.request
        assert "new key" in request.title.lower()
        assert any("not replaced" in line for line in request.body_lines)
        if invalidate:
            modal.vm.set_key("another")
        await pilot.press("enter")
        await drain_workers(app)
        destination = PathRef(("bucket", "prefix", "a.txt.recovered"))
        if invalidate:
            with pytest.raises(NotFoundError):
                await fs.stat(destination)
        else:
            assert (await fs.stat(destination)).size == 7
            assert (await fs.stat(PathRef(("bucket", "prefix", "a.txt")))).size == 7


@pytest.mark.parametrize("stage", ["destination", "confirmation"])
async def test_source_change_while_covered_retires_browser_without_dismissing_confirmation(
    app_context_factory, stage
):
    from aws_tui.ui.widgets.s3_versions import VersionDestinationModal

    app, fs, ctx = await make_app(app_context_factory)
    async with app.run_test(size=(80, 24)) as pilot:
        pane = await select_file(app, pilot)
        modal = await open_browser(app, pilot)
        await pilot.press("down")
        await choose_button(app, pilot, "download")
        await wait_until(
            lambda: isinstance(app.screen, VersionDestinationModal), what="destination"
        )
        if stage == "confirmation":
            await pilot.press("enter")
            await wait_until(lambda: isinstance(app.screen, ConfirmModal), what="confirmation")
            covered = app.screen
        await pane.vm.navigate_to(PathRef(("bucket",)))
        if stage == "confirmation":
            assert app.screen is covered
            await pilot.press("enter")
        await wait_until(lambda: len(app.screen_stack) == 1, what="invalid browser removed")
        await drain_workers(app)
        assert modal.vm.state == "closed"
        assert not (ctx.config_store.path.parent / "a.txt").exists()
        assert not any(kind == "stream" for kind, _ in fs.reads)


@pytest.mark.parametrize("kind", ["download", "recovery"])
async def test_keyboard_cancel_drains_accepted_transfer_and_preserves_published_recovery(
    app_context_factory, kind
):
    import asyncio

    from aws_tui.ui.widgets.s3_versions import VersionDestinationModal
    from aws_tui.vm.file_manager.transfer_vm import TransferState
    from tests.integration.test_transfer_keyboard import open_controls
    from tests.s3_versions_support import ReadGate

    app, fs, ctx = await make_app(app_context_factory)
    gate = ReadGate()
    if kind == "download":
        fs.stream_gate = gate
    else:
        fs.publish_gate = gate
    async with app.run_test(size=(80, 24)) as pilot:
        await select_file(app, pilot)
        modal = await open_browser(app, pilot)
        await pilot.press("down")
        await choose_button(app, pilot, "download" if kind == "download" else "recover")
        await wait_until(
            lambda: isinstance(app.screen, VersionDestinationModal), what="destination"
        )
        await pilot.press("enter")
        await wait_until(lambda: isinstance(app.screen, ConfirmModal), what="confirmation")
        await pilot.press("enter")
        await asyncio.wait_for(gate.entered.wait(), 2)
        assert modal.vm.state == "closed"
        assert len(app.screen_stack) == 1
        transfer = next(
            t
            for t in ctx.transfers_vm.transfers
            if t.state in {TransferState.PENDING, TransferState.RUNNING}
        )
        await open_controls(app, ctx, pilot)
        await pilot.press("enter")
        await asyncio.wait_for(gate.cancelled.wait(), 2)
        await drain_workers(app)
        assert transfer.state is TransferState.CANCELLED
        assert fs.stream_closed == 1
        if kind == "download":
            assert not (ctx.config_store.path.parent / "a.txt").exists()
            assert not list(ctx.config_store.path.parent.glob(".aws-tui-*"))
        else:
            destination = PathRef(("bucket", "prefix", "a.txt.recovered"))
            assert (await fs.stat(destination)).size == 7
            # The warning must survive runtime cancellation and modal disposal.
            assert any(
                "published and preserved" in item.model.text
                for item in ctx.root_vm.chrome.toast_stack.toasts
            )
        await pilot.press("escape")


@pytest.mark.parametrize("size", [(80, 24), (120, 40)])
async def test_browser_painted_historical_labels_and_new_key_controls(
    app_context_factory, tmp_path, size
):
    app, _, _ = await make_app(app_context_factory)
    async with app.run_test(size=size) as pilot:
        await select_file(app, pilot)
        modal = await open_browser(app, pilot)
        await pilot.press("down")
        await pilot.pause()
        svg = app.export_screenshot()
        (tmp_path / f"versions-{size[0]}.svg").write_text(svg)
        rendered = unescape(svg).replace("\xa0", " ")
        assert "historical retrieval" in rendered
        assert "Recover NEW" in rendered
        assert "Download" in rendered
        assert modal.query_one(TextArea).region.height >= 4
        for identity in ["inspect", "download", "recover", "close"]:
            button = modal.query_one(f"#s3-versions-{identity}", ModalButton)
            assert button.region.intersection(modal.region) == button.region
        await pilot.press("escape")


@pytest.mark.parametrize("reload", [False, True])
async def test_completed_listing_preserves_newer_viewer_focus(app_context_factory, reload):
    from aws_tui.ui.widgets.s3_versions import S3VersionsModal
    from tests.s3_versions_support import ReadGate

    app, fs, _ = await make_app(app_context_factory)
    async with app.run_test(size=(80, 24)) as pilot:
        await select_file(app, pilot)
        if reload:
            modal = await open_browser(app, pilot)
            fs.list_gate = ReadGate()
            await pilot.click("#s3-versions-list")
        else:
            fs.list_gate = ReadGate()
            await pilot.press("ctrl+k", *"S3 object versions", "enter")
            await wait_until(lambda: isinstance(app.screen, S3VersionsModal), what="browser")
            modal = app.screen
        await wait_until(lambda: fs.list_gate.entered.is_set(), what="held listing")
        viewer = modal.query_one(TextArea)
        app.set_focus(viewer)
        await pilot.pause()
        assert modal.focused is viewer
        fs.list_gate.release.set()
        await wait_until(lambda: modal.vm.state == "ready", what="listing")
        await pilot.pause()
        assert modal.focused is viewer
        await pilot.press("escape")


async def test_stale_key_input_events_do_not_retire_newest_submitted_listing(app_context_factory):
    app, fs, _ = await make_app(app_context_factory)
    async with app.run_test(size=(80, 24)) as pilot:
        await select_file(app, pilot)
        modal = await open_browser(app, pilot)
        field = modal.query_one(Input)
        app.set_focus(field)
        assert modal.focused is field
        field.value = "older"
        field.value = "newest"
        modal.action_commit_focused()
        await wait_until(
            lambda: modal.vm.listing is not None and modal.vm.listing.key == "prefix/newest",
            what="newest submitted listing",
        )
        assert modal.vm.key == field.value == "newest"
        assert fs.reads[-1] == ("list", PathRef(("bucket", "prefix", "newest")))
        await pilot.press("escape")


async def test_native_paste_immediately_followed_by_enter_lists_visible_key(app_context_factory):
    from textual import events

    app, fs, _ = await make_app(app_context_factory)
    async with app.run_test(size=(80, 24)) as pilot:
        await select_file(app, pilot)
        modal = await open_browser(app, pilot)
        field = modal.query_one(Input)
        app.set_focus(field)
        await pilot.press("home", "shift+end")
        app._driver.process_message(events.Paste("newest"))
        app._driver.process_message(events.Key("enter", "\r"))
        await wait_until(
            lambda: modal.vm.listing is not None and modal.vm.listing.key == "prefix/newest",
            what="native pasted and submitted key",
        )
        assert field.value == modal.vm.key == "newest"
        assert fs.reads[-1] == ("list", PathRef(("bucket", "prefix", "newest")))
        await pilot.press("escape")


@pytest.mark.parametrize("covered", [False, True, "dismissed"])
async def test_shutdown_drains_resistant_browser_read_before_closing_clients(
    app_context_factory, covered
):
    import asyncio

    from tests.s3_versions_support import ReadGate

    app, fs, ctx = await make_app(app_context_factory)
    gate = fs.list_gate = ReadGate(resistant=True)
    observations = []
    original_close = ctx.aws_session.aclose_all_clients

    async def close_clients():
        observations.append(gate.release.is_set())
        await original_close()

    ctx.aws_session.aclose_all_clients = close_clients
    async with app.run_test(size=(80, 24)) as pilot:
        await select_file(app, pilot)
        await pilot.press("ctrl+k", *"S3 object versions", "enter")
        await wait_until(lambda: gate.entered.is_set(), what="held version read")
        if covered == "dismissed":
            # Do not await pilot.press while removal itself is draining.
            from textual import events

            app._driver.process_message(events.Key("escape", "\x1b"))
            await wait_until(lambda: len(app.screen_stack) == 1, what="browser popped")
        elif covered:
            from textual.screen import ModalScreen

            await app.push_screen(ModalScreen())
        shutdown = asyncio.create_task(app._aws_tui_shutdown())
        try:
            await wait_until(
                lambda: shutdown.done() or gate.cancelled.is_set(),
                what="shutdown reached version browser",
            )
            await asyncio.sleep(0.1)
            assert not shutdown.done(), observations
            assert not observations
        finally:
            gate.release.set()
            await shutdown
        assert observations == [True]


@pytest.mark.parametrize("mode", ["download", "recovery"])
async def test_native_destination_paste_then_enter_reviews_the_visible_destination(
    app_context_factory, mode
):
    from textual import events

    from aws_tui.ui.widgets.s3_versions import VersionDestinationModal

    app, _, _ = await make_app(app_context_factory)
    async with app.run_test(size=(80, 24)) as pilot:
        await select_file(app, pilot)
        await open_browser(app, pilot)
        await pilot.press("down")
        await choose_button(app, pilot, "download" if mode == "download" else "recover")
        await wait_until(
            lambda: isinstance(app.screen, VersionDestinationModal), what="destination"
        )
        await pilot.press("home", "shift+end")
        app._driver.process_message(events.Paste("chosen-key.txt"))
        app._driver.process_message(events.Key("enter", "\r"))
        await wait_until(lambda: isinstance(app.screen, ConfirmModal), what="destination review")
        assert app.screen.request.paths[-1].path.endswith("/chosen-key.txt")
        await pilot.press("escape", "escape")


async def test_queued_list_does_not_steal_focus_chosen_before_worker_dispatch(app_context_factory):
    app, fs, _ = await make_app(app_context_factory)
    async with app.run_test(size=(80, 24)) as pilot:
        await select_file(app, pilot)
        modal = await open_browser(app, pilot)
        app.set_focus(modal.query_one(Input))
        before = len(fs.reads)
        modal.action_commit_focused()
        viewer = modal.query_one(TextArea)
        app.set_focus(viewer)
        assert modal.focused is viewer
        await wait_until(lambda: len(fs.reads) > before, what="queued listing")
        await pilot.pause()
        assert modal.focused is viewer
        await pilot.press("escape")


async def test_repeated_browser_close_releases_retained_lifetime_owners(app_context_factory):
    app, _, _ = await make_app(app_context_factory)
    async with app.run_test(size=(80, 24)) as pilot:
        await select_file(app, pilot)
        for _ in range(4):
            await open_browser(app, pilot)
            assert len(app._version_browsers) == 1
            await pilot.press("escape")
            await wait_until(lambda: not app._version_browsers, what="browser fully drained")
            assert len(app.screen_stack) == 1


async def test_closing_pending_browser_restores_original_pane_after_drain(app_context_factory):
    import asyncio

    from textual import events

    from aws_tui.vm.chrome.focus_coordinator_vm import FocusSlot
    from tests.s3_versions_support import ReadGate

    app, fs, ctx = await make_app(app_context_factory)
    async with app.run_test(size=(80, 24)) as pilot:
        pane = await select_file(app, pilot)
        selected = pane.vm.selected_entry
        gate = fs.list_gate = ReadGate(resistant=True)
        await pilot.press("ctrl+k", *"S3 object versions", "enter")
        await wait_until(lambda: gate.entered.is_set(), what="held listing")
        app._driver.process_message(events.Key("escape", "\x1b"))
        await wait_until(lambda: len(app.screen_stack) == 1, what="browser popped")
        assert app._version_browsers
        await asyncio.sleep(0.1)
        gate.release.set()
        await wait_until(lambda: not app._version_browsers, what="browser drained")
        await wait_until(
            lambda: ctx.focus_coordinator.focused_slot is FocusSlot.S3_LEFT and app.focused is None,
            what="origin pane focus restored after modal refresh",
        )
        await pilot.pause()
        assert ctx.focus_coordinator.focused_slot is FocusSlot.S3_LEFT
        assert app.focused is None
        assert pane.vm.selected_entry is selected
        fs.list_gate = None
        await open_browser(app, pilot)
        await pilot.press("escape")
