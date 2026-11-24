"""One bounded token pager for the Glue VMs."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import TypeVar

from aws_tui.vm._token_paging import reject_token_cycles
from aws_tui.vm.glue._lifecycle import GlueOperationOwner, GlueOperationSuperseded
from aws_tui.vm.paging import BoundedTokenPagedComposition

T = TypeVar("T")


def bounded_glue_pager(
    operations: GlueOperationOwner,
    list_page: Callable[[str | None], Awaitable[tuple[list[T], str | None]]],
    *,
    is_current: Callable[[], bool],
    cycle_message: str,
    max_items: int,
    unbound: bool = False,
) -> BoundedTokenPagedComposition[T, str]:
    """Build a pager whose pages are owned, generation-checked and cycle-checked.

    ``unbound`` means that no target is selected. The pager then returns an empty page
    and never calls the operation owner.
    """

    async def fetch(token: str | None) -> tuple[list[T], str | None]:
        if unbound:
            return [], None

        async def run_page() -> tuple[list[T], str | None]:
            if not is_current():
                raise GlueOperationSuperseded
            return await list_page(token)

        rows, next_token = await operations.run(run_page)
        if not is_current():
            return [], None
        return rows, next_token

    return BoundedTokenPagedComposition(
        reject_token_cycles(fetch, message=cycle_message),
        max_items=max_items,
    )
