"""DualPaneVM — Norton Commander left/right facade.

Holds two :class:`PaneVM` instances and orchestrates cross-pane
operations (copy / move / delete-in-focused). Copy and move route
through M2's :class:`CrossFsCopy` / :class:`CrossFsMove`; per-file
progress is bridged to :class:`TransferProgressMessage` on the hub so
:class:`TransfersVM` and the transfers overlay can react.

The facade does not subclass VMx's ``AggregateVM2`` — its components are
facades (which AggregateVMN cannot wrap). We mirror the pattern used by
``ChromeVM``: hold a marker :class:`ComponentVM` named ``"dual_pane"``
plus the two child facades, and forward lifecycle calls explicitly.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from enum import StrEnum
from typing import TYPE_CHECKING

import reactivex as rx
from vmx import ComponentVM, Message, MessageHub, PropertyChangedMessage, RelayCommand
from vmx.lifecycle.status import ConstructionStatus
from vmx.services.dispatcher import Dispatcher

from aws_tui.domain.cross_fs import ConflictResolution, CrossFsCopy, CrossFsMove
from aws_tui.domain.filesystem import EntryKind, ProviderError
from aws_tui.domain.transfer_history import TransferHistoryDescriptor
from aws_tui.domain.transfer_journal import TransferJournal
from aws_tui.vm._observable import ObserverSafeSubject, send_value_free
from aws_tui.vm.file_manager.entry_vm import EntryVM
from aws_tui.vm.file_manager.pane_vm import PaneVM
from aws_tui.vm.file_manager.transfer_runtime import TransferRuntime
from aws_tui.vm.messages import (
    TransferCancelRequestedMessage,
    TransferState,
)

if TYPE_CHECKING:
    from reactivex.abc import DisposableBase


_MAX_TRANSFER_BATCH_ENTRIES = 1_000


class FocusedPane(StrEnum):
    LEFT = "left"
    RIGHT = "right"


_logger = logging.getLogger(__name__)


def _drain_refresh_exception(task: asyncio.Task[None]) -> None:
    if task.cancelled():
        return
    exception = task.exception()
    if exception is None:
        return
    # Retrieving the exception silences asyncio's "never retrieved" warning;
    # discarding it left a background refresh that died on expired credentials
    # or a dropped connection with NO toast and NO log line, so the pane kept
    # showing a stale listing and the user saw files that no longer exist.
    _logger.warning(
        "pane background refresh failed",
        extra={"error_type": type(exception).__name__, "error": str(exception)},
    )


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
        transfer_runtime: TransferRuntime | None = None,
    ) -> None:
        self._hub: MessageHub[Message] = hub
        self._left: PaneVM = left
        self._right: PaneVM = right
        self._journal: TransferJournal = transfer_journal
        self.transfer_runtime = transfer_runtime or TransferRuntime(transfer_journal, hub)
        self._owns_runtime = transfer_runtime is None
        self._focused: FocusedPane = FocusedPane.LEFT

        # Per-transfer cancellation events. Populated by ``copy_across`` /
        # ``move_across`` when each transfer is queued; the hub subscription
        # for ``TransferCancelRequestedMessage`` sets the event so the run
        # loop's ``asyncio.wait`` race interrupts the in-flight copy task.
        self._cancel_events = self.transfer_runtime.cancel_events
        self._active_transfer_ids: set[str] = set()
        self._cancel_sub: DisposableBase | None = None
        self._refresh_tasks: set[asyncio.Task[None]] = set()
        self._shutdown_started = False
        self._disposed = False

        # Per-VM Observable (round-3 §9.bis.11 / PR #103 retirement path):
        # fires the name of the property that just changed, scoped to THIS
        # facade. :class:`aws_tui.ui.widgets.dual_pane.DualPane` binds here
        # instead of filtering the shared ``MessageHub`` by
        # ``sender_object`` -- the hub has no sender-keyed routing, so that
        # filter woke the widget for every message in the app to learn about
        # one boolean.
        self._on_property_changed: ObserverSafeSubject[str] = ObserverSafeSubject[str]()

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
    def on_property_changed(self) -> rx.Observable[str]:
        """Per-VM-instance Observable scoped to THIS facade.

        The binding surface for :class:`~aws_tui.ui.widgets.dual_pane.DualPane`.
        Round-3 / PR #103 retirement path: a subscriber here hears only this
        facade's own property changes.
        """
        return self._on_property_changed

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
        # ``if … is None`` guard makes construct→destruct→construct
        # cycles safe: each construct must subscribe exactly once.
        # Mirrors the other hub-subscribing VMs' symmetric
        # construct/destruct contracts.
        if self._cancel_sub is None:
            self._cancel_sub = self._hub.messages.subscribe(on_next=self._on_hub_message)

    def destruct(self) -> None:
        # Release the hub subscription FIRST — without this it
        # outlives destruct (the hub keeps the bound method alive,
        # pinning the DualPaneVM + both panes + _cancel_events
        # registry). Symmetric with ``construct``'s subscribe.
        if self._cancel_sub is not None:
            self._cancel_sub.dispose()
            self._cancel_sub = None
        self._right.destruct()
        self._left.destruct()
        self._inner.destruct()

    def dispose(self) -> None:
        if self._disposed:
            return
        self._disposed = True
        self._shutdown_started = True
        self._cancel_detached_refreshes()
        if self._cancel_sub is not None:
            self._cancel_sub.dispose()
            self._cancel_sub = None
        if self._owns_runtime:
            self.transfer_runtime.dispose()
        self._switch_focus_command.dispose()
        self._copy_across_command.dispose()
        self._move_across_command.dispose()
        self._delete_in_focused_command.dispose()
        self._right.dispose()
        self._left.dispose()
        # Complete and tear the subject down BEFORE the inner VM, matching
        # ``PaneVM.dispose`` / ``EntryVM.dispose``: a bound widget that is
        # still mounted must be told the stream ended rather than left
        # holding a live observer on a disposed view model.
        self._on_property_changed.on_completed()
        self._on_property_changed.dispose()
        self._inner.dispose()

    async def shutdown(self) -> None:
        """Cancel and durably drain detached pane refreshes."""
        self._shutdown_started = True
        await self.transfer_runtime.shutdown()
        await self._cancel_and_drain_refreshes()

    def _schedule_owned_refresh(self, pane: PaneVM) -> asyncio.Task[None] | None:
        """Schedule ``pane.refresh()`` and tie it to this VM's lifecycle.

        Copy/move cleanup detaches refreshes so worker cancellation does
        not strand panes in LOADING. Detached still needs ownership:
        content swaps dispose this VM, so stale refreshes must be cancelled
        before they emit pane-state updates from old providers.
        """
        if self._shutdown_started:
            return None
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return None  # No running loop (sync-driven tests); caller can refresh manually.
        task = loop.create_task(pane.refresh())
        self._refresh_tasks.add(task)

        def _done(done: asyncio.Task[None]) -> None:
            self._refresh_tasks.discard(done)
            _drain_refresh_exception(done)

        task.add_done_callback(_done)
        return task

    async def _refresh_after_operation(self, *panes: PaneVM) -> None:
        """Refresh panes before returning while surviving caller cancellation."""
        tasks = [task for pane in panes if (task := self._schedule_owned_refresh(pane))]
        if tasks:
            await asyncio.shield(asyncio.gather(*tasks))

    def _cancel_detached_refreshes(self) -> None:
        for task in tuple(self._refresh_tasks):
            task.cancel()
        self._refresh_tasks.clear()

    async def _cancel_and_drain_refreshes(self) -> None:
        tasks = tuple(self._refresh_tasks)
        current = asyncio.current_task()
        cancellation_count = current.cancelling() if current is not None else 0
        cancelled = False
        for task in tasks:
            task.cancel()
        for task in tasks:
            while not task.done():
                try:
                    await asyncio.shield(task)
                except asyncio.CancelledError:
                    current_count = current.cancelling() if current is not None else 0
                    if current_count > cancellation_count:
                        cancelled = True
                        cancellation_count = current_count
                    continue
            if not task.cancelled():
                with contextlib.suppress(Exception):
                    task.result()
            self._refresh_tasks.discard(task)
        if cancelled:
            raise asyncio.CancelledError

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
        async with asyncio.TaskGroup() as tasks:
            tasks.create_task(self._left.setup())
            tasks.create_task(self._right.setup())

    def set_focused(self, pane: FocusedPane) -> None:
        """Explicitly set the active pane."""
        if self._focused is pane:
            return
        self._focused = pane
        self._notify("focused")

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
        source_provider, destination_provider = src_pane.provider, dst_pane.provider
        src_base, dst_base = src_pane.path, dst_pane.path
        transfer_ids = await self._pre_register_pending(
            targets, src_pane, dst_pane, operation="copy"
        )
        # Bind both directories ONCE, next to the provider pair already
        # snapshotted above. Re-reading ``*_pane.path`` per iteration lets a
        # navigation between two transfers redirect the rest of the batch, and
        # ``_pre_register_pending`` has already recorded the ORIGINAL paths in
        # the journal and the transfers overlay — so the record would name a
        # destination the bytes never reached.
        # Track which ``transfer_id`` the loop has actually consumed
        # (success, fail, or user-cancel). If an entry's
        # ``_run_one_transfer`` raises, the loop exits early and the
        # remaining ids never get a terminal marker — their PENDING
        # journal files would otherwise outlive the session and
        # survive as phantom interrupted-transfer records.
        consumed: set[str] = set()
        try:
            for entry, transfer_id in transfer_ids:
                src_path = src_base.join(entry.entry.name)
                dst_path = dst_base.join(entry.entry.name)
                # Mark BEFORE awaiting so a raise from
                # ``_run_one_transfer`` (which has already
                # ``mark_aborted``-ed its own transfer's journal
                # before re-raising) still counts this id as
                # consumed and we don't re-mark it in the finally.
                consumed.add(transfer_id)
                self._active_transfer_ids.add(transfer_id)
                try:
                    before_publication, file_progress = self.transfer_runtime.copy_hooks(
                        transfer_id,
                        src_path,
                        directory=entry.entry.kind is EntryKind.DIRECTORY,
                    )
                    copier = CrossFsCopy(
                        source=source_provider,
                        destination=destination_provider,
                        before_publication=before_publication,
                        file_progress_transform=file_progress,
                    )
                    completed = await self._run_one_transfer(
                        operation=copier.copy,
                        src_path=src_path,
                        dst_path=dst_path,
                        on_conflict=on_conflict,
                        transfer_id=transfer_id,
                        entry=entry,
                    )
                    if completed:
                        await self._mark_transfer_completed(transfer_id, entry)
                finally:
                    self._active_transfer_ids.discard(transfer_id)
        finally:
            await self.transfer_runtime.settle_batch(
                [(tid, entry.entry.size) for entry, tid in transfer_ids],
                consumed,
            )
            # Refresh the destination pane INSIDE the finally so
            # the user sees the partial result even when the loop
            # raised mid-batch. Files 1..K-1 are physically present
            # on disk; without this the pane would still show the
            # pre-batch listing and a retry with
            # ``on_conflict=OVERWRITE`` would silently clobber, or
            # ERROR would hit EEXIST on the partial set.
            #
            # The owned task survives outer-worker cancellation, while
            # normal callers do not return before the pane reflects the
            # completed copy.
            await self._refresh_after_operation(dst_pane)

    async def move_across(
        self, *, on_conflict: ConflictResolution = ConflictResolution.ERROR
    ) -> None:
        """Copy then delete each marked entry."""
        src_pane = self.focused_pane
        dst_pane = self.other_pane
        targets = list(src_pane.marked_entries)
        if not targets:
            return
        source_provider, destination_provider = src_pane.provider, dst_pane.provider
        src_base, dst_base = src_pane.path, dst_pane.path
        transfer_ids = await self._pre_register_pending(
            targets, src_pane, dst_pane, operation="move"
        )
        # Bind both directories ONCE, next to the provider pair already
        # snapshotted above. Re-reading ``*_pane.path`` per iteration lets a
        # navigation between two transfers redirect the rest of the batch, and
        # ``_pre_register_pending`` has already recorded the ORIGINAL paths in
        # the journal and the transfers overlay — so the record would name a
        # destination the bytes never reached.
        # See ``copy_across`` for the rationale on the ``consumed``
        # set — mid-batch failure must not strand PENDING journal
        # entries for ids the loop never reached.
        consumed: set[str] = set()
        try:
            for entry, transfer_id in transfer_ids:
                src_path = src_base.join(entry.entry.name)
                dst_path = dst_base.join(entry.entry.name)
                # See ``copy_across`` for why consumed.add precedes the await.
                consumed.add(transfer_id)
                self._active_transfer_ids.add(transfer_id)
                try:
                    before_publication, file_progress = self.transfer_runtime.copy_hooks(
                        transfer_id,
                        src_path,
                        directory=entry.entry.kind is EntryKind.DIRECTORY,
                    )
                    mover = CrossFsMove(
                        source=source_provider,
                        destination=destination_provider,
                        before_publication=before_publication,
                        file_progress_transform=file_progress,
                    )
                    completed = await self._run_one_transfer(
                        operation=mover.move,
                        src_path=src_path,
                        dst_path=dst_path,
                        on_conflict=on_conflict,
                        transfer_id=transfer_id,
                        entry=entry,
                    )
                    if completed:
                        await self._mark_transfer_completed(transfer_id, entry)
                finally:
                    self._active_transfer_ids.discard(transfer_id)
        finally:
            await self.transfer_runtime.settle_batch(
                [(tid, entry.entry.size) for entry, tid in transfer_ids],
                consumed,
            )
            # Refresh BOTH panes inside the finally — see
            # ``copy_across`` for the rationale. Move is even more
            # sensitive: files 1..K-1 are both copied AND deleted
            # from src, so the source pane must redraw or the user
            # sees ghost rows for entries that are gone. Same
            # owned, shielded refresh as copy_across so outer-worker
            # cancellation doesn't strand the panes in LOADING.
            await self._refresh_after_operation(src_pane, dst_pane)

    async def delete_in_focused(self) -> None:
        """Delete marked original paths through the durable transfer path."""
        pane = self.focused_pane
        provider, base = pane.provider, pane.path
        targets = list(pane.marked_entries)
        tids = await self._pre_register_pending(targets, pane, None, operation="delete")
        consumed: set[str] = set()

        async def delete(source: object, _destination: object, **_kwargs: object) -> bool:
            await provider.delete(source)  # type: ignore[arg-type]
            return True

        try:
            for entry, tid in tids:
                consumed.add(tid)
                if await self._run_one_transfer(
                    operation=delete,
                    src_path=base.join(entry.entry.name),
                    dst_path=None,
                    on_conflict=ConflictResolution.ERROR,
                    transfer_id=tid,
                    entry=entry,
                ):
                    await self.transfer_runtime.complete(tid, 0, entry.entry.size)
        finally:
            await self.transfer_runtime.settle_batch(
                [(tid, entry.entry.size) for entry, tid in tids],
                consumed,
            )
            await self._refresh_after_operation(pane)

    async def _mark_transfer_completed(self, transfer_id: str, entry: EntryVM) -> None:
        await self.transfer_runtime.complete(transfer_id, entry.entry.size or 0, entry.entry.size)

    async def _pre_register_pending(
        self,
        targets: list[EntryVM],
        src_pane: PaneVM,
        dst_pane: PaneVM | None,
        *,
        operation: str = "copy",
    ) -> list[tuple[EntryVM, str]]:
        if len(targets) > _MAX_TRANSFER_BATCH_ENTRIES:
            raise ProviderError(
                "copy and move batches support at most "
                f"{_MAX_TRANSFER_BATCH_ENTRIES} selected entries"
            )
        # Snapshot original paths and identities before the first disk await.
        endpoints = [
            (
                entry,
                _pane_uri(src_pane, entry.entry.name),
                _pane_uri(dst_pane, entry.entry.name) if dst_pane else "",
            )
            for entry in targets
        ]
        source_identity = src_pane.transfer_connection
        destination_identity = dst_pane.transfer_connection if dst_pane else None
        transfer_ids: list[tuple[EntryVM, str]] = []
        try:
            for entry, source_uri, destination_uri in endpoints:
                descriptor = None
                if source_identity is not None and (
                    dst_pane is None or destination_identity is not None
                ):
                    descriptor = TransferHistoryDescriptor(
                        operation=operation,  # type: ignore[arg-type]
                        source_connection=source_identity,
                        destination_connection=destination_identity,
                        source_uri=source_uri,
                        destination_uri=destination_uri or None,
                        bytes_total=entry.entry.size,
                    )
                tid = await self.transfer_runtime.begin(
                    source_uri=source_uri,
                    destination_uri=destination_uri,
                    bytes_total=entry.entry.size,
                    descriptor=descriptor,
                )
                transfer_ids.append((entry, tid))
                self.transfer_runtime.progress(
                    tid, TransferState.PENDING, 0, entry.entry.size, source_uri, destination_uri
                )
        except BaseException:
            await self.transfer_runtime.settle_batch(
                [(tid, entry.entry.size) for entry, tid in transfer_ids],
                set(),
            )
            raise
        return transfer_ids

    async def _run_one_transfer(
        self,
        *,
        operation: object,
        src_path: object,
        dst_path: object,
        on_conflict: ConflictResolution,
        transfer_id: str,
        entry: EntryVM,
    ) -> bool:
        return await self.transfer_runtime.run_one(
            operation=operation,
            src_path=src_path,
            dst_path=dst_path,
            on_conflict=on_conflict,
            transfer_id=transfer_id,
            bytes_total=entry.entry.size,
        )

    # ── Internal ────────────────────────────────────────────────────────────

    def _focused_pane(self) -> PaneVM:
        return self._left if self._focused is FocusedPane.LEFT else self._right

    def _switch_focus(self) -> None:
        target = FocusedPane.RIGHT if self._focused is FocusedPane.LEFT else FocusedPane.LEFT
        self.set_focused(target)

    def _signal_copy_requested(self) -> None:
        self._notify("copy_requested")

    def _signal_move_requested(self) -> None:
        self._notify("move_requested")

    def _signal_delete_requested(self) -> None:
        self._notify("delete_requested")

    def _notify(self, prop: str) -> None:
        """Emit on BOTH the shared hub and this facade's own Observable.

        Never either/or: the hub send is the published contract
        (``tests/unit/vm/file_manager/test_dual_pane_vm.py`` asserts the
        ``"focused"`` message, and the three ``*_requested`` signals are a
        hub-only surface), while the subject is what the bound
        :class:`~aws_tui.ui.widgets.dual_pane.DualPane` listens to. The guard
        stops a late caller from publishing a property change for a disposed
        view model, matching ``PaneVM._notify`` and ``EntryVM._notify``.
        """
        if self._disposed:
            return
        send_value_free(self._hub, PropertyChangedMessage.create(self, self._inner.name, prop))
        self._on_property_changed.on_next(prop)


__all__ = ["DualPaneVM", "FocusedPane"]
