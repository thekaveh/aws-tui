"""CommandPaletteVM — fuzzy-filterable command palette overlay.

The palette holds a registry of :class:`PaletteEntry` plus the action
callable bound to each. Filtering is a simple substring + leading-char
score over ``label`` and ``keywords`` — explicitly avoiding ``rapidfuzz``
to keep the dependency footprint tiny. The view layer reads
``filtered_entries`` and ``selected_index`` directly; reactive updates fire
``PropertyChangedMessage`` on the hub.
"""

from __future__ import annotations

import asyncio
import inspect
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from vmx import (
    ComponentVM,
    Message,
    MessageHub,
    PropertyChangedMessage,
    RelayCommand,
    RelayCommandOf,
)
from vmx.lifecycle.status import ConstructionStatus
from vmx.services.dispatcher import Dispatcher

#: User-supplied callable for a palette entry — sync or async (returns an awaitable).
PaletteAction = Callable[[], None | Awaitable[None]]


@dataclass(frozen=True, slots=True)
class PaletteEntry:
    """Immutable description of a single palette entry.

    Parameters
    ----------
    id:
        Stable identifier (e.g. ``"connection.switch.minio-local"``); used by
        ``unregister_entry``.
    label:
        Human-visible text.
    category:
        Coarse grouping tag (e.g. ``"connection"``, ``"theme"``). Surfaced by
        the view layer as a chip.
    keywords:
        Additional search tokens that match even if the label doesn't.
    """

    id: str
    label: str
    category: str
    keywords: tuple[str, ...] = ()


def _subsequence_span(text: str, query: str) -> int | None:
    """Return the minimum span (last_idx - first_idx) of *query* as a
    subsequence in *text*. None if not present as a subsequence.

    Tight clusters of the query inside the text score lower (better).
    """
    if not query:
        return 0
    first: int | None = None
    last: int | None = None
    cursor = 0
    for ch in query:
        idx = text.find(ch, cursor)
        if idx < 0:
            return None
        if first is None:
            first = idx
        last = idx
        cursor = idx + 1
    assert first is not None
    assert last is not None
    return last - first


def _score(entry: PaletteEntry, query: str) -> int | None:
    """Return a sort score for *entry* against *query* (lower = better).

    None means the entry does not match the query and should be excluded.
    Strategy: exact-prefix > substring > tight subsequence > keyword
    substring > keyword subsequence.
    """
    if not query:
        return 0
    q = query.casefold()
    label = entry.label.casefold()
    if label.startswith(q):
        return 0
    idx = label.find(q)
    if idx >= 0:
        return 100 + idx
    span = _subsequence_span(label, q)
    if span is not None:
        return 500 + span
    for kw in entry.keywords:
        kwl = kw.casefold()
        if kwl.startswith(q):
            return 1_000
        if q in kwl:
            return 1_500
        kspan = _subsequence_span(kwl, q)
        if kspan is not None:
            return 2_000 + kspan
    return None


