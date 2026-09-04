from __future__ import annotations

import asyncio
import gc
import traceback
from dataclasses import FrozenInstanceError
from datetime import UTC, datetime
from pathlib import Path
from typing import cast
from unittest.mock import call

import botocore.exceptions
import pytest

from aws_tui.domain import athena as athena_module
from aws_tui.domain.athena import (
    AthenaCatalogSummary,
    AthenaClient,
    AthenaWorkgroupDetail,
    AthenaWorkgroupSummary,
    QueryContextRejectedError,
    ResultConfigurationRequiredError,
    ResultLocationUnavailableError,
    WorkgroupRejectedError,
    map_athena_error,
)
from aws_tui.domain.data_catalog import (
    DatabaseRef,
    DatabaseSummary,
    TableRef,
    TableSummary,
)
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
    AthenaQueryError,
    NamedQuery,
    PreparedStatement,
    PreparedStatementSummary,
    QueryContext,
    QueryExecutionRef,
    QueryExecutionSummary,
    QueryState,
    QueryStatistics,
    ResultColumn,
)
from aws_tui.infra.aws_session import AwsSession
from aws_tui.infra.connection_resolver import Connection
from aws_tui.infra.crash_dump import CrashDump
from tests.unit.domain._fake_aws_client import (
    FakeAwsClient,
    FakeAwsSession,
)

NOW = datetime(2026, 7, 25, 12, 0, tzinfo=UTC)
LATER = datetime(2026, 7, 25, 12, 1, tzinfo=UTC)
CONNECTION = Connection(
    name="dev",
    kind="aws",
    region="us-east-1",
    source="config",
    profile="dev",
)
CONTEXT = QueryContext(
    "dev",
    "us-east-1",
    "analysts",
    "AwsDataCatalog",
    "analytics",
)
FIRST_RESULT_RESPONSE = {
    "ResultSet": {
        "ResultSetMetadata": {
            "ColumnInfo": [
                {"Name": "event_id", "Type": "varchar", "Nullable": "UNKNOWN"},
                {"Name": "count", "Type": "bigint", "Nullable": "NULLABLE"},
            ]
        },
        "Rows": [
            {"Data": [{"VarCharValue": "event_id"}, {"VarCharValue": "count"}]},
            {"Data": [{"VarCharValue": "a"}, {"VarCharValue": "3"}]},
        ],
    },
    "NextToken": "next",
}


def _athena_client(
    method: str | None = None,
    response: object | None = None,
) -> tuple[AthenaClient, FakeAwsClient, FakeAwsSession]:
    boto = FakeAwsClient()
    if method is not None:
        getattr(boto, method).return_value = response
    session = FakeAwsSession({"athena": boto})
    client = AthenaClient(
        aws_session=cast(AwsSession, session),
        connection=CONNECTION,
    )
    return client, boto, session


def _client_error(
    code: str,
    message: str,
    *,
    operation: str = "GetQueryExecution",
    response_metadata: dict[str, object] | None = None,
) -> botocore.exceptions.ClientError:
    response: dict[str, object] = {
        "Error": {"Code": code, "Message": message},
    }
    if response_metadata is not None:
        response["ResponseMetadata"] = response_metadata
    return botocore.exceptions.ClientError(response, operation)


def _query_execution(
    state: str = "SUCCEEDED",
    *,
    query: str = "SELECT event_id FROM analytics.events",
) -> dict[str, object]:
    return {
        "QueryExecution": {
            "QueryExecutionId": "q-123",
            "Query": query,
            "StatementType": "DML",
            "QueryExecutionContext": {
                "Catalog": "AwsDataCatalog",
                "Database": "analytics",
            },
            "Status": {
                "State": state,
                "StateChangeReason": f"reason for {query}",
                "SubmissionDateTime": NOW,
                "CompletionDateTime": LATER,
                "AthenaError": {
                    "ErrorCategory": 2,
                    "ErrorType": 1001,
                    "Retryable": True,
                    "ErrorMessage": f"failed while running {query}",
                },
            },
            "Statistics": {
                "EngineExecutionTimeInMillis": 120,
                "QueryQueueTimeInMillis": 4,
                "QueryPlanningTimeInMillis": 8,
                "ServiceProcessingTimeInMillis": 2,
                "DataScannedInBytes": 1024,
                "ResultReuseInformation": {"ReusedPreviousResult": True},
            },
            "ResultConfiguration": {
                "OutputLocation": "s3://sensitive-results/q-123.csv",
            },
            "WorkGroup": "analysts",
            "EngineVersion": {
                "SelectedEngineVersion": "AUTO",
                "EffectiveEngineVersion": "Athena engine version 3",
            },
        }
    }


async def test_list_workgroups_maps_one_page_and_opaque_token() -> None:
    client, boto, session = _athena_client(
        "list_work_groups",
        {
            "WorkGroups": [
                {
                    "Name": "analysts",
                    "State": "ENABLED",
                    "Description": "Analyst queries",
                    "CreationTime": NOW,
                },
                {"Name": "adhoc", "State": "DISABLED"},
            ],
            "NextToken": "workgroups-2",
        },
    )

    rows, token = await client.list_workgroups_page()

    assert rows == [
        AthenaWorkgroupSummary("analysts", "ENABLED", "Analyst queries", NOW),
        AthenaWorkgroupSummary("adhoc", "DISABLED", None, None),
    ]
    assert token == "workgroups-2"
    boto.list_work_groups.assert_awaited_once_with(MaxResults=50)
    assert session.requests == [(CONNECTION, "athena")]


async def test_list_workgroups_requests_only_supplied_page_token() -> None:
    client, boto, _ = _athena_client("list_work_groups", {"WorkGroups": []})

    await client.list_workgroups_page(start_token="opaque")

    boto.list_work_groups.assert_awaited_once_with(
        MaxResults=50,
        NextToken="opaque",
    )


async def test_get_workgroup_maps_enforced_result_configuration() -> None:
    client, boto, _ = _athena_client(
        "get_work_group",
        {
            "WorkGroup": {
                "Name": "analysts",
                "State": "ENABLED",
                "Description": "Analyst queries",
                "CreationTime": NOW,
                "Configuration": {
                    "ResultConfiguration": {
                        "OutputLocation": "s3://athena-results/analysts/",
                    },
                    "ManagedQueryResultsConfiguration": {"Enabled": True},
                    "EnforceWorkGroupConfiguration": True,
                    "PublishCloudWatchMetricsEnabled": True,
                    "BytesScannedCutoffPerQuery": 1_000_000,
                    "EngineVersion": {
                        "EffectiveEngineVersion": "Athena engine version 3",
                    },
                },
            }
        },
    )

    detail = await client.get_workgroup("analysts")

    assert detail == AthenaWorkgroupDetail(
        AthenaWorkgroupSummary("analysts", "ENABLED", "Analyst queries", NOW),
        "s3://athena-results/analysts/",
        True,
        True,
        1_000_000,
        "Athena engine version 3",
        True,
    )
    boto.get_work_group.assert_awaited_once_with(WorkGroup="analysts")


async def test_get_workgroup_preserves_missing_output_configuration() -> None:
    client, _, _ = _athena_client(
        "get_work_group",
        {
            "WorkGroup": {
                "Name": "adhoc",
                "State": "ENABLED",
                "Configuration": {},
            }
        },
    )

    detail = await client.get_workgroup("adhoc")

    assert detail.output_location is None
    assert detail.enforce_workgroup_configuration is False
    assert detail.publish_cloudwatch_metrics is False
    assert detail.bytes_scanned_cutoff is None
    assert detail.engine_version is None


