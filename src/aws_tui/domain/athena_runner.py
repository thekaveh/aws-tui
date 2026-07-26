"""Reusable bounded execution for read-only Athena queries."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, NoReturn, Protocol

import anyio

from aws_tui.domain.filesystem import ProviderError, ValidationError
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
        except Exception as exc:
            prepared_error = _prepared_run_error(exc, phase="run")
            del exc
            del operation_task
        _raise_prepared_run_error(prepared_error)

    async def _run_request(
        self,
        sql: str,
        context: QueryContext,
        *,
        request_token: str,
        max_rows: int,
    ) -> BoundedQueryResult:
        normalized_sql = self.validate(sql, context)
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
            finalizer = asyncio.create_task(self._finalize_cancelled_submission(submission_task))
            del submission_task
            await asyncio.shield(finalizer)
            del finalizer
            raise
        except Exception as exc:
            prepared_error = _prepared_run_error(exc, phase="start")
            del exc
            del submission_task
            _raise_prepared_run_error(prepared_error)
        del submission_task

        if not _ref_matches_context(ref, context):
            await self._best_effort_stop(ref)
            raise ValidationError("Athena query does not match the active context")

        try:
            detail = await self._poll(ref, context)
        except asyncio.CancelledError:
            await asyncio.shield(self._best_effort_stop(ref))
            raise
        except Exception as exc:
            await self._best_effort_stop(ref)
            prepared_error = _prepared_run_error(exc, phase="poll")
            del exc
            _raise_prepared_run_error(prepared_error)

        state = detail.summary.state
        if state is QueryState.FAILED:
            del detail
            _raise_prepared_run_error(AthenaQueryFailedError("Athena query failed"))
        if state is QueryState.CANCELLED:
            del detail
            _raise_prepared_run_error(AthenaQueryCancelledError("Athena query was cancelled"))
        try:
            columns, rows = await self._bounded_results(ref, max_rows=max_rows)
        except asyncio.CancelledError:
            del detail
            raise
        except Exception as exc:
            del detail
            prepared_error = _prepared_run_error(exc, phase="results")
            del exc
            _raise_prepared_run_error(prepared_error)
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
                raise ValidationError("Athena query does not match the active context")
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
                raise AthenaResultShapeError("Athena result pagination exceeded its bound")
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
                raise AthenaResultShapeError("Athena result columns changed between pages")
            remaining = max_rows - len(rows)
            page_rows = page.rows
            rows.extend(page_rows[:remaining])
            next_token = page.next_token
            if len(rows) >= max_rows or next_token is None:
                break
            if not page_rows:
                raise AthenaResultShapeError("Athena result pagination did not advance")
            if next_token in seen_tokens:
                raise AthenaResultShapeError("Athena result pagination token repeated")
            seen_tokens.add(next_token)
            token = next_token
        return columns or (), tuple(rows)

    async def _finalize_cancelled_submission(
        self,
        task: asyncio.Task[QueryExecutionRef],
    ) -> None:
        try:
            ref = await task
        except (asyncio.CancelledError, Exception):
            return
        await asyncio.shield(self._best_effort_stop(ref))

    async def _best_effort_stop(self, ref: QueryExecutionRef) -> None:
        try:
            await self.stop(ref)
        except Exception:
            return


def _validated_result_page(value: object) -> ResultPage:
    if type(value) is not ResultPage:
        raise AthenaResultShapeError("Athena returned an invalid result page")
    page = value
    if type(page.columns) is not tuple or not all(
        type(column) is ResultColumn
        and type(column.name) is str
        and type(column.type_name) is str
        and type(column.nullable) is str
        for column in page.columns
    ):
        raise AthenaResultShapeError("Athena returned invalid result columns")
    if type(page.rows) is not tuple:
        raise AthenaResultShapeError("Athena returned invalid result rows")
    for row in page.rows:
        if type(row) is not tuple:
            raise AthenaResultShapeError("Athena returned an invalid result row")
        if len(row) != len(page.columns):
            raise AthenaResultShapeError("Athena result row width does not match columns")
        if not all(item is None or type(item) is str for item in row):
            raise AthenaResultShapeError("Athena returned an invalid result value")
    if page.next_token is not None and (type(page.next_token) is not str or not page.next_token):
        raise AthenaResultShapeError("Athena returned an invalid pagination token")
    return page


def _prepared_run_error(exc: BaseException, *, phase: str) -> BaseException:
    if isinstance(exc, AthenaResultShapeError):
        return AthenaResultShapeError(str(exc))
    if isinstance(exc, AthenaQueryFailedError):
        return AthenaQueryFailedError("Athena query failed")
    if isinstance(exc, AthenaQueryCancelledError):
        return AthenaQueryCancelledError("Athena query was cancelled")
    if isinstance(exc, QueryRejectedError):
        return QueryRejectedError(str(exc))
    if isinstance(exc, ValidationError):
        return ValidationError(str(exc))
    if isinstance(exc, ProviderError) and phase == "run":
        return ProviderError(str(exc))
    if phase == "start":
        return ProviderError("Athena query start failed")
    if phase == "poll":
        return ProviderError("Athena query status request failed")
    if phase == "results":
        return ProviderError("Athena results request failed")
    return ProviderError("Athena query request failed")


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
