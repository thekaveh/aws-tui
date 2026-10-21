"""Recursive upload through the real palette, review, confirmation and runtime."""

import asyncio
from html import unescape

import pytest
from textual.widgets import DataTable, Select, TextArea

from aws_tui.app import AwsTuiApp
from aws_tui.domain.cross_fs import ConflictResolution
from aws_tui.domain.filesystem import PathRef, ProviderError
from aws_tui.domain.upload_batch import UploadStatus
from aws_tui.ui.widgets.confirm_modal import ConfirmModal
from aws_tui.ui.widgets.dual_pane import DualPane
from aws_tui.ui.widgets.modal_button import ModalButton
from aws_tui.ui.widgets.pane import EntryRow, Pane
from aws_tui.vm.chrome.focus_coordinator_vm import FocusSlot
from tests.helpers import drain_workers, focus_and_settle, wait_until
from tests.unit.vm.file_manager.test_recursive_upload_vm import UploadFS


async def setup(factory):
    fs = UploadFS()
    await fs.mkdir(PathRef(("bucket", "prefix")))
    ctx = factory(fs=fs)
    root = ctx.config_store.path.parent
    (root / "tree").mkdir()
    (root / "tree" / "a.txt").write_bytes(b"hello")
    (root / "tree" / "nested").mkdir()
    (root / "tree" / "nested" / "b.txt").write_bytes(b"world")
    (root / "tree" / "empty").mkdir()
    (root / "tree" / "link").symlink_to(root / "tree" / "a.txt")
    return AwsTuiApp(ctx), fs, ctx


async def select_folder(app, pilot):
    await drain_workers(app)
    remote = app.query_one("#pane-left", Pane)
    await remote.vm.navigate_to(PathRef(("bucket", "prefix")))
    local = app.query_one("#pane-right", Pane)
    await wait_until(
        lambda: any(r.entry_vm.name == "tree" for r in local.query(EntryRow)),
        what="local folder row",
    )
    await pilot.press("tab")
    local.vm.move_cursor_to(
        next(i for i, row in enumerate(local.vm.filtered_entries) if row.name == "tree")
    )
    app._app_ctx.focus_coordinator.set_focused_slot(FocusSlot.S3_RIGHT)
    app.set_focus(None)
    await pilot.pause()
    return local, remote


async def open_review(app, pilot):
    from aws_tui.ui.widgets.recursive_upload import RecursiveUploadModal

    await pilot.press("ctrl+k", *"Upload folder to S3", "enter")
    await wait_until(lambda: isinstance(app.screen, RecursiveUploadModal), what="upload review")
    modal = app.screen
    await wait_until(lambda: modal.vm.state == "ready", what="read-only upload plan")
    await wait_until(
        lambda: modal.query_one(DataTable).row_count == len(modal.vm.snapshot.outcomes),
        what="projected upload rows",
    )
    return modal


async def button(app, pilot, identity):
    for _ in range(16):
        if isinstance(app.focused, ModalButton) and app.focused.button_id == identity:
            await pilot.press("enter")
            return
        await pilot.press("tab")
    raise AssertionError(f"unreachable upload button: {identity}")


@pytest.mark.parametrize("size", [(80, 24), (120, 40)])
async def test_actual_palette_review_confirmation_upload_and_snapshot(
    app_context_factory, size, tmp_path
):
    app, fs, _ = await setup(app_context_factory)
    async with app.run_test(size=size) as pilot:
        await select_folder(app, pilot)
        modal = await open_review(app, pilot)
        text = modal.query_one(TextArea).text
        for fragment in (
            "tree",
            "s3://bucket/prefix/tree/",
            "2 objects",
            "10 bytes",
            "empty",
            "link",
            "test",
            "us-east-1",
        ):
            assert fragment in text
        assert not fs.writes
        assert modal.query_one(DataTable).row_count == 2
        await pilot.pause()
        screenshot = app.export_screenshot()
        (tmp_path / f"recursive-upload-{size[0]}.svg").write_text(screenshot)
        svg = unescape(screenshot).replace("\xa0", " ")
        assert "Upload folder" in svg
        assert "Review" in svg
        assert modal.query_one(TextArea).region.height >= 4
        assert modal.query_one(DataTable).region.height >= 3
        for control in modal.query(ModalButton):
            assert control.region.intersection(modal.region) == control.region
        await button(app, pilot, "upload")
        await wait_until(lambda: isinstance(app.screen, ConfirmModal), what="explicit confirmation")
        assert "2 objects" in " ".join(app.screen.request.body_lines)
        assert not fs.writes
        await pilot.pause()
        for control in app.screen.query(ModalButton):
            assert control.region.intersection(app.screen.region) == control.region
        await pilot.press("enter")
        await wait_until(lambda: modal.vm.snapshot.finished, what="object uploads drain")
        assert modal.vm.snapshot.status == UploadStatus.SUCCEEDED
        assert len(fs.objects) == 2
        assert app.screen is modal
        await focus_and_settle(modal.query_one(DataTable))
        await pilot.press("enter")
        await wait_until(
            lambda: "succeeded" in modal.query_one(TextArea).text,
            what="full selected object outcome",
        )
        await pilot.press("escape")
        await wait_until(lambda: len(app.screen_stack) == 1, what="review dismissed")