async def test_list_catalogs_maps_one_page_without_hidden_detail_calls() -> None:
    client, boto, _ = _athena_client(
        "list_data_catalogs",
        {
            "DataCatalogsSummary": [
                {"CatalogName": "AwsDataCatalog", "Type": "GLUE"},
                {"CatalogName": "federated", "Type": "LAMBDA"},
            ],
            "NextToken": "catalogs-2",
        },
    )

    rows, token = await client.list_catalogs_page(
        workgroup="analysts",
        start_token="catalogs-1",
    )

    assert rows == [
        AthenaCatalogSummary("AwsDataCatalog", "GLUE", None),
        AthenaCatalogSummary("federated", "LAMBDA", None),
    ]
    assert token == "catalogs-2"
    boto.list_data_catalogs.assert_awaited_once_with(
        WorkGroup="analysts",
        MaxResults=50,
        NextToken="catalogs-1",
    )


async def test_list_databases_maps_connection_scoped_refs() -> None:
    client, boto, _ = _athena_client(
        "list_databases",
        {
            "DatabaseList": [
                {"Name": "analytics", "Description": "Curated data"},
                {"Name": "raw"},
            ],
            "NextToken": "databases-2",
        },
    )

    rows, token = await client.list_databases_page(
        "AwsDataCatalog",
        workgroup="analysts",
        start_token="databases-1",
    )

    assert rows == [
        DatabaseSummary(
            DatabaseRef("AwsDataCatalog", "analytics", "dev", "us-east-1"),
            "Curated data",
            None,
            None,
        ),
        DatabaseSummary(
            DatabaseRef("AwsDataCatalog", "raw", "dev", "us-east-1"),
            None,
            None,
            None,
        ),
    ]
    assert token == "databases-2"
    boto.list_databases.assert_awaited_once_with(
        CatalogName="AwsDataCatalog",
        WorkGroup="analysts",
        MaxResults=50,
        NextToken="databases-1",
    )


async def test_list_tables_maps_only_available_athena_metadata() -> None:
    client, boto, _ = _athena_client(
        "list_table_metadata",
        {
            "TableMetadataList": [
                {
                    "Name": "events",
                    "TableType": "EXTERNAL_TABLE",
                    "CreateTime": NOW,
                    "LastAccessTime": LATER,
                    "Parameters": {"comment": "not a summary field"},
                },
                {"Name": "empty"},
            ],
            "NextToken": "tables-2",
        },
    )

    rows, token = await client.list_tables_page(
        "AwsDataCatalog",
        "analytics",
        workgroup="analysts",
        start_token="tables-1",
    )

    assert rows == [
        TableSummary(
            TableRef(
                "AwsDataCatalog",
                "analytics",
                "events",
                "dev",
                "us-east-1",
            ),
            None,
            None,
            "EXTERNAL_TABLE",
            NOW,
            None,
        ),
        TableSummary(
            TableRef(
                "AwsDataCatalog",
                "analytics",
                "empty",
                "dev",
                "us-east-1",
            ),
            None,
            None,
            None,
            None,
            None,
        ),
    ]
    assert token == "tables-2"
    boto.list_table_metadata.assert_awaited_once_with(
        CatalogName="AwsDataCatalog",
        DatabaseName="analytics",
        WorkGroup="analysts",
        MaxResults=50,
        NextToken="tables-1",
    )


@pytest.mark.parametrize(
    ("method", "response"),
    [
        ("list_data_catalogs", {"DataCatalogsSummary": []}),
        ("list_databases", {"DatabaseList": []}),
        ("list_table_metadata", {"TableMetadataList": []}),
    ],
)
async def test_metadata_calls_omit_unsupplied_workgroup(
    method: str,
    response: object,
) -> None:
    client, boto, _ = _athena_client(method, response)

    if method == "list_data_catalogs":
        await client.list_catalogs_page()
        expected = call(MaxResults=50)
    elif method == "list_databases":
        await client.list_databases_page("AwsDataCatalog")
        expected = call(CatalogName="AwsDataCatalog", MaxResults=50)
    else:
        await client.list_tables_page("AwsDataCatalog", "analytics")
        expected = call(
            CatalogName="AwsDataCatalog",
            DatabaseName="analytics",
            MaxResults=50,
        )

    assert getattr(boto, method).await_args == expected


async def test_list_query_executions_scopes_refs_and_fetches_no_details() -> None:
    client, boto, session = _athena_client(
        "list_query_executions",
        {
            "QueryExecutionIds": ["q-2", "q-1"],
            "NextToken": "history-2",
        },
    )

    rows, token = await client.list_query_executions_page(
        "analysts",
        start_token="history-1",
    )

    assert rows == [
        QueryExecutionRef("q-2", "dev", "us-east-1", "analysts"),
        QueryExecutionRef("q-1", "dev", "us-east-1", "analysts"),
    ]
    assert token == "history-2"
    boto.list_query_executions.assert_awaited_once_with(
        WorkGroup="analysts",
        MaxResults=50,
        NextToken="history-1",
    )
    boto.get_query_execution.assert_not_awaited()
    assert session.requests == [(CONNECTION, "athena")]


@pytest.mark.parametrize(
    ("wire_state", "expected"),
    [
        ("QUEUED", QueryState.QUEUED),
        ("RUNNING", QueryState.RUNNING),
        ("SUCCEEDED", QueryState.SUCCEEDED),
        ("FAILED", QueryState.FAILED),
        ("CANCELLED", QueryState.CANCELLED),
    ],
)
async def test_get_query_execution_maps_states_statistics_and_structured_error(
    wire_state: str,
    expected: QueryState,
) -> None:
    client, boto, _ = _athena_client(
        "get_query_execution",
        _query_execution(wire_state),
    )

    detail = await client.get_query_execution("q-123")

    assert detail.summary == QueryExecutionSummary(
        QueryExecutionRef("q-123", "dev", "us-east-1", "analysts"),
        expected,
        NOW,
        LATER,
        "DML",
    )
    assert detail.context == CONTEXT
    assert detail.statistics == QueryStatistics(120, 4, 8, 2, 1024, True)
    assert detail.output_location == "s3://sensitive-results/q-123.csv"
    assert detail.engine_version == "Athena engine version 3"
    assert detail.error == AthenaQueryError(
        2,
        1001,
        True,
        "failed while running [REDACTED]",
    )
    assert detail.state_reason == "reason for [REDACTED]"
    boto.get_query_execution.assert_awaited_once_with(QueryExecutionId="q-123")


async def test_get_query_execution_defaults_optional_statistics() -> None:
    response = _query_execution("RUNNING")
    execution = cast(dict[str, object], response["QueryExecution"])
    execution["Statistics"] = {}
    execution["Status"] = {"State": "RUNNING"}
    execution["QueryExecutionContext"] = {}
    execution["ResultConfiguration"] = {}
    execution["EngineVersion"] = {"SelectedEngineVersion": "AUTO"}
    client, _, _ = _athena_client("get_query_execution", response)

    detail = await client.get_query_execution("q-123")

    assert detail.statistics == QueryStatistics(None, None, None, None, None, False)
    assert detail.context == QueryContext("dev", "us-east-1", "analysts", "", "")
    assert detail.output_location is None
    assert detail.engine_version == "AUTO"
    assert detail.error is None


