"""Reusable bounded execution for read-only Athena queries."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, NoReturn, Protocol, TypeVar

import anyio

from aws_tui.domain.athena import ResultConfigurationRequiredError
from aws_tui.domain.filesystem import (
    AuthRequiredError,
    NotFoundError,
    PermissionDeniedError,
    ProviderError,
    ProviderUnreachableError,
    ThrottledError,
    ValidationError,
)
from aws_tui.domain.query import (
    QueryContext,
    QueryExecutionDetail,
    QueryExecutionRef,
    QueryState,
    ResultColumn,
    ResultPage,
)
from aws_tui.domain.sql_policy import QueryRejectedError, ReadOnlySqlPolicy

Sleep = Callable[[float], Awaitable[None]]
T = TypeVar("T")
_TERMINAL_STATES = frozenset(
    {
        QueryState.SUCCEEDED,
        QueryState.FAILED,
        QueryState.CANCELLED,
    }
)


class AthenaQueryFailedError(ProviderError):
    """A bounded Athena query reached the failed terminal state."""


class AthenaQueryCancelledError(ProviderError):
    """A bounded Athena query reached the cancelled terminal state."""


class AthenaResultShapeError(ValidationError):
    """Athena returned inconsistent bounded-result metadata."""


class _PreparedRunError(Exception):
    """Carry only an app-owned public error type and a sanitized message."""

    def __init__(self, error_type: type[ProviderError], message: str) -> None:
        super().__init__(message)
        self.error_type = error_type

    def public_error(self) -> ProviderError:
        return self.error_type(str(self))


class AthenaRunnerClient(Protocol):
    async def start_query(
        self,
        sql: str,
        context: QueryContext,
        *,
        request_token: str,
    ) -> QueryExecutionRef: ...

    async def get_query_execution(
        self,
        execution_id: str,
    ) -> QueryExecutionDetail: ...

    async def stop_query(self, execution_id: str) -> None: ...

    async def get_results_page(
        self,
        execution_id: str,
        *,
        start_token: str | None = None,
    ) -> Any: ...


@dataclass(frozen=True, slots=True)
class BoundedQueryResult:
    detail: QueryExecutionDetail
    columns: tuple[ResultColumn, ...]
    rows: tuple[tuple[str | None, ...], ...] = field(repr=False)


@dataclass(eq=False, repr=False, slots=True)
class _CleanupOperation:
    """Own the submission-recovery and stop phases for one accepted query."""

    submission_task: asyncio.Task[QueryExecutionRef] | None = None
    ref: QueryExecutionRef | None = None
    waiter_task: asyncio.Task[None] | None = None
    stop_task: asyncio.Task[None] | None = None
    stop_request_task: asyncio.Task[None] | None = None
    stop_invoked: bool = False
    complete: bool = False


class AthenaQueryRunner:
    """Validate, execute, poll, and page a read-only Athena query."""

    def __init__(
        self,
        client: AthenaRunnerClient,
        policy: ReadOnlySqlPolicy,
        *,
        sleep: Sleep = anyio.sleep,
    ) -> None:
        self._client = client
        self._policy = policy
        self._sleep = sleep
        self._cleanup_operations: set[_CleanupOperation] = set()

    @property
    def client(self) -> AthenaRunnerClient:
        return self._client

    def validate(self, sql: str, context: QueryContext) -> str:
        if not all(value.strip() for value in context.cache_key):
            raise ValidationError("query context is incomplete")
        return self._policy.validate(sql)

    async def start(
        self,
        sql: str,
        context: QueryContext,
        *,
        request_token: str,
    ) -> QueryExecutionRef:
        normalized_sql = self.validate(sql, context)
        ref = await self._client.start_query(
            normalized_sql,
            context,
            request_token=request_token,
        )
        return ref

    async def detail(
        self,
        ref: QueryExecutionRef,
        context: QueryContext,
    ) -> QueryExecutionDetail:
        return await self._client.get_query_execution(ref.execution_id)

    async def stop(self, ref: QueryExecutionRef) -> None:
        await self._client.stop_query(ref.execution_id)

    async def pause(self, delay: float) -> None:
        await self._sleep(delay)

    async def run(
        self,
        sql: str,
        context: QueryContext,
        *,
        request_token: str,
        max_rows: int,
    ) -> BoundedQueryResult:
        if max_rows <= 0:
            sql = ""
            raise ValidationError("max_rows must be positive")
        operation_task = asyncio.create_task(
            self._run_request(
                sql,
                context,
                request_token=request_token,
                max_rows=max_rows,
            )
        )
        sql = ""
        request_token = ""
        try:
            return await operation_task
        except asyncio.CancelledError:
            del operation_task
            raise
        except _PreparedRunError as exc:
            prepared_error = exc.public_error()
            del exc
            del operation_task
        except Exception:
            del operation_task
            prepared_error = ProviderError("Athena query request failed")
        _raise_prepared_run_error(prepared_error)

    async def _run_request(
        self,
        sql: str,
        context: QueryContext,
        *,
        request_token: str,
        max_rows: int,
    ) -> BoundedQueryResult:
        try:
            normalized_sql = self.validate(sql, context)
        except QueryRejectedError as exc:
            prepared_error = _PreparedRunError(QueryRejectedError, str(exc))
            del exc
            sql = ""
            request_token = ""
            raise prepared_error from None
        except ValidationError as exc:
            prepared_error = _PreparedRunError(ValidationError, str(exc))
            del exc
            sql = ""
            request_token = ""
            raise prepared_error from None
        operation = self._run_validated(
            normalized_sql, context, request_token=request_token, max_rows=max_rows
        )
        sql = ""
        normalized_sql = ""
        request_token = ""
        return await operation

    async def _run_validated(
        self,
        normalized_sql: str,
        context: QueryContext,
        *,
        request_token: str,
        max_rows: int,
    ) -> BoundedQueryResult:
        submission_task = asyncio.create_task(
            self._client.start_query(
                normalized_sql,
                context,
                request_token=request_token,
            )
        )
        normalized_sql = ""
        request_token = ""
        try:
            ref = await asyncio.shield(submission_task)
        except asyncio.CancelledError:
            cleanup = self._retain_cleanup(submission_task=submission_task)
            del submission_task
            await self._drain_cleanup(cleanup)
            raise
        except Exception as exc:
            prepared_error = _prepared_provider_error(exc, phase="start")
            del exc
            del submission_task
            raise prepared_error from None
        del submission_task

        if not _ref_matches_context(ref, context):
            cleanup = self._retain_cleanup(ref=ref)
            cancelled = await self._drain_cleanup(cleanup)
            if cancelled:
                raise asyncio.CancelledError
            raise _PreparedRunError(
                ValidationError,
                "Athena query does not match the active context",
            )

        try:
            detail = await self._poll(ref, context)
        except asyncio.CancelledError:
            cleanup = self._retain_cleanup(ref=ref)
            await self._drain_cleanup(cleanup)
            raise
        except _PreparedRunError:
            cleanup = self._retain_cleanup(ref=ref)
            cancelled = await self._drain_cleanup(cleanup)
            if cancelled:
                raise asyncio.CancelledError from None
            raise
        except Exception as exc:
            prepared_error = _prepared_provider_error(exc, phase="poll")
            del exc
            cleanup = self._retain_cleanup(ref=ref)
            cancelled = await self._drain_cleanup(cleanup)
            if cancelled:
                raise asyncio.CancelledError from None
            raise prepared_error from None

        state = detail.summary.state
        if state is QueryState.FAILED:
            del detail
            raise _PreparedRunError(AthenaQueryFailedError, "Athena query failed")
        if state is QueryState.CANCELLED:
            del detail
            raise _PreparedRunError(
                AthenaQueryCancelledError,
                "Athena query was cancelled",
            )
        try:
            columns, rows = await self._bounded_results(ref, max_rows=max_rows)
        except asyncio.CancelledError:
            del detail
            raise
        except _PreparedRunError:
            del detail
            raise
        except Exception as exc:
            del detail
            prepared_error = _prepared_provider_error(exc, phase="results")
            del exc
            raise prepared_error from None
        return BoundedQueryResult(detail, columns, rows)

    async def _poll(
        self,
        ref: QueryExecutionRef,
        context: QueryContext,
    ) -> QueryExecutionDetail:
        delay = 0.25
        while True:
            detail = await self.detail(ref, context)
            if not _detail_matches_context(detail, ref, context):
                raise _PreparedRunError(
                    ValidationError,
                    "Athena query does not match the active context",
                )
            if detail.summary.state in _TERMINAL_STATES:
                return detail
            await self.pause(delay)
            delay = min(delay * 2, 5.0)

    async def _bounded_results(
        self,
        ref: QueryExecutionRef,
        *,
        max_rows: int,
    ) -> tuple[tuple[ResultColumn, ...], tuple[tuple[str | None, ...], ...]]:
        columns: tuple[ResultColumn, ...] | None = None
        rows: list[tuple[str | None, ...]] = []
        token: str | None = None
        seen_tokens: set[str] = set()
        request_count = 0
        while len(rows) < max_rows:
            if request_count >= max_rows:
                raise _PreparedRunError(
                    AthenaResultShapeError,
                    "Athena result pagination exceeded its bound",
                )
            raw_page = await self._client.get_results_page(
                ref.execution_id,
                start_token=token,
            )
            request_count += 1
            page = _validated_result_page(raw_page)
            raw_page = None
            page_columns = page.columns
            if columns is None:
                columns = page_columns
            elif page_columns != columns:
                raise _PreparedRunError(
                    AthenaResultShapeError,
                    "Athena result columns changed between pages",
                )
            remaining = max_rows - len(rows)
            page_rows = page.rows
            rows.extend(page_rows[:remaining])
            next_token = page.next_token
            if len(rows) >= max_rows or next_token is None:
                break
            if not page_rows:
                raise _PreparedRunError(
                    AthenaResultShapeError,
                    "Athena result pagination did not advance",
                )
            if next_token in seen_tokens:
                raise _PreparedRunError(
                    AthenaResultShapeError,
                    "Athena result pagination token repeated",
                )
            seen_tokens.add(next_token)
            token = next_token
        return columns or (), tuple(rows)

    def _retain_cleanup(
        self,
        *,
        submission_task: asyncio.Task[QueryExecutionRef] | None = None,
        ref: QueryExecutionRef | None = None,
    ) -> _CleanupOperation:
        if (submission_task is None) == (ref is None):
            raise ValueError("cleanup requires exactly one query owner")
        cleanup = _CleanupOperation(submission_task=submission_task, ref=ref)
        cleanup.waiter_task = asyncio.create_task(self._finalize_cleanup(cleanup))
        self._cleanup_operations.add(cleanup)
        return cleanup

    async def _drain_cleanup(self, cleanup: _CleanupOperation) -> bool:
        """Drain one owner and report cancellation received while waiting."""

        current_task = asyncio.current_task()
        cancellation_count = current_task.cancelling() if current_task is not None else 0
        cancelled = False
        while not cleanup.complete:
            waiter = cleanup.waiter_task
            if waiter is None or waiter.done():
                if waiter is not None and not waiter.cancelled():
                    try:
                        waiter.result()
                    except Exception:
                        cleanup.complete = True
                        break
                if cleanup.complete:
                    break
                waiter = asyncio.create_task(self._finalize_cleanup(cleanup))
                cleanup.waiter_task = waiter
            try:
                await asyncio.shield(waiter)
            except asyncio.CancelledError:
                current_count = current_task.cancelling() if current_task is not None else 0
                if current_count > cancellation_count:
                    cancelled = True
                    cancellation_count = current_count
                continue
            except Exception:
                cleanup.complete = True
        self._cleanup_operations.discard(cleanup)
        cleanup.submission_task = None
        cleanup.ref = None
        cleanup.waiter_task = None
        cleanup.stop_task = None
        cleanup.stop_request_task = None
        cleanup.stop_invoked = False
        return cancelled

    async def _finalize_cleanup(self, cleanup: _CleanupOperation) -> None:
        if cleanup.ref is None:
            submission_task = cleanup.submission_task
            if submission_task is None:
                cleanup.complete = True
                return
            try:
                cleanup.ref = await _await_task_through_cancellation(submission_task)
            except asyncio.CancelledError:
                cleanup.complete = True
                return
            except Exception:
                cleanup.complete = True
                return
            finally:
                cleanup.submission_task = None

        while not cleanup.complete:
            stop_task = cleanup.stop_task
            if stop_task is None:
                stop_task = asyncio.create_task(self._best_effort_stop(cleanup))
                cleanup.stop_task = stop_task
            try:
                await _await_task_through_cancellation(stop_task)
            except asyncio.CancelledError:
                if cleanup.stop_request_task is None:
                    cleanup.stop_task = None
                    continue
            cleanup.complete = True

    async def _best_effort_stop(self, cleanup: _CleanupOperation) -> None:
        ref = cleanup.ref
        if ref is None:
            return
        while True:
            request_task = cleanup.stop_request_task
            if request_task is None:
                request_task = asyncio.create_task(self._invoke_stop(cleanup, ref))
                cleanup.stop_request_task = request_task
            try:
                await _await_task_through_cancellation(request_task)
            except asyncio.CancelledError:
                if not cleanup.stop_invoked:
                    cleanup.stop_request_task = None
                    continue
                return
            except Exception:
                return
            return

    async def _invoke_stop(
        self,
        cleanup: _CleanupOperation,
        ref: QueryExecutionRef,
    ) -> None:
        # The bit is set in the same task step that begins the provider call.
        cleanup.stop_invoked = True
        await self.stop(ref)


def _validated_result_page(value: object) -> ResultPage:
    if type(value) is not ResultPage:
        raise _PreparedRunError(
            AthenaResultShapeError,
            "Athena returned an invalid result page",
        )
    page = value
    if type(page.columns) is not tuple or not all(
        type(column) is ResultColumn
        and type(column.name) is str
        and type(column.type_name) is str
        and type(column.nullable) is str
        for column in page.columns
    ):
        raise _PreparedRunError(
            AthenaResultShapeError,
            "Athena returned invalid result columns",
        )
    if type(page.rows) is not tuple:
        raise _PreparedRunError(
            AthenaResultShapeError,
            "Athena returned invalid result rows",
        )
    for row in page.rows:
        if type(row) is not tuple:
            raise _PreparedRunError(
                AthenaResultShapeError,
                "Athena returned an invalid result row",
            )
        if len(row) != len(page.columns):
            raise _PreparedRunError(
                AthenaResultShapeError,
                "Athena result row width does not match columns",
            )
        if not all(item is None or type(item) is str for item in row):
            raise _PreparedRunError(
                AthenaResultShapeError,
                "Athena returned an invalid result value",
            )
    if page.next_token is not None and (type(page.next_token) is not str or not page.next_token):
        raise _PreparedRunError(
            AthenaResultShapeError,
            "Athena returned an invalid pagination token",
        )
    return page


async def _await_task_through_cancellation(task: asyncio.Task[T]) -> T:
    """Wait for an owned task despite repeated cancellation of this waiter."""

    while True:
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            if task.done():
                return await task


def _prepared_provider_error(exc: BaseException, *, phase: str) -> _PreparedRunError:
    error_type: type[ProviderError]
    if isinstance(exc, ResultConfigurationRequiredError):
        error_type = ResultConfigurationRequiredError
    elif isinstance(exc, AuthRequiredError):
        error_type = AuthRequiredError
    elif isinstance(exc, ProviderUnreachableError):
        error_type = ProviderUnreachableError
    elif isinstance(exc, PermissionDeniedError):
        error_type = PermissionDeniedError
    elif isinstance(exc, NotFoundError):
        error_type = NotFoundError
    elif isinstance(exc, ThrottledError):
        error_type = ThrottledError
    elif isinstance(exc, AthenaResultShapeError):
        error_type = AthenaResultShapeError
    elif isinstance(exc, ValidationError):
        error_type = ValidationError
    else:
        error_type = ProviderError
    return _PreparedRunError(error_type, _provider_phase_message(error_type, phase=phase))


def _provider_phase_message(error_type: type[ProviderError], *, phase: str) -> str:
    operation = {
        "start": "query start",
        "poll": "query status request",
        "results": "results request",
    }[phase]
    if issubclass(error_type, AuthRequiredError):
        return f"Athena authentication failed during {operation}"
    if issubclass(error_type, ProviderUnreachableError):
        return f"Athena is unreachable during {operation}"
    if issubclass(error_type, PermissionDeniedError):
        return f"Athena {operation} is forbidden"
    if issubclass(error_type, NotFoundError):
        return f"Athena resource was not found during {operation}"
    if issubclass(error_type, ThrottledError):
        return f"Athena {operation} was throttled"
    if issubclass(error_type, ResultConfigurationRequiredError):
        return f"Athena result configuration is required during {operation}"
    if issubclass(error_type, AthenaResultShapeError):
        return f"Athena returned invalid data during {operation}"
    if issubclass(error_type, ValidationError):
        return f"Athena rejected the {operation}"
    return f"Athena {operation} failed"


def _raise_prepared_run_error(error: BaseException) -> NoReturn:
    error.__traceback__ = None
    error.__context__ = None
    error.__cause__ = None
    try:
        raise error from None
    except BaseException as raised:
        raised.__context__ = None
        raised.__cause__ = None
        raise


def _ref_matches_context(ref: QueryExecutionRef, context: QueryContext) -> bool:
    return (
        ref.connection_name == context.connection_name
        and ref.region == context.region
        and ref.workgroup == context.workgroup
    )


def _detail_matches_context(
    detail: QueryExecutionDetail,
    ref: QueryExecutionRef,
    context: QueryContext,
) -> bool:
    return (
        detail.summary.ref == ref
        and detail.context == context
        and _ref_matches_context(detail.summary.ref, context)
    )


__all__ = [
    "AthenaQueryCancelledError",
    "AthenaQueryFailedError",
    "AthenaQueryRunner",
    "AthenaResultShapeError",
    "BoundedQueryResult",
]
