"""Prepare all saved navigation off-screen; publication remains with the app's navigation owner."""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Awaitable, Callable, Collection
from dataclasses import dataclass
from typing import Protocol

from vmx import Message, MessageHub

from aws_tui.domain.filesystem import ProviderError
from aws_tui.domain.navigation_workspace import (
    Bookmark,
    NavigationTarget,
    Workspace,
    WorkspaceError,
)
from aws_tui.domain.unavailable_navigation_fs import UnavailableNavigationFS
from aws_tui.domain.workspace_store import WorkspaceStore
from aws_tui.infra.connection_resolver import Connection, ConnectionNotFound
from aws_tui.services.athena.service import AthenaService
from aws_tui.services.emr_serverless.service import EmrServerlessService
from aws_tui.services.glue.service import GlueService
from aws_tui.services.s3.service import S3Service
from aws_tui.vm.athena.page_vm import AthenaPageVM
from aws_tui.vm.emr_serverless.page_vm import EmrServerlessPageVM
from aws_tui.vm.file_manager.dual_pane_vm import DualPaneVM
from aws_tui.vm.file_manager.pane_vm import PaneVM
from aws_tui.vm.file_manager.transfer_runtime import OwnedDiskOperations
from aws_tui.vm.glue.page_vm import GluePageVM
from aws_tui.vm.messages import ConnectionListChangedMessage
from aws_tui.vm.services_protocol import RecoveryServiceVM, ServiceNotFound, ServiceRegistry
from aws_tui.vm.workspace_navigation import WorkspaceNavigationError, resolve_service_target
from aws_tui.vm.workspace_panes import capture_pane_target

NavigationService = AthenaService | GlueService | EmrServerlessService


@dataclass(frozen=True, slots=True)
class PreparedWorkspace:
    service_id: str
    connection: Connection | None
    recovery: RecoveryServiceVM


@dataclass(frozen=True, slots=True)
class PreparedPaneBookmark:
    is_current: Callable[[], bool]
    commit: Callable[[], None]


async def validate_saved_navigation(
    workspace: Workspace, bookmark: Bookmark | None, store: WorkspaceStore
) -> None:
    """Reject a stale selected record without blocking the application pump."""
    records = await OwnedDiskOperations().call(store.list)
    current = next((record for record in records if record.name == workspace.name), None)
    if current is None or (
        current != workspace if bookmark is None else bookmark not in current.bookmarks
    ):
        raise WorkspaceError("changed")


async def prepare_pane_bookmark(
    pane: PaneVM,
    target: NavigationTarget,
    service: S3Service,
    sources: tuple[Connection, ...],
) -> PreparedPaneBookmark:
    """Use normal provider recovery for one pane, with strict destination reads."""

    async def prepare() -> PreparedPaneBookmark:
        candidate = service.build_navigation_pane(target, sources)
        try:
            stage = await pane.stage_provider_recovery(
                candidate.provider,
                path=candidate.path,
                identity_label=candidate.identity_label,
                path_protocol=candidate.path_protocol,
                connection_key=candidate.current_connection_key,
                connection_region=candidate.current_connection_region,
                transfer_connection=candidate.transfer_connection,
                strict_missing=True,
            )

            def commit() -> None:
                if not pane.can_commit_provider_recovery(stage):
                    raise WorkspaceError("changed")
                pane.commit_provider_recovery(stage)

            return PreparedPaneBookmark(lambda: pane.can_commit_provider_recovery(stage), commit)
        except ProviderError:
            raise WorkspaceNavigationError("destination_unavailable") from None
        finally:
            candidate.dispose()

    task = asyncio.create_task(prepare())
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        if not task.done() and not task.cancelling():
            task.cancel()
        while not task.done():
            try:
                await asyncio.shield(task)
            except (Exception, asyncio.CancelledError):
                continue
        if not task.cancelled():
            task.exception()
        raise


