from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from typing import Generic, TypeVar

import reactivex as rx
from reactivex import operators as ops
from reactivex.subject import Subject
from vmx import AsyncRelayCommand, CollectionChangedEvent

from aws_tui.domain.filesystem import ProviderError

T = TypeVar("T")
TToken = TypeVar("TToken")


class PagerCollectionLimitError(ProviderError):
    """The cumulative pager snapshot exceeded its configured ceiling."""


class SnapshotTokenPager(Generic[T, TToken]):
    """Token pager with an explicit, side-effect-free snapshot boundary.

    VMx 3.23 does not expose public snapshot hydration on
    ``TokenPagedComposition``. Athena therefore owns this small pager state
    while continuing to use VMx commands for execution and enablement.
    """

    def __init__(
        self,
        fetch_next: Callable[
            [TToken | None],
            Awaitable[tuple[Sequence[T], TToken | None]],
        ],
        *,
        max_items: int | None = None,
    ) -> None:
        if max_items is not None and max_items < 1:
            raise ValueError("max_items must be positive")
        self._fetch_next = fetch_next
        self._max_items = max_items
        self._items: list[T] = []
        self._current_token: TToken | None = None
        self._loaded_once = False
        self._limit_reached = False
        self._page_sizes: list[int] = []
        self._page_tokens: list[TToken | None] = []
        self._consumed_tokens: set[TToken] = set()
        self._operation_generation = 0
        self._refreshing = False
        self._disposed = False
        self._collection_changed: Subject[CollectionChangedEvent] = Subject()
        self._property_changed: Subject[str] = Subject()
        self._command_changed: Subject[None] = Subject()
        self._load_more_command = (
            AsyncRelayCommand.builder()
            .predicate(lambda: self.has_more and not self._disposed and not self._refreshing)
            .triggers(self._command_changed)
            .task(self._load_more)
            .build()
        )
        self._refresh_command = (
            AsyncRelayCommand.builder()
            .predicate(lambda: not self._disposed)
            .triggers(self._command_changed)
            .task(self._refresh)
            .build()
        )

    @property
    def items(self) -> list[T]:
        return list(self._items)

    @property
    def current_token(self) -> TToken | None:
        return self._current_token

    @property
    def has_more(self) -> bool:
        return not self._limit_reached and (
            not self._loaded_once or self._current_token is not None
        )

    @property
    def limit_reached(self) -> bool:
        return self._limit_reached

    @property
    def load_more_command(self) -> AsyncRelayCommand:
        return self._load_more_command

    @property
    def refresh_command(self) -> AsyncRelayCommand:
        return self._refresh_command

    @property
    def on_collection_changed(self) -> rx.Observable[CollectionChangedEvent]:
        return self._collection_changed.pipe(ops.as_observable())

    @property
    def on_property_changed(self) -> rx.Observable[str]:
        return self._property_changed.pipe(ops.as_observable())

    def restore(self, items: Sequence[T], next_token: TToken | None) -> None:
        if self._disposed:
            raise RuntimeError("SnapshotTokenPager is disposed")
        self._operation_generation += 1
        self._refreshing = False
        restored = list(items)
        self._check_item_budget(len(restored), snapshot=True)
        self._items = restored
        self._limit_reached = self._at_limit_with_more(len(restored), next_token)
        self._current_token = None if self._limit_reached else next_token
        self._loaded_once = True
        # Snapshots do not carry page boundaries, so treat the entire restored
        # sequence as one page. A shorter refresh must replace it conservatively.
        self._page_sizes = [len(restored)]
        self._page_tokens = [self._current_token]
        self._consumed_tokens = set()
        self._notify_reset()

    async def _load_more(self) -> None:
        generation = self._operation_generation
        request_token = self._current_token
        page, next_token = await self._fetch_next(request_token)
        if self._disposed or generation != self._operation_generation:
            return
        consumed = set(self._consumed_tokens)
        if request_token is not None:
            consumed.add(request_token)
        if next_token is not None and next_token in consumed:
            self._current_token = None
            self._loaded_once = True
            self._notify_properties()
            raise ProviderError("Athena returned a repeated continuation token")
        additions = list(page)
        if self._max_items is not None and len(self._items) + len(additions) > self._max_items:
            self._current_token = None
            self._loaded_once = True
            self._limit_reached = True
            self._notify_properties()
            raise PagerCollectionLimitError("Athena collection safety limit exceeded")
        self._items.extend(additions)
        self._limit_reached = self._at_limit_with_more(len(self._items), next_token)
        self._current_token = None if self._limit_reached else next_token
        self._consumed_tokens = consumed
        self._loaded_once = True
        self._page_sizes.append(len(additions))
        self._page_tokens.append(self._current_token)
        self._notify_reset()

    async def _refresh(self) -> None:
        self._operation_generation += 1
        generation = self._operation_generation
        self._refreshing = True
        self._limit_reached = False
        self._command_changed.on_next(None)
        try:
            page, next_token = await self._fetch_next(None)
        finally:
            if generation == self._operation_generation:
                self._refreshing = False
                self._command_changed.on_next(None)
        if self._disposed or generation != self._operation_generation:
            return
        fresh = list(page)
        self._check_item_budget(len(fresh), snapshot=False)
        self._limit_reached = self._at_limit_with_more(len(fresh), next_token)
        bounded_next_token = None if self._limit_reached else next_token
        first_page_size = self._page_sizes[0] if self._page_sizes else None
        if (
            first_page_size is not None
            and len(fresh) == first_page_size
            and fresh == self._items[:first_page_size]
            and bounded_next_token == self._page_tokens[0]
        ):
            self._page_tokens[0] = bounded_next_token
            self._current_token = self._page_tokens[-1]
            self._loaded_once = True
            self._notify_properties()
            return
        self._items = fresh
        self._current_token = bounded_next_token
        self._loaded_once = True
        self._page_sizes = [len(fresh)]
        self._page_tokens = [bounded_next_token]
        self._consumed_tokens = set()
        self._notify_reset()

    def _check_item_budget(self, count: int, *, snapshot: bool) -> None:
        if self._max_items is None or count <= self._max_items:
            return
        if snapshot:
            raise ValueError("Athena snapshot collection safety limit exceeded")
        raise PagerCollectionLimitError("Athena collection safety limit exceeded")

    def _at_limit_with_more(self, count: int, next_token: TToken | None) -> bool:
        return self._max_items is not None and count >= self._max_items and next_token is not None

    def _notify_reset(self) -> None:
        self._collection_changed.on_next(CollectionChangedEvent(action="reset"))
        self._notify_properties()

    def _notify_properties(self) -> None:
        for name in ("items", "current_token", "has_more"):
            self._property_changed.on_next(name)
        self._command_changed.on_next(None)

    def dispose(self) -> None:
        if self._disposed:
            return
        self._disposed = True
        self._operation_generation += 1
        self._load_more_command.dispose()
        self._refresh_command.dispose()
        self._collection_changed.on_completed()
        self._collection_changed.dispose()
        self._property_changed.on_completed()
        self._property_changed.dispose()
        self._command_changed.on_completed()
        self._command_changed.dispose()


def seed_token_pager(
    pager: SnapshotTokenPager[T, TToken],
    items: Sequence[T],
    next_token: TToken | None,
) -> None:
    """Restore Athena pagination state without fetching remote data."""
    if not isinstance(pager, SnapshotTokenPager):
        raise TypeError("pager must be a SnapshotTokenPager")
    pager.restore(items, next_token)


__all__ = ["PagerCollectionLimitError", "SnapshotTokenPager", "seed_token_pager"]
