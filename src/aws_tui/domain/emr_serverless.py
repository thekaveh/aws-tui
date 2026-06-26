"""EMR Serverless domain types — records + StrEnums.

PR-A ships only the read-only verbs (list applications, list job
runs, get job run detail). PR-B adds cancel + lifecycle, PR-C adds
submit. The records here are the wire-shape mapped from boto3
``EMRServerless`` responses by :class:`EmrServerlessClient` (added
in Task 3). VMs above hold these by value — no fields beyond what
the read-only browser needs."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import TYPE_CHECKING, Any, cast

import botocore.exceptions
from botocore.config import Config as BotoConfig

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
    retries={"max_attempts": 6, "mode": "adaptive"},
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


@dataclass(frozen=True, slots=True)
class ApplicationSummary:
    """One row in the application picker dropdown."""

    id: str
    name: str
    state: ApplicationState
    type: str  # "SPARK" or "HIVE" — v1 only renders SPARK applications
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
    entry_point: str | None
    entry_point_arguments: tuple[str, ...]
    spark_submit_parameters: str | None
    execution_role_arn: str
    duration_ms: int | None


__all__ = [
    "ApplicationState",
    "ApplicationSummary",
    "EmrServerlessClient",
    "JobRunDetail",
    "JobRunState",
    "JobRunSummary",
]


# ── boto3 error mapping ──────────────────────────────────────────────────

_CLIENT_ERROR_CODE_MAP: dict[str, type[ProviderError]] = {
    "AccessDeniedException": PermissionDeniedError,
    "ThrottlingException": ThrottledError,
    "ResourceNotFoundException": NotFoundError,
    "ValidationException": ValidationError,
}


def _map_boto_error(exc: BaseException) -> ProviderError | None:
    """Translate a boto3/botocore exception to the domain
    :class:`ProviderError` hierarchy. Returns ``None`` for anything
    that isn't AWS — callers should re-raise those unchanged.

    ``ValueError`` and ``KeyError`` from response-shape parsing
    (e.g. ``StrEnum`` constructors on an unrecognised state, or a
    response dict missing a required field) are mapped to
    :class:`ValidationError` so future AWS additions surface as a
    typed domain error instead of a crash modal."""
    if isinstance(
        exc, botocore.exceptions.NoCredentialsError | botocore.exceptions.TokenRetrievalError
    ):
        return AuthRequiredError(str(exc) or "no AWS credentials")
    if isinstance(
        exc,
        botocore.exceptions.EndpointConnectionError
        | botocore.exceptions.ConnectTimeoutError
        | botocore.exceptions.ReadTimeoutError,
    ):
        return ProviderUnreachableError(str(exc) or "endpoint unreachable")
    if isinstance(exc, botocore.exceptions.ClientError):
        code = exc.response.get("Error", {}).get("Code", "")
        cls = _CLIENT_ERROR_CODE_MAP.get(code, ProviderError)
        return cls(exc.response.get("Error", {}).get("Message", str(exc)))
    if isinstance(exc, ValueError | KeyError):
        return ValidationError(f"malformed EMR Serverless response: {exc}")
    return None


# ── Async aioboto3 facade ────────────────────────────────────────────────


class EmrServerlessClient:
    """Async aioboto3 facade for EMR Serverless. PR-A surfaces three
    read-only verbs; PR-B/C extend with cancel/lifecycle/submit.

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
        async with self._session.client(
            "emr-serverless", region_name=self._region_name, config=_EMR_BOTO_CONFIG
        ) as c:
            try:
                items: list[dict[str, Any]] = []
                next_token: str | None = None
                while True:
                    kwargs: dict[str, Any] = {}
                    if next_token is not None:
                        kwargs["nextToken"] = next_token
                    resp = await c.list_applications(**kwargs)
                    items.extend(resp.get("applications", []))
                    next_token = resp.get("nextToken")
                    if next_token is None:
                        break
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

    async def list_job_runs(
        self,
        application_id: str,
        *,
        states: set[JobRunState] | None = None,
        max_results: int = 100,
    ) -> list[JobRunSummary]:
        """List most-recent runs (sorted descending by createdAt).

        ``states`` filters CLIENT-side after paging. boto3 supports
        a single-state ``states`` parameter but multi-state requires
        client-side filtering anyway, so we fetch unfiltered and
        keep the logic in one place."""
        async with self._session.client(
            "emr-serverless", region_name=self._region_name, config=_EMR_BOTO_CONFIG
        ) as c:
            try:
                items: list[dict[str, object]] = []
                next_token: str | None = None
                while len(items) < max_results:
                    kwargs: dict[str, str] = {"applicationId": application_id}
                    if next_token is not None:
                        kwargs["nextToken"] = next_token
                    resp = await c.list_job_runs(**kwargs)
                    items.extend(resp.get("jobRuns", []))
                    next_token = resp.get("nextToken")
                    if next_token is None:
                        break
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
        async with self._session.client(
            "emr-serverless", region_name=self._region_name, config=_EMR_BOTO_CONFIG
        ) as c:
            try:
                resp = await c.get_job_run(applicationId=application_id, jobRunId=job_run_id)
                r = resp["jobRun"]
                spark = r.get("jobDriver", {}).get("sparkSubmit", {})
                duration_seconds = r.get("totalExecutionDurationSeconds")
                return JobRunDetail(
                    application_id=r["applicationId"],
                    job_run_id=r.get("id", r.get("jobRunId", job_run_id)),
                    name=r.get("name"),
                    state=JobRunState(r["state"]),
                    created_at=r["createdAt"],
                    updated_at=r["updatedAt"],
                    entry_point=spark.get("entryPoint"),
                    entry_point_arguments=tuple(spark.get("entryPointArguments", ())),
                    spark_submit_parameters=spark.get("sparkSubmitParameters"),
                    execution_role_arn=r.get("executionRole", ""),
                    duration_ms=(duration_seconds * 1000) if duration_seconds is not None else None,
                )
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
        entry_point_arguments: tuple[str, ...],
        spark_submit_parameters: str | None,
        name: str | None = None,
    ) -> str:
        """Submit a new job run. Returns the new ``job_run_id``.

        The matching VM (``JobRunCloneVM``) pre-populates the form
        from a :class:`JobRunDetail` then calls this to fire the
        re-run. Errors from boto3 are mapped through
        :func:`_map_boto_error` to the domain :class:`ProviderError`
        hierarchy so the modal can surface a typed error inline."""
        async with self._session.client(
            "emr-serverless", region_name=self._region_name, config=_EMR_BOTO_CONFIG
        ) as c:
            try:
                kwargs: dict[str, Any] = {
                    "applicationId": application_id,
                    "executionRoleArn": execution_role_arn,
                    "jobDriver": {
                        "sparkSubmit": {
                            "entryPoint": entry_point,
                            "entryPointArguments": list(entry_point_arguments),
                            "sparkSubmitParameters": spark_submit_parameters or "",
                        }
                    },
                }
                if name is not None:
                    kwargs["name"] = name
                resp = await c.start_job_run(**kwargs)
                return cast(str, resp["jobRunId"])
            except Exception as exc:
                mapped = _map_boto_error(exc)
                if mapped is None:
                    raise
                raise mapped from exc
