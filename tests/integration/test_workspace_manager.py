"""Keyboard workspace management through the actual production app and palette."""

import asyncio
import contextlib
import json
from dataclasses import replace

import pytest
from rich.cells import cell_len
from textual.widgets import Static
from vmx import PropertyChangedMessage

from aws_tui import composition
from aws_tui.app import AwsTuiApp
from aws_tui.composition import build_app_context
from aws_tui.demo.in_memory_fs import InMemoryFS
from aws_tui.domain.filesystem import PathRef
from aws_tui.domain.navigation_workspace import Bookmark, FileLocation, NavigationTarget
from aws_tui.domain.workspace_store import WorkspaceStore
from aws_tui.infra.aws_session import TokenProbeResult, TokenState
from aws_tui.infra.keychain import InMemoryKeychain
from aws_tui.services.athena import AthenaService
from aws_tui.services.s3 import S3Service
from aws_tui.ui.widgets.context_picker import ContextPicker
from aws_tui.ui.widgets.dual_pane import DualPane
from aws_tui.ui.widgets.modal_button import ModalButton
from aws_tui.ui.widgets.workspace_library_modal import WorkspaceOpenRequest
from tests.helpers import drain_workers, wait_until


def make_workspace_app(tmp_path, monkeypatch):
    monkeypatch.setattr(composition, "Keyring", InMemoryKeychain)
    config = tmp_path / "config"
    config.mkdir(exist_ok=True)
    (config / "config.toml").write_text(
        '[connections.dev]\nkind="aws"\nregion="us-east-1"\n[defaults]\nconnection="dev"\n'
    )
    ctx = build_app_context(config_dir=config, cache_dir=tmp_path / "cache")
    ctx.aws_session.probe_token = lambda _connection: TokenProbeResult(TokenState.CONNECTED)
    service = ctx.registry.get("s3")
    assert isinstance(service, S3Service)
    service._s3_fs_factory = lambda _connection: InMemoryFS()
    local = tmp_path / "local"
    local.mkdir(exist_ok=True)
    service._local_root = local
    return AwsTuiApp(ctx), ctx


async def focus_with_tabs(app, pilot, identity):
    for _ in range(12):
        if app.focused is not None and app.focused.id == identity:
            return
        await pilot.press("tab")
    raise AssertionError(f"Keyboard could not reach {identity}")


async def open_manager(app, pilot):
    await pilot.press("ctrl+k")
    await wait_until(lambda: bool(app.screen.query("#palette-input")), what="command palette")
    await pilot.press(*"workspaces")
    await wait_until(
        lambda: len(app.app_ctx.command_palette_vm.filtered_entries) == 1,
        what="workspace command in palette",
    )
    assert app.app_ctx.command_palette_vm.filtered_entries[0].id == "app.workspaces"
    await pilot.press("enter")
    await wait_until(lambda: bool(app.screen.query("#workspace-name")), what="workspace manager")
    await drain_workers(app)
    await pilot.pause()


def save_current_request(app, ctx):
    record = app._capture_navigation_workspace("Daily")
    WorkspaceStore(path=ctx.config_store.path.parent / "workspaces.json").save(record)
    return WorkspaceOpenRequest(record)


@pytest.mark.parametrize("size", [(80, 24), (120, 40)])
@pytest.mark.asyncio
async def test_actual_palette_keyboard_save_rename_delete_and_confirmation(
    tmp_path, monkeypatch, size
):
    app, ctx = make_workspace_app(tmp_path, monkeypatch)
    store = WorkspaceStore(path=ctx.config_store.path.parent / "workspaces.json")
    try:
        async with app.run_test(size=size) as pilot:
            await drain_workers(app)
            await open_manager(app, pilot)
            for button in app.screen.query(ModalButton):
                assert cell_len(str(button.render())) <= button.content_size.width
            await focus_with_tabs(app, pilot, "workspace-name")
            await pilot.press(*"Daily")
            await focus_with_tabs(app, pilot, "workspace-save")
            await pilot.press("enter")
            await drain_workers(app)
            assert [row.name for row in store.list()] == ["Daily"]
            saved = store.list()[0]
            assert saved.file_panes is not None
            assert saved.file_panes[0].connection == "dev"
            assert saved.file_panes[1].location.local == str(tmp_path / "local")
            await focus_with_tabs(app, pilot, "workspace-name")
            await pilot.press("home", "shift+end", "backspace", *"Renamed")
            await focus_with_tabs(app, pilot, "workspace-rename")
            await pilot.press("enter")
            await drain_workers(app)
            assert [row.name for row in store.list()] == ["Renamed"]
            await focus_with_tabs(app, pilot, "workspace-delete")
            await pilot.press("enter")
            await wait_until(lambda: len(app.screen_stack) == 3, what="safe delete confirmation")
            await pilot.press("escape")
            await drain_workers(app)
            assert [row.name for row in store.list()] == ["Renamed"]
            await focus_with_tabs(app, pilot, "workspace-delete")
            await pilot.press("enter")
            await wait_until(lambda: len(app.screen_stack) == 3, what="delete confirmation")
            await pilot.press("right", "enter")
            await drain_workers(app)
            assert store.list() == ()
            await pilot.press("escape")
            await wait_until(lambda: len(app.screen_stack) == 1, what="usable current screen")
            assert ctx.root_vm.content_host.current_id == "s3"
            assert not app._crash_report
    finally:
        ctx.root_vm.dispose()
        ctx.log_sink.close()


