from __future__ import annotations

from collections.abc import Awaitable, Callable, Hashable, Sequence
from typing import TypeVar

from aws_tui.domain.filesystem import ProviderError

T = TypeVar("T")
TToken = TypeVar("TToken", bound=Hashable)


def reject_token_cycles(
    fetch: Callable[
        [TToken | None],
        Awaitable[tuple[Sequence[T], TToken | None]],
    ],
    *,
    message: str,
) -> Callable[
    [TToken | None],
    Awaitable[tuple[Sequence[T], TToken | None]],
]:
    """Guard a VMx token-page fetcher against cyclic continuation tokens."""
    consumed_tokens: set[TToken] = set()

    async def guarded(token: TToken | None) -> tuple[Sequence[T], TToken | None]:
        page, next_token = await fetch(token)
        if token is None:
            consumed_tokens.clear()
        else:
            consumed_tokens.add(token)
        if next_token is not None and next_token in consumed_tokens:
            raise ProviderError(message)
        return page, next_token

    return guarded


__all__ = ["reject_token_cycles"]
