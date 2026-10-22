"""A file bookmark applies one destination through actual application navigation."""

import asyncio
import contextlib
import threading
from dataclasses import replace

import pytest

from aws_tui.demo.in_memory_fs import InMemoryFS
from aws_tui.domain.filesystem import PathRef, ProviderUnreachableError
from aws_tui.domain.navigation_workspace import Bookmark, FileLocation, NavigationTarget
from aws_tui.domain.workspace_store import WorkspaceStore
from aws_tui.ui.widgets.workspace_library_modal import WorkspaceOpenRequest
from aws_tui.vm.chrome.focus_coordinator_vm import FocusSlot
from aws_tui.vm.file_manager.pane_vm import PaneSortField, PaneState
from aws_tui.vm.messages import ConnectionListChangedMessage
from aws_tui.vm.workspace_panes import capture_pane_target
from tests.helpers import drain_workers
from tests.integration.test_workspace_manager import (
    focus_with_tabs,
    make_workspace_app,
    open_manager,
    save_current_request,
)


async def open_saved_bookmark(app, pilot):
    await open_manager(app, pilot)
    await focus_with_tabs(app, pilot, "workspace-list")
    await pilot.press("down", "enter")
    await drain_workers(app)
    await pilot.pause()


@pytest.mark.parametrize("side", ["left", "right"])
@pytest.mark.parametrize("destination", ["local", "remote"])
@pytest.mark.asyncio
async def test_keyboard_bookmark_replaces_only_chosen_pane(
    tmp_path, monkeypatch, side, destination
):
    app, ctx = make_workspace_app(tmp_path, monkeypatch)
    remote = InMemoryFS()
    segments = ("bucket", "literal", "", "λ\\x")
    await remote.mkdir(PathRef(segments))
    (tmp_path / "local" / "chosen").mkdir()
    ctx.registry.get("s3")._s3_fs_factory = lambda _source: remote
    target = (
        NavigationTarget("s3", "dev", "us-east-1", location=FileLocation(s3=segments))
        if destination == "remote"
        else NavigationTarget("s3", location=FileLocation(local=str(tmp_path / "local" / "chosen")))
    )
    try:
        async with app.run_test(size=(80, 24)) as pilot:
            await drain_workers(app)
            original = ctx.root_vm.content_host.current
            app._project_focus_slot(FocusSlot.S3_LEFT if side == "left" else FocusSlot.S3_RIGHT)
            await pilot.pause()
            selected = getattr(original, side)
            other = original.right if side == "left" else original.left
            other_provider, other_target, other_revision = (
                other.provider,
                capture_pane_target(other),
                other.listing_revision,
            )
            parent = replace(
                app._capture_navigation_workspace("Daily"), bookmarks=(Bookmark("Chosen", target),)
            )
            WorkspaceStore(path=ctx.config_store.path.parent / "workspaces.json").save(parent)
            await open_saved_bookmark(app, pilot)
            assert ctx.root_vm.content_host.current is original
            assert capture_pane_target(selected) == target
            assert other.provider is other_provider
            assert capture_pane_target(other) == other_target
            assert other.listing_revision == other_revision
            assert not app.crash_report
    finally:
        ctx.root_vm.dispose()
        ctx.log_sink.close()


@pytest.mark.asyncio
async def test_local_bookmark_ignores_unrelated_missing_pane_source(tmp_path, monkeypatch):
    app, ctx = make_workspace_app(tmp_path, monkeypatch)
    (tmp_path / "local" / "chosen").mkdir()
    target = NavigationTarget("s3", location=FileLocation(local=str(tmp_path / "local" / "chosen")))
    try:
        async with app.run_test(size=(80, 24)) as pilot:
            await drain_workers(app)
            original = ctx.root_vm.content_host.current
            await app._rebind_pane_to_connection(
                original.left, replace(ctx.root_vm.active_connection, name="gone")
            )
            original_provider = original.left.provider

            async def forbidden_read(_path):
                pytest.fail("A bookmark must not validate the other pane")

            monkeypatch.setattr(original_provider, "list", forbidden_read)
            app._project_focus_slot(FocusSlot.S3_RIGHT)
            await pilot.pause()
            parent = replace(
                app._capture_navigation_workspace("Daily"), bookmarks=(Bookmark("Chosen", target),)
            )
            WorkspaceStore(path=ctx.config_store.path.parent / "workspaces.json").save(parent)
            await open_saved_bookmark(app, pilot)
            assert ctx.root_vm.content_host.current is original
            assert capture_pane_target(original.right) == target
            assert original.left.provider is original_provider
            assert original.left.current_connection_key == ("aws", "gone")
            assert not app.crash_report
    finally:
        ctx.root_vm.dispose()
        ctx.log_sink.close()


