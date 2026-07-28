from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime

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
)
from aws_tui.vm.athena._domain_validation import (
    valid_athena_catalog_summary,
    valid_athena_workgroup_detail,
    valid_athena_workgroup_summary,
    valid_database_summary,
    valid_named_query,
    valid_named_query_summary,
    valid_prepared_statement,
    valid_prepared_statement_summary,
    valid_query_execution_detail,
    valid_query_execution_summary,
    valid_table_ref,
)

_NOW = datetime(2026, 7, 28, tzinfo=UTC)
_CONTEXT = QueryContext(
    "analytics",
    "us-west-2",
    "analysts",
    "AwsDataCatalog",
    "events",
)
_REF = QueryExecutionRef("query-1", "analytics", "us-west-2", "analysts")
_SUMMARY = QueryExecutionSummary(
    _REF,
    QueryState.SUCCEEDED,
    _NOW,
    _NOW,
    "DML",
)
_DETAIL = QueryExecutionDetail(
    _SUMMARY,
    None,
    _CONTEXT,
    QueryStatistics(1, 2, 3, 4, 5, False),
    "s3://results/query-1.csv",
    "Athena engine version 3",
    None,
)


def _assert_each_field_rejected(
    validator: object,
    valid: object,
    invalid_fields: dict[str, object],
) -> None:
    for field_name, invalid_value in invalid_fields.items():
        assert not validator(  # type: ignore[operator]
            replace(valid, **{field_name: invalid_value})
        ), field_name


def test_workgroup_and_catalog_validators_reject_every_malformed_field() -> None:
    summary = AthenaWorkgroupSummary("analysts", "ENABLED", "team", _NOW)
    _assert_each_field_rejected(
        valid_athena_workgroup_summary,
        summary,
        {
            "name": object(),
            "state": object(),
            "description": object(),
            "created_at": object(),
        },
    )
    detail = AthenaWorkgroupDetail(
        summary,
        "s3://results/",
        True,
        False,
        100,
        "Athena engine version 3",
        True,
    )
    _assert_each_field_rejected(
        valid_athena_workgroup_detail,
        detail,
        {
            "summary": replace(summary, state=object()),
            "output_location": object(),
            "enforce_workgroup_configuration": 1,
            "publish_cloudwatch_metrics": 0,
            "bytes_scanned_cutoff": True,
            "engine_version": object(),
            "managed_query_results_enabled": 1,
        },
    )
    catalog = AthenaCatalogSummary("AwsDataCatalog", "GLUE", "default")
    _assert_each_field_rejected(
        valid_athena_catalog_summary,
        catalog,
        {
            "name": object(),
            "catalog_type": object(),
            "description": object(),
        },
    )


def test_database_and_table_validators_reject_every_malformed_field() -> None:
    ref = DatabaseRef("AwsDataCatalog", "events", "analytics", "us-west-2")
    database = DatabaseSummary(ref, "events", "s3://warehouse/", _NOW)
    _assert_each_field_rejected(
        valid_database_summary,
        database,
        {
            "ref": replace(ref, region=object()),
            "description": object(),
            "location_uri": object(),
            "created_at": object(),
        },
    )
    table = TableRef(
        "AwsDataCatalog",
        "events",
        "orders",
        "analytics",
        "us-west-2",
    )
    _assert_each_field_rejected(
        valid_table_ref,
        table,
        {
            "catalog_name": object(),
            "database_name": object(),
            "table_name": object(),
            "connection_name": object(),
            "region": object(),
        },
    )


def test_history_validators_reject_every_nested_malformed_field() -> None:
    _assert_each_field_rejected(
        valid_query_execution_summary,
        _SUMMARY,
        {
            "ref": replace(_REF, execution_id=object()),
            "state": "SUCCEEDED",
            "submitted_at": object(),
            "completed_at": object(),
            "statement_type": object(),
        },
    )
    _assert_each_field_rejected(
        valid_query_execution_detail,
        _DETAIL,
        {
            "summary": replace(_SUMMARY, state="SUCCEEDED"),
            "state_reason": object(),
            "context": replace(_CONTEXT, catalog=object()),
            "statistics": QueryStatistics(True, 2, 3, 4, 5, False),
            "output_location": object(),
            "engine_version": object(),
            "error": AthenaQueryError(None, None, 1, "message"),
        },
    )


def test_saved_record_validators_reject_every_malformed_field() -> None:
    named_summary = NamedQuerySummary(
        "named-1",
        "Event count",
        "counts",
        "events",
        "analysts",
    )
    _assert_each_field_rejected(
        valid_named_query_summary,
        named_summary,
        {
            "query_id": object(),
            "name": object(),
            "description": object(),
            "database": object(),
            "workgroup": object(),
        },
    )
    named = NamedQuery(
        "named-1",
        "Event count",
        "counts",
        "events",
        "SELECT count(*) FROM events",
        "analysts",
    )
    _assert_each_field_rejected(
        valid_named_query,
        named,
        {
            "query_id": object(),
            "name": object(),
            "description": object(),
            "database": object(),
            "query_string": object(),
            "workgroup": object(),
        },
    )
    prepared_summary = PreparedStatementSummary("prepared-1", _NOW)
    _assert_each_field_rejected(
        valid_prepared_statement_summary,
        prepared_summary,
        {
            "name": object(),
            "last_modified_at": object(),
        },
    )
    prepared = PreparedStatement(
        "prepared-1",
        "SELECT * FROM events WHERE id = ?",
        "analysts",
        "one event",
        _NOW,
    )
    _assert_each_field_rejected(
        valid_prepared_statement,
        prepared,
        {
            "name": object(),
            "query_statement": object(),
            "workgroup": object(),
            "description": object(),
            "last_modified_at": object(),
        },
    )