async def test_get_query_executions_batches_once_and_preserves_requested_order() -> None:
    first = cast(dict[str, object], _query_execution("SUCCEEDED")["QueryExecution"])
    first["QueryExecutionId"] = "q-1"
    second = cast(dict[str, object], _query_execution("RUNNING")["QueryExecution"])
    second["QueryExecutionId"] = "q-2"
    client, boto, _ = _athena_client(
        "batch_get_query_execution",
        {
            "QueryExecutions": [second, first],
            "UnprocessedQueryExecutionIds": [],
        },
    )

    details = await client.get_query_executions(["q-1", "q-2"])

    assert tuple(detail.summary.ref.execution_id for detail in details) == ("q-1", "q-2")
    boto.batch_get_query_execution.assert_awaited_once_with(QueryExecutionIds=["q-1", "q-2"])
    boto.get_query_execution.assert_not_awaited()


async def test_get_query_executions_rejects_unprocessed_ids() -> None:
    client, _, _ = _athena_client(
        "batch_get_query_execution",
        {
            "QueryExecutions": [],
            "UnprocessedQueryExecutionIds": [
                {
                    "QueryExecutionId": "q-sensitive",
                    "ErrorCode": "INTERNAL_ERROR",
                    "ErrorMessage": "could not process q-sensitive",
                }
            ],
        },
    )

    with pytest.raises(ProviderError) as raised:
        await client.get_query_executions(["q-sensitive"])

    assert "q-sensitive" not in str(raised.value)


@pytest.mark.parametrize(
    "response_ids",
    [("q-1",), ("q-1", "q-1")],
)
async def test_get_query_executions_rejects_incomplete_or_duplicate_results(
    response_ids: tuple[str, ...],
) -> None:
    rows: list[dict[str, object]] = []
    for execution_id in response_ids:
        row = cast(dict[str, object], _query_execution()["QueryExecution"])
        row["QueryExecutionId"] = execution_id
        rows.append(row)
    client, _, _ = _athena_client(
        "batch_get_query_execution",
        {"QueryExecutions": rows, "UnprocessedQueryExecutionIds": []},
    )

    with pytest.raises(ValidationError, match="malformed Athena response"):
        await client.get_query_executions(["q-1", "q-2"])


async def test_get_query_runtime_statistics_maps_timeline_and_input_bytes() -> None:
    client, boto, _ = _athena_client(
        "get_query_runtime_statistics",
        {
            "QueryRuntimeStatistics": {
                "Timeline": {
                    "EngineExecutionTimeInMillis": 101,
                    "QueryQueueTimeInMillis": 7,
                    "QueryPlanningTimeInMillis": 9,
                    "ServiceProcessingTimeInMillis": 3,
                },
                "Rows": {
                    "InputBytes": 2048,
                    "InputRows": 10,
                    "OutputBytes": 20,
                    "OutputRows": 2,
                },
            }
        },
    )

    statistics = await client.get_query_runtime_statistics("q-123")

    assert statistics == QueryStatistics(101, 7, 9, 3, 2048, False)
    boto.get_query_runtime_statistics.assert_awaited_once_with(
        QueryExecutionId="q-123",
    )


async def test_start_query_validates_before_sdk_and_sends_exact_context_and_token() -> None:
    client, boto, session = _athena_client(
        "start_query_execution",
        {"QueryExecutionId": "q-123"},
    )

    ref = await client.start_query(
        "  SELECT 1  ",
        CONTEXT,
        request_token="token-123",
    )

    assert ref == QueryExecutionRef("q-123", "dev", "us-east-1", "analysts")
    boto.start_query_execution.assert_awaited_once_with(
        QueryString="SELECT 1",
        ClientRequestToken="token-123",
        QueryExecutionContext={
            "Catalog": "AwsDataCatalog",
            "Database": "analytics",
        },
        WorkGroup="analysts",
    )
    assert session.requests == [(CONNECTION, "athena")]


async def test_start_query_rejects_mutation_before_opening_sdk_client() -> None:
    client, boto, session = _athena_client()

    with pytest.raises(ValidationError, match="not read-only"):
        await client.start_query(
            "DELETE FROM analytics.events",
            CONTEXT,
            request_token="token-123",
        )

    boto.start_query_execution.assert_not_awaited()
    assert session.requests == []


async def test_start_query_rejects_cross_connection_context_before_sdk_call() -> None:
    client, boto, session = _athena_client()
    context = QueryContext(
        "prod",
        "us-west-2",
        "analysts",
        "AwsDataCatalog",
        "analytics",
    )

    with pytest.raises(ValidationError, match="query context"):
        await client.start_query("SELECT 1", context, request_token="token-123")

    boto.start_query_execution.assert_not_awaited()
    assert session.requests == []


async def test_start_query_can_supply_explicit_output_location() -> None:
    client, boto, _ = _athena_client(
        "start_query_execution",
        {"QueryExecutionId": "q-output"},
    )

    await client.start_query(
        "SELECT 1",
        CONTEXT,
        request_token="token-output",
        output_location="s3://caller-results/query/",
    )

    boto.start_query_execution.assert_awaited_once_with(
        QueryString="SELECT 1",
        ClientRequestToken="token-output",
        QueryExecutionContext={
            "Catalog": "AwsDataCatalog",
            "Database": "analytics",
        },
        WorkGroup="analysts",
        ResultConfiguration={
            "OutputLocation": "s3://caller-results/query/",
        },
    )


async def test_missing_result_configuration_maps_to_typed_error() -> None:
    sql = "SELECT sensitive_customer_secret FROM analytics.events"
    client, boto, _ = _athena_client()
    boto.start_query_execution.side_effect = _client_error(
        "InvalidRequestException",
        (
            "No output location provided. An output location is required either "
            f"through the WorkGroup result configuration or as API input for {sql}"
        ),
        operation="StartQueryExecution",
    )

    with pytest.raises(
        ResultConfigurationRequiredError,
        match="Athena result configuration is required",
    ) as raised:
        await client.start_query(sql, CONTEXT, request_token="token-123")

    assert sql not in str(raised.value)
    assert raised.value.__cause__ is None


@pytest.mark.parametrize(
    ("message", "expected_type", "expected_message", "secret"),
    [
        (
            "Unable to verify/create output bucket s3://RESULT_SECRET_7F4C2A9D/",
            ResultLocationUnavailableError,
            "Athena cannot access the workgroup result location",
            "RESULT_SECRET_7F4C2A9D",
        ),
        (
            "Catalog CATALOG_SECRET_7F4C2A9D does not exist",
            QueryContextRejectedError,
            "Athena rejected the selected query context",
            "CATALOG_SECRET_7F4C2A9D",
        ),
        (
            "WorkGroup WORKGROUP_SECRET_7F4C2A9D is disabled",
            WorkgroupRejectedError,
            "Athena rejected the selected workgroup",
            "WORKGROUP_SECRET_7F4C2A9D",
        ),
    ],
)
async def test_start_query_classifies_known_invalid_requests_without_raw_values(
    message: str,
    expected_type: type[ValidationError],
    expected_message: str,
    secret: str,
) -> None:
    client, boto, _ = _athena_client()
    boto.start_query_execution.side_effect = _client_error(
        "InvalidRequestException",
        message,
        operation="StartQueryExecution",
    )

    with pytest.raises(expected_type, match=expected_message) as raised:
        await client.start_query("SELECT 1", CONTEXT, request_token="token-123")

    assert str(raised.value) == expected_message
    assert secret not in str(raised.value)
    assert raised.value.__cause__ is None