@pytest.mark.parametrize("size", [(80, 24), (120, 40)])
@pytest.mark.asyncio
async def test_keyboard_add_rename_delete_bookmark_preserves_parent_and_other_workspace(
    tmp_path, monkeypatch, size
):
    app, ctx = make_workspace_app(tmp_path, monkeypatch)
    store = WorkspaceStore(path=ctx.config_store.path.parent / "workspaces.json")
    try:
        async with app.run_test(size=size) as pilot:
            await drain_workers(app)
            saved = app._capture_navigation_workspace("Daily")
            other = replace(saved, name="Keep")
            store.save(saved)
            store.save(other)
            await open_manager(app, pilot)
            await focus_with_tabs(app, pilot, "workspace-list")
            await pilot.press("home")
            await focus_with_tabs(app, pilot, "workspace-name")
            await pilot.press(*"Place")
            await focus_with_tabs(app, pilot, "workspace-bookmark")
            await pilot.press("enter")
            await drain_workers(app)
            current = next(record for record in store.list() if record.name == "Daily")
            assert [bookmark.name for bookmark in current.bookmarks] == ["Place"]
            assert replace(current, bookmarks=()) == saved
            await focus_with_tabs(app, pilot, "workspace-name")
            await pilot.press("home", "shift+end", "backspace", *"Renamed")
            await focus_with_tabs(app, pilot, "workspace-rename")
            await pilot.press("enter")
            await drain_workers(app)
            current = next(record for record in store.list() if record.name == "Daily")
            assert [bookmark.name for bookmark in current.bookmarks] == ["Renamed"]
            await focus_with_tabs(app, pilot, "workspace-delete")
            await pilot.press("enter")
            await wait_until(
                lambda: len(app.screen_stack) == 3, what="bookmark delete confirmation"
            )
            await pilot.press("right", "enter")
            await drain_workers(app)
            assert store.list() == (saved, other)
            await pilot.press("escape")
            assert not app.crash_report
    finally:
        ctx.root_vm.dispose()
        ctx.log_sink.close()


@pytest.mark.asyncio
async def test_keyboard_open_restores_both_panes_and_matching_view(tmp_path, monkeypatch):
    app, ctx = make_workspace_app(tmp_path, monkeypatch)
    store = WorkspaceStore(path=ctx.config_store.path.parent / "workspaces.json")
    (tmp_path / "local" / "later").mkdir()
    try:
        async with app.run_test(size=(80, 24)) as pilot:
            await drain_workers(app)
            original = ctx.root_vm.content_host.current
            store.save(app._capture_navigation_workspace("Daily"))
            await original.right.navigate_to(PathRef(("later",)))
            assert original.right.path == PathRef(("later",))
            await open_manager(app, pilot)
            await focus_with_tabs(app, pilot, "workspace-open")
            await pilot.press("enter")
            await drain_workers(app)
            restored = ctx.root_vm.content_host.current
            assert restored is not original
            assert restored.right.path == PathRef(())
            views = list(app.query(DualPane))
            assert len(views) == 1
            assert views[0].vm is restored
            assert all(node.display for node in views[0].ancestors_with_self)
            assert len(app.screen_stack) == 1
            assert not app.crash_report
    finally:
        ctx.root_vm.dispose()
        ctx.log_sink.close()


