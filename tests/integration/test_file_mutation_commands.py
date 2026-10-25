"""New file commands exercise actual dispatch, reviews and provider effects."""

from __future__ import annotations

import pytest
from textual.widgets import Input

from aws_tui.app import AwsTuiApp
from aws_tui.domain.filesystem import PathRef
from aws_tui.infra.keymap_store import KeymapStore
from aws_tui.ui.widgets.confirm_modal import ConfirmModal
from tests.helpers import drain_workers, wait_until
from tests.integration.test_copy_delete_actions import _use_injected_s3_connection
from tests.integration.test_keyboard_selection import RecordingFS, seeded


async def ready(app):
    await drain_workers(app)
    return app._focused_file_pane()


async def open_action(app, pilot, ctx, operation, route):
    action, key, title = {
        "move": ("pane.move", "m", "Move or rename selected entries"),
        "new": ("pane.new", "n", "Create a new folder"),
    }[operation]
    if route == "palette":
        await pilot.press("colon", *title)
        await pilot.pause()
        assert [e.id for e in ctx.command_palette_vm.filtered_entries] == [action]
        await pilot.press("enter")
    elif route == "registry":
        await app._actions.invoke(action)
    else:
        await pilot.press("x" if route == "remap" else key)
    await wait_until(lambda: len(app.screen_stack) == 2, timeout=2, what="file intent modal")
    await pilot.pause()


@pytest.mark.parametrize("route", ["key", "remap", "registry", "palette"])
@pytest.mark.parametrize("operation", ["move", "new"])
async def test_command_routes_mutate_only_after_review(app_context_factory, operation, route):
    fs = await seeded()
    ctx = app_context_factory(fs=fs)
    _use_injected_s3_connection(ctx)
    if route == "remap":
        ctx.keymap_store = KeymapStore(overlay={f"pane.{operation}": "x", "emr.cancel": "z"})
    app = AwsTuiApp(ctx)
    async with app.run_test(size=(120, 40)) as pilot:
        pane = await ready(app)
        destination = RecordingFS()
        await ctx.root_vm.content_host.current.right.swap_provider(destination)
        await pilot.pause()
        await open_action(app, pilot, ctx, operation, route)
        if operation == "move":
            assert "alpha.txt" in {e.name for e in await fs.list(PathRef(()))}
            assert not await destination.list(PathRef(()))
            assert await pilot.click("#file-operation-move")
        else:
            app.screen.query_one(Input).value = "日本語 folder"
            await pilot.press("enter")
            await wait_until(lambda: isinstance(app.screen, ConfirmModal), what="folder review")
            assert "日本語 folder" not in {e.name for e in await fs.list(PathRef(()))}
            await pilot.press("enter")
        await drain_workers(app)
        await pilot.pause()
        source_names = {e.name for e in await fs.list(PathRef(()))}
        if operation == "move":
            assert source_names == {"beta.txt", "gamma.txt"}
            assert {e.name for e in await destination.list(PathRef(()))} == {"alpha.txt"}
        else:
            assert "日本語 folder" in source_names
            assert pane.selected_entry.name == "alpha.txt"
        assert app._crash_report is None
        assert app._focused_file_pane() is pane


async def test_rename_marked_entry_keeps_unrelated_cursor(app_context_factory):
    fs = await seeded()
    ctx = app_context_factory(fs=fs)
    _use_injected_s3_connection(ctx)
    app = AwsTuiApp(ctx)
    async with app.run_test(size=(120, 40)) as pilot:
        pane = await ready(app)
        pane.set_marked_entries([pane.entries[0]], marked=True)
        pane.move_cursor_to(1)
        await pilot.pause()
        await open_action(app, pilot, ctx, "move", "key")
        assert await pilot.click("#file-operation-rename")
        await wait_until(lambda: bool(app.screen.query(Input)), what="rename name")
        app.screen.query_one(Input).value = "renamed[one].txt"
        await pilot.press("enter")
        await wait_until(lambda: isinstance(app.screen, ConfirmModal), what="rename review")
        assert ctx.confirm_vm.request.paths[0].path.endswith("/alpha.txt")
        assert ctx.confirm_vm.request.paths[1].path.endswith("/renamed[one].txt")
        await pilot.press("enter")
        await drain_workers(app)
        assert {e.name for e in await fs.list(PathRef(()))} == {
            "renamed[one].txt",
            "beta.txt",
            "gamma.txt",
        }
        assert pane.selected_entry.name == "beta.txt"
        assert app._crash_report is None