@pytest.mark.parametrize("companion", ["available", "missing_source", "removed_directory"])
@pytest.mark.asyncio
async def test_bookmark_from_settings_preserves_companion_intent_without_gating_target(
    tmp_path, monkeypatch, companion
):
    app, ctx = make_workspace_app(tmp_path, monkeypatch)
    (tmp_path / "local" / "chosen").mkdir()
    target = NavigationTarget("s3", location=FileLocation(local=str(tmp_path / "local" / "chosen")))
    try:
        async with app.run_test(size=(80, 24)) as pilot:
            await drain_workers(app)
            original = ctx.root_vm.content_host.current
            if companion == "missing_source":
                await app._rebind_pane_to_connection(
                    original.right, replace(ctx.root_vm.active_connection, name="gone")
                )
            elif companion == "removed_directory":
                (tmp_path / "local" / "removed").mkdir()
                await original.right.navigate_to(PathRef(("removed",)))
                (tmp_path / "local" / "removed").rmdir()
            expected_other = capture_pane_target(original.right)
            parent = replace(
                app._capture_navigation_workspace("Daily"), bookmarks=(Bookmark("Chosen", target),)
            )
            WorkspaceStore(path=ctx.config_store.path.parent / "workspaces.json").save(parent)
            app.action_open_settings()
            await drain_workers(app)
            assert ctx.root_vm.content_host.current_id == "settings"
            await open_saved_bookmark(app, pilot)
            current = ctx.root_vm.content_host.current
            assert ctx.root_vm.content_host.current_id == "s3"
            assert capture_pane_target(current.left) == target
            assert capture_pane_target(current.right) == expected_other
            assert ctx.root_vm.active_connection is None
            assert not app.crash_report
    finally:
        ctx.root_vm.dispose()
        ctx.log_sink.close()


@pytest.mark.parametrize("change", ["external_edit", "event_aba"])
@pytest.mark.asyncio
async def test_bookmark_revalidates_source_after_metadata_read(tmp_path, monkeypatch, change):
    app, ctx = make_workspace_app(tmp_path, monkeypatch)
    config = ctx.config_store.path
    original_config = config.read_text()
    other_config = '\n[connections.other]\nkind="aws"\nregion="us-east-1"\n'
    config.write_text(original_config + other_config)
    entered, release = asyncio.Event(), asyncio.Event()
    task = None

    class Held(InMemoryFS):
        async def list(self, path):
            entered.set()
            await release.wait()
            return await super().list(path)

    try:
        async with app.run_test(size=(80, 24)):
            await drain_workers(app)
            original = ctx.root_vm.content_host.current
            before = capture_pane_target(original.right)
            app._project_focus_slot(FocusSlot.S3_RIGHT)
            service = ctx.registry.get("s3")
            service._s3_fs_factory = lambda _source: Held()
            target = NavigationTarget("s3", "other", "us-east-1", location=FileLocation(s3=()))
            parent = replace(
                app._capture_navigation_workspace("Daily"), bookmarks=(Bookmark("Other", target),)
            )
            WorkspaceStore(path=ctx.config_store.path.parent / "workspaces.json").save(parent)
            task = asyncio.create_task(
                app._open_navigation_workspace(WorkspaceOpenRequest(parent, parent.bookmarks[0]))
            )
            await entered.wait()
            config.write_text(original_config + other_config.replace("us-east-1", "us-west-2"))
            if change == "event_aba":
                ctx.hub.send(ConnectionListChangedMessage(names=("other",), change="updated"))
                config.write_text(original_config + other_config)
                ctx.hub.send(ConnectionListChangedMessage(names=("other",), change="updated"))
            release.set()
            await task
            assert ctx.root_vm.content_host.current is original
            assert capture_pane_target(original.right) == before
            assert not app.crash_report
    finally:
        release.set()
        if task is not None:
            with contextlib.suppress(asyncio.CancelledError):
                await task
        ctx.root_vm.dispose()
        ctx.log_sink.close()


