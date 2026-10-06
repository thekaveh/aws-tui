"""Tests for EMR Serverless domain data records.

These are plain frozen dataclasses + StrEnums; the tests pin field
order, immutability, and the canonical state enums so VMs above
can pattern-match on stable values."""

from __future__ import annotations

import asyncio
import json
from dataclasses import FrozenInstanceError, replace
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import aioboto3
import botocore.exceptions
import pytest
from aiobotocore.awsrequest import AioAWSResponse
from aiobotocore.httpsession import AIOHTTPSession

from aws_tui.demo.in_memory_emr import InMemoryEmr as _InMemoryEmr
from aws_tui.domain import emr_serverless as emr_domain
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
            botocore.exceptions.PartialCredentialsError(
                provider="shared-credentials",
                cred_var="aws_secret_access_key",
            ),
            AuthRequiredError,
        ),
        (botocore.exceptions.ProfileNotFound(profile="missing"), AuthRequiredError),
        (
            botocore.exceptions.TokenRetrievalError(provider="sso", error_msg="token expired"),
            AuthRequiredError,
        ),
        (
            botocore.exceptions.CredentialRetrievalError(
                provider="credential-process",
                error_msg="credential process failed",
            ),
            AuthRequiredError,
        ),
        (botocore.exceptions.SSOTokenLoadError(error_msg="expired"), AuthRequiredError),
        (botocore.exceptions.UnauthorizedSSOTokenError(), AuthRequiredError),
        (botocore.exceptions.NoAuthTokenError(), AuthRequiredError),
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
        (_client_error("ExpiredToken"), AuthRequiredError),
        (_client_error("InvalidClientTokenId"), AuthRequiredError),
        (_client_error("UnrecognizedClientException"), AuthRequiredError),
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


def test_credential_retrieval_error_is_auth_required_without_raw_stderr() -> None:
    exc = botocore.exceptions.CredentialRetrievalError(
        provider="credential-process",
        error_msg="SECRET_TOKEN=leaked",
    )

    mapped = _map_boto_error(exc)

    assert isinstance(mapped, AuthRequiredError)
    msg = str(mapped)
    assert "credential process failed" in msg
    assert "SECRET_TOKEN" not in msg
    assert "leaked" not in msg


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


class _CredentialRetrievalContextFailure:
    async def __aenter__(self) -> _CredentialRetrievalContextFailure:
        raise botocore.exceptions.CredentialRetrievalError(
            provider="credential-process",
            error_msg="SECRET_TOKEN=leaked",
        )

    async def __aexit__(self, *args: object) -> None:
        return None


class _ContextFailureSession:
    def client(self, service_name: str, **kwargs: object) -> _CredentialRetrievalContextFailure:
        assert service_name == "emr-serverless"
        assert "config" in kwargs
        return _CredentialRetrievalContextFailure()


@pytest.mark.asyncio
async def test_list_applications_maps_client_context_auth_failure() -> None:
    client = EmrServerlessClient(session=_ContextFailureSession())  # type: ignore[arg-type]

    with pytest.raises(AuthRequiredError) as exc_info:
        await client.list_applications()

    msg = str(exc_info.value)
    assert "credential process failed" in msg
    assert "SECRET_TOKEN" not in msg
    assert "leaked" not in msg


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
    stub.list_applications.assert_awaited_once_with(maxResults=50)


@pytest.mark.asyncio
async def test_list_applications_rejects_repeated_pagination_token() -> None:
    stub = _StubClient()
    stub.list_applications.return_value = {"applications": [], "nextToken": "same"}
    client = EmrServerlessClient(session=_StubSession(stub))  # type: ignore[arg-type]

    with pytest.raises(ProviderError, match="repeated an application continuation token"):
        await client.list_applications()
    assert stub.list_applications.await_count == 2


@pytest.mark.asyncio
async def test_list_applications_rejects_pagination_beyond_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from aws_tui.domain import emr_serverless

    stub = _StubClient()
    stub.list_applications.side_effect = [
        {"applications": [], "nextToken": "page-2"},
        {"applications": []},
    ]
    monkeypatch.setattr(emr_serverless, "_MAX_EMR_LISTING_PAGES", 1, raising=False)
    client = EmrServerlessClient(session=_StubSession(stub))  # type: ignore[arg-type]

    with pytest.raises(ProviderError, match="application pagination safety limit"):
        await client.list_applications()

    assert stub.list_applications.await_count == 1


@pytest.mark.asyncio
async def test_list_applications_rejects_collection_beyond_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from aws_tui.domain import emr_serverless

    stub = _StubClient()
    stub.list_applications.return_value = _fake_app_response(
        [
            {
                "id": f"app-{index}",
                "state": "STARTED",
                "createdAt": datetime(2026, 6, 25, tzinfo=UTC),
            }
            for index in range(2)
        ]
    )
    monkeypatch.setattr(emr_serverless, "_MAX_EMR_APPLICATIONS", 1, raising=False)
    client = EmrServerlessClient(session=_StubSession(stub))  # type: ignore[arg-type]

    with pytest.raises(ProviderError, match="application collection safety limit"):
        await client.list_applications()


@pytest.mark.asyncio
async def test_bulk_job_runs_rejects_pagination_beyond_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from aws_tui.domain import emr_serverless

    stub = _StubClient()
    stub.list_job_runs.side_effect = [
        {"jobRuns": [], "nextToken": "page-2"},
        {"jobRuns": []},
    ]
    monkeypatch.setattr(emr_serverless, "_MAX_EMR_LISTING_PAGES", 1, raising=False)
    client = EmrServerlessClient(session=_StubSession(stub))  # type: ignore[arg-type]

    with pytest.raises(ProviderError, match="job-run pagination safety limit"):
        await client.list_job_runs("app-1")

    assert stub.list_job_runs.await_count == 1


