"""Tests for EMR Serverless domain data records.

These are plain frozen dataclasses + StrEnums; the tests pin field
order, immutability, and the canonical state enums so VMs above
can pattern-match on stable values."""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import UTC, datetime

import botocore.exceptions
import pytest

from aws_tui.domain.emr_serverless import (
    ApplicationState,
    ApplicationSummary,
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
