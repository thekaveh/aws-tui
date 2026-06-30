"""DualPaneVM — Norton Commander left/right facade.

Holds two :class:`PaneVM` instances and orchestrates cross-pane
operations (copy / move / delete-in-focused). Copy and move route
through M2's :class:`CrossFsCopy` / :class:`CrossFsMove`; per-file
progress is bridged to :class:`TransferProgressMessage` on the hub so
:class:`TransfersVM` and the chrome status bar can react.

The facade does not subclass VMx's ``AggregateVM2`` — its components are
facades (which AggregateVMN cannot wrap). We mirror the pattern used by
``ChromeVM``: hold a marker :class:`ComponentVM` named ``"dual_pane"``
plus the two child facades, and forward lifecycle calls explicitly.
"""

from __future__ import annotations

import asyncio
import contextlib
from enum import StrEnum
from typing import TYPE_CHECKING

from vmx import ComponentVM, Message, MessageHub, PropertyChangedMessage, RelayCommand
from vmx.lifecycle.status import ConstructionStatus
from vmx.services.dispatcher import Dispatcher

from aws_tui.domain.cross_fs import ConflictResolution, CrossFsCopy, CrossFsMove
from aws_tui.domain.filesystem import TransferProgress
from aws_tui.domain.transfer_journal import TransferJournal
from aws_tui.vm.file_manager.entry_vm import EntryVM
from aws_tui.vm.file_manager.pane_vm import PaneVM
from aws_tui.vm.messages import (
    TransferCancelRequestedMessage,
    TransferProgressMessage,
    TransferState,
)

if TYPE_CHECKING:
    from reactivex.abc import DisposableBase


class FocusedPane(StrEnum):
    LEFT = "left"
    RIGHT = "right"


def _pane_uri(pane: PaneVM, leaf: str) -> str:
    """Build a stable scheme-prefixed label for transfer source/
    destination identifiers.

    The scheme prefix (``pane.path_protocol``, e.g. ``"s3:"`` for an
    S3 pane, ``""`` for local) is preserved so downstream consumers —
    notably ``TransfersVM._infer_direction`` — can classify the
    transfer as upload / download / s3-copy / local-copy without
    re-parsing the underlying provider type.
    """
    # ``rstrip("/")`` makes ``base`` empty for root, never ``"/"`` —
    # so a single template covers both root and non-root paths.
    base = pane.path.as_posix().rstrip("/")
    body = f"{base}/{leaf}"
    if pane.path_protocol:
        return f"{pane.path_protocol}/{body}"
    return body


