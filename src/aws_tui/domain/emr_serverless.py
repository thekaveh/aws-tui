"""EMR Serverless domain types — records + StrEnums.

The supported surface is read-mostly: list applications and job runs, inspect
job-run details and logs, clone an existing run through ``start_job_run``, and
request cancellation through ``cancel_job_run``.
The records here map boto3 ``EMRServerless`` responses into values consumed by
the viewmodels; generic submission remains deferred."""

from __future__ import annotations

import re
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import TYPE_CHECKING, Any, Protocol, cast

import botocore.exceptions
from botocore.config import Config as BotoConfig

from aws_tui.domain.aws_auth import AWS_AUTH_ERROR_CODES, AWS_CREDENTIAL_EXCEPTIONS
from aws_tui.domain.aws_transport import AWS_TRANSPORT_EXCEPTIONS
from aws_tui.domain.emr_job_request import build_start_job_run_request
from aws_tui.domain.filesystem import (
    AuthRequiredError,
    NotFoundError,
    PermissionDeniedError,
    ProviderError,
    ProviderUnreachableError,
    ThrottledError,
    ValidationError,
)

if TYPE_CHECKING:
    import aioboto3

# Same timeout + adaptive-retry shape ``infra/aws_session.py`` and
# ``domain/s3_fs.py`` use. Without an explicit config the aioboto3
# client falls back to boto3 defaults (60 s connect, legacy retries)
# — a flaky network would compound with the EMR-page pollers and
# stack overlapping ``list_*`` calls.
_EMR_BOTO_CONFIG: BotoConfig = BotoConfig(
    connect_timeout=10,
    read_timeout=60,
    retries={"total_max_attempts": 6, "mode": "adaptive"},
)
EMR_BOTO_CONFIG: BotoConfig = _EMR_BOTO_CONFIG

# This mutation must not be retried automatically, including by the SDK.
EMR_CANCEL_BOTO_CONFIG: BotoConfig = EMR_BOTO_CONFIG.merge(
    BotoConfig(retries={"total_max_attempts": 1, "mode": "standard"})
)


class ApplicationState(StrEnum):
    """Application lifecycle states per the boto3 enum.

    Mirrors the full ``ApplicationState`` shape in the botocore
    service model (``emr-serverless/2021-07-13/service-2.json``) so
    a freshly created application — which starts in ``CREATING`` —
    doesn't raise ``ValueError`` from the picker poller.

    See https://docs.aws.amazon.com/emr-serverless/latest/APIReference/
    """

    CREATING = "CREATING"
    CREATED = "CREATED"
    STARTING = "STARTING"
    STARTED = "STARTED"
    STOPPING = "STOPPING"
    STOPPED = "STOPPED"
    TERMINATED = "TERMINATED"


class JobRunState(StrEnum):
    """Job-run lifecycle states.

    Mirrors the full ``JobRunState`` shape in the botocore service
    model. ``SUBMITTED`` / ``QUEUED`` / ``SCHEDULED`` are the three
    pre-``RUNNING`` states a freshly submitted run cycles through;
    omitting them used to crash the 10-s ``set_interval`` poller
    within one tick of any new submission. ``CANCELLING`` is the
    transient state after a cancel request before ``CANCELLED`` is
    observed."""

    SUBMITTED = "SUBMITTED"
    PENDING = "PENDING"
    SCHEDULED = "SCHEDULED"
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"
    CANCELLING = "CANCELLING"
    CANCELLED = "CANCELLED"


CANCELLABLE_JOB_RUN_STATES: frozenset[JobRunState] = frozenset(
    {
        JobRunState.SUBMITTED,
        JobRunState.PENDING,
        JobRunState.SCHEDULED,
        JobRunState.QUEUED,
        JobRunState.RUNNING,
    }
)


@dataclass(frozen=True, slots=True)
class ApplicationSummary:
    """One row in the application picker dropdown."""

    id: str
    name: str
    state: ApplicationState
    type: str  # Service-reported application type, currently SPARK or HIVE.
    created_at: datetime


