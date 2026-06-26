"""In-memory fake :class:`EmrServerlessClient` for unit tests.

VMs are constructor-injected with the client; tests substitute this
fake to drive deterministic responses without any aioboto3 / botocore
plumbing. Mirrors the public interface of
:class:`aws_tui.domain.emr_serverless.EmrServerlessClient` — no
Protocol earns its place for a single non-network implementation
(see PR-A spec §1)."""

from __future__ import annotations

from datetime import datetime

from aws_tui.domain.emr_serverless import (
    ApplicationState,
    ApplicationSummary,
    JobRunDetail,
    JobRunState,
    JobRunSummary,
)


class _InMemoryEmr:
    """Test-only fake EMR client.

    Use :meth:`add_application`, :meth:`add_job_run`, and
    :meth:`add_job_run_detail` to seed before driving a VM. The
    same instance can be reused across calls — state mutations
    are intentional so tests can flip a run's state mid-poll."""

    def __init__(self) -> None:
        self._apps: dict[str, ApplicationSummary] = {}
        self._runs: dict[str, dict[str, JobRunSummary]] = {}  # app_id -> run_id -> summary
        self._details: dict[tuple[str, str], JobRunDetail] = {}
        # Counter so each call is observable in tests that pin the
        # auto-refresh cadence.
        self.calls: list[tuple[str, tuple[object, ...]]] = []

    # ── Test seeding ────────────────────────────────────────────────────────

    def add_application(
        self,
        *,
        app_id: str,
        name: str,
        state: ApplicationState = ApplicationState.STARTED,
        app_type: str = "SPARK",
        created_at: datetime | None = None,
    ) -> ApplicationSummary:
        s = ApplicationSummary(
            id=app_id,
            name=name,
            state=state,
            type=app_type,
            created_at=created_at or datetime.fromisoformat("2026-06-25T12:00:00+00:00"),
        )
        self._apps[app_id] = s
        self._runs.setdefault(app_id, {})
        return s

    def add_job_run(
        self,
        *,
        application_id: str,
        job_run_id: str,
        name: str | None = None,
        state: JobRunState = JobRunState.SUCCESS,
        created_at: datetime | None = None,
        updated_at: datetime | None = None,
    ) -> JobRunSummary:
        ts = created_at or datetime.fromisoformat("2026-06-25T12:00:00+00:00")
        s = JobRunSummary(
            application_id=application_id,
            job_run_id=job_run_id,
            name=name,
            state=state,
            created_at=ts,
            updated_at=updated_at or ts,
        )
        self._runs.setdefault(application_id, {})[job_run_id] = s
        return s

    def add_job_run_detail(
        self,
        *,
        application_id: str,
        job_run_id: str,
        entry_point: str | None = "s3://example/job.py",
        entry_point_arguments: tuple[str, ...] = (),
        spark_submit_parameters: str | None = None,
        execution_role_arn: str = "arn:aws:iam::123456789012:role/EmrJobRole",
        duration_ms: int | None = None,
    ) -> JobRunDetail:
        summary = self._runs.get(application_id, {}).get(job_run_id)
        if summary is None:
            summary = self.add_job_run(application_id=application_id, job_run_id=job_run_id)
        d = JobRunDetail(
            application_id=application_id,
            job_run_id=job_run_id,
            name=summary.name,
            state=summary.state,
            created_at=summary.created_at,
            updated_at=summary.updated_at,
            entry_point=entry_point,
            entry_point_arguments=entry_point_arguments,
            spark_submit_parameters=spark_submit_parameters,
            execution_role_arn=execution_role_arn,
            duration_ms=duration_ms,
        )
        self._details[(application_id, job_run_id)] = d
        return d

    def set_run_state(self, application_id: str, job_run_id: str, state: JobRunState) -> None:
        """Mutate the state of a previously-added run (used by tests
        that pin auto-refresh observable side effects)."""
        s = self._runs[application_id][job_run_id]
        self._runs[application_id][job_run_id] = JobRunSummary(
            application_id=s.application_id,
            job_run_id=s.job_run_id,
            name=s.name,
            state=state,
            created_at=s.created_at,
            updated_at=s.updated_at,
        )

    # ── Public client surface (matches EmrServerlessClient) ────────────────

    async def list_applications(self) -> list[ApplicationSummary]:
        self.calls.append(("list_applications", ()))
        return sorted(self._apps.values(), key=lambda a: a.created_at, reverse=True)

    async def list_job_runs(
        self,
        application_id: str,
        *,
        states: set[JobRunState] | None = None,
        max_results: int = 100,
    ) -> list[JobRunSummary]:
        self.calls.append(("list_job_runs", (application_id, states, max_results)))
        runs = list(self._runs.get(application_id, {}).values())
        if states is not None:
            runs = [r for r in runs if r.state in states]
        runs.sort(key=lambda r: r.created_at, reverse=True)
        return runs[:max_results]

    async def get_job_run(self, application_id: str, job_run_id: str) -> JobRunDetail:
        self.calls.append(("get_job_run", (application_id, job_run_id)))
        return self._details[(application_id, job_run_id)]


__all__ = ["_InMemoryEmr"]