@pytest.mark.parametrize("ambient_cancel", [False, True])
@pytest.mark.asyncio
async def test_open_finishes_actual_publication_after_cancellation(
    tmp_path, monkeypatch, ambient_cancel
):
    app, ctx = make_workspace_app(tmp_path, monkeypatch)
    candidate = None
    try:
        async with app.run_test(size=(80, 24)) as pilot:
            await drain_workers(app)
            original = ctx.root_vm.content_host.current
            request = save_current_request(app, ctx)

            def observer(message):
                nonlocal candidate
                current = ctx.root_vm.content_host.current
                if (
                    candidate is None
                    and isinstance(message, PropertyChangedMessage)
                    and message.property_name == "current"
                    and current is not None
                    and current is not original
                ):
                    candidate = current
                    if ambient_cancel:
                        asyncio.current_task().cancel()
                    else:
                        raise asyncio.CancelledError("publication callback canceled")

            subscription = ctx.hub.messages.subscribe(observer)
            try:
                task = asyncio.create_task(app._open_navigation_workspace(request))
                with contextlib.suppress(asyncio.CancelledError):
                    await task
                await pilot.pause()
                assert candidate is not None
                assert ctx.root_vm.content_host.current is candidate
                views = list(app.query(DualPane))
                assert len(views) == 1
                assert views[0].vm is candidate
                assert all(node.display for node in views[0].ancestors_with_self)
                assert candidate._navigation_adopted
                assert not candidate._disposed
                assert not app.crash_report
            finally:
                subscription.dispose()
    finally:
        ctx.root_vm.dispose()
        ctx.log_sink.close()


@pytest.mark.parametrize("failure", ["missing_connection", "removed_resource"])
@pytest.mark.asyncio
async def test_keyboard_open_failed_destination_keeps_usable_live_screen(
    tmp_path, monkeypatch, failure
):
    app, ctx = make_workspace_app(tmp_path, monkeypatch)
    store = WorkspaceStore(path=ctx.config_store.path.parent / "workspaces.json")
    try:
        async with app.run_test(size=(80, 24)) as pilot:
            await drain_workers(app)
            original = ctx.root_vm.content_host.current
            record = app._capture_navigation_workspace("Daily")
            left, right = record.file_panes
            if failure == "missing_connection":
                record = replace(
                    record, connection="gone", file_panes=(replace(left, connection="gone"), right)
                )
            else:
                record = replace(
                    record,
                    file_panes=(
                        left,
                        replace(
                            right, location=FileLocation(local=str(tmp_path / "local" / "removed"))
                        ),
                    ),
                )
            store.save(record)
            await open_manager(app, pilot)
            await focus_with_tabs(app, pilot, "workspace-open")
            await pilot.press("enter")
            await drain_workers(app)
            assert ctx.root_vm.content_host.current is original
            assert not original._disposed
            assert len(app.screen_stack) == 1
            assert ctx.root_vm.chrome.toast_stack.toasts
            assert not app.crash_report
            before = original.focused
            await pilot.press("tab")
            assert original.focused != before
            await pilot.press("shift+tab")
            assert original.focused == before
    finally:
        ctx.root_vm.dispose()
        ctx.log_sink.close()


@pytest.mark.parametrize("failure", ["malformed", "future_schema"])
@pytest.mark.asyncio
async def test_invalid_library_is_visible_preserved_and_leaves_keyboard_usable(
    tmp_path, monkeypatch, failure
):
    app, ctx = make_workspace_app(tmp_path, monkeypatch)
    path = ctx.config_store.path.parent / "workspaces.json"
    payload = (
        b"{broken-json"
        if failure == "malformed"
        else json.dumps({"schema_version": 999, "workspaces": []}).encode()
    )
    path.write_bytes(payload)
    try:
        async with app.run_test(size=(80, 24)) as pilot:
            await drain_workers(app)
            original = ctx.root_vm.content_host.current
            await open_manager(app, pilot)
            error = app.screen.vm.error_text
            assert error
            assert ("invalid" if failure == "malformed" else "unsupported") in error
            assert error in str(app.screen.query_one("#workspace-status", Static).render())
            await focus_with_tabs(app, pilot, "workspace-name")
            await pilot.press(*"Keep original")
            await focus_with_tabs(app, pilot, "workspace-save")
            await pilot.press("enter")
            await drain_workers(app)
            assert app.screen.vm.error_text
            assert path.read_bytes() == payload
            await pilot.press("escape")
            await wait_until(lambda: len(app.screen_stack) == 1, what="usable unchanged screen")
            assert ctx.root_vm.content_host.current is original
            assert not original._disposed
            before = original.focused
            await pilot.press("tab")
            assert original.focused != before
            assert not app.crash_report
    finally:
        ctx.root_vm.dispose()
        ctx.log_sink.close()