async def prepare_file_bookmark(
    target: NavigationTarget,
    companion: NavigationTarget | None,
    service: S3Service,
    sources: tuple[Connection, ...],
    *,
    resolver: WorkspaceSourceResolver | None = None,
    hub: MessageHub[Message] | None = None,
) -> PreparedWorkspace:
    """Own one strict bookmark candidate, retaining a nonblocking companion pane."""

    async def prepare() -> PreparedWorkspace:
        loader = None
        if (
            resolver is not None
            and companion is not None
            and companion.connection is not None
            and not any(
                (source.name, source.region) == (companion.connection, companion.region)
                for source in sources
            )
        ):
            companion_target = companion
            companion_resolver = resolver

            async def load_companion(pane: PaneVM) -> None:
                if isinstance(pane.provider, UnavailableNavigationFS):
                    # Establish a truthful fallback before the deferred read.
                    await pane.refresh()
                with pane.navigation_loading():
                    await resolve_companion(pane)

            async def resolve_companion(pane: PaneVM) -> None:
                original_provider, original_path, revision = (
                    pane.provider,
                    pane.path,
                    pane.listing_revision,
                )
                changed = False

                def observed(_property: str) -> None:
                    nonlocal changed
                    if _property in {
                        "path",
                        "filter_text",
                        "filtered_entries",
                        "cursor_index",
                        "is_multiselect_mode",
                        "viewmodel",
                    }:
                        changed = True

                def owns() -> bool:
                    return (
                        not changed
                        and pane.provider is original_provider
                        and pane.path == original_path
                        and pane.listing_revision == revision
                    )

                def source_changed(message: object) -> None:
                    nonlocal changed
                    if (
                        isinstance(message, ConnectionListChangedMessage)
                        and companion_target.connection in message.names
                    ):
                        changed = True

                subscription = pane.on_property_changed.subscribe(observed)
                source_subscription = (
                    hub.messages.subscribe(source_changed) if hub is not None else None
                )
                try:
                    resolved = await resolve_bookmark_sources(companion_target, companion_resolver)
                    if not owns():
                        return
                    prepared = await prepare_pane_bookmark(
                        pane, companion_target, service, resolved
                    )
                    if (
                        await resolve_bookmark_sources(companion_target, companion_resolver)
                        != resolved
                    ):
                        raise WorkspaceError("changed")
                    if owns() and prepared.is_current():
                        prepared.commit()
                except WorkspaceError:
                    if owns():
                        await pane.refresh()
                finally:
                    subscription.dispose()
                    if source_subscription is not None:
                        source_subscription.dispose()

            loader = load_companion
        stage = service.build_bookmark_vm(target, companion, sources, companion_loader=loader)
        try:
            stage.vm.construct()  # type: ignore[attr-defined]
            await stage.vm.setup()  # type: ignore[attr-defined]
            return PreparedWorkspace(
                "s3", _source(sources, target.connection, target.region), stage
            )
        except BaseException:
            await retire_workspace_candidate(stage.vm)
            raise

    task = asyncio.create_task(prepare())
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        if not task.done() and not task.cancelling():
            task.cancel()
        while not task.done():
            try:
                await asyncio.shield(task)
            except (Exception, asyncio.CancelledError):
                continue
        if not task.cancelled() and task.exception() is None:
            await retire_workspace_candidate(task.result().recovery.vm)
        raise


class WorkspaceSourceResolver(Protocol):
    def resolve_selected(self, name: str) -> Connection: ...


def capture_workspace(
    name: str,
    registry: ServiceRegistry,
    service_id: str | None,
    connection: Connection | None,
    current: object,
) -> Workspace:
    """Capture only allowlisted current intent; never an execution/session snapshot."""
    try:
        s3 = registry.get("s3")
    except ServiceNotFound:
        raise WorkspaceError("invalid") from None
    if not isinstance(s3, S3Service):
        raise WorkspaceError("invalid")
    panes: tuple[NavigationTarget, NavigationTarget] | None
    if service_id == "s3":
        if not isinstance(current, DualPaneVM):
            raise WorkspaceError("invalid")
        panes = (capture_pane_target(current.left), capture_pane_target(current.right))
    else:
        expected = {
            "athena": AthenaPageVM,
            "glue": GluePageVM,
            "emr-serverless": EmrServerlessPageVM,
        }.get(service_id or "")
        if (
            expected is None
            or not isinstance(current, expected)
            or not isinstance(current, (AthenaPageVM, GluePageVM, EmrServerlessPageVM))
            or connection is None
            or current.source.connection_key != (connection.name, connection.region)
        ):
            raise WorkspaceError("invalid")
        panes = s3.navigation_panes()
    scopes = tuple(
        target
        for service in ("athena", "glue", "emr-serverless")
        if service in registry
        for target in _navigation_service(registry, service).navigation_targets()
    )
    assert service_id is not None
    return Workspace(
        name,
        service_id,
        connection.name if connection else None,
        connection.region if connection else None,
        panes,
        scopes,
    )


