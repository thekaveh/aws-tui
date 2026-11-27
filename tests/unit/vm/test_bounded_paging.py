"""A superseded continuation must not set the limit flag for the list that replaced it."""

from __future__ import annotations

import asyncio

import pytest

from aws_tui.vm.paging import BoundedTokenPagedComposition


@pytest.mark.asyncio
async def test_superseded_continuation_does_not_set_the_limit_for_the_refreshed_list() -> None:
    continuation_gate = asyncio.Event()
    refresh_gate = asyncio.Event()
    continuation_started = asyncio.Event()
    refreshes = 0

    async def fetch(token: str | None) -> tuple[list[int], str | None]:
        nonlocal refreshes
        if token is None:
            refreshes += 1
            if refreshes == 1:
                return list(range(1, 9)), "t1"
            await refresh_gate.wait()
            return [101, 102, 103], "R1"
        continuation_started.set()
        await continuation_gate.wait()
        return [9, 10, 11], "t2"

    pager = BoundedTokenPagedComposition(fetch, max_items=10)
    await pager.refresh_command.execute_async()
    continuation = asyncio.create_task(pager.load_more_command.execute_async())
    await continuation_started.wait()
    refresh = asyncio.create_task(pager.refresh_command.execute_async())
    await asyncio.sleep(0)

    # The continuation resumes while the refresh is still in flight, so it sees the old list.
    continuation_gate.set()
    await continuation
    refresh_gate.set()
    await refresh

    assert pager.items == [101, 102, 103]
    assert not pager.limit_reached
