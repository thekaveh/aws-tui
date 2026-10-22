from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any

import botocore.exceptions
import botocore.session
import pytest
from botocore.validate import validate_parameters

from aws_tui.domain.filesystem import PermissionDeniedError, ThrottledError, ValidationError
from aws_tui.domain.overview_client import OverviewClient
from aws_tui.domain.scoped_overview import WatchScope
from aws_tui.infra.connection_resolver import Connection

CONNECTION = Connection("dev", "aws", "eu-west-1", "config", profile="dev-profile")
NOW = datetime(2026, 1, 2, tzinfo=UTC)


class CountedSession:
    def __init__(self, **responses: Any) -> None:
        self.responses = responses
        self.calls: list[tuple[str, str, dict[str, Any]]] = []
        self.connections: list[Connection] = []
        self.sdk = botocore.session.get_session()

    async def client(self, connection: Connection, service: str) -> Any:
        self.connections.append(connection)
        owner = self

        class Client:
            def __getattr__(self, method: str) -> Any:
                async def call(**kwargs: Any) -> Any:
                    model = owner.sdk.get_service_model(service)
                    operation = next(
                        name
                        for name in model.operation_names
                        if botocore.xform_name(name) == method
                    )
                    validate_parameters(kwargs, model.operation_model(operation).input_shape)
                    owner.calls.append((service, method, kwargs))
                    response = owner.responses[method]
                    if isinstance(response, Exception):
                        raise response
                    return response

                return call

        @asynccontextmanager
        async def context() -> Any:
            yield Client()

        return context()


def scope(kind: str, resource: str = "chosen") -> WatchScope:
    return WatchScope(kind, CONNECTION.name, CONNECTION.region, resource)


def execution(identifier: str, state: str = "RUNNING", workgroup: str = "chosen") -> dict[str, Any]:
    return {
        "QueryExecutionId": identifier,
        "WorkGroup": workgroup,
        "Status": {"State": state, "SubmissionDateTime": NOW, "StateChangeReason": "native reason"},
        "Query": "DO NOT RETAIN THIS SQL",
        "ResultConfiguration": {"OutputLocation": "s3://private-results"},
    }


@pytest.mark.asyncio
async def test_athena_reads_only_explicit_workgroup_and_one_exact_batch() -> None:
    session = CountedSession(
        list_query_executions={
            "QueryExecutionIds": ["run-a", "run-b"],
            "NextToken": "not-followed",
        },
        batch_get_query_execution={
            "QueryExecutions": [execution("run-b", "FAILED"), execution("run-a")],
            "UnprocessedQueryExecutionIds": [],
        },
    )
    page = await OverviewClient(session).read(scope("athena"), CONNECTION)
    assert session.calls == [
        ("athena", "list_query_executions", {"WorkGroup": "chosen", "MaxResults": 25}),
        ("athena", "batch_get_query_execution", {"QueryExecutionIds": ["run-a", "run-b"]}),
    ]
    assert session.connections == [CONNECTION]
    assert [row.identifier for row in page.entries] == ["run-a", "run-b"]
    assert [row.group for row in page.entries] == ["active", "failed"]
    assert page.entries[0].times == (("submitted", NOW), ("completed", None))
    assert page.cap is not None
    assert "DO NOT RETAIN" not in repr(page)
    assert "private-results" not in repr(page)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "response",
    [
        {"QueryExecutions": [execution("unrequested")]},
        {"QueryExecutions": [execution("run-a", workgroup="other")]},
        {"QueryExecutions": [execution("run-a"), execution("run-a")]},
        {"QueryExecutions": []},
        {
            "QueryExecutions": [execution("run-a")],
            "UnprocessedQueryExecutionIds": [{"QueryExecutionId": "run-a"}],
        },
    ],
)
async def test_athena_rejects_foreign_duplicate_missing_and_unprocessed_details(
    response: dict[str, Any],
) -> None:
    session = CountedSession(
        list_query_executions={"QueryExecutionIds": ["run-a"]},
        batch_get_query_execution=response,
    )
    with pytest.raises(ValidationError):
        await OverviewClient(session).read(scope("athena"), CONNECTION)
    assert len(session.calls) == 2


@pytest.mark.asyncio
async def test_empty_athena_scope_does_not_make_empty_batch_request() -> None:
    session = CountedSession(list_query_executions={"QueryExecutionIds": []})
    assert not (await OverviewClient(session).read(scope("athena"), CONNECTION)).entries
    assert len(session.calls) == 1


@pytest.mark.asyncio
async def test_glue_native_run_status_timing_error_and_explicit_job_request() -> None:
    session = CountedSession(
        get_job_runs={
            "JobRuns": [
                {
                    "JobName": "chosen",
                    "Id": "run-a",
                    "JobRunState": "TIMEOUT",
                    "StartedOn": NOW,
                    "ErrorMessage": "native failure",
                    "Arguments": {"--secret": "must-not-retain"},
                },
                {"JobName": "chosen", "Id": "run-b", "JobRunState": "SUCCEEDED"},
            ]
        }
    )
    page = await OverviewClient(session).read(scope("glue-job"), CONNECTION)
    assert session.calls == [("glue", "get_job_runs", {"JobName": "chosen", "MaxResults": 25})]
    assert page.entries[0].status == "TIMEOUT"
    assert page.entries[0].group == "failed"
    assert page.entries[0].error_summary == "native failure"
    assert page.entries[0].times[0] == ("started", NOW)
    assert page.entries[1].group == "other"
    assert "must-not-retain" not in repr(page)


