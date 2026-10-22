"""Sync through the actual palette, native controls, confirmation and runtime."""

import asyncio
from html import unescape

import pytest
from textual.events import Paste
from textual.widgets import DataTable, Input, Select, TextArea

from aws_tui.app import AwsTuiApp
from aws_tui.domain.filesystem import PathRef, ProviderError
from aws_tui.domain.sync_plan import SyncComparison
from aws_tui.ui.widgets.confirm_modal import ConfirmModal
from aws_tui.ui.widgets.modal_button import ModalButton
from aws_tui.ui.widgets.sync import SyncModal
from tests.helpers import drain_workers, focus_and_settle, wait_until
from tests.integration.test_recursive_upload import button, select_folder
from tests.s3_versions_support import ReadGate
from tests.unit.vm.file_manager.test_sync_vm import SyncFS


async def setup(factory, *, files=2):
    fs = SyncFS()
    await fs.mkdir(PathRef(("bucket", "prefix")))
    ctx = factory(fs=fs)
    root = ctx.config_store.path.parent
    (root / "tree").mkdir()
    for index in range(files):
        (root / "tree" / f"{index}.txt").write_bytes(b"hello")
    return AwsTuiApp(ctx), fs, root


async def open_review(app, pilot):
    await pilot.press("ctrl+k", *"Sync folder to S3", "enter")
    await wait_until(lambda: isinstance(app.screen, SyncModal), what="sync review")
    modal = app.screen
    await wait_until(lambda: modal.vm.state == "ready", what="sync comparison")
    await wait_until(
        lambda: modal.query_one(DataTable).row_count == len(modal.vm.rows), what="sync rows"
    )
    return modal


@pytest.mark.parametrize("size", [(80, 24), (120, 40)])
async def test_palette_drop_operation_and_confirm_only_selected(
    app_context_factory, size, tmp_path
):
    app, fs, _ = await setup(app_context_factory)
    async with app.run_test(size=size) as pilot:
        await select_folder(app, pilot)
        modal = await open_review(app, pilot)
        text = modal.query_one(TextArea).text
        for value in ("SHA-256", "GET", "test", "us-east-1", "s3://bucket/prefix/tree/"):
            assert value in text
        assert not fs.writes
        await focus_and_settle(modal.query_one(DataTable))
        await pilot.press("space")
        await wait_until(lambda: modal.vm.selected_count == 1, what="operation omitted")
        assert modal.vm.remaining_count == 1
        await pilot.pause()
        for control in modal.query(ModalButton):
            assert control.region.intersection(modal.region) == control.region
        assert modal.query_one(DataTable).region.height >= 3
        screenshot = app.export_screenshot()
        (tmp_path / f"sync-{size[0]}.svg").write_text(screenshot)
        assert "Sync folder" in unescape(screenshot).replace("\xa0", " ")
        await button(app, pilot, "upload")
        await wait_until(lambda: isinstance(app.screen, ConfirmModal), what="sync confirmation")
        assert "1 objects" in " ".join(app.screen.request.body_lines)
        assert not fs.writes
        await pilot.press("enter")
        await wait_until(lambda: modal.vm.snapshot.finished, what="sync execution")
        assert len(fs.objects) == 1
        assert modal.vm.remaining_count == 0
        assert any(row.state == "omitted" for row in modal.vm.rows)
        await focus_and_settle(modal.query_one(DataTable))
        await pilot.press("enter")
        assert "Reason:" in modal.query_one(TextArea).text


@pytest.mark.parametrize("change", ["paste", "aba", "mode", "selection"])
async def test_native_edits_during_confirmation_retire_admission(app_context_factory, change):
    app, fs, _ = await setup(app_context_factory)
    async with app.run_test(size=(80, 24)) as pilot:
        await select_folder(app, pilot)
        modal = await open_review(app, pilot)
        await button(app, pilot, "upload")
        await wait_until(lambda: isinstance(app.screen, ConfirmModal), what="confirmation")
        if change in {"paste", "aba"}:
            field = modal.query_one("#sync-include", Input)
            old = field.value
            field.value = "0.txt"
            if change == "aba":
                field.value = old
        elif change == "mode":
            modal.query_one(Select).value = SyncComparison.SIZE_TIME.value
        else:
            modal.vm.toggle_operation(PathRef(("tree", "0.txt")))
        await pilot.press("enter")
        await drain_workers(app)
        assert not fs.writes
        assert not modal.vm.snapshot.finished