@pytest.mark.asyncio
async def test_list_job_runs_passes_multi_state_filter_to_service() -> None:
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
    assert set(stub.list_job_runs.call_args.kwargs["states"]) == {"SUCCESS", "RUNNING"}
    assert stub.list_job_runs.call_args.kwargs["maxResults"] == 50


@pytest.mark.asyncio
async def test_list_job_runs_rejects_repeated_pagination_token() -> None:
    stub = _StubClient()
    stub.list_job_runs.return_value = {"jobRuns": [], "nextToken": "same"}
    client = EmrServerlessClient(session=_StubSession(stub))  # type: ignore[arg-type]

    with pytest.raises(ProviderError, match="repeated a job-run continuation token"):
        await client.list_job_runs("00abc")
    assert stub.list_job_runs.await_count == 2


@pytest.mark.asyncio
async def test_list_job_runs_omits_all_states_filter() -> None:
    stub = _StubClient()
    stub.list_job_runs.return_value = {"jobRuns": []}
    client = EmrServerlessClient(session=_StubSession(stub))  # type: ignore[arg-type]

    await client.list_job_runs("00abc", states=set(JobRunState))

    assert "states" not in stub.list_job_runs.call_args.kwargs


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
        client_token="tok-nightly-1",
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
    assert kwargs["clientToken"] == "tok-nightly-1"


@pytest.mark.asyncio
async def test_start_job_run_omits_name_and_spark_params_when_unset() -> None:
    """``name`` is optional in the boto3 contract — it must be absent
    from the kwargs when the modal leaves the field blank.
    ``sparkSubmitParameters`` is also optional; None omits the key.
    Supplied strings are preserved exactly, covered separately."""
    stub = _StubClient()
    stub.start_job_run.return_value = {"jobRunId": "jr-new-2"}
    client = EmrServerlessClient(session=_StubSession(stub))  # type: ignore[arg-type]
    await client.start_job_run(
        "00abc",
        execution_role_arn="arn:aws:iam::123456789012:role/Role",
        entry_point="s3://b/job.py",
        entry_point_arguments=(),
        spark_submit_parameters=None,
        client_token="tok",
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
            client_token="tok",
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
        client_token="tok",
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
            client_token="tok",
        )


def test_emr_boto_config_pins_timeout_and_retry_shape() -> None:
    """Pin the explicit ``BotoConfig`` the EMR client opens with —
    matching ``infra/aws_session.py`` / ``domain/s3_fs.py``. Without
    this config the aioboto3 client falls back to boto3 defaults
    (60-s connect, legacy retries) and the EMR pollers stack
    overlapping ``list_*`` calls on a flaky network."""
    assert _EMR_BOTO_CONFIG.connect_timeout == 10
    assert _EMR_BOTO_CONFIG.read_timeout == 60
    assert _EMR_BOTO_CONFIG.retries == {"total_max_attempts": 6, "mode": "adaptive"}
    assert EMR_BOTO_CONFIG is _EMR_BOTO_CONFIG


@pytest.mark.asyncio
async def test_list_job_runs_page_forwards_token_and_maps_the_page() -> None:
    """The real client's paging method had no direct test at all — 26/26 of its
    mutants survived. The VM tests use fakes, and the domain tests only covered
    the bulk `list_job_runs`. Dropping the `nextToken` forwarding made the Job
    Runs pane re-fetch page 1 forever — the exact user complaint quoted in the
    method's own docstring.
    """
    stub = _StubClient()
    stub.list_job_runs.return_value = {
        "jobRuns": [
            {
                "applicationId": "app-1",
                "id": "run-51",
                "name": "nightly",
                "state": "SUCCESS",
                "createdAt": datetime(2026, 6, 25, tzinfo=UTC),
                "updatedAt": datetime(2026, 6, 25, 1, tzinfo=UTC),
            }
        ],
        "nextToken": "page-3",
    }
    client = EmrServerlessClient(session=_StubSession(stub))  # type: ignore[arg-type]

    runs, token = await client.list_job_runs_page("app-1", start_token="page-2")

    assert [run.job_run_id for run in runs] == ["run-51"]
    assert token == "page-3", "the next page's token was not surfaced"
    stub.list_job_runs.assert_awaited_once_with(
        applicationId="app-1", maxResults=50, nextToken="page-2"
    )


@pytest.mark.asyncio
async def test_list_job_runs_page_first_page_omits_the_token() -> None:
    """Positive control: no `nextToken` key on the first request."""
    stub = _StubClient()
    stub.list_job_runs.return_value = {"jobRuns": []}
    client = EmrServerlessClient(session=_StubSession(stub))  # type: ignore[arg-type]

    runs, token = await client.list_job_runs_page("app-1")

    assert runs == []
    assert token is None
    stub.list_job_runs.assert_awaited_once_with(applicationId="app-1", maxResults=50)


@pytest.mark.asyncio
async def test_in_memory_emr_reuses_the_run_for_a_repeated_client_token() -> None:
    """Mirror AWS: the same token returns the same job run, no second run."""
    fake = _InMemoryEmr()
    fake.add_application(app_id="00abc", name="etl")
    kwargs = dict(
        execution_role_arn="arn:aws:iam::123456789012:role/EmrJobRole",
        entry_point="s3://b/job.py",
        entry_point_arguments=(),
        spark_submit_parameters=None,
        client_token="tok-same",
    )
    first = await fake.start_job_run("00abc", **kwargs)
    second = await fake.start_job_run("00abc", **kwargs)
    assert first == second
    assert len([c for c in fake.calls if c[0] == "start_job_run"]) == 2
    runs = await fake.list_job_runs("00abc")
    assert len(runs) == 1