@pytest.mark.parametrize(
    ("error", "output_location"),
    [
        (
            _client_error(
                "InvalidRequestException",
                "The S3 output location is malformed: LOCATION_SECRET_7F4C2A9D",
                operation="StartQueryExecution",
            ),
            "s3://LOCATION_SECRET_7F4C2A9D",
        ),
        (
            botocore.exceptions.ParamValidationError(
                report="invalid output location LOCATION_SECRET_7F4C2A9D"
            ),
            "LOCATION_SECRET_7F4C2A9D",
        ),
    ],
)
async def test_malformed_supplied_result_location_is_ordinary_validation(
    error: Exception,
    output_location: str,
) -> None:
    client, boto, _ = _athena_client()
    boto.start_query_execution.side_effect = error

    with pytest.raises(ValidationError) as raised:
        await client.start_query(
            "SELECT 1",
            CONTEXT,
            request_token="token-123",
            output_location=output_location,
        )

    assert type(raised.value) is ValidationError
    assert "LOCATION_SECRET_7F4C2A9D" not in str(raised.value)
    assert raised.value.__cause__ is None


async def test_non_start_missing_output_message_is_ordinary_validation() -> None:
    client, boto, _ = _athena_client()
    boto.list_data_catalogs.side_effect = _client_error(
        "InvalidRequestException",
        "No output location provided",
        operation="ListDataCatalogs",
    )

    with pytest.raises(ValidationError) as raised:
        await client.list_catalogs_page()

    assert type(raised.value) is ValidationError


async def test_stop_query_allows_only_app_started_active_execution() -> None:
    client, boto, _ = _athena_client(
        "start_query_execution",
        {"QueryExecutionId": "q-started"},
    )
    boto.stop_query_execution.return_value = {}
    await client.start_query("SELECT 1", CONTEXT, request_token="token-123")

    await client.stop_query("q-started")

    boto.stop_query_execution.assert_awaited_once_with(
        QueryExecutionId="q-started",
    )


async def test_concurrent_stop_awaits_share_one_dispatch() -> None:
    client, boto, _ = _athena_client(
        "start_query_execution",
        {"QueryExecutionId": "q-started"},
    )
    await client.start_query("SELECT 1", CONTEXT, request_token="token-123")
    dispatched = asyncio.Event()
    release = asyncio.Event()

    async def stop_once(**_: object) -> dict[str, object]:
        dispatched.set()
        await release.wait()
        return {}

    boto.stop_query_execution.side_effect = stop_once
    first = asyncio.create_task(client.stop_query("q-started"))
    await dispatched.wait()
    second = asyncio.create_task(client.stop_query("q-started"))
    await asyncio.sleep(0)
    dispatch_count = boto.stop_query_execution.await_count
    release.set()
    results = await asyncio.gather(first, second, return_exceptions=True)

    assert dispatch_count == 1
    assert results == [None, None]


async def test_cancelled_stop_waiter_retrieves_late_dispatch_failure() -> None:
    client, boto, _ = _athena_client(
        "start_query_execution",
        {"QueryExecutionId": "q-started"},
    )
    await client.start_query("SELECT 1", CONTEXT, request_token="token-123")
    dispatched = asyncio.Event()
    release = asyncio.Event()

    async def fail_after_cancellation(**_: object) -> None:
        dispatched.set()
        await release.wait()
        raise _client_error("TooManyRequestsException", "retry later")

    boto.stop_query_execution.side_effect = fail_after_cancellation
    loop = asyncio.get_running_loop()
    previous_handler = loop.get_exception_handler()
    unhandled: list[dict[str, object]] = []
    loop.set_exception_handler(lambda _loop, context: unhandled.append(context))
    try:
        waiter = asyncio.create_task(client.stop_query("q-started"))
        await dispatched.wait()
        waiter.cancel()
        with pytest.raises(asyncio.CancelledError):
            await waiter
        release.set()
        for _ in range(10):
            await asyncio.sleep(0)
            if not client._stop_tasks:
                break
        gc.collect()
        await asyncio.sleep(0)
    finally:
        loop.set_exception_handler(previous_handler)

    assert not [
        context
        for context in unhandled
        if context.get("message") == "Task exception was never retrieved"
    ]


async def test_failed_stop_restores_authority_for_retry() -> None:
    client, boto, _ = _athena_client(
        "start_query_execution",
        {"QueryExecutionId": "q-started"},
    )
    await client.start_query("SELECT 1", CONTEXT, request_token="token-123")
    boto.stop_query_execution.side_effect = [
        _client_error("TooManyRequestsException", "retry later"),
        {},
    ]

    with pytest.raises(ThrottledError, match="retry later"):
        await client.stop_query("q-started")
    await client.stop_query("q-started")

    assert boto.stop_query_execution.await_args_list == [
        call(QueryExecutionId="q-started"),
        call(QueryExecutionId="q-started"),
    ]


async def test_stop_query_rejects_history_execution_without_sdk_call() -> None:
    client, boto, session = _athena_client()

    with pytest.raises(ValidationError, match="not an active app-started query"):
        await client.stop_query("history-query")

    boto.stop_query_execution.assert_not_awaited()
    assert session.requests == []


async def test_terminal_observation_revokes_stop_authority() -> None:
    client, boto, _ = _athena_client(
        "start_query_execution",
        {"QueryExecutionId": "q-started"},
    )
    await client.start_query("SELECT 1", CONTEXT, request_token="token-123")
    response = _query_execution("SUCCEEDED")
    execution = cast(dict[str, object], response["QueryExecution"])
    execution["QueryExecutionId"] = "q-started"
    boto.get_query_execution.return_value = response
    await client.get_query_execution("q-started")

    with pytest.raises(ValidationError, match="not an active app-started query"):
        await client.stop_query("q-started")

    boto.stop_query_execution.assert_not_awaited()


@pytest.mark.asyncio
async def test_foreign_terminal_history_does_not_enter_app_ownership_ledgers() -> None:
    client, boto, _ = _athena_client()

    for index in range(25):
        execution_id = f"history-{index}"
        response = _query_execution("SUCCEEDED")
        execution = cast(dict[str, object], response["QueryExecution"])
        execution["QueryExecutionId"] = execution_id
        boto.get_query_execution.return_value = response

        await client.get_query_execution(execution_id)

    assert client._app_started_active_queries == set()
    assert client._app_started_query_ids_by_token == {}
    assert client._retired_app_started_queries == set()
    assert client._stop_tasks == {}


def test_retired_app_started_query_ledger_is_bounded() -> None:
    client, _boto, _session = _athena_client()
    limit = athena_module._MAX_RETIRED_APP_STARTED_QUERIES

    for index in range(limit + 25):
        execution_id = f"owned-{index}"
        client._app_started_active_queries.add(execution_id)
        client._retire_app_started_query(execution_id)

    assert len(client._retired_app_started_queries) == limit
    assert "owned-0" not in client._retired_app_started_queries
    assert f"owned-{limit + 24}" in client._retired_app_started_queries