@pytest.mark.asyncio
async def test_open_does_not_cancel_accepted_transfer_worker(tmp_path, monkeypatch):
    app, ctx = make_workspace_app(tmp_path, monkeypatch)
    entered, release = asyncio.Event(), asyncio.Event()
    canceled = False

    async def accepted_transfer():
        nonlocal canceled
        entered.set()
        try:
            await release.wait()
        except asyncio.CancelledError:
            canceled = True
            raise

    try:
        async with app.run_test(size=(80, 24)):
            await drain_workers(app)
            original = ctx.root_vm.content_host.current
            generation = app.service_navigation.generation
            worker = app._run_lifecycle_worker(accepted_transfer, group="transfer-copy")
            await entered.wait()
            await app._open_navigation_workspace(save_current_request(app, ctx))
            assert ctx.root_vm.content_host.current is original
            assert app.service_navigation.generation == generation
            assert not worker.is_finished
            assert not canceled
            release.set()
            await drain_workers(app)
            assert not app.crash_report
    finally:
        release.set()
        ctx.root_vm.dispose()
        ctx.log_sink.close()


@pytest.mark.asyncio
async def test_open_does_not_overwrite_pane_navigation_aba(tmp_path, monkeypatch):
    from aws_tui import app as app_module

    app, ctx = make_workspace_app(tmp_path, monkeypatch)
    entered, release = asyncio.Event(), asyncio.Event()
    real_resolve = app_module.resolve_workspace_sources

    async def delayed_resolve(*args):
        entered.set()
        await release.wait()
        return await real_resolve(*args)

    (tmp_path / "local" / "later").mkdir()
    monkeypatch.setattr(app_module, "resolve_workspace_sources", delayed_resolve)
    task = None
    try:
        async with app.run_test(size=(80, 24)):
            await drain_workers(app)
            original = ctx.root_vm.content_host.current
            task = asyncio.create_task(
                app._open_navigation_workspace(save_current_request(app, ctx))
            )
            await entered.wait()
            await original.right.navigate_to(PathRef(("later",)))
            await original.right.navigate_to(PathRef(()))
            release.set()
            await task
            assert ctx.root_vm.content_host.current is original
            assert not original._disposed
            assert not app.crash_report
    finally:
        release.set()
        if task is not None:
            with contextlib.suppress(asyncio.CancelledError):
                await task
        ctx.root_vm.dispose()
        ctx.log_sink.close()


@pytest.mark.asyncio
async def test_keyboard_bookmark_uses_normal_context_pickers_and_ignores_other_destinations(
    tmp_path, monkeypatch
):
    from tests.unit.vm.athena.test_page_vm import PageClient

    app, ctx = make_workspace_app(tmp_path, monkeypatch)
    client = PageClient(connection_name="dev", region="us-east-1")
    service = ctx.registry.get("athena")
    assert isinstance(service, AthenaService)
    service._client_factory = lambda _connection: client
    target = NavigationTarget(
        "athena",
        "dev",
        "us-east-1",
        (("workgroup", "analysts"), ("catalog", "AwsDataCatalog"), ("database", "events")),
    )
    store = WorkspaceStore(path=ctx.config_store.path.parent / "workspaces.json")
    try:
        async with app.run_test(size=(120, 40)) as pilot:
            await drain_workers(app)
            parent = replace(
                app._capture_navigation_workspace("Daily"),
                scopes=(NavigationTarget("glue", "gone", "eu-west-1"),),
                bookmarks=(Bookmark("Events", target),),
            )
            store.save(parent)
            await open_manager(app, pilot)
            await focus_with_tabs(app, pilot, "workspace-list")
            await pilot.press("down", "enter")
            await drain_workers(app)
            await pilot.pause()
            assert ctx.root_vm.content_host.current_id == "athena"
            assert app.query_one("#athena-workgroup", ContextPicker).value == "analysts"
            assert app.query_one("#athena-catalog", ContextPicker).value == "AwsDataCatalog"
            assert app.query_one("#athena-database", ContextPicker).value == "events"
            assert not client.start_calls
            assert not client.stop_calls
            assert not client.result_calls
            assert not app.crash_report
    finally:
        ctx.root_vm.dispose()
        ctx.log_sink.close()