class DualPaneVM:
    """Two-pane file-manager facade."""

    def __init__(
        self,
        *,
        left: PaneVM,
        right: PaneVM,
        hub: MessageHub[Message],
        dispatcher: Dispatcher,
        transfer_journal: TransferJournal,
    ) -> None:
        self._hub: MessageHub[Message] = hub
        self._left: PaneVM = left
        self._right: PaneVM = right
        self._journal: TransferJournal = transfer_journal
        self._focused: FocusedPane = FocusedPane.LEFT

        # Per-transfer cancellation events. Populated by ``copy_across`` /
        # ``move_across`` when each transfer is queued; the hub subscription
        # for ``TransferCancelRequestedMessage`` sets the event so the run
        # loop's ``asyncio.wait`` race interrupts the in-flight copy task.
        self._cancel_events: dict[str, asyncio.Event] = {}
        self._cancel_sub: DisposableBase | None = None

        self._inner: ComponentVM = (
            ComponentVM.builder().name("dual_pane").services(hub, dispatcher).build()
        )

        # ── Commands ────────────────────────────────────────────────────────
        self._switch_focus_command: RelayCommand = (
            RelayCommand.builder().task(self._switch_focus).build()
        )
        # copy/move/delete are async operations; the relay command bridges
        # to a hub signal so the caller (UI/keymap router) can schedule the
        # awaited work. Direct programmatic access goes via the async
        # methods below.
        self._copy_across_command: RelayCommand = (
            RelayCommand.builder()
            .predicate(lambda: bool(self._focused_pane().marked_entries))
            .task(self._signal_copy_requested)
            .build()
        )
        self._move_across_command: RelayCommand = (
            RelayCommand.builder()
            .predicate(lambda: bool(self._focused_pane().marked_entries))
            .task(self._signal_move_requested)
            .build()
        )
        self._delete_in_focused_command: RelayCommand = (
            RelayCommand.builder()
            .predicate(lambda: bool(self._focused_pane().marked_entries))
            .task(self._signal_delete_requested)
            .build()
        )

    # ── Children accessors ──────────────────────────────────────────────────

    @property
    def left(self) -> PaneVM:
        return self._left

    @property
    def right(self) -> PaneVM:
        return self._right

    @property
    def focused(self) -> FocusedPane:
        return self._focused

    @property
    def focused_pane(self) -> PaneVM:
        return self._focused_pane()

    @property
    def other_pane(self) -> PaneVM:
        return self._right if self._focused is FocusedPane.LEFT else self._left

    @property
    def switch_focus_command(self) -> RelayCommand:
        return self._switch_focus_command

    @property
    def copy_across_command(self) -> RelayCommand:
        return self._copy_across_command

    @property
    def move_across_command(self) -> RelayCommand:
        return self._move_across_command

    @property
    def delete_in_focused_command(self) -> RelayCommand:
        return self._delete_in_focused_command

    @property
    def status(self) -> ConstructionStatus:
        return self._inner.status

    @property
    def is_constructed(self) -> bool:
        return self._inner.is_constructed

    @property
    def name(self) -> str:
        return self._inner.name

    # ── Lifecycle ───────────────────────────────────────────────────────────

    def construct(self) -> None:
        self._inner.construct()
        self._left.construct()
        self._right.construct()
        # Subscribe AFTER children construct so any cancel message that
        # somehow arrives mid-construction doesn't fire before the
        # children are ready to handle the subsequent state shuffle.
        self._cancel_sub = self._hub.messages.subscribe(on_next=self._on_hub_message)

    def destruct(self) -> None:
        self._right.destruct()
        self._left.destruct()
        self._inner.destruct()

    def dispose(self) -> None:
        if self._cancel_sub is not None:
            self._cancel_sub.dispose()
            self._cancel_sub = None
        self._switch_focus_command.dispose()
        self._copy_across_command.dispose()
        self._move_across_command.dispose()
        self._delete_in_focused_command.dispose()
        self._right.dispose()
        self._left.dispose()
        self._inner.dispose()

    def _on_hub_message(self, msg: object) -> None:
        """Hub subscriber for cancel requests.

        Sets the per-transfer cancel event so the run loop's
        ``asyncio.wait`` race wakes up and interrupts the active copy
        task. The TransferVM has already transitioned to CANCELLED for
        UI feedback (see ``TransferVM._cancel``); this is the
        asynchronous "actually stop the bytes" signal.
        """
        if not isinstance(msg, TransferCancelRequestedMessage):
            return
        event = self._cancel_events.get(msg.transfer_id)
        if event is not None and not event.is_set():
            event.set()

    async def setup(self) -> None:
        await self._left.setup()
        await self._right.setup()

    # ── Async cross-pane operations ────────────────────────────────────────

    async def copy_across(
        self, *, on_conflict: ConflictResolution = ConflictResolution.ERROR
    ) -> None:
        """Copy every marked entry from the focused pane to the other one."""
        src_pane = self.focused_pane
        dst_pane = self.other_pane
        targets = list(src_pane.marked_entries)
        if not targets:
            return
        copier = CrossFsCopy(source=src_pane.provider, destination=dst_pane.provider)
        transfer_ids = self._pre_register_pending(targets, src_pane, dst_pane)
        # Track which ``transfer_id`` the loop has actually consumed
        # (success, fail, or user-cancel). If an entry's
        # ``_run_one_transfer`` raises, the loop exits early and the
        # remaining ids never get a terminal marker — their PENDING
        # journal files would otherwise outlive the session and
        # eventually fan out into the deferred resume modal as
        # phantom restartable transfers.
        consumed: set[str] = set()
        try:
            for entry, transfer_id in transfer_ids:
                src_path = src_pane.path.join(entry.entry.name)
                dst_path = dst_pane.path.join(entry.entry.name)
                # Mark BEFORE awaiting so a raise from
                # ``_run_one_transfer`` (which has already
                # ``mark_aborted``-ed its own transfer's journal
                # before re-raising) still counts this id as
                # consumed and we don't re-mark it in the finally.
                consumed.add(transfer_id)
                completed = await self._run_one_transfer(
                    operation=copier.copy,
                    src_path=src_path,
                    dst_path=dst_path,
                    on_conflict=on_conflict,
                    transfer_id=transfer_id,
                    entry=entry,
                )
                if completed:
                    self._hub.send(
                        TransferProgressMessage(
                            transfer_id=transfer_id,
                            bytes_transferred=entry.entry.size or 0,
                            bytes_total=entry.entry.size,
                            state=TransferState.COMPLETED,
                        )
                    )
                    self._journal.mark_finished(transfer_id)
        finally:
            for entry, transfer_id in transfer_ids:
                self._cancel_events.pop(transfer_id, None)
                if transfer_id not in consumed:
                    # Loop never reached this entry — mark its
                    # journal file ABORTED so ``find_unfinished``
                    # doesn't surface it on next launch, AND publish
                    # a terminal TransferProgressMessage so the
                    # in-memory TransferVM the pre-register placed
                    # in PENDING leaves the active set (otherwise
                    # the status-bar aggregate + cancel_all predicate
                    # stay "active" with phantom queued rows visible
                    # in the transfers overlay).
                    self._journal.mark_aborted(transfer_id)
                    self._hub.send(
                        TransferProgressMessage(
                            transfer_id=transfer_id,
                            bytes_transferred=0,
                            bytes_total=entry.entry.size,
                            state=TransferState.CANCELLED,
                        )
                    )
            # Refresh the destination pane INSIDE the finally so
            # the user sees the partial result even when the loop
            # raised mid-batch. Files 1..K-1 are physically present
            # on disk; without this the pane would still show the
            # pre-batch listing and a retry with
            # ``on_conflict=OVERWRITE`` would silently clobber, or
            # ERROR would hit EEXIST on the partial set. The
            # journal cleanup above keeps state coherent; this
            # makes the UI match. Suppressed so a refresh failure
            # can't mask the original copy exception.
            with contextlib.suppress(Exception):
                await dst_pane.refresh()

    async def move_across(
        self, *, on_conflict: ConflictResolution = ConflictResolution.ERROR
    ) -> None:
        """Copy then delete each marked entry."""
        src_pane = self.focused_pane
        dst_pane = self.other_pane
        targets = list(src_pane.marked_entries)
        if not targets:
            return
        mover = CrossFsMove(source=src_pane.provider, destination=dst_pane.provider)
        transfer_ids = self._pre_register_pending(targets, src_pane, dst_pane)
        # See ``copy_across`` for the rationale on the ``consumed``
        # set — mid-batch failure must not strand PENDING journal
        # entries for ids the loop never reached.
        consumed: set[str] = set()
        try:
            for entry, transfer_id in transfer_ids:
                src_path = src_pane.path.join(entry.entry.name)
                dst_path = dst_pane.path.join(entry.entry.name)
                # See ``copy_across`` for why consumed.add precedes the await.
                consumed.add(transfer_id)
                completed = await self._run_one_transfer(
                    operation=mover.move,
                    src_path=src_path,
                    dst_path=dst_path,
                    on_conflict=on_conflict,
                    transfer_id=transfer_id,
                    entry=entry,
                )
                if completed:
                    self._hub.send(
                        TransferProgressMessage(
                            transfer_id=transfer_id,
                            bytes_transferred=entry.entry.size or 0,
                            bytes_total=entry.entry.size,
                            state=TransferState.COMPLETED,
                        )
                    )
                    self._journal.mark_finished(transfer_id)
        finally:
            for entry, transfer_id in transfer_ids:
                self._cancel_events.pop(transfer_id, None)
                if transfer_id not in consumed:
                    # See ``copy_across`` for the parity rationale —
                    # publish CANCELLED so the in-memory TransferVM
                    # doesn't stay in PENDING forever and inflate the
                    # status-bar / transfers-overlay active count.
                    self._journal.mark_aborted(transfer_id)
                    self._hub.send(
                        TransferProgressMessage(
                            transfer_id=transfer_id,
                            bytes_transferred=0,
                            bytes_total=entry.entry.size,
                            state=TransferState.CANCELLED,
                        )
                    )
            # Refresh BOTH panes inside the finally — see
            # ``copy_across`` for the rationale. Move is even more
            # sensitive: files 1..K-1 are both copied AND deleted
            # from src, so the source pane must redraw or the user
            # sees ghost rows for entries that are gone.
            with contextlib.suppress(Exception):
                await src_pane.refresh()
            with contextlib.suppress(Exception):
                await dst_pane.refresh()

    async def delete_in_focused(self) -> None:
        """Delete every marked entry in the focused pane."""
        await self.focused_pane.delete_marked()

    # ── Transfer-batch helpers ─────────────────────────────────────────────

    def _pre_register_pending(
        self,
        targets: list[EntryVM],
        src_pane: PaneVM,
        dst_pane: PaneVM,
    ) -> list[tuple[EntryVM, str]]:
        """Pre-register every queued transfer as PENDING + create the
        per-transfer cancel event before the run loop starts.

        Without the pre-register, only the currently-running transfer
        (and any lingering recently-finished ones) is visible in the
        overlay — the user can't see how many more are queued. The
        journal entries are also created upfront so a crash mid-batch
        records all-of-them as unfinished (not just the one being
        copied).
        """
        transfer_ids: list[tuple[EntryVM, str]] = []
        try:
            for entry in targets:
                src_uri = _pane_uri(src_pane, entry.entry.name)
                dst_uri = _pane_uri(dst_pane, entry.entry.name)
                transfer_id = self._journal.begin(
                    source_uri=src_uri,
                    destination_uri=dst_uri,
                    bytes_total=entry.entry.size,
                )
                transfer_ids.append((entry, transfer_id))
                # An asyncio.Event per transfer — set by the hub
                # subscriber when the user clicks the cancel chip; raced
                # against the copy task inside ``_run_one_transfer``.
                self._cancel_events[transfer_id] = asyncio.Event()
                self._hub.send(
                    TransferProgressMessage(
                        transfer_id=transfer_id,
                        bytes_transferred=0,
                        bytes_total=entry.entry.size,
                        state=TransferState.PENDING,
                        source_label=src_uri,
                        destination_label=dst_uri,
                    )
                )
        except Exception:
            # Mid-loop failure (e.g. ``_journal.begin`` hits disk-full
            # or permission-denied, ``_hub.send`` raises from a
            # subscriber) leaves entries 1..K-1 with THREE half-done
            # side effects that the caller's ``finally`` would
            # otherwise never reach (the caller iterates the RETURNED
            # ``transfer_ids``, which never materializes when this
            # method raises):
            #   1. cancel_event registrations (memory only)
            #   2. journal files in PENDING state (resume modal will
            #      surface them on next launch as phantom resumable
            #      transfers)
            #   3. in-memory TransferVMs in PENDING (status-bar
            #      aggregate, transfers overlay, cancel_all predicate
            #      all read off these)
            # Reap all three so a single mid-batch raise can't
            # accumulate phantom queued transfers across the session.
            # Symmetric with the round-19 cleanup in copy_across /
            # move_across's finally.
            for entry, transfer_id in transfer_ids:
                self._cancel_events.pop(transfer_id, None)
                with contextlib.suppress(Exception):
                    self._journal.mark_aborted(transfer_id)
                with contextlib.suppress(Exception):
                    self._hub.send(
                        TransferProgressMessage(
                            transfer_id=transfer_id,
                            bytes_transferred=0,
                            bytes_total=entry.entry.size,
                            state=TransferState.CANCELLED,
                        )
                    )
            raise
        return transfer_ids

    async def _run_one_transfer(
        self,
        *,
        operation: object,  # async callable: (src, dst, *, progress=, on_conflict=) -> Awaitable
        src_path: object,
        dst_path: object,
        on_conflict: ConflictResolution,
        transfer_id: str,
        entry: EntryVM,
    ) -> bool:
        """Run one transfer, racing it against ``self._cancel_events[transfer_id]``.

        Returns ``True`` if the transfer ran to completion (caller is
        responsible for sending the COMPLETED message + marking the
        journal finished). Returns ``False`` if the transfer was
        cancelled (this method has already called ``mark_aborted`` on
        the journal). Re-raises on a real error — the caller must
        decide whether to continue the batch or propagate.
        """
        # ``_pre_register_pending`` unconditionally creates a
        # per-transfer cancel event before this method runs, so the
        # event is guaranteed to be present. We still narrow with an
        # ``assert`` for the type checker; the previous fallback
        # branch was dead code (asserted by the comment that used to
        # live there) and has been removed.
        cancel_event = self._cancel_events.get(transfer_id)
        assert cancel_event is not None, (
            f"cancel event missing for {transfer_id!r} — _pre_register_pending should install it"
        )

        # Pre-cancelled-while-PENDING fast path: the user clicked
        # cancel before this transfer got its turn. Skip the work
        # entirely, mark the journal aborted, move on.
        if cancel_event.is_set():
            self._journal.mark_aborted(transfer_id)
            return False

        def _progress(p: TransferProgress, *, _tid: str = transfer_id) -> None:
            self._hub.send(
                TransferProgressMessage(
                    transfer_id=_tid,
                    bytes_transferred=p.bytes_transferred,
                    bytes_total=p.bytes_total,
                    state=TransferState.RUNNING,
                )
            )

        # Wrap the copy in a task so we can race it against the cancel
        # event. ``asyncio.create_task`` schedules it on the current
        # loop; we keep a reference so ``cancel()`` actually reaches
        # it on the cancel path.
        copy_task: asyncio.Task[None] = asyncio.create_task(
            operation(  # type: ignore[operator]
                src_path,
                dst_path,
                progress=_progress,
                on_conflict=on_conflict,
            )
        )

        cancel_task: asyncio.Task[bool] = asyncio.create_task(cancel_event.wait())
        try:
            await asyncio.wait(
                {copy_task, cancel_task},
                return_when=asyncio.FIRST_COMPLETED,
            )
        except BaseException:
            # Outer-worker cancellation (a second ``c``/``d`` press
            # cancels the prior ``run_worker(group="transfer-ops")``
            # via exclusive=True; Settings switch; shutdown). Without
            # this branch the asyncio.wait raises CancelledError, the
            # finally below only cleans up cancel_task, and copy_task
            # is left running in the background — a multi-MB S3
            # upload would keep writing bytes after the user-facing
            # copy command has logically aborted. Cancel + await
            # copy_task too, then re-raise so the caller's own
            # cancellation chain stays intact.
            if not copy_task.done():
                copy_task.cancel()
                with contextlib.suppress(Exception, asyncio.CancelledError):
                    await copy_task
            if not cancel_task.done():
                cancel_task.cancel()
                with contextlib.suppress(Exception, asyncio.CancelledError):
                    await cancel_task
            raise
        finally:
            if not cancel_task.done():
                cancel_task.cancel()
                with contextlib.suppress(Exception, asyncio.CancelledError):
                    await cancel_task

        # Prioritise copy_task done over cancel_task — handles the
        # race where the copy completed naturally a microsecond
        # before the user-cancel signal arrived. Treating it as
        # cancelled would wrongly mark a successful copy as aborted.
        if copy_task.done():
            exc = copy_task.exception()
            if exc is not None:
                self._hub.send(
                    TransferProgressMessage(
                        transfer_id=transfer_id,
                        bytes_transferred=0,
                        bytes_total=entry.entry.size,
                        state=TransferState.FAILED,
                    )
                )
                self._journal.mark_aborted(transfer_id)
                raise exc
            return True

        # Cancel won the race. Kill the copy task so the underlying
        # provider (aioboto3 client, file write) bails at its next
        # await point. Mark journal aborted. The TransferVM has
        # already transitioned to CANCELLED via the immediate
        # cancel_command path in TransferVM._cancel, so no progress
        # message is needed here — the overlay already shows
        # ``⊘ cancelled`` from the moment the user clicked.
        copy_task.cancel()
        with contextlib.suppress(Exception, asyncio.CancelledError):
            await copy_task
        self._journal.mark_aborted(transfer_id)
        return False

    # ── Internal ────────────────────────────────────────────────────────────

    def _focused_pane(self) -> PaneVM:
        return self._left if self._focused is FocusedPane.LEFT else self._right

    def _switch_focus(self) -> None:
        self._focused = FocusedPane.RIGHT if self._focused is FocusedPane.LEFT else FocusedPane.LEFT
        self._hub.send(PropertyChangedMessage.create(self, self._inner.name, "focused"))

    def _signal_copy_requested(self) -> None:
        self._hub.send(PropertyChangedMessage.create(self, self._inner.name, "copy_requested"))

    def _signal_move_requested(self) -> None:
        self._hub.send(PropertyChangedMessage.create(self, self._inner.name, "move_requested"))

    def _signal_delete_requested(self) -> None:
        self._hub.send(PropertyChangedMessage.create(self, self._inner.name, "delete_requested"))


__all__ = ["DualPaneVM", "FocusedPane"]