@pytest.mark.asyncio
async def test_unadopted_workspace_failure_does_not_change_live_source_reachability(
    tmp_path, monkeypatch
):
    app, ctx = make_workspace_app(tmp_path, monkeypatch)

    class Unreachable(InMemoryFS):
        async def list(self, path):
            raise ProviderUnreachableError("controlled unavailable destination")

    try:
        async with app.run_test(size=(80, 24)):
            await drain_workers(app)
            original = ctx.root_vm.content_host.current
            before = set(ctx.unreachable_connections)
            request = save_current_request(app, ctx)
            ctx.registry.get("s3")._s3_fs_factory = lambda _source: Unreachable()
            await app._open_navigation_workspace(request)
            assert ctx.root_vm.content_host.current is original
            assert ctx.unreachable_connections == before
            assert not app.crash_report
    finally:
        ctx.root_vm.dispose()
        ctx.log_sink.close()


@pytest.mark.asyncio
async def test_inactive_bookmark_does_not_wait_for_companion_rpc_and_retires_owned_read(
    tmp_path, monkeypatch
):
    app, ctx = make_workspace_app(tmp_path, monkeypatch)
    with ctx.config_store.path.open("a") as config:
        config.write('\n[connections.companion]\nkind="aws"\nregion="us-east-1"\n')
    (tmp_path / "local" / "chosen").mkdir()
    target = NavigationTarget("s3", location=FileLocation(local=str(tmp_path / "local" / "chosen")))
    entered, cleanup, release = asyncio.Event(), asyncio.Event(), asyncio.Event()

    class HeldCompanion(InMemoryFS):
        async def list(self, path):
            entered.set()
            try:
                await asyncio.Future()
            finally:
                cleanup.set()
                await release.wait()

    try:
        async with app.run_test(size=(80, 24)) as pilot:
            await drain_workers(app)
            original = ctx.root_vm.content_host.current
            service = ctx.registry.get("s3")
            connection = ctx.connection_resolver.resolve_selected("companion")
            await app._rebind_pane_to_connection(original.right, connection)
            expected = capture_pane_target(original.right)
            parent = replace(
                app._capture_navigation_workspace("Daily"), bookmarks=(Bookmark("Chosen", target),)
            )
            WorkspaceStore(path=ctx.config_store.path.parent / "workspaces.json").save(parent)
            app.action_open_settings()
            await drain_workers(app)
            service._s3_fs_factory = lambda _source: HeldCompanion()
            await open_saved_bookmark(app, pilot)
            await entered.wait()
            current = ctx.root_vm.content_host.current
            assert capture_pane_target(current.left) == target
            assert capture_pane_target(current.right) == expected
            assert current.right.state is PaneState.LOADING
            assert current._navigation_adopted
            assert current._refresh_tasks
            app.action_open_settings()
            await cleanup.wait()
            assert ctx.root_vm.content_host.current is current
            release.set()
            await drain_workers(app)
            assert ctx.root_vm.content_host.current_id == "settings"
            assert current._disposed
            assert not current._refresh_tasks
            assert not app.crash_report
    finally:
        release.set()
        ctx.root_vm.dispose()
        ctx.log_sink.close()


