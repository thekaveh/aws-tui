"""CommandPaletteVM — fuzzy-filterable command palette overlay.

The palette holds a registry of :class:`PaletteEntry` plus the action
callable bound to each. Filtering is a simple substring + leading-char
score over ``label`` and ``keywords`` — explicitly avoiding ``rapidfuzz``
to keep the dependency footprint tiny. The view layer reads
``filtered_entries`` and ``selected_index`` directly; reactive updates fire
``PropertyChangedMessage`` on the hub.

Round-3 directive (spec §9.bis.11): the registry composes
:class:`vmx.CompositeVM` over per-entry :class:`PaletteEntryVM`
facades. VMx ``ScoredFilteredCompositeVM`` owns the hidden/ranked visible
projection; the facade still exposes the app-specific
``filter_text`` / ``filtered_entries`` / ``selected_index`` surface.
"""

from __future__ import annotations

import asyncio
import inspect
from collections.abc import Awaitable, Callable

from vmx import (
    ComponentVM,
    ComponentVMOf,
    CompositeVM,
    ConstructionStatus,
    Dispatcher,
    Message,
    MessageHub,
    PropertyChangedMessage,
    RelayCommand,
    RelayCommandOf,
    ScoredFilteredCompositeVM,
)

from aws_tui.vm._observable import send_value_free
from aws_tui.vm.chrome.action_catalog import ActionPresentation
from aws_tui.vm.messages import PaletteActionFailedMessage

#: User-supplied callable for a palette entry — sync or async (returns an awaitable).
PaletteAction = Callable[[], Awaitable[None] | None]


PaletteEntry = ActionPresentation


def _subsequence_span(text: str, query: str) -> int | None:
    """Return the minimum span (last_idx - first_idx) of *query* as a
    subsequence in *text*. None if not present as a subsequence.

    Tight clusters of the query inside the text score lower (better).
    """
    if not query:
        return 0
    first: int = -1
    last: int = -1
    cursor = 0
    for ch in query:
        idx = text.find(ch, cursor)
        if idx < 0:
            return None
        if first == -1:
            first = idx
        last = idx
        cursor = idx + 1
    return last - first


def _score(entry: PaletteEntry, query: str) -> int | None:
    """Return a sort score for *entry* against *query* (lower = better).

    None means the entry does not match the query and should be excluded.
    Strategy: exact-prefix > substring > tight subsequence > keyword
    substring > keyword subsequence.
    """
    if not entry.available:
        return None
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


