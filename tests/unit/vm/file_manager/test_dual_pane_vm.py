"""Tests for DualPaneVM."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path
from typing import cast

import pytest
from vmx import NULL_DISPATCHER, MessageHub
from vmx.lifecycle.status import ConstructionStatus
from vmx.messages.protocols import Message

from aws_tui.domain.filesystem import PathRef
from aws_tui.domain.transfer_journal import TransferJournal
from aws_tui.vm.file_manager.dual_pane_vm import DualPaneVM, FocusedPane
from aws_tui.vm.file_manager.pane_vm import PaneVM
from aws_tui.vm.messages import TransferProgressMessage, TransferState
from tests.unit.domain._in_memory_fs import InMemoryFS


def _hub() -> MessageHub[Message]:
    return cast("MessageHub[Message]", MessageHub())


async def _astream(payload: bytes) -> AsyncIterator[bytes]:
    yield payload


async def _seed_left() -> InMemoryFS:
    fs = InMemoryFS()
    await fs.write_stream(PathRef(("alpha.txt",)), _astream(b"alpha-bytes"))
    await fs.write_stream(PathRef(("beta.txt",)), _astream(b"beta-bytes"))
    return fs


async def _seed_right() -> InMemoryFS:
    fs = InMemoryFS()
    return fs


async def _make_dual(tmp_path: Path) -> tuple[DualPaneVM, MessageHub[Message]]:
    hub = _hub()
    left_fs = await _seed_left()
    right_fs = await _seed_right()
    left = PaneVM(provider=left_fs, hub=hub, dispatcher=NULL_DISPATCHER, id_prefix="left")
    right = PaneVM(provider=right_fs, hub=hub, dispatcher=NULL_DISPATCHER, id_prefix="right")
    journal = TransferJournal(base_dir=tmp_path / "journal")
    dp = DualPaneVM(
        left=left,
        right=right,
        hub=hub,
        dispatcher=NULL_DISPATCHER,
        transfer_journal=journal,
    )
    dp.construct()
    await dp.setup()
    return dp, hub


@pytest.mark.asyncio
async def test_dual_construct_dispose(tmp_path: Path) -> None:
    dp, _ = await _make_dual(tmp_path)
    assert dp.focused == FocusedPane.LEFT
    dp.dispose()
    assert dp.status == ConstructionStatus.DISPOSED


@pytest.mark.asyncio
async def test_dual_switch_focus(tmp_path: Path) -> None:
    dp, _ = await _make_dual(tmp_path)
    dp.switch_focus_command.execute()
    assert dp.focused == FocusedPane.RIGHT
    dp.switch_focus_command.execute()
    assert dp.focused == FocusedPane.LEFT
    dp.dispose()


@pytest.mark.asyncio
async def test_dual_set_focused_is_explicit_and_idempotent(tmp_path: Path) -> None:
    dp, hub = await _make_dual(tmp_path)
    notified: list[str] = []
    sub = hub.messages.subscribe(on_next=lambda m: notified.append(getattr(m, "property_name", "")))
    try:
        dp.set_focused(FocusedPane.RIGHT)
        assert dp.focused == FocusedPane.RIGHT
        dp.set_focused(FocusedPane.RIGHT)
        assert notified.count("focused") == 1
        dp.set_focused(FocusedPane.LEFT)
        assert dp.focused == FocusedPane.LEFT
        assert notified.count("focused") == 2
    finally:
        sub.dispose()
        dp.dispose()


@pytest.mark.asyncio
async def test_focused_and_other_pane_swap_with_focus(tmp_path: Path) -> None:
    """``focused_pane`` and ``other_pane`` track ``focused`` correctly.

    ``AwsTuiApp.action_copy`` reads these two properties to decide the
    copy direction — ``src_pane = dual.focused_pane`` and
    ``dst_pane = dual.other_pane``. The user reported a bug where Tab-
    switching to the right pane and then pressing ``c`` still copied
    LEFT → RIGHT; the root cause turned out to be the 3-slot Tab cycle
    stranding ``focused`` at LEFT after a NAV detour, NOT a VM-level
    direction bug. Lock the VM contract in so any future regression
    that breaks the swap surfaces here without needing a full app
    pilot.
    """
    dp, _ = await _make_dual(tmp_path)
    # Initial: LEFT focused → focused_pane = left, other = right.
    assert dp.focused_pane is dp.left
    assert dp.other_pane is dp.right
    # Toggle: RIGHT focused → focused_pane = right, other = left.
    dp.switch_focus_command.execute()
    assert dp.focused_pane is dp.right
    assert dp.other_pane is dp.left
    # Toggle back: LEFT focused again.
    dp.switch_focus_command.execute()
    assert dp.focused_pane is dp.left
    assert dp.other_pane is dp.right
    dp.dispose()


@pytest.mark.asyncio
async def test_dual_copy_across_publishes_transfer_progress(tmp_path: Path) -> None:
    dp, hub = await _make_dual(tmp_path)
    received: list[TransferProgressMessage] = []
    hub.messages.subscribe(
        on_next=lambda m: received.append(m) if isinstance(m, TransferProgressMessage) else None
    )
    # Mark alpha.txt on the left pane.
    dp.left.enter_multiselect_command.execute()
    dp.left.toggle_select_command.execute()
    assert dp.left.marked_entries[0].entry.name == "alpha.txt"
    await dp.copy_across()
    await dp.right.refresh()
    # Right pane should now have alpha.txt.
    names = [e.entry.name for e in dp.right.entries]
    assert "alpha.txt" in names
    # At least one progress and one completed message must have fired.
    states = [m.state for m in received]
    assert "running" in states
    assert "completed" in states
    # Pin the producer-side URI shape that ``TransfersVM._infer_direction``
    # depends on: both panes are LocalFS in this test, so labels must
    # start with the absolute filesystem ``/`` (no scheme prefix).
    # Locks in the V4-001 fix end-to-end on the real flow, not just
    # the mocked consumer test in test_transfers.py.
    labeled = [m for m in received if m.source_label and m.destination_label]
    assert labeled, "expected at least one progress message to carry source/dest labels"
    sample = labeled[0]
    assert sample.source_label.startswith("/"), (
        f"local pane source must emit unprefixed posix path, got {sample.source_label!r}"
    )
    assert sample.destination_label.startswith("/"), (
        f"local pane destination must emit unprefixed posix path, got {sample.destination_label!r}"
    )
    dp.dispose()


@pytest.mark.asyncio
async def test_dual_copy_across_pre_registers_all_pending_before_running(
    tmp_path: Path,
) -> None:
    """Pre-PR: copy_across registered each transfer one at a time, so the
    user only saw the currently-running transfer (+ the most recent
    completed one lingering). With N marked entries, the overlay
    appeared to handle them in pairs (one running + one done) — masking
    that N-2 more were queued.

    Post-PR: every marked entry sends a PENDING TransferProgressMessage
    upfront, BEFORE the loop starts running any copy. The user sees
    all N rows immediately; each one transitions RUNNING → COMPLETED
    in order.

    Test verifies: every PENDING message fires before the first RUNNING
    message.
    """
    dp, hub = await _make_dual(tmp_path)
    received: list[TransferProgressMessage] = []
    hub.messages.subscribe(
        on_next=lambda m: received.append(m) if isinstance(m, TransferProgressMessage) else None
    )
    # Mark BOTH entries.
    dp.left.enter_multiselect_command.execute()
    dp.left.select_all_command.execute()
    assert len(dp.left.marked_entries) == 2
    await dp.copy_across()

    pending_indexes = [i for i, m in enumerate(received) if m.state == TransferState.PENDING]
    running_indexes = [i for i, m in enumerate(received) if m.state == TransferState.RUNNING]
    assert len(pending_indexes) == 2, (
        f"expected 2 PENDING messages (one per marked entry); got {len(pending_indexes)}"
    )
    assert running_indexes, "expected at least one RUNNING message"
    # The critical assertion: every PENDING message arrives BEFORE
    # the first RUNNING message. That's what makes the overlay show
    # all queued transfers upfront.
    assert max(pending_indexes) < min(running_indexes), (
        "all PENDING messages should fire before any RUNNING — got "
        f"pending at {pending_indexes}, running at {running_indexes}"
    )
    dp.dispose()


@pytest.mark.asyncio
async def test_dual_copy_across_cancel_event_interrupts_in_flight_copy(
    tmp_path: Path,
) -> None:
    """User clicks the cancel chip mid-copy: the per-transfer
    ``asyncio.Event`` is set, ``copy_across``'s ``asyncio.wait`` race
    wakes up, the copy task is cancelled, journal is marked aborted,
    and the batch loop continues to the next queued transfer.

    Pre-PR (just the VM state flip): the row showed CANCELLED while
    bytes kept transferring — the user-reported "cancel doesn't work"
    bug. This test pins the actual interruption.
    """
    # A provider that blocks indefinitely inside read_stream so the
    # copy task is interruptable mid-flight (it sits at an await
    # point that respects CancelledError).
    from collections.abc import AsyncIterator as _AsyncIterator

    from aws_tui.domain.filesystem import EntryKind, FileEntry
    from aws_tui.domain.filesystem import PathRef as _PathRef
    from aws_tui.vm.messages import TransferCancelRequestedMessage

    class _BlockingProvider(InMemoryFS):
        """LocalFS-shaped provider whose read_stream blocks forever
        on a "big file" — gives the copy task something to await on
        that we can interrupt via task.cancel().
        """

        def __init__(self) -> None:
            super().__init__()
            self._block_event = asyncio.Event()
            self.read_started = asyncio.Event()
            self.read_was_cancelled = False
            self._entry = FileEntry(
                name="big-file.bin",
                kind=EntryKind.FILE,
                size=10_000_000,
                modified=None,
            )

        async def list(self, path: _PathRef) -> tuple[FileEntry, ...]:
            return (self._entry,)

        async def stat(self, path: _PathRef) -> FileEntry:
            # CrossFsCopy.copy calls stat before read_stream; without
            # this the test errors out with NotFoundError before ever
            # reaching the interruptable await.
            if path.segments and path.segments[-1] == "big-file.bin":
                return self._entry
            return await super().stat(path)

        async def read_stream(  # type: ignore[override]
            self, _path: _PathRef, *, chunk_size: int = 8 * 1024 * 1024
        ) -> _AsyncIterator[bytes]:
            # Match the FileSystemProvider protocol: ``async def`` whose
            # body ``return``s a separately-defined async generator (same
            # pattern as ``LocalFS.read_stream`` and ``InMemoryFS``).
            # The inner generator yields one empty chunk so the consumer
            # enters its ``async for`` loop, then blocks on the never-set
            # event — leaving the copy task awaiting at a cancellation
            # point we can interrupt via ``task.cancel()``.
            return self._blocking_gen()

        async def _blocking_gen(self) -> _AsyncIterator[bytes]:
            yield b""
            self.read_started.set()
            try:
                await self._block_event.wait()
            except asyncio.CancelledError:
                # The generator's awaiter (CrossFsCopy.copy's
                # ``async for`` consumer) was cancelled — record it so
                # the test can prove the cancel actually interrupted
                # the in-flight copy task and re-raise.
                self.read_was_cancelled = True
                raise
            yield b"never reached"  # pragma: no cover

    hub: MessageHub[Message] = cast("MessageHub[Message]", MessageHub())
    journal = TransferJournal(base_dir=tmp_path / "journal")
    left_provider = _BlockingProvider()
    right_fs = InMemoryFS()
    left = PaneVM(provider=left_provider, hub=hub, dispatcher=NULL_DISPATCHER, id_prefix="left")
    right = PaneVM(provider=right_fs, hub=hub, dispatcher=NULL_DISPATCHER, id_prefix="right")
    dp = DualPaneVM(
        left=left,
        right=right,
        hub=hub,
        dispatcher=NULL_DISPATCHER,
        transfer_journal=journal,
    )
    dp.construct()
    await dp.setup()
    try:
        dp.left.enter_multiselect_command.execute()
        dp.left.toggle_select_command.execute()
        assert dp.left.marked_entries, "expected at least one marked entry"

        # Start the copy in the background; it'll hang inside
        # read_stream waiting on the blocking event.
        copy_task = asyncio.create_task(dp.copy_across())

        # Wait for the cancel event to appear in DualPaneVM's registry
        # (it's populated synchronously by the pre-register loop, but
        # we need to yield to the loop so copy_across runs that far).
        for _ in range(50):
            await asyncio.sleep(0.01)
            if dp._cancel_events:
                break
        assert dp._cancel_events, "copy_across never populated _cancel_events"
        await asyncio.wait_for(left_provider.read_started.wait(), timeout=2.0)

        # Fire the cancel-request as if the user clicked the chip.
        transfer_id = next(iter(dp._cancel_events.keys()))
        hub.send(TransferCancelRequestedMessage(transfer_id=transfer_id))

        # copy_across should now return cleanly (cancel races the
        # blocked copy task, kills it, moves on to the next queued
        # transfer — but there's only one, so the batch ends).
        await asyncio.wait_for(copy_task, timeout=2.0)

        # Cancel event entry was cleaned up in the `finally` block.
        assert transfer_id not in dp._cancel_events
        # The critical assertion that distinguishes "the VM flipped
        # its state to CANCELLED" (pre-fix behaviour) from "the actual
        # in-flight copy task was interrupted" (post-fix behaviour).
        # Without ``copy_task.cancel()`` in ``DualPaneVM._run_one_transfer``
        # the consumer keeps awaiting ``self._block_event.wait()``
        # forever and ``read_was_cancelled`` stays False — exactly the
        # user-reported "cancel doesn't work" bug.
        assert left_provider.read_was_cancelled, (
            "copy_task was not actually cancelled — bytes would have "
            "kept transferring despite the row showing CANCELLED"
        )
    finally:
        dp.dispose()


@pytest.mark.asyncio
async def test_dual_move_across_deletes_source(tmp_path: Path) -> None:
    dp, _ = await _make_dual(tmp_path)
    dp.left.enter_multiselect_command.execute()
    dp.left.toggle_select_command.execute()  # marks alpha.txt
    await dp.move_across()
    await dp.left.refresh()
    await dp.right.refresh()
    left_names = [e.entry.name for e in dp.left.entries]
    right_names = [e.entry.name for e in dp.right.entries]
    assert "alpha.txt" not in left_names
    assert "alpha.txt" in right_names
    dp.dispose()


@pytest.mark.asyncio
async def test_dual_delete_in_focused(tmp_path: Path) -> None:
    dp, _ = await _make_dual(tmp_path)
    dp.left.enter_multiselect_command.execute()
    dp.left.select_all_command.execute()
    await dp.delete_in_focused()
    assert dp.left.entries == ()
    dp.dispose()


@pytest.mark.asyncio
async def test_dual_focused_pane_property(tmp_path: Path) -> None:
    dp, _ = await _make_dual(tmp_path)
    assert dp.focused_pane is dp.left
    assert dp.other_pane is dp.right
    dp.switch_focus_command.execute()
    assert dp.focused_pane is dp.right
    assert dp.other_pane is dp.left
    dp.dispose()


@pytest.mark.asyncio
async def test_dual_copy_command_requires_marks(tmp_path: Path) -> None:
    dp, _ = await _make_dual(tmp_path)
    assert not dp.copy_across_command.can_execute()
    dp.left.enter_multiselect_command.execute()
    dp.left.select_all_command.execute()
    assert dp.copy_across_command.can_execute()
    dp.dispose()