async def test_terminal_observation_wins_over_failed_stop_race() -> None:
    client, boto, _ = _athena_client(
        "start_query_execution",
        {"QueryExecutionId": "q-started"},
    )
    await client.start_query("SELECT 1", CONTEXT, request_token="token-123")
    dispatched = asyncio.Event()
    release = asyncio.Event()

    async def fail_stop(**_: object) -> None:
        dispatched.set()
        await release.wait()
        raise _client_error("TooManyRequestsException", "retry later")

    boto.stop_query_execution.side_effect = fail_stop
    stopping = asyncio.create_task(client.stop_query("q-started"))
    await dispatched.wait()
    response = _query_execution("SUCCEEDED")
    execution = cast(dict[str, object], response["QueryExecution"])
    execution["QueryExecutionId"] = "q-started"
    boto.get_query_execution.return_value = response
    await client.get_query_execution("q-started")
    release.set()

    with pytest.raises(ThrottledError, match="retry later"):
        await stopping
    with pytest.raises(ValidationError, match="not an active app-started query"):
        await client.stop_query("q-started")

    boto.stop_query_execution.assert_awaited_once_with(
        QueryExecutionId="q-started",
    )


async def test_terminal_execution_cannot_be_reauthorized_by_token_or_id_reuse() -> None:
    client, boto, _ = _athena_client()
    boto.start_query_execution.side_effect = [
        {"QueryExecutionId": "q-old"},
        {"QueryExecutionId": "q-old"},
        {"QueryExecutionId": "q-old"},
        {"QueryExecutionId": "q-new"},
    ]
    await client.start_query("SELECT 1", CONTEXT, request_token="token-old")
    response = _query_execution("SUCCEEDED")
    execution = cast(dict[str, object], response["QueryExecution"])
    execution["QueryExecutionId"] = "q-old"
    boto.get_query_execution.return_value = response
    await client.get_query_execution("q-old")

    await client.start_query("SELECT 1", CONTEXT, request_token="token-old")
    await client.start_query("SELECT 1", CONTEXT, request_token="token-other")
    await client.start_query("SELECT 1", CONTEXT, request_token="token-new")

    assert client._app_started_query_ids_by_token == {"token-new": "q-new"}
    with pytest.raises(ValidationError, match="not an active app-started query"):
        await client.stop_query("q-old")
    await client.stop_query("q-new")

    boto.stop_query_execution.assert_awaited_once_with(
        QueryExecutionId="q-new",
    )


async def test_results_page_removes_header_only_on_first_page() -> None:
    client, boto, _ = _athena_client(
        "get_query_results",
        FIRST_RESULT_RESPONSE,
    )

    page = await client.get_results_page("q-123")

    assert page.columns == (
        ResultColumn("event_id", "varchar", "UNKNOWN"),
        ResultColumn("count", "bigint", "NULLABLE"),
    )
    assert page.rows == (("a", "3"),)
    assert page.next_token == "next"
    boto.get_query_results.assert_awaited_once_with(
        QueryExecutionId="q-123",
        MaxResults=1000,
    )


async def test_results_page_preserves_header_on_later_page_and_null_vs_empty() -> None:
    response = {
        "ResultSet": {
            "ResultSetMetadata": {
                "ColumnInfo": [
                    {"Name": "event_id", "Type": "varchar"},
                    {"Name": "value", "Type": "varchar"},
                ]
            },
            "Rows": [
                {"Data": [{"VarCharValue": "event_id"}, {"VarCharValue": "value"}]},
                {"Data": [{}, {"VarCharValue": ""}]},
                {"Data": [{"VarCharValue": "7"}, {"VarCharValue": "false"}]},
            ],
        }
    }
    client, boto, _ = _athena_client("get_query_results", response)

    page = await client.get_results_page("q-123", start_token="results-2")

    assert page.rows == (
        ("event_id", "value"),
        (None, ""),
        ("7", "false"),
    )
    assert [column.nullable for column in page.columns] == ["UNKNOWN", "UNKNOWN"]
    boto.get_query_results.assert_awaited_once_with(
        QueryExecutionId="q-123",
        MaxResults=1000,
        NextToken="results-2",
    )


async def test_list_named_queries_returns_only_one_id_page() -> None:
    client, boto, _ = _athena_client(
        "list_named_queries",
        {
            "NamedQueryIds": ["named-2", "named-1"],
            "NextToken": "named-2-page",
        },
    )

    ids, token = await client.list_named_queries_page(
        "analysts",
        start_token="named-1-page",
    )

    assert ids == ["named-2", "named-1"]
    assert token == "named-2-page"
    boto.list_named_queries.assert_awaited_once_with(
        WorkGroup="analysts",
        MaxResults=50,
        NextToken="named-1-page",
    )
    boto.batch_get_named_query.assert_not_awaited()


async def test_get_named_queries_batches_at_50_and_preserves_service_order() -> None:
    client, boto, session = _athena_client()
    ids = [f"named-{index}" for index in range(51)]
    boto.batch_get_named_query.side_effect = [
        {
            "NamedQueries": [
                {
                    "NamedQueryId": "named-1",
                    "Name": "Second returned",
                    "Description": "Description",
                    "Database": "analytics",
                    "QueryString": "SELECT 2",
                    "WorkGroup": "analysts",
                },
                {
                    "NamedQueryId": "named-0",
                    "Name": "First requested",
                    "Database": "analytics",
                    "QueryString": "SELECT 1",
                    "WorkGroup": "analysts",
                },
            ]
        },
        {
            "NamedQueries": [
                {
                    "NamedQueryId": "named-50",
                    "Name": "Last",
                    "Database": "analytics",
                    "QueryString": "SELECT 50",
                    "WorkGroup": "analysts",
                }
            ]
        },
    ]

    rows = await client.get_named_queries(ids)

    assert rows == (
        NamedQuery(
            "named-1",
            "Second returned",
            "Description",
            "analytics",
            "SELECT 2",
            "analysts",
        ),
        NamedQuery(
            "named-0",
            "First requested",
            None,
            "analytics",
            "SELECT 1",
            "analysts",
        ),
        NamedQuery(
            "named-50",
            "Last",
            None,
            "analytics",
            "SELECT 50",
            "analysts",
        ),
    )
    assert boto.batch_get_named_query.await_args_list == [
        call(NamedQueryIds=ids[:50]),
        call(NamedQueryIds=ids[50:]),
    ]
    assert session.requests == [(CONNECTION, "athena")]


async def test_get_named_queries_empty_input_makes_no_sdk_call() -> None:
    client, boto, session = _athena_client()

    assert await client.get_named_queries([]) == ()

    boto.batch_get_named_query.assert_not_awaited()
    assert session.requests == []


@pytest.mark.parametrize(
    ("failed_batch", "error_code", "expected_type"),
    [
        (0, "TooManyRequestsException", ThrottledError),
        (1, "InternalServerException", ProviderError),
    ],
)
async def test_get_named_queries_rejects_unprocessed_ids_per_batch_without_leak(
    failed_batch: int,
    error_code: str,
    expected_type: type[ProviderError],
) -> None:
    client, boto, _ = _athena_client()
    ids = [f"sensitive-named-{index}-7f4c2a9d" for index in range(51)]
    successful_response = {
        "NamedQueries": [
            {
                "NamedQueryId": ids[0],
                "Name": "Loaded",
                "Database": "analytics",
                "QueryString": "SELECT 1",
                "WorkGroup": "analysts",
            }
        ]
    }
    failed_id = ids[0] if failed_batch == 0 else ids[50]
    failed_response = {
        "NamedQueries": [
            {
                "NamedQueryId": failed_id,
                "Name": "Partial result must not escape",
                "Database": "analytics",
                "QueryString": "SELECT 1",
                "WorkGroup": "analysts",
            }
        ],
        "UnprocessedNamedQueryIds": [
            {
                "NamedQueryId": failed_id,
                "ErrorCode": error_code,
                "ErrorMessage": f"could not process {failed_id}",
            }
        ],
    }
    boto.batch_get_named_query.side_effect = (
        [failed_response] if failed_batch == 0 else [successful_response, failed_response]
    )

    with pytest.raises(expected_type) as raised:
        await client.get_named_queries(ids)

    rendered = "".join(
        traceback.format_exception(
            type(raised.value),
            raised.value,
            raised.value.__traceback__,
        )
    )
    assert "sensitive-named-" not in rendered
    assert raised.value.__cause__ is None
    assert boto.batch_get_named_query.await_count == failed_batch + 1


