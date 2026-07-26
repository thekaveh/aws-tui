from __future__ import annotations

import asyncio
import hashlib
from collections.abc import Awaitable, Callable
from contextlib import suppress
from typing import Any
from uuid import uuid4

import anyio
import reactivex as rx
from reactivex.subject import Subject
from vmx import (
    AsyncRelayCommand,
    ComponentVMOf,
    Message,
    MessageHub,
    PropertyChangedMessage,
)
from vmx.lifecycle.status import ConstructionStatus
from vmx.services.dispatcher import Dispatcher

from aws_tui.domain.filesystem import ProviderError
from aws_tui.domain.query import (
    AthenaQueryError,
    QueryContext,
    QueryExecutionDetail,
    QueryExecutionRef,
    QueryState,
    QueryStatistics,
)
from aws_tui.domain.sql_policy import QueryRejectedError, ReadOnlySqlPolicy
from aws_tui.vm.athena._errors import map_provider_error, map_unexpected_error
from aws_tui.vm.athena.results_vm import AthenaResultsVM
from aws_tui.vm.file_manager.pane_vm import PaneState

_QUERY_ERROR = "Athena query request failed"
_CONTEXT_ERROR = "Athena returned a query outside the active context"
_TERMINAL_QUERY_STATES = frozenset(
    {
        QueryState.SUCCEEDED,
        QueryState.FAILED,
        QueryState.CANCELLED,
    }
)
_EMPTY_STATISTICS = QueryStatistics(None, None, None, None, None, False)

Sleep = Callable[[float], Awaitable[None]]


class _QueryContextMismatchError(Exception):
    pass


