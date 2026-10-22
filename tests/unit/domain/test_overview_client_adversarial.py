from dataclasses import asdict

import botocore.exceptions
import pytest

from aws_tui.domain.filesystem import ProviderError, ValidationError
from aws_tui.domain.overview_client import OverviewClient
from tests.unit.domain.test_overview_client import CONNECTION, CountedSession, execution, scope


@pytest.mark.asyncio
async def test_emr_read_errors_do_not_expose_unbounded_raw_provider_text():
    marker = "synthetic-overview-secret"
    session = CountedSession(
        list_job_runs=botocore.exceptions.ClientError(
            {
                "Error": {
                    "Code": "AccessDeniedException",
                    "Message": "secret_access_key=" + marker + "\n" + "x" * 1000,
                }
            },
            "ListJobRuns",
        )
    )
    with pytest.raises(ProviderError) as caught:
        await OverviewClient(session).read(scope("emr-serverless"), CONNECTION)
    assert marker not in str(caught.value)
    assert len(str(caught.value)) <= 500
    assert "\n" not in str(caught.value)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "last_status", ["x" * 1000, "FAILED\x1b[31m\n"], ids=["oversize", "controls"]
)
async def test_crawler_last_status_is_bounded_and_control_free(last_status):
    session = CountedSession(
        get_crawler={
            "Crawler": {
                "Name": "chosen",
                "State": "READY",
                "LastCrawl": {"Status": last_status},
            }
        }
    )
    with pytest.raises(ValidationError):
        await OverviewClient(session).read(scope("glue-crawler"), CONNECTION)


@pytest.mark.asyncio
async def test_failed_athena_summary_does_not_retain_the_returned_raw_query():
    sql = "SELECT confidential_customer_literal FROM private_table WHERE segment = 'sensitive-literal'"
    row = execution("run-a", state="FAILED")
    row["Query"] = sql
    row["Status"]["StateChangeReason"] = "Failed while executing SQL: " + sql
    session = CountedSession(
        list_query_executions={"QueryExecutionIds": ["run-a"]},
        batch_get_query_execution={"QueryExecutions": [row]},
    )
    page = await OverviewClient(session).read(scope("athena"), CONNECTION)
    assert sql not in str(asdict(page)), "raw Query was copied into retained error summary"
    assert "confidential_customer_literal" not in page.entries[0].error_summary


@pytest.mark.asyncio
async def test_emr_missing_required_job_runs_is_not_a_successful_empty_page():
    session = CountedSession(list_job_runs={})
    with pytest.raises(ValidationError):
        await OverviewClient(session).read(scope("emr-serverless"), CONNECTION)


@pytest.mark.parametrize(
    ("kind", "method", "key", "token"),
    [
        ("athena", "list_query_executions", "QueryExecutionIds", "NextToken"),
        ("glue-job", "get_job_runs", "JobRuns", "NextToken"),
        ("emr-serverless", "list_job_runs", "jobRuns", "nextToken"),
    ],
)
@pytest.mark.parametrize("malformed", ["array", "token"])
@pytest.mark.asyncio
async def test_malformed_collection_or_page_token_is_refused(kind, method, key, token, malformed):
    response = {key: {}} if malformed == "array" else {key: [], token: {"not": "a string"}}
    session = CountedSession(**{method: response})
    with pytest.raises(ValidationError):
        await OverviewClient(session).read(scope(kind), CONNECTION)
    assert len(session.calls) == 1


@pytest.mark.parametrize(
    ("kind", "status", "last_status", "expected"),
    [
        ("glue-job", "STARTING", None, "active"),
        ("glue-job", "STOPPED", None, "other"),
        ("glue-job", "EXPIRED", None, "failed"),
        ("glue-crawler", "READY", "FAILED", "failed"),
        ("glue-crawler", "READY", "CANCELLED", "other"),
        ("glue-crawler", "STOPPING", "FAILED", "active"),
        ("glue-crawler", "FUTURE_STATE", "FAILED", "other"),
        ("emr-serverless", "QUEUED", None, "active"),
        ("emr-serverless", "CANCELLED", None, "other"),
    ],
)
def test_native_group_controls(kind, status, last_status, expected):
    from aws_tui.domain.scoped_overview import OverviewEntry

    entry = OverviewEntry(scope(kind), "run", status, last_status=last_status)
    assert entry.group == expected
    assert entry.status == status
    assert entry.last_status == last_status


@pytest.mark.parametrize(
    "kind", ["athena", "glue-job", "glue-crawler", "emr-serverless", "transfers"]
)
@pytest.mark.asyncio
async def test_non_aws_connection_never_creates_a_service_client(kind):
    from dataclasses import replace

    session = CountedSession()
    selected = scope(kind, "" if kind == "transfers" else "chosen")
    with pytest.raises(ValidationError):
        await OverviewClient(session).read(selected, replace(CONNECTION, kind="s3-compatible"))
    assert not session.calls
    assert not session.connections


@pytest.mark.parametrize(
    "error_type", ["auth", "denied", "throttled", "notfound", "unreachable", "invalid", "generic"]
)
@pytest.mark.asyncio
async def test_direct_provider_error_is_safe_and_keeps_category(error_type):
    from aws_tui.domain.filesystem import (
        AuthRequiredError,
        NotFoundError,
        PermissionDeniedError,
        ProviderUnreachableError,
        ThrottledError,
    )

    types = {
        "auth": AuthRequiredError,
        "denied": PermissionDeniedError,
        "throttled": ThrottledError,
        "notfound": NotFoundError,
        "unreachable": ProviderUnreachableError,
        "invalid": ValidationError,
        "generic": ProviderError,
    }
    category = types[error_type]
    session = CountedSession(list_job_runs=category("password=synthetic-sensitive\n" + "x" * 1000))
    with pytest.raises(category) as caught:
        await OverviewClient(session).read(scope("emr-serverless"), CONNECTION)
    assert "synthetic-sensitive" not in str(caught.value)
    assert len(str(caught.value)) <= 500
    assert "\n" not in str(caught.value)


@pytest.mark.parametrize(
    "style", ["case_only", "collapsed_whitespace", "without_semicolon", "query_excerpt"]
)
@pytest.mark.asyncio
async def test_athena_echo_normalization_does_not_retain_sql(style):
    marker = "confidential_projection_marker"
    sql = "SELECT\n    " + marker + "\nFROM private_table;"
    echoed = {
        "case_only": sql.lower(),
        "collapsed_whitespace": " ".join(sql.split()),
        "without_semicolon": sql.rstrip(";"),
        "query_excerpt": "SELECT\n    " + marker,
    }[style]
    row = execution("run-a", state="FAILED")
    row["Query"] = sql
    row["Status"]["StateChangeReason"] = "Failed while executing SQL: " + echoed
    session = CountedSession(
        list_query_executions={"QueryExecutionIds": ["run-a"]},
        batch_get_query_execution={"QueryExecutions": [row]},
    )
    page = await OverviewClient(session).read(scope("athena"), CONNECTION)
    assert marker not in page.entries[0].error_summary.casefold(), (
        "SQL content survived native error echo normalization"
    )
    assert len(session.calls) == 2
