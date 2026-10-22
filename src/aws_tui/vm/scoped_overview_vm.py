"""Explicit watches and owned manual refreshes; no polling or operation controls."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from typing import Any

import reactivex as rx

from aws_tui.domain.filesystem import (
    AuthRequiredError,
    PermissionDeniedError,
    ProviderUnreachableError,
    ThrottledError,
)
from aws_tui.domain.scoped_overview import (
    MAX_CONCURRENT_REFRESHES,
    MAX_WATCHES,
    OVERVIEW_PAGE_SIZE,
    OverviewEntry,
    OverviewPage,
    WatchScope,
)
from aws_tui.infra.connection_resolver import Connection
from aws_tui.vm._observable import ObserverSafeSubject


@dataclass(frozen=True, slots=True)
class OverviewSection:
    scope: WatchScope
    page: OverviewPage = field(default_factory=lambda: OverviewPage(()))
    state: str = "idle"
    last_success: datetime | None = None
    error_text: str | None = None
    stale: bool = False


class ScopedOverviewVM:
    def __init__(
        self,
        *,
        connection: Connection,
        read: Callable[[WatchScope], Awaitable[OverviewPage]],
        source_is_current: Callable[[], Awaitable[bool]],
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        slots: asyncio.Semaphore | None = None,
    ) -> None:
        self.connection = connection
        self._read = read
        self._source_is_current = source_is_current
        self._clock = clock
        self._sections: dict[WatchScope, OverviewSection] = {}
        self._tokens: dict[WatchScope, object] = {}
        self._tasks: dict[WatchScope, asyncio.Task[None]] = {}
        self._task_tokens: dict[WatchScope, object] = {}
        self._owners: dict[asyncio.Task[None], WatchScope] = {}
        self._cancel_sent: set[asyncio.Task[None]] = set()
        self._slots = slots if slots is not None else asyncio.Semaphore(MAX_CONCURRENT_REFRESHES)
        self._closed = False
        self._disposed = False
        self._changed: ObserverSafeSubject[str] = ObserverSafeSubject()

    @property
    def sections(self) -> tuple[OverviewSection, ...]:
        return tuple(self._sections.values())

    @property
    def busy(self) -> bool:
        return any(not task.done() for task in self._owners)

    @property
    def closed(self) -> bool:
        return self._closed

    async def source_is_current(self) -> bool:
        return not self._closed and await self._source_is_current() and not self._closed

    def entry_owner(self, entry: OverviewEntry) -> object | None:
        section = self._sections.get(entry.scope)
        if (
            self._closed
            or section is None
            or section.state != "ready"
            or section.stale
            or not any(row is entry for row in section.page.entries)
        ):
            return None
        return self._tokens.get(entry.scope)

    @property
    def on_property_changed(self) -> rx.Observable[str]:
        return self._changed

    def _notify(self) -> None:
        if not self._closed and not self._disposed:
            self._changed.on_next("sections")

    def add(self, scope: WatchScope) -> bool:
        if self._closed:
            raise ValueError("Overview is closed")
        if (scope.connection_name, scope.region) != (self.connection.name, self.connection.region):
            raise ValueError("Watched source does not match this overview")
        if scope in self._sections:
            return False
        if len(self._sections) >= MAX_WATCHES:
            raise ValueError("The overview supports at most eight watched scopes")
        self._sections[scope] = OverviewSection(scope)
        self._tokens[scope] = object()
        self._notify()
        return True

    def remove(self, scope: WatchScope) -> None:
        self._sections.pop(scope, None)
        self._tokens.pop(scope, None)
        task = self._tasks.pop(scope, None)
        self._task_tokens.pop(scope, None)
        if task is not None:
            self._request_cancel(task)
        self._notify()

    def _owns(self, scope: WatchScope, token: object) -> bool:
        return not self._closed and self._tokens.get(scope) is token

    def _set(self, scope: WatchScope, token: object, **changes: Any) -> None:
        if self._owns(scope, token):
            self._sections[scope] = replace(self._sections[scope], **changes)
            self._notify()

    async def refresh(self, scope: WatchScope | None = None) -> None:
        if self._closed:
            return
        watches = tuple(self._sections) if scope is None else (scope,)
        selected: list[asyncio.Task[None]] = []
        created: dict[WatchScope, asyncio.Task[None]] = {}
        for watch in watches:
            if watch not in self._sections:
                continue
            task = self._tasks.get(watch)
            if (
                task is None
                or task.done()
                or self._task_tokens.get(watch) is not self._tokens[watch]
            ):
                token = self._tokens[watch]
                task = asyncio.create_task(self._refresh_one(watch, token))
                self._tasks[watch] = task
                self._task_tokens[watch] = token
                self._owners[task] = watch
                created[watch] = task

                def completed(done: asyncio.Task[None], watched: WatchScope = watch) -> None:
                    if self._tasks.get(watched) is done:
                        self._tasks.pop(watched, None)
                        self._task_tokens.pop(watched, None)
                    self._owners.pop(done, None)
                    self._cancel_sent.discard(done)
                    if not done.cancelled():
                        done.exception()
                    self._notify()

                task.add_done_callback(completed)
            selected.append(task)
        try:
            for task in selected:
                try:
                    await asyncio.shield(task)
                except asyncio.CancelledError:
                    owner = asyncio.current_task()
                    if owner is None or owner.cancelling():
                        raise
                    # Removing a watch cancels only that child's read. A
                    # caller cancellation owns the whole admitted batch.
                    continue
        except asyncio.CancelledError:
            for watch, task in created.items():
                if self._tasks.get(watch) is task:
                    self._cancel(watch, task)
            await self._drain(tuple(created.values()))
            raise

    async def _refresh_one(self, scope: WatchScope, token: object) -> None:
        previous = self._sections[scope]
        self._set(
            scope, token, state="loading", stale=previous.last_success is not None, error_text=None
        )
        try:
            async with self._slots:
                if not self._owns(scope, token):
                    return
                if not await self._source_is_current():
                    self._source_changed(scope, token)
                    return
                if not self._owns(scope, token):
                    return
                page = await self._read(scope)
                if not self._owns(scope, token):
                    return
                if not await self._source_is_current():
                    self._source_changed(scope, token)
                    return
                if any(entry.scope != scope for entry in page.entries) or (
                    scope.kind != "transfers" and len(page.entries) > OVERVIEW_PAGE_SIZE
                ):
                    raise ValueError("Invalid overview projection")
                self._set(
                    scope,
                    token,
                    page=page,
                    state="ready",
                    last_success=self._clock(),
                    error_text=None,
                    stale=False,
                )
        except asyncio.CancelledError:
            self._set(
                scope,
                token,
                state="canceled",
                error_text="Refresh canceled",
                stale=previous.last_success is not None,
            )
            raise
        except Exception as error:
            state, text = "error", "Overview refresh failed"
            for error_type, failure_state, message in (
                (AuthRequiredError, "auth", "Source authentication required"),
                (PermissionDeniedError, "denied", "Access denied for this watched scope"),
                (ProviderUnreachableError, "unreachable", "Watched service is unreachable"),
                (ThrottledError, "throttled", "Watched service is throttled; retry manually"),
            ):
                if isinstance(error, error_type):
                    state, text = failure_state, message
                    break
            self._set(
                scope, token, state=state, error_text=text, stale=previous.last_success is not None
            )

    def _source_changed(self, scope: WatchScope, token: object) -> None:
        self._set(
            scope,
            token,
            state="changed",
            error_text="Watched source changed; reopen the overview",
            stale=self._sections[scope].last_success is not None,
        )

    def _cancel(self, scope: WatchScope, task: asyncio.Task[None]) -> None:
        # A new token latches supersession even if a provider consumes cancellation.
        if self._tasks.get(scope) is task and scope in self._tokens:
            self._tokens[scope] = object()
            section = self._sections[scope]
            self._sections[scope] = replace(
                section,
                state="canceled",
                error_text="Refresh canceled",
                stale=section.last_success is not None,
            )
        self._request_cancel(task)
        self._notify()

    def _request_cancel(self, task: asyncio.Task[None]) -> None:
        # Task.uncancel() can clear Python's counter while provider cleanup
        # remains in progress. Remember our irreversible cancellation request
        # until the actual task terminates, including removal and shutdown.
        if task.done() or task in self._cancel_sent:
            return
        self._cancel_sent.add(task)
        if not task.cancelling():
            task.cancel()

    def cancel_refresh(self) -> None:
        for task, scope in tuple(self._owners.items()):
            self._cancel(scope, task)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self.cancel_refresh()
        self._sections = {
            scope: replace(
                section,
                state="changed",
                stale=section.last_success is not None,
                error_text="Overview closed; reopen for the current source",
            )
            for scope, section in self._sections.items()
        }
        self._changed.on_next("sections")

    @staticmethod
    async def _drain(tasks: tuple[asyncio.Task[None], ...]) -> None:
        for task in tasks:
            while not task.done():
                try:
                    await asyncio.shield(task)
                except (Exception, asyncio.CancelledError):
                    continue
            if not task.cancelled():
                task.exception()

    async def shutdown(self) -> None:
        self.close()
        await self._drain(tuple(self._owners))
        if not self._disposed:
            self._disposed = True
            self._changed.on_completed()
            self._changed.dispose()
