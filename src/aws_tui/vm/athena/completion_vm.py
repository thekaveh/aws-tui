"""Explicit, bounded schema completion owned by one Athena provider lifetime."""

from __future__ import annotations

import asyncio
import math
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Literal, TypeVar

from aws_tui.domain.data_catalog import Column, TableRef, TableSummary
from aws_tui.domain.query import QueryContext
from aws_tui.domain.sql_authoring import (
    CompletionTable,
    CompletionTarget,
    SqlCompletion,
    completion_choices,
    completion_target,
)
from aws_tui.domain.sql_policy import ReadOnlySqlPolicy
from aws_tui.vm.athena.query_vm import AthenaQueryVM

CompletionState = Literal["ready", "partial", "unavailable"]
T = TypeVar("T")
MAX_TABLES = 200
MAX_LIST_PAGES = 4
MAX_DETAILS = 4
MAX_COLUMNS = 2000


@dataclass(frozen=True, slots=True)
class CompletionBatch:
    target: CompletionTarget = field(repr=False)
    choices: tuple[SqlCompletion, ...] = field(repr=False)
    state: CompletionState
    message: str
    provider: object = field(repr=False)
    generation: int
    editor_revision: int
    context: QueryContext = field(repr=False)
    context_revision: int


def _same_source(ref: TableRef, context: QueryContext) -> bool:
    return type(ref) is TableRef and (
        type(ref.table_name) is str
        and bool(ref.table_name)
        and ref.connection_name == context.connection_name
        and ref.region == context.region
        and ref.catalog_name == context.catalog
        and ref.database_name == context.database
    )


def _required_tables(
    sql: str, target: CompletionTarget, context: QueryContext
) -> tuple[TableRef, ...]:
    if target.table_position:
        return ()
    # The cursor marker makes a partial identifier into a parseable expression;
    # this is metadata discovery, never a query submission or policy change.
    proposed = sql[: target.start] + "__aws_tui_schema_cursor__" + sql[target.end :]
    return tuple(
        ref
        for ref in ReadOnlySqlPolicy().table_refs(proposed, context)
        if _same_source(ref, context)
    )


