"""EMR Serverless in-memory fake — production-grade for demo mode.

Lives in ``src/aws_tui/demo/`` so it's reachable from demo mode
(``AwsTuiApp`` consumes it when ``AWS_TUI_DEMO=1``) AND the test
suite (via the shim at ``tests/unit/domain/_in_memory_emr.py``).

Conforms to the surface of
:class:`aws_tui.domain.emr_serverless.EmrServerlessClient`. Three
production polishes over the original test fake:

- ``start_job_run`` schedules an async state-machine walk
  (SUBMITTED → SCHEDULED → RUNNING → SUCCESS over ~5s) so demo
  users see realistic state transitions in the runs pane.
- 50 ms latency at entry of every async method (so the UI's
  ``loading…`` placeholders appear).
- ``dispose()`` cancels in-flight state walks on shutdown.
"""

from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import datetime

from aws_tui.domain.emr_serverless import (
    ApplicationState,
    ApplicationSummary,
    JobRunDetail,
    JobRunState,
    JobRunSummary,
)

# Mirrors aws_tui.demo.in_memory_fs._DEMO_LATENCY_SEC. Surfaces the
# UI's loading… placeholders during demo runs.
_DEMO_LATENCY_SEC: float = 0.05


class InMemoryEmr:
    """In-memory EMR Serverless fake — shared by demo mode and the test suite.

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
        # Monotonic suffix so multiple ``start_job_run`` calls produce
        # unique ids without the tests needing to seed them.
        self._next_run_seq: int = 1
        # Hook for tests that need ``start_job_run`` to raise — set
        # this to a ``ProviderError`` (or any exception) to drive the
        # error-path assertions on ``JobRunCloneVM.submit``.
        self.start_job_run_exc: BaseException | None = None
        # Dummy attributes for JobRunLogsVM constructor (not used by fake).
        self._session = None
        self._region_name = None
        # Active state-walk tasks scheduled by ``start_job_run``.
        # Tracked so ``dispose()`` can cancel them on demo shutdown
        # (otherwise asyncio surfaces "Task was destroyed but it is
        # pending" warnings).
        self._state_tasks: set[asyncio.Task[None]] = set()

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
        s3_monitoring_log_uri: str | None = None,
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
            s3_monitoring_log_uri=s3_monitoring_log_uri,
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
        await asyncio.sleep(_DEMO_LATENCY_SEC)
        self.calls.append(("list_applications", ()))
        return sorted(self._apps.values(), key=lambda a: a.created_at, reverse=True)

    async def list_job_runs(
        self,
        application_id: str,
        *,
        states: set[JobRunState] | None = None,
        max_results: int = 100,
    ) -> list[JobRunSummary]:
        await asyncio.sleep(_DEMO_LATENCY_SEC)
        self.calls.append(("list_job_runs", (application_id, states, max_results)))
        runs = list(self._runs.get(application_id, {}).values())
        if states is not None:
            runs = [r for r in runs if r.state in states]
        runs.sort(key=lambda r: r.created_at, reverse=True)
        return runs[:max_results]

    #: Per-page size used by :meth:`list_job_runs_page`. Defaults
    #: to a large value so tests that don't care about paging
    #: (the majority) see ALL seeded runs in a single page.
    #: Paging-specific tests monkey-patch this to a small value
    #: (e.g. ``fake.page_size = 2``) to exercise the next-token
    #: walk without seeding 100+ runs.
    page_size: int = 100

    async def list_job_runs_page(
        self,
        application_id: str,
        *,
        start_token: str | None = None,
        states: set[JobRunState] | None = None,
    ) -> tuple[list[JobRunSummary], str | None]:
        """In-memory analogue of :meth:`EmrServerlessClient.list_job_runs_page`.

        ``start_token`` encodes the integer offset of the next page
        in the sorted run list. Returns at most :attr:`page_size`
        runs and the token for the next page (or ``None`` when the
        current page exhausts the list)."""
        await asyncio.sleep(_DEMO_LATENCY_SEC)
        self.calls.append(("list_job_runs_page", (application_id, start_token, states)))
        runs = list(self._runs.get(application_id, {}).values())
        if states is not None:
            runs = [r for r in runs if r.state in states]
        runs.sort(key=lambda r: r.created_at, reverse=True)
        offset = int(start_token) if start_token is not None else 0
        page = runs[offset : offset + self.page_size]
        next_offset = offset + self.page_size
        next_token = str(next_offset) if next_offset < len(runs) else None
        return page, next_token

    async def get_job_run(self, application_id: str, job_run_id: str) -> JobRunDetail:
        await asyncio.sleep(_DEMO_LATENCY_SEC)
        self.calls.append(("get_job_run", (application_id, job_run_id)))
        return self._details[(application_id, job_run_id)]

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
        """Record the submit call + materialise a new ``SUBMITTED`` run.

        Returns the synthesised ``job_run_id``. Tests that want to
        observe failure paths set ``self.start_job_run_exc`` first —
        that exception is raised in place of producing a new run.

        In demo mode the run also schedules an async state-machine walk
        (SUBMITTED → SCHEDULED → RUNNING → SUCCESS over ~5 s) so the
        runs pane shows realistic state transitions."""
        await asyncio.sleep(_DEMO_LATENCY_SEC)
        self.calls.append(
            (
                "start_job_run",
                (
                    application_id,
                    execution_role_arn,
                    entry_point,
                    entry_point_arguments,
                    spark_submit_parameters,
                    name,
                ),
            )
        )
        if self.start_job_run_exc is not None:
            raise self.start_job_run_exc
        new_id = f"r-clone-{self._next_run_seq:03d}"
        self._next_run_seq += 1
        ts = datetime.fromisoformat("2026-06-26T12:00:00+00:00")
        s = JobRunSummary(
            application_id=application_id,
            job_run_id=new_id,
            name=name,
            state=JobRunState.SUBMITTED,
            created_at=ts,
            updated_at=ts,
        )
        self._runs.setdefault(application_id, {})[new_id] = s
        d = JobRunDetail(
            application_id=application_id,
            job_run_id=new_id,
            name=name,
            state=JobRunState.SUBMITTED,
            created_at=ts,
            updated_at=ts,
            entry_point=entry_point,
            entry_point_arguments=entry_point_arguments,
            spark_submit_parameters=spark_submit_parameters,
            execution_role_arn=execution_role_arn,
            duration_ms=None,
            s3_monitoring_log_uri=None,
        )
        self._details[(application_id, new_id)] = d
        # Schedule the state walk. ``asyncio.create_task`` requires
        # a running event loop; demo mode always runs inside Textual's
        # loop. The discard-on-done callback keeps ``_state_tasks``
        # bounded.
        task = asyncio.create_task(self._advance_state(application_id, new_id))
        self._state_tasks.add(task)
        task.add_done_callback(self._state_tasks.discard)
        return new_id

    async def _advance_state(self, application_id: str, job_run_id: str) -> None:
        """Walk a freshly-submitted job through SUBMITTED → SCHEDULED →
        RUNNING → SUCCESS over ~5s. Each transition mutates the
        in-memory run + detail records so the next ``list_job_runs_page``
        or ``get_job_run`` call observes the new state.
        """
        try:
            await asyncio.sleep(1.0)
            self._set_run_state(application_id, job_run_id, JobRunState.SCHEDULED)
            await asyncio.sleep(1.0)
            self._set_run_state(application_id, job_run_id, JobRunState.RUNNING)
            await asyncio.sleep(3.0)
            self._set_run_state(application_id, job_run_id, JobRunState.SUCCESS)
        except asyncio.CancelledError:
            # ``dispose()`` cancels in-flight walks; swallow so the
            # task ends cleanly. Pre-fix the CancelledError would
            # surface as an "unhandled exception in Task" warning on
            # demo-mode shutdown.
            raise

    def _set_run_state(self, application_id: str, job_run_id: str, state: JobRunState) -> None:
        """Mutate a run record's state by id.

        Uses ``dataclasses.replace`` — the idiomatic copy-with-changes
        API for ``@dataclass(frozen=True, slots=True)`` records.
        The brief proposed a ``__slots__``-reflection dict comprehension,
        but ``dataclasses.replace`` is cleaner and avoids the branch.
        """
        runs = self._runs.get(application_id, {})
        existing = runs.get(job_run_id)
        if existing is not None:
            runs[job_run_id] = replace(existing, state=state)
        detail = self._details.get((application_id, job_run_id))
        if detail is not None:
            self._details[(application_id, job_run_id)] = replace(detail, state=state)

    def dispose(self) -> None:
        """Cancel any in-flight clone state-machine tasks.

        Called by ``EmrServerlessPageVM.dispose`` and (in tests)
        ``DemoEmr.dispose()`` via test fixtures. Idempotent; safe
        to call multiple times.
        """
        for task in list(self._state_tasks):
            if not task.done():
                task.cancel()
        self._state_tasks.clear()


__all__ = ["InMemoryEmr"]
