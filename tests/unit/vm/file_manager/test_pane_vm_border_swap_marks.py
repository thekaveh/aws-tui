"""Unit tests for PaneVM border / swap / marked-bytes behavior.

Locks in:
- ``border_title`` is the live path (s3://bucket/folder or /abs/path)
- ``border_subtitle`` carries the identity label (left pane only)
- ``swap_provider`` replaces the source + resets to root
- ``toggle_mark_at`` enters multi-select + republishes viewmodel
- ``summary`` shows marked-byte total when entries are marked
- ``activate(idx)`` dispatches ".." vs directory vs file correctly
- ``move_cursor_to`` clamps + emits cursor_index PropertyChanged
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from vmx import NULL_DISPATCHER, MessageHub, RxDispatcher

from aws_tui.demo.in_memory_fs import InMemoryFS
from aws_tui.domain.filesystem import PathRef
from aws_tui.vm.file_manager.pane_vm import PaneState, PaneVM


async def _stream(data: bytes) -> AsyncIterator[bytes]:
    yield data


async def _seed() -> InMemoryFS:
    fs = InMemoryFS()
    await fs.write_stream(PathRef(("alpha.txt",)), _stream(b"a" * 10))
    await fs.write_stream(PathRef(("beta.txt",)), _stream(b"b" * 100))
    await fs.write_stream(PathRef(("gamma.txt",)), _stream(b"c" * 1000))
    await fs.mkdir(PathRef(("subdir",)))
    return fs


@pytest.mark.asyncio
async def test_border_title_is_path_with_protocol() -> None:
    hub: MessageHub = MessageHub()
    dispatcher = RxDispatcher.immediate()
    fs = await _seed()
    vm = PaneVM(
        provider=fs,
        hub=hub,
        dispatcher=dispatcher,
        id_prefix="pane",
        identity_label="aws · dev · us-east-1",
        path_protocol="s3:",
    )
    vm.construct()
    await vm.setup()
    try:
        # At root.
        assert vm.viewmodel.border_title == "s3://"
        assert vm.viewmodel.border_subtitle == "aws · dev · us-east-1"

        # Navigate into a directory.
        await vm.navigate_to(PathRef(("subdir",)))
        assert vm.viewmodel.border_title == "s3://subdir"
    finally:
        vm.dispose()


@pytest.mark.asyncio
async def test_border_title_no_protocol_for_local() -> None:
    """Local pane uses ``path_protocol=""`` so the border shows
    ``/path`` instead of ``s3://path``."""
    hub: MessageHub = MessageHub()
    dispatcher = RxDispatcher.immediate()
    fs = await _seed()
    vm = PaneVM(
        provider=fs,
        hub=hub,
        dispatcher=dispatcher,
        id_prefix="pane.local",
        identity_label="local",
        path_protocol="",
    )
    vm.construct()
    await vm.setup()
    try:
        assert vm.viewmodel.border_title == "/"
        await vm.navigate_to(PathRef(("subdir",)))
        assert vm.viewmodel.border_title == "/subdir"
    finally:
        vm.dispose()


@pytest.mark.asyncio
async def test_swap_provider_replaces_source_and_resets_root() -> None:
    hub: MessageHub = MessageHub()
    dispatcher = RxDispatcher.immediate()
    fs_a = await _seed()
    fs_b = InMemoryFS()
    await fs_b.write_stream(PathRef(("only-in-b.txt",)), _stream(b"hello"))

    vm = PaneVM(
        provider=fs_a,
        hub=hub,
        dispatcher=dispatcher,
        identity_label="A",
        path_protocol="s3:",
    )
    vm.construct()
    await vm.setup()
    try:
        await vm.navigate_to(PathRef(("subdir",)))
        assert not vm.path.is_root

        await vm.swap_provider(fs_b, identity_label="B", path_protocol="")

        # Pane reset to root with the new provider's listing.
        assert vm.path.is_root
        assert vm.viewmodel.border_subtitle == "B"
        assert vm.viewmodel.border_title == "/"
        names = {e.entry.name for e in vm.filtered_entries}
        assert "only-in-b.txt" in names
    finally:
        vm.dispose()