async def test_edited_filters_require_replan_and_freeze_selected_paths(app_context_factory):
    app, fs, root = await setup(app_context_factory)
    async with app.run_test(size=(80, 24)) as pilot:
        await select_folder(app, pilot)
        modal = await open_review(app, pilot)
        field = modal.query_one("#sync-include", Input)
        await focus_and_settle(field)
        await pilot.press("ctrl+shift+a", *"0.txt")
        assert field.value == "0.txt"
        assert modal.vm.plan is None
        assert modal.vm.state == "idle"
        await button(app, pilot, "review")
        await wait_until(lambda: modal.vm.state == "ready", what="filtered comparison")
        assert modal.vm.selected_count == 1
        assert modal.vm.settings.include == ("0.txt",)
        await button(app, pilot, "upload")
        await wait_until(lambda: isinstance(app.screen, ConfirmModal), what="confirmation")
        (root / "tree/new.txt").write_bytes(b"not in review")
        await pilot.press("enter")
        await wait_until(lambda: modal.vm.snapshot.finished, what="frozen run")
        assert PathRef(("bucket", "prefix", "tree", "new.txt")) not in fs.objects
        assert len(fs.writes) <= 1  # Root revision may reject the entire frozen run.


async def test_failed_retry_resolves_matching_file_with_zero_new_writes(app_context_factory):
    app, fs, _ = await setup(app_context_factory)
    key = PathRef(("bucket", "prefix", "tree", "0.txt"))
    fs.errors[key] = ProviderError("controlled failure")
    async with app.run_test(size=(80, 24)) as pilot:
        await select_folder(app, pilot)
        modal = await open_review(app, pilot)
        await button(app, pilot, "upload")
        await wait_until(lambda: isinstance(app.screen, ConfirmModal), what="confirmation")
        await pilot.press("enter")
        await wait_until(lambda: modal.vm.snapshot.finished, what="failed attempt")
        fs.errors.clear()
        fs.objects[key] = b"hello"
        count = len(fs.writes)
        await button(app, pilot, "review")
        await wait_until(lambda: modal.vm.state == "ready", what="fresh retry comparison")
        assert modal.vm.selected_count == modal.vm.remaining_count == 0
        assert not modal.vm.can_retry
        assert len(fs.writes) == count
        assert any(
            row.state == "unchanged" and row.outcome is not None and not row.outcome.published
            for row in modal.vm.rows
        )


async def test_running_cancel_reports_remaining_and_retries_only_unattempted(app_context_factory):
    app, fs, _ = await setup(app_context_factory, files=6)
    fs.hold = asyncio.Event()
    async with app.run_test(size=(80, 24)) as pilot:
        await select_folder(app, pilot)
        modal = await open_review(app, pilot)
        await button(app, pilot, "upload")
        await wait_until(lambda: isinstance(app.screen, ConfirmModal), what="confirmation")
        await pilot.press("enter")
        for _ in range(4):
            await asyncio.wait_for(fs.entered.get(), 3)
        await button(app, pilot, "cancel")
        fs.hold.set()
        await wait_until(lambda: modal.vm.snapshot.finished, what="cancellation drains")
        assert modal.vm.remaining_count == 6
        fs.hold = None
        await button(app, pilot, "review")
        await wait_until(lambda: modal.vm.state == "ready", what="unattempted retry")
        assert modal.vm.selected_count == 2
        await button(app, pilot, "upload")
        await wait_until(lambda: isinstance(app.screen, ConfirmModal), what="retry confirmation")
        await pilot.press("enter")
        await wait_until(lambda: modal.vm.snapshot.finished, what="retry drained")
        assert len(fs.objects) == 2
        assert modal.vm.remaining_count == 4
        assert not modal.vm.can_retry


async def test_noop_review_has_no_enabled_sync_or_writes(app_context_factory):
    app, fs, _ = await setup(app_context_factory)
    fs.objects = {
        PathRef(("bucket", "prefix", "tree", f"{index}.txt")): b"hello" for index in range(2)
    }
    async with app.run_test(size=(80, 24)) as pilot:
        await select_folder(app, pilot)
        modal = await open_review(app, pilot)
        assert modal.vm.selected_count == modal.vm.remaining_count == 0
        assert all(row.state == "unchanged" for row in modal.vm.rows)
        await wait_until(
            lambda: modal.query_one("#upload-upload", ModalButton).disabled,
            what="no-op sync disabled",
        )
        assert not fs.writes