@pytest.mark.parametrize("operation", ["move", "new"])
async def test_cancel_has_no_mutation(app_context_factory, operation):
    fs = await seeded()
    ctx = app_context_factory(fs=fs)
    _use_injected_s3_connection(ctx)
    app = AwsTuiApp(ctx)
    async with app.run_test(size=(120, 40)) as pilot:
        await ready(app)
        before = list(fs.calls)
        await open_action(app, pilot, ctx, operation, "key")
        await pilot.press("escape")
        await drain_workers(app)
        assert fs.calls == before
        assert len(app.screen_stack) == 1


@pytest.mark.parametrize("operation", ["move", "new", "rename"])
@pytest.mark.parametrize("stage", ["input", "review"])
async def test_changed_listing_invalidates_original_intent(app_context_factory, operation, stage):
    from aws_tui.ui.widgets.file_operation_modals import FileNameModal

    fs = await seeded()
    ctx = app_context_factory(fs=fs)
    _use_injected_s3_connection(ctx)
    app = AwsTuiApp(ctx)
    async with app.run_test(size=(120, 40)) as pilot:
        pane = await ready(app)
        await pilot.pause()
        await open_action(app, pilot, ctx, "new" if operation == "new" else "move", "key")
        if operation == "rename":
            await pilot.click("#file-operation-rename")
            await wait_until(lambda: isinstance(app.screen, FileNameModal), what="rename input")
        if operation != "move":
            app.screen.query_one(Input).value = "changed"
            if stage == "review":
                await pilot.press("enter")
                await wait_until(lambda: isinstance(app.screen, ConfirmModal), what="name review")
        await pane.refresh()
        before = list(fs.calls)
        if operation == "move":
            await pilot.click("#file-operation-move")
        else:
            await pilot.press("enter")
        await drain_workers(app)
        assert not [call for call in fs.calls[len(before) :] if call[0] in {"write", "delete"}]
        assert {e.name for e in await fs.list(PathRef(()))} == {
            "alpha.txt",
            "beta.txt",
            "gamma.txt",
        }
        assert len(app.screen_stack) == 1
        assert app._crash_report is None


@pytest.mark.parametrize(
    ("operation", "protected_side"),
    [("move", "source"), ("move", "destination"), ("new", "source"), ("rename", "source")],
)
async def test_post_review_protection_prevents_mutation(
    app_context_factory, operation, protected_side
):
    from aws_tui.domain.mutation_guard import GuardedRemote, fixed_mutation_check
    from aws_tui.ui.widgets.file_operation_modals import FileNameModal

    protected = set()

    async def source_check():
        await fixed_mutation_check("source" in protected)()

    async def destination_check():
        await fixed_mutation_check("destination" in protected)()

    fs = await seeded()
    ctx = app_context_factory(fs=GuardedRemote(fs, source_check))
    _use_injected_s3_connection(ctx)
    app = AwsTuiApp(ctx)
    async with app.run_test(size=(120, 40)) as pilot:
        await ready(app)
        destination = RecordingFS()
        await ctx.root_vm.content_host.current.right.swap_provider(
            GuardedRemote(destination, destination_check)
        )
        await pilot.pause()
        await open_action(app, pilot, ctx, "new" if operation == "new" else "move", "key")
        if operation == "rename":
            await pilot.click("#file-operation-rename")
            await wait_until(lambda: isinstance(app.screen, FileNameModal), what="rename input")
        if operation != "move":
            app.screen.query_one(Input).value = "changed"
            await pilot.press("enter")
            await wait_until(lambda: isinstance(app.screen, ConfirmModal), what="name review")
        protected.add(protected_side)
        before = len(fs.calls), len(destination.calls)
        if operation == "move":
            await pilot.click("#file-operation-move")
        else:
            await pilot.press("enter")
        await drain_workers(app)
        assert not [
            call
            for call in fs.calls[before[0] :] + destination.calls[before[1] :]
            if call[0] in {"write", "delete"}
        ]
        assert {e.name for e in await fs.list(PathRef(()))} == {
            "alpha.txt",
            "beta.txt",
            "gamma.txt",
        }
        assert await destination.list(PathRef(())) == []
        protected.clear()
        await drain_workers(app)
        assert {e.name for e in await fs.list(PathRef(()))} == {
            "alpha.txt",
            "beta.txt",
            "gamma.txt",
        }
        assert app._crash_report is None


async def test_move_to_same_storage_and_path_cannot_delete_source(app_context_factory):
    fs = await seeded()
    ctx = app_context_factory(fs=fs)
    _use_injected_s3_connection(ctx)
    app = AwsTuiApp(ctx)
    async with app.run_test(size=(120, 40)) as pilot:
        await ready(app)
        await ctx.root_vm.content_host.current.right.swap_provider(fs)
        await pilot.pause()
        before = len(fs.calls)
        await open_action(app, pilot, ctx, "move", "key")
        await pilot.click("#file-operation-move")
        await drain_workers(app)
        assert not [call for call in fs.calls[before:] if call[0] in {"write", "delete"}]
        assert {e.name for e in await fs.list(PathRef(()))} == {
            "alpha.txt",
            "beta.txt",
            "gamma.txt",
        }
        assert app._crash_report is None


