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


class ApplicationState(StrEnum):
    """Application lifecycle states per the boto3 enum.

    See https://docs.aws.amazon.com/emr-serverless/latest/APIReference/
    """

    CREATED = "CREATED"
    STARTING = "STARTING"
    STARTED = "STARTED"
    STOPPING = "STOPPING"
    STOPPED = "STOPPED"
    TERMINATED = "TERMINATED"


class JobRunState(StrEnum):
    """Job-run lifecycle states. ``CANCELLING`` is the transient
    state after a cancel request before ``CANCELLED`` is observed."""

    PENDING = "PENDING"
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
    "JobRunDetail",
    "JobRunState",
    "JobRunSummary",
]
