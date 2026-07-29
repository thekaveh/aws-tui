from __future__ import annotations

from datetime import datetime

from aws_tui.domain.athena import (
    AthenaCatalogSummary,
    AthenaWorkgroupDetail,
    AthenaWorkgroupSummary,
)
from aws_tui.domain.data_catalog import DatabaseRef, DatabaseSummary, TableRef
from aws_tui.domain.query import (
    AthenaQueryError,
    NamedQuery,
    NamedQuerySummary,
    PreparedStatement,
    PreparedStatementSummary,
    QueryContext,
    QueryExecutionDetail,
    QueryExecutionRef,
    QueryExecutionSummary,
    QueryState,
    QueryStatistics,
    ResultColumn,
)

_ATHENA_ERROR_CATEGORIES = frozenset({1, 2, 3})
_ATHENA_ERROR_TYPE_RANGE = range(0, 10_000)
_ATHENA_WORKGROUP_STATES = frozenset({"ENABLED", "DISABLED"})
_ATHENA_CATALOG_TYPES = frozenset({"LAMBDA", "GLUE", "HIVE", "FEDERATED"})
_ATHENA_NULLABILITY = frozenset({"NOT_NULL", "NULLABLE", "UNKNOWN"})
_ATHENA_STATEMENT_TYPES = frozenset({"DDL", "DML", "UTILITY"})
_MIN_BYTES_SCANNED_CUTOFF = 10_000_000


def optional_exact_string(value: object) -> bool:
    return value is None or type(value) is str


def optional_non_empty_exact_string(value: object) -> bool:
    return value is None or (type(value) is str and bool(value))


def optional_exact_datetime(value: object) -> bool:
    return value is None or type(value) is datetime


def valid_query_context(value: object) -> bool:
    return type(value) is QueryContext and all(
        type(part) is str
        for part in (
            value.connection_name,
            value.region,
            value.workgroup,
            value.catalog,
            value.database,
        )
    )


def valid_query_execution_ref(value: object) -> bool:
    return type(value) is QueryExecutionRef and all(
        type(part) is str
        for part in (
            value.execution_id,
            value.connection_name,
            value.region,
            value.workgroup,
        )
    )


def valid_query_statistics(value: object) -> bool:
    return (
        type(value) is QueryStatistics
        and type(value.reused_previous_result) is bool
        and all(
            item is None or (type(item) is int and item >= 0)
            for item in (
                value.engine_ms,
                value.queue_ms,
                value.planning_ms,
                value.service_ms,
                value.bytes_scanned,
            )
        )
    )


def valid_athena_query_error(value: object) -> bool:
    return value is None or (
        type(value) is AthenaQueryError
        and (
            value.category is None
            or (type(value.category) is int and value.category in _ATHENA_ERROR_CATEGORIES)
        )
        and (
            value.error_type is None
            or (type(value.error_type) is int and value.error_type in _ATHENA_ERROR_TYPE_RANGE)
        )
        and type(value.retryable) is bool
        and type(value.message) is str
    )


def valid_athena_workgroup_summary(value: object) -> bool:
    return (
        type(value) is AthenaWorkgroupSummary
        and type(value.name) is str
        and type(value.state) is str
        and value.state in _ATHENA_WORKGROUP_STATES
        and optional_exact_string(value.description)
        and optional_exact_datetime(value.created_at)
    )


def valid_athena_workgroup_detail(value: object) -> bool:
    return (
        type(value) is AthenaWorkgroupDetail
        and valid_athena_workgroup_summary(value.summary)
        and optional_exact_string(value.output_location)
        and type(value.enforce_workgroup_configuration) is bool
        and type(value.publish_cloudwatch_metrics) is bool
        and (
            value.bytes_scanned_cutoff is None
            or (
                type(value.bytes_scanned_cutoff) is int
                and value.bytes_scanned_cutoff >= _MIN_BYTES_SCANNED_CUTOFF
            )
        )
        and optional_exact_string(value.engine_version)
        and type(value.managed_query_results_enabled) is bool
    )