async def test_list_prepared_statements_maps_one_page() -> None:
    client, boto, _ = _athena_client(
        "list_prepared_statements",
        {
            "PreparedStatements": [
                {
                    "StatementName": "event_by_id",
                    "LastModifiedTime": NOW,
                },
                {
                    "StatementName": "recent",
                },
            ],
            "NextToken": "prepared-2",
        },
    )

    rows, token = await client.list_prepared_statements_page(
        "analysts",
        start_token="prepared-1",
    )

    assert rows == [
        PreparedStatementSummary("event_by_id", NOW),
        PreparedStatementSummary("recent", None),
    ]
    assert token == "prepared-2"
    boto.list_prepared_statements.assert_awaited_once_with(
        WorkGroup="analysts",
        MaxResults=50,
        NextToken="prepared-1",
    )


async def test_get_prepared_statement_maps_detail() -> None:
    client, boto, _ = _athena_client(
        "get_prepared_statement",
        {
            "PreparedStatement": {
                "StatementName": "event_by_id",
                "QueryStatement": "SELECT * FROM events WHERE event_id = ?",
                "WorkGroupName": "analysts",
                "Description": "Lookup",
                "LastModifiedTime": NOW,
            }
        },
    )

    detail = await client.get_prepared_statement("event_by_id", "analysts")

    assert detail == PreparedStatement(
        "event_by_id",
        "SELECT * FROM events WHERE event_id = ?",
        "analysts",
        "Lookup",
        NOW,
    )
    boto.get_prepared_statement.assert_awaited_once_with(
        StatementName="event_by_id",
        WorkGroup="analysts",
    )


@pytest.mark.parametrize(
    ("error", "expected_type"),
    [
        (botocore.exceptions.NoCredentialsError(), AuthRequiredError),
        (
            botocore.exceptions.PartialCredentialsError(
                provider="env",
                cred_var="AWS_SECRET_ACCESS_KEY",
            ),
            AuthRequiredError,
        ),
        (botocore.exceptions.UnauthorizedSSOTokenError(), AuthRequiredError),
        (
            botocore.exceptions.SSOTokenLoadError(error_msg="bad token cache"),
            AuthRequiredError,
        ),
        (
            botocore.exceptions.EndpointConnectionError(
                endpoint_url="https://athena.example.test",
            ),
            ProviderUnreachableError,
        ),
        (
            botocore.exceptions.ReadTimeoutError(
                endpoint_url="https://athena.example.test",
            ),
            ProviderUnreachableError,
        ),
        (
            _client_error("AccessDeniedException", "denied"),
            PermissionDeniedError,
        ),
        (
            _client_error("TooManyRequestsException", "slow down"),
            ThrottledError,
        ),
        (
            _client_error("ResourceNotFoundException", "missing"),
            NotFoundError,
        ),
        (
            _client_error("InvalidRequestException", "bad input"),
            ValidationError,
        ),
        (
            _client_error("InternalServerException", "service failed"),
            ProviderError,
        ),
        (
            botocore.exceptions.ParamValidationError(report="invalid parameter"),
            ValidationError,
        ),
        (KeyError("Name"), ValidationError),
        (TypeError("bad shape"), ValidationError),
        (ValueError("unknown state"), ValidationError),
    ],
)
def test_map_athena_error_uses_canonical_provider_errors(
    error: BaseException,
    expected_type: type[ProviderError],
) -> None:
    mapped = map_athena_error(error)

    assert isinstance(mapped, expected_type)


async def test_access_denied_maps_without_exposing_query_or_raw_response() -> None:
    sql = "SELECT private_customer_value FROM restricted_table"
    raw_secret = "RAW_RESPONSE_SECRET_7F4C2A9D"
    request_token = "REQUEST_TOKEN_SECRET_7F4C2A9D"
    output_location = "s3://private-results/SECRET_PREFIX_7F4C2A9D/"
    client, boto, _ = _athena_client()
    boto.start_query_execution.side_effect = _client_error(
        "AccessDeniedException",
        (
            f"denied while running {sql}; request id {request_token}; "
            f"output {output_location}; token=service-secret"
        ),
        operation="StartQueryExecution",
        response_metadata={"RawSecret": raw_secret},
    )

    with pytest.raises(PermissionDeniedError, match="denied") as raised:
        await client.start_query(
            sql,
            CONTEXT,
            request_token=request_token,
            output_location=output_location,
        )

    rendered = "".join(
        traceback.format_exception(
            type(raised.value),
            raised.value,
            raised.value.__traceback__,
        )
    )
    assert sql not in rendered
    assert raw_secret not in rendered
    assert request_token not in rendered
    assert "SECRET_PREFIX_7F4C2A9D" not in rendered
    assert "service-secret" not in rendered
    assert "[REDACTED]" in rendered
    assert raised.value.__cause__ is None


async def test_unknown_start_exception_is_stable_and_crash_safe(tmp_path: Path) -> None:
    sql = "SELECT private_fixture_value FROM restricted_table"
    request_token = "REQUEST_TOKEN_SECRET_7F4C2A9D"
    output_location = "s3://private-results/LOCATION_SECRET_7F4C2A9D/"
    client, boto, _ = _athena_client()
    boto.start_query_execution.side_effect = RuntimeError(
        f"unknown failure for {sql} {request_token} {output_location}"
    )

    with pytest.raises(ProviderError, match="Athena request failed") as raised:
        await client.start_query(
            sql,
            CONTEXT,
            request_token=request_token,
            output_location=output_location,
        )

    rendered = "".join(
        traceback.format_exception(
            type(raised.value),
            raised.value,
            raised.value.__traceback__,
        )
    )
    crash_path = CrashDump(base_dir=tmp_path / "crash").write(exc=raised.value)
    crash_text = crash_path.read_text(encoding="utf-8")
    for secret in (
        "private_fixture_value",
        request_token,
        "LOCATION_SECRET_7F4C2A9D",
    ):
        assert secret not in rendered
        assert secret not in crash_text
    assert raised.value.__cause__ is None
    assert type(raised.value) is ProviderError