def _clone_source_response() -> dict:
    return {
        "applicationId": "00abc",
        "jobRunId": "jr-source",
        "name": "nightly",
        "state": "SUCCESS",
        "createdAt": datetime(2026, 6, 25, tzinfo=UTC),
        "updatedAt": datetime(2026, 6, 25, tzinfo=UTC),
        "executionRole": "arn:aws:iam::123456789012:role/EmrJobRole",
        "jobDriver": {
            "sparkSubmit": {
                "entryPoint": "s3://b/job.py",
                "entryPointArguments": ["", "a\nb", "  padded  "],
                "sparkSubmitParameters": "  --conf k=v  ",
            }
        },
        "configurationOverrides": {
            "applicationConfiguration": [
                {
                    "classification": "spark-defaults",
                    "properties": {"spark.executor.instances": "4"},
                    "configurations": [{"classification": "nested", "properties": {"k": "v"}}],
                }
            ],
            "monitoringConfiguration": {
                "s3MonitoringConfiguration": {
                    "logUri": "s3://logs/jobs/",
                    "encryptionKeyArn": "key",
                },
                "cloudWatchLoggingConfiguration": {"enabled": True, "logGroupName": "group"},
                "managedPersistenceMonitoringConfiguration": {"enabled": False},
                "prometheusMonitoringConfiguration": {"remoteWriteUrl": "https://metrics.example"},
            },
        },
        "executionTimeoutMinutes": 0,
        "mode": "STREAMING",
        "retryPolicy": {"maxFailedAttemptsPerHour": 2},
        "executionIamPolicy": {"policyArns": ["arn:aws:iam::123456789012:policy/JobPolicy"]},
        "tags": {"team": "analytics"},
        "releaseLabel": "emr-7.10.0",
        "networkConfiguration": {"subnetIds": ["subnet-one"]},
        "imageConfiguration": {"imageUri": "image:tag"},
        "workerTypeSpecifications": {
            "SparkDriver": {"imageConfiguration": {"imageUri": "driver:tag"}}
        },
    }


async def test_get_job_run_preserves_clone_configuration_without_aliasing() -> None:
    source = _clone_source_response()
    stub = _StubClient()
    stub.get_job_run.return_value = {"jobRun": source}
    client = EmrServerlessClient(session=_StubSession(stub))  # type: ignore[arg-type]
    detail = await client.get_job_run("00abc", "jr-source")
    assert detail.configuration_overrides == source["configurationOverrides"]
    assert detail.execution_timeout_minutes == 0
    assert detail.retry_policy == source["retryPolicy"]
    assert detail.mode == "STREAMING"
    assert detail.execution_iam_policy == source["executionIamPolicy"]
    assert detail.tags == source["tags"]
    assert detail.job_driver == source["jobDriver"]
    assert detail.source_application_settings == {
        key: source[key]
        for key in (
            "releaseLabel",
            "networkConfiguration",
            "imageConfiguration",
            "workerTypeSpecifications",
        )
    }
    assert detail.s3_monitoring_log_uri == "s3://logs/jobs/"
    source["configurationOverrides"]["applicationConfiguration"][0]["properties"][
        "spark.executor.instances"
    ] = "99"
    source["executionIamPolicy"]["policyArns"].append("changed")
    source["networkConfiguration"]["subnetIds"].append("changed")
    source["jobDriver"]["sparkSubmit"]["entryPointArguments"].append("changed")
    assert (
        detail.configuration_overrides["applicationConfiguration"][0]["properties"][
            "spark.executor.instances"
        ]
        == "4"
    )
    assert len(detail.execution_iam_policy["policyArns"]) == 1
    assert detail.source_application_settings["networkConfiguration"]["subnetIds"] == ["subnet-one"]
    assert detail.job_driver["sparkSubmit"]["entryPointArguments"] == ["", "a\nb", "  padded  "]


@pytest.mark.parametrize(
    "driver",
    [
        None,
        {},
        {"hive": {"query": "s3://b/query.hql"}},
        {"future": {"config": "value"}},
        {"sparkSubmit": {"entryPoint": "s3://b/job.py"}, "hive": {}},
    ],
)
async def test_get_job_run_retains_unsupported_driver_for_clone_refusal(
    driver: dict | None,
) -> None:
    source = _clone_source_response()
    if driver is None:
        del source["jobDriver"]
    else:
        source["jobDriver"] = driver
    stub = _StubClient()
    stub.get_job_run.return_value = {"jobRun": source}
    client = EmrServerlessClient(session=_StubSession(stub))  # type: ignore[arg-type]
    detail = await client.get_job_run("00abc", "jr-source")
    assert detail.job_driver == driver


@pytest.mark.parametrize("present", [False, True])
async def test_get_job_run_distinguishes_missing_and_empty_settings(present: bool) -> None:
    source = _clone_source_response()
    for key in ["configurationOverrides", "retryPolicy", "executionIamPolicy", "tags"]:
        if present:
            source[key] = {}
        else:
            del source[key]
    source.pop("executionTimeoutMinutes")
    source.pop("mode")
    stub = _StubClient()
    stub.get_job_run.return_value = {"jobRun": source}
    detail = await EmrServerlessClient(session=_StubSession(stub)).get_job_run("00abc", "jr-source")  # type: ignore[arg-type]
    for value in [
        detail.configuration_overrides,
        detail.retry_policy,
        detail.execution_iam_policy,
        detail.tags,
    ]:
        assert value == ({} if present else None)
    assert detail.execution_timeout_minutes is None
    assert detail.mode is None