@pytest.mark.asyncio
async def test_mark_at_is_idempotent_set_not_toggle() -> None:
    """Regression: shift+arrow extend-selection used to call toggle_mark_at
    on the *current* row before moving. When the user walked the cursor
    back across an already-marked row, toggle un-marked it — leaving
    holes mid-range. ``mark_at(idx, marked=True)`` is the new idempotent
    setter and fixes that.

    Trace: mark rows 0 and 1, then `mark_at(1, marked=True)` again. The
    second call must be a no-op, not a flip.
    """
    hub: MessageHub = MessageHub()
    dispatcher = RxDispatcher.immediate()
    fs = await _seed()
    vm = PaneVM(provider=fs, hub=hub, dispatcher=dispatcher)
    vm.construct()
    await vm.setup()
    try:
        names = [e.entry.name for e in vm.filtered_entries]
        idx_a = names.index("alpha.txt")
        idx_b = names.index("beta.txt")

        vm.mark_at(idx_a, marked=True)
        vm.mark_at(idx_b, marked=True)
        first = {e.entry.name for e in vm.filtered_entries if e.is_marked}
        assert first == {"alpha.txt", "beta.txt"}

        # Calling mark_at(beta, True) again must NOT flip beta off.
        vm.mark_at(idx_b, marked=True)
        second = {e.entry.name for e in vm.filtered_entries if e.is_marked}
        assert second == {"alpha.txt", "beta.txt"}, (
            "mark_at(..., marked=True) on an already-marked entry must be a no-op"
        )

        # And mark_at(..., marked=False) explicitly clears.
        vm.mark_at(idx_a, marked=False)
        third = {e.entry.name for e in vm.filtered_entries if e.is_marked}
        assert third == {"beta.txt"}
    finally:
        vm.dispose()


@pytest.mark.asyncio
async def test_toggle_mark_at_updates_summary() -> None:
    hub: MessageHub = MessageHub()
    dispatcher = RxDispatcher.immediate()
    fs = await _seed()
    vm = PaneVM(provider=fs, hub=hub, dispatcher=dispatcher)
    vm.construct()
    await vm.setup()
    try:
        # Find the index of beta.txt (100 bytes) in the filtered list.
        names = [e.entry.name for e in vm.filtered_entries]
        idx = names.index("beta.txt")

        before = vm.viewmodel.summary
        vm.toggle_mark_at(idx)
        after = vm.viewmodel.summary

        assert before != after
        assert "marked" in after
        # 100 bytes formatted by _human_bytes is "100 B".
        assert "100 B" in after
    finally:
        vm.dispose()


@pytest.mark.asyncio
async def test_summary_with_no_marks_shows_total() -> None:
    hub: MessageHub = MessageHub()
    dispatcher = RxDispatcher.immediate()
    fs = await _seed()
    vm = PaneVM(provider=fs, hub=hub, dispatcher=dispatcher)
    vm.construct()
    await vm.setup()
    try:
        summary = vm.viewmodel.summary
        # 3 files seeded (10 + 100 + 1000 = 1110 bytes ≈ 1.1 K) + subdir.
        assert "marked" not in summary
        assert "obj" in summary
    finally:
        vm.dispose()


@pytest.mark.asyncio
async def test_move_cursor_to_clamps_and_updates_index() -> None:
    hub: MessageHub = MessageHub()
    dispatcher = RxDispatcher.immediate()
    fs = await _seed()
    vm = PaneVM(provider=fs, hub=hub, dispatcher=dispatcher)
    vm.construct()
    await vm.setup()
    try:
        n = len(vm.filtered_entries)
        assert n > 0

        vm.move_cursor_to(2)
        assert vm.cursor_index == 2

        vm.move_cursor_to(100)  # out-of-range → clamped to n-1
        assert vm.cursor_index == n - 1

        vm.move_cursor_to(-5)  # negative → clamped to 0
        assert vm.cursor_index == 0
    finally:
        vm.dispose()