class AthenaCompletionVM:
    def __init__(
        self,
        *,
        client: Any,
        query: AthenaQueryVM,
        context_revision: Callable[[], int] = lambda: 0,
        is_current: Callable[[], bool] = lambda: True,
        notify: Callable[[], None] = lambda: None,
        metadata_timeout: float = 10.0,
    ) -> None:
        if not math.isfinite(metadata_timeout) or metadata_timeout <= 0:
            raise ValueError("Completion deadline must be finite and positive")
        self._client = client
        self._query = query
        self._context_revision = context_revision
        self._is_current = is_current
        self._notify = notify
        self._timeout = metadata_timeout
        self._provider = object()
        self._generation = 0
        self._closed = False
        self._busy = False
        self._active: asyncio.Task[Any] | None = None
        self._computations: set[asyncio.Task[Any]] = set()
        self._metadata_tasks: set[asyncio.Task[tuple[bool, bool]]] = set()
        self._state = "idle"
        self._message = "Completion loads schema only when requested"
        self._cache_key: tuple[QueryContext, int] | None = None
        self._tables: tuple[CompletionTable, ...] | None = None
        self._tables_partial = False
        self._schema_key: tuple[TableRef, ...] | None = None
        self._schemas: tuple[CompletionTable, ...] = ()
        self._schemas_partial = False

    @property
    def busy(self) -> bool:
        return self._busy or bool(self._metadata_tasks)

    @property
    def state(self) -> str:
        return self._state

    @property
    def message(self) -> str:
        return self._message

    def _alive(self) -> bool:
        return not self._closed and self._is_current()

    def invalidate(self, *, notify: bool = True) -> None:
        self._generation += 1
        self._cache_key = None
        self._tables = None
        self._schema_key = None
        self._schemas = ()
        self._state = "idle"
        self._message = "Completion schema was refreshed or its context changed"
        if notify and not self._closed:
            self._notify()

    def is_current(self, batch: CompletionBatch | None) -> bool:
        return type(batch) is CompletionBatch and (
            self._alive()
            and batch.provider is self._provider
            and batch.generation == self._generation
            and batch.context == self._query.context
            and batch.context_revision == self._context_revision()
            and batch.editor_revision == self._query.editor_revision
        )

    async def _compute(self, operation: Callable[[], T]) -> T:
        # Lexing/parsing a large buffer must not block Textual's message pump.
        task = asyncio.create_task(asyncio.to_thread(operation))
        self._computations.add(task)
        task.add_done_callback(self._computations.discard)
        cancelled = False
        try:
            while True:
                try:
                    result = await asyncio.shield(task)
                    if cancelled:
                        raise asyncio.CancelledError
                    return result
                except asyncio.CancelledError:
                    if task.done():
                        raise
                    cancelled = True
        finally:
            self._computations.discard(task)

    async def _metadata(
        self,
        sql: str,
        target: CompletionTarget,
        context: QueryContext,
        work: list[CompletionTable],
        owned: Callable[[], bool],
    ) -> tuple[bool, bool]:
        """Fill one bounded local result; return (partial, unavailable)."""
        partial = self._tables_partial if self._tables is not None else False
        if self._tables is not None:
            work.extend(self._tables)
        else:
            token: str | None = None
            tokens: set[str] = set()
            try:
                for _ in range(MAX_LIST_PAGES):
                    if not owned():
                        return True, not work
                    rows, next_token = await self._client.list_tables_page(
                        context.catalog,
                        context.database,
                        workgroup=context.workgroup,
                        start_token=token,
                    )
                    if not owned():
                        return True, True
                    if not isinstance(rows, (tuple, list)) or (
                        next_token is not None
                        and (
                            type(next_token) is not str or not next_token or len(next_token) > 1024
                        )
                    ):
                        return True, not work
                    for row in rows[: MAX_TABLES + 1]:
                        if type(row) is not TableSummary or not _same_source(row.ref, context):
                            return True, not work
                        if row.ref not in {item.ref for item in work}:
                            if len(work) == MAX_TABLES:
                                partial = True
                                break
                            work.append(CompletionTable(row.ref, ()))
                    partial |= len(rows) > MAX_TABLES
                    if not next_token:
                        break
                    if len(work) >= MAX_TABLES or next_token in tokens:
                        partial = True
                        break
                    token = next_token
                    tokens.add(token)
                else:
                    partial |= bool(next_token)
            except Exception:
                return True, not work
            if owned():
                self._tables = tuple(work)
                self._tables_partial = partial
        if target.table_position or not owned():
            return partial, False
        needed = await self._compute(lambda: _required_tables(sql, target, context))
        if not owned():
            return True, True
        wanted = needed or tuple(item.ref for item in work)
        canonical: list[TableRef] = []
        for ref in wanted:
            matches = [
                row.ref
                for row in work
                if row.ref.table_name.casefold() == ref.table_name.casefold()
            ]
            if len(matches) > 1:
                partial = True
                continue
            canonical.append(matches[0] if matches else ref)
        wanted = tuple(canonical)
        partial |= len(wanted) > MAX_DETAILS
        wanted = tuple(dict.fromkeys(wanted))[:MAX_DETAILS]
        # Referenced current-context tables can be beyond the listing ceiling.
        refs = set(wanted)
        work[:] = (
            [CompletionTable(ref, ()) for ref in wanted]
            + [item for item in work if item.ref not in refs]
        )[:MAX_TABLES]
        if wanted == self._schema_key:
            detail_rows = self._schemas
            partial |= self._schemas_partial
        else:
            details: list[CompletionTable] = []
            remaining = MAX_COLUMNS
            unavailable = False
            for ref in wanted:
                if not owned():
                    return True, True
                try:
                    result = await self._client.get_table_columns(
                        context.catalog,
                        context.database,
                        ref.table_name,
                        workgroup=context.workgroup,
                    )
                    if not owned():
                        return True, True
                    if (
                        type(result) is not CompletionTable
                        or result.ref != ref
                        or type(result.columns) is not tuple
                        or any(
                            type(c) is not Column
                            or type(c.name) is not str
                            or not c.name
                            or type(c.type_name) is not str
                            or not c.type_name
                            for c in result.columns
                        )
                    ):
                        raise ValueError("invalid schema metadata")
                    columns = result.columns[:remaining]
                    partial |= len(result.columns) > remaining or not columns
                    remaining -= len(columns)
                    bounded = CompletionTable(ref, columns)
                    details.append(bounded)
                    # Retain available columns even if a later request times out.
                    work[:] = [bounded if item.ref == ref else item for item in work]
                except Exception:
                    partial = unavailable = True
            detail_rows = tuple(details)
            if owned() and not unavailable:
                self._schema_key = wanted
                self._schemas = detail_rows
                self._schemas_partial = partial
        by_ref = {row.ref: row for row in detail_rows}
        work[:] = [by_ref.get(row.ref, row) for row in work]
        return partial, bool(wanted) and not any(row.columns for row in detail_rows)

    async def complete(self, cursor: int) -> CompletionBatch | None:
        task = asyncio.current_task()
        if not self._alive() or self.busy or (task is not None and task.cancelling()):
            return None
        context = self._query.context
        revision = self._query.editor_revision
        context_revision = self._context_revision()
        sql = self._query.sql
        key = (context, context_revision)
        if key != self._cache_key:
            self.invalidate()
            self._cache_key = key
        self._generation += 1
        generation = self._generation
        retired = False

        def owned() -> bool:
            return (
                not retired
                and self._alive()
                and self._generation == generation
                and self._query.context == context
                and self._query.editor_revision == revision
                and self._context_revision() == context_revision
                and (task is None or task.cancelling() == 0)
            )

        self._busy = True
        self._active = task
        self._state = "loading"
        self._message = "Loading bounded schema completion"
        self._notify()
        work: list[CompletionTable] = []
        metadata: asyncio.Task[tuple[bool, bool]] | None = None
        try:
            if not owned():
                return None
            target = await self._compute(lambda: completion_target(sql, cursor))
            if not owned():
                return None
            partial = unavailable = False
            if not all(context.cache_key):
                unavailable = True
            elif target.allowed:
                deadline = asyncio.get_running_loop().time() + self._timeout
                metadata = asyncio.create_task(
                    self._metadata(
                        sql,
                        target,
                        context,
                        work,
                        lambda: owned() and asyncio.get_running_loop().time() < deadline,
                    )
                )
                self._metadata_tasks.add(metadata)
                metadata.add_done_callback(self._metadata_finished)
                done, _ = await asyncio.wait({metadata}, timeout=self._timeout)
                if metadata in done:
                    partial, unavailable = metadata.result()
                else:
                    metadata.cancel()
                    partial, unavailable = True, not work
            if not owned():
                return None
            choices = await self._compute(
                lambda: completion_choices(sql, target, tuple(work), context)
            )
            if not owned():
                return None
            partial |= len(choices) == 200
            state: CompletionState = (
                "unavailable" if unavailable and not work else "partial" if partial else "ready"
            )
            message = {
                "ready": "Schema completion ready",
                "partial": "Partial schema or suggestion limit; metadata reads are bounded",
                "unavailable": "Schema unavailable; keywords remain available. Select a context or refresh.",
            }[state]
            if not target.allowed:
                state = "unavailable"
                message = "Completion is unavailable inside literals or comments or at this cursor"
            self._state, self._message = state, message
            return CompletionBatch(
                target,
                choices,
                state,
                message,
                self._provider,
                generation,
                revision,
                context,
                context_revision,
            )
        except asyncio.CancelledError:
            return None
        finally:
            retired = True
            if metadata is not None and not metadata.done() and not metadata.cancelling():
                metadata.cancel()
            self._busy = False
            self._active = None
            if self._generation == generation and self._state == "loading":
                self._state = "idle"
                self._message = "Completion canceled or its editor context changed"
            if not self._closed:
                self._notify()

    def _metadata_finished(self, task: asyncio.Task[tuple[bool, bool]]) -> None:
        self._metadata_tasks.discard(task)
        if not task.cancelled():
            task.exception()
        if not self._closed:
            self._notify()

    def close_input_admission(self) -> None:
        self._closed = True
        self.invalidate()
        try:
            current = asyncio.current_task()
        except RuntimeError:
            current = None
        if self._active is not None and self._active is not current:
            self._active.cancel()
        for task in self._metadata_tasks:
            if not task.done() and not task.cancelling():
                task.cancel()

    async def shutdown(self) -> None:
        self.close_input_admission()
        current = asyncio.current_task()
        tasks = set(self._computations) | self._metadata_tasks
        if self._active is not None and self._active is not current:
            tasks.add(self._active)
        if not tasks:
            return
        draining = asyncio.gather(*tasks, return_exceptions=True)
        while not draining.done():
            try:
                await asyncio.shield(draining)
            except asyncio.CancelledError:
                continue