async def test_job_detail_repr_does_not_include_clone_values() -> None:
    source = _clone_source_response()
    stub = _StubClient()
    stub.get_job_run.return_value = {"jobRun": source}
    detail = await EmrServerlessClient(session=_StubSession(stub)).get_job_run("00abc", "jr-source")  # type: ignore[arg-type]
    rendered = repr(detail)
    for sensitive in ["padded", "spark.executor.instances", "JobPolicy", "--conf", "s3://b/job.py"]:
        assert sensitive not in rendered


@pytest.mark.parametrize("mode", ["BATCH", "STREAMING"])
async def test_start_job_run_preserves_complete_clone_request(mode: str) -> None:
    source = _clone_source_response()
    overrides = source["configurationOverrides"]
    overrides["monitoringConfiguration"]["s3MonitoringConfiguration"]["encryptionKeyArn"] = (
        "arn:aws:kms:us-east-1:123456789012:key/example"
    )
    stub = _StubClient()
    stub.start_job_run.return_value = {"jobRunId": "jr-clone"}
    client = EmrServerlessClient(session=_StubSession(stub))  # type: ignore[arg-type]
    assert (
        await client.start_job_run(
            "00abc",
            execution_role_arn=source["executionRole"],
            entry_point="s3://b/job.py",
            entry_point_arguments=("", "a\nb", "  padded  "),
            spark_submit_parameters="  --conf k=v  ",
            client_token="clone-intent",
            configuration_overrides=overrides,
            execution_timeout_minutes=0,
            retry_policy=source["retryPolicy"],
            mode=mode,
            execution_iam_policy=source["executionIamPolicy"],
            tags=source["tags"],
        )
        == "jr-clone"
    )
    kwargs = stub.start_job_run.await_args.kwargs
    assert kwargs == {
        "applicationId": "00abc",
        "executionRoleArn": source["executionRole"],
        "jobDriver": source["jobDriver"],
        "clientToken": "clone-intent",
        "configurationOverrides": overrides,
        "executionTimeoutMinutes": 0,
        "retryPolicy": source["retryPolicy"],
        "mode": mode,
        "executionIamPolicy": source["executionIamPolicy"],
        "tags": source["tags"],
    }
    kwargs["configurationOverrides"]["applicationConfiguration"][0]["properties"][
        "spark.executor.instances"
    ] = "99"
    kwargs["executionIamPolicy"]["policyArns"].append("changed")
    kwargs["tags"]["team"] = "changed"
    assert overrides["applicationConfiguration"][0]["properties"]["spark.executor.instances"] == "4"
    assert len(source["executionIamPolicy"]["policyArns"]) == 1
    assert source["tags"] == {"team": "analytics"}


@pytest.mark.parametrize("parameters", ["   ", "\n\t", "  --conf key=value\n"])
async def test_start_job_run_preserves_spark_parameter_whitespace(parameters: str) -> None:
    stub = _StubClient()
    stub.start_job_run.return_value = {"jobRunId": "jr-clone"}
    client = EmrServerlessClient(session=_StubSession(stub))  # type: ignore[arg-type]
    await client.start_job_run(
        "00abc",
        execution_role_arn="role",
        entry_point="s3://b/job.py",
        entry_point_arguments=(),
        spark_submit_parameters=parameters,
        client_token="clone-intent",
    )
    assert (
        stub.start_job_run.await_args.kwargs["jobDriver"]["sparkSubmit"]["sparkSubmitParameters"]
        == parameters
    )


@pytest.mark.parametrize(
    "settings",
    [
        {"mode": "FUTURE"},
        {"execution_timeout_minutes": -1},
        {"execution_timeout_minutes": True},
        {"execution_timeout_minutes": "secret-duration"},
        {"retry_policy": {"maxAttempts": "secret-attempt"}},
        {
            "configuration_overrides": {
                "diskEncryptionConfiguration": {"encryptionKeyArn": "secret-key"}
            }
        },
        {"configuration_overrides": {"monitoringConfiguration": {"unknown": "secret-monitor"}}},
        {"execution_iam_policy": {"policy": 123}},
        {"execution_iam_policy": {"unknown": "secret-policy"}},
        {"tags": {"team": ["secret-tag"]}},
    ],
)
async def test_start_job_run_blocks_unsupported_settings_without_reduced_request(
    settings: dict,
) -> None:
    stub = _StubClient()
    client = EmrServerlessClient(session=_StubSession(stub))  # type: ignore[arg-type]
    with pytest.raises(ValidationError) as caught:
        await client.start_job_run(
            "00abc",
            execution_role_arn="role",
            entry_point="s3://b/job.py",
            entry_point_arguments=(),
            spark_submit_parameters=None,
            client_token="clone-intent",
            **settings,
        )
    stub.start_job_run.assert_not_awaited()
    assert "secret-" not in str(caught.value)
    assert "FUTURE" not in str(caught.value)


