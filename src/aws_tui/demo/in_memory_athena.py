"""Deterministic, profile-local Amazon Athena client for demo mode."""

from __future__ import annotations

import csv
import hmac
import io
import secrets
import unicodedata
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from hashlib import sha256
from typing import TypeVar

from aws_tui.domain.athena import (
    AthenaCatalogSummary,
    AthenaWorkgroupDetail,
    AthenaWorkgroupSummary,
    ResultConfigurationRequiredError,
)
from aws_tui.domain.data_catalog import (
    DatabaseRef,
    DatabaseSummary,
    TableRef,
    TableSummary,
)
from aws_tui.domain.filesystem import (
    FileSystemProvider,
    NotFoundError,
    PathRef,
    PermissionDeniedError,
    ValidationError,
)
from aws_tui.domain.query import (
    NamedQuery,
    PreparedStatement,
    PreparedStatementSummary,
    QueryContext,
    QueryExecutionDetail,
    QueryExecutionRef,
    QueryExecutionSummary,
    QueryState,
    QueryStatistics,
    ResultColumn,
    ResultPage,
)
from aws_tui.domain.s3_uri import parse_s3_uri
from aws_tui.domain.sql_policy import ReadOnlySqlPolicy

T = TypeVar("T")
_STARTED_STATES = (QueryState.QUEUED, QueryState.RUNNING, QueryState.SUCCEEDED)
_TERMINAL_STATES = frozenset(
    {
        QueryState.SUCCEEDED,
        QueryState.FAILED,
        QueryState.CANCELLED,
    }
)


@dataclass(frozen=True, slots=True)
class AthenaCall:
    """Recorded fake invocation whose sensitive arguments stay out of reprs."""

    method: str
    arguments: tuple[object, ...] = field(repr=False)


@dataclass(frozen=True, slots=True)
class _IdempotentQuery:
    request_fingerprint: bytes = field(repr=False)
    execution_id: str = field(repr=False)


@dataclass(frozen=True, slots=True)
class _SeededQueryResult:
    pages: tuple[ResultPage, ...] = field(repr=False)


@dataclass(frozen=True, slots=True)
class _ListTokenRecord:
    scope: tuple[str | None, ...]
    offset: int
    page_size: int


class _ListTokenCodec:
    """Instance-owned opaque pagination tokens for deterministic demo lists."""

    def __init__(self) -> None:
        self._key = secrets.token_bytes(32)
        self._records: dict[str, _ListTokenRecord] = {}

    def page(
        self,
        rows: Sequence[T],
        *,
        scope: tuple[str | None, ...],
        start_token: str | None,
        page_size: int,
    ) -> tuple[list[T], str | None]:
        if type(page_size) is not int or page_size <= 0:
            raise ValidationError("invalid Athena page size")
        if start_token is None:
            offset = 0
        else:
            record = self._record_for(start_token)
            if record.scope != scope or record.page_size != page_size or record.offset >= len(rows):
                raise ValidationError("invalid Athena pagination token")
            offset = record.offset
        page = list(rows[offset : offset + page_size])
        next_offset = offset + page_size
        if next_offset >= len(rows):
            return page, None
        record = _ListTokenRecord(scope, next_offset, page_size)
        token = self._encode(record)
        self._records[token] = record
        return page, token

    def _record_for(self, token: str) -> _ListTokenRecord:
        if (
            type(token) is not str
            or len(token) != 64
            or not token.isascii()
            or token != token.lower()
            or any(char not in "0123456789abcdef" for char in token)
        ):
            raise ValidationError("invalid Athena pagination token")
        record = self._records.get(token)
        if record is None or not hmac.compare_digest(token, self._encode(record)):
            raise ValidationError("invalid Athena pagination token")
        return record

    def _encode(self, record: _ListTokenRecord) -> str:
        material = _fingerprint(
            *record.scope,
            str(record.offset),
            str(record.page_size),
        )
        return hmac.digest(self._key, material, "sha256").hex()


