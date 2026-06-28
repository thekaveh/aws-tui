"""JobRunsVM — LEFT pane's run-list state.

Scoped to one application at a time. State-filter chips are
multi-select with all-on default; toggling a chip re-applies the
filter against the cached list — no API call. ``refresh()`` is the
only thing that re-fetches."""

from __future__ import annotations

from typing import Any

from vmx import ComponentVMOf, Message, MessageHub, PropertyChangedMessage
from vmx.services.dispatcher import Dispatcher

from aws_tui.domain.emr_serverless import JobRunState, JobRunSummary
from aws_tui.domain.filesystem import ProviderError
from aws_tui.vm.emr_serverless._errors import map_provider_error
from aws_tui.vm.file_manager.pane_vm import PaneState

_ALL_STATES: frozenset[JobRunState] = frozenset(JobRunState)

# Pre-terminal states the poller uses to keep the active (60-s)
# cadence. See ``has_active_runs`` for the rationale; ``CANCELLING``
# is included because the user just requested a cancel and the state
# landing on ``CANCELLED`` is the signal they're waiting on.
_ACTIVE_STATES: frozenset[JobRunState] = frozenset(
    {
        JobRunState.SUBMITTED,
        JobRunState.PENDING,
        JobRunState.SCHEDULED,
        JobRunState.QUEUED,
        JobRunState.RUNNING,
        JobRunState.CANCELLING,
    }
)


