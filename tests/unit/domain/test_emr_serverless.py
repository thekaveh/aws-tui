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
    _EMR_BOTO_CONFIG,
    EMR_BOTO_CONFIG,
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
from tests.unit.domain._in_memory_emr import _InMemoryEmr


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
        s3_monitoring_log_uri=None,
    )
    assert d.entry_point_arguments == ("--in", "s3://b/in/")
    assert d.duration_ms == 240_000


def test_application_state_enum_values() -> None:
    """Mirrors the full ``ApplicationState`` shape from the botocore
    service model — including ``CREATING`` which the picker poller
    hits on freshly-provisioned applications. See the overnight
    maintenance loop's external-deps report for the rationale."""
    assert ApplicationState.STARTED == "STARTED"
    assert {s.value for s in ApplicationState} == {
        "CREATING",
        "CREATED",
        "STARTING",
        "STARTED",
        "STOPPING",
        "STOPPED",
        "TERMINATED",
    }


def test_job_run_state_enum_values() -> None:
    """Mirrors the full ``JobRunState`` shape from the botocore
    service model — ``SUBMITTED`` / ``SCHEDULED`` / ``QUEUED`` are
    the three pre-``RUNNING`` states a fresh submission cycles
    through; omitting them used to crash the 10-s poller within one
    tick. See the overnight maintenance loop's external-deps report
    for the rationale."""
    assert JobRunState.SUCCESS == "SUCCESS"
    assert {s.value for s in JobRunState} == {
        "SUBMITTED",
        "PENDING",
        "SCHEDULED",
        "QUEUED",
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
        (botocore.exceptions.ConnectionClosedError(endpoint_url="x"), ProviderUnreachableError),
        (
            botocore.exceptions.ProxyConnectionError(proxy_url="https://proxy.example"),
            ProviderUnreachableError,
        ),
        (botocore.exceptions.SSLError(endpoint_url="x", error="tls"), ProviderUnreachableError),
        (_client_error("AccessDeniedException"), PermissionDeniedError),
        (_client_error("ThrottlingException"), ThrottledError),
        (_client_error("ResourceNotFoundException"), NotFoundError),
        (_client_error("ValidationException"), ValidationError),
        (botocore.exceptions.ParamValidationError(report="bad parameter"), ValidationError),
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
    """Unmapped exceptions (anything that isn't a botocore error or a
    response-shape ``ValueError`` / ``KeyError``) must return None so
    the caller re-raises them unchanged."""
    assert _map_boto_error(RuntimeError("not an aws exception")) is None


def test_map_boto_error_wraps_value_error_as_validation_error() -> None:
    """A ``ValueError`` from the ``StrEnum`` constructor (or any other
    response-shape parse) becomes a typed ``ValidationError`` instead
    of crashing the worker / crash modal. This is the defensive layer
    behind the enum-completeness fix — even when AWS adds a new state
    we don't yet model, the user sees a typed domain error, not a
    raw ``ValueError`` traceback."""
    from aws_tui.domain.filesystem import ValidationError

    mapped = _map_boto_error(ValueError("unknown EMR state: SOMETHING_NEW"))
    assert isinstance(mapped, ValidationError)
    assert "malformed EMR Serverless response" in str(mapped)


def test_map_boto_error_wraps_key_error_as_validation_error() -> None:
    """Same defensive shape — a ``KeyError`` from a response missing a
    required field is a malformed-response signal."""
    from aws_tui.domain.filesystem import ValidationError

    mapped = _map_boto_error(KeyError("createdAt"))
    assert isinstance(mapped, ValidationError)


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
        self.start_job_run = AsyncMock()

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


@pytest.mark.asyncio
async def test_in_memory_emr_round_trips_records() -> None:
    fake = _InMemoryEmr()
    fake.add_application(app_id="a1", name="etl")
    fake.add_job_run(application_id="a1", job_run_id="r1", state=JobRunState.RUNNING)
    fake.add_job_run_detail(application_id="a1", job_run_id="r1", entry_point="s3://b/x.py")
    apps = await fake.list_applications()
    runs = await fake.list_job_runs("a1")
    detail = await fake.get_job_run("a1", "r1")
    assert apps[0].id == "a1"
    assert runs[0].state is JobRunState.RUNNING
    assert detail.entry_point == "s3://b/x.py"
    # Calls are recorded so cadence tests can assert poll counts.
    assert [c[0] for c in fake.calls] == ["list_applications", "list_job_runs", "get_job_run"]


@pytest.mark.asyncio
async def test_start_job_run_forwards_form_fields_to_boto() -> None:
    """The boto3 ``start_job_run`` call must receive the same
    five-field shape the modal exposes — ``applicationId``,
    ``executionRoleArn``, ``jobDriver.sparkSubmit`` (entry point +
    arguments + spark submit params), and optional ``name``."""
    stub = _StubClient()
    stub.start_job_run.return_value = {"jobRunId": "jr-new-1"}
    client = EmrServerlessClient(session=_StubSession(stub))  # type: ignore[arg-type]
    new_id = await client.start_job_run(
        "00abc",
        execution_role_arn="arn:aws:iam::123456789012:role/EmrJobRole",
        entry_point="s3://b/job.py",
        entry_point_arguments=("--in", "s3://b/in/"),
        spark_submit_parameters="--conf spark.executor.instances=4",
        name="nightly",
    )
    assert new_id == "jr-new-1"
    stub.start_job_run.assert_awaited_once()
    kwargs = stub.start_job_run.await_args.kwargs
    assert kwargs["applicationId"] == "00abc"
    assert kwargs["executionRoleArn"] == "arn:aws:iam::123456789012:role/EmrJobRole"
    assert kwargs["name"] == "nightly"
    spark = kwargs["jobDriver"]["sparkSubmit"]
    assert spark["entryPoint"] == "s3://b/job.py"
    assert spark["entryPointArguments"] == ["--in", "s3://b/in/"]
    assert spark["sparkSubmitParameters"] == "--conf spark.executor.instances=4"


@pytest.mark.asyncio
async def test_start_job_run_omits_name_and_blank_spark_params_when_unset() -> None:
    """``name`` is optional in the boto3 contract — it must be absent
    from the kwargs when the modal leaves the field blank.
    ``sparkSubmitParameters`` is also optional; botocore requires a
    non-blank value when the key is present, so blank modal values
    must omit the key entirely."""
    stub = _StubClient()
    stub.start_job_run.return_value = {"jobRunId": "jr-new-2"}
    client = EmrServerlessClient(session=_StubSession(stub))  # type: ignore[arg-type]
    await client.start_job_run(
        "00abc",
        execution_role_arn="arn:aws:iam::123456789012:role/Role",
        entry_point="s3://b/job.py",
        entry_point_arguments=(),
        spark_submit_parameters="   ",
        name=None,
    )
    kwargs = stub.start_job_run.await_args.kwargs
    assert "name" not in kwargs
    spark = kwargs["jobDriver"]["sparkSubmit"]
    assert "sparkSubmitParameters" not in spark
    assert spark["entryPointArguments"] == []


@pytest.mark.asyncio
async def test_start_job_run_maps_validation_exception_to_validation_error() -> None:
    """A boto ``ValidationException`` from a malformed entry point
    must surface as the typed :class:`ValidationError` so the modal
    can render an inline message instead of crashing."""
    stub = _StubClient()
    stub.start_job_run.side_effect = _client_error("ValidationException", op="StartJobRun")
    client = EmrServerlessClient(session=_StubSession(stub))  # type: ignore[arg-type]
    with pytest.raises(ValidationError):
        await client.start_job_run(
            "00abc",
            execution_role_arn="arn",
            entry_point="not-an-s3-url",
            entry_point_arguments=(),
            spark_submit_parameters=None,
        )


@pytest.mark.asyncio
async def test_in_memory_emr_start_job_run_records_and_materializes() -> None:
    """The fake mirrors the real client's contract — start a run,
    get a new job_run_id back, and observe the run + detail are
    visible through the regular ``list_job_runs`` + ``get_job_run``
    surface."""
    fake = _InMemoryEmr()
    fake.add_application(app_id="00abc", name="etl")
    new_id = await fake.start_job_run(
        "00abc",
        execution_role_arn="arn:aws:iam::123456789012:role/Role",
        entry_point="s3://b/job.py",
        entry_point_arguments=("--in", "s3://b/in/"),
        spark_submit_parameters="--conf x=y",
        name="cloned",
    )
    assert new_id.startswith("r-clone-")
    runs = await fake.list_job_runs("00abc")
    assert any(r.job_run_id == new_id for r in runs)
    detail = await fake.get_job_run("00abc", new_id)
    assert detail.entry_point == "s3://b/job.py"
    assert detail.state is JobRunState.SUBMITTED


@pytest.mark.asyncio
async def test_in_memory_emr_start_job_run_can_raise_for_failure_paths() -> None:
    """Tests that want to drive ``submit()`` into the error branch
    set ``start_job_run_exc`` on the fake before calling."""
    from aws_tui.domain.filesystem import ValidationError as _VE

    fake = _InMemoryEmr()
    fake.add_application(app_id="00abc", name="etl")
    fake.start_job_run_exc = _VE("invalid")
    with pytest.raises(_VE):
        await fake.start_job_run(
            "00abc",
            execution_role_arn="arn",
            entry_point="s3://b/job.py",
            entry_point_arguments=(),
            spark_submit_parameters=None,
        )


def test_emr_boto_config_pins_timeout_and_retry_shape() -> None:
    """Pin the explicit ``BotoConfig`` the EMR client opens with —
    matching ``infra/aws_session.py`` / ``domain/s3_fs.py``. Without
    this config the aioboto3 client falls back to boto3 defaults
    (60-s connect, legacy retries) and the EMR pollers stack
    overlapping ``list_*`` calls on a flaky network."""
    assert _EMR_BOTO_CONFIG.connect_timeout == 10
    assert _EMR_BOTO_CONFIG.read_timeout == 60
    assert _EMR_BOTO_CONFIG.retries == {"max_attempts": 6, "mode": "adaptive"}
    assert EMR_BOTO_CONFIG is _EMR_BOTO_CONFIG
