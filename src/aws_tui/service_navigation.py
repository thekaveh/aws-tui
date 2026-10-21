"""Service-navigation ownership and adoption, independent of Textual.

Inputs are a service ID, an optional exact required-connection identity, the
current RootVM, and explicit per-attempt operations in ServiceMountPorts.
View host capture/publication and actual UI worker operations stay in those
ports; this module never reads application-private attributes.

ServiceNavigation owns navigation generation/current owner, serialization
locks, task-scoped selection suppression, navigation/rollback task lifetime
and the remembered S3 local-fallback identity. Adoption drains transfer
workers, checks ownership and required identity, applies remembered retry
policy, switches the VM, then publishes the view. Failed replacement retains
the existing switch-versus-render recovery distinction. ContentHostVM still
owns candidate construction, setup, shutdown and disposal.

Shutdown is deliberately two-phase: close_intake is synchronous; after the
caller stops its UI workers and passes the service-lock barrier, drain cancels
navigation tasks and waits for rollback tasks through caller cancellation.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Awaitable, Callable, Iterator
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol, TypedDict, Unpack

from aws_tui.infra.connection_resolver import Connection

if TYPE_CHECKING:
    from aws_tui.infra.aws_session import TokenState
    from aws_tui.vm.root_vm import RootVM

NavigationOwner = tuple[str, int] | None
ViewPublisher = Callable[[str, object], Awaitable[None]]


class NavigationLogger(Protocol):
    def error(self, event: str, **fields: object) -> None: ...


class ConnectionAdoptionOptions(TypedDict, total=False):
    prepare_vm: Callable[[object], None] | None
    ownership_is_current: Callable[[], bool] | None
    claim_input: Callable[[], bool] | None


class PreparedAdoptionOptions(TypedDict, total=False):
    ownership_is_current: Callable[[], bool] | None
    claim_input: Callable[[], bool] | None


@dataclass(frozen=True)
class ServiceMountPorts:
    """Live model and narrowly scoped operations for one adoption attempt.

    prepare_mount captures the current UI host when called, returning a
    publisher for that host. Constructing this bundle must not capture a host
    or snapshot the model's active connection before transfer draining.
    """

    root: RootVM
    log: NavigationLogger
    cancel_transfers: Callable[[], Awaitable[None]]
    prepare_mount: Callable[[], ViewPublisher]
    retry_connection: Callable[[Connection], Awaitable[str]]
    mount_local_fallback: Callable[[Connection], Awaitable[bool]]
    begin_retry: Callable[[Connection], None]
    report_retry_failure: Callable[[Connection, str], None]
    report_local_fallback: Callable[[], None]
    restore_navigation: Callable[[str], None]
    refresh_hints: Callable[[], None]


class ServiceNavigation:
    """The single owner of navigation state, with no app/context constructor."""

    def __init__(self) -> None:
        self._generation = 0
        self._owner: NavigationOwner = None
        self._closed = False
        self._service_lock = asyncio.Lock()
        self._table_lock = asyncio.Lock()
        self._navigations: set[asyncio.Task[None]] = set()
        self._rollbacks: set[asyncio.Task[bool]] = set()
        self._suppression: tuple[asyncio.Task[Any] | None, str] | None = None
        self._local_fallback = False
        self._fallback_connection: Connection | None = None

    @property
    def generation(self) -> int:
        return self._generation

    def set_generation(self, generation: int) -> None:
        """Preserve the existing diagnostic generation-injection seam."""
        self._generation = generation

    @property
    def owner(self) -> NavigationOwner:
        return self._owner

    @property
    def closed(self) -> bool:
        return self._closed

    @property
    def service_lock(self) -> asyncio.Lock:
        return self._service_lock

    @property
    def table_lock(self) -> asyncio.Lock:
        return self._table_lock

    @property
    def navigation_tasks(self) -> frozenset[asyncio.Task[None]]:
        return frozenset(self._navigations)

    @property
    def rollback_tasks(self) -> frozenset[asyncio.Task[bool]]:
        return frozenset(self._rollbacks)

    def advance(self, owner: str, *, cancel_table_tasks: bool = False) -> int:
        self._generation += 1
        self._owner = (owner, self._generation)
        if cancel_table_tasks:
            current = asyncio.current_task()
            for task in tuple(self._navigations):
                if task is not current and not task.done():
                    task.cancel()
        return self._generation

    def supersede(self) -> int:
        return self.advance("external", cancel_table_tasks=True)

    def owns(self, owner: str, generation: int) -> bool:
        return self._owner == (owner, generation)

    def claim_table(self, generation: int) -> bool:
        if generation != self._generation:
            return False
        self._owner = ("table", generation)
        return True

    def may_restore_handoff(self, generation: int) -> bool:
        return generation <= self._generation and (self._owner is None or self._owner[0] == "table")

    def track_navigation(self, task: asyncio.Task[None]) -> None:
        self._navigations.add(task)

    def untrack_navigation(self, task: asyncio.Task[None]) -> None:
        self._navigations.discard(task)

    def track_rollback(self, task: asyncio.Task[bool]) -> None:
        self._rollbacks.add(task)

    def untrack_rollback(self, task: asyncio.Task[bool]) -> None:
        self._rollbacks.discard(task)

    @contextlib.contextmanager
    def suppress_selection(self, selected: str | None) -> Iterator[None]:
        current = asyncio.current_task()
        token = (current, selected) if current is not None and selected is not None else None
        self._suppression = token
        try:
            yield
        finally:
            if self._suppression is token:
                self._suppression = None

    def consume_selection(self, selected: str) -> bool:
        token = self._suppression
        if token is not None and token[0] is asyncio.current_task() and token[1] == selected:
            self._suppression = None
            return True
        return False

    @property
    def local_fallback(self) -> bool:
        return self._local_fallback

    @property
    def fallback_connection(self) -> Connection | None:
        return self._fallback_connection

    def remember_local_fallback(self, connection: Connection) -> None:
        self._local_fallback = True
        self._fallback_connection = connection

    def clear_local_fallback(self) -> None:
        self._local_fallback = False
        self._fallback_connection = None

    def restore_fallback_state(self, local_only: bool, connection: Connection | None) -> None:
        """Restore the existing diagnostic fallback-state injection seam."""
        self._local_fallback = local_only
        self._fallback_connection = connection

    def mark_local_recovered(self) -> None:
        """Clear the local-only decision while retaining its historical memo."""
        self._local_fallback = False

    async def mount_service(
        self,
        service_id: str,
        *,
        ports: ServiceMountPorts,
        required_connection: Connection | None = None,
    ) -> bool:
        owner = self._owner

        def owns_mount() -> bool:
            return not self._closed and self._owner == owner

        await ports.cancel_transfers()
        if not owns_mount():
            return False
        if required_connection is not None and ports.root.active_connection != required_connection:
            active = ports.root.active_connection
            ports.log.error(
                "app.mount_service_view.connection_mismatch",
                service_id=service_id,
                required_connection=required_connection.name,
                active_connection=active.name if active is not None else None,
            )
            return False
        if (
            service_id == "s3"
            and required_connection is None
            and self._local_fallback
            and self._fallback_connection is not None
        ):
            retry_connection = self._fallback_connection
            self._local_fallback = False
            ports.begin_retry(retry_connection)
            outcome = await ports.retry_connection(retry_connection)
            if outcome == "ok":
                self._fallback_connection = None
                return True
            ports.report_retry_failure(retry_connection, outcome)
            ports.report_local_fallback()
            self.remember_local_fallback(retry_connection)
            return await ports.mount_local_fallback(retry_connection)
        try:
            publish_view = ports.prepare_mount()
            await self.switch_service(ports.root, service_id)
            if not owns_mount():
                return False
        except Exception as exc:
            ports.log.error(
                "app.mount_service_view.switch_service_failed",
                service_id=service_id,
                error=str(exc),
                error_type=type(exc).__name__,
            )
            ports.restore_navigation(service_id)
            return False
        try:
            current_vm = ports.root.content_host.current
            if current_vm is None:
                ports.log.error("app.mount_service_view.missing_view_model", service_id=service_id)
                return False
            await publish_view(service_id, current_vm)
            if not owns_mount():
                return False
            if service_id in {"glue", "athena", "emr-serverless"}:
                ports.refresh_hints()
        except Exception as exc:
            ports.log.error(
                "app.mount_service_view.mount_failed",
                service_id=service_id,
                error=str(exc),
                error_type=type(exc).__name__,
                notes=getattr(exc, "__notes__", ()),
            )
            return False
        return True

    async def switch_service(self, root: RootVM, service_id: str) -> None:
        await root.switch_service(service_id)

    async def switch_connection(
        self, root: RootVM, connection: Connection, auth_state: TokenState
    ) -> None:
        await root.switch_connection_with(connection, auth_state)

    async def switch_connection_and_service(
        self,
        root: RootVM,
        connection: Connection,
        auth_state: TokenState,
        service_id: str,
        **options: Unpack[ConnectionAdoptionOptions],
    ) -> bool:
        """Retain caller ownership/locks and the exact optional-keyword shape."""
        return await root.switch_connection_and_service(
            connection, auth_state, service_id, **options
        )

    async def adopt_prepared_service(
        self,
        root: RootVM,
        connection: Connection,
        auth_state: TokenState,
        service_id: str,
        vm: object,
        **options: Unpack[PreparedAdoptionOptions],
    ) -> bool:
        return await root.adopt_prepared_service_vm(
            connection, auth_state, service_id, vm, **options
        )

    async def adopt_content(
        self, root: RootVM, vm: object | None, *, service_id: str | None
    ) -> bool:
        return await root.content_host.set_content(vm, service_id=service_id)

    def restore_navigation(
        self,
        root: RootVM,
        requested_id: str,
        *,
        notify_failure: Callable[[str, str | None], None],
    ) -> None:
        current_id = root.content_host.current_id
        menu = root.services_menu
        if menu.selected_id != current_id:
            with self.suppress_selection(current_id):
                if current_id is None:
                    menu.clear_selection()
                else:
                    menu.switch_service_command.execute(current_id)
        root.chrome.hint_legend.set_current_service(current_id)
        with contextlib.suppress(Exception):
            notify_failure(requested_id, current_id)

    def close_intake(self) -> None:
        self._closed = True

    async def drain(self) -> None:
        while self._navigations or self._rollbacks:
            navigations = tuple(self._navigations)
            for task in navigations:
                if not task.done():
                    task.cancel()
            if navigations:
                await self._await_tasks(navigations)
                self._navigations.difference_update(navigations)
            rollbacks = tuple(self._rollbacks)
            if rollbacks:
                await self._await_tasks(rollbacks)
                self._rollbacks.difference_update(rollbacks)

    @staticmethod
    async def _await_tasks(tasks: tuple[asyncio.Task[Any], ...]) -> None:
        for task in tasks:
            while not task.done():
                try:
                    await asyncio.shield(task)
                except asyncio.CancelledError:
                    continue
            with contextlib.suppress(Exception, asyncio.CancelledError):
                task.result()