class JobRunsVM:
    def __init__(
        self,
        *,
        client: Any,
        hub: MessageHub[Message],
        dispatcher: Dispatcher,
    ) -> None:
        self._client = client
        self._hub: MessageHub[Message] = hub
        self._dispatcher: Dispatcher = dispatcher
        self._application_id: str | None = None
        self._runs_cache: tuple[JobRunSummary, ...] = ()  # unfiltered, paged-accumulator
        self._next_token: str | None = None
        self._selected_id: str | None = None
        self._state: PaneState = PaneState.EMPTY
        self._error_text: str | None = None
        self._state_filter: frozenset[JobRunState] = _ALL_STATES
        self._inner: ComponentVMOf[None] = (
            ComponentVMOf[None]
            .builder()
            .name("emr.job_runs")
            .model(None)
            .services(hub, dispatcher)
            .build()
        )

    # ── Public surface ──────────────────────────────────────────────────────

    @property
    def runs(self) -> tuple[JobRunSummary, ...]:
        return tuple(r for r in self._runs_cache if r.state in self._state_filter)

    @property
    def selected_id(self) -> str | None:
        return self._selected_id

    @property
    def state(self) -> PaneState:
        return self._state

    @property
    def state_filter(self) -> frozenset[JobRunState]:
        return self._state_filter

    @property
    def application_id(self) -> str | None:
        return self._application_id

    @property
    def error_text(self) -> str | None:
        return self._error_text

    @property
    def has_more(self) -> bool:
        """``True`` when at least one more page of runs is available
        for the current application. The pane shows a "load more"
        sentinel row in that case; ``PgDn`` triggers ``load_more``."""
        return self._next_token is not None

    def set_application(self, app_id: str | None) -> None:
        """Re-scope to a new application. Clears selection + cache;
        caller must subsequently call :meth:`refresh`."""
        if self._application_id == app_id:
            return
        self._application_id = app_id
        self._runs_cache = ()
        self._next_token = None
        if self._selected_id is not None:
            self._selected_id = None
            self._hub.send(PropertyChangedMessage.create(self, "emr.job_runs", "selected_id"))
        self._hub.send(PropertyChangedMessage.create(self, "emr.job_runs", "runs"))
        self._set_state(PaneState.LOADING if app_id is not None else PaneState.EMPTY)

    def select(self, run_id: str) -> None:
        if self._selected_id == run_id:
            return
        self._selected_id = run_id
        self._hub.send(PropertyChangedMessage.create(self, "emr.job_runs", "selected_id"))

    def set_state_filter(self, states: frozenset[JobRunState]) -> None:
        if states == self._state_filter:
            return
        self._state_filter = states
        self._hub.send(PropertyChangedMessage.create(self, "emr.job_runs", "state_filter"))
        self._hub.send(PropertyChangedMessage.create(self, "emr.job_runs", "runs"))

    def toggle_state_filter(self, state: JobRunState) -> None:
        states = set(self._state_filter)
        if state in states:
            states.discard(state)
        else:
            states.add(state)
        self.set_state_filter(frozenset(states))

    async def refresh(self) -> None:
        """Reset paging and fetch the first page of runs.

        Wipes the accumulated cache + next-token, then drains one
        boto page through :meth:`list_job_runs_page`. ``load_more``
        is what walks subsequent pages — refresh is the "from
        scratch" path triggered by application switch, manual ``r``,
        or the periodic poller."""
        if self._application_id is None:
            self._runs_cache = ()
            self._next_token = None
            self._set_state(PaneState.EMPTY)
            return
        self._set_state(PaneState.LOADING)
        try:
            # Fetch unfiltered; filter is applied client-side via `runs` property.
            runs, next_token = await self._client.list_job_runs_page(
                self._application_id, start_token=None, states=None
            )
        except ProviderError as exc:
            new_state, self._error_text = map_provider_error(exc)
            self._set_state(new_state)
            return
        self._runs_cache = tuple(runs)
        self._next_token = next_token
        # Drop stale selection if the run vanished.
        if self._selected_id is not None and not any(
            r.job_run_id == self._selected_id for r in runs
        ):
            self._selected_id = None
            self._hub.send(PropertyChangedMessage.create(self, "emr.job_runs", "selected_id"))
        self._hub.send(PropertyChangedMessage.create(self, "emr.job_runs", "runs"))
        self._set_state(PaneState.IDLE if self.runs else PaneState.EMPTY)

    async def load_more(self) -> None:
        """Fetch the next page using ``next_token`` and append to
        the accumulator. No-op when ``has_more`` is False or no
        application is selected. Errors map the same way as
        :meth:`refresh`; on error we KEEP the existing accumulated
        runs (no destructive reset on a paging failure)."""
        if self._application_id is None or self._next_token is None:
            return
        try:
            runs, next_token = await self._client.list_job_runs_page(
                self._application_id, start_token=self._next_token, states=None
            )
        except ProviderError as exc:
            new_state, self._error_text = map_provider_error(exc)
            self._set_state(new_state)
            return
        if not runs and next_token is None:
            # Server returned an empty page + no more — just clear
            # the token so the sentinel goes away.
            self._next_token = None
            self._hub.send(PropertyChangedMessage.create(self, "emr.job_runs", "runs"))
            return
        self._runs_cache = self._runs_cache + tuple(runs)
        self._next_token = next_token
        self._hub.send(PropertyChangedMessage.create(self, "emr.job_runs", "runs"))

    def has_active_runs(self) -> bool:
        """Used by the page-VM poller to choose between the active
        (60-s) and idle (~6-min) cadences. ``Active'' means any
        pre-terminal state —
        ``SUBMITTED`` / ``PENDING`` / ``SCHEDULED`` / ``QUEUED`` /
        ``RUNNING`` (the user expects rapid updates while these
        churn) and ``CANCELLING`` (the user just hit cancel; the
        state landing on ``CANCELLED`` is the signal they're
        waiting for)."""
        return any(r.state in _ACTIVE_STATES for r in self._runs_cache)

    # ── Lifecycle ───────────────────────────────────────────────────────────

    def construct(self) -> None:
        self._inner.construct()

    def dispose(self) -> None:
        self._inner.dispose()

    # ── Internal ────────────────────────────────────────────────────────────

    def _set_state(self, state: PaneState) -> None:
        if self._state == state:
            return
        self._state = state
        self._hub.send(PropertyChangedMessage.create(self, "emr.job_runs", "state"))


__all__ = ["JobRunsVM"]