class CommandPaletteVM:
    """Reactive command-palette viewmodel."""

    def __init__(
        self,
        *,
        hub: MessageHub[Message],
        dispatcher: Dispatcher,
    ) -> None:
        self._hub: MessageHub[Message] = hub
        self._dispatcher: Dispatcher = dispatcher

        self._entries: dict[str, tuple[PaletteEntry, PaletteAction]] = {}
        self._filter_text: str = ""
        self._filtered: tuple[PaletteEntry, ...] = ()
        self._selected_index: int = 0
        self._is_open: bool = False
        # Strong refs to in-flight palette-action tasks to satisfy RUF006
        # and prevent them being GC'd mid-await.
        self._pending_tasks: set[asyncio.Task[None]] = set()

        self._inner: ComponentVM = (
            ComponentVM.builder().name("command_palette").services(hub, dispatcher).build()
        )

        self._open_command: RelayCommand = (
            RelayCommand.builder().predicate(lambda: not self._is_open).task(self._open).build()
        )
        self._close_command: RelayCommand = (
            RelayCommand.builder().predicate(lambda: self._is_open).task(self._close).build()
        )
        self._execute_selected_command: RelayCommand = (
            RelayCommand.builder()
            .predicate(lambda: self._is_open and len(self._filtered) > 0)
            .task(self._execute_selected)
            .build()
        )
        self._move_selection_command: RelayCommandOf[int] = (
            RelayCommandOf[int]
            .builder()
            .predicate(lambda _delta: self._is_open and len(self._filtered) > 0)
            .task(self._move_selection)
            .build()
        )

    # ── Properties ──────────────────────────────────────────────────────────

    @property
    def is_open(self) -> bool:
        return self._is_open

    @property
    def filter_text(self) -> str:
        return self._filter_text

    @filter_text.setter
    def filter_text(self, value: str) -> None:
        if self._filter_text == value:
            return
        self._filter_text = value
        self._hub.send(PropertyChangedMessage.create(self, self.name, "filter_text"))
        self._recompute_filtered()

    @property
    def filtered_entries(self) -> tuple[PaletteEntry, ...]:
        return self._filtered

    @property
    def selected_index(self) -> int:
        return self._selected_index

    @property
    def open_command(self) -> RelayCommand:
        return self._open_command

    @property
    def close_command(self) -> RelayCommand:
        return self._close_command

    @property
    def execute_selected_command(self) -> RelayCommand:
        return self._execute_selected_command

    @property
    def move_selection_command(self) -> RelayCommandOf[int]:
        return self._move_selection_command

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
        self._open_command.dispose()
        self._close_command.dispose()
        self._execute_selected_command.dispose()
        self._move_selection_command.dispose()
        self._inner.dispose()

    # ── Registry API ───────────────────────────────────────────────────────

    def register_entry(self, entry: PaletteEntry, action: PaletteAction) -> None:
        """Register or replace ``entry``; rebuilds the filtered list."""
        self._entries[entry.id] = (entry, action)
        self._recompute_filtered()

    def unregister_entry(self, entry_id: str) -> None:
        if entry_id in self._entries:
            del self._entries[entry_id]
            self._recompute_filtered()

    # ── Command implementations ────────────────────────────────────────────

    def _open(self) -> None:
        self._set_open(True)
        # Reset selection / filter on open so the user always starts clean.
        self._filter_text = ""
        self._hub.send(PropertyChangedMessage.create(self, self.name, "filter_text"))
        self._recompute_filtered()

    def _close(self) -> None:
        self._set_open(False)

    def _set_open(self, value: bool) -> None:
        if self._is_open == value:
            return
        self._is_open = value
        self._hub.send(PropertyChangedMessage.create(self, self.name, "is_open"))

    def _execute_selected(self) -> None:
        if not self._filtered:
            return
        idx = max(0, min(self._selected_index, len(self._filtered) - 1))
        entry = self._filtered[idx]
        _, action = self._entries[entry.id]
        self._set_open(False)
        result = action()
        if inspect.isawaitable(result):
            self._spawn_awaitable(result)

    def _spawn_awaitable(self, awaitable: Awaitable[None]) -> None:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            # No running loop — caller is expected to drive the palette from
            # an asyncio context. We bail rather than crash.
            return
        task = loop.create_task(self._await_action(awaitable))
        self._pending_tasks.add(task)
        task.add_done_callback(self._pending_tasks.discard)

    async def _await_action(self, awaitable: Awaitable[None]) -> None:
        await awaitable

    def _move_selection(self, delta: int | None) -> None:
        if delta is None or not self._filtered:
            return
        new_index = self._selected_index + delta
        new_index = max(0, min(new_index, len(self._filtered) - 1))
        self._set_selected_index(new_index)

    def _set_selected_index(self, value: int) -> None:
        if self._selected_index == value:
            return
        self._selected_index = value
        self._hub.send(PropertyChangedMessage.create(self, self.name, "selected_index"))

    # ── Filter machinery ───────────────────────────────────────────────────

    def _recompute_filtered(self) -> None:
        query = self._filter_text
        # Enumerate to keep a stable insertion-order tiebreaker so an empty
        # query renders entries in registration order; a query renders best
        # matches first, with insertion-order as the deterministic tiebreaker.
        scored: list[tuple[int, int, PaletteEntry]] = []
        for insertion_idx, (entry, _) in enumerate(self._entries.values()):
            score = _score(entry, query)
            if score is None:
                continue
            scored.append((score, insertion_idx, entry))
        scored.sort(key=lambda t: (t[0], t[1]))
        new_filtered = tuple(e for _, _, e in scored)
        if new_filtered != self._filtered:
            self._filtered = new_filtered
            self._hub.send(PropertyChangedMessage.create(self, self.name, "filtered_entries"))
        # Reset selection whenever the filter pool changes.
        if self._selected_index != 0:
            self._set_selected_index(0)


__all__ = ["CommandPaletteVM", "PaletteAction", "PaletteEntry"]
