"""Tests for DualPaneVM."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator
from pathlib import Path
from typing import cast

import pytest
from vmx import NULL_DISPATCHER, MessageHub
from vmx.lifecycle.status import ConstructionStatus
from vmx.messages.protocols import Message

from aws_tui.demo.in_memory_fs import InMemoryFS
from aws_tui.domain.cross_fs import ConflictResolution
from aws_tui.domain.filesystem import NotFoundError, PathRef, ProviderError
from aws_tui.domain.transfer_journal import TransferJournal
from aws_tui.vm.file_manager.dual_pane_vm import DualPaneVM, FocusedPane, _drain_refresh_exception
from aws_tui.vm.file_manager.pane_vm import PaneVM
from aws_tui.vm.messages import (
    TransferCancelRequestedMessage,
    TransferProgressMessage,
    TransferState,
)


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
async def test_dual_setup_starts_both_panes_concurrently(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dp, _ = await _make_dual(tmp_path)
    left_started = asyncio.Event()
    right_started = asyncio.Event()
    release = asyncio.Event()

    async def _left_setup() -> None:
        left_started.set()
        await release.wait()

    async def _right_setup() -> None:
        right_started.set()
        await release.wait()

    monkeypatch.setattr(dp.left, "setup", _left_setup)
    monkeypatch.setattr(dp.right, "setup", _right_setup)
    setup_task = asyncio.create_task(dp.setup())
    try:
        await asyncio.wait_for(
            asyncio.gather(left_started.wait(), right_started.wait()),
            timeout=0.5,
        )
        release.set()
        await setup_task
    finally:
        release.set()
        setup_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await setup_task
        dp.dispose()


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
async def test_dual_copy_rejects_oversized_batch_before_journaling(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from aws_tui.vm.file_manager import dual_pane_vm

    dp, _ = await _make_dual(tmp_path)
    monkeypatch.setattr(dual_pane_vm, "_MAX_TRANSFER_BATCH_ENTRIES", 1)
    dp.left.enter_multiselect_command.execute()
    dp.left.select_all_command.execute()

    with pytest.raises(ProviderError, match="at most 1"):
        await dp.copy_across()

    assert dp._journal.find_unfinished() == []  # type: ignore[attr-defined]
    assert dp._cancel_events == {}  # type: ignore[attr-defined]
    dp.dispose()


@pytest.mark.asyncio
async def test_dual_queued_cancel_marks_journal_aborted_immediately(
    tmp_path: Path,
) -> None:
    """Cancelling a queued row must survive a crash before its turn runs."""
    dp, hub = await _make_dual(tmp_path)
    try:
        dp.left.enter_multiselect_command.execute()
        dp.left.select_all_command.execute()
        transfer_ids = dp._pre_register_pending(
            list(dp.left.marked_entries),
            dp.left,
            dp.right,
        )
        assert len(transfer_ids) == 2

        queued_id = transfer_ids[1][1]
        hub.send(TransferCancelRequestedMessage(transfer_id=queued_id))

        unfinished_ids = {
            entry.transfer_id
            for entry in dp._journal.find_unfinished()  # type: ignore[attr-defined]
        }
        assert queued_id not in unfinished_ids
        assert transfer_ids[0][1] in unfinished_ids
    finally:
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
async def test_dual_copy_across_outer_cancellation_aborts_current_journal(
    tmp_path: Path,
) -> None:
    """Cancelling the Textual worker must not leave a resumable phantom.

    ``copy_across`` marks the current transfer id as consumed before
    awaiting ``_run_one_transfer``. If the outer worker is cancelled
    while the copy task is running, that active journal still needs a
    terminal ABORTED marker; otherwise the next launch offers a resume
    for a transfer the user already cancelled by replacing/shutting down
    the worker.
    """
    from collections.abc import AsyncIterator as _AsyncIterator

    from aws_tui.domain.filesystem import EntryKind, FileEntry
    from aws_tui.domain.filesystem import PathRef as _PathRef

    class _BlockingProvider(InMemoryFS):
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
            if path.segments and path.segments[-1] == "big-file.bin":
                return self._entry
            return await super().stat(path)

        async def read_stream(  # type: ignore[override]
            self, _path: _PathRef, *, chunk_size: int = 8 * 1024 * 1024
        ) -> _AsyncIterator[bytes]:
            return self._blocking_gen()

        async def _blocking_gen(self) -> _AsyncIterator[bytes]:
            yield b""
            self.read_started.set()
            try:
                await self._block_event.wait()
            except asyncio.CancelledError:
                self.read_was_cancelled = True
                raise

    hub: MessageHub[Message] = cast("MessageHub[Message]", MessageHub())
    journal = TransferJournal(base_dir=tmp_path / "journal")
    left_provider = _BlockingProvider()
    left = PaneVM(provider=left_provider, hub=hub, dispatcher=NULL_DISPATCHER, id_prefix="left")
    right = PaneVM(provider=InMemoryFS(), hub=hub, dispatcher=NULL_DISPATCHER, id_prefix="right")
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
        copy_task = asyncio.create_task(dp.copy_across())
        await asyncio.wait_for(left_provider.read_started.wait(), timeout=2.0)

        copy_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(copy_task, timeout=2.0)

        assert left_provider.read_was_cancelled
        assert journal.find_unfinished() == []
    finally:
        dp.dispose()


@pytest.mark.asyncio
async def test_outer_cancellation_preserves_copy_that_already_completed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dp, hub = await _make_dual(tmp_path)
    progress_states: list[TransferState] = []
    finished: list[str] = []
    aborted: list[str] = []
    subscription = hub.messages.subscribe(
        on_next=lambda message: (
            progress_states.append(message.state)
            if isinstance(message, TransferProgressMessage)
            else None
        )
    )
    original_mark_finished = dp._journal.mark_finished
    original_mark_aborted = dp._journal.mark_aborted

    def _mark_finished(transfer_id: str) -> None:
        finished.append(transfer_id)
        original_mark_finished(transfer_id)

    def _mark_aborted(transfer_id: str) -> None:
        aborted.append(transfer_id)
        original_mark_aborted(transfer_id)

    async def _cancel_waiter_after_copy_settles(
        tasks: set[asyncio.Task[object]],
        *,
        return_when: object,
    ) -> tuple[set[asyncio.Task[object]], set[asyncio.Task[object]]]:
        del return_when
        while not any(task.done() for task in tasks):
            await asyncio.sleep(0)
        current = asyncio.current_task()
        assert current is not None
        current.cancel()
        await asyncio.sleep(0)
        raise AssertionError("task cancellation should interrupt the wait")

    monkeypatch.setattr(dp._journal, "mark_finished", _mark_finished)
    monkeypatch.setattr(dp._journal, "mark_aborted", _mark_aborted)
    monkeypatch.setattr(asyncio, "wait", _cancel_waiter_after_copy_settles)

    try:
        dp.left.enter_multiselect_command.execute()
        dp.left.select_all_command.execute()

        transfer = asyncio.create_task(dp.copy_across())
        with pytest.raises(asyncio.CancelledError):
            await transfer

        copied = b"".join(
            [chunk async for chunk in await dp.right.provider.read_stream(PathRef(("alpha.txt",)))]
        )
        assert copied == b"alpha-bytes"
        with pytest.raises(NotFoundError):
            await dp.right.provider.stat(PathRef(("beta.txt",)))
        assert TransferState.COMPLETED in progress_states
        assert progress_states[-1] is TransferState.CANCELLED
        assert len(finished) == 1
        assert len(aborted) == 1
        assert dp._journal.find_unfinished() == []
    finally:
        subscription.dispose()
        dp.dispose()


@pytest.mark.asyncio
async def test_transfer_repeated_cancellation_durably_drains_copy_task(tmp_path: Path) -> None:
    dp, _hub = await _make_dual(tmp_path)
    entry = dp.left.entries[0]
    transfer_id = dp._journal.begin(  # type: ignore[attr-defined]
        source_uri="local:///alpha.txt",
        destination_uri="local:///copy.txt",
        bytes_total=entry.entry.size,
    )
    dp._cancel_events[transfer_id] = asyncio.Event()  # type: ignore[attr-defined]
    operation_started = asyncio.Event()
    cleanup_started = asyncio.Event()
    release_cleanup = asyncio.Event()

    async def _blocking_operation(*_args: object, **_kwargs: object) -> bool:
        operation_started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cleanup_started.set()
            await release_cleanup.wait()
            raise

    transfer = asyncio.create_task(
        dp._run_one_transfer(  # type: ignore[attr-defined]
            operation=_blocking_operation,
            src_path=object(),
            dst_path=object(),
            on_conflict=ConflictResolution.OVERWRITE,
            transfer_id=transfer_id,
            entry=entry,
        )
    )
    await operation_started.wait()

    transfer.cancel()
    await cleanup_started.wait()
    transfer.cancel()
    await asyncio.sleep(0)
    assert not transfer.done()
    release_cleanup.set()
    with pytest.raises(asyncio.CancelledError):
        await transfer

    assert dp._journal.find_unfinished() == []  # type: ignore[attr-defined]
    dp.dispose()


@pytest.mark.asyncio
async def test_user_cancel_then_worker_cancel_durably_drains_copy_task(tmp_path: Path) -> None:
    dp, _hub = await _make_dual(tmp_path)
    entry = dp.left.entries[0]
    transfer_id = dp._journal.begin(  # type: ignore[attr-defined]
        source_uri="local:///alpha.txt",
        destination_uri="local:///copy.txt",
        bytes_total=entry.entry.size,
    )
    cancel_event = asyncio.Event()
    dp._cancel_events[transfer_id] = cancel_event  # type: ignore[attr-defined]
    operation_started = asyncio.Event()
    cleanup_started = asyncio.Event()
    release_cleanup = asyncio.Event()

    async def _blocking_operation(*_args: object, **_kwargs: object) -> bool:
        operation_started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cleanup_started.set()
            await release_cleanup.wait()
            raise

    transfer = asyncio.create_task(
        dp._run_one_transfer(  # type: ignore[attr-defined]
            operation=_blocking_operation,
            src_path=object(),
            dst_path=object(),
            on_conflict=ConflictResolution.OVERWRITE,
            transfer_id=transfer_id,
            entry=entry,
        )
    )
    await operation_started.wait()
    cancel_event.set()
    await cleanup_started.wait()

    transfer.cancel()
    await asyncio.sleep(0)
    assert not transfer.done()
    release_cleanup.set()
    with pytest.raises(asyncio.CancelledError):
        await transfer

    assert dp._journal.find_unfinished() == []  # type: ignore[attr-defined]
    dp.dispose()


@pytest.mark.asyncio
async def test_dual_dispose_cancels_owned_refresh_tasks(tmp_path: Path) -> None:
    """Post-transfer refresh tasks are owned by the DualPaneVM lifecycle."""
    dp, _hub = await _make_dual(tmp_path)
    refresh_started = asyncio.Event()
    refresh_cancelled = asyncio.Event()

    async def _blocking_refresh() -> None:
        refresh_started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            refresh_cancelled.set()
            raise

    dp.right.refresh = _blocking_refresh  # type: ignore[method-assign]

    dp._schedule_owned_refresh(dp.right)
    await asyncio.wait_for(refresh_started.wait(), timeout=2.0)
    dp.dispose()
    await asyncio.wait_for(refresh_cancelled.wait(), timeout=2.0)


@pytest.mark.asyncio
async def test_dual_shutdown_cancels_and_drains_owned_refresh_tasks(tmp_path: Path) -> None:
    dp, _hub = await _make_dual(tmp_path)
    refresh_started = asyncio.Event()
    refresh_cancelled = asyncio.Event()
    release_cleanup = asyncio.Event()

    async def _blocking_refresh() -> None:
        refresh_started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            refresh_cancelled.set()
            await release_cleanup.wait()
            raise

    dp.right.refresh = _blocking_refresh  # type: ignore[method-assign]
    dp._schedule_owned_refresh(dp.right)
    await refresh_started.wait()

    shutdown = asyncio.create_task(dp.shutdown())
    await refresh_cancelled.wait()
    assert not shutdown.done()
    release_cleanup.set()
    await shutdown

    assert not dp._refresh_tasks  # type: ignore[attr-defined]
    assert dp._schedule_owned_refresh(dp.right) is None
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


@pytest.mark.asyncio
async def test_failed_background_refresh_is_logged_not_swallowed(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A background refresh that dies must leave a log line.

    `_drain_refresh_exception` retrieves the exception to silence asyncio's
    "never retrieved" warning. Its own comment records why the retrieval alone
    was not enough: discarding the exception left a refresh that died on expired
    credentials or a dropped connection with no toast and no log line, so the
    pane kept showing a stale listing. Nothing pinned the logging half, so that
    fix could be undone silently — which is how it was lost the first time.
    """

    async def boom() -> None:
        raise ProviderError("credentials expired")

    task = asyncio.create_task(boom())
    with contextlib.suppress(ProviderError):
        await task

    with caplog.at_level(logging.WARNING, logger="aws_tui.vm.file_manager.dual_pane_vm"):
        _drain_refresh_exception(task)

    assert [record.message for record in caplog.records] == ["pane background refresh failed"]
    assert caplog.records[0].error_type == "ProviderError"  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_cancelled_background_refresh_is_not_logged(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Cancellation is ordinary teardown, not a failure worth a log line."""

    async def forever() -> None:
        await asyncio.Event().wait()

    task = asyncio.create_task(forever())
    await asyncio.sleep(0)
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task

    with caplog.at_level(logging.WARNING, logger="aws_tui.vm.file_manager.dual_pane_vm"):
        _drain_refresh_exception(task)

    assert caplog.records == []
