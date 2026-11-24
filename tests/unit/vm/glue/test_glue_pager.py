"""``bounded_glue_pager`` fetches owned, generation-checked, cycle-checked pages."""

from __future__ import annotations

import pytest

from aws_tui.vm.glue._lifecycle import GlueOperationOwner, GlueOperationSuperseded
from aws_tui.vm.glue._pager import bounded_glue_pager


async def test_current_pager_returns_rows() -> None:
    calls: list[str | None] = []

    async def list_page(token: str | None) -> tuple[list[str], str | None]:
        calls.append(token)
        return ["a", "b"], None

    pager = bounded_glue_pager(
        GlueOperationOwner(),
        list_page,
        is_current=lambda: True,
        cycle_message="cycle",
        max_items=10,
    )
    await pager.refresh_command.execute_async()
    assert list(pager.items) == ["a", "b"]
    assert calls == [None]
    pager.dispose()


async def test_generation_move_during_the_call_drops_the_page() -> None:
    current = True

    async def list_page(token: str | None) -> tuple[list[str], str | None]:
        nonlocal current
        current = False
        return ["a"], None

    pager = bounded_glue_pager(
        GlueOperationOwner(),
        list_page,
        is_current=lambda: current,
        cycle_message="cycle",
        max_items=10,
    )
    await pager.refresh_command.execute_async()
    assert list(pager.items) == []
    pager.dispose()


async def test_stale_pager_raises_superseded_before_the_call() -> None:
    calls: list[str | None] = []

    async def list_page(token: str | None) -> tuple[list[str], str | None]:
        calls.append(token)
        return [], None

    pager = bounded_glue_pager(
        GlueOperationOwner(),
        list_page,
        is_current=lambda: False,
        cycle_message="cycle",
        max_items=10,
    )
    with pytest.raises(GlueOperationSuperseded):
        await pager.refresh_command.execute_async()
    assert calls == []
    pager.dispose()


async def test_unbound_pager_returns_an_empty_page_without_a_call() -> None:
    calls: list[str | None] = []

    async def list_page(token: str | None) -> tuple[list[str], str | None]:
        calls.append(token)
        return ["a"], None

    pager = bounded_glue_pager(
        GlueOperationOwner(),
        list_page,
        is_current=lambda: True,
        cycle_message="cycle",
        max_items=10,
        unbound=True,
    )
    await pager.refresh_command.execute_async()
    assert calls == []
    assert list(pager.items) == []
    pager.dispose()
