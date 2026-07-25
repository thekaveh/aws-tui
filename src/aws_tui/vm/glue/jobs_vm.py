from __future__ import annotations

from typing import Any

import reactivex as rx
from reactivex.subject import Subject
from vmx import ComponentVMOf, Message, MessageHub, PropertyChangedMessage
from vmx.collections.token_paged_composition import TokenPagedComposition
from vmx.lifecycle.status import ConstructionStatus
from vmx.services.dispatcher import Dispatcher

from aws_tui.domain.filesystem import ProviderError
from aws_tui.domain.glue import GlueJobRunSummary, GlueJobSummary
from aws_tui.infra.redaction import redact_text
from aws_tui.vm.file_manager.pane_vm import PaneState
from aws_tui.vm.glue._errors import map_provider_error


class GlueJobsVM:
    def __init__(
        self,
        *,
        client: Any,
        hub: MessageHub[Message],
        dispatcher: Dispatcher,
    ) -> None:
        self._client = client
        self._hub = hub
        self._disposed = False
        self._on_property_changed: Subject[str] = Subject()
        self._inner: ComponentVMOf[None] = (
            ComponentVMOf[None]
            .builder()
            .name("glue.jobs")
            .model(None)
            .services(hub, dispatcher)
            .build()
        )
        self._job_generation = 0
        self._run_generation = 0
        self._run_state_filter: frozenset[str] = frozenset()
        self._job_pager = self._make_job_pager()
        self._run_pager = self._make_run_pager(None)
        self._selected_job_name: str | None = None
        self._selected_run_id: str | None = None
        self._jobs_state = PaneState.LOADING
        self._runs_state = PaneState.EMPTY
        self._jobs_error_text: str | None = None
        self._runs_error_text: str | None = None

    @property
    def jobs(self) -> tuple[GlueJobSummary, ...]:
        return tuple(self._job_pager.items)

    @property
    def runs(self) -> tuple[GlueJobRunSummary, ...]:
        return tuple(self._run_pager.items)

    @property
    def selected_job_name(self) -> str | None:
        return self._selected_job_name

    @property
    def selected_run_id(self) -> str | None:
        return self._selected_run_id

    @property
    def selected_job(self) -> GlueJobSummary | None:
        return next((job for job in self.jobs if job.name == self._selected_job_name), None)

    @property
    def selected_run(self) -> GlueJobRunSummary | None:
        return next((run for run in self.runs if run.run_id == self._selected_run_id), None)

    @property
    def run_state_filter(self) -> frozenset[str]:
        return self._run_state_filter

    @property
    def has_more_jobs(self) -> bool:
        return self._job_pager.current_token is not None

    @property
    def has_more_runs(self) -> bool:
        return self._run_pager.current_token is not None

    @property
    def state(self) -> PaneState:
        return self._jobs_state

    @property
    def error_text(self) -> str | None:
        return self._jobs_error_text

    @property
    def jobs_state(self) -> PaneState:
        return self._jobs_state

    @property
    def runs_state(self) -> PaneState:
        return self._runs_state

    @property
    def jobs_error_text(self) -> str | None:
        return self._jobs_error_text

    @property
    def runs_error_text(self) -> str | None:
        return self._runs_error_text

    @property
    def status(self) -> ConstructionStatus:
        return self._inner.status

    @property
    def on_property_changed(self) -> rx.Observable[str]:
        return self._on_property_changed

    def construct(self) -> None:
        self._inner.construct()

    def dispose(self) -> None:
        if self._disposed:
            return
        self._disposed = True
        self._run_pager.dispose()
        self._job_pager.dispose()
        self._on_property_changed.on_completed()
        self._on_property_changed.dispose()
        self._inner.dispose()

    async def setup(self) -> None:
        await self.refresh_jobs()

    async def refresh_jobs(self) -> None:
        self._job_generation += 1
        old_pager = self._job_pager
        self._job_pager = self._make_job_pager()
        old_pager.dispose()
        generation = self._job_generation
        self._jobs_error_text = None
        self._set_state("_jobs_state", PaneState.LOADING, "state")
        try:
            await self._job_pager.refresh_command.execute_async()
        except ProviderError as exc:
            if generation != self._job_generation:
                return
            state, self._jobs_error_text = map_provider_error(exc)
            self._set_state("_jobs_state", state, "state")
            return
        except Exception as exc:
            if generation != self._job_generation:
                return
            self._jobs_error_text = redact_text(f"unexpected error: {exc}")
            self._set_state("_jobs_state", PaneState.ERROR, "state")
            return
        if generation != self._job_generation:
            return
        self._notify("jobs")
        self._notify("has_more_jobs")
        self._set_state(
            "_jobs_state",
            PaneState.IDLE if self.jobs else PaneState.EMPTY,
            "state",
        )

    async def load_more_jobs(self) -> None:
        if not self.has_more_jobs:
            return
        generation = self._job_generation
        try:
            await self._job_pager.load_more_command.execute_async()
        except ProviderError as exc:
            if generation != self._job_generation:
                return
            state, self._jobs_error_text = map_provider_error(exc)
            self._set_state("_jobs_state", state, "state")
            return
        if generation == self._job_generation:
            self._notify("jobs")
            self._notify("has_more_jobs")

    async def select_job(self, job_name: str) -> None:
        if not any(job.name == job_name for job in self.jobs):
            return
        self._selected_job_name = job_name
        self._selected_run_id = None
        self._notify("selected_job_name")
        self._notify("selected_run_id")
        await self._reload_runs()

    def select_run(self, run_id: str) -> None:
        if not any(run.run_id == run_id for run in self.runs):
            return
        if self._selected_run_id == run_id:
            return
        self._selected_run_id = run_id
        self._notify("selected_run_id")

    async def set_run_state_filter(self, states: frozenset[str]) -> None:
        if states == self._run_state_filter:
            return
        self._run_state_filter = frozenset(states)
        self._notify("run_state_filter")
        await self._reload_runs()

    async def load_more_runs(self) -> None:
        if not self.has_more_runs:
            return
        generation = self._run_generation
        try:
            await self._run_pager.load_more_command.execute_async()
        except ProviderError as exc:
            if generation != self._run_generation:
                return
            state, self._runs_error_text = map_provider_error(exc)
            self._set_state("_runs_state", state, "runs_state")
            return
        if generation == self._run_generation:
            self._notify("runs")
            self._notify("has_more_runs")

    async def _reload_runs(self) -> None:
        self._run_generation += 1
        generation = self._run_generation
        old_pager = self._run_pager
        self._run_pager = self._make_run_pager(self._selected_job_name)
        old_pager.dispose()
        self._selected_run_id = None
        self._runs_error_text = None
        self._notify("runs")
        self._notify("selected_run_id")
        if self._selected_job_name is None:
            self._set_state("_runs_state", PaneState.EMPTY, "runs_state")
            return
        self._set_state("_runs_state", PaneState.LOADING, "runs_state")
        try:
            await self._run_pager.refresh_command.execute_async()
        except ProviderError as exc:
            if generation != self._run_generation:
                return
            state, self._runs_error_text = map_provider_error(exc)
            self._set_state("_runs_state", state, "runs_state")
            return
        except Exception as exc:
            if generation != self._run_generation:
                return
            self._runs_error_text = redact_text(f"unexpected error: {exc}")
            self._set_state("_runs_state", PaneState.ERROR, "runs_state")
            return
        if generation != self._run_generation:
            return
        if self.runs:
            self._selected_run_id = self.runs[0].run_id
            self._notify("selected_run_id")
        self._notify("runs")
        self._notify("has_more_runs")
        self._set_state(
            "_runs_state",
            PaneState.IDLE if self.runs else PaneState.EMPTY,
            "runs_state",
        )

    def _make_job_pager(self) -> TokenPagedComposition[GlueJobSummary, str]:
        generation = self._job_generation

        async def fetch(token: str | None) -> tuple[list[GlueJobSummary], str | None]:
            rows, next_token = await self._client.list_jobs_page(start_token=token)
            if generation != self._job_generation:
                return [], None
            return rows, next_token

        return TokenPagedComposition(fetch)

    def _make_run_pager(
        self,
        job_name: str | None,
    ) -> TokenPagedComposition[GlueJobRunSummary, str]:
        generation = self._run_generation
        states = tuple(sorted(self._run_state_filter))

        async def fetch(token: str | None) -> tuple[list[GlueJobRunSummary], str | None]:
            if job_name is None:
                return [], None
            rows, next_token = await self._client.list_job_runs_page(
                job_name,
                start_token=token,
                states=states,
            )
            if generation != self._run_generation:
                return [], None
            return rows, next_token

        return TokenPagedComposition(fetch)

    def _set_state(self, field: str, state: PaneState, property_name: str) -> None:
        if getattr(self, field) == state:
            return
        setattr(self, field, state)
        self._notify(property_name)

    def _notify(self, property_name: str) -> None:
        self._hub.send(PropertyChangedMessage.create(self, "glue.jobs", property_name))
        self._on_property_changed.on_next(property_name)


__all__ = ["GlueJobsVM"]
