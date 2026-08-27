from __future__ import annotations

import pytest

from aws_tui.domain.filesystem import ProviderError
from aws_tui.vm._token_paging import reject_token_cycles


@pytest.mark.asyncio
async def test_reject_token_cycles_rejects_immediate_repeat_before_returning_page() -> None:
    async def fetch(token: str | None) -> tuple[list[str], str | None]:
        return [str(token)], "A"

    guarded = reject_token_cycles(fetch, message="provider repeated token")
    assert await guarded(None) == (["None"], "A")

    with pytest.raises(ProviderError, match="provider repeated token"):
        await guarded("A")


@pytest.mark.asyncio
async def test_reject_token_cycles_rejects_multi_token_cycle_and_resets_on_refresh() -> None:
    next_tokens = {None: "A", "A": "B", "B": "A"}

    async def fetch(token: str | None) -> tuple[list[str], str | None]:
        return [str(token)], next_tokens[token]

    guarded = reject_token_cycles(fetch, message="provider repeated token")
    assert await guarded(None) == (["None"], "A")
    assert await guarded("A") == (["A"], "B")
    with pytest.raises(ProviderError, match="provider repeated token"):
        await guarded("B")

    assert await guarded(None) == (["None"], "A")
