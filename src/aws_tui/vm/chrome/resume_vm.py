"""ResumeVM — facade for the deferred unfinished-transfer modal.

The VM holds an immutable snapshot of :class:`TransferJournalEntry` rows
and exposes a single async ``ask()`` returning a :class:`ResumeAction`.
The app does not yet scan ``TransferJournal.find_unfinished()`` at
startup, so this VM is currently covered as modal/application
scaffolding rather than a wired launch flow.

The matching apply step (resume / abort / keep) lives in the composition
root, not on this VM — keeping the VM strictly view-side simplifies
testing and lets the composition reach into the multipart abort path
without pulling boto3 into ``vm/chrome/``.
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from enum import StrEnum

from vmx import ComponentVM, Message, MessageHub, PropertyChangedMessage, RelayCommand
from vmx.lifecycle.status import ConstructionStatus
from vmx.services.dispatcher import Dispatcher

from aws_tui.domain.transfer_journal import TransferJournalEntry


class ResumeAction(StrEnum):
    """User decision when offered the resume modal."""

    RESUME_ALL = "resume_all"
    ABORT_ALL = "abort_all"
    DECIDE_EACH = "decide_each"
    KEEP_FOR_LATER = "keep_for_later"


class ResumeVM:
    """Holds unfinished transfer entries; exposes a single async decision."""

    def __init__(
        self,
        entries: Sequence[TransferJournalEntry],
        *,
        hub: MessageHub[Message],
        dispatcher: Dispatcher,
    ) -> None:
        self._hub: MessageHub[Message] = hub

        self._entries: tuple[TransferJournalEntry, ...] = tuple(entries)
        self._is_open: bool = False
        self._future: asyncio.Future[ResumeAction] | None = None
        self._disposed: bool = False

        self._inner: ComponentVM = (
            ComponentVM.builder().name("resume").services(hub, dispatcher).build()
        )

        self._resume_all_command: RelayCommand = self._make_decision_command(
            ResumeAction.RESUME_ALL
        )
        self._abort_all_command: RelayCommand = self._make_decision_command(ResumeAction.ABORT_ALL)
        self._decide_each_command: RelayCommand = self._make_decision_command(
            ResumeAction.DECIDE_EACH
        )
        self._keep_for_later_command: RelayCommand = self._make_decision_command(
            ResumeAction.KEEP_FOR_LATER
        )

    def _make_decision_command(self, action: ResumeAction) -> RelayCommand:
        def _task() -> None:
            self._resolve(action)

        return RelayCommand.builder().predicate(lambda: self._is_open).task(_task).build()

    # ── Properties ──────────────────────────────────────────────────────────

    @property
    def entries(self) -> tuple[TransferJournalEntry, ...]:
        return self._entries

    @property
    def is_open(self) -> bool:
        return self._is_open

    @property
    def count(self) -> int:
        return len(self._entries)

    @property
    def resume_all_command(self) -> RelayCommand:
        return self._resume_all_command

    @property
    def abort_all_command(self) -> RelayCommand:
        return self._abort_all_command

    @property
    def decide_each_command(self) -> RelayCommand:
        return self._decide_each_command

    @property
    def keep_for_later_command(self) -> RelayCommand:
        return self._keep_for_later_command

    @property
    def status(self) -> ConstructionStatus:
        return self._inner.status

    @property
    def name(self) -> str:
        return self._inner.name

    # ── Lifecycle ───────────────────────────────────────────────────────────

    def construct(self) -> None:
        self._inner.construct()

    def destruct(self) -> None:
        self._inner.destruct()

    def dispose(self) -> None:
        if self._disposed:
            return
        self._disposed = True
        if self._future is not None and not self._future.done():
            self._future.set_result(ResumeAction.KEEP_FOR_LATER)
        self._resume_all_command.dispose()
        self._abort_all_command.dispose()
        self._decide_each_command.dispose()
        self._keep_for_later_command.dispose()
        self._inner.dispose()

    # ── Async API ──────────────────────────────────────────────────────────

    async def ask(self) -> ResumeAction:
        """Open the modal and return the user's decision."""
        if self._is_open or self._future is not None:
            raise RuntimeError("resume modal is already open")
        if self._disposed:
            raise RuntimeError("resume modal has been disposed")
        loop = asyncio.get_running_loop()
        self._future = loop.create_future()
        self._set_open(True)
        try:
            return await self._future
        finally:
            self._future = None
            self._set_open(False)

    # ── Internal ────────────────────────────────────────────────────────────

    def _resolve(self, action: ResumeAction) -> None:
        if self._future is None or self._future.done():
            return
        self._future.set_result(action)

    def _set_open(self, value: bool) -> None:
        if self._is_open == value:
            return
        self._is_open = value
        self._hub.send(PropertyChangedMessage.create(self, self.name, "is_open"))


def humanize_bytes(n: int | None) -> str:
    """Return a short ``X.Y kB / MB / GB`` rendering for the modal preview."""
    if n is None:
        return "?"
    units = ("B", "kB", "MB", "GB", "TB")
    size = float(n)
    for unit in units:
        if size < 1024 or unit == units[-1]:
            return f"{size:.1f} {unit}" if unit != "B" else f"{int(size)} B"
        size /= 1024
    return f"{n} B"


def entry_summary(entry: TransferJournalEntry) -> str:
    """One-line summary used by the resume modal body.

    Mirrors the format in spec §7.6 (filename, parts completed, total
    size). Without per-part byte totals we cannot honestly report a
    percentage — the previous ``parts / (parts + 1)`` asymptote rendered
    "50% done" for a single-part transfer, which the user reasonably
    read as a lie. We render the part count verbatim instead. Exact
    bytes come from the resumed transfer itself once the user picks
    RESUME_ALL.
    """
    name = _basename(entry.destination_uri)
    parts = len(entry.completed_parts)
    if entry.bytes_total is None:
        return f"{name}  ({parts} parts)"
    return f"{name}  ({parts} parts, {humanize_bytes(entry.bytes_total)} total)"


def _basename(uri: str) -> str:
    # Strip any scheme, then take the trailing path segment.
    tail = uri.split("://", 1)[-1]
    return tail.rsplit("/", 1)[-1] or tail


__all__ = ["ResumeAction", "ResumeVM", "entry_summary", "humanize_bytes"]