@pytest.mark.asyncio
async def test_workspace_can_be_opened_from_settings(tmp_path, monkeypatch):
    app, ctx = make_workspace_app(tmp_path, monkeypatch)
    try:
        async with app.run_test(size=(80, 24)) as pilot:
            await drain_workers(app)
            request = save_current_request(app, ctx)
            app.action_open_settings()
            await drain_workers(app)
            assert ctx.root_vm.content_host.current_id == "settings"
            await app._open_navigation_workspace(request)
            await pilot.pause()
            assert ctx.root_vm.content_host.current_id == "s3"
            assert len(list(app.query(DualPane))) == 1
            assert not app.crash_report
    finally:
        ctx.root_vm.dispose()
        ctx.log_sink.close()


@pytest.mark.parametrize("cancel_parent", [False, True])
@pytest.mark.asyncio
async def test_claimed_workspace_swap_pauses_input_and_finishes_before_cancel(
    tmp_path, monkeypatch, cancel_parent
):
    app, ctx = make_workspace_app(tmp_path, monkeypatch)
    entered, release = asyncio.Event(), asyncio.Event()
    task = None
    try:
        async with app.run_test(size=(80, 24)) as pilot:
            await drain_workers(app)
            original = ctx.root_vm.content_host.current
            shutdown = original.transfer_runtime.shutdown

            async def held_shutdown():
                entered.set()
                await release.wait()
                await shutdown()

            monkeypatch.setattr(original.transfer_runtime, "shutdown", held_shutdown)
            task = asyncio.create_task(
                app._open_navigation_workspace(save_current_request(app, ctx))
            )
            await entered.wait()
            assert original._shutdown_started
            assert app._workspace_input_claimed
            assert app.query_one("#main-area").disabled
            owner = app.service_navigation.owner
            await pilot.press("S")
            assert app.service_navigation.owner == owner
            if cancel_parent:
                task.cancel()
                await asyncio.sleep(0)
                task.cancel()
                await asyncio.sleep(0)
                assert not task.done()
            release.set()
            with contextlib.suppress(asyncio.CancelledError):
                await task
            await drain_workers(app)
            current = ctx.root_vm.content_host.current
            assert current is not None
            assert current is not original
            assert current._navigation_adopted
            assert not current._disposed
            views = list(app.query(DualPane))
            assert len(views) == 1
            assert views[0].vm is current
            assert not app._workspace_input_claimed
            assert not app.query_one("#main-area").disabled
            assert not app.crash_report
    finally:
        release.set()
        if task is not None:
            with contextlib.suppress(asyncio.CancelledError):
                await task
        ctx.root_vm.dispose()
        ctx.log_sink.close()


@pytest.mark.asyncio
async def test_accepted_source_key_supersedes_workspace_before_worker_runs(tmp_path, monkeypatch):
    app, ctx = make_workspace_app(tmp_path, monkeypatch)
    commit_entered, allow_commit = asyncio.Event(), asyncio.Event()
    source_entered, allow_source = asyncio.Event(), asyncio.Event()
    commit = app._commit_staged_service_recovery
    swap_source = app.action_swap_source
    candidate = None
    task = None

    async def held_commit(recovery, **kwargs):
        nonlocal candidate
        candidate = recovery.vm
        commit_entered.set()
        await allow_commit.wait()
        return await commit(recovery, **kwargs)

    async def held_source(**kwargs):
        source_entered.set()
        await allow_source.wait()
        await swap_source(**kwargs)

    monkeypatch.setattr(app, "_commit_staged_service_recovery", held_commit)
    monkeypatch.setattr(app, "action_swap_source", held_source)
    try:
        async with app.run_test(size=(80, 24)) as pilot:
            await drain_workers(app)
            original = ctx.root_vm.content_host.current
            task = asyncio.create_task(
                app._open_navigation_workspace(save_current_request(app, ctx))
            )
            await commit_entered.wait()
            assert not app._workspace_input_claimed
            generation = app.service_navigation.generation
            await pilot.press("S")
            await source_entered.wait()
            assert app.service_navigation.generation > generation
            with contextlib.suppress(asyncio.CancelledError):
                await task
            assert candidate._disposed
            assert ctx.root_vm.content_host.current is original
            assert not original._disposed
            allow_source.set()
            await drain_workers(app)
            assert original.left.current_connection_key is None
            assert not app.crash_report
    finally:
        allow_commit.set()
        allow_source.set()
        if task is not None:
            with contextlib.suppress(asyncio.CancelledError):
                await task
        ctx.root_vm.dispose()
        ctx.log_sink.close()
