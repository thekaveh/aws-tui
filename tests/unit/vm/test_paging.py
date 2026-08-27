from __future__ import annotations

import pytest

from aws_tui.vm.paging import BoundedTokenPagedComposition


@pytest.mark.asyncio
async def test_bounded_token_pager_clips_final_page_and_stops_continuation() -> None:
    pages = {
        None: (["one", "two"], "next"),
        "next": (["three", "four"], "more"),
    }

    async def fetch(token: str | None) -> tuple[list[str], str | None]:
        return pages[token]

    pager = BoundedTokenPagedComposition(fetch, max_items=3)
    await pager.refresh_command.execute_async()
    await pager.load_more_command.execute_async()

    assert pager.items == ["one", "two", "three"]
    assert not pager.has_more
    assert pager.current_token is None
    assert pager.limit_reached


def test_bounded_token_pager_requires_positive_limit() -> None:
    async def fetch(_token: str | None) -> tuple[list[str], str | None]:
        return [], None

    with pytest.raises(ValueError, match="max_items must be positive"):
        BoundedTokenPagedComposition(fetch, max_items=0)