@pytest.mark.asyncio
async def test_crawler_reads_no_tags_metrics_sts_or_inventory() -> None:
    session = CountedSession(
        get_crawler={
            "Crawler": {
                "Name": "chosen",
                "State": "RUNNING",
                "LastUpdated": NOW,
                "LastCrawl": {
                    "Status": "FAILED",
                    "StartTime": NOW,
                    "ErrorMessage": "last crawl failure",
                },
            }
        }
    )
    page = await OverviewClient(session).read(scope("glue-crawler"), CONNECTION)
    assert session.calls == [("glue", "get_crawler", {"Name": "chosen"})]
    assert len(page.entries) == 1
    row = page.entries[0]
    assert row.group == "active"
    assert row.last_status == "FAILED"
    assert row.error_summary == "last crawl failure"
    assert ("last crawl started", NOW) in row.times


@pytest.mark.asyncio
async def test_emr_summary_has_native_error_without_per_run_hydration() -> None:
    session = CountedSession(
        list_job_runs={
            "jobRuns": [
                {
                    "applicationId": "chosen",
                    "id": "run-a",
                    "state": "FAILED",
                    "stateDetails": "native failure",
                    "createdAt": NOW,
                    "updatedAt": NOW,
                }
            ],
            "nextToken": "not-followed",
        }
    )
    page = await OverviewClient(session).read(scope("emr-serverless"), CONNECTION)
    assert session.calls == [
        ("emr-serverless", "list_job_runs", {"applicationId": "chosen", "maxResults": 25})
    ]
    assert page.entries[0].error_summary == "native failure"
    assert page.entries[0].times == (("created", NOW), ("updated", NOW))
    assert page.cap is not None


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["athena", "glue-job", "glue-crawler", "emr-serverless"])
async def test_current_connection_and_region_are_checked_before_any_request(kind: str) -> None:
    session = CountedSession()
    other = Connection("dev", "aws", "us-east-1", "config", profile="dev-profile")
    with pytest.raises(ValidationError):
        await OverviewClient(session).read(scope(kind), other)
    assert session.calls == []
    assert session.connections == []


@pytest.mark.asyncio
async def test_oversized_service_response_is_capped_without_following_pages() -> None:
    session = CountedSession(
        list_job_runs={
            "jobRuns": [
                {"applicationId": "chosen", "id": f"run-{i}", "state": "RUNNING"} for i in range(30)
            ]
        }
    )
    page = await OverviewClient(session).read(scope("emr-serverless"), CONNECTION)
    assert len(page.entries) == 25
    assert page.cap is not None
    assert len(session.calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("code", "expected"),
    [("AccessDeniedException", PermissionDeniedError), ("ThrottlingException", ThrottledError)],
)
async def test_provider_failures_keep_shared_taxonomy(code: str, expected: type[Exception]) -> None:
    error = botocore.exceptions.ClientError(
        {"Error": {"Code": code, "Message": "raw secret=must-not-retain"}}, "GetJobRuns"
    )
    session = CountedSession(get_job_runs=error)
    with pytest.raises(expected) as caught:
        await OverviewClient(session).read(scope("glue-job"), CONNECTION)
    assert "must-not-retain" not in str(caught.value)
    assert len(session.calls) == 1


@pytest.mark.asyncio
async def test_empty_optional_error_fields_do_not_fail_a_valid_crawler_read() -> None:
    session = CountedSession(
        get_crawler={
            "Crawler": {"Name": "chosen", "State": "READY", "LastCrawl": {"ErrorMessage": ""}}
        }
    )
    page = await OverviewClient(session).read(scope("glue-crawler"), CONNECTION)
    assert page.entries[0].error_summary is None


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["glue-job", "glue-crawler", "emr-serverless"])
async def test_other_services_reject_foreign_resource_identities(kind: str) -> None:
    session = CountedSession(
        get_job_runs={"JobRuns": [{"JobName": "other", "Id": "run-a", "JobRunState": "RUNNING"}]},
        get_crawler={"Crawler": {"Name": "other", "State": "READY"}},
        list_job_runs={"jobRuns": [{"applicationId": "other", "id": "run-a", "state": "RUNNING"}]},
    )
    with pytest.raises(ValidationError):
        await OverviewClient(session).read(scope(kind), CONNECTION)
    assert len(session.calls) == 1


@pytest.mark.asyncio
async def test_athena_summary_retains_native_structured_error_without_freeform_sql() -> None:
    row = execution("run-a", "FAILED")
    row["Status"]["AthenaError"] = {
        "ErrorCategory": 2,
        "ErrorType": 1006,
        "Retryable": False,
        "ErrorMessage": "parse error near SELECT sensitive_customer",
    }
    session = CountedSession(
        list_query_executions={"QueryExecutionIds": ["run-a"]},
        batch_get_query_execution={"QueryExecutions": [row]},
    )
    page = await OverviewClient(session).read(scope("athena"), CONNECTION)
    summary = page.entries[0].error_summary
    assert summary is not None
    assert "category=2" in summary
    assert "type=1006" in summary
    assert "retryable=false" in summary
    assert "sensitive_customer" not in summary
