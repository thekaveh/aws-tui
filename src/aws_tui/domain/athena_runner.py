"""Reusable bounded execution for read-only Athena queries."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

import anyio

from aws_tui.domain.filesystem import ProviderError, ValidationError
from aws_tui.domain.query import (
    QueryContext,
    QueryExecutionDetail,
    QueryExecutionRef,
    QueryState,
    ResultColumn,
)
from aws_tui.domain.sql_policy import ReadOnlySqlPolicy

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
            raise ValidationError("max_rows must be positive")
        ref: QueryExecutionRef | None = None
        active = False
        try:
            ref = await self.start(
                sql,
                context,
                request_token=request_token,
            )
            active = True
            if not _ref_matches_context(ref, context):
                raise ValidationError("Athena query does not match the active context")
            detail = await self._poll(ref, context)
            active = False
            if detail.summary.state is QueryState.FAILED:
                raise AthenaQueryFailedError("Athena query failed")
            if detail.summary.state is QueryState.CANCELLED:
                raise AthenaQueryCancelledError("Athena query was cancelled")
            columns, rows = await self._bounded_results(ref, max_rows=max_rows)
            return BoundedQueryResult(detail, columns, rows)
        except asyncio.CancelledError:
            if active and ref is not None:
                await asyncio.shield(self._best_effort_stop(ref))
            raise
        except ValidationError:
            if active and ref is not None:
                await self._best_effort_stop(ref)
            raise

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
        while len(rows) < max_rows:
            page = await self._client.get_results_page(
                ref.execution_id,
                start_token=token,
            )
            page_columns = tuple(page.columns)
            if columns is None:
                columns = page_columns
            elif page_columns != columns:
                raise AthenaResultShapeError("Athena result columns changed between pages")
            remaining = max_rows - len(rows)
            page_rows = tuple(page.rows)
            if any(len(row) != len(page_columns) for row in page_rows):
                raise AthenaResultShapeError("Athena result row width does not match columns")
            rows.extend(page_rows[:remaining])
            next_token = page.next_token
            if len(rows) >= max_rows or next_token is None:
                break
            if next_token in seen_tokens:
                raise AthenaResultShapeError("Athena result pagination token repeated")
            seen_tokens.add(next_token)
            token = next_token
        return columns or (), tuple(rows)

    async def _best_effort_stop(self, ref: QueryExecutionRef) -> None:
        try:
            await self.stop(ref)
        except Exception:
            return


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