@pytest.mark.parametrize("new_input", ["filter", "sort", "cursor", "mark", "modal"])
@pytest.mark.asyncio
async def test_pending_bookmark_preserves_newer_target_input(tmp_path, monkeypatch, new_input):
    app, ctx = make_workspace_app(tmp_path, monkeypatch)
    (tmp_path / "local" / "chosen").mkdir()
    (tmp_path / "local" / "present.txt").write_text("controlled fixture")
    entered, release = asyncio.Event(), asyncio.Event()
    task = None

    class Held(InMemoryFS):
        async def list(self, path):
            entered.set()
            await release.wait()
            return await super().list(path)

    try:
        async with app.run_test(size=(80, 24)) as pilot:
            await drain_workers(app)
            current = ctx.root_vm.content_host.current
            pane = current.right
            before = capture_pane_target(pane)
            app._project_focus_slot(FocusSlot.S3_RIGHT)
            ctx.registry.get("s3")._s3_fs_factory = lambda _source: Held()
            target = NavigationTarget("s3", "dev", "us-east-1", location=FileLocation(s3=()))
            parent = replace(
                app._capture_navigation_workspace("Daily"), bookmarks=(Bookmark("Remote", target),)
            )
            WorkspaceStore(path=ctx.config_store.path.parent / "workspaces.json").save(parent)
            task = asyncio.create_task(
                app._open_navigation_workspace(WorkspaceOpenRequest(parent, parent.bookmarks[0]))
            )
            await entered.wait()
            if new_input == "filter":
                pane.set_filter_command.execute("newer-input")
            elif new_input == "sort":
                pane.set_sort(PaneSortField.NAME, descending=True)
            elif new_input == "cursor":
                pane.move_cursor_to(1)
            elif new_input == "mark":
                pane.mark_at(0)
            else:
                await pilot.press("ctrl+k")
                assert len(app.screen_stack) == 2
            release.set()
            await task
            assert capture_pane_target(pane) == before
            if new_input == "filter":
                assert pane.filter_text == "newer-input"
            assert not app.crash_report
    finally:
        release.set()
        if task is not None:
            with contextlib.suppress(asyncio.CancelledError):
                await task
        ctx.root_vm.dispose()
        ctx.log_sink.close()


@pytest.mark.asyncio
async def test_inactive_bookmark_does_not_wait_for_unrelated_source_resolution(
    tmp_path, monkeypatch
):
    app, ctx = make_workspace_app(tmp_path, monkeypatch)
    with ctx.config_store.path.open("a") as config:
        config.write('\n[connections.companion]\nkind="aws"\nregion="us-east-1"\n')
    (tmp_path / "local" / "chosen").mkdir()
    target = NavigationTarget("s3", location=FileLocation(local=str(tmp_path / "local" / "chosen")))
    entered, release = threading.Event(), threading.Event()
    task = None
    try:
        async with app.run_test(size=(80, 24)) as pilot:
            await drain_workers(app)
            original = ctx.root_vm.content_host.current
            connection = ctx.connection_resolver.resolve_selected("companion")
            await app._rebind_pane_to_connection(original.right, connection)
            parent = replace(
                app._capture_navigation_workspace("Daily"), bookmarks=(Bookmark("Chosen", target),)
            )
            WorkspaceStore(path=ctx.config_store.path.parent / "workspaces.json").save(parent)
            app.action_open_settings()
            await drain_workers(app)
            resolve = ctx.connection_resolver.resolve_selected

            def held_resolve(name):
                if name == "companion":
                    entered.set()
                    assert release.wait(5)
                return resolve(name)

            monkeypatch.setattr(ctx.connection_resolver, "resolve_selected", held_resolve)
            task = asyncio.create_task(open_saved_bookmark(app, pilot))
            assert await asyncio.to_thread(entered.wait, 5)
            await pilot.pause()
            assert ctx.root_vm.content_host.current_id == "s3"
            assert capture_pane_target(ctx.root_vm.content_host.current.left) == target
            assert ctx.root_vm.content_host.current._navigation_adopted
            release.set()
            await task
            assert not app.crash_report
    finally:
        release.set()
        if task is not None:
            with contextlib.suppress(asyncio.CancelledError):
                await task
        ctx.root_vm.dispose()
        ctx.log_sink.close()
