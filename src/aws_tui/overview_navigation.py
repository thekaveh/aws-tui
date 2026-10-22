"""Prepare exact existing detail pages; publication belongs to App navigation."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

from aws_tui.domain.navigation_workspace import NavigationTarget, WorkspaceError
from aws_tui.domain.scoped_overview import OverviewEntry
from aws_tui.infra.connection_resolver import Connection
from aws_tui.services.athena import AthenaService
from aws_tui.services.emr_serverless import EmrServerlessService
from aws_tui.services.glue import GlueService
from aws_tui.vm.athena.page_vm import AthenaPageVM
from aws_tui.vm.emr_serverless.page_vm import EmrServerlessPageVM
from aws_tui.vm.file_manager.pane_vm import PaneState
from aws_tui.vm.glue.page_vm import GluePageVM
from aws_tui.vm.services_protocol import RecoveryServiceVM, ServiceRegistry
from aws_tui.vm.workspace_navigation import find_navigation_value, resolve_service_target
from aws_tui.workspace_restore import retire_workspace_candidate

_AVAILABLE = {PaneState.IDLE, PaneState.EMPTY}


def _unavailable() -> ValueError:
    return ValueError("Overview destination is unavailable; current navigation was retained")


@dataclass(frozen=True, slots=True)
class PreparedOverview:
    service_id: str
    connection: Connection
    recovery: RecoveryServiceVM


async def prepare_overview_entry(
    entry: OverviewEntry, connection: Connection, registry: ServiceRegistry
) -> PreparedOverview:
    """Keep ownership of reads and a success-delivery race through caller cancellation."""
    if connection.kind != "aws" or (entry.scope.connection_name, entry.scope.region) != (
        connection.name,
        connection.region,
    ):
        raise ValueError("Overview source changed")
    task = asyncio.create_task(_prepare(entry, connection, registry))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        if not task.done():
            task.cancel()
        while not task.done():
            try:
                await asyncio.shield(task)
            except (Exception, asyncio.CancelledError):
                continue
        if not task.cancelled() and task.exception() is None:
            await retire_workspace_candidate(task.result().recovery.vm)
        raise


async def _prepare(
    entry: OverviewEntry, connection: Connection, registry: ServiceRegistry
) -> PreparedOverview:
    scope = entry.scope
    service = registry.get(scope.service)
    if scope.kind == "glue-crawler" and entry.identifier != scope.resource:
        raise _unavailable()
    if isinstance(service, GlueService) and scope.kind in {"glue-job", "glue-crawler"}:
        recovery = service.build_overview_vm(connection, scope)
        target = None
    elif isinstance(service, AthenaService) and scope.kind == "athena":
        target = NavigationTarget(
            "athena", connection.name, connection.region, (("workgroup", scope.resource),)
        )
        recovery = service.build_navigation_vm(connection, target)
    elif isinstance(service, EmrServerlessService) and scope.kind == "emr-serverless":
        target = NavigationTarget(
            "emr-serverless", connection.name, connection.region, (("application", scope.resource),)
        )
        recovery = service.build_navigation_vm(connection, target)
    else:
        raise _unavailable()
    page = recovery.vm
    try:
        if not isinstance(page, (AthenaPageVM, GluePageVM, EmrServerlessPageVM)):
            raise _unavailable()
        page.construct()
        if target is not None:
            await resolve_service_target(page, target)
        else:
            await page.setup()
        await _select_entry(page, entry)
        return PreparedOverview(scope.service, connection, recovery)
    except BaseException as error:
        await retire_workspace_candidate(page)
        if isinstance(error, WorkspaceError):
            raise _unavailable() from None
        raise


async def _select_entry(page: object, entry: OverviewEntry) -> None:
    resource, identity = entry.scope.resource, entry.identifier
    if isinstance(page, AthenaPageVM):
        await page.select_view("history")
        history = page.history
        await find_navigation_value(
            identity,
            names=lambda: tuple(row.ref.execution_id for row in history.items),
            state=lambda: history.state,
            more=lambda: history.has_more,
            limited=lambda: history.limit_reached,
            load_more=history.load_more,
        )
        await history.select_execution(identity)
        detail = history.detail
        expected_source = (entry.scope.connection_name, entry.scope.region, resource)
        if (
            history.selected_execution_id != identity
            or page.context.workgroup != resource
            or (
                detail is None
                or (
                    detail.summary.ref.connection_name,
                    detail.summary.ref.region,
                    detail.summary.ref.workgroup,
                )
                != expected_source
                or (detail.context.connection_name, detail.context.region, detail.context.workgroup)
                != expected_source
                or detail.summary.ref.execution_id != identity
            )
        ):
            raise _unavailable()
    elif isinstance(page, GluePageVM) and entry.scope.kind == "glue-job":
        jobs = page.jobs
        await find_navigation_value(
            resource,
            names=lambda: tuple(row.name for row in jobs.jobs),
            state=lambda: jobs.jobs_state,
            more=lambda: jobs.has_more_jobs,
            limited=lambda: jobs.job_limit_reached,
            load_more=jobs.load_more_jobs,
        )
        if jobs.selected_job_name != resource:
            await page.select_job(resource)
        await find_navigation_value(
            identity,
            names=lambda: tuple(row.run_id for row in jobs.runs),
            state=lambda: jobs.runs_state,
            more=lambda: jobs.has_more_runs,
            limited=lambda: jobs.run_limit_reached,
            load_more=jobs.load_more_runs,
        )
        page.select_job_run(identity)
        selected = jobs.selected_run
        if (
            jobs.selected_job_name != resource
            or jobs.selected_run_id != identity
            or (selected is None or (selected.job_name, selected.run_id) != (resource, identity))
        ):
            raise _unavailable()
    elif isinstance(page, GluePageVM) and entry.scope.kind == "glue-crawler":
        crawlers = page.crawlers
        await find_navigation_value(
            resource,
            names=lambda: tuple(row.name for row in crawlers.crawlers),
            state=lambda: crawlers.state,
            more=lambda: crawlers.has_more_crawlers,
            limited=lambda: crawlers.limit_reached,
            load_more=crawlers.load_more_crawlers,
        )
        if crawlers.selected_crawler_name != resource:
            await page.select_crawler(resource)
        crawler_detail = crawlers.crawler_detail
        if (
            crawlers.selected_crawler_name != resource
            or crawlers.detail_state not in _AVAILABLE
            or (crawler_detail is None or crawler_detail.summary.name != resource)
        ):
            raise _unavailable()
    elif isinstance(page, EmrServerlessPageVM):
        runs = page.job_runs
        await find_navigation_value(
            identity,
            names=lambda: tuple(row.job_run_id for row in runs.runs),
            state=lambda: runs.state,
            more=lambda: runs.has_more,
            limited=lambda: runs.limit_reached,
            load_more=page.load_more_job_runs,
        )
        await page.select_job_run(identity)
        run_detail = page.job_run_detail.detail
        if (
            page.applications.selected_id != resource
            or runs.selected_id != identity
            or (
                run_detail is None
                or (run_detail.application_id, run_detail.job_run_id) != (resource, identity)
            )
        ):
            raise _unavailable()
    else:
        raise _unavailable()