async def test_new_folder_creates_real_local_directory(app_context_factory, tmp_path):
    from aws_tui.domain.local_fs import LocalFS

    root = tmp_path / "root"
    root.mkdir()
    (root / "valuable.txt").write_text("valuable")
    ctx = app_context_factory(fs=LocalFS(root=root))
    _use_injected_s3_connection(ctx)
    app = AwsTuiApp(ctx)
    async with app.run_test(size=(80, 24)) as pilot:
        await ready(app)
        await pilot.pause()
        await open_action(app, pilot, ctx, "new", "key")
        app.screen.query_one(Input).value = "new folder"
        await pilot.press("enter")
        await wait_until(lambda: isinstance(app.screen, ConfirmModal), what="folder review")
        assert not (root / "new folder").exists()
        await pilot.press("enter")
        await drain_workers(app)
        assert (root / "new folder").is_dir()
        assert (root / "valuable.txt").read_text() == "valuable"
        assert app._crash_report is None


@pytest.mark.parametrize("operation", ["move", "new", "rename"])
async def test_review_shows_connection_identity_separately_from_paths(
    app_context_factory, operation
):
    from textual.widgets import Static

    from aws_tui.ui.widgets.file_operation_modals import FileNameModal

    ctx = app_context_factory(fs=await seeded())
    _use_injected_s3_connection(ctx)
    app = AwsTuiApp(ctx)
    async with app.run_test(size=(120, 40)) as pilot:
        pane = await ready(app)
        dual = ctx.root_vm.content_host.current
        await pane.swap_provider(
            await seeded(),
            identity_label="production · east [profile]",
            connection_key=("production", "us-east-1"),
        )
        await dual.right.swap_provider(
            RecordingFS(),
            identity_label="staging · west [profile]",
            connection_key=("staging", "us-west-2"),
        )
        await pilot.pause()
        await open_action(app, pilot, ctx, "new" if operation == "new" else "move", "key")
        if operation in {"move", "rename"}:
            text = str(app.screen.query_one("#file-move-review", Static).render())
            assert "production · east [profile]" in text
            assert "staging · west [profile]" in text
        if operation == "rename":
            await pilot.click("#file-operation-rename")
            await wait_until(lambda: isinstance(app.screen, FileNameModal), what="rename input")
        if operation != "move":
            assert "production · east [profile]" in str(
                app.screen.query_one(".file-name-location Static", Static).render()
            )
            app.screen.query_one(Input).value = "literal"
            await pilot.press("enter")
            await wait_until(lambda: isinstance(app.screen, ConfirmModal), what="name review")
            assert "production · east [profile]" in "\n".join(ctx.confirm_vm.request.body_lines)
        await pilot.press("escape")
        await drain_workers(app)


async def final_review(app, pilot, ctx, operation):
    from aws_tui.ui.widgets.file_operation_modals import FileNameModal

    await open_action(app, pilot, ctx, "new" if operation == "new" else "move", "key")
    if operation == "rename":
        await pilot.click("#file-operation-rename")
        await wait_until(lambda: isinstance(app.screen, FileNameModal), what="rename input")
    if operation != "move":
        app.screen.query_one(Input).value = "changed"
        await pilot.press("enter")
        await wait_until(lambda: isinstance(app.screen, ConfirmModal), what="name review")


@pytest.mark.parametrize("operation", ["move", "new", "rename"])
@pytest.mark.parametrize("change", ["source", "navigate", "destination"])
async def test_context_change_cannot_redirect_review(app_context_factory, operation, change):
    fs = await seeded()
    ctx = app_context_factory(fs=fs)
    _use_injected_s3_connection(ctx)
    app = AwsTuiApp(ctx)
    async with app.run_test(size=(120, 40)) as pilot:
        pane = await ready(app)
        await pilot.pause()
        await final_review(app, pilot, ctx, operation)
        if change == "source":
            await pane.swap_provider(await seeded(), identity_label="replacement")
        elif change == "navigate":
            await fs.mkdir(PathRef(("folder",)))
            await pane.navigate_to(PathRef(("folder",)))
        else:
            await ctx.root_vm.content_host.current.right.swap_provider(RecordingFS())
        if operation == "move":
            await pilot.click("#file-operation-move")
        else:
            # A new-folder review deliberately owns only its source pane.
            await pilot.press("enter")
        await drain_workers(app)
        original = {e.name for e in await fs.list(PathRef(()))}
        expected = {"alpha.txt", "beta.txt", "gamma.txt"} | (
            {"folder"} if change == "navigate" else set()
        )
        if change == "destination" and operation == "new":
            expected.add("changed")
        assert original == expected
        assert app._crash_report is None


