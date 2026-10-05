"""Application-owned draft revisions, persistence and bounded observation."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from concurrent.futures import Future
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

import reactivex as rx
from vmx import Message, MessageHub, PropertyChangedMessage
from vmx.services.dispatcher import Dispatcher

from aws_tui.domain.query import QueryContext
from aws_tui.infra.athena_draft_store import (
    AthenaDraftStore,
    DraftPermit,
    DraftStoreResult,
    SqlDraft,
)
from aws_tui.infra.athena_draft_store import (
    draft_id as context_draft_id,
)
from aws_tui.infra.draft_worker import DraftWorker
from aws_tui.vm._observable import ObserverSafeSubject, send_value_free

DEBOUNCE_SECONDS = 0.500
SHUTDOWN_SECONDS = 2.000
DraftState = Literal["off", "empty", "pending", "saved", "error", "context_required"]
_SAVE_ERROR = "Athena SQL draft could not be saved. Edit again to retry."
_CONTEXT_ERROR = "Editor belongs to another context. Return to that context or clear the editor."
_INCOMPLETE_CONTEXT = "Select a complete Athena context and edit to save."
_OPERATION_ERROR = "Athena SQL draft operation failed. Retry the operation."


@dataclass(frozen=True, slots=True)
class DraftFlushReport:
    unpersisted: int
    timed_out: bool


@dataclass(frozen=True, slots=True)
class _EditCapture:
    record: SqlDraft = field(repr=False)
    captured_editor_revision: int
    captured_write_revision: int
    permit: DraftPermit = field(repr=False)


class _WriteCoordinator:
    def __init__(self, store: AthenaDraftStore, worker: DraftWorker) -> None:
        self.store = store
        self.worker = worker
        self.loop = asyncio.get_running_loop()
        self.sequence = 0
        self.latest_revision: dict[str, int] = {}
        self.current: dict[str, _EditCapture] = {}
        self.timers: dict[str, asyncio.TimerHandle] = {}
        self.launches: dict[str, Callable[[], None]] = {}
        self.futures: set[Future[DraftStoreResult]] = set()
        self.waiters: dict[Future[DraftStoreResult], asyncio.Future[DraftStoreResult]] = {}
        self.intake = True
        self.active = True
        self.terminal: DraftFlushReport | None = None
        self.shutdown_lock = asyncio.Lock()
        self.mutation_lock = asyncio.Lock()

    def is_current(self, draft_id: str, captured_write_revision: int, permit: DraftPermit) -> bool:
        capture = self.current.get(draft_id)
        return (
            capture is not None
            and capture.captured_write_revision == captured_write_revision
            and capture.permit is permit
            and not permit.cancelled
        )

    def observe(self, future: Future[DraftStoreResult]) -> asyncio.Future[DraftStoreResult]:
        existing = self.waiters.get(future)
        if existing is not None:
            return existing
        waiter: asyncio.Future[DraftStoreResult] = self.loop.create_future()
        self.futures.add(future)
        self.waiters[future] = waiter

        def deliver(result: DraftStoreResult) -> None:
            if not waiter.done():
                waiter.set_result(result)
            self.futures.discard(future)
            self.waiters.pop(future, None)

        def completed(done: Future[DraftStoreResult]) -> None:
            result = done.result()
            if self.loop.is_closed() or not self.active:
                self.futures.discard(done)
                self.waiters.pop(done, None)
                return
            try:
                self.loop.call_soon_threadsafe(deliver, result)
            except RuntimeError:
                self.futures.discard(done)
                self.waiters.pop(done, None)

        future.add_done_callback(completed)
        return waiter

    def schedule(
        self,
        *,
        sql: str,
        context: QueryContext,
        captured_editor_revision: int,
        completed: Callable[[_EditCapture, DraftStoreResult], None],
    ) -> _EditCapture | None:
        if not self.intake or not all(context.cache_key):
            return None
        identity = context_draft_id(context.cache_key)
        previous = self.current.get(identity)
        if previous is not None:
            previous.permit.cancel()
        old_timer = self.timers.pop(identity, None)
        if old_timer is not None:
            old_timer.cancel()
        self.sequence += 1
        now = datetime.now(UTC)
        capture = _EditCapture(
            SqlDraft(identity, context.cache_key, sql, now, now),
            captured_editor_revision,
            self.sequence,
            DraftPermit(),
        )
        self.current[identity] = capture
        self.latest_revision[identity] = self.sequence

        def launch() -> None:
            self.timers.pop(identity, None)
            self.launches.pop(identity, None)
            if not self.is_current(identity, capture.captured_write_revision, capture.permit):
                return

            def operation() -> DraftStoreResult:
                if sql.strip():
                    return self.store.save(capture.record, permit=capture.permit)
                if capture.permit.cancelled:
                    return DraftStoreResult(code="cancelled")
                return self.store.delete(identity)

            waiter = self.observe(self.worker.submit(operation))

            def apply(done: asyncio.Future[DraftStoreResult]) -> None:
                if done.cancelled() or self.terminal is not None or not self.active:
                    return
                completed(capture, done.result())

            waiter.add_done_callback(apply)

        self.launches[identity] = launch
        self.timers[identity] = self.loop.call_later(DEBOUNCE_SECONDS, launch)
        return capture

    def launch(self, capture: _EditCapture) -> None:
        if not self.is_current(capture.record.id, capture.captured_write_revision, capture.permit):
            return
        timer = self.timers.pop(capture.record.id, None)
        if timer is not None:
            timer.cancel()
        launch = self.launches.get(capture.record.id)
        if launch is not None:
            launch()

    def fence(self, ids: set[str] | None) -> None:
        for identity in tuple(self.current):
            if ids is not None and identity not in ids:
                continue
            self.current[identity].permit.cancel()
            timer = self.timers.pop(identity, None)
            if timer is not None:
                timer.cancel()
            self.launches.pop(identity, None)
            self.current.pop(identity, None)

    async def mutate(
        self,
        operation: Callable[[], DraftStoreResult],
        *,
        ids: set[str] | None,
        tombstone: Callable[[], None],
        completed: Callable[[DraftStoreResult], None],
        accepted: Callable[[], None],
    ) -> DraftStoreResult:
        async with self.mutation_lock:
            if self.terminal is not None or not self.intake:
                return DraftStoreResult(code="cancelled")
            self.fence(ids)
            tombstone()
            accepted()
            waiter = self.observe(self.worker.submit(operation))
            waiter.add_done_callback(
                lambda done: completed(done.result()) if not done.cancelled() else None
            )
            return await asyncio.shield(waiter)

    async def finish(self, *, deadline: float, unconfirmed: Callable[[], int]) -> DraftFlushReport:
        async with self.shutdown_lock:
            if self.terminal is not None:
                return self.terminal
            self.intake = False
            for timer in self.timers.values():
                timer.cancel()
            self.timers.clear()
            for launch in tuple(self.launches.values()):
                launch()
            self.launches.clear()
            self.worker.close_intake()
            waiters = tuple(self.waiters.values())
            cancelled = False
            if waiters:
                try:
                    _, pending = await asyncio.wait(
                        waiters, timeout=max(0.0, deadline - self.loop.time())
                    )
                except asyncio.CancelledError:
                    cancelled = True
                    pending = {waiter for waiter in waiters if not waiter.done()}
            else:
                pending = set()
            try:
                await asyncio.sleep(0)
            except asyncio.CancelledError:
                cancelled = True
            count = unconfirmed()
            if pending:
                self.fence(None)
                for waiter in pending:
                    waiter.cancel()
            self.terminal = DraftFlushReport(count, bool(pending))
            if cancelled:
                raise asyncio.CancelledError
            return self.terminal


class AthenaDraftsVM:
    def __init__(
        self,
        *,
        store: AthenaDraftStore,
        enabled: bool,
        read_only: bool,
        directory: Path,
        hub: MessageHub[Message],
        dispatcher: Dispatcher,
    ) -> None:
        self._sessions: set[AthenaDraftSession] = set()
        self._coordinator: _WriteCoordinator | None = None
        self._worker = DraftWorker()
        self._store = store
        self._enabled = enabled and not read_only
        self._read_only = read_only
        self._directory = directory
        self._hub = hub
        self._dispatcher = dispatcher
        self._disposed = False
        self._saving_suspended = False
        self._items: tuple[SqlDraft, ...] = ()
        self._error_text: str | None = None
        self._busy = False
        self._cleanup_required = False
        self._terminal: DraftFlushReport | None = None
        self._operation_lock: asyncio.Lock | None = None
        self._preference_generation = 0
        self._pending_mutations = 0
        self._on_property_changed = ObserverSafeSubject[str]()

    @property
    def enabled(self) -> bool:
        return self._enabled

    @property
    def read_only(self) -> bool:
        return self._read_only

    @property
    def directory(self) -> Path:
        return self._directory

    @property
    def items(self) -> tuple[SqlDraft, ...]:
        return self._items

    @property
    def busy(self) -> bool:
        return self._busy

    @property
    def cleanup_required(self) -> bool:
        return self._cleanup_required

    @property
    def dispatcher(self) -> Dispatcher:
        return self._dispatcher

    @property
    def error_text(self) -> str | None:
        return self._error_text

    @property
    def on_property_changed(self) -> rx.Observable[str]:
        return self._on_property_changed

    def _ensure_coordinator(self) -> _WriteCoordinator:
        if self._coordinator is None:
            self._coordinator = _WriteCoordinator(self._store, self._worker)
        return self._coordinator

    def open_session(self, *, active: bool = True) -> AthenaDraftSession:
        session = AthenaDraftSession(self, active=active)
        if self._disposed or self._terminal is not None:
            session._detached = True
        else:
            self._sessions.add(session)
        return session

    def schedule_session(
        self,
        session: AthenaDraftSession,
        sql: str,
        context: QueryContext,
        captured_editor_revision: int,
    ) -> None:
        if self._disposed or self._saving_suspended or not self.enabled:
            return
        coordinator = self._ensure_coordinator()

        def completed(capture: _EditCapture, result: DraftStoreResult) -> None:
            current = (
                not self._disposed
                and not session._detached
                and self.enabled
                and coordinator.is_current(
                    capture.record.id, capture.captured_write_revision, capture.permit
                )
                and session._editor_revision == capture.captured_editor_revision
                and not capture.permit.cancelled
            )
            if not current:
                return
            if result.code is None:
                session._save_failed_revision = None
                session._saved_sql = capture.record.sql
                session._saved_context = QueryContext(*capture.record.context)
                session._state = "saved" if capture.record.sql.strip() else "empty"
            elif result.code not in {"cancelled", "disabled"}:
                session._save_failed_revision = capture.captured_editor_revision
                session._state = "error"
            if session._context_needs_guard():
                session._state = "context_required"
            session._notify("state")
            session._notify("error_text")

        session._capture = coordinator.schedule(
            sql=sql,
            context=context,
            captured_editor_revision=captured_editor_revision,
            completed=completed,
        )

    def _revoke_session_capture(self, session: AthenaDraftSession) -> None:
        coordinator, capture = self._coordinator, session._capture
        if (
            coordinator is not None
            and capture is not None
            and coordinator.is_current(
                capture.record.id, capture.captured_write_revision, capture.permit
            )
        ):
            coordinator.fence({capture.record.id})

    def _prepare_final(self, session: AthenaDraftSession) -> None:
        coordinator, capture = self._coordinator, session._capture
        if (
            coordinator is None
            or capture is None
            or not coordinator.intake
            or self._disposed
            or self._saving_suspended
            or session._deleted_revision == session._editor_revision
            or capture.captured_editor_revision != session._editor_revision
            or not coordinator.is_current(
                capture.record.id, capture.captured_write_revision, capture.permit
            )
        ):
            return
        if session._save_failed_revision == session._editor_revision:
            self.schedule_session(
                session,
                capture.record.sql,
                QueryContext(*capture.record.context),
                capture.captured_editor_revision,
            )
            capture = session._capture
        if capture is not None:
            coordinator.launch(capture)

    def _session_is_unconfirmed(self, session: AthenaDraftSession) -> bool:
        if (
            not self.enabled
            or not session._active
            or session._detached
            or not session._sql.strip()
            or session._deleted_revision == session._editor_revision
            or session._context is None
            or not session.has_unsaved_text(session._sql, session._context)
        ):
            return False
        capture = session._capture
        if capture is not None and self._coordinator is not None:
            latest = self._coordinator.latest_revision.get(capture.record.id)
            if latest is not None and latest != capture.captured_write_revision:
                return False
        return True

    async def refresh(self) -> None:
        if not self.enabled or self._disposed or self._terminal is not None:
            return
        coordinator = self._ensure_coordinator()
        result = await asyncio.shield(coordinator.observe(self._worker.submit(self._store.list)))
        if not self._disposed and self.enabled and coordinator.intake:
            self._apply_result(result)

    def _tombstone(self, identity: str | None) -> None:
        for session in self._sessions:
            session.deleted(identity)

    def _begin_mutation(self) -> None:
        self._pending_mutations += 1
        self._busy = True
        self._notify("busy")

    def _operation_completed(self, result: DraftStoreResult) -> None:
        self._pending_mutations -= 1
        self._busy = self._pending_mutations > 0
        coordinator = self._coordinator
        if self._disposed or coordinator is None or not coordinator.intake:
            return
        self._apply_result(result)
        self._notify("busy")

    async def _mutation(
        self, operation: Callable[[], DraftStoreResult], identity: str | None
    ) -> bool:
        if not self.enabled or self._disposed or self._terminal is not None:
            return False
        coordinator = self._ensure_coordinator()
        result = await coordinator.mutate(
            operation,
            ids={identity} if identity is not None else None,
            tombstone=lambda: self._tombstone(identity),
            completed=self._operation_completed,
            accepted=self._begin_mutation,
        )
        return result.code is None and not self._disposed and coordinator.intake

    async def delete(self, draft_id: str) -> bool:
        return await self._mutation(lambda: self._store.delete(draft_id), draft_id)

    async def clear(self) -> bool:
        return await self._mutation(self._store.clear, None)

    def _preference_completed(
        self, enabled: bool, generation: int, result: DraftStoreResult
    ) -> None:
        coordinator = self._coordinator
        if self._disposed or coordinator is None or not coordinator.intake:
            return
        if result.enabled is not None:
            self._enabled = result.enabled
        self._operation_completed(result)
        if generation != self._preference_generation:
            self._notify("enabled")
            return
        self._cleanup_required = not enabled and result.code is not None
        if enabled and result.code is None and self.enabled:
            self._saving_suspended = False
            for session in self._sessions:
                if session._active and session._context is not None and session._sql.strip():
                    session.edited(session._sql, session._context)
        elif not enabled:
            self._saving_suspended = True
            for session in self._sessions:
                session._state = "off" if not self.enabled else "empty"
                session._notify("state")
        self._notify("enabled")
        self._notify("cleanup_required")

    async def set_enabled(self, enabled: bool) -> bool:
        if self._read_only or self._disposed or self._terminal is not None:
            return False
        if self._operation_lock is None:
            self._operation_lock = asyncio.Lock()
        self._preference_generation += 1
        generation = self._preference_generation
        if not enabled:
            self._saving_suspended = True
        async with self._operation_lock:
            coordinator = self._ensure_coordinator()
            if not coordinator.intake:
                return False
            if not enabled:
                self._saving_suspended = True

            def completed(result: DraftStoreResult) -> None:
                self._preference_completed(enabled, generation, result)

            if enabled:
                async with coordinator.mutation_lock:
                    if not coordinator.intake:
                        return False
                    self._begin_mutation()
                    waiter = coordinator.observe(
                        self._worker.submit(lambda: self._store.set_enabled(True))
                    )
                    waiter.add_done_callback(
                        lambda done: completed(done.result()) if not done.cancelled() else None
                    )
                    result = await asyncio.shield(waiter)
            else:
                result = await coordinator.mutate(
                    lambda: self._store.set_enabled(False),
                    ids=None,
                    tombstone=lambda: self._tombstone(None),
                    completed=completed,
                    accepted=self._begin_mutation,
                )
            return result.code is None and not self._disposed and coordinator.intake

    def _apply_result(self, result: DraftStoreResult) -> None:
        if result.code is None:
            self._items = result.records
        self._error_text = None if result.code is None else _OPERATION_ERROR
        self._notify("items")
        self._notify("error_text")

    async def shutdown(self) -> DraftFlushReport:
        if self._terminal is not None:
            return self._terminal
        coordinator = self._coordinator
        if coordinator is not None and coordinator.terminal is not None:
            self._terminal = coordinator.terminal
            return self._terminal
        if not self.enabled and coordinator is None:
            self._worker.close_intake()
            self._terminal = DraftFlushReport(0, False)
            for session in tuple(self._sessions):
                session.detach()
            return self._terminal
        deadline = asyncio.get_running_loop().time() + SHUTDOWN_SECONDS
        coordinator = self._ensure_coordinator()
        for session in tuple(self._sessions):
            self._prepare_final(session)
        try:
            self._terminal = await coordinator.finish(
                deadline=deadline,
                unconfirmed=lambda: sum(self._session_is_unconfirmed(s) for s in self._sessions),
            )
        finally:
            if coordinator.terminal is not None:
                self._terminal = coordinator.terminal
                for session in tuple(self._sessions):
                    session.detach()
        return self._terminal

    def dispose(self) -> None:
        if self._disposed:
            return
        self._disposed = True
        if self._coordinator is not None:
            self._coordinator.active = False
            self._coordinator.intake = False
            self._coordinator.fence(None)
            for waiter in tuple(self._coordinator.waiters.values()):
                waiter.cancel()
        for session in tuple(self._sessions):
            session.detach()
        self._worker.close_intake()
        self._on_property_changed.on_completed()
        self._on_property_changed.dispose()

    def _notify(self, property_name: str) -> None:
        if self._disposed:
            return
        send_value_free(
            self._hub, PropertyChangedMessage.create(self, "athena.drafts", property_name)
        )
        self._on_property_changed.on_next(property_name)


class AthenaDraftSession:
    def __init__(self, runtime: AthenaDraftsVM, *, active: bool = True) -> None:
        self._runtime = runtime
        self._editor_revision = 0
        self._sql = ""
        self._context: QueryContext | None = None
        self._bound_context: QueryContext | None = None
        self._saved_sql: str | None = None
        self._saved_context: QueryContext | None = None
        self._deleted_revision: int | None = None
        self._capture: _EditCapture | None = None
        self._save_failed_revision: int | None = None
        self._detached = False
        self._active = active
        self._state: DraftState = "off"
        self._on_property_changed = ObserverSafeSubject[str]()

    @property
    def state(self) -> DraftState:
        return self._state

    @property
    def error_text(self) -> str | None:
        if self._state == "error":
            return _SAVE_ERROR
        if self._state == "context_required":
            if self._bound_context is not None:
                return _CONTEXT_ERROR
            return _INCOMPLETE_CONTEXT
        return None

    @property
    def editor_revision(self) -> int:
        return self._editor_revision

    @property
    def bound_context(self) -> QueryContext | None:
        return self._bound_context

    @property
    def on_property_changed(self) -> rx.Observable[str]:
        return self._on_property_changed

    def activate(self, sql: str, context: QueryContext) -> None:
        if self._detached:
            return
        self._sql = sql
        self._context = context
        self._bound_context = context if sql.strip() and all(context.cache_key) else None
        self._active = True

    def edited(self, sql: str, context: QueryContext) -> None:
        if self._detached:
            return
        self._editor_revision += 1
        self._save_failed_revision = None
        self._sql = sql
        self._context = context
        self._deleted_revision = None
        if not sql.strip():
            self._bound_context = None
        elif self._bound_context is None and all(context.cache_key):
            self._bound_context = context
        if not self._active or not self._runtime.enabled:
            self._runtime._revoke_session_capture(self)
            self._state = "off"
        elif not all(context.cache_key) or (
            self._bound_context is not None and self._bound_context != context
        ):
            self._runtime._revoke_session_capture(self)
            self._state = "context_required"
        else:
            self._state = "pending"
            self._runtime.schedule_session(self, sql, context, self._editor_revision)
        self._notify("state")
        self._notify("error_text")

    def _context_needs_guard(self) -> bool:
        return bool(self._sql.strip()) and (
            self._context is None
            or not all(self._context.cache_key)
            or (self._bound_context is not None and self._bound_context != self._context)
        )

    def context_changed(self, context: QueryContext) -> None:
        if self._detached:
            return
        self._context = context
        if self._runtime.enabled and self._context_needs_guard():
            self._state = "context_required"
        elif self._state == "context_required" and self._bound_context == context:
            if self._save_failed_revision == self._editor_revision:
                self._state = "error"
            else:
                self._state = "pending" if self.has_unsaved_text(self._sql, context) else "saved"
        self._notify("state")
        self._notify("error_text")

    def recovered(self, record: SqlDraft) -> None:
        if self._detached:
            return
        coordinator, capture = self._runtime._coordinator, self._capture
        if (
            coordinator is not None
            and capture is not None
            and coordinator.is_current(
                capture.record.id, capture.captured_write_revision, capture.permit
            )
        ):
            coordinator.fence({capture.record.id})
        self._capture = None
        self._editor_revision += 1
        self._save_failed_revision = None
        self._sql = record.sql
        self._context = QueryContext(*record.context)
        self._bound_context = self._context
        self._saved_sql = record.sql
        self._saved_context = self._context
        self._deleted_revision = None
        self._state = "saved"
        self._notify("state")
        self._notify("error_text")

    def deleted(self, draft_id: str | None) -> None:
        if self._detached:
            return
        if draft_id is not None and (
            self._bound_context is None
            or draft_id != context_draft_id(self._bound_context.cache_key)
        ):
            return
        self._deleted_revision = self._editor_revision
        self._saved_sql = None
        self._saved_context = None
        self._state = "empty"
        self._notify("state")
        self._notify("error_text")

    def has_unsaved_text(self, sql: str, context: QueryContext) -> bool:
        return bool(sql.strip()) and (sql != self._saved_sql or context != self._saved_context)

    async def flush(self, *, deadline: float) -> DraftFlushReport:
        runtime = self._runtime
        if runtime._terminal is not None:
            return runtime._terminal
        coordinator = runtime._coordinator
        if coordinator is not None and coordinator.terminal is not None:
            return coordinator.terminal
        if not runtime.enabled or self._detached or runtime._disposed:
            return DraftFlushReport(0, False)
        runtime._prepare_final(self)
        coordinator = runtime._ensure_coordinator()
        waiters = tuple(coordinator.waiters.values())
        if waiters:
            _, pending = await asyncio.wait(
                waiters, timeout=max(0.0, deadline - coordinator.loop.time())
            )
        else:
            pending = set()
        await asyncio.sleep(0)
        return DraftFlushReport(int(runtime._session_is_unconfirmed(self)), bool(pending))

    def detach(self) -> None:
        if self._detached:
            return
        self._runtime._prepare_final(self)
        self._detached = True
        self._runtime._sessions.discard(self)
        self._on_property_changed.on_completed()
        self._on_property_changed.dispose()

    def _notify(self, property_name: str) -> None:
        if self._detached or self._runtime._disposed:
            return
        send_value_free(
            self._runtime._hub,
            PropertyChangedMessage.create(self, "athena.draft_session", property_name),
        )
        self._on_property_changed.on_next(property_name)


__all__ = ["AthenaDraftSession", "AthenaDraftsVM", "DraftFlushReport", "DraftState"]