@dataclass(frozen=True, slots=True)
class JobRunSummary:
    """One row in the LEFT pane's job-runs list."""

    application_id: str
    job_run_id: str
    name: str | None
    state: JobRunState
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class CloudWatchLogConfiguration:
    """Reported monitoring configuration; unknown enablement never enables reads."""

    enabled: bool | None
    log_group_name: str | None = None
    log_stream_name_prefix: str | None = None


def parse_cloudwatch_monitoring(
    overrides: dict[str, Any] | None,
) -> CloudWatchLogConfiguration | None:
    if overrides is None:
        return None
    monitoring = overrides.get("monitoringConfiguration", {})
    if not isinstance(monitoring, dict):
        return CloudWatchLogConfiguration(None)
    config = monitoring.get("cloudWatchLoggingConfiguration")
    if config is None:
        return None
    if not isinstance(config, dict):
        return CloudWatchLogConfiguration(None)
    enabled = config.get("enabled")
    enabled = enabled if isinstance(enabled, bool) else None
    group = config.get("logGroupName")
    prefix = config.get("logStreamNamePrefix")
    for value, pattern in ((group, r"[.\-_/#A-Za-z0-9]+"), (prefix, r"[^:*]+")):
        if value is None:
            continue
        if not isinstance(value, str) or not 1 <= len(value) <= 512:
            return CloudWatchLogConfiguration(None)
        if re.fullmatch(pattern, value) is None:
            return CloudWatchLogConfiguration(None)
        try:
            value.encode("utf-8")
        except UnicodeError:
            return CloudWatchLogConfiguration(None)
    return CloudWatchLogConfiguration(enabled, group, prefix)


@dataclass(frozen=True, slots=True)
class JobRunDetail:
    """Full job-run view shown in the RIGHT pane.

    Superset of :class:`JobRunSummary` — the ``list_job_runs`` API
    returns only summaries; ``get_job_run`` is required to fill in
    the entry point, args, Spark params, IAM role, and timing
    fields below."""

    application_id: str
    job_run_id: str
    name: str | None
    state: JobRunState
    created_at: datetime
    updated_at: datetime
    entry_point: str | None = field(repr=False)
    entry_point_arguments: tuple[str, ...] | None = field(repr=False)
    spark_submit_parameters: str | None = field(repr=False)
    execution_role_arn: str
    duration_ms: int | None
    # Parsed from response ``configurationOverrides
    # .monitoringConfiguration.s3MonitoringConfiguration.logUri``.
    # ``None`` when the job didn't set up S3 log monitoring (no
    # monitoringConfiguration block, or no s3MonitoringConfiguration
    # block within it). The logs pane shows the NO_LOG_CONFIG
    # placeholder in that case.
    s3_monitoring_log_uri: str | None
    job_driver: dict[str, Any] | None = field(default=None, repr=False)
    configuration_overrides: dict[str, Any] | None = field(default=None, repr=False)
    execution_timeout_minutes: int | None = None
    retry_policy: dict[str, Any] | None = field(default=None, repr=False)
    mode: str | None = None
    execution_iam_policy: dict[str, Any] | None = field(default=None, repr=False)
    tags: dict[str, str] | None = field(default=None, repr=False)
    source_application_settings: dict[str, Any] = field(default_factory=dict, repr=False)
    cloudwatch_monitoring: CloudWatchLogConfiguration | None = None

    def __post_init__(self) -> None:
        # A source response or fixture must not retain mutable aliases into
        # this snapshot. VM/request boundaries take their own copies too.
        for name in (
            "job_driver",
            "configuration_overrides",
            "retry_policy",
            "execution_iam_policy",
            "tags",
            "source_application_settings",
        ):
            object.__setattr__(self, name, deepcopy(getattr(self, name)))


