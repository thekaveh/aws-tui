"""Exact saved-service destinations through the ordinary selector paths.

This preflight operates only on an isolated candidate. The navigation owner
decides whether to adopt it; resolving never commits live selection stores.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

from aws_tui.domain.navigation_workspace import NavigationTarget, WorkspaceError
from aws_tui.vm.athena.page_vm import AthenaPageVM
from aws_tui.vm.emr_serverless.page_vm import EmrServerlessPageVM
from aws_tui.vm.file_manager.pane_vm import PaneState
from aws_tui.vm.glue.page_vm import GluePageVM

MAX_DESTINATION_PAGES = 64
_AVAILABLE = {PaneState.IDLE, PaneState.EMPTY}


class WorkspaceNavigationError(WorkspaceError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.args = (
            {
                "destination_missing": "A saved destination no longer exists; current navigation was kept",
                "destination_unavailable": "A saved destination could not be verified; current navigation was kept",
            }[code],
        )


def _checkpoint() -> None:
    task = asyncio.current_task()
    if task is not None and task.cancelling():
        raise asyncio.CancelledError


def _available(state: PaneState) -> None:
    _checkpoint()
    if state not in _AVAILABLE:
        raise WorkspaceNavigationError("destination_unavailable")


async def _find(
    value: str,
    *,
    names: Callable[[], tuple[str, ...]],
    state: Callable[[], PaneState],
    more: Callable[[], bool],
    limited: Callable[[], bool],
    load_more: Callable[[], Awaitable[None]],
) -> None:
    for _ in range(MAX_DESTINATION_PAGES + 1):
        _available(state())
        if value in names():
            return
        if limited():
            raise WorkspaceNavigationError("destination_unavailable")
        if not more():
            raise WorkspaceNavigationError(
                "destination_unavailable" if limited() else "destination_missing"
            )
        if _ == MAX_DESTINATION_PAGES:
            break
        await load_more()
    raise WorkspaceNavigationError("destination_unavailable")


async def resolve_service_target(page: object, target: NavigationTarget) -> None:
    """Set up and exactly resolve one fresh AWS-service candidate, without replay."""
    expected = {
        "athena": AthenaPageVM,
        "glue": GluePageVM,
        "emr-serverless": EmrServerlessPageVM,
    }.get(target.service)
    if expected is None or not isinstance(page, expected):
        raise WorkspaceError("invalid")
    if not isinstance(page, (AthenaPageVM, GluePageVM, EmrServerlessPageVM)) or (
        page.source.connection_key != (target.connection, target.region)
    ):
        raise WorkspaceError("invalid")
    _checkpoint()
    await page.setup()
    _checkpoint()
    selected = dict(target.selectors)
    select: Callable[[str], Awaitable[None]]
    if isinstance(page, AthenaPageVM):
        for field, names, state, more, limited, load_more, select in (
            (
                "workgroup",
                lambda: tuple(row.name for row in page.workgroups),
                lambda: page.workgroups_state,
                lambda: page.has_more_workgroups,
                lambda: page.workgroup_limit_reached,
                page.load_more_workgroups,
                page.select_workgroup,
            ),
            (
                "catalog",
                lambda: tuple(row.name for row in page.catalogs),
                lambda: page.catalogs_state,
                lambda: page.has_more_catalogs,
                lambda: page.catalog_limit_reached,
                page.load_more_catalogs,
                page.select_catalog,
            ),
            (
                "database",
                lambda: tuple(row.ref.database_name for row in page.databases),
                lambda: page.databases_state,
                lambda: page.has_more_databases,
                lambda: page.database_limit_reached,
                page.load_more_databases,
                page.select_database,
            ),
        ):
            value = selected.get(field)
            if value is None:
                continue
            await _find(
                value, names=names, state=state, more=more, limited=limited, load_more=load_more
            )
            if field == "workgroup" and not any(
                row.name == value and row.state == "ENABLED" for row in page.workgroups
            ):
                raise WorkspaceNavigationError("destination_unavailable")
            if getattr(page.context, field) != value:
                await select(value)
            _checkpoint()
            if getattr(page.context, field) != value:
                raise WorkspaceNavigationError("destination_unavailable")
        _available(page.workgroups_state)
        if "workgroup" in selected:
            _available(page.workgroup_detail_state)
            if page.workgroup_detail is None or page.workgroup_detail.summary.state != "ENABLED":
                raise WorkspaceNavigationError("destination_unavailable")
    elif isinstance(page, GluePageVM):
        catalog = page.catalog
        for field, names, state, more, limited, load_more, select in (
            (
                "database",
                lambda: tuple(row.ref.database_name for row in catalog.databases),
                lambda: catalog.databases_state,
                lambda: catalog.has_more_databases,
                lambda: catalog.database_limit_reached,
                catalog.load_more_databases,
                page.select_database,
            ),
            (
                "table",
                lambda: tuple(row.ref.table_name for row in catalog.tables),
                lambda: catalog.tables_state,
                lambda: catalog.has_more_tables,
                lambda: catalog.table_limit_reached,
                catalog.load_more_tables,
                page.select_table,
            ),
        ):
            value = selected.get(field)
            if value is None:
                continue
            await _find(
                value, names=names, state=state, more=more, limited=limited, load_more=load_more
            )
            if getattr(catalog, f"selected_{field}_name") != value:
                await select(value)
            _checkpoint()
            if getattr(catalog, f"selected_{field}_name") != value:
                raise WorkspaceNavigationError("destination_unavailable")
        _available(catalog.databases_state)
        if "table" in selected:
            _available(catalog.detail_state)
    else:
        _available(page.applications.state)
        value = selected.get("application")
        if value is not None:
            if value not in {row.id for row in page.applications.applications}:
                raise WorkspaceNavigationError("destination_missing")
            if page.applications.selected_id != value:
                await page.select_application(value)
            _checkpoint()
            if page.applications.selected_id != value:
                raise WorkspaceNavigationError("destination_unavailable")
    _checkpoint()