async def _invoke_sensitive_operation(
    client: AthenaClient,
    boto: FakeAwsClient,
    method: str,
    sensitive: str,
) -> None:
    if method == "list_work_groups":
        await client.list_workgroups_page(start_token=sensitive)
    elif method == "get_work_group":
        await client.get_workgroup(sensitive)
    elif method == "list_data_catalogs":
        await client.list_catalogs_page(workgroup=sensitive, start_token=sensitive)
    elif method == "list_databases":
        await client.list_databases_page(
            sensitive,
            workgroup=sensitive,
            start_token=sensitive,
        )
    elif method == "list_table_metadata":
        await client.list_tables_page(
            sensitive,
            sensitive,
            workgroup=sensitive,
            start_token=sensitive,
        )
    elif method == "list_query_executions":
        await client.list_query_executions_page(sensitive, start_token=sensitive)
    elif method == "get_query_execution":
        await client.get_query_execution(sensitive)
    elif method == "batch_get_query_execution":
        await client.get_query_executions([sensitive])
    elif method == "get_query_runtime_statistics":
        await client.get_query_runtime_statistics(sensitive)
    elif method == "stop_query_execution":
        boto.start_query_execution.return_value = {"QueryExecutionId": sensitive}
        await client.start_query("SELECT 1", CONTEXT, request_token="setup-token")
        await client.stop_query(sensitive)
    elif method == "get_query_results":
        await client.get_results_page(sensitive, start_token=sensitive)
    elif method == "list_named_queries":
        await client.list_named_queries_page(sensitive, start_token=sensitive)
    elif method == "batch_get_named_query":
        await client.get_named_queries([sensitive])
    elif method == "list_prepared_statements":
        await client.list_prepared_statements_page(sensitive, start_token=sensitive)
    else:
        await client.get_prepared_statement(sensitive, sensitive)


@pytest.mark.parametrize(
    "method",
    [
        "list_work_groups",
        "get_work_group",
        "list_data_catalogs",
        "list_databases",
        "list_table_metadata",
        "list_query_executions",
        "get_query_execution",
        "batch_get_query_execution",
        "get_query_runtime_statistics",
        "stop_query_execution",
        "get_query_results",
        "list_named_queries",
        "batch_get_named_query",
        "list_prepared_statements",
        "get_prepared_statement",
    ],
)
async def test_operation_identifiers_and_tokens_are_scrubbed_from_tracebacks(
    method: str,
) -> None:
    sensitive = "OPERATION_SECRET_7F4C2A9D"
    raw_secret = "RAW_RESPONSE_SECRET_7F4C2A9D"
    client, boto, _ = _athena_client()
    getattr(boto, method).side_effect = _client_error(
        "InternalServerException",
        f"failed for {sensitive}",
        operation=method,
        response_metadata={"RawSecret": raw_secret},
    )

    with pytest.raises(ProviderError) as raised:
        await _invoke_sensitive_operation(client, boto, method, sensitive)

    rendered = "".join(
        traceback.format_exception(
            type(raised.value),
            raised.value,
            raised.value.__traceback__,
        )
    )
    assert sensitive not in rendered
    assert raw_secret not in rendered
    assert raised.value.__cause__ is None


async def _invoke_malformed_case(client: AthenaClient, method: str) -> None:
    if method == "list_work_groups":
        await client.list_workgroups_page()
    elif method == "get_work_group":
        await client.get_workgroup("analysts")
    elif method == "list_data_catalogs":
        await client.list_catalogs_page()
    elif method == "list_databases":
        await client.list_databases_page("AwsDataCatalog")
    elif method == "list_table_metadata":
        await client.list_tables_page("AwsDataCatalog", "analytics")
    elif method == "list_query_executions":
        await client.list_query_executions_page("analysts")
    elif method == "get_query_execution":
        await client.get_query_execution("q-123")
    elif method == "batch_get_query_execution":
        await client.get_query_executions(["q-123"])
    elif method == "get_query_runtime_statistics":
        await client.get_query_runtime_statistics("q-123")
    elif method == "get_query_results":
        await client.get_results_page("q-123")
    elif method == "list_named_queries":
        await client.list_named_queries_page("analysts")
    elif method == "batch_get_named_query":
        await client.get_named_queries(["named-1"])
    elif method == "get_prepared_statement":
        await client.get_prepared_statement("prepared-1", "analysts")
    else:
        await client.list_prepared_statements_page("analysts")


@pytest.mark.parametrize(
    ("method", "response"),
    [
        ("list_work_groups", {"WorkGroups": [{"State": "ENABLED"}]}),
        ("get_work_group", {}),
        ("list_data_catalogs", {"DataCatalogsSummary": [{"Type": "GLUE"}]}),
        ("list_databases", {"DatabaseList": [{"Description": "missing name"}]}),
        ("list_table_metadata", {"TableMetadataList": [{"TableType": "VIEW"}]}),
        ("list_query_executions", {"QueryExecutionIds": [7]}),
        ("get_query_execution", {"QueryExecution": {"QueryExecutionId": "q-123"}}),
        (
            "batch_get_query_execution",
            {"QueryExecutions": [{"QueryExecutionId": "q-123"}]},
        ),
        (
            "get_query_runtime_statistics",
            {"QueryRuntimeStatistics": {"Timeline": []}},
        ),
        (
            "get_query_results",
            {"ResultSet": {"ResultSetMetadata": {"ColumnInfo": "not-a-list"}}},
        ),
        ("list_named_queries", {"NamedQueryIds": ["named-1", None]}),
        ("batch_get_named_query", {"NamedQueries": [{"Name": "missing fields"}]}),
        (
            "get_prepared_statement",
            {"PreparedStatement": {"StatementName": "missing fields"}},
        ),
        (
            "list_prepared_statements",
            {"PreparedStatements": [{"LastModifiedTime": NOW}]},
        ),
    ],
)
async def test_malformed_responses_become_stable_validation_errors(
    method: str,
    response: object,
) -> None:
    client, boto, _ = _athena_client(method, response)

    with pytest.raises(ValidationError, match="malformed Athena response") as raised:
        await _invoke_malformed_case(client, method)

    assert raised.value.__cause__ is None
    assert repr(response) not in str(raised.value)
    assert getattr(boto, method).await_count == 1


def test_athena_owned_records_are_immutable_and_hide_result_location() -> None:
    summary = AthenaWorkgroupSummary("analysts", "ENABLED", None, NOW)
    detail = AthenaWorkgroupDetail(
        summary,
        "s3://private-results/secret-prefix/",
        True,
        False,
        None,
        "Athena engine version 3",
    )
    catalog = AthenaCatalogSummary("AwsDataCatalog", "GLUE", None)

    with pytest.raises(FrozenInstanceError):
        summary.name = "other"
    with pytest.raises(FrozenInstanceError):
        catalog.name = "other"
    assert "private-results" not in repr(detail)


def test_athena_module_exports_public_client_records_and_errors() -> None:
    from aws_tui.domain import athena

    assert set(athena.__all__) >= {
        "AthenaCatalogSummary",
        "AthenaClient",
        "AthenaWorkgroupDetail",
        "AthenaWorkgroupSummary",
        "ResultConfigurationRequiredError",
        "map_athena_error",
    }


def test_credential_retrieval_failure_never_carries_the_process_output() -> None:
    """botocore renders credential-process output into the exception message.

    `CredentialRetrievalError` is the one credential exception whose `str()`
    embeds whatever the configured `credential_process` printed — routinely the
    literal `aws_secret_access_key <secret>`. Every other branch of this mapper
    passes `str(exc)` through `_sanitize_message`; this one deliberately does
    not, returning fixed copy instead.

    Nothing pinned that. Removing the branch let the raw output reach
    `log_sink`'s rotating file and `crash_dump`'s durable dumps. Redaction is
    now a second line of defence, but the message must not carry the secret in
    the first place.
    """
    leaked = "wJalrXUtnFEMIK7MDENGbPxRfiCY"
    exc = botocore.exceptions.CredentialRetrievalError(
        provider="custom-process",
        error_msg=f"command output: aws_secret_access_key {leaked}",
    )

    mapped = map_athena_error(exc)

    assert isinstance(mapped, AuthRequiredError)
    assert leaked not in str(mapped)
    assert "command output" not in str(mapped)
    assert str(mapped) == "credential process failed"