class EmrServerlessClientProtocol(Protocol):
    """Structural client boundary consumed by the EMR viewmodels."""

    async def list_applications(self) -> list[ApplicationSummary]: ...

    async def list_job_runs_page(
        self,
        application_id: str,
        *,
        start_token: str | None = None,
        states: set[JobRunState] | None = None,
    ) -> tuple[list[JobRunSummary], str | None]: ...

    async def get_job_run(
        self,
        application_id: str,
        job_run_id: str,
    ) -> JobRunDetail: ...

    async def cancel_job_run(self, application_id: str, job_run_id: str) -> None: ...

    async def start_job_run(
        self,
        application_id: str,
        *,
        execution_role_arn: str,
        entry_point: str,
        entry_point_arguments: tuple[str, ...] | None,
        spark_submit_parameters: str | None,
        client_token: str,
        name: str | None = None,
        configuration_overrides: dict[str, Any] | None = None,
        execution_timeout_minutes: int | None = None,
        retry_policy: dict[str, Any] | None = None,
        mode: str | None = None,
        execution_iam_policy: dict[str, Any] | None = None,
        tags: dict[str, str] | None = None,
    ) -> str: ...


__all__ = [
    "CANCELLABLE_JOB_RUN_STATES",
    "EMR_BOTO_CONFIG",
    "EMR_CANCEL_BOTO_CONFIG",
    "ApplicationState",
    "ApplicationSummary",
    "CloudWatchLogConfiguration",
    "EmrServerlessClient",
    "EmrServerlessClientProtocol",
    "JobRunDetail",
    "JobRunState",
    "JobRunSummary",
    "map_boto_error",
    "parse_cloudwatch_monitoring",
]


# ── boto3 error mapping ──────────────────────────────────────────────────

_CLIENT_ERROR_CODE_MAP: dict[str, type[ProviderError]] = {
    "AccessDeniedException": PermissionDeniedError,
    "ThrottlingException": ThrottledError,
    "ResourceNotFoundException": NotFoundError,
    "ValidationException": ValidationError,
    "InvalidParameterException": ValidationError,
    "ServiceUnavailableException": ProviderUnreachableError,
}
_TRANSPORT_FAILURE_EXCEPTIONS = AWS_TRANSPORT_EXCEPTIONS
_MAX_EMR_LISTING_PAGES = 100
_MAX_EMR_APPLICATIONS = 1000
_EMR_PAGE_SIZE = 50


def _map_boto_error(exc: BaseException) -> ProviderError | None:
    """Translate a boto3/botocore exception to the domain
    :class:`ProviderError` hierarchy. Returns ``None`` for anything
    that isn't AWS — callers should re-raise those unchanged.

    ``ValueError`` and ``KeyError`` from response-shape parsing
    (e.g. ``StrEnum`` constructors on an unrecognised state, or a
    response dict missing a required field) are mapped to
    :class:`ValidationError` so future AWS additions surface as a
    typed domain error instead of a crash modal."""
    if isinstance(exc, AWS_CREDENTIAL_EXCEPTIONS):
        if isinstance(exc, botocore.exceptions.CredentialRetrievalError):
            return AuthRequiredError("credential process failed")
        return AuthRequiredError(str(exc) or "no AWS credentials")
    if isinstance(exc, _TRANSPORT_FAILURE_EXCEPTIONS):
        return ProviderUnreachableError(str(exc) or "endpoint unreachable")
    if isinstance(exc, botocore.exceptions.ClientError):
        code = exc.response.get("Error", {}).get("Code", "")
        if code in AWS_AUTH_ERROR_CODES:
            return AuthRequiredError(exc.response.get("Error", {}).get("Message", str(exc)))
        cls = _CLIENT_ERROR_CODE_MAP.get(code, ProviderError)
        return cls(exc.response.get("Error", {}).get("Message", str(exc)))
    if isinstance(exc, botocore.exceptions.ParamValidationError):
        return ValidationError(str(exc))
    if isinstance(exc, ValueError | KeyError):
        return ValidationError(f"malformed EMR Serverless response: {exc}")
    return None


#: Public alias so the sibling :mod:`aws_tui.domain.emr_logs` module
#: (and any future EMR-domain facade) can route boto exceptions
#: through the same canonical mapper without each facade re-deriving
#: the credentials / endpoint / ClientError-code logic. The private
#: ``_map_boto_error`` keeps the internal call sites unchanged.
map_boto_error = _map_boto_error


# ── Async aioboto3 facade ────────────────────────────────────────────────


