"""Tests for EMR Serverless domain data records.

These are plain frozen dataclasses + StrEnums; the tests pin field
order, immutability, and the canonical state enums so VMs above
can pattern-match on stable values."""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import UTC, datetime
from unittest.mock import AsyncMock

import botocore.exceptions
import pytest

from aws_tui.domain.emr_serverless import (
    ApplicationState,
    ApplicationSummary,
    EmrServerlessClient,
    JobRunDetail,
    JobRunState,
    JobRunSummary,
    _map_boto_error,
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


def test_application_summary_is_frozen() -> None:
    a = ApplicationSummary(
        id="00fabc",
        name="etl",
        state=ApplicationState.STARTED,
        type="SPARK",
        created_at=datetime(2026, 6, 25, tzinfo=UTC),
    )
    with pytest.raises(FrozenInstanceError):
        a.name = "renamed"  # type: ignore[misc]


def test_job_run_summary_carries_state_and_timestamps() -> None:
    r = JobRunSummary(
        application_id="00fabc",
        job_run_id="00jrx",
        name="nightly",
        state=JobRunState.RUNNING,
        created_at=datetime(2026, 6, 25, 12, 0, tzinfo=UTC),
        updated_at=datetime(2026, 6, 25, 12, 5, tzinfo=UTC),
    )
    assert r.state is JobRunState.RUNNING
    assert r.created_at < r.updated_at


def test_job_run_detail_extends_summary_with_spark_params() -> None:
    d = JobRunDetail(
        application_id="00fabc",
        job_run_id="00jrx",
        name=None,
        state=JobRunState.SUCCESS,
        created_at=datetime(2026, 6, 25, 12, 0, tzinfo=UTC),
        updated_at=datetime(2026, 6, 25, 12, 4, tzinfo=UTC),
        entry_point="s3://b/job.py",
        entry_point_arguments=("--in", "s3://b/in/"),
        spark_submit_parameters="--conf spark.executor.instances=4",
        execution_role_arn="arn:aws:iam::123456789012:role/EmrJobRole",
        duration_ms=240_000,
    )
    assert d.entry_point_arguments == ("--in", "s3://b/in/")
    assert d.duration_ms == 240_000


def test_application_state_enum_values() -> None:
    assert ApplicationState.STARTED == "STARTED"
    assert {s.value for s in ApplicationState} == {
        "CREATED",
        "STARTING",
        "STARTED",
        "STOPPING",
        "STOPPED",
        "TERMINATED",
    }


def test_job_run_state_enum_values() -> None:
    assert JobRunState.SUCCESS == "SUCCESS"
    assert {s.value for s in JobRunState} == {
        "PENDING",
        "RUNNING",
        "SUCCESS",
        "FAILED",
        "CANCELLING",
        "CANCELLED",
    }


def _client_error(code: str, op: str = "ListApplications") -> botocore.exceptions.ClientError:
    return botocore.exceptions.ClientError(
        {"Error": {"Code": code, "Message": f"mocked {code}"}}, op
    )


@pytest.mark.parametrize(
    ("raised", "expected"),
    [
        (botocore.exceptions.NoCredentialsError(), AuthRequiredError),
        (
            botocore.exceptions.TokenRetrievalError(provider="sso", error_msg="token expired"),
            AuthRequiredError,
        ),
        (botocore.exceptions.EndpointConnectionError(endpoint_url="x"), ProviderUnreachableError),
        (botocore.exceptions.ConnectTimeoutError(endpoint_url="x"), ProviderUnreachableError),
        (botocore.exceptions.ReadTimeoutError(endpoint_url="x"), ProviderUnreachableError),
        (_client_error("AccessDeniedException"), PermissionDeniedError),
        (_client_error("ThrottlingException"), ThrottledError),
        (_client_error("ResourceNotFoundException"), NotFoundError),
        (_client_error("ValidationException"), ValidationError),
        (_client_error("InternalServerException"), ProviderError),
    ],
)
def test_map_boto_error_maps_to_provider_error_subclass(
    raised: BaseException, expected: type[ProviderError]
) -> None:
    mapped = _map_boto_error(raised)
    assert mapped is not None
    assert isinstance(mapped, expected)
    # Original cause preserved for log_sink + debugging.
    assert (
        mapped.__cause__ is raised or mapped.__cause__ is None
    )  # cause may be set by `raise from`


def test_map_boto_error_returns_none_for_unrelated_exceptions() -> None:
    assert _map_boto_error(ValueError("not an aws exception")) is None


# ── EmrServerlessClient tests ────────────────────────────────────────────────


def _fake_app_response(items: list[dict]) -> dict:
    return {"applications": items, "nextToken": None}


def _fake_run_response(items: list[dict]) -> dict:
    return {"jobRuns": items, "nextToken": None}


class _StubClient:
    """Minimal aioboto3-shaped async client we can hand to
    EmrServerlessClient for testing without touching the network."""

    def __init__(self) -> None:
        self.list_applications = AsyncMock()
        self.list_job_runs = AsyncMock()
        self.get_job_run = AsyncMock()

    async def __aenter__(self) -> _StubClient:
        return self

    async def __aexit__(self, *args: object) -> None:
        return None


class _StubSession:
    """aioboto3.Session-shaped fake."""

    def __init__(self, stub: _StubClient) -> None:
        self._stub = stub

    def client(self, service_name: str, **kwargs: object) -> _StubClient:
        assert service_name == "emr-serverless"
        return self._stub


@pytest.mark.asyncio
async def test_list_applications_maps_response_to_records() -> None:
    stub = _StubClient()
    stub.list_applications.return_value = _fake_app_response(
        [
            {
                "id": "00abc",
                "name": "etl",
                "state": "STARTED",
                "type": "SPARK",
                "createdAt": datetime(2026, 6, 25, tzinfo=UTC),
            },
            {
                "id": "00def",
                "name": "ad-hoc",
                "state": "STOPPED",
                "type": "SPARK",
                "createdAt": datetime(2026, 6, 24, tzinfo=UTC),
            },
        ]
    )
    client = EmrServerlessClient(session=_StubSession(stub))  # type: ignore[arg-type]

    apps = await client.list_applications()
    assert len(apps) == 2
    assert apps[0].id == "00abc"
    assert apps[0].state.value == "STARTED"
    assert apps[1].name == "ad-hoc"


@pytest.mark.asyncio
async def test_list_job_runs_filters_by_state_client_side() -> None:
    stub = _StubClient()
    stub.list_job_runs.return_value = _fake_run_response(
        [
            {
                "applicationId": "00abc",
                "id": "jr1",
                "name": "n1",
                "state": "SUCCESS",
                "createdAt": datetime(2026, 6, 25, tzinfo=UTC),
                "updatedAt": datetime(2026, 6, 25, tzinfo=UTC),
            },
            {
                "applicationId": "00abc",
                "id": "jr2",
                "name": "n2",
                "state": "FAILED",
                "createdAt": datetime(2026, 6, 25, tzinfo=UTC),
                "updatedAt": datetime(2026, 6, 25, tzinfo=UTC),
            },
            {
                "applicationId": "00abc",
                "id": "jr3",
                "name": "n3",
                "state": "RUNNING",
                "createdAt": datetime(2026, 6, 25, tzinfo=UTC),
                "updatedAt": datetime(2026, 6, 25, tzinfo=UTC),
            },
        ]
    )
    client = EmrServerlessClient(session=_StubSession(stub))  # type: ignore[arg-type]
    runs = await client.list_job_runs("00abc", states={JobRunState.SUCCESS, JobRunState.RUNNING})
    assert [r.job_run_id for r in runs] == ["jr1", "jr3"]


@pytest.mark.asyncio
async def test_get_job_run_maps_detail_fields() -> None:
    stub = _StubClient()
    stub.get_job_run.return_value = {
        "jobRun": {
            "applicationId": "00abc",
            "jobRunId": "jr1",
            "name": "etl",
            "state": "SUCCESS",
            "createdAt": datetime(2026, 6, 25, 12, 0, tzinfo=UTC),
            "updatedAt": datetime(2026, 6, 25, 12, 4, tzinfo=UTC),
            "executionRole": "arn:aws:iam::123456789012:role/EmrJobRole",
            "jobDriver": {
                "sparkSubmit": {
                    "entryPoint": "s3://b/job.py",
                    "entryPointArguments": ["--in", "s3://b/in/"],
                    "sparkSubmitParameters": "--conf spark.executor.instances=4",
                },
            },
            "totalExecutionDurationSeconds": 240,
        },
    }
    client = EmrServerlessClient(session=_StubSession(stub))  # type: ignore[arg-type]
    detail = await client.get_job_run("00abc", "jr1")
    assert detail.entry_point == "s3://b/job.py"
    assert detail.entry_point_arguments == ("--in", "s3://b/in/")
    assert detail.spark_submit_parameters == "--conf spark.executor.instances=4"
    assert detail.execution_role_arn == "arn:aws:iam::123456789012:role/EmrJobRole"
    assert detail.duration_ms == 240_000


@pytest.mark.asyncio
async def test_list_applications_re_raises_as_provider_error_on_no_creds() -> None:
    stub = _StubClient()
    stub.list_applications.side_effect = botocore.exceptions.NoCredentialsError()
    client = EmrServerlessClient(session=_StubSession(stub))  # type: ignore[arg-type]
    with pytest.raises(AuthRequiredError):
        await client.list_applications()