class PaletteEntryVM:
    """Facade wrapping a :class:`ComponentVMOf` so the inner can be
    parented into the palette's :class:`CompositeVM`.

    Same pattern as ``NavItemVM``. The public payload is the
    :class:`PaletteEntry` value. ``inner`` is the composite's child handle.
    """

    def __init__(
        self,
        *,
        entry: PaletteEntry,
        hub: MessageHub[Message],
        dispatcher: Dispatcher,
    ) -> None:
        self._entry: PaletteEntry = entry
        self._inner: ComponentVMOf[PaletteEntry] = (
            ComponentVMOf[PaletteEntry]
            .builder()
            .name(f"palette_entry.{entry.id}")
            .model(entry)
            .services(hub, dispatcher)
            .build()
        )

    @property
    def entry(self) -> PaletteEntry:
        return self._entry

    @property
    def inner(self) -> ComponentVMOf[PaletteEntry]:
        return self._inner

    @property
    def is_constructed(self) -> bool:
        return self._inner.is_constructed

    def construct(self) -> None:
        self._inner.construct()

    def dispose(self) -> None:
        self._inner.dispose()


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

        # Registry: composite of PaletteEntryVM inners (lifecycle +
        # on_collection_changed), plus a parallel dict for action lookup
        # by entry id. The composite is internal — consumers bind to the
        # filter_text / filtered_entries / selected_index surface.
        self._items: dict[str, PaletteEntryVM] = {}
        self._actions: dict[str, PaletteAction] = {}
        self._inner_registry: CompositeVM[ComponentVMOf[PaletteEntry]] = (
            CompositeVM[ComponentVMOf[PaletteEntry]]
            .builder()
            .name("command_palette.registry")
            .services(hub, dispatcher)
            .children(self._initial_children)
            .auto_construct_on_add(True)
            .build()
        )

        self._filter_text: str = ""
        self._active_service_id: str | None = None
        self._scored_filter: ScoredFilteredCompositeVM[ComponentVMOf[PaletteEntry]] = (
            ScoredFilteredCompositeVM(self._inner_registry, scorer=self._score_for_vmx)
        )
        self._is_open: bool = False
        self._pending_tasks: dict[asyncio.Future[None], str] = {}

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
            .predicate(lambda: self._is_open and self._scored_filter.visible_count > 0)
            .task(self._execute_selected)
            .build()
        )
        self._move_selection_command: RelayCommandOf[int] = (
            RelayCommandOf[int]
            .builder()
            .predicate(lambda _delta: self._is_open and self._scored_filter.visible_count > 0)
            .task(self._move_selection)
            .build()
        )
        self._published_entries: tuple[PaletteEntry, ...] = self.filtered_entries
        self._published_index: int = self.selected_index
        # One subscription publishes both names for every change of the view,
        # including a cursor move that no VM method made.
        self._view_sub = self._scored_filter.on_changed.subscribe(
            lambda _change: self._publish_projection()
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
        self._notify("filter_text")
        self._scored_filter.refresh_scores()
        self._select_first()

    @property
    def active_service_id(self) -> str | None:
        return self._active_service_id

    def set_active_service(self, service_id: str | None) -> None:
        if self._active_service_id == service_id:
            return
        self._active_service_id = service_id
        self._notify("active_service_id")
        self._scored_filter.refresh_scores()
        self._select_first()

    @property
    def filtered_entries(self) -> tuple[PaletteEntry, ...]:
        return tuple(item.model for item in self._scored_filter.visible)

    @property
    def selected_index(self) -> int:
        current, visible = self._scored_filter.current, self._scored_filter.visible
        return next((i for i, item in enumerate(visible) if item is current), 0)

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
        self._inner_registry.construct()

    def destruct(self) -> None:
        self._inner_registry.destruct()
        self._inner.destruct()

    async def shutdown(self) -> None:
        """Cancel and drain every asynchronous palette action."""
        tasks = tuple(self._pending_tasks)
        for task in tasks:
            if not task.done():
                task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        for task in tasks:
            self._pending_tasks.pop(task, None)

    def dispose(self) -> None:
        for task in list(self._pending_tasks):
            if not task.done():
                task.cancel()
        self._pending_tasks.clear()
        for item in list(self._items.values()):
            item.dispose()
        self._items.clear()
        self._actions.clear()
        self._open_command.dispose()
        self._close_command.dispose()
        self._execute_selected_command.dispose()
        self._move_selection_command.dispose()
        # End the view subscription first, so teardown publishes no projection.
        self._view_sub.dispose()
        # Dispose the scored filter BEFORE the registry it subscribes to, so
        # its subscription unwinds against a live source. `PaneVM.dispose`
        # documents the same ordering for its `FilteredCompositeVM`; this
        # class had drifted and left the filter — and therefore a live rx
        # subscription onto the disposed registry plus strong references to
        # every disposed entry — alive for the process lifetime.
        self._scored_filter.dispose()
        self._inner_registry.dispose()
        self._inner.dispose()

    # ── Registry API ───────────────────────────────────────────────────────

    def register_entry(self, entry: PaletteEntry, action: PaletteAction) -> None:
        """Register or replace ``entry``; rebuilds the filtered list."""
        # Replace path: drop the old inner from the composite first so
        # auto_construct + composite ordering stays sane.
        existing = self._items.get(entry.id)
        if existing is not None and existing.entry == entry:
            self._actions[entry.id] = action
            return
        if existing is not None:
            if existing.inner in self._inner_registry:
                self._inner_registry.remove(existing.inner)
            existing.dispose()
        item = PaletteEntryVM(entry=entry, hub=self._hub, dispatcher=self._dispatcher)
        self._items[entry.id] = item
        self._actions[entry.id] = action
        self._inner_registry.append(item.inner)
        self._select_first()

    def unregister_entry(self, entry_id: str) -> None:
        item = self._items.pop(entry_id, None)
        if item is None:
            return
        self._actions.pop(entry_id, None)
        if item.inner in self._inner_registry:
            self._inner_registry.remove(item.inner)
        item.dispose()
        self._select_first()

    # ── Command implementations ────────────────────────────────────────────

    def _open(self) -> None:
        self._set_open(True)
        if self._filter_text != "":
            self._filter_text = ""
            self._notify("filter_text")
        self._scored_filter.refresh_scores()
        self._select_first()

    def _close(self) -> None:
        self._set_open(False)

    def _set_open(self, value: bool) -> None:
        if self._is_open == value:
            return
        self._is_open = value
        self._notify("is_open")

    def _execute_selected(self) -> None:
        current = self._scored_filter.current
        if current is None:
            return
        entry = current.model
        action = self._actions.get(entry.id)
        self._set_open(False)
        if action is None:
            return
        try:
            result = action()
        except Exception as exc:
            self._publish_action_failure(entry.id, exc)
            return
        if inspect.isawaitable(result):
            self._spawn_awaitable(entry.id, result)

    def _spawn_awaitable(self, entry_id: str, awaitable: Awaitable[None]) -> None:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            close = getattr(awaitable, "close", None)
            if close is not None:
                close()
            self._publish_action_failure(entry_id, RuntimeError("no running event loop"))
            return
        # Own the returned awaitable itself. A wrapper cancelled before its
        # first turn can strand a coroutine or leave an existing task running.
        task = asyncio.ensure_future(awaitable, loop=loop)
        self._pending_tasks[task] = entry_id
        task.add_done_callback(self._on_action_done)

    def _on_action_done(self, task: asyncio.Future[None]) -> None:
        """Done-callback for spawned palette actions. Drains the
        task's exception (if any) so asyncio's destructor doesn't
        emit a silent ``Task exception was never retrieved`` to
        stderr — invisible in a TUI. The exception is then routed
        through the hub as a PropertyChangedMessage so a future
        toast subscriber can surface it; today the bare drain
        prevents the silent-loss antipattern even without a
        subscriber wired."""
        entry_id = self._pending_tasks.pop(task, "unknown")
        if task.cancelled():
            return
        exc = task.exception()
        if exc is None:
            return
        self._publish_action_failure(entry_id, exc)

    def _publish_action_failure(self, entry_id: str, exc: BaseException) -> None:
        self._hub.send(
            PaletteActionFailedMessage(
                entry_id=entry_id,
                error_type=type(exc).__name__,
            )
        )

    def _move_selection(self, delta: int | None) -> None:
        if not delta:
            return
        view = self._scored_filter
        step = view.move_to_next_visible if delta > 0 else view.move_to_previous_visible
        for _ in range(min(abs(delta), view.visible_count)):
            step()

    # ── Filter machinery ───────────────────────────────────────────────────

    def _initial_children(self) -> tuple[ComponentVMOf[PaletteEntry], ...]:
        return tuple(item.inner for item in self._items.values())

    def _score_for_vmx(self, item_inner: ComponentVMOf[PaletteEntry]) -> int | None:
        entry = item_inner.model
        if entry.service_ids and self._active_service_id not in entry.service_ids:
            return None
        score = _score(entry, self._filter_text)
        if score is None:
            return None
        # VMx ScoredFilteredCompositeVM ranks higher scores first; aws-tui's
        # palette scorer historically ranks lower scores first.
        return -score

    def _select_first(self) -> None:
        visible = self._scored_filter.visible
        self._scored_filter.set_current(visible[0] if visible else None)

    def _notify(self, name: str) -> None:
        send_value_free(self._hub, PropertyChangedMessage.create(self, self.name, name))

    def _publish_projection(self) -> None:
        entries, index = self.filtered_entries, self.selected_index
        if entries != self._published_entries:
            self._published_entries = entries
            self._notify("filtered_entries")
        if index != self._published_index:
            self._published_index = index
            self._notify("selected_index")


__all__ = ["CommandPaletteVM", "PaletteAction", "PaletteEntry", "PaletteEntryVM"]