async def test_native_paste_immediately_retires_before_submit_and_stale_message_is_ignored(
    app_context_factory,
):
    app, fs, _ = await setup(app_context_factory)
    async with app.run_test(size=(80, 24)) as pilot:
        await select_folder(app, pilot)
        modal = await open_review(app, pilot)
        field = modal.query_one("#sync-include", Input)
        previous = field.value
        field.action_select_all()
        field._on_paste(Paste("0.txt"))
        # No event-loop yield: Input.Changed is still queued, but admission is retired.
        assert modal.vm.plan is None
        modal._submit()
        await drain_workers(app)
        assert app.screen is modal
        assert not fs.writes
        await button(app, pilot, "review")
        await wait_until(lambda: modal.vm.state == "ready", what="fresh pasted filter")
        prepared = modal.vm.prepare()
        field.post_message(Input.Changed(field, previous))
        await pilot.pause()
        assert modal.vm.accepts(prepared)


async def test_review_uses_exact_configured_provider_mapping(app_context_factory):
    app, fs, _ = await setup(app_context_factory)
    fs.upload_location = lambda path: ("fixed", "configured/" + "/".join(path.segments))
    async with app.run_test(size=(80, 24)) as pilot:
        await select_folder(app, pilot)
        modal = await open_review(app, pilot)
        await pilot.pause()
        assert modal.vm.destination_label == "s3://fixed/configured/bucket/prefix/tree/"
        for row in modal.vm.plan.objects:
            assert (
                modal.query_one(DataTable).get_row(row.source.as_posix())[1].plain
                == row.location[1]
            )


async def test_cost_notice_is_painted_before_default_payload_comparison(app_context_factory):
    app, fs, _ = await setup(app_context_factory)
    fs.objects = {
        PathRef(("bucket", "prefix", "tree", f"{index}.txt")): b"hello" for index in range(2)
    }
    gate = ReadGate()
    original = fs.read_sync_checksum

    async def held_checksum(path, *, expected):
        await gate.wait()
        return await original(path, expected=expected)

    fs.read_sync_checksum = held_checksum
    async with app.run_test(size=(80, 24)) as pilot:
        await select_folder(app, pilot)
        await pilot.press("ctrl+k", *"Sync folder to S3", "enter")
        await wait_until(lambda: isinstance(app.screen, SyncModal), what="sync review mounted")
        try:
            await asyncio.wait_for(gate.entered.wait(), 3)
            await pilot.pause()
            painted = unescape(app.export_screenshot()).replace("\xa0", " ")
            assert "costs" in painted
        finally:
            gate.release.set()


@pytest.mark.parametrize("refused", [False, True])
async def test_unsupported_row_shows_exact_mapping_or_explicit_unmapped_path(
    app_context_factory, refused
):
    app, fs, root = await setup(app_context_factory)
    (root / "tree/link").symlink_to(root / "tree/0.txt")

    def mapping(path):
        if refused and path.name == "link":
            raise ProviderError("mapping refused")
        return "fixed", "configured/" + "/".join(path.segments)

    fs.upload_location = mapping
    async with app.run_test(size=(80, 24)) as pilot:
        await select_folder(app, pilot)
        modal = await open_review(app, pilot)
        await pilot.pause()
        key = modal.query_one(DataTable).get_row("/tree/link")[1].plain
        modal._inspect_row("/tree/link")
        if refused:
            assert key.startswith("Unmapped:")
            assert "mapping unavailable" in modal.query_one(TextArea).text
        else:
            assert key == "configured/bucket/prefix/tree/link"
            assert "s3://fixed/configured/bucket/prefix/tree/link" in modal.query_one(TextArea).text
        assert not fs.writes


@pytest.mark.parametrize("change", ["insert", "remove"])
async def test_toggle_cannot_target_a_new_positional_row_before_repaint(
    app_context_factory, change
):
    app, fs, root = await setup(app_context_factory)
    async with app.run_test(size=(80, 24)) as pilot:
        await select_folder(app, pilot)
        modal = await open_review(app, pilot)
        table = modal.query_one(DataTable)
        await focus_and_settle(table)
        old_identity = table.coordinate_to_cell_key(table.cursor_coordinate).row_key.value
        assert old_identity == "/tree/0.txt"
        if change == "insert":
            (root / "tree/-first.txt").write_bytes(b"new review only")
        else:
            (root / "tree/0.txt").unlink()
        observed = []

        def ready(_):
            if modal.vm.state != "ready" or observed:
                return
            # Exact ready-publication boundary, before the next periodic paint.
            observed.append(table.coordinate_to_cell_key(table.cursor_coordinate).row_key.value)
            modal.action_toggle_operation()

        subscription = modal.vm.on_property_changed.subscribe(ready)
        try:
            await modal.vm.load()
        finally:
            subscription.dispose()
        assert observed == [old_identity]
        assert modal.vm.review.omitted == ()
        assert not fs.writes
