"""Version browser ownership and immutable confirmation targets."""

import asyncio
from collections.abc import AsyncIterator

import pytest
from vmx import NULL_DISPATCHER, MessageHub

from aws_tui.domain.filesystem import ConflictError, PathRef
from aws_tui.vm.file_manager.pane_vm import PaneVM
from tests.s3_versions_support import ReadGate, VersionsFS


@pytest.fixture
async def context() -> AsyncIterator:
    fs = VersionsFS()
    await fs.mkdir(PathRef(("bucket", "prefix")))

    async def content():
        yield b"current"

    await fs.write_stream(PathRef(("bucket", "prefix", "a.txt")), content())
    hub = MessageHub()
    pane = PaneVM(
        provider=fs,
        path_protocol="s3:",
        connection_key=("aws", "fixture"),
        hub=hub,
        dispatcher=NULL_DISPATCHER,
    )
    pane.construct()
    await pane.setup()
    await pane.navigate_to(PathRef(("bucket", "prefix")))
    pane.move_cursor_to(
        next(i for i, row in enumerate(pane.filtered_entries) if row.name == "a.txt")
    )
    browsers = []
    try:
        yield fs, pane, hub, browsers
    finally:
        for vm in browsers:
            await vm.shutdown()
            vm.dispose()
        pane.dispose()


def browser(context):
    fs, pane, hub, browsers = context
    from aws_tui.vm.file_manager.s3_versions_vm import S3VersionsVM

    vm = S3VersionsVM(pane=pane, hub=hub, dispatcher=NULL_DISPATCHER)
    vm.construct()
    browsers.append(vm)
    return fs, pane, vm


async def test_selected_key_lists_history_and_inspects_only_explicit_selected_version(context):
    fs, _, vm = browser(context)
    assert vm.key == "a.txt"
    await vm.load()
    assert vm.state == "ready"
    assert fs.reads == [("list", PathRef(("bucket", "prefix", "a.txt")))]
    assert vm.selected.is_delete_marker
    vm.select(1)
    await vm.inspect()
    assert vm.details.version_id == "old +/="
    assert fs.reads[-1] == ("details", vm.selected)


async def test_exact_deleted_key_keeps_all_literal_components(context):
    fs, _, vm = browser(context)
    vm.set_key("deleted//../marker/")
    await vm.load()
    assert fs.reads[-1] == (
        "list",
        PathRef(("bucket", "prefix", "deleted", "", "..", "marker", "")),
    )
    assert vm.listing.key == "prefix/deleted//../marker/"


async def test_key_change_retires_a_cancellation_resistant_listing(context):
    fs, _, vm = browser(context)
    gate = fs.list_gate = ReadGate(resistant=True)
    task = asyncio.create_task(vm.load())
    await asyncio.wait_for(gate.entered.wait(), 1)
    vm.set_key("another")
    gate.release.set()
    await task
    assert vm.key == "another"
    assert vm.listing is None
    assert vm.state == "idle"


async def test_selection_change_retires_late_detail(context):
    fs, _, vm = browser(context)
    await vm.load()
    vm.select(1)
    gate = fs.detail_gate = ReadGate(resistant=True)
    task = asyncio.create_task(vm.inspect())
    await asyncio.wait_for(gate.entered.wait(), 1)
    vm.select(0)
    gate.release.set()
    await task
    assert vm.selected.is_delete_marker
    assert vm.details is None


async def test_prefix_change_closes_browser_and_drains_held_read(context):
    fs, pane, vm = browser(context)
    gate = fs.list_gate = ReadGate(resistant=True)
    task = asyncio.create_task(vm.load())
    await asyncio.wait_for(gate.entered.wait(), 1)
    await pane.navigate_to(PathRef(("bucket",)))
    gate.release.set()
    await task
    await vm.shutdown()
    assert vm.state == "closed"
    assert vm.listing is None


async def test_marker_cannot_prepare_content_transfer(context):
    fs, _, vm = browser(context)
    await vm.load()
    count = len(fs.reads)
    with pytest.raises(ConflictError, match="delete marker"):
        await vm.prepare_recovery("recovered")
    assert len(fs.reads) == count


async def test_prepared_recovery_is_bound_to_key_selection_and_generation(context):
    _, _, vm = browser(context)
    await vm.load()
    vm.select(1)
    prepared = await vm.prepare_recovery("new//../key")
    assert vm.accepts(prepared)
    assert prepared.request.destination_path == PathRef(
        ("bucket", "prefix", "new", "", "..", "key")
    )
    assert prepared.request.version.version_id == "old +/="
    vm.set_key("another")
    assert not vm.accepts(prepared)


async def test_loading_observer_can_invalidate_before_provider_entry(context):
    fs, _, vm = browser(context)
    subscription = vm.on_property_changed.subscribe(
        lambda _: vm.set_key("changed") if vm.state == "loading" else None
    )
    await vm.load()
    subscription.dispose()
    assert not fs.reads
    assert vm.state == "idle"


async def test_provider_errors_are_redacted_and_retry_is_available(context):
    fs, _, vm = browser(context)
    fs.list_error = RuntimeError("https://user:secret@example.com/?X-Amz-Signature=hidden")
    await vm.load()
    assert vm.state == "error"
    assert "secret" not in vm.error_text
    assert "hidden" not in vm.error_text
    fs.list_error = None
    await vm.load()
    assert vm.state == "ready"


@pytest.mark.parametrize("operation", ["load", "inspect", "recovery"])
async def test_cancelled_read_releases_controls_without_closing_browser(context, operation):
    fs, _, vm = browser(context)
    gate = ReadGate()
    if operation == "load":
        fs.list_gate = gate
        work = vm.load()
    else:
        await vm.load()
        vm.select(1)
        fs.detail_gate = gate
        work = vm.inspect() if operation == "inspect" else vm.prepare_recovery("new-key")
    task = asyncio.create_task(work)
    await asyncio.wait_for(gate.entered.wait(), 1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert vm.state != "closed"
    assert not vm.busy
    assert vm.state != "loading"
    gate.release.set()
    fs.list_gate = fs.detail_gate = None
    await vm.load()
    assert vm.state == "ready"
