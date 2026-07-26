from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import UTC, datetime

import pytest

from aws_tui.domain.query import (
    AthenaQueryError,
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

NOW = datetime(2026, 7, 25, 12, 0, tzinfo=UTC)


def test_query_context_includes_connection_region_and_workgroup() -> None:
    context = QueryContext("prod-west", "us-west-2", "analysts", "AwsDataCatalog", "sales")

    assert context.cache_key == (
        "prod-west",
        "us-west-2",
        "analysts",
        "AwsDataCatalog",
        "sales",
    )


def test_query_state_has_all_athena_execution_states() -> None:
    assert {state.value for state in QueryState} == {
        "QUEUED",
        "RUNNING",
        "SUCCEEDED",
        "FAILED",
        "CANCELLED",
    }


def test_query_execution_models_preserve_context_statistics_and_error() -> None:
    ref = QueryExecutionRef("execution-1", "prod-west", "us-west-2", "analysts")
    summary = QueryExecutionSummary(ref, QueryState.FAILED, NOW, NOW, "DML")
    statistics = QueryStatistics(120, 4, 8, 2, 1024, False)
    error = AthenaQueryError(2, 1001, False, "query failed")
    context = QueryContext("prod-west", "us-west-2", "analysts", "AwsDataCatalog", "sales")

    detail = QueryExecutionDetail(
        summary,
        "terminal failure",
        context,
        statistics,
        "s3://results/execution-1.csv",
        "Athena engine version 3",
        error,
    )

    assert detail.summary.ref == ref
    assert detail.context == context
    assert detail.statistics.bytes_scanned == 1024
    assert detail.error == error


def test_result_page_preserves_typed_columns_nulls_and_page_token() -> None:
    page = ResultPage(
        (ResultColumn("event_id", "varchar", "UNKNOWN"),),
        (("evt-1",), (None,)),
        "page-2",
    )

    assert page.columns[0].type_name == "varchar"
    assert page.rows == (("evt-1",), (None,))
    assert page.next_token == "page-2"


def test_saved_query_models_preserve_ui_fields() -> None:
    named = NamedQuery(
        "named-1",
        "Recent events",
        "Last 100 events",
        "sales",
        "SELECT * FROM events LIMIT 100",
        "analysts",
    )
    prepared = PreparedStatement(
        "event_by_id",
        "SELECT * FROM events WHERE event_id = ?",
        "analysts",
        "Lookup by event ID",
        NOW,
    )
    prepared_summary = PreparedStatementSummary("event_by_id", NOW)

    assert named.database == "sales"
    assert named.workgroup == "analysts"
    assert prepared_summary.name == "event_by_id"
    assert prepared_summary.last_modified_at == NOW
    assert prepared.description == "Lookup by event ID"
    assert prepared.last_modified_at == NOW


@pytest.mark.parametrize(
    "record",
    [
        QueryContext("prod-west", "us-west-2", "analysts", "AwsDataCatalog", "sales"),
        QueryExecutionRef("execution-1", "prod-west", "us-west-2", "analysts"),
        QueryStatistics(None, None, None, None, None, False),
        AthenaQueryError(None, None, False, "failed"),
        ResultColumn("value", "varchar", "UNKNOWN"),
        ResultPage((), (), None),
        NamedQuery("id", "name", None, "sales", "SELECT 1", "analysts"),
        PreparedStatementSummary("name", None),
        PreparedStatement("name", "SELECT 1", "analysts", None, None),
    ],
)
def test_query_records_are_immutable(record: object) -> None:
    with pytest.raises(FrozenInstanceError):
        setattr(record, next(iter(record.__slots__)), None)


def test_sensitive_query_values_are_excluded_from_repr() -> None:
    named = NamedQuery(
        "named-1",
        "Sensitive query",
        None,
        "sales",
        "SELECT secret_column FROM customer_data",
        "analysts",
    )
    prepared = PreparedStatement(
        "sensitive_statement",
        "SELECT api_token FROM credentials",
        "analysts",
        "prepared-description-secret-7f4c2a9d",
        None,
    )
    page = ResultPage(
        (ResultColumn("token", "varchar", "UNKNOWN"),),
        (("secret-value",),),
        "result-page-token-secret-7f4c2a9d",
    )

    assert "secret_column" not in repr(named)
    assert "api_token" not in repr(prepared)
    assert "prepared-description-secret-7f4c2a9d" not in repr(prepared)
    assert "secret-value" not in repr(page)
    assert "result-page-token-secret-7f4c2a9d" not in repr(page)


def test_query_execution_detail_sensitive_aws_fields_are_excluded_from_repr() -> None:
    detail = QueryExecutionDetail(
        QueryExecutionSummary(
            QueryExecutionRef("execution-1", "prod-west", "us-west-2", "analysts"),
            QueryState.FAILED,
            NOW,
            NOW,
            "DML",
        ),
        "state-reason-secret-7f4c2a9d",
        QueryContext("prod-west", "us-west-2", "analysts", "AwsDataCatalog", "sales"),
        QueryStatistics(120, 4, 8, 2, 1024, False),
        "s3://sensitive-results-bucket/execution-1.csv",
        "Athena engine version 3",
        AthenaQueryError(2, 1001, False, "aws-error-secret-7f4c2a9d"),
    )

    rendered = repr(detail)

    assert "state-reason-secret-7f4c2a9d" not in rendered
    assert "sensitive-results-bucket" not in rendered
    assert "aws-error-secret-7f4c2a9d" not in rendered