async def test_start_job_run_keeps_explicit_empty_optional_blocks() -> None:
    stub = _StubClient()
    stub.start_job_run.return_value = {"jobRunId": "jr-clone"}
    client = EmrServerlessClient(session=_StubSession(stub))  # type: ignore[arg-type]
    await client.start_job_run(
        "00abc",
        execution_role_arn="role",
        entry_point="s3://b/job.py",
        entry_point_arguments=(),
        spark_submit_parameters=None,
        client_token="clone-intent",
        configuration_overrides={},
        retry_policy={},
        execution_iam_policy={},
        tags={},
    )
    assert stub.start_job_run.await_args.kwargs == {
        "applicationId": "00abc",
        "executionRoleArn": "role",
        "jobDriver": {"sparkSubmit": {"entryPoint": "s3://b/job.py", "entryPointArguments": []}},
        "clientToken": "clone-intent",
        "configurationOverrides": {},
        "retryPolicy": {},
        "executionIamPolicy": {},
        "tags": {},
    }


async def test_demo_clone_preserves_optional_settings_and_monitoring() -> None:
    source = _clone_source_response()
    source["configurationOverrides"]["monitoringConfiguration"]["s3MonitoringConfiguration"][
        "encryptionKeyArn"
    ] = "arn:aws:kms:us-east-1:123456789012:key/example"
    fake = _InMemoryEmr()
    fake.add_application(app_id="00abc", name="etl")
    try:
        new_id = await fake.start_job_run(
            "00abc",
            execution_role_arn=source["executionRole"],
            entry_point="s3://b/job.py",
            entry_point_arguments=("", "a\nb", "  padded  "),
            spark_submit_parameters="  --conf k=v  ",
            client_token="clone-intent",
            configuration_overrides=source["configurationOverrides"],
            execution_timeout_minutes=0,
            retry_policy=source["retryPolicy"],
            mode="STREAMING",
            execution_iam_policy=source["executionIamPolicy"],
            tags=source["tags"],
        )
        detail = await fake.get_job_run("00abc", new_id)
        assert detail.job_driver == source["jobDriver"]
        assert detail.configuration_overrides == source["configurationOverrides"]
        assert detail.execution_timeout_minutes == 0
        assert detail.retry_policy == source["retryPolicy"]
        assert detail.mode == "STREAMING"
        assert detail.execution_iam_policy == source["executionIamPolicy"]
        assert detail.tags == source["tags"]
        assert detail.s3_monitoring_log_uri == "s3://logs/jobs/"
        source["tags"]["team"] = "changed"
        assert detail.tags == {"team": "analytics"}
    finally:
        fake.dispose()


async def test_demo_seed_records_spark_driver_and_source_monitoring() -> None:
    fake = _InMemoryEmr()
    fake.add_application(app_id="00abc", name="etl")
    detail = fake.add_job_run_detail(
        application_id="00abc",
        job_run_id="r-source",
        entry_point="s3://b/job.py",
        entry_point_arguments=("", "arg"),
        spark_submit_parameters="  --conf k=v  ",
        s3_monitoring_log_uri="s3://logs/source/",
    )
    assert detail.job_driver == {
        "sparkSubmit": {
            "entryPoint": "s3://b/job.py",
            "entryPointArguments": ["", "arg"],
            "sparkSubmitParameters": "  --conf k=v  ",
        }
    }
    assert detail.configuration_overrides == {
        "monitoringConfiguration": {"s3MonitoringConfiguration": {"logUri": "s3://logs/source/"}}
    }


@pytest.mark.parametrize("kind", ["validation", "credentials", "transport", "unexpected"])
async def test_clone_failures_do_not_expose_argument_values_in_logs_or_diagnostics(
    kind: str, caplog
) -> None:
    import logging

    from aws_tui.infra.log_sink import _JsonLineFormatter

    argument = "ARG_SENTINEL_238_PRIVATE"
    credential = "CREDENTIAL_SENTINEL_238_PRIVATE"
    echoed = f"request failed with {argument} and {credential}"
    errors = {
        "validation": botocore.exceptions.ClientError(
            {"Error": {"Code": "ValidationException", "Message": echoed}}, "StartJobRun"
        ),
        "credentials": botocore.exceptions.CredentialRetrievalError(
            provider="process", error_msg=echoed
        ),
        "transport": botocore.exceptions.EndpointConnectionError(
            endpoint_url=f"https://{argument}.example/{credential}"
        ),
        "unexpected": RuntimeError(echoed),
    }
    stub = _StubClient()
    stub.start_job_run.side_effect = errors[kind]
    client = EmrServerlessClient(session=_StubSession(stub))  # type: ignore[arg-type]
    logger = logging.getLogger("aws_tui.tests.clone_diagnostics")
    with caplog.at_level(logging.DEBUG):
        try:
            await client.start_job_run(
                "00abc",
                execution_role_arn="role",
                entry_point="s3://b/job.py",
                entry_point_arguments=(argument,),
                spark_submit_parameters=credential,
                client_token="clone-intent",
            )
        except ProviderError as exc:
            logger.error("clone submission failed", exc_info=True)
            message = str(exc)
        else:
            pytest.fail("the simulated provider error must propagate")
    assert argument not in message
    assert credential not in message
    assert caplog.records
    diagnostics = "\n".join(_JsonLineFormatter().format(record) for record in caplog.records)
    assert argument not in diagnostics
    assert credential not in diagnostics
    assert argument not in caplog.text
    assert credential not in caplog.text


