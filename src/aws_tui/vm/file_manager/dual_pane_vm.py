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

from enum import StrEnum

from vmx import ComponentVM, Message, MessageHub, PropertyChangedMessage, RelayCommand
from vmx.lifecycle.status import ConstructionStatus
from vmx.services.dispatcher import Dispatcher

from aws_tui.domain.cross_fs import ConflictResolution, CrossFsCopy, CrossFsMove
from aws_tui.domain.filesystem import TransferProgress
from aws_tui.domain.transfer_journal import TransferJournal
from aws_tui.vm.file_manager.pane_vm import PaneVM
from aws_tui.vm.messages import TransferProgressMessage, TransferState


class FocusedPane(StrEnum):
    LEFT = "left"
    RIGHT = "right"


def _pane_uri(pane: PaneVM, leaf: str) -> str:
    """Build a stable label for transfer source/destination identifiers."""
    base = pane.path.as_posix().rstrip("/")
    return f"{base}/{leaf}" if base != "/" else f"/{leaf}"


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
        self._dispatcher: Dispatcher = dispatcher
        self._left: PaneVM = left
        self._right: PaneVM = right
        self._journal: TransferJournal = transfer_journal
        self._focused: FocusedPane = FocusedPane.LEFT

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

    def destruct(self) -> None:
        self._right.destruct()
        self._left.destruct()
        self._inner.destruct()

    def dispose(self) -> None:
        self._switch_focus_command.dispose()
        self._copy_across_command.dispose()
        self._move_across_command.dispose()
        self._delete_in_focused_command.dispose()
        self._right.dispose()
        self._left.dispose()
        self._inner.dispose()

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
        for entry in targets:
            src_path = src_pane.path.join(entry.entry.name)
            dst_path = dst_pane.path.join(entry.entry.name)
            src_uri = _pane_uri(src_pane, entry.entry.name)
            dst_uri = _pane_uri(dst_pane, entry.entry.name)
            transfer_id = self._journal.begin(
                source_uri=src_uri,
                destination_uri=dst_uri,
                bytes_total=entry.entry.size,
            )

            # First progress message carries the labels so TransfersVM
            # can auto-register a placeholder with meaningful "from /
            # to" text instead of "??". Subsequent messages may omit
            # them — the placeholder is already set up.
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

            def _progress(p: TransferProgress, *, _tid: str = transfer_id) -> None:
                self._hub.send(
                    TransferProgressMessage(
                        transfer_id=_tid,
                        bytes_transferred=p.bytes_transferred,
                        bytes_total=p.bytes_total,
                        state=TransferState.RUNNING,
                    )
                )

            try:
                await copier.copy(src_path, dst_path, progress=_progress, on_conflict=on_conflict)
            except Exception:
                self._hub.send(
                    TransferProgressMessage(
                        transfer_id=transfer_id,
                        bytes_transferred=0,
                        bytes_total=entry.entry.size,
                        state=TransferState.FAILED,
                    )
                )
                self._journal.mark_aborted(transfer_id)
                raise
            self._hub.send(
                TransferProgressMessage(
                    transfer_id=transfer_id,
                    bytes_transferred=entry.entry.size or 0,
                    bytes_total=entry.entry.size,
                    state=TransferState.COMPLETED,
                )
            )
            self._journal.mark_finished(transfer_id)
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
        for entry in targets:
            src_path = src_pane.path.join(entry.entry.name)
            dst_path = dst_pane.path.join(entry.entry.name)
            src_uri = _pane_uri(src_pane, entry.entry.name)
            dst_uri = _pane_uri(dst_pane, entry.entry.name)
            transfer_id = self._journal.begin(
                source_uri=src_uri,
                destination_uri=dst_uri,
                bytes_total=entry.entry.size,
            )

            # Seed the placeholder labels (see copy_across for rationale).
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

            def _progress(p: TransferProgress, *, _tid: str = transfer_id) -> None:
                self._hub.send(
                    TransferProgressMessage(
                        transfer_id=_tid,
                        bytes_transferred=p.bytes_transferred,
                        bytes_total=p.bytes_total,
                        state=TransferState.RUNNING,
                    )
                )

            state: TransferState
            try:
                await mover.move(src_path, dst_path, progress=_progress, on_conflict=on_conflict)
                state = TransferState.COMPLETED
            except Exception:
                self._hub.send(
                    TransferProgressMessage(
                        transfer_id=transfer_id,
                        bytes_transferred=0,
                        bytes_total=entry.entry.size,
                        state=TransferState.FAILED,
                    )
                )
                self._journal.mark_aborted(transfer_id)
                raise
            self._hub.send(
                TransferProgressMessage(
                    transfer_id=transfer_id,
                    bytes_transferred=entry.entry.size or 0,
                    bytes_total=entry.entry.size,
                    state=state,
                )
            )
            self._journal.mark_finished(transfer_id)
        await src_pane.refresh()
        await dst_pane.refresh()

    async def delete_in_focused(self) -> None:
        """Delete every marked entry in the focused pane."""
        await self.focused_pane.delete_marked()

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