async def resolve_workspace_sources(
    workspace: Workspace, resolver: WorkspaceSourceResolver
) -> tuple[Connection, ...]:
    """Read only referenced current sources off-pump, retaining no stale credentials."""
    references = {
        (target.connection, target.region)
        for target in (*workspace.scopes, *(workspace.file_panes or ()))
        if target.connection is not None
    }
    if workspace.connection is not None:
        references.add((workspace.connection, workspace.region))
    return await _resolve_sources(references, resolver)


async def resolve_bookmark_sources(
    target: NavigationTarget,
    resolver: WorkspaceSourceResolver,
) -> tuple[Connection, ...]:
    """Resolve only one destination; unrelated saved sources are not dependencies."""
    references = {(target.connection, target.region)} if target.connection is not None else set()
    return await _resolve_sources(references, resolver)


async def _resolve_sources(
    references: Collection[tuple[str | None, str | None]],
    resolver: WorkspaceSourceResolver,
) -> tuple[Connection, ...]:

    def read() -> tuple[Connection, ...]:
        selected: dict[str, Connection] = {}
        for name, region in sorted(references):
            assert name is not None
            if name not in selected:
                try:
                    selected[name] = resolver.resolve_selected(name)
                except ConnectionNotFound:
                    raise WorkspaceError("invalid") from None
                except Exception:
                    raise WorkspaceNavigationError("destination_unavailable") from None
            if (selected[name].name, selected[name].region) != (name, region):
                raise WorkspaceError("invalid")
        return tuple(selected.values())

    return await OwnedDiskOperations().call(read)


def _source(
    sources: tuple[Connection, ...], name: str | None, region: str | None
) -> Connection | None:
    if name is None and region is None:
        return None
    matches = [source for source in sources if (source.name, source.region) == (name, region)]
    if len(matches) != 1:
        raise WorkspaceError("invalid")
    return matches[0]


async def _retire_candidates(candidates: tuple[object, ...]) -> None:
    async def retire() -> None:
        failure: BaseException | None = None
        for candidate in candidates:
            try:
                shutdown = getattr(candidate, "shutdown", None)
                if callable(shutdown):
                    result = shutdown()
                    if isinstance(result, Awaitable):
                        await result
            except BaseException as error:
                failure = failure or error
            finally:
                try:
                    dispose = getattr(candidate, "dispose", None)
                    if not callable(dispose):
                        raise WorkspaceError("invalid")
                    dispose()
                except BaseException as error:
                    failure = failure or error
        if failure is not None:
            raise failure

    # Cleanup itself owns one task. Repeated caller cancellation cannot strand
    # any remaining candidate after the first one's resistant reads drain.
    task = asyncio.create_task(retire())
    canceled = False
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            canceled = True
        except Exception:
            break
    try:
        task.result()
    finally:
        if canceled:
            raise asyncio.CancelledError


async def retire_workspace_candidate(candidate: object) -> None:
    """Drain and dispose an unhosted candidate; never pass adopted live content here."""
    await _retire_candidates((candidate,))


def _navigation_service(registry: ServiceRegistry, service_id: str) -> NavigationService:
    try:
        service = registry.get(service_id)
    except ServiceNotFound:
        raise WorkspaceError("invalid") from None
    expected = {
        "athena": AthenaService,
        "glue": GlueService,
        "emr-serverless": EmrServerlessService,
    }.get(service_id)
    if (
        expected is None
        or not isinstance(service, expected)
        or not isinstance(service, (AthenaService, GlueService, EmrServerlessService))
    ):
        raise WorkspaceError("invalid")
    return service


async def prepare_workspace(
    workspace: Workspace, registry: ServiceRegistry, sources: tuple[Connection, ...]
) -> PreparedWorkspace:
    """Own the whole attempt so repeated caller cancellation cannot interrupt RPC cleanup."""
    task = asyncio.create_task(_prepare_workspace(workspace, registry, sources))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        if not task.done() and not task.cancelling():
            task.cancel()
        while not task.done():
            try:
                await asyncio.shield(task)
            except (Exception, asyncio.CancelledError):
                continue
        if not task.cancelled() and task.exception() is None:
            # Cancellation may win after successful preparation but before the
            # caller receives ownership. That returned candidate is still ours.
            await retire_workspace_candidate(task.result().recovery.vm)
        raise


