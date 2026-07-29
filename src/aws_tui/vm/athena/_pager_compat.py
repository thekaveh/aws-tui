from __future__ import annotations

from collections.abc import Sequence
from typing import TypeVar

from vmx.collections.token_paged_composition import TokenPagedComposition

T = TypeVar("T")
TToken = TypeVar("TToken")

_REQUIRED_INTERNALS = ("_items", "_current_token", "_loaded_once")
_CONTRACT_ERROR = (
    "VMx TokenPagedComposition internals changed; update Athena snapshot pager compatibility"
)


def seed_token_pager(
    pager: TokenPagedComposition[T, TToken],
    items: Sequence[T],
    next_token: TToken | None,
) -> None:
    """Restore a VMx 3.x token pager without fetching or emitting callbacks."""
    if not isinstance(pager, TokenPagedComposition) or not all(
        hasattr(pager, name) for name in _REQUIRED_INTERNALS
    ):
        raise RuntimeError(_CONTRACT_ERROR)
    if type(pager._items) is not list or type(pager._loaded_once) is not bool:
        raise RuntimeError(_CONTRACT_ERROR)

    seeded_items = list(items)
    pager._items = seeded_items
    pager._current_token = next_token
    pager._loaded_once = True

    if (
        pager.items != seeded_items
        or pager.current_token != next_token
        or pager.has_more != (next_token is not None)
    ):
        raise RuntimeError(_CONTRACT_ERROR)


__all__ = ["seed_token_pager"]
