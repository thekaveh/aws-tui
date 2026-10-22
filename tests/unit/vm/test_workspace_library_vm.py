"""Async workspace management keeps truthful receipts across modal cancellation."""

import asyncio
import threading

import pytest

from aws_tui.domain.navigation_workspace import Bookmark, NavigationTarget, Workspace
from aws_tui.domain.workspace_store import WorkspaceStore


def record(name="Daily"):
    target = NavigationTarget("glue", "dev", "us-east-1", (("database", "lake"),))
    return Workspace(name, "glue", "dev", "us-east-1", scopes=(target,))


def manager(tmp_path, **kwargs):
    from aws_tui.vm.workspace_library_vm import WorkspaceLibraryVM

    store = WorkspaceStore(path=tmp_path / "config" / "workspaces.json")
    return WorkspaceLibraryVM(store=store, **kwargs), store


@pytest.mark.asyncio
async def test_manager_crud_and_bookmarks_leave_unrelated_records_intact(tmp_path):
    vm, store = manager(tmp_path)
    notices = []
    subscription = vm.on_write_completed.subscribe(notices.append)
    try:
        assert await vm.refresh()
        assert vm.items == ()
        assert await vm.save(record())
        assert await vm.save(record("Other"))
        assert not await vm.save(record("DAILY"))
        assert "already exists" in vm.error_text
        selected = next(row for row in vm.items if row.name == "Daily")
        bookmark = Bookmark("Lake", selected.scopes[0])
        assert await vm.add_bookmark(selected, bookmark)
        selected = next(row for row in vm.items if row.name == "Daily")
        assert await vm.rename_bookmark(selected, "Lake", "New Lake")
        selected = next(row for row in vm.items if row.name == "Daily")
        assert await vm.delete_bookmark(selected, "New Lake")
        selected = next(row for row in vm.items if row.name == "Daily")
        assert await vm.rename(selected, "Renamed")
        selected = next(row for row in vm.items if row.name == "Renamed")
        assert await vm.delete(selected)
        assert store.list() == (record("Other"),)
        assert len(notices) == 7
        assert all(not notice.warning for notice in notices)
    finally:
        await vm.shutdown()
        subscription.dispose()


@pytest.mark.asyncio
async def test_stale_management_record_cannot_overwrite_a_concurrent_edit(tmp_path):
    vm, store = manager(tmp_path)
    store.save(record())
    try:
        assert await vm.refresh()
        stale = vm.items[0]
        store.add_bookmark(stale.name, Bookmark("Concurrent", stale.scopes[0]))
        assert not await vm.rename(stale, "Overwrite")
        assert "changed" in vm.error_text
        assert store.list()[0].bookmarks[0].name == "Concurrent"
    finally:
        await vm.shutdown()


@pytest.mark.asyncio
async def test_read_only_manager_can_list_but_cannot_write(tmp_path):
    vm, store = manager(tmp_path, read_only=True)
    store.save(record())
    original = store.path.read_bytes()
    try:
        assert await vm.refresh()
        assert not await vm.save(record("Another"))
        assert "read-only" in vm.error_text
        assert store.path.read_bytes() == original
    finally:
        await vm.shutdown()


@pytest.mark.parametrize("raw", [b"{broken", b'{"schema_version":999,"workspaces":[]}'])
@pytest.mark.asyncio
async def test_library_failure_is_safe_visible_and_never_overwritten(tmp_path, raw):
    vm, store = manager(tmp_path)
    store.path.parent.mkdir()
    store.path.write_bytes(raw)
    try:
        assert not await vm.refresh()
        assert vm.error_text
        assert not await vm.save(record())
        assert store.path.read_bytes() == raw
        assert not vm.busy
    finally:
        await vm.shutdown()