@pytest.mark.parametrize("present", [False, True])
async def test_clone_distinguishes_omitted_and_empty_arguments(present: bool) -> None:
    source = _clone_source_response()
    spark = {"entryPoint": "s3://b/job.py"}
    if present:
        spark["entryPointArguments"] = []
    source["jobDriver"] = {"sparkSubmit": spark}
    stub = _StubClient()
    stub.get_job_run.return_value = {"jobRun": source}
    stub.start_job_run.return_value = {"jobRunId": "jr-clone"}
    client = EmrServerlessClient(session=_StubSession(stub))
    detail = await client.get_job_run("00abc", "r-001")
    assert detail.entry_point_arguments == (() if present else None)
    await client.start_job_run(
        "00abc",
        execution_role_arn=source["executionRole"],
        entry_point=detail.entry_point,
        entry_point_arguments=detail.entry_point_arguments,
        spark_submit_parameters=detail.spark_submit_parameters,
        client_token="omission-intent",
    )
    assert stub.start_job_run.await_args.kwargs["jobDriver"] == source["jobDriver"]


# Cancellation is a single mutation attempt, including inside the SDK transport.
def _cancel_boundary(raised: BaseException | None = None):
    wire = SimpleNamespace(
        cancel_job_run=AsyncMock(
            return_value={"applicationId": "a1", "jobRunId": "r1"},
            side_effect=raised,
        )
    )
    manager = AsyncMock()
    manager.__aenter__.return_value = wire
    session = Mock()
    session.client.return_value = manager
    client = EmrServerlessClient(session=session, region_name="us-east-1")
    return client, session, manager, wire


def test_cancellable_job_run_states_contract() -> None:
    assert (
        frozenset(
            {
                JobRunState.SUBMITTED,
                JobRunState.PENDING,
                JobRunState.SCHEDULED,
                JobRunState.QUEUED,
                JobRunState.RUNNING,
            }
        )
        == emr_domain.CANCELLABLE_JOB_RUN_STATES
    )
    assert isinstance(emr_domain.CANCELLABLE_JOB_RUN_STATES, frozenset)
    assert callable(emr_domain.EmrServerlessClientProtocol.cancel_job_run)


@pytest.mark.asyncio
async def test_cancel_job_run_exact_wire_config_and_cleanup() -> None:
    client, session, manager, wire = _cancel_boundary()
    assert await client.cancel_job_run("a1", "r1") is None
    wire.cancel_job_run.assert_awaited_once_with(applicationId="a1", jobRunId="r1")
    session.client.assert_called_once_with(
        "emr-serverless",
        region_name="us-east-1",
        config=emr_domain.EMR_CANCEL_BOTO_CONFIG,
    )
    config = session.client.call_args.kwargs["config"]
    assert config.retries == {"total_max_attempts": 1, "mode": "standard"}
    assert (config.connect_timeout, config.read_timeout) == (10, 60)
    assert EMR_BOTO_CONFIG.retries == {"total_max_attempts": 6, "mode": "adaptive"}
    manager.__aexit__.assert_awaited_once()


@pytest.mark.parametrize(
    ("raised", "expected"),
    [
        (_client_error("AccessDeniedException", "CancelJobRun"), PermissionDeniedError),
        (_client_error("ResourceNotFoundException", "CancelJobRun"), NotFoundError),
        (_client_error("ThrottlingException", "CancelJobRun"), ThrottledError),
        (_client_error("ValidationException", "CancelJobRun"), ValidationError),
        (botocore.exceptions.NoCredentialsError(), AuthRequiredError),
        (
            botocore.exceptions.PartialCredentialsError(provider="test", cred_var="secret"),
            AuthRequiredError,
        ),
        (botocore.exceptions.ProfileNotFound(profile="missing"), AuthRequiredError),
        (
            botocore.exceptions.TokenRetrievalError(provider="sso", error_msg="expired"),
            AuthRequiredError,
        ),
        (
            botocore.exceptions.CredentialRetrievalError(provider="test", error_msg="failed"),
            AuthRequiredError,
        ),
        (botocore.exceptions.SSOTokenLoadError(error_msg="expired"), AuthRequiredError),
        (botocore.exceptions.UnauthorizedSSOTokenError(), AuthRequiredError),
        (botocore.exceptions.NoAuthTokenError(), AuthRequiredError),
        (botocore.exceptions.EndpointConnectionError(endpoint_url="x"), ProviderUnreachableError),
        (
            botocore.exceptions.EndpointResolutionError(msg="missing endpoint"),
            ProviderUnreachableError,
        ),
        (botocore.exceptions.ConnectTimeoutError(endpoint_url="x"), ProviderUnreachableError),
        (botocore.exceptions.ReadTimeoutError(endpoint_url="x"), ProviderUnreachableError),
        (botocore.exceptions.ConnectionError(error="offline"), ProviderUnreachableError),
        (botocore.exceptions.ConnectionClosedError(endpoint_url="x"), ProviderUnreachableError),
        (
            botocore.exceptions.ResponseStreamingError(error="broken stream"),
            ProviderUnreachableError,
        ),
        (
            botocore.exceptions.IncompleteReadError(actual_bytes=1, expected_bytes=2),
            ProviderUnreachableError,
        ),
        (botocore.exceptions.ProxyConnectionError(proxy_url="x"), ProviderUnreachableError),
        (botocore.exceptions.SSLError(endpoint_url="x", error="tls"), ProviderUnreachableError),
        (botocore.exceptions.ParamValidationError(report="bad parameter"), ValidationError),
        (RuntimeError("unrelated"), RuntimeError),
        (asyncio.CancelledError(), asyncio.CancelledError),
    ],
)
@pytest.mark.asyncio
async def test_cancel_job_run_maps_errors_once_and_closes_context(raised, expected) -> None:
    client, _session, manager, wire = _cancel_boundary(raised)
    with pytest.raises(expected) as caught:
        await client.cancel_job_run("a1", "r1")
    if isinstance(raised, RuntimeError | asyncio.CancelledError):
        assert caught.value is raised
    else:
        assert caught.value.__cause__ is raised
    wire.cancel_job_run.assert_awaited_once_with(applicationId="a1", jobRunId="r1")
    manager.__aexit__.assert_awaited_once()
    assert manager.__aexit__.call_args.args[1] is raised


