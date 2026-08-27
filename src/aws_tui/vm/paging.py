from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from typing import Generic, TypeVar

from vmx import TokenPagedComposition

T = TypeVar("T")
TToken = TypeVar("TToken")


class BoundedTokenPagedComposition(TokenPagedComposition[T, TToken], Generic[T, TToken]):
    """VMx token pager with an explicit cumulative item ceiling."""

    def __init__(
        self,
        fetch_next: Callable[
            [TToken | None],
            Awaitable[tuple[Sequence[T], TToken | None]],
        ],
        *,
        max_items: int,
        auto_construct_on_add: bool = False,
        pages_equal: Callable[[Sequence[T], Sequence[T]], bool] | None = None,
    ) -> None:
        if max_items < 1:
            raise ValueError("max_items must be positive")
        self._max_items = max_items
        self._limit_reached = False

        async def bounded_fetch(token: TToken | None) -> tuple[Sequence[T], TToken | None]:
            if token is None:
                self._limit_reached = False
            page, next_token = await fetch_next(token)
            rows = list(page)
            loaded = 0 if token is None else len(self.items)
            remaining = max(0, self._max_items - loaded)
            accepted = rows[:remaining]
            if len(rows) > remaining or (next_token is not None and len(accepted) == remaining):
                self._limit_reached = True
                next_token = None
            return accepted, next_token

        super().__init__(
            bounded_fetch,
            auto_construct_on_add=auto_construct_on_add,
            pages_equal=pages_equal,
        )

    @property
    def has_more(self) -> bool:
        return self.current_token is not None and len(self.items) < self._max_items

    @property
    def limit_reached(self) -> bool:
        return self._limit_reached or (
            len(self.items) >= self._max_items and self.current_token is not None
        )


__all__ = ["BoundedTokenPagedComposition"]