@pytest.mark.parametrize("admitted", [False, True])
@pytest.mark.asyncio
async def test_closing_manager_cancels_only_unadmitted_writes_and_reports_publication(
    tmp_path, monkeypatch, admitted
):
    import aws_tui.domain.workspace_store as module

    vm, store = manager(tmp_path)
    notices = []
    subscription = vm.on_write_completed.subscribe(notices.append)
    entered = threading.Event()
    release = threading.Event()
    exited = threading.Event()
    if admitted:
        original = module.os.replace

        def held(*args, **kwargs):
            entered.set()
            try:
                assert release.wait(5)
                return original(*args, **kwargs)
            finally:
                exited.set()

        monkeypatch.setattr(module.os, "replace", held)
    else:
        original = store._write

        def held(*args, **kwargs):
            entered.set()
            try:
                assert release.wait(5)
                return original(*args, **kwargs)
            finally:
                exited.set()

        monkeypatch.setattr(store, "_write", held)
    task = asyncio.create_task(vm.save(record()))
    shutdown = None
    try:
        assert await asyncio.to_thread(entered.wait, 5)
        vm.close()
        shutdown = asyncio.create_task(vm.shutdown())
        await asyncio.sleep(0)  # Let the actual shutdown own its drain before canceling it.
        for _ in range(2):
            shutdown.cancel()
            task.cancel()
            await asyncio.sleep(0)
            assert not task.done()
        assert not exited.is_set()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        await asyncio.gather(shutdown, return_exceptions=True)
        assert exited.is_set()
        assert store.list() == ((record(),) if admitted else ())
        assert len(notices) == int(admitted)
        assert not vm.busy
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)
        if shutdown is not None:
            await asyncio.gather(shutdown, return_exceptions=True)
        await vm.shutdown()
        subscription.dispose()


@pytest.mark.asyncio
async def test_error_after_owned_publication_reports_saved_with_warning(tmp_path, monkeypatch):
    vm, store = manager(tmp_path)
    notices = []
    subscription = vm.on_write_completed.subscribe(notices.append)
    original = store._check_parent
    calls = 0

    def fail_after_publish(expected):
        nonlocal calls
        calls += 1
        if store.path.exists():
            from aws_tui.domain.navigation_workspace import WorkspaceError

            raise WorkspaceError("io")
        return original(expected)

    monkeypatch.setattr(store, "_check_parent", fail_after_publish)
    try:
        assert await vm.save(record())
        assert calls
        assert notices[-1].warning
        assert "saved" in notices[-1].message.lower()
        monkeypatch.setattr(store, "_check_parent", original)
        assert store.list() == (record(),)
    finally:
        await vm.shutdown()
        subscription.dispose()


@pytest.mark.asyncio
async def test_closed_manager_rejects_new_work(tmp_path):
    vm, store = manager(tmp_path)
    vm.close()
    assert not await vm.refresh()
    assert not await vm.save(record())
    assert not store.path.exists()
    await vm.shutdown()


@pytest.mark.asyncio
async def test_observer_closing_on_busy_cannot_start_a_new_disk_write(tmp_path):
    vm, store = manager(tmp_path)
    subscription = vm.on_property_changed.subscribe(
        lambda _property: vm.close() if vm.busy else None
    )
    try:
        assert not await vm.save(record())
        assert not store.path.exists()
        assert not vm.busy
    finally:
        await vm.shutdown()
        subscription.dispose()


@pytest.mark.parametrize("via_observer", [False, True])
@pytest.mark.asyncio
async def test_canceled_owner_never_dispatches_a_new_store_operation(
    tmp_path, monkeypatch, via_observer
):
    vm, store = manager(tmp_path)
    dispatched = []
    original = store.save

    def recording(*args, **kwargs):
        dispatched.append("save")
        return original(*args, **kwargs)

    monkeypatch.setattr(store, "save", recording)

    def observer(_property):
        if via_observer and vm.busy:
            asyncio.current_task().cancel()

    subscription = vm.on_property_changed.subscribe(observer)

    async def save():
        if not via_observer:
            asyncio.current_task().cancel()
        await vm.save(record())

    task = asyncio.create_task(save())
    try:
        with pytest.raises(asyncio.CancelledError):
            await task
        assert dispatched == []
        assert not store.path.exists()
        assert not vm.busy
    finally:
        await vm.shutdown()
        subscription.dispose()