def test_other_credential_failures_still_report_their_reason() -> None:
    """Positive control: only the credential-process case is blanked.

    A `NoCredentialsError` must still explain itself, or the guard above could
    be satisfied by discarding every credential message.
    """
    mapped = map_athena_error(botocore.exceptions.NoCredentialsError())

    assert isinstance(mapped, AuthRequiredError)
    assert str(mapped), "the message was blanked entirely"
    assert str(mapped) != "credential process failed"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("context", "axis"),
    [
        # Name differs, region matches the active connection.
        (QueryContext("prod", "us-east-1", "analysts", "AwsDataCatalog", "analytics"), "name"),
        # Region differs, name matches.
        (QueryContext("dev", "eu-west-1", "analysts", "AwsDataCatalog", "analytics"), "region"),
    ],
)
async def test_start_query_rejects_a_single_axis_context_mismatch(
    context: QueryContext, axis: str
) -> None:
    """Each operand of the connection guard must reject on its own.

    The sibling test above mismatches name AND region together, so either
    operand alone still rejected it — and a mutation sweep found both operands
    individually survivable. With one neutered, a context built for another
    account (or another region) is submitted through THIS connection's client:
    the query runs against the wrong account with the wrong workgroup and
    database.
    """
    client, boto, session = _athena_client()

    with pytest.raises(ValidationError, match="query context"):
        await client.start_query("SELECT 1", context, request_token="token-123")

    boto.start_query_execution.assert_not_awaited(), axis
    assert session.requests == []


@pytest.mark.asyncio
async def test_query_detail_for_a_different_execution_id_is_rejected() -> None:
    """A detail response must belong to the execution that was asked for.

    `get_query_execution` trusts the service to echo the id back; the identity
    check is the only thing stopping a mismatched response being attributed to
    the requested execution. It was pinned by nothing — with it removed, asking
    for one id returned another execution's detail as if it were the requested
    one.
    """
    client, _boto, _session = _athena_client(
        "get_query_execution",
        {
            "QueryExecution": {
                "QueryExecutionId": "OTHER-EXECUTION",
                "Status": {"State": "SUCCEEDED"},
                "WorkGroup": "analysts",
                "Query": "SELECT 1",
                "StatusUpdateDateTime": None,
            }
        },
    )

    with pytest.raises(ProviderError):
        await client.get_query_execution("REQUESTED-EXECUTION")


async def test_first_page_without_a_header_row_keeps_every_data_row() -> None:
    """The header heuristic must compare, not assume.

    Athena includes the column-name header row only for queries that produce
    one; a first page whose row 0 is real data must keep it. Forcing the
    name-comparison to True survived the whole suite — the existing first-page
    test uses a response whose row 0 IS the header, so dropping row 0
    unconditionally looked identical. Under that mutation the first data row of
    every headerless first page was silently discarded.
    """
    response = {
        "ResultSet": {
            "ResultSetMetadata": {
                "ColumnInfo": [
                    {"Name": "event_id", "Type": "varchar"},
                    {"Name": "count", "Type": "bigint"},
                ]
            },
            "Rows": [
                {"Data": [{"VarCharValue": "row-1"}, {"VarCharValue": "10"}]},
                {"Data": [{"VarCharValue": "row-2"}, {"VarCharValue": "20"}]},
            ],
        }
    }
    client, _boto, _session = _athena_client("get_query_results", response)

    page = await client.get_results_page("q-123")

    assert page.rows == (("row-1", "10"), ("row-2", "20")), (
        "a data row was mistaken for the header and dropped"
    )


async def test_empty_first_result_page_is_an_empty_table_not_an_error() -> None:
    """A SELECT with zero rows must render as empty, not raise.

    Forcing the `rows` truthiness operand made an empty first page reach
    `rows[0]` and surface `ValidationError: malformed Athena response` — an
    empty result set reported as a broken one.
    """
    response = {
        "ResultSet": {
            "ResultSetMetadata": {"ColumnInfo": [{"Name": "event_id", "Type": "varchar"}]},
            "Rows": [],
        }
    }
    client, _boto, _session = _athena_client("get_query_results", response)

    page = await client.get_results_page("q-123")

    assert page.rows == ()
    assert page.columns == (ResultColumn("event_id", "varchar", "UNKNOWN"),)


async def test_a_full_history_page_of_exactly_50_ids_is_accepted() -> None:
    """The batch cap must be `>`, not `>=`: 50 ids IS a full page.

    `list_query_executions_page` returns pages of exactly `_PAGE_SIZE` (50)
    ids, and history hydration feeds each page straight into this batch call.
    Flipping the comparison to `>=` rejected every full page with
    "Athena history batches support at most 50 ids" — history stopped loading
    for any workgroup with 50+ executions. No test hydrated a full page.
    """
    executions = []
    for index in range(50):
        execution = cast(dict[str, object], _query_execution("SUCCEEDED")["QueryExecution"])
        execution = dict(execution)
        execution["QueryExecutionId"] = f"q-{index}"
        executions.append(execution)
    client, boto, _ = _athena_client(
        "batch_get_query_execution",
        {"QueryExecutions": executions, "UnprocessedQueryExecutionIds": []},
    )
    ids = [f"q-{index}" for index in range(50)]

    details = await client.get_query_executions(ids)

    assert len(details) == 50
    boto.batch_get_query_execution.assert_awaited_once_with(QueryExecutionIds=ids)


async def test_a_history_batch_of_51_ids_is_still_rejected() -> None:
    """Positive control for the cap itself."""
    client, boto, _ = _athena_client()

    with pytest.raises(ValidationError, match="at most 50"):
        await client.get_query_executions([f"q-{index}" for index in range(51)])

    boto.batch_get_query_execution.assert_not_awaited()


async def test_an_unprocessed_batch_id_surfaces_its_actual_error() -> None:
    """An AccessDenied on one execution must not become "malformed response".

    `_unprocessed_query_execution_error` maps the per-id error code to the
    typed taxonomy. Neutralising it let the generic
    `ValidationError: malformed Athena response` replace an actionable
    permission error — the user loses the one message that explains what to fix.
    """
    execution = cast(dict[str, object], _query_execution("SUCCEEDED")["QueryExecution"])
    client, _boto, _session = _athena_client(
        "batch_get_query_execution",
        {
            "QueryExecutions": [execution],
            "UnprocessedQueryExecutionIds": [
                {
                    "QueryExecutionId": "q-denied",
                    "ErrorCode": "AccessDeniedException",
                    "ErrorMessage": "not authorized to view q-denied",
                }
            ],
        },
    )

    with pytest.raises(PermissionDeniedError):
        await client.get_query_executions(["q-1", "q-denied"])


async def test_a_cancelled_stop_request_propagates_as_cancellation() -> None:
    """A cancelled stop must not read as a successful stop.

    `stop_query`'s `except BaseException` re-raise exists so a CancelledError
    from the SDK call propagates instead of falling through to
    `_retire_app_started_query` — which would tell the UI the query was
    cancelled when the stop request itself never completed. Replacing the
    re-raise with `pass` survived the whole suite.
    """
    import asyncio

    client, boto, _session = _athena_client(
        "start_query_execution",
        {"QueryExecutionId": "q-started"},
    )
    boto.stop_query_execution.side_effect = asyncio.CancelledError()
    await client.start_query("SELECT 1", CONTEXT, request_token="token-123")

    with pytest.raises(asyncio.CancelledError):
        await client.stop_query("q-started")