class EmrServerlessClient:
    """Async aioboto3 facade for EMR Serverless read, clone and cancellation operations.

    The client opens a fresh aioboto3 ``emr-serverless`` client per
    call (the boto3 EMR Serverless client is cheap to instantiate
    and aioboto3 contexts are short-lived). The session is owned by
    the caller — typically built once per :class:`Connection` and
    threaded through :meth:`EmrServerlessService.build_vm`."""

    def __init__(
        self,
        *,
        session: aioboto3.Session,
        region_name: str | None = None,
    ) -> None:
        self._session = session
        self._region_name = region_name

    async def list_applications(self) -> list[ApplicationSummary]:
        try:
            async with self._session.client(
                "emr-serverless", region_name=self._region_name, config=_EMR_BOTO_CONFIG
            ) as c:
                items: list[dict[str, Any]] = []
                next_token: str | None = None
                seen_tokens: set[str] = set()
                page_count = 0
                while True:
                    if page_count >= _MAX_EMR_LISTING_PAGES:
                        raise ProviderError(
                            "EMR Serverless application pagination safety limit exceeded"
                        )
                    page_count += 1
                    kwargs: dict[str, Any] = {"maxResults": _EMR_PAGE_SIZE}
                    if next_token is not None:
                        kwargs["nextToken"] = next_token
                    resp = await c.list_applications(**kwargs)
                    items.extend(resp.get("applications", []))
                    if len(items) > _MAX_EMR_APPLICATIONS:
                        raise ProviderError(
                            "EMR Serverless application collection safety limit exceeded"
                        )
                    next_token = resp.get("nextToken")
                    if next_token is None:
                        break
                    if next_token in seen_tokens:
                        raise ProviderError(
                            "EMR Serverless repeated an application continuation token"
                        )
                    seen_tokens.add(next_token)
                return [
                    ApplicationSummary(
                        id=a["id"],
                        name=a.get("name", a["id"]),
                        state=ApplicationState(a["state"]),
                        type=a.get("type", "SPARK"),
                        created_at=a["createdAt"],
                    )
                    for a in items
                ]
        except Exception as exc:
            mapped = _map_boto_error(exc)
            if mapped is None:
                raise
            raise mapped from exc

    async def list_job_runs_page(
        self,
        application_id: str,
        *,
        start_token: str | None = None,
        states: set[JobRunState] | None = None,
    ) -> tuple[list[JobRunSummary], str | None]:
        """Fetch ONE page of job runs and return ``(runs, next_token)``.

        The VM threads ``next_token`` back through this method on
        ``load_more()`` to walk pages on demand. ``states`` is passed
        through to the service's list-valued filter and also checked
        locally as a defensive response-contract guard.

        Used by :class:`JobRunsVM` so the LEFT pane can page through
        large run histories incrementally — user feedback (post-
        PR-#92): "the list of job runs seems to be limited to a
        select few that represents only the 1st page of returned
        list of job runs. But we need an elegant way to page
        through all job runs". The bulk ``list_job_runs`` below is
        retained for non-paged callers (tests, future scripts) that
        want a one-shot "give me everything up to N" semantics.
        """
        try:
            async with self._session.client(
                "emr-serverless", region_name=self._region_name, config=_EMR_BOTO_CONFIG
            ) as c:
                kwargs: dict[str, Any] = {
                    "applicationId": application_id,
                    "maxResults": _EMR_PAGE_SIZE,
                }
                if start_token is not None:
                    kwargs["nextToken"] = start_token
                if states and states != set(JobRunState):
                    kwargs["states"] = sorted(state.value for state in states)
                resp = await c.list_job_runs(**kwargs)
                summaries = [
                    JobRunSummary(
                        application_id=cast(str, r["applicationId"]),
                        job_run_id=cast(str, r.get("id", r.get("jobRunId", ""))),
                        name=cast(str | None, r.get("name")),
                        state=JobRunState(cast(str, r["state"])),
                        created_at=cast(datetime, r["createdAt"]),
                        updated_at=cast(datetime, r["updatedAt"]),
                    )
                    for r in resp.get("jobRuns", [])
                ]
                if states is not None:
                    summaries = [s for s in summaries if s.state in states]
                summaries.sort(key=lambda s: s.created_at, reverse=True)
                return summaries, resp.get("nextToken")
        except Exception as exc:
            mapped = _map_boto_error(exc)
            if mapped is None:
                raise
            raise mapped from exc

    async def list_job_runs(
        self,
        application_id: str,
        *,
        states: set[JobRunState] | None = None,
        max_results: int = 100,
    ) -> list[JobRunSummary]:
        """List most-recent runs (sorted descending by createdAt).

        ``states`` uses EMR Serverless's list-valued server-side filter;
        local filtering defensively rejects an out-of-contract response."""
        try:
            async with self._session.client(
                "emr-serverless", region_name=self._region_name, config=_EMR_BOTO_CONFIG
            ) as c:
                items: list[dict[str, object]] = []
                next_token: str | None = None
                seen_tokens: set[str] = set()
                page_count = 0
                while len(items) < max_results:
                    if page_count >= _MAX_EMR_LISTING_PAGES:
                        raise ProviderError(
                            "EMR Serverless job-run pagination safety limit exceeded"
                        )
                    page_count += 1
                    kwargs: dict[str, Any] = {
                        "applicationId": application_id,
                        "maxResults": min(_EMR_PAGE_SIZE, max_results - len(items)),
                    }
                    if next_token is not None:
                        kwargs["nextToken"] = next_token
                    if states and states != set(JobRunState):
                        kwargs["states"] = sorted(state.value for state in states)
                    resp = await c.list_job_runs(**kwargs)
                    items.extend(resp.get("jobRuns", []))
                    next_token = resp.get("nextToken")
                    if next_token is None:
                        break
                    if next_token in seen_tokens:
                        raise ProviderError("EMR Serverless repeated a job-run continuation token")
                    seen_tokens.add(next_token)
                summaries = [
                    JobRunSummary(
                        application_id=cast(str, r["applicationId"]),
                        job_run_id=cast(str, r.get("id", r.get("jobRunId", ""))),
                        name=cast(str | None, r.get("name")),
                        state=JobRunState(cast(str, r["state"])),
                        created_at=cast(datetime, r["createdAt"]),
                        updated_at=cast(datetime, r["updatedAt"]),
                    )
                    for r in items
                ]
                if states is not None:
                    summaries = [s for s in summaries if s.state in states]
                summaries.sort(key=lambda s: s.created_at, reverse=True)
                return summaries[:max_results]
        except Exception as exc:
            mapped = _map_boto_error(exc)
            if mapped is None:
                raise
            raise mapped from exc

    async def get_job_run(self, application_id: str, job_run_id: str) -> JobRunDetail:
        try:
            async with self._session.client(
                "emr-serverless", region_name=self._region_name, config=_EMR_BOTO_CONFIG
            ) as c:
                resp = await c.get_job_run(applicationId=application_id, jobRunId=job_run_id)
                r = resp["jobRun"]
                spark = r.get("jobDriver", {}).get("sparkSubmit", {})
                duration_seconds = r.get("totalExecutionDurationSeconds")
                log_uri = (
                    r.get("configurationOverrides", {})
                    .get("monitoringConfiguration", {})
                    .get("s3MonitoringConfiguration", {})
                    .get("logUri")
                )
                return JobRunDetail(
                    application_id=r["applicationId"],
                    job_run_id=r.get("id", r.get("jobRunId", job_run_id)),
                    name=r.get("name"),
                    state=JobRunState(r["state"]),
                    created_at=r["createdAt"],
                    updated_at=r["updatedAt"],
                    entry_point=spark.get("entryPoint"),
                    entry_point_arguments=(
                        tuple(spark["entryPointArguments"])
                        if "entryPointArguments" in spark
                        else None
                    ),
                    spark_submit_parameters=spark.get("sparkSubmitParameters"),
                    execution_role_arn=r.get("executionRole", ""),
                    duration_ms=(duration_seconds * 1000) if duration_seconds is not None else None,
                    s3_monitoring_log_uri=log_uri,
                    cloudwatch_monitoring=parse_cloudwatch_monitoring(
                        r.get("configurationOverrides")
                    ),
                    job_driver=r.get("jobDriver"),
                    configuration_overrides=r.get("configurationOverrides"),
                    execution_timeout_minutes=r.get("executionTimeoutMinutes"),
                    retry_policy=r.get("retryPolicy"),
                    mode=r.get("mode"),
                    execution_iam_policy=r.get("executionIamPolicy"),
                    tags=r.get("tags"),
                    source_application_settings={
                        key: r[key]
                        for key in (
                            "releaseLabel",
                            "networkConfiguration",
                            "imageConfiguration",
                            "workerTypeSpecifications",
                        )
                        if key in r
                    },
                )
        except Exception as exc:
            mapped = _map_boto_error(exc)
            if mapped is None:
                raise
            raise mapped from exc

    async def cancel_job_run(self, application_id: str, job_run_id: str) -> None:
        """Request cancellation once; the acknowledgement is not a run state."""
        try:
            async with self._session.client(
                "emr-serverless", region_name=self._region_name, config=EMR_CANCEL_BOTO_CONFIG
            ) as c:
                await c.cancel_job_run(applicationId=application_id, jobRunId=job_run_id)
        except Exception as exc:
            mapped = _map_boto_error(exc)
            if mapped is None:
                raise
            raise mapped from exc

    async def start_job_run(
        self,
        application_id: str,
        *,
        execution_role_arn: str,
        entry_point: str,
        entry_point_arguments: tuple[str, ...] | None,
        spark_submit_parameters: str | None,
        client_token: str,
        name: str | None = None,
        configuration_overrides: dict[str, Any] | None = None,
        execution_timeout_minutes: int | None = None,
        retry_policy: dict[str, Any] | None = None,
        mode: str | None = None,
        execution_iam_policy: dict[str, Any] | None = None,
        tags: dict[str, str] | None = None,
    ) -> str:
        """Submit a new job run. Returns the new ``job_run_id``.

        The matching VM (``JobRunCloneVM``) pre-populates the form
        from a :class:`JobRunDetail` then calls this to fire the
        re-run. Errors from boto3 are mapped through
        :func:`_map_boto_error` to the domain :class:`ProviderError`
        hierarchy so the modal can surface a typed error inline.

        ``client_token`` is the app-owned ``clientToken``. The model marks it
        ``idempotencyToken: true``, so left unset botocore mints a fresh UUID
        per call; that turns a retry after an ambiguous failure (accepted
        request, lost response) into a second billable job. The clone VM owns
        one token per form intent and reuses it across such retries."""
        kwargs = build_start_job_run_request(
            application_id,
            execution_role_arn=execution_role_arn,
            entry_point=entry_point,
            entry_point_arguments=entry_point_arguments,
            spark_submit_parameters=spark_submit_parameters,
            client_token=client_token,
            name=name,
            configuration_overrides=configuration_overrides,
            execution_timeout_minutes=execution_timeout_minutes,
            retry_policy=retry_policy,
            mode=mode,
            execution_iam_policy=execution_iam_policy,
            tags=tags,
        )
        try:
            async with self._session.client(
                "emr-serverless", region_name=self._region_name, config=_EMR_BOTO_CONFIG
            ) as c:
                resp = await c.start_job_run(**kwargs)
                return cast(str, resp["jobRunId"])
        except Exception as exc:
            mapped = _map_boto_error(exc)
            category = type(mapped) if mapped is not None else ProviderError
            messages = {
                AuthRequiredError: "Authentication required; reauthenticate the selected profile",
                PermissionDeniedError: "Access denied; check the execution role and StartJobRun permission",
                ProviderUnreachableError: "No response from AWS; retry the unchanged request with the same client token",
                ThrottledError: "AWS throttled the request; retry after waiting",
                ValidationError: "AWS rejected the job settings; review the role, entry point and configuration",
                NotFoundError: "The source application is no longer available",
            }
            # Provider messages and chained SDK exceptions can contain arbitrary
            # Spark arguments, policies or credential-process output.
            raise category(messages.get(category, "EMR job submission failed")) from None