class AthenaQueryVM:
    def __init__(
        self,
        *,
        client: Any,
        policy: ReadOnlySqlPolicy,
        context: QueryContext,
        hub: MessageHub[Message],
        dispatcher: Dispatcher,
        sleep: Sleep = anyio.sleep,
    ) -> None:
        self._client = client
        self._policy = policy
        self._context = context
        self._hub = hub
        self._disposed = False
        self._shutdown_started = False
        self._shutdown_complete = False
        self._lifecycle_transition = False
        self._lifecycle_lock = asyncio.Lock()
        self._on_property_changed: Subject[str] = Subject()
        self._inner: ComponentVMOf[None] = (
            ComponentVMOf[None]
            .builder()
            .name("athena.query")
            .model(None)
            .services(hub, dispatcher)
            .build()
        )
        self._results = AthenaResultsVM(
            client=client,
            hub=hub,
            dispatcher=dispatcher,
        )
        self._sleep = sleep
        self._generation = 0
        self._execution_task: asyncio.Task[None] | None = None
        self._submission_task: asyncio.Task[QueryExecutionRef] | None = None
        self._submission_finalizers: set[asyncio.Task[None]] = set()
        self._pending_cleanup_refs: dict[str, QueryExecutionRef] = {}
        self._owns_active_query = False
        self._busy = False
        self._is_submitting = False
        self._sql = ""
        self._validation_error: str | None = None
        self._execution_ref: QueryExecutionRef | None = None
        self._state: QueryState | None = None
        self._statistics = _EMPTY_STATISTICS
        self._query_error: AthenaQueryError | None = None
        self._state_reason: str | None = None
        self._output_location: str | None = None
        self._engine_version: str | None = None
        self._pane_state = PaneState.EMPTY
        self._error_text: str | None = None
        self._execute_command: AsyncRelayCommand = (
            AsyncRelayCommand.builder()
            .predicate(self._can_execute)
            .triggers(self._on_property_changed)
            .task(self._run_execution)
            .build()
        )
        self._cancel_command: AsyncRelayCommand = (
            AsyncRelayCommand.builder()
            .predicate(self._can_cancel)
            .triggers(self._on_property_changed)
            .task(self._cancel_active)
            .build()
        )

    @property
    def context(self) -> QueryContext:
        return self._context

    @property
    def sql(self) -> str:
        return self._sql

    @property
    def validation_error(self) -> str | None:
        return self._validation_error

    @property
    def execution_ref(self) -> QueryExecutionRef | None:
        return self._execution_ref

    @property
    def state(self) -> QueryState | None:
        return self._state

    @property
    def statistics(self) -> QueryStatistics:
        return self._statistics

    @property
    def query_error(self) -> AthenaQueryError | None:
        return self._query_error

    @property
    def state_reason(self) -> str | None:
        return self._state_reason

    @property
    def output_location(self) -> str | None:
        return self._output_location

    @property
    def engine_version(self) -> str | None:
        return self._engine_version

    @property
    def pane_state(self) -> PaneState:
        return self._pane_state

    @property
    def error_text(self) -> str | None:
        return self._error_text

    @property
    def is_submitting(self) -> bool:
        return self._is_submitting

    @property
    def is_executing(self) -> bool:
        return self._busy

    @property
    def owns_active_query(self) -> bool:
        return self._owns_active_query

    @property
    def results(self) -> AthenaResultsVM:
        return self._results

    @property
    def execute_command(self) -> AsyncRelayCommand:
        return self._execute_command

    @property
    def cancel_command(self) -> AsyncRelayCommand:
        return self._cancel_command

    @property
    def status(self) -> ConstructionStatus:
        return self._inner.status

    @property
    def on_property_changed(self) -> rx.Observable[str]:
        return self._on_property_changed

    def construct(self) -> None:
        self._inner.construct()
        self._results.construct()

    def set_sql(self, sql: str) -> None:
        if self._disposed or sql == self._sql:
            return
        self._sql = sql
        self._validation_error = None
        self._notify("sql")
        self._notify("validation_error")

    async def set_context(self, context: QueryContext) -> None:
        if self._disposed or context == self._context:
            return
        async with self._lifecycle_lock:
            if self._disposed or context == self._context:
                return
            self._lifecycle_transition = True
            self._generation += 1
            if self._owns_active_query and self._execution_ref is not None:
                self._retain_cleanup(self._execution_ref)
            self._owns_active_query = False
            self._context = context
            self._reset_execution_state()
            self._notify("context")
            self._execute_command.cancel()
            await self._drain_execution_task()
            await self._stop_pending_cleanup(report_error=False)
            self._lifecycle_transition = False

    async def execute(self) -> None:
        if (
            not self._execute_command.can_execute()
            and not self._disposed
            and not self._shutdown_started
            and not self._busy
            and bool(self._sql.strip())
        ):
            try:
                self._validate()
            except QueryRejectedError as exc:
                self._validation_error = str(exc)
                self._notify("validation_error")
            return
        await self._execute_command.execute_async()

    async def cancel(self) -> None:
        if self._cancel_command.can_execute():
            await self._cancel_command.execute_async()
        elif self._can_interrupt():
            await self._cancel_active()

    async def shutdown(self) -> None:
        async with self._lifecycle_lock:
            if self._shutdown_complete:
                return
            self._shutdown_started = True
            self._lifecycle_transition = True
            self._generation += 1
            self._results.clear()
            ref = self._execution_ref
            if self._owns_active_query and ref is not None:
                self._retain_cleanup(ref)
            self._owns_active_query = False
            self._notify("owns_active_query")
            self._execute_command.cancel()
            await self._drain_execution_task()
            await self._drain_submission_finalizers()
            await self._stop_pending_cleanup(report_error=True)
            await self._results.shutdown()
            self._is_submitting = False
            self._shutdown_complete = True
            self._notify("is_executing")
            self._notify("is_submitting")

    def dispose(self) -> None:
        if self._disposed:
            return
        self._disposed = True
        self._generation += 1
        self._cancel_command.dispose()
        self._execute_command.dispose()
        self._results.dispose()
        self._on_property_changed.on_completed()
        self._on_property_changed.dispose()
        self._inner.dispose()

    async def _run_execution(self) -> None:
        task = asyncio.current_task()
        if task is None:
            return
        self._execution_task = task
        self._set_busy(True)
        generation = self._generation + 1
        self._generation = generation
        try:
            try:
                normalized_sql = self._validate()
            except QueryRejectedError as exc:
                if generation == self._generation:
                    self._validation_error = str(exc)
                    self._notify("validation_error")
                return
            self._reset_execution_state()
            self._validation_error = None
            self._is_submitting = True
            self._pane_state = PaneState.LOADING
            self._notify("validation_error")
            self._notify("is_submitting")
            self._notify("pane_state")
            submission_context = self._context
            request_token = _request_token(submission_context)
            submission_task = asyncio.create_task(
                self._client.start_query(
                    normalized_sql,
                    submission_context,
                    request_token=request_token,
                )
            )
            self._submission_task = submission_task
            try:
                ref = await asyncio.shield(submission_task)
            except asyncio.CancelledError:
                finalizer = asyncio.create_task(
                    self._finalize_cancelled_submission(submission_task)
                )
                self._submission_finalizers.add(finalizer)
                finalizer.add_done_callback(self._submission_finalizers.discard)
                await asyncio.shield(finalizer)
                raise
            except ProviderError as exc:
                if generation == self._generation:
                    self._apply_provider_error(exc)
                return
            except Exception:
                if generation == self._generation:
                    self._apply_unexpected_error()
                return
            finally:
                if generation == self._generation:
                    self._is_submitting = False
                    self._notify("is_submitting")
                if self._submission_task is submission_task:
                    self._submission_task = None
            if generation != self._generation or self._disposed:
                await self._stop_stale_submission(ref)
                return
            if not self._ref_matches_context(ref, self._context):
                await self._reject_out_of_context(ref, generation)
                return
            self._execution_ref = ref
            self._owns_active_query = True
            self._state = QueryState.QUEUED
            self._pane_state = PaneState.IDLE
            self._notify_execution()
            try:
                await self._poll(ref, generation)
            except asyncio.CancelledError:
                raise
            except _QueryContextMismatchError:
                if generation == self._generation:
                    await self._reject_out_of_context(ref, generation)
            except ProviderError as exc:
                if generation == self._generation:
                    self._apply_provider_error(exc)
            except Exception:
                if generation == self._generation:
                    self._apply_unexpected_error()
        finally:
            if self._execution_task is task:
                self._execution_task = None
            self._set_busy(False)

    def _validate(self) -> str:
        if not all(value.strip() for value in self._context.cache_key):
            raise QueryRejectedError("query context is incomplete")
        return self._policy.validate(self._sql)

    async def _poll(self, ref: QueryExecutionRef, generation: int) -> None:
        delay = 0.25
        while generation == self._generation and not self._disposed:
            detail = await self._client.get_query_execution(ref.execution_id)
            if generation != self._generation or self._disposed:
                return
            if not self._detail_matches_context(detail, ref):
                raise _QueryContextMismatchError
            self._apply_detail(detail)
            if detail.summary.state in _TERMINAL_QUERY_STATES:
                self._owns_active_query = False
                self._notify("owns_active_query")
                if detail.summary.state is QueryState.SUCCEEDED:
                    await self._results.load(ref.execution_id)
                    if generation != self._generation or self._disposed:
                        return
                return
            await self._sleep(delay)
            if generation != self._generation or self._disposed:
                return
            delay = min(delay * 2, 5.0)

    async def _cancel_active(self) -> None:
        async with self._lifecycle_lock:
            if not self._can_interrupt():
                return
            ref = self._execution_ref
            if self._owns_active_query and ref is not None and not await self._try_stop(ref):
                return
            self._lifecycle_transition = True
            self._generation += 1
            self._owns_active_query = False
            self._state = QueryState.CANCELLED
            self._is_submitting = False
            self._results.clear()
            self._notify("owns_active_query")
            self._notify("state")
            self._notify("is_submitting")
            self._execute_command.cancel()
            await self._drain_execution_task()
            await self._stop_pending_cleanup(report_error=True)
            self._lifecycle_transition = False

    async def _try_stop(
        self,
        ref: QueryExecutionRef,
        *,
        report_error: bool = True,
    ) -> bool:
        try:
            await self._client.stop_query(ref.execution_id)
        except ProviderError as exc:
            if report_error:
                self._apply_provider_error(exc)
            return False
        except Exception:
            if report_error:
                self._apply_unexpected_error()
            return False
        return True

    async def _finalize_cancelled_submission(
        self,
        task: asyncio.Task[QueryExecutionRef],
    ) -> None:
        try:
            ref = await task
        except asyncio.CancelledError:
            return
        except Exception:
            return
        await self._stop_stale_submission(ref)

    async def _stop_stale_submission(self, ref: QueryExecutionRef) -> None:
        self._retain_cleanup(ref)
        if self._lifecycle_transition and not self._disposed:
            return
        await self._stop_retained_ref(ref, report_error=False)

    def _retain_cleanup(self, ref: QueryExecutionRef) -> None:
        self._pending_cleanup_refs[ref.execution_id] = ref

    async def _stop_retained_ref(
        self,
        ref: QueryExecutionRef,
        *,
        report_error: bool,
    ) -> bool:
        if not await self._try_stop(ref, report_error=report_error):
            return False
        self._pending_cleanup_refs.pop(ref.execution_id, None)
        return True

    async def _stop_pending_cleanup(self, *, report_error: bool) -> None:
        for ref in tuple(self._pending_cleanup_refs.values()):
            await self._stop_retained_ref(ref, report_error=report_error)

    async def _drain_execution_task(self) -> None:
        task = self._execution_task
        if task is None or task is asyncio.current_task():
            return
        with suppress(asyncio.CancelledError):
            await task

    async def _drain_submission_finalizers(self) -> None:
        while self._submission_finalizers:
            await asyncio.gather(
                *(asyncio.shield(task) for task in tuple(self._submission_finalizers)),
                return_exceptions=True,
            )

    def _apply_detail(self, detail: QueryExecutionDetail) -> None:
        self._state = detail.summary.state
        self._statistics = detail.statistics
        self._query_error = detail.error
        self._state_reason = detail.state_reason
        self._output_location = detail.output_location
        self._engine_version = detail.engine_version
        self._pane_state = PaneState.IDLE
        for property_name in (
            "state",
            "statistics",
            "query_error",
            "state_reason",
            "output_location",
            "engine_version",
            "pane_state",
        ):
            self._notify(property_name)

    async def _reject_out_of_context(
        self,
        ref: QueryExecutionRef,
        generation: int,
    ) -> None:
        self._retain_cleanup(ref)
        stopped = await self._stop_retained_ref(ref, report_error=False)
        if generation != self._generation or self._disposed:
            return
        self._apply_context_error(None if stopped else ref)

    def _apply_context_error(self, active_ref: QueryExecutionRef | None) -> None:
        self._owns_active_query = active_ref is not None
        self._execution_ref = active_ref
        self._state = None
        self._statistics = _EMPTY_STATISTICS
        self._query_error = None
        self._state_reason = None
        self._output_location = None
        self._engine_version = None
        self._is_submitting = False
        self._pane_state = PaneState.ERROR
        self._error_text = _CONTEXT_ERROR
        self._notify_execution()

    def _apply_provider_error(self, exc: ProviderError) -> None:
        self._pane_state, self._error_text = map_provider_error(
            exc,
            fallback=_QUERY_ERROR,
        )
        self._notify("pane_state")
        self._notify("error_text")

    def _apply_unexpected_error(self) -> None:
        self._pane_state, self._error_text = map_unexpected_error(
            fallback=_QUERY_ERROR,
        )
        self._notify("pane_state")
        self._notify("error_text")

    def _reset_execution_state(self) -> None:
        self._execution_ref = None
        self._owns_active_query = False
        self._state = None
        self._statistics = _EMPTY_STATISTICS
        self._query_error = None
        self._state_reason = None
        self._output_location = None
        self._engine_version = None
        self._is_submitting = False
        self._pane_state = PaneState.EMPTY
        self._error_text = None
        self._results.clear()
        self._notify_execution()

    def _notify_execution(self) -> None:
        for property_name in (
            "execution_ref",
            "owns_active_query",
            "state",
            "statistics",
            "query_error",
            "state_reason",
            "output_location",
            "engine_version",
            "is_submitting",
            "pane_state",
            "error_text",
        ):
            self._notify(property_name)

    def _set_busy(self, value: bool) -> None:
        if self._busy == value:
            return
        self._busy = value
        self._notify("is_executing")

    def _can_execute(self) -> bool:
        if self._disposed or self._shutdown_started or not self._sql.strip() or self._busy:
            return False
        try:
            self._validate()
        except QueryRejectedError:
            return False
        return True

    def _can_cancel(self) -> bool:
        return (
            not self._disposed
            and not self._shutdown_started
            and self._busy
            and self._owns_active_query
            and self._execution_ref is not None
        )

    def _can_interrupt(self) -> bool:
        return (
            not self._disposed
            and not self._shutdown_started
            and self._busy
            and (
                self._is_submitting or (self._owns_active_query and self._execution_ref is not None)
            )
        )

    @staticmethod
    def _ref_matches_context(
        ref: QueryExecutionRef,
        context: QueryContext,
    ) -> bool:
        return (
            ref.connection_name == context.connection_name
            and ref.region == context.region
            and ref.workgroup == context.workgroup
        )

    def _detail_matches_context(
        self,
        detail: QueryExecutionDetail,
        ref: QueryExecutionRef,
    ) -> bool:
        return detail.summary.ref == ref and detail.context.cache_key == self._context.cache_key

    def _notify(self, property_name: str) -> None:
        if self._disposed:
            return
        self._hub.send(
            PropertyChangedMessage.create(
                self,
                "athena.query",
                property_name,
            )
        )
        self._on_property_changed.on_next(property_name)


def _request_token(context: QueryContext) -> str:
    material = "\0".join((*context.cache_key, uuid4().hex)).encode()
    return hashlib.sha256(material).hexdigest()


__all__ = ["AthenaQueryVM"]
