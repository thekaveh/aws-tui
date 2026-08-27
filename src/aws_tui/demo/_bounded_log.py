"""List-compatible bounded recorder used by stateful demo clients."""

from __future__ import annotations

from collections.abc import Iterable
from typing import TypeVar

T = TypeVar("T")

MAX_RECORDED_CALLS = 2_048


class BoundedCallLog(list[T]):
    """Preserve list semantics while discarding the oldest call records."""

    def append(self, item: T) -> None:
        super().append(item)
        self._trim()

    def extend(self, items: Iterable[T]) -> None:
        super().extend(items)
        self._trim()

    def _trim(self) -> None:
        overflow = len(self) - MAX_RECORDED_CALLS
        if overflow > 0:
            del self[:overflow]


__all__ = ["MAX_RECORDED_CALLS", "BoundedCallLog"]
