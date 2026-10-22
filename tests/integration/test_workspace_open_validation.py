import asyncio
import contextlib
from dataclasses import replace

import pytest
from textual.widgets import TextArea

from aws_tui.demo.in_memory_fs import InMemoryFS
from aws_tui.domain.filesystem import PathRef
from aws_tui.domain.navigation_workspace import Bookmark, FileLocation, NavigationTarget
from aws_tui.domain.workspace_store import WorkspaceStore
from aws_tui.ui.widgets.workspace_library_modal import WorkspaceOpenRequest
from aws_tui.vm.chrome.focus_coordinator_vm import FocusSlot
from tests.helpers import drain_workers, wait_until
from tests.integration.test_workspace_manager import (
    focus_with_tabs,
    make_workspace_app,
    open_manager,
)
from tests.unit.vm.athena.test_page_vm import PageClient


@pytest.mark.parametrize("kind", ["workspace", "bookmark", "unrelated_parent_change"])
@pytest.mark.asyncio
async def test_record_change_during_metadata_read_is_rechecked_before_adoption(
    tmp_path, monkeypatch, kind
):
    app, ctx = make_workspace_app(tmp_path, monkeypatch)
    entered, release = asyncio.Event(), asyncio.Event()
    store = WorkspaceStore(path=ctx.config_store.path.parent / "workspaces.json")
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
            pane = original.focused_pane
            original_provider = pane.provider
            target = NavigationTarget("s3", "dev", "us-east-1", location=FileLocation(s3=()))
            saved = replace(
                app._capture_navigation_workspace("Daily"), bookmarks=(Bookmark("Place", target),)
            )
            store.save(saved)
            replacement_provider = Held()
            ctx.registry.get("s3")._s3_fs_factory = lambda _source: replacement_provider
            request = WorkspaceOpenRequest(
                saved, saved.bookmarks[0] if kind != "workspace" else None
            )
            task = asyncio.create_task(app._open_navigation_workspace(request))
            try:
                await asyncio.wait_for(entered.wait(), 5)
                replacement = replace(
                    saved,
                    bookmarks=saved.bookmarks if kind == "unrelated_parent_change" else (),
                    scopes=(NavigationTarget("glue", "unrelated", "us-east-1"),),
                )
                store.delete(saved.name, expected=saved)
                store.save(replacement)
                release.set()
                await task
                if kind == "unrelated_parent_change":
                    assert pane.provider is replacement_provider
                    assert pane.path == PathRef()
                    assert any(
                        "Bookmark opened" in toast.model.text
                        for toast in ctx.root_vm.chrome.toast_stack.toasts
                    )
                else:
                    assert ctx.root_vm.content_host.current is original
                    assert not original._disposed
                    assert pane.provider is original_provider
                    assert any(
                        "changed" in toast.model.text
                        for toast in ctx.root_vm.chrome.toast_stack.toasts
                    )
                assert not app.crash_report
            finally:
                release.set()
                if task is not None:
                    with contextlib.suppress(asyncio.CancelledError):
                        await task
    finally:
        ctx.root_vm.dispose()
        ctx.log_sink.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["workspace", "bookmark"])
async def test_loaded_saved_record_cannot_open_obsolete_destination(tmp_path, monkeypatch, kind):
    app, ctx = make_workspace_app(tmp_path, monkeypatch)
    local = tmp_path / "local"
    (local / "old").mkdir()
    (local / "new").mkdir()

    def target(leaf):
        return NavigationTarget("s3", location=FileLocation(local=str(local / leaf)))

    store = WorkspaceStore(path=ctx.config_store.path.parent / "workspaces.json")
    try:
        async with app.run_test(size=(80, 24)) as pilot:
            await drain_workers(app)
            original = ctx.root_vm.content_host.current
            initial = app._capture_navigation_workspace("Daily")
            initial = replace(
                initial,
                file_panes=(initial.file_panes[0], target("old")),
                bookmarks=(Bookmark("Place", target("old")),),
            )
            store.save(initial)
            if kind == "bookmark":
                app._project_focus_slot(FocusSlot.S3_RIGHT)
                await pilot.pause()
                assert original.focused_pane is original.right
            await open_manager(app, pilot)
            assert app.screen.vm.items == (initial,)
            replacement = replace(
                initial,
                file_panes=(initial.file_panes[0], target("new")),
                bookmarks=(Bookmark("Place", target("new")),),
            )
            store.delete("Daily", expected=initial)
            store.save(replacement)
            if kind == "bookmark":
                # Right file pane is the selected existing pane; preserve explicit keyboard focus intent.
                assert original.focused_pane is original.right
                await focus_with_tabs(app, pilot, "workspace-list")
                await pilot.press("down", "enter")
            else:
                await focus_with_tabs(app, pilot, "workspace-open")
                await pilot.press("enter")
            await drain_workers(app)
            await pilot.pause()
            current = ctx.root_vm.content_host.current
            # Either reject stale saved intent or freshly resolve current intent.
            # Silently adopting the obsolete target is the bad outcome.
            assert current is original or current.right.path == PathRef(("new",)), (
                "obsolete saved workspace was silently adopted"
            )
            if kind == "bookmark":
                assert current.right.path != PathRef(("old",)), (
                    "obsolete named bookmark destination was silently applied"
                )
            assert store.list() == (replacement,)
            assert not app.crash_report
    finally:
        ctx.root_vm.dispose()
        ctx.log_sink.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("answer", ["decline", "accept"])
async def test_athena_unsaved_sql_confirmation_does_not_execute_or_lose_decline(
    tmp_path, monkeypatch, answer
):
    app, ctx = make_workspace_app(tmp_path, monkeypatch)
    client = PageClient(connection_name="dev", region="us-east-1")
    ctx.registry.get("athena")._client_factory = lambda _source: client
    store = WorkspaceStore(path=ctx.config_store.path.parent / "workspaces.json")
    try:
        async with app.run_test(size=(120, 40)) as pilot:
            await drain_workers(app)
            saved = app._capture_navigation_workspace("Files")
            store.save(saved)
            ctx.root_vm.services_menu.switch_service_command.execute("athena")
            await drain_workers(app)
            await app._wait_for_current_service_setup()
            current = ctx.root_vm.content_host.current
            current.query.set_sql("SELECT 'review263-unsaved'")
            await pilot.pause()
            assert app.query_one("#athena-editor", TextArea).text == current.query.sql
            await open_manager(app, pilot)
            await focus_with_tabs(app, pilot, "workspace-open")
            await pilot.press("enter")
            await wait_until(
                lambda: ctx.confirm_vm.is_open, what="actual unsaved authoring confirmation"
            )
            if answer == "decline":
                await pilot.press("escape")
            else:
                await pilot.press("right", "enter")
            await drain_workers(app)
            await pilot.pause()
            if answer == "decline":
                assert ctx.root_vm.content_host.current is current
                assert current.query.sql == "SELECT 'review263-unsaved'"
            else:
                assert ctx.root_vm.content_host.current_id == "s3"
                assert ctx.root_vm.content_host.current is not current
            assert not client.start_calls
            assert not client.stop_calls
            assert not client.result_calls
            assert not app.crash_report
    finally:
        ctx.root_vm.dispose()
        ctx.log_sink.close()
