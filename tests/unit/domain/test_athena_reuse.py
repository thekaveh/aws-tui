"""Explicit Athena result-reuse request and admission contracts."""

from __future__ import annotations

from typing import cast

import pytest

from aws_tui.domain.filesystem import PermissionDeniedError, ProviderError, ValidationError
from aws_tui.domain.query import QueryExecutionRef
from tests.unit.domain.test_athena import CONTEXT, _athena_client, _client_error


def _workgroup(
    *, name: str = "analysts", state: str = "ENABLED", managed: bool = False
) -> dict[str, object]:
    return {
        "WorkGroup": {
            "Name": name,
            "State": state,
            "Configuration": {
                "ManagedQueryResultsConfiguration": {"Enabled": managed},
            },
        }
    }


@pytest.mark.parametrize("age", [0, 60, 10_080])
async def test_enabled_reuse_sends_exact_request_after_fresh_workgroup_check(age: int) -> None:
    client, boto, session = _athena_client("start_query_execution", {"QueryExecutionId": "q-reuse"})
    operations: list[str] = []

    async def read_workgroup(**_: object) -> dict[str, object]:
        operations.append("metadata")
        return _workgroup()

    async def start_query(**_: object) -> dict[str, object]:
        operations.append("start")
        return {"QueryExecutionId": "q-reuse"}

    boto.get_work_group.side_effect = read_workgroup
    boto.start_query_execution.side_effect = start_query

    ref = await client.start_query(
        " SELECT 1 ",
        CONTEXT,
        request_token="token-reuse",
        result_reuse_max_age_minutes=age,
    )

    assert ref == QueryExecutionRef("q-reuse", "dev", "us-east-1", "analysts")
    assert operations == ["metadata", "start"]
    boto.get_work_group.assert_awaited_once_with(WorkGroup="analysts")
    boto.start_query_execution.assert_awaited_once_with(
        QueryString="SELECT 1",
        ClientRequestToken="token-reuse",
        QueryExecutionContext={"Catalog": "AwsDataCatalog", "Database": "analytics"},
        WorkGroup="analysts",
        ResultReuseConfiguration={
            "ResultReuseByAgeConfiguration": {"Enabled": True, "MaxAgeInMinutes": age}
        },
    )
    assert len(session.requests) == 2


@pytest.mark.parametrize("explicit_none", [False, True])
async def test_disabled_reuse_preserves_request_without_workgroup_read(explicit_none: bool) -> None:
    client, boto, session = _athena_client("start_query_execution", {"QueryExecutionId": "q-plain"})
    options = {"result_reuse_max_age_minutes": None} if explicit_none else {}

    await client.start_query("SELECT 1", CONTEXT, request_token="token-plain", **options)

    boto.get_work_group.assert_not_awaited()
    boto.start_query_execution.assert_awaited_once_with(
        QueryString="SELECT 1",
        ClientRequestToken="token-plain",
        QueryExecutionContext={"Catalog": "AwsDataCatalog", "Database": "analytics"},
        WorkGroup="analysts",
    )
    assert len(session.requests) == 1


@pytest.mark.parametrize(
    "age",
    [-1, 10_081, True, False, 60.0, "AGE_SECRET_7F4C2A9D", "60", [], {}],
)
async def test_invalid_reuse_age_is_value_free_validation_before_any_sdk_request(
    age: object,
) -> None:
    client, boto, session = _athena_client()

    with pytest.raises(ValidationError) as raised:
        await client.start_query(
            "SELECT 1",
            CONTEXT,
            request_token="token-reuse",
            result_reuse_max_age_minutes=cast(int, age),
        )

    assert str(raised.value) == "result reuse age must be an integer from 0 to 10080 minutes"
    assert raised.value.__cause__ is None
    boto.get_work_group.assert_not_awaited()
    boto.start_query_execution.assert_not_awaited()
    assert session.requests == []


@pytest.mark.parametrize("output_location", ["s3://OUTPUT_SECRET_7F4C2A9D/", ""])
async def test_enabled_reuse_refuses_caller_output_override_before_any_sdk_request(
    output_location: str,
) -> None:
    client, boto, session = _athena_client()

    with pytest.raises(ValidationError, match=r"result reuse.*output") as raised:
        await client.start_query(
            "SELECT 1",
            CONTEXT,
            request_token="token-reuse",
            output_location=output_location,
            result_reuse_max_age_minutes=60,
        )

    assert "OUTPUT_SECRET_7F4C2A9D" not in str(raised.value)
    boto.get_work_group.assert_not_awaited()
    boto.start_query_execution.assert_not_awaited()
    assert session.requests == []


@pytest.mark.parametrize(
    "metadata",
    [
        _workgroup(managed=True),
        _workgroup(state="DISABLED"),
        _workgroup(state=""),
        _workgroup(name="WORKGROUP_SECRET_7F4C2A9D"),
    ],
    ids=["managed-results", "disabled", "unknown-state", "wrong-identity"],
)
async def test_ineligible_fresh_workgroup_refuses_reuse_before_start(
    metadata: dict[str, object],
) -> None:
    client, boto, session = _athena_client("get_work_group", metadata)

    with pytest.raises(ValidationError) as raised:
        await client.start_query(
            "SELECT 1",
            CONTEXT,
            request_token="token-reuse",
            result_reuse_max_age_minutes=60,
        )

    assert "WORKGROUP_SECRET_7F4C2A9D" not in str(raised.value)
    boto.get_work_group.assert_awaited_once_with(WorkGroup="analysts")
    boto.start_query_execution.assert_not_awaited()
    assert len(session.requests) == 1


async def test_reuse_rechecks_workgroup_on_each_submission() -> None:
    client, boto, _ = _athena_client("start_query_execution", {"QueryExecutionId": "q-reuse"})
    boto.get_work_group.side_effect = [_workgroup(), _workgroup(managed=True)]

    await client.start_query(
        "SELECT 1", CONTEXT, request_token="first", result_reuse_max_age_minutes=60
    )
    with pytest.raises(ValidationError):
        await client.start_query(
            "SELECT 1", CONTEXT, request_token="second", result_reuse_max_age_minutes=60
        )

    assert boto.get_work_group.await_count == 2
    assert boto.start_query_execution.await_count == 1


async def test_reuse_still_rejects_mutating_sql_before_any_sdk_request() -> None:
    client, boto, session = _athena_client()

    with pytest.raises(ValidationError):
        await client.start_query(
            "DROP TABLE events", CONTEXT, request_token="token", result_reuse_max_age_minutes=60
        )

    boto.get_work_group.assert_not_awaited()
    boto.start_query_execution.assert_not_awaited()
    assert session.requests == []


@pytest.mark.parametrize(
    ("error", "expected_type"),
    [
        (RuntimeError("METADATA_SECRET_7F4C2A9D"), ProviderError),
        (_client_error("AccessDeniedException", "access denied"), PermissionDeniedError),
    ],
)
async def test_reuse_metadata_failures_are_safe_provider_errors_without_starting_query(
    error: Exception, expected_type: type[ProviderError]
) -> None:
    client, boto, session = _athena_client()
    boto.get_work_group.side_effect = error

    with pytest.raises(expected_type) as raised:
        await client.start_query(
            "SELECT 1", CONTEXT, request_token="token", result_reuse_max_age_minutes=60
        )

    assert type(raised.value) is expected_type
    assert "METADATA_SECRET_7F4C2A9D" not in str(raised.value)
    assert raised.value.__cause__ is None
    boto.start_query_execution.assert_not_awaited()
    assert len(session.requests) == 1