@pytest.mark.parametrize("change", ["navigate", "unmount"])
async def test_confirmation_rejects_changed_destination_and_never_writes(
    app_context_factory, change
):
    app, fs, _ = await setup(app_context_factory)
    async with app.run_test(size=(80, 24)) as pilot:
        _, remote = await select_folder(app, pilot)
        modal = await open_review(app, pilot)
        await button(app, pilot, "upload")
        await wait_until(lambda: isinstance(app.screen, ConfirmModal), what="confirmation")
        if change == "navigate":
            await remote.vm.navigate_to(PathRef(("bucket",)))
        else:
            await app.query_one(DualPane).remove()
        await pilot.press("enter")
        await drain_workers(app)
        assert not fs.writes
        if change == "navigate":
            assert modal.vm.state == "closed"


async def test_policy_retry_is_subset_and_retains_previous_success(app_context_factory):
    app, fs, _ = await setup(app_context_factory)
    a = PathRef(("bucket", "prefix", "tree", "a.txt"))
    b = PathRef(("bucket", "prefix", "tree", "nested", "b.txt"))
    fs.errors[b] = ProviderError("denied")
    async with app.run_test(size=(80, 24)) as pilot:
        await select_folder(app, pilot)
        modal = await open_review(app, pilot)
        await button(app, pilot, "upload")
        await wait_until(lambda: isinstance(app.screen, ConfirmModal), what="confirmation")
        await pilot.press("enter")
        await wait_until(lambda: modal.vm.snapshot.finished, what="mixed result")
        assert modal.vm.snapshot.status == UploadStatus.FAILED
        fs.errors.clear()
        modal.query_one(Select).value = ConflictResolution.OVERWRITE.value
        await wait_until(
            lambda: (
                modal.vm.state == "ready" and modal.vm.plan.policy == ConflictResolution.OVERWRITE
            ),
            what="fresh retry policy",
        )
        assert len(modal.vm.plan.objects) == 1
        await button(app, pilot, "upload")
        await wait_until(lambda: isinstance(app.screen, ConfirmModal), what="retry confirmation")
        assert app.screen.request.danger
        await pilot.press("right", "enter")
        await wait_until(lambda: modal.vm.snapshot.finished, what="retry completed")
        assert modal.vm.snapshot.status == UploadStatus.SUCCEEDED
        assert len(modal.vm.snapshot.outcomes) == 2
        assert sum(path == a for path, _ in fs.writes) == 1


async def test_hide_active_review_keeps_app_owned_batch_and_shared_cancel(app_context_factory):
    app, fs, ctx = await setup(app_context_factory)
    fs.hold = asyncio.Event()
    async with app.run_test(size=(80, 24)) as pilot:
        await select_folder(app, pilot)
        await open_review(app, pilot)
        await button(app, pilot, "upload")
        await wait_until(lambda: isinstance(app.screen, ConfirmModal), what="confirmation")
        await pilot.press("enter")
        await asyncio.wait_for(fs.entered.get(), 2)
        await pilot.press("escape")
        await wait_until(lambda: len(app.screen_stack) == 1, what="hidden review")
        assert fs.active
        from aws_tui.vm.messages import TransferCancelRequestedMessage

        runtime = app._dual_pane().transfer_runtime
        ctx.hub.send(TransferCancelRequestedMessage(transfer_id=next(iter(runtime.owned_ids))))
        await wait_until(lambda: not runtime.owned_ids, what="shared cancellation drains")
        assert not fs.active
        assert not fs.objects


@pytest.mark.parametrize(
    ("steps", "policy"),
    [
        (1, ConflictResolution.SKIP),
        (2, ConflictResolution.RENAME),
        (3, ConflictResolution.OVERWRITE),
    ],
)
async def test_conflict_policy_is_keyboard_reachable_and_escape_only_closes_picker(
    app_context_factory, steps, policy
):
    app, fs, _ = await setup(app_context_factory)
    async with app.run_test(size=(80, 24)) as pilot:
        await select_folder(app, pilot)
        modal = await open_review(app, pilot)
        field = modal.query_one(Select)
        await focus_and_settle(field)
        await pilot.press("enter")
        assert field.expanded
        await pilot.press(*(["down"] * steps), "enter")
        await wait_until(
            lambda: modal.vm.state == "ready" and modal.vm.plan.policy == policy,
            what="keyboard conflict policy",
        )
        await wait_until(lambda: not field.disabled, what="enabled policy selector after planning")
        await focus_and_settle(field)
        await pilot.press("enter")
        assert field.expanded
        await pilot.press("escape")
        assert app.screen is modal
        assert not field.expanded
        assert not fs.writes


async def test_reviewed_rows_use_exact_provider_bucket_and_configured_prefix(app_context_factory):
    app, fs, _ = await setup(app_context_factory)
    fs.upload_location = lambda path: ("fixed", "configured/" + "/".join(path.segments))
    async with app.run_test(size=(80, 24)) as pilot:
        await select_folder(app, pilot)
        modal = await open_review(app, pilot)
        assert modal.vm.destination_label == "s3://fixed/configured/bucket/prefix/tree/"
        for row in modal.vm.plan.objects:
            assert (
                modal.query_one(DataTable).get_row(row.source.as_posix())[1].plain
                == row.location[1]
            )


async def test_direct_action_refuses_unmounted_panes_with_retained_view_model(app_context_factory):
    app, fs, _ = await setup(app_context_factory)
    async with app.run_test(size=(80, 24)) as pilot:
        await select_folder(app, pilot)
        assert app._folder_upload_origin() is not None
        await app.query_one(DualPane).remove()
        app.set_focus(None)
        app.action_upload_folder()
        await pilot.pause()
        assert len(app.screen_stack) == 1
        assert not fs.writes