@pytest.mark.asyncio
async def test_cancel_job_run_maps_context_entry_credentials_failure() -> None:
    client = EmrServerlessClient(session=_ContextFailureSession())
    with pytest.raises(AuthRequiredError, match="credential process failed"):
        await client.cancel_job_run("a1", "r1")


@pytest.mark.parametrize(
    ("failure", "expected"),
    [
        ("server", ProviderError),
        ("throttle", ThrottledError),
        ("connect", ProviderUnreachableError),
        ("read", ProviderUnreachableError),
    ],
)
@pytest.mark.asyncio
async def test_cancel_job_run_sdk_http_boundary_has_one_attempt(
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
    expected: type[ProviderError],
) -> None:
    requests = []

    async def send(_http_session, request):
        requests.append(request)
        if failure == "connect":
            raise botocore.exceptions.EndpointConnectionError(endpoint_url=request.url)
        if failure == "read":
            raise botocore.exceptions.ReadTimeoutError(endpoint_url=request.url)
        status, code = (
            (500, "InternalServerException")
            if failure == "server"
            else (429, "ThrottlingException")
        )
        body = json.dumps({"message": "synthetic error"}).encode()
        return AioAWSResponse(
            request.url,
            status,
            {
                "content-type": "application/json",
                "x-amzn-errortype": code,
            },
            SimpleNamespace(read=AsyncMock(return_value=body)),
        )

    monkeypatch.setattr(AIOHTTPSession, "send", send)
    exits = []
    original_exit = AIOHTTPSession.__aexit__

    async def observed_exit(http_session, *args):
        exits.append(args)
        await original_exit(http_session, *args)

    monkeypatch.setattr(AIOHTTPSession, "__aexit__", observed_exit)
    session = aioboto3.Session(
        aws_access_key_id="fake-access",
        aws_secret_access_key="fake-secret",
        aws_session_token="fake-token",
        region_name="us-east-1",
    )
    client = EmrServerlessClient(session=session, region_name="us-east-1")
    with pytest.raises(expected):
        await client.cancel_job_run("a1", "r1")
    assert len(requests) == 1
    assert requests[0].method == "DELETE"
    assert requests[0].url.endswith("/applications/a1/jobruns/r1")
    assert requests[0].body in (None, b"", "")
    assert len(exits) == 1


@pytest.mark.parametrize(
    "state",
    [
        JobRunState.SUBMITTED,
        JobRunState.PENDING,
        JobRunState.SCHEDULED,
        JobRunState.QUEUED,
        JobRunState.RUNNING,
        JobRunState.CANCELLING,
    ],
)
@pytest.mark.asyncio
async def test_demo_cancel_changes_only_selected_records_and_preserves_payload(state) -> None:
    fake = _InMemoryEmr()
    fake.add_application(app_id="a1", name="selected")
    other_app = fake.add_application(app_id="a2", name="other")
    old_summary = fake.add_job_run(application_id="a1", job_run_id="r1", name="run", state=state)
    old_detail = fake.add_job_run_detail(
        application_id="a1",
        job_run_id="r1",
        entry_point_arguments=("arg",),
        spark_submit_parameters="--conf x=y",
        s3_monitoring_log_uri="s3://logs/prefix",
    )
    old_detail = replace(
        old_detail,
        execution_timeout_minutes=12,
        retry_policy={"maxAttempts": 2},
        mode="BATCH",
        execution_iam_policy={"policy": "value"},
        tags={"tag": "value"},
        source_application_settings={"releaseLabel": "emr-7.0.0"},
    )
    fake._details[("a1", "r1")] = old_detail
    other_summary = fake.add_job_run(
        application_id="a2", job_run_id="r1", state=JobRunState.RUNNING
    )
    other_detail = fake.add_job_run_detail(application_id="a2", job_run_id="r1")
    try:
        assert await fake.cancel_job_run("a1", "r1") is None
        assert ("cancel_job_run", ("a1", "r1")) in fake.calls
        new_summary = (await fake.list_job_runs("a1"))[0]
        new_detail = await fake.get_job_run("a1", "r1")
        assert new_summary.state is new_detail.state is JobRunState.CANCELLED
        assert old_summary.state is old_detail.state is state
        assert new_summary.updated_at == new_detail.updated_at > old_summary.updated_at
        assert new_detail.duration_ms == int(
            (new_detail.updated_at - new_detail.created_at).total_seconds() * 1000
        )
        assert replace(new_summary, state=state, updated_at=old_summary.updated_at) == old_summary
        assert (
            replace(
                new_detail,
                state=state,
                updated_at=old_detail.updated_at,
                duration_ms=old_detail.duration_ms,
            )
            == old_detail
        )
        assert (await fake.list_job_runs("a2"))[0] is other_summary
        assert await fake.get_job_run("a2", "r1") is other_detail
        assert other_app in await fake.list_applications()
    finally:
        await fake.aclose()