class InMemoryAthena:
    """In-memory implementation of the paginated Athena client surface."""

    def __init__(
        self,
        *,
        connection_name: str,
        region: str,
        storage_namespace: str | None = None,
        result_store: FileSystemProvider | None = None,
    ) -> None:
        self.connection_name = connection_name
        self.region = region
        self.storage_namespace = storage_namespace or connection_name
        self._result_store = result_store
        self.page_size = 100
        self.calls: list[AthenaCall] = []
        self.workgroups: list[AthenaWorkgroupSummary] = []
        self.workgroup_details: dict[str, AthenaWorkgroupDetail] = {}
        self.catalogs: dict[str, list[AthenaCatalogSummary]] = {}
        self.databases: dict[tuple[str, str], list[DatabaseSummary]] = {}
        self.tables: dict[tuple[str, str, str], list[TableSummary]] = {}
        self.history: dict[str, list[str]] = {}
        self.query_executions: dict[str, QueryExecutionDetail] = {}
        self.result_pages: dict[tuple[str, str | None], ResultPage] = {}
        self._result_page_order: dict[str, tuple[ResultPage, ...]] = {}
        self.result_errors: dict[str, Exception] = {}
        self.named_queries: dict[str, NamedQuery] = {}
        self.named_query_ids: dict[str, list[str]] = {}
        self.prepared_statements: dict[tuple[str, str], PreparedStatement] = {}
        self.prepared_names: dict[str, list[str]] = {}
        self.access_error: PermissionDeniedError | None = None
        self._sql_policy = ReadOnlySqlPolicy()
        self._list_tokens = _ListTokenCodec()
        self._request_tokens: dict[bytes, _IdempotentQuery] = {}
        self._started_state_indexes: dict[str, int] = {}
        self._active_app_started: set[str] = set()
        self._published_result_ids: set[str] = set()
        self._seeded_query_results: dict[
            tuple[str, tuple[str, str, str, str, str]],
            _SeededQueryResult,
        ] = {}
        self._next_execution_number = 1

    def add_workgroup(
        self,
        name: str,
        *,
        output_location: str | None,
        managed_results: bool = False,
        enforce_workgroup_configuration: bool = True,
        state: str = "ENABLED",
    ) -> AthenaWorkgroupDetail:
        if state not in {"ENABLED", "DISABLED"}:
            raise ValueError("invalid demo Athena workgroup state")
        summary = AthenaWorkgroupSummary(
            name,
            state,
            f"{name} demo workgroup",
            None,
        )
        detail = AthenaWorkgroupDetail(
            summary,
            output_location,
            enforce_workgroup_configuration,
            True,
            100_000_000,
            "Athena engine version 3",
            managed_results,
        )
        self.workgroups.append(summary)
        self.workgroup_details[name] = detail
        self.catalogs.setdefault(name, [])
        self.history.setdefault(name, [])
        self.named_query_ids.setdefault(name, [])
        self.prepared_names.setdefault(name, [])
        return detail

    def add_catalog(self, workgroup: str, name: str) -> AthenaCatalogSummary:
        catalog = AthenaCatalogSummary(
            name,
            "GLUE",
            f"{name} demo catalog",
        )
        self.catalogs.setdefault(workgroup, []).append(catalog)
        return catalog

    def add_database(
        self,
        workgroup: str,
        catalog: str,
        name: str,
    ) -> DatabaseSummary:
        database = DatabaseSummary(
            DatabaseRef(catalog, name, self.connection_name, self.region),
            f"{name} demo database",
            f"s3://{self.storage_namespace}/{name}/",
            None,
        )
        self.databases.setdefault((workgroup, catalog), []).append(database)
        return database

    def add_table(
        self,
        workgroup: str,
        catalog: str,
        database: str,
        name: str,
    ) -> TableSummary:
        table = TableSummary(
            TableRef(
                catalog,
                database,
                name,
                self.connection_name,
                self.region,
            ),
            f"{name} demo table",
            "data-platform",
            "EXTERNAL_TABLE",
            None,
            None,
        )
        self.tables.setdefault((workgroup, catalog, database), []).append(table)
        return table

    def add_query_result(
        self,
        sql: str,
        context: QueryContext,
        *,
        columns: tuple[ResultColumn, ...],
        rows: tuple[tuple[str | None, ...], ...],
    ) -> None:
        """Register one exact read-only query response for a profile-local context."""
        if (
            context.connection_name != self.connection_name
            or context.region != self.region
            or not context.workgroup
            or not context.catalog
            or not context.database
        ):
            raise ValueError("seeded query context does not match fake")
        normalized_sql = self._sql_policy.validate(sql)
        if not columns or any(len(row) != len(columns) for row in rows):
            raise ValueError("seeded query result shape is invalid")
        if type(self.page_size) is not int or self.page_size <= 0:
            raise ValueError("seeded query page size is invalid")
        key = (normalized_sql, context.cache_key)
        if key in self._seeded_query_results:
            raise ValueError("seeded query result already exists")
        pages = tuple(
            ResultPage(columns, rows[offset : offset + self.page_size], None)
            for offset in range(0, max(len(rows), 1), self.page_size)
        )
        self._seeded_query_results[key] = _SeededQueryResult(pages)

    def add_query_execution(
        self,
        detail: QueryExecutionDetail,
        *,
        result_pages: Sequence[ResultPage] = (),
        result_error: Exception | None = None,
    ) -> None:
        ref = detail.summary.ref
        if (
            ref.connection_name != self.connection_name
            or ref.region != self.region
            or detail.context.connection_name != self.connection_name
            or detail.context.region != self.region
            or detail.context.workgroup != ref.workgroup
        ):
            raise ValueError("query execution identity does not match fake")
        self.query_executions[ref.execution_id] = detail
        self.history.setdefault(ref.workgroup, []).append(ref.execution_id)
        if result_error is not None:
            self.result_errors[ref.execution_id] = result_error
        self._install_result_pages(ref.execution_id, result_pages)

    def add_named_query(self, query: NamedQuery) -> None:
        self.named_queries[query.query_id] = query
        self.named_query_ids.setdefault(query.workgroup, []).append(query.query_id)

    def add_prepared_statement(self, statement: PreparedStatement) -> None:
        key = (statement.workgroup, statement.name)
        self.prepared_statements[key] = statement
        self.prepared_names.setdefault(statement.workgroup, []).append(statement.name)

    async def list_workgroups_page(
        self,
        *,
        start_token: str | None = None,
    ) -> tuple[list[AthenaWorkgroupSummary], str | None]:
        self._record("list_workgroups_page", start_token)
        self._raise_if_denied()
        return self._list_tokens.page(
            self.workgroups,
            scope=("list_workgroups_page",),
            start_token=start_token,
            page_size=self.page_size,
        )

    async def get_workgroup(self, name: str) -> AthenaWorkgroupDetail:
        self._record("get_workgroup", name)
        self._raise_if_denied()
        try:
            return self.workgroup_details[name]
        except KeyError:
            raise NotFoundError("Athena workgroup not found") from None

    async def list_catalogs_page(
        self,
        *,
        workgroup: str | None = None,
        start_token: str | None = None,
    ) -> tuple[list[AthenaCatalogSummary], str | None]:
        self._record("list_catalogs_page", workgroup, start_token)
        self._raise_if_denied()
        rows = self.catalogs.get(workgroup or "", [])
        return self._list_tokens.page(
            rows,
            scope=("list_catalogs_page", workgroup),
            start_token=start_token,
            page_size=self.page_size,
        )

    async def list_databases_page(
        self,
        catalog: str,
        *,
        workgroup: str | None = None,
        start_token: str | None = None,
    ) -> tuple[list[DatabaseSummary], str | None]:
        self._record("list_databases_page", catalog, workgroup, start_token)
        self._raise_if_denied()
        rows = self.databases.get((workgroup or "", catalog), [])
        return self._list_tokens.page(
            rows,
            scope=("list_databases_page", catalog, workgroup),
            start_token=start_token,
            page_size=self.page_size,
        )

    async def list_tables_page(
        self,
        catalog: str,
        database: str,
        *,
        workgroup: str | None = None,
        start_token: str | None = None,
    ) -> tuple[list[TableSummary], str | None]:
        self._record(
            "list_tables_page",
            catalog,
            database,
            workgroup,
            start_token,
        )
        self._raise_if_denied()
        rows = self.tables.get((workgroup or "", catalog, database), [])
        return self._list_tokens.page(
            rows,
            scope=("list_tables_page", catalog, database, workgroup),
            start_token=start_token,
            page_size=self.page_size,
        )

    async def list_query_executions_page(
        self,
        workgroup: str,
        *,
        start_token: str | None = None,
    ) -> tuple[list[QueryExecutionRef], str | None]:
        self._record("list_query_executions_page", workgroup, start_token)
        self._raise_if_denied()
        ids, token = self._list_tokens.page(
            self.history.get(workgroup, []),
            scope=("list_query_executions_page", workgroup),
            start_token=start_token,
            page_size=self.page_size,
        )
        return [self.query_executions[execution_id].summary.ref for execution_id in ids], token

    async def get_query_execution(
        self,
        execution_id: str,
    ) -> QueryExecutionDetail:
        self._record("get_query_execution", execution_id)
        self._raise_if_denied()
        try:
            detail = self.query_executions[execution_id]
        except KeyError:
            raise NotFoundError("Athena query execution not found") from None
        state_index = self._started_state_indexes.get(execution_id)
        if state_index is None:
            if detail.summary.state is QueryState.SUCCEEDED:
                await self._publish_result_object(detail)
            return detail
        state = _STARTED_STATES[state_index]
        now = datetime(2026, 7, 26, 12, 0, tzinfo=UTC)
        updated = replace(
            detail,
            summary=replace(
                detail.summary,
                state=state,
                completed_at=now if state in _TERMINAL_STATES else None,
            ),
        )
        if state is QueryState.SUCCEEDED:
            await self._publish_result_object(updated)
        self.query_executions[execution_id] = updated
        if state_index + 1 < len(_STARTED_STATES):
            self._started_state_indexes[execution_id] = state_index + 1
        else:
            self._active_app_started.discard(execution_id)
            self._started_state_indexes.pop(execution_id, None)
        return updated

    async def get_query_runtime_statistics(
        self,
        execution_id: str,
    ) -> QueryStatistics:
        self._record("get_query_runtime_statistics", execution_id)
        detail = await self._query_execution_without_record(execution_id)
        return detail.statistics

    async def start_query(
        self,
        sql: str,
        context: QueryContext,
        *,
        request_token: str,
        output_location: str | None = None,
    ) -> QueryExecutionRef:
        self._record("start_query", sql, context, request_token, output_location)
        self._raise_if_denied()
        self._validate_start_arguments(
            context,
            request_token=request_token,
            output_location=output_location,
        )
        if type(sql) is not str:
            raise ValidationError("Athena SQL is invalid")
        normalized_sql = self._sql_policy.validate(sql)
        seeded = self._seeded_query_results.get((normalized_sql, context.cache_key))
        if seeded is None:
            raise ValidationError("Athena demo query fixture is unavailable")
        token_fingerprint = _fingerprint(request_token)
        request_fingerprint = _fingerprint(
            normalized_sql,
            *context.cache_key,
            output_location,
        )
        known = self._request_tokens.get(token_fingerprint)
        if known is not None:
            if known.request_fingerprint != request_fingerprint:
                raise ValidationError(
                    "Athena request token was reused with different query parameters"
                )
            return self.query_executions[known.execution_id].summary.ref
        workgroup = self.workgroup_details[context.workgroup]
        result_root = (
            workgroup.output_location
            if workgroup.enforce_workgroup_configuration
            else output_location or workgroup.output_location
        )
        if result_root is None and not workgroup.managed_query_results_enabled:
            raise ResultConfigurationRequiredError("Athena result configuration is required")
        execution_id = f"{self.connection_name}-app-{self._next_execution_number:04d}"
        self._next_execution_number += 1
        ref = QueryExecutionRef(
            execution_id,
            self.connection_name,
            self.region,
            context.workgroup,
        )
        detail = QueryExecutionDetail(
            QueryExecutionSummary(
                ref,
                QueryState.QUEUED,
                datetime(2026, 7, 26, 12, 0, tzinfo=UTC),
                None,
                "DML",
            ),
            None,
            context,
            QueryStatistics(18, 2, 3, 1, 128, False),
            _started_output_location(result_root, execution_id),
            "Athena engine version 3",
            None,
        )
        self.query_executions[execution_id] = detail
        self.history.setdefault(context.workgroup, []).insert(0, execution_id)
        self._install_result_pages(execution_id, seeded.pages)
        self._request_tokens[token_fingerprint] = _IdempotentQuery(
            request_fingerprint,
            execution_id,
        )
        self._started_state_indexes[execution_id] = 0
        self._active_app_started.add(execution_id)
        return ref

    async def stop_query(self, execution_id: str) -> None:
        self._record("stop_query", execution_id)
        self._raise_if_denied()
        if execution_id not in self._active_app_started:
            raise ValidationError("query is not an active app-started query")
        detail = self.query_executions[execution_id]
        self.query_executions[execution_id] = replace(
            detail,
            summary=replace(
                detail.summary,
                state=QueryState.CANCELLED,
                completed_at=datetime(2026, 7, 26, 12, 0, tzinfo=UTC),
            ),
            state_reason="Cancelled from the demo query page",
        )
        self._active_app_started.remove(execution_id)
        self._started_state_indexes.pop(execution_id, None)

    async def get_results_page(
        self,
        execution_id: str,
        *,
        start_token: str | None = None,
    ) -> ResultPage:
        self._record("get_results_page", execution_id, start_token)
        self._raise_if_denied()
        if type(execution_id) is not str or not execution_id:
            raise ValidationError("Athena query execution is invalid")
        if execution_id not in self.query_executions:
            raise NotFoundError("Athena query results not found")
        if start_token is not None and (
            type(start_token) is not str
            or not start_token
            or len(start_token) > 64
            or (execution_id, start_token) not in self.result_pages
        ):
            raise ValidationError("invalid Athena result pagination token")
        error = self.result_errors.get(execution_id)
        if error is not None:
            raise error
        try:
            return self.result_pages[(execution_id, start_token)]
        except KeyError:
            raise NotFoundError("Athena query results not found") from None

    async def list_named_queries_page(
        self,
        workgroup: str,
        *,
        start_token: str | None = None,
    ) -> tuple[list[str], str | None]:
        self._record("list_named_queries_page", workgroup, start_token)
        self._raise_if_denied()
        return self._list_tokens.page(
            self.named_query_ids.get(workgroup, []),
            scope=("list_named_queries_page", workgroup),
            start_token=start_token,
            page_size=self.page_size,
        )

    async def get_named_queries(self, ids: list[str]) -> tuple[NamedQuery, ...]:
        self._record("get_named_queries", tuple(ids))
        self._raise_if_denied()
        return tuple(
            self.named_queries[query_id] for query_id in ids if query_id in self.named_queries
        )

    async def list_prepared_statements_page(
        self,
        workgroup: str,
        *,
        start_token: str | None = None,
    ) -> tuple[list[PreparedStatementSummary], str | None]:
        self._record("list_prepared_statements_page", workgroup, start_token)
        self._raise_if_denied()
        names, token = self._list_tokens.page(
            self.prepared_names.get(workgroup, []),
            scope=("list_prepared_statements_page", workgroup),
            start_token=start_token,
            page_size=self.page_size,
        )
        return [
            PreparedStatementSummary(
                name,
                self.prepared_statements[(workgroup, name)].last_modified_at,
            )
            for name in names
        ], token

    async def get_prepared_statement(
        self,
        name: str,
        workgroup: str,
    ) -> PreparedStatement:
        self._record("get_prepared_statement", name, workgroup)
        self._raise_if_denied()
        try:
            return self.prepared_statements[(workgroup, name)]
        except KeyError:
            raise NotFoundError("Athena prepared statement not found") from None

    async def _query_execution_without_record(
        self,
        execution_id: str,
    ) -> QueryExecutionDetail:
        self._raise_if_denied()
        try:
            return self.query_executions[execution_id]
        except KeyError:
            raise NotFoundError("Athena query execution not found") from None

    async def _publish_result_object(self, detail: QueryExecutionDetail) -> None:
        execution_id = detail.summary.ref.execution_id
        if self._result_store is None or execution_id in self._published_result_ids:
            return
        location = parse_s3_uri(detail.output_location)
        if location is None:
            return
        key = location.path.removeprefix("/")
        if not key or key.endswith("/"):
            return
        path = PathRef((location.bucket, *key.split("/")))
        await self._result_store.mkdir(path.parent())
        pages = self._result_page_order.get(execution_id)
        if not pages:
            return
        body = serialize_result_pages_csv(pages)

        async def chunks() -> AsyncIterator[bytes]:
            yield body

        await self._result_store.write_stream(
            path,
            chunks(),
            total_size=len(body),
        )
        self._published_result_ids.add(execution_id)

    def _install_result_pages(
        self,
        execution_id: str,
        pages: Sequence[ResultPage],
    ) -> None:
        normalized = tuple(pages)
        if not normalized:
            return
        columns = normalized[0].columns
        if any(page.columns != columns for page in normalized):
            raise ValueError("query result pages have inconsistent columns")
        installed: list[ResultPage] = []
        for index, page in enumerate(normalized):
            token = None if index == 0 else _result_token(execution_id, index)
            next_token = (
                _result_token(execution_id, index + 1) if index + 1 < len(normalized) else None
            )
            installed_page = replace(page, next_token=next_token)
            self.result_pages[(execution_id, token)] = installed_page
            installed.append(installed_page)
        self._result_page_order[execution_id] = tuple(installed)

    def _validate_start_arguments(
        self,
        context: QueryContext,
        *,
        request_token: str,
        output_location: str | None,
    ) -> None:
        if type(context) is not QueryContext or any(
            type(value) is not str or not value for value in context.cache_key
        ):
            raise ValidationError("Athena query context is invalid")
        if context.connection_name != self.connection_name or context.region != self.region:
            raise ValidationError("query context does not match Athena connection")
        workgroup = self.workgroup_details.get(context.workgroup)
        if workgroup is None or workgroup.summary.state != "ENABLED":
            raise ValidationError("Athena query context is unavailable")
        if not any(
            catalog.name == context.catalog for catalog in self.catalogs.get(context.workgroup, ())
        ):
            raise ValidationError("Athena query context is unavailable")
        if not any(
            database.ref.database_name == context.database
            for database in self.databases.get(
                (context.workgroup, context.catalog),
                (),
            )
        ):
            raise ValidationError("Athena query context is unavailable")
        if (
            type(request_token) is not str
            or not 32 <= len(request_token) <= 128
            or any(unicodedata.category(char) == "Cc" for char in request_token)
        ):
            raise ValidationError("Athena request token is invalid")
        if output_location is not None:
            location = (
                parse_s3_uri(output_location)
                if type(output_location) is str and output_location.startswith("s3://")
                else None
            )
            if (
                location is None
                or not location.path.startswith("/")
                or location.path.startswith("//")
                or not location.path.strip("/")
            ):
                raise ValidationError("Athena output location is invalid")

    def _raise_if_denied(self) -> None:
        if self.access_error is not None:
            raise self.access_error

    def _record(self, method: str, *arguments: object) -> None:
        self.calls.append(AthenaCall(method, arguments))


def _started_output_location(
    result_root: str | None,
    execution_id: str,
) -> str | None:
    if result_root is None:
        return None
    return f"{result_root.rstrip('/')}/{execution_id}.csv"


def _fingerprint(*values: str | None) -> bytes:
    digest = sha256()
    for value in values:
        encoded = b"" if value is None else value.encode("utf-8")
        digest.update(b"\x00" if value is None else b"\x01")
        digest.update(len(encoded).to_bytes(8, "big"))
        digest.update(encoded)
    return digest.digest()


def _result_token(execution_id: str, page_index: int) -> str:
    return sha256(f"{execution_id}\0{page_index}".encode()).hexdigest()


def serialize_result_pages_csv(pages: Sequence[ResultPage]) -> bytes:
    """Serialize complete Athena result pages as one deterministic CSV artifact."""
    if not pages:
        raise ValueError("Athena result pages are required")
    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer, lineterminator="\r\n")
    writer.writerow(column.name for column in pages[0].columns)
    for page in pages:
        for row in page.rows:
            writer.writerow("" if value is None else value for value in row)
    return buffer.getvalue().encode("utf-8")


__all__ = ["AthenaCall", "InMemoryAthena", "serialize_result_pages_csv"]