@pytest.mark.parametrize("operation", ["move", "new", "rename"])
async def test_queued_worker_rechecks_review_context(app_context_factory, monkeypatch, operation):
    fs = await seeded()
    ctx = app_context_factory(fs=fs)
    _use_injected_s3_connection(ctx)
    app = AwsTuiApp(ctx)
    queued = []
    async with app.run_test(size=(120, 40)) as pilot:
        pane = await ready(app)
        await pilot.pause()
        await final_review(app, pilot, ctx, operation)
        monkeypatch.setattr(
            app, "_run_lifecycle_worker", lambda work, **kwargs: queued.append(work)
        )
        if operation == "move":
            await pilot.click("#file-operation-move")
        else:
            await pilot.press("enter")
        await drain_workers(app)
        assert len(queued) == 1
        await pane.refresh()
        await queued[0]()
        assert {e.name for e in await fs.list(PathRef(()))} == {
            "alpha.txt",
            "beta.txt",
            "gamma.txt",
        }
        assert app._crash_report is None


@pytest.mark.parametrize(
    ("operation", "stage"), [("rename", "input"), ("rename", "review"), ("new", "review")]
)
async def test_cancel_name_stages_preserves_original_entries(app_context_factory, operation, stage):
    from aws_tui.ui.widgets.file_operation_modals import FileNameModal

    fs = await seeded()
    ctx = app_context_factory(fs=fs)
    _use_injected_s3_connection(ctx)
    app = AwsTuiApp(ctx)
    async with app.run_test(size=(120, 40)) as pilot:
        await ready(app)
        await pilot.pause()
        if stage == "review":
            await final_review(app, pilot, ctx, operation)
        else:
            await open_action(app, pilot, ctx, "move", "key")
            await pilot.click("#file-operation-rename")
            await wait_until(lambda: isinstance(app.screen, FileNameModal), what="rename input")
        before = list(fs.calls)
        await pilot.press("escape")
        await drain_workers(app)
        assert fs.calls == before
        assert len(app.screen_stack) == 1


@pytest.mark.parametrize(("action", "key"), [("pane.move", "m"), ("pane.new", "n")])
@pytest.mark.parametrize("remap", [False, True])
async def test_help_and_hint_use_effective_command_keys(app_context_factory, action, key, remap):
    from aws_tui.ui.widgets.help_modal import HelpActionRow

    ctx = app_context_factory(
        fs=await seeded(),
        keymap=KeymapStore(overlay={action: "x", "emr.cancel": "z"}) if remap else None,
    )
    _use_injected_s3_connection(ctx)
    app = AwsTuiApp(ctx)
    async with app.run_test(size=(120, 40)) as pilot:
        await ready(app)
        await pilot.pause()
        expected_key = "x" if remap else key
        hints = {row.action_id: row for row in ctx.root_vm.chrome.hint_legend.actions}
        assert hints[action].key_label == expected_key
        await app.action_help()
        await pilot.pause()
        rows = {row.action_id: row.presentation for row in app.screen.query(HelpActionRow)}
        assert rows[action].available
        assert rows[action].effective_keys == (expected_key,)
        await pilot.press("escape")


async def test_new_folder_does_not_cancel_an_active_move(app_context_factory):
    from tests.integration.test_copy_delete_actions import _BlockingReadFS, _stream

    fs = _BlockingReadFS()
    await fs.write_stream(PathRef(("alpha.txt",)), _stream(b"valuable"))
    ctx = app_context_factory(fs=fs)
    _use_injected_s3_connection(ctx)
    app = AwsTuiApp(ctx)
    async with app.run_test(size=(120, 40)) as pilot:
        pane = await ready(app)
        await pilot.pause()
        await open_action(app, pilot, ctx, "move", "key")
        await pilot.click("#file-operation-move")
        import asyncio

        await asyncio.wait_for(fs.read_started.wait(), 2)
        try:
            await final_review(app, pilot, ctx, "new")
            await pilot.press("enter")
            await wait_until(
                lambda: any(row.name == "changed" for row in pane.entries),
                what="new folder listing",
            )
            assert not fs.read_cancelled.is_set()
        finally:
            await pilot.press("comma")
            await asyncio.wait_for(fs.read_cancelled.wait(), 2)
