"""Immutable Athena query domain values."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum


class QueryState(StrEnum):
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


@dataclass(frozen=True, slots=True)
class QueryContext:
    connection_name: str
    region: str
    workgroup: str
    catalog: str
    database: str

    @property
    def cache_key(self) -> tuple[str, str, str, str, str]:
        return (
            self.connection_name,
            self.region,
            self.workgroup,
            self.catalog,
            self.database,
        )


@dataclass(frozen=True, slots=True)
class QueryExecutionRef:
    execution_id: str
    connection_name: str
    region: str
    workgroup: str


@dataclass(frozen=True, slots=True)
class QueryStatistics:
    engine_ms: int | None
    queue_ms: int | None
    planning_ms: int | None
    service_ms: int | None
    bytes_scanned: int | None
    reused_previous_result: bool


@dataclass(frozen=True, slots=True)
class AthenaQueryError:
    category: int | None
    error_type: int | None
    retryable: bool
    message: str = field(repr=False)


@dataclass(frozen=True, slots=True)
class QueryExecutionSummary:
    ref: QueryExecutionRef
    state: QueryState
    submitted_at: datetime | None
    completed_at: datetime | None
    statement_type: str | None


@dataclass(frozen=True, slots=True)
class QueryExecutionDetail:
    summary: QueryExecutionSummary
    state_reason: str | None = field(repr=False)
    context: QueryContext
    statistics: QueryStatistics
    output_location: str | None = field(repr=False)
    engine_version: str | None
    error: AthenaQueryError | None


@dataclass(frozen=True, slots=True)
class ResultColumn:
    name: str
    type_name: str
    nullable: str


@dataclass(frozen=True, slots=True)
class ResultPage:
    columns: tuple[ResultColumn, ...]
    rows: tuple[tuple[str | None, ...], ...] = field(repr=False)
    next_token: str | None


@dataclass(frozen=True, slots=True)
class NamedQuery:
    query_id: str
    name: str
    description: str | None
    database: str
    query_string: str = field(repr=False)
    workgroup: str


@dataclass(frozen=True, slots=True)
class PreparedStatement:
    name: str
    query_statement: str = field(repr=False)
    workgroup: str
    description: str | None
    last_modified_at: datetime | None


__all__ = [
    "AthenaQueryError",
    "NamedQuery",
    "PreparedStatement",
    "QueryContext",
    "QueryExecutionDetail",
    "QueryExecutionRef",
    "QueryExecutionSummary",
    "QueryState",
    "QueryStatistics",
    "ResultColumn",
    "ResultPage",
]