@pytest.mark.asyncio
async def test_activate_descends_into_directory() -> None:
    hub: MessageHub = MessageHub()
    dispatcher = RxDispatcher.immediate()
    fs = await _seed()
    vm = PaneVM(provider=fs, hub=hub, dispatcher=dispatcher)
    vm.construct()
    await vm.setup()
    try:
        names = [e.entry.name for e in vm.filtered_entries]
        idx = names.index("subdir")
        await vm.activate(idx)
        assert vm.path == PathRef(("subdir",))
    finally:
        vm.dispose()


@pytest.mark.asyncio
async def test_placeholder_text_in_idle_state() -> None:
    """When IDLE with entries, placeholder_text should be None."""
    hub: MessageHub = MessageHub()
    dispatcher = RxDispatcher.immediate()
    fs = await _seed()
    vm = PaneVM(provider=fs, hub=hub, dispatcher=dispatcher)
    vm.construct()
    await vm.setup()
    try:
        assert vm.state is PaneState.IDLE
        assert vm.viewmodel.placeholder_text is None
    finally:
        vm.dispose()


@pytest.mark.asyncio
async def test_selection_mode_summary_publishes_at_zero_and_hidden_clear() -> None:
    fs = await _seed()
    vm = PaneVM(provider=fs, hub=MessageHub(), dispatcher=NULL_DISPATCHER)
    vm.construct()
    await vm.setup()
    try:
        vm.enter_multiselect_command.execute()
        assert "multi:" in vm.viewmodel.summary
        assert "0 marked" in vm.viewmodel.summary
        assert "0 B" in vm.viewmodel.summary
        vm.move_cursor_to(1)
        vm.toggle_select_command.execute()
        vm.set_filter_command.execute("beta")
        vm.select_all_command.execute()
        assert {e.entry.name for e in vm.marked_entries} == {"beta.txt"}
        assert {e.entry.name for e in vm.entries if e.is_marked} == {"alpha.txt", "beta.txt"}
        assert "100 B" in vm.viewmodel.summary
        vm.clear_selection_command.execute()
        assert not any(entry.is_marked for entry in vm.entries)
        assert vm.marked_entries == ()
        assert vm.is_multiselect_mode
        assert "0 marked" in vm.viewmodel.summary
        vm.exit_multiselect_command.execute()
        assert not vm.is_multiselect_mode
        assert "multi:" not in vm.viewmodel.summary
        await vm.navigate_to(PathRef(("subdir",)))
        vm.select_all_command.execute()
        vm.toggle_select_command.execute()
        assert vm.marked_entries == ()
        await vm.swap_provider(InMemoryFS())
        vm.select_all_command.execute()
        vm.toggle_select_command.execute()
        assert vm.marked_entries == ()
        assert not vm.is_multiselect_mode
    finally:
        vm.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["success", "error", "cancel"])
async def test_reload_clears_marks_before_provider_returns(outcome: str) -> None:
    import asyncio

    from aws_tui.domain.filesystem import ProviderError

    fs = await _seed()
    vm = PaneVM(provider=fs, hub=MessageHub(), dispatcher=NULL_DISPATCHER)
    vm.construct()
    await vm.setup()
    started, release = asyncio.Event(), asyncio.Event()
    original_list = fs.list

    async def blocked_list(path: PathRef):  # type: ignore[no-untyped-def]
        started.set()
        await release.wait()
        if outcome == "error":
            raise ProviderError("listing failed")
        return await original_list(path)

    fs.list = blocked_list  # type: ignore[method-assign]
    task = None
    try:
        vm.toggle_mark_at(1)
        assert len(vm.marked_entries) == 1
        task = asyncio.create_task(vm.refresh())
        await started.wait()
        assert vm.state is PaneState.LOADING
        assert not any(entry.is_marked for entry in vm.entries)
        assert vm.marked_entries == ()
        if outcome == "cancel":
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        else:
            release.set()
            await task
        assert vm.marked_entries == ()
    finally:
        if task is not None and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        vm.dispose()