@pytest.mark.parametrize("state", [JobRunState.SUCCESS, JobRunState.FAILED, JobRunState.CANCELLED])
@pytest.mark.asyncio
async def test_demo_cancel_does_not_rewrite_terminal_records(state) -> None:
    fake = _InMemoryEmr()
    fake.add_application(app_id="a1", name="app")
    summary = fake.add_job_run(application_id="a1", job_run_id="r1", state=state)
    detail = fake.add_job_run_detail(application_id="a1", job_run_id="r1")
    assert await fake.cancel_job_run("a1", "r1") is None
    assert await fake.cancel_job_run("a1", "r1") is None
    assert (await fake.list_job_runs("a1"))[0] is summary
    assert await fake.get_job_run("a1", "r1") is detail
    await fake.aclose()


@pytest.mark.parametrize(("application_id", "job_run_id"), [("missing", "r1"), ("a1", "missing")])
@pytest.mark.asyncio
async def test_demo_cancel_missing_application_or_run_is_not_found(
    application_id, job_run_id
) -> None:
    fake = _InMemoryEmr()
    fake.add_application(app_id="a1", name="app")
    # An orphan seed still cannot make an unknown application valid.
    fake.add_job_run(application_id="missing", job_run_id="r1", state=JobRunState.RUNNING)
    with pytest.raises(NotFoundError):
        await fake.cancel_job_run(application_id, job_run_id)
    assert ("cancel_job_run", (application_id, job_run_id)) in fake.calls
    await fake.aclose()


@pytest.mark.parametrize(
    ("transition", "observed"),
    [
        (1, JobRunState.SUBMITTED),
        (2, JobRunState.SCHEDULED),
        (3, JobRunState.RUNNING),
    ],
)
@pytest.mark.asyncio
async def test_demo_cancel_cannot_be_resurrected_by_any_pending_transition(
    monkeypatch: pytest.MonkeyPatch,
    transition: int,
    observed: JobRunState,
) -> None:
    reached, release = asyncio.Event(), asyncio.Event()
    original_sleep = asyncio.sleep
    selected_task = None
    stages = {}

    async def controlled_sleep(delay):
        task = asyncio.current_task()
        if delay >= 1:
            stages[task] = stages.get(task, 0) + 1
            if task is selected_task and stages[task] == transition:
                reached.set()
                await release.wait()
        await original_sleep(0)

    monkeypatch.setattr(asyncio, "sleep", controlled_sleep)
    fake = _InMemoryEmr()
    fake.add_application(app_id="a1", name="selected")
    fake.add_application(app_id="a2", name="other")
    kwargs = dict(
        execution_role_arn="arn:aws:iam::123456789012:role/Job",
        entry_point="s3://bucket/job.py",
        entry_point_arguments=(),
        spark_submit_parameters=None,
    )
    try:
        run_id = await fake.start_job_run("a1", client_token="one", **kwargs)
        selected_task = next(iter(fake._state_tasks))
        other_run_id = await fake.start_job_run("a2", client_token="two", **kwargs)
        walk_tasks = tuple(fake._state_tasks)
        assert len(walk_tasks) == 2
        await reached.wait()
        assert (await fake.get_job_run("a1", run_id)).state is observed
        await fake.cancel_job_run("a1", run_id)
        release.set()
        await asyncio.gather(*walk_tasks)
        assert (await fake.get_job_run("a1", run_id)).state is JobRunState.CANCELLED
        assert (await fake.list_job_runs("a1"))[0].state is JobRunState.CANCELLED
        assert (await fake.get_job_run("a2", other_run_id)).state is JobRunState.SUCCESS
        assert all(task.done() and not task.cancelled() for task in walk_tasks)
    finally:
        release.set()
        await fake.aclose()
    assert not fake._state_tasks


@pytest.mark.parametrize(
    ("config", "expected"),
    [
        (None, None),
        ({"enabled": True}, (True, None, None)),
        ({"enabled": False, "logGroupName": "group"}, (False, "group", None)),
        (
            {"enabled": True, "logGroupName": "/custom/emr", "logStreamNamePrefix": "literal/"},
            (True, "/custom/emr", "literal/"),
        ),
        ({}, (None, None, None)),
        ({"enabled": "true"}, (None, None, None)),
        ({"enabled": True, "logGroupName": ""}, (None, None, None)),
        ({"enabled": True, "logGroupName": "invalid:group"}, (None, None, None)),
        ({"enabled": True, "logGroupName": "g" * 513}, (None, None, None)),
        ({"enabled": True, "logStreamNamePrefix": "invalid*prefix"}, (None, None, None)),
        ({"enabled": True, "logStreamNamePrefix": "😀" * 512}, (True, None, "😀" * 512)),
        ({"enabled": True, "logStreamNamePrefix": "\ud800"}, (None, None, None)),
        ([], (None, None, None)),
    ],
)
async def test_get_job_run_parses_cloudwatch_alongside_s3(config, expected) -> None:
    source = _clone_source_response()
    monitoring = source["configurationOverrides"]["monitoringConfiguration"]
    if config is None:
        monitoring.pop("cloudWatchLoggingConfiguration")
    else:
        monitoring["cloudWatchLoggingConfiguration"] = config
    stub = _StubClient()
    stub.get_job_run.return_value = {"jobRun": source}
    detail = await EmrServerlessClient(session=_StubSession(stub)).get_job_run("00abc", "jr-source")
    assert detail.s3_monitoring_log_uri == "s3://logs/jobs/"
    actual = detail.cloudwatch_monitoring
    assert (
        None
        if actual is None
        else (actual.enabled, actual.log_group_name, actual.log_stream_name_prefix)
    ) == expected
    assert detail.configuration_overrides == source["configurationOverrides"]
    monitoring["cloudWatchLoggingConfiguration"] = {"enabled": False}
    assert detail.configuration_overrides != source["configurationOverrides"]