async def _prepare_workspace(
    workspace: Workspace, registry: ServiceRegistry, sources: tuple[Connection, ...]
) -> PreparedWorkspace:
    """Resolve every saved scope before exposing a single adoptable candidate.

    Sources must come from current exact-name resolution with each saved region
    applied. The app must revalidate those routes and its own input/generation
    fences at adoption. No inactive candidate activates drafts or commits stores.
    """
    active_connection = _source(sources, workspace.connection, workspace.region)
    try:
        active_service = registry.get(workspace.active_service)
        s3 = registry.get("s3") if workspace.file_panes is not None else None
    except ServiceNotFound:
        raise WorkspaceError("invalid") from None
    if active_connection is not None and not active_service.supports(active_connection):
        raise WorkspaceError("invalid")
    if s3 is not None and not isinstance(s3, S3Service):
        raise WorkspaceError("invalid")

    scope_services: dict[str, NavigationService] = {}
    scope_connections: list[Connection] = []
    for target in workspace.scopes:
        service = _navigation_service(registry, target.service)
        connection = _source(sources, target.connection, target.region)
        if connection is None or not service.supports(connection):
            raise WorkspaceError("invalid")
        scope_services[target.service] = service
        scope_connections.append(connection)
    # All current source references are validated before any provider or
    # metadata read, including the otherwise inactive file browser's sources.
    for target in workspace.file_panes or ():
        connection = _source(sources, target.connection, target.region)
        if connection is not None and (s3 is None or not s3.supports(connection)):
            raise WorkspaceError("invalid")

    owned: list[object] = []
    active: RecoveryServiceVM | None = None
    validated_panes: tuple[NavigationTarget, NavigationTarget] | None = None
    try:
        for target, connection in zip(workspace.scopes, scope_connections, strict=True):
            stage = scope_services[target.service].build_navigation_vm(connection, target)
            owned.append(stage.vm)
            stage.vm.construct()  # type: ignore[attr-defined]
            await resolve_service_target(stage.vm, target)
            if (target.service, target.connection, target.region) == (
                workspace.active_service,
                workspace.connection,
                workspace.region,
            ):
                active = stage
            else:
                await retire_workspace_candidate(stage.vm)
                owned.remove(stage.vm)

        if isinstance(s3, S3Service) and workspace.file_panes is not None:
            stage = s3.build_workspace_vm(workspace.file_panes, sources)
            owned.append(stage.vm)
            stage.vm.construct()  # type: ignore[attr-defined]
            await stage.vm.setup()  # type: ignore[attr-defined]
            validated_panes = (
                capture_pane_target(stage.vm.left),  # type: ignore[attr-defined]
                capture_pane_target(stage.vm.right),  # type: ignore[attr-defined]
            )
            if workspace.active_service == "s3":
                active = stage
            else:
                await retire_workspace_candidate(stage.vm)
                owned.remove(stage.vm)
        if active is None:
            raise WorkspaceError("invalid")
    except BaseException:
        with contextlib.suppress(Exception, asyncio.CancelledError):
            await _retire_candidates(tuple(reversed(owned)))
        raise

    committed = False

    def commit() -> None:
        nonlocal committed
        if committed:
            return
        # The active candidate commits its resolved scope and binds its normal
        # future selections. Inactive candidates never activate authoring state.
        active.commit_selection()
        for service_id, service in scope_services.items():
            service.install_navigation_targets(
                tuple(
                    target
                    for target in workspace.scopes
                    if target.service == service_id
                    and (target.service, target.connection, target.region)
                    != (workspace.active_service, workspace.connection, workspace.region)
                )
            )
        if (
            isinstance(s3, S3Service)
            and validated_panes is not None
            and workspace.active_service != "s3"
        ):
            s3.install_navigation_panes(validated_panes)
        committed = True

    return PreparedWorkspace(
        workspace.active_service, active_connection, RecoveryServiceVM(active.vm, commit)
    )
