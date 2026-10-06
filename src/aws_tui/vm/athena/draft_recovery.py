"""Fresh, bounded validation of an exact draft source and Athena context."""

from collections.abc import Awaitable, Callable
from typing import Any, TypeVar

from aws_tui.domain.query import QueryContext
from aws_tui.vm.athena._domain_validation import (
    valid_athena_catalog_summary,
    valid_athena_workgroup_detail,
    valid_athena_workgroup_summary,
    valid_database_summary,
)

T = TypeVar("T")


async def _contains_exact(
    fetch: Callable[[str | None], Awaitable[tuple[list[T], str | None]]],
    *,
    valid: Callable[[T], bool],
    matches: Callable[[T], bool],
    identity: Callable[[T], object],
) -> bool:
    token: str | None = None
    seen_tokens: set[str] = set()
    seen_rows: set[object] = set()
    empty_pages = 0
    for _ in range(64):
        rows, next_token = await fetch(token)
        if type(rows) is not list or not all(valid(row) for row in rows):
            return False
        if next_token is not None and (type(next_token) is not str or not next_token):
            return False
        for row in rows:
            key = identity(row)
            if key in seen_rows:
                return False
            seen_rows.add(key)
        if len(seen_rows) > 1_000:
            return False
        if any(matches(row) for row in rows):
            return True
        empty_pages = empty_pages + 1 if not rows else 0
        if empty_pages > 3 or next_token is None or next_token in seen_tokens:
            return False
        seen_tokens.add(next_token)
        token = next_token
    return False


async def validate_draft_context(
    *,
    context: QueryContext,
    client: Any,
    source_is_current: Callable[[], Awaitable[bool]],
) -> bool:
    try:
        if not all(type(part) is str and bool(part) for part in context.cache_key):
            return False
        if not await source_is_current():
            return False
        if not await _contains_exact(
            lambda token: client.list_workgroups_page(start_token=token),
            valid=valid_athena_workgroup_summary,
            matches=lambda row: row.name == context.workgroup,
            identity=lambda row: row.name,
        ):
            return False
        detail = await client.get_workgroup(context.workgroup)
        if (
            not valid_athena_workgroup_detail(detail)
            or detail.summary.name != context.workgroup
            or detail.summary.state != "ENABLED"
        ):
            return False
        if not await _contains_exact(
            lambda token: client.list_catalogs_page(workgroup=context.workgroup, start_token=token),
            valid=valid_athena_catalog_summary,
            matches=lambda row: row.name == context.catalog,
            identity=lambda row: row.name,
        ):
            return False
        if not await _contains_exact(
            lambda token: client.list_databases_page(
                context.catalog, workgroup=context.workgroup, start_token=token
            ),
            valid=lambda row: (
                valid_database_summary(row)
                and row.ref.connection_name == context.connection_name
                and row.ref.region == context.region
                and row.ref.catalog_name == context.catalog
            ),
            matches=lambda row: row.ref.database_name == context.database,
            identity=lambda row: row.ref,
        ):
            return False
        return await source_is_current()
    except Exception:
        # Provider/configuration failures never transport raw exceptions or payloads.
        return False