def valid_athena_catalog_summary(value: object) -> bool:
    return (
        type(value) is AthenaCatalogSummary
        and type(value.name) is str
        and type(value.catalog_type) is str
        and value.catalog_type in _ATHENA_CATALOG_TYPES
        and optional_exact_string(value.description)
    )


def valid_database_ref(value: object) -> bool:
    return type(value) is DatabaseRef and all(
        type(part) is str
        for part in (
            value.catalog_name,
            value.database_name,
            value.connection_name,
            value.region,
        )
    )


def valid_database_summary(value: object) -> bool:
    return (
        type(value) is DatabaseSummary
        and valid_database_ref(value.ref)
        and optional_exact_string(value.description)
        and optional_exact_string(value.location_uri)
        and optional_exact_datetime(value.created_at)
    )


def valid_table_ref(value: object) -> bool:
    return type(value) is TableRef and all(
        type(part) is str
        for part in (
            value.catalog_name,
            value.database_name,
            value.table_name,
            value.connection_name,
            value.region,
        )
    )


def valid_query_execution_summary(value: object) -> bool:
    return (
        type(value) is QueryExecutionSummary
        and valid_query_execution_ref(value.ref)
        and type(value.state) is QueryState
        and optional_exact_datetime(value.submitted_at)
        and optional_exact_datetime(value.completed_at)
        and (
            value.statement_type is None
            or (
                type(value.statement_type) is str
                and value.statement_type in _ATHENA_STATEMENT_TYPES
            )
        )
    )


def valid_query_execution_detail(value: object) -> bool:
    return (
        type(value) is QueryExecutionDetail
        and valid_query_execution_summary(value.summary)
        and optional_exact_string(value.state_reason)
        and valid_query_context(value.context)
        and valid_query_statistics(value.statistics)
        and optional_exact_string(value.output_location)
        and optional_exact_string(value.engine_version)
        and valid_athena_query_error(value.error)
    )


def valid_result_column(value: object) -> bool:
    return (
        type(value) is ResultColumn
        and type(value.name) is str
        and type(value.type_name) is str
        and type(value.nullable) is str
        and value.nullable in _ATHENA_NULLABILITY
    )


def valid_named_query_summary(value: object) -> bool:
    return (
        type(value) is NamedQuerySummary
        and type(value.query_id) is str
        and type(value.name) is str
        and optional_exact_string(value.description)
        and type(value.database) is str
        and type(value.workgroup) is str
    )


def valid_named_query(value: object) -> bool:
    return (
        type(value) is NamedQuery
        and type(value.query_id) is str
        and type(value.name) is str
        and optional_exact_string(value.description)
        and type(value.database) is str
        and type(value.query_string) is str
        and type(value.workgroup) is str
    )


def valid_prepared_statement_summary(value: object) -> bool:
    return (
        type(value) is PreparedStatementSummary
        and type(value.name) is str
        and optional_exact_datetime(value.last_modified_at)
    )


def valid_prepared_statement(value: object) -> bool:
    return (
        type(value) is PreparedStatement
        and type(value.name) is str
        and type(value.query_statement) is str
        and type(value.workgroup) is str
        and optional_exact_string(value.description)
        and optional_exact_datetime(value.last_modified_at)
    )


__all__ = [
    "optional_exact_datetime",
    "optional_exact_string",
    "optional_non_empty_exact_string",
    "valid_athena_catalog_summary",
    "valid_athena_query_error",
    "valid_athena_workgroup_detail",
    "valid_athena_workgroup_summary",
    "valid_database_ref",
    "valid_database_summary",
    "valid_named_query",
    "valid_named_query_summary",
    "valid_prepared_statement",
    "valid_prepared_statement_summary",
    "valid_query_context",
    "valid_query_execution_detail",
    "valid_query_execution_ref",
    "valid_query_execution_summary",
    "valid_query_statistics",
    "valid_result_column",
    "valid_table_ref",
]
