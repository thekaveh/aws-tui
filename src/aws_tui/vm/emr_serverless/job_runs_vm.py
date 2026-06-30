"""JobRunsVM — LEFT pane's run-list state.

Scoped to one application at a time. State-filter chips are
multi-select with all-on default; toggling a chip re-applies the
filter against the cached list — no API call. ``refresh()`` is the
only thing that re-fetches.

Phase 2 of the toolkit-adoption refactor (spec §4.2.1, §9.bis.2,
§9.bis.11): the VM composes a :class:`CompositeVM` internally over a
:class:`JobRunItemVM` facade per run. Selection lives in the
composite's ``current`` slot — exposed publicly only as
``selected_id`` (derived). Forward-only ``nextToken`` pagination is
NOT delegated to ``PagedComposition`` (misfit per §9.bis.2); it stays
as a VM-level ``_next_token`` field with ``load_more`` /
``refresh`` methods that append to / reset the composite. The
composite is NOT exposed in the public surface (round-3 directive
§9.bis.11).

Closes the §9.bis.9 acceptance criteria for PR #99(b)
(no ``selected_id`` PropertyChanged for a hub-watch to mis-route)
and PR #100(a) (single ``on_current_changed`` on selection move).
"""

from __future__ import annotations

from typing import Any

import reactivex as rx
from reactivex.subject import Subject
from vmx import ComponentVMOf, CompositeVM, Message, MessageHub, PropertyChangedMessage
from vmx.lifecycle.status import ConstructionStatus
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


class JobRunItemVM:
    """A single job-run row backed by a VMx ``ComponentVMOf``.

    The facade lets ``JobRunsVM`` parent the job-run summaries into
    its inner ``CompositeVM`` (CompositeVM's children must be
    ``_ComponentVMBase`` instances). The summary is the public
    payload; ``inner`` is the composite's child handle.
    """

    def __init__(
        self,
        *,
        summary: JobRunSummary,
        hub: MessageHub[Message],
        dispatcher: Dispatcher,
    ) -> None:
        self._summary: JobRunSummary = summary
        self._inner: ComponentVMOf[JobRunSummary] = (
            ComponentVMOf[JobRunSummary]
            .builder()
            .name(f"emr.job_run.{summary.job_run_id}")
            .model(summary)
            .services(hub, dispatcher)
            .build()
        )

    @property
    def summary(self) -> JobRunSummary:
        return self._summary

    @property
    def inner(self) -> ComponentVMOf[JobRunSummary]:
        return self._inner

    @property
    def is_constructed(self) -> bool:
        return self._inner.is_constructed

    def construct(self) -> None:
        self._inner.construct()

    def dispose(self) -> None:
        self._inner.dispose()


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
        self._items: list[JobRunItemVM] = []  # paged accumulator (unfiltered)
        self._next_token: str | None = None
        self._state: PaneState = PaneState.EMPTY
        self._error_text: str | None = None
        self._state_filter: frozenset[JobRunState] = _ALL_STATES
        self._disposed: bool = False
        # Per-VM Observable (round-3 / PR #103 retirement path): fires
        # the name of the property that just changed, scoped to THIS
        # VM instance. Views can subscribe here instead of filtering
        # ``MessageHub`` events by ``sender_object``.
        self._on_property_changed: Subject[str] = Subject()
        # CompositeVM owns the per-row VMs + the canonical ``current``
        # slot. ``selected_id`` is derived; the composite is NOT
        # exposed in the public surface (round-3 directive §9.bis.11).
        self._inner: CompositeVM[ComponentVMOf[JobRunSummary]] = (
            CompositeVM[ComponentVMOf[JobRunSummary]]
            .builder()
            .name("emr.job_runs")
            .services(hub, dispatcher)
            .children(self._initial_children)
            .auto_construct_on_add(True)
            .build()
        )

    # ── Public surface ──────────────────────────────────────────────────────

    @property
    def runs(self) -> tuple[JobRunSummary, ...]:
        return tuple(
            item.summary for item in self._items if item.summary.state in self._state_filter
        )

    @property
    def selected_id(self) -> str | None:
        current = self._inner.current
        if current is None:
            return None
        return current.model.job_run_id

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

    @property
    def status(self) -> ConstructionStatus:
        return self._inner.status

    @property
    def is_constructed(self) -> bool:
        return self._inner.is_constructed

    @property
    def on_property_changed(self) -> rx.Observable[str]:
        """Per-VM-instance Observable that fires the name of the
        property that just changed (round-3 / PR #103 retirement
        path). Views subscribing here are immune to cross-VM
        ``state`` PropertyChanged collisions on a shared
        ``MessageHub``."""
        return self._on_property_changed

    def set_application(self, app_id: str | None) -> None:
        """Re-scope to a new application. Clears selection + cache;
        caller must subsequently call :meth:`refresh`."""
        if self._application_id == app_id:
            return
        self._application_id = app_id
        prior_selected_id = self.selected_id
        self._clear_items()
        self._next_token = None
        # Clear the prior app's error text — without this the
        # window between set_application and refresh's next state
        # write briefly carries the OLD app's error_text alongside
        # the NEW state. Sibling parity with JobRunLogsVM.set_target
        # and PaneVM._reload, both of which clear error_text at
        # the equivalent seam.
        self._error_text = None
        if prior_selected_id is not None:
            # Composite.clear() already drops current to None; the
            # public ``selected_id`` event mirrors that for View
            # consumers.
            self._notify("selected_id")
        self._notify("runs")
        self._set_state(PaneState.LOADING if app_id is not None else PaneState.EMPTY)

    def select(self, run_id: str) -> None:
        if self.selected_id == run_id:
            return
        match: JobRunItemVM | None = None
        for item in self._items:
            if item.summary.job_run_id == run_id:
                match = item
                break
        if match is None:
            # Unknown id — silent no-op.
            return
        self._inner.current = match.inner
        self._notify("selected_id")

    def set_state_filter(self, states: frozenset[JobRunState]) -> None:
        if states == self._state_filter:
            return
        self._state_filter = states
        self._notify("state_filter")
        self._notify("runs")

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
            self._clear_items()
            self._next_token = None
            self._set_state(PaneState.EMPTY)
            return
        # Capture the target identity BEFORE the await so a
        # concurrent ``set_application(B)`` (from picker / Shift+S)
        # that lands while ``list_job_runs_page`` is in flight
        # doesn't let app A's late response write into the
        # accumulator under app B's identity. The pollers
        # (``_tick_runs``) and user actions (``select_application``,
        # ``cycle_application``) run in DIFFERENT Textual worker
        # groups, so ``exclusive=True`` does not protect against
        # cross-group interleaving.
        target_app_id = self._application_id
        self._set_state(PaneState.LOADING)
        try:
            # Fetch unfiltered; filter is applied client-side via `runs` property.
            runs, next_token = await self._client.list_job_runs_page(
                target_app_id, start_token=None, states=None
            )
        except ProviderError as exc:
            if self._application_id != target_app_id:
                return  # target changed mid-flight; drop the stale error
            new_state, self._error_text = map_provider_error(exc)
            self._set_state(new_state)
            return
        except Exception as exc:  # defensive
            # Non-ProviderError escape (botocore param-validation,
            # OSError, programmer bug). Same shield JobRunLogsVM
            # already has — without it the worker exception is
            # swallowed by Textual's run_worker and the runs pane
            # is permanently stuck on LOADING.
            if self._application_id != target_app_id:
                return
            self._error_text = f"unexpected error: {exc}"
            self._set_state(PaneState.ERROR)
            return
        if self._application_id != target_app_id:
            return  # target changed mid-flight; drop the stale response
        new_runs: tuple[JobRunSummary, ...] = tuple(runs)
        prior_selected_id = self.selected_id

        # Dedup-on-set parity with ApplicationsVM (§9.bis.9 Q-A): if
        # the freshly-fetched page matches the current accumulator
        # head, the composite is NOT mutated and no `runs`/
        # `selected_id` events fire. The next_token may still differ
        # (the server can grow the result set without changing the
        # first page); persist it unconditionally below.
        unchanged = self._items_equal(new_runs)
        if not unchanged:
            self._clear_items()
            for summary in new_runs:
                self._add_item(summary)
            # CompositeVM.clear() during _clear_items drops current
            # to None automatically. Restore selection by id if still
            # present; otherwise emit the user-visible "selected_id"
            # event so consumers update their cursor.
            if prior_selected_id is not None:
                restored = False
                for item in self._items:
                    if item.summary.job_run_id == prior_selected_id:
                        self._inner.current = item.inner
                        restored = True
                        break
                if not restored:
                    self._notify("selected_id")
            self._notify("runs")
        self._next_token = next_token
        # Success path — drop any error text carried forward from a
        # prior failed poll (sibling parity with PaneVM._reload).
        self._error_text = None
        self._set_state(PaneState.IDLE if self.runs else PaneState.EMPTY)

    async def load_more(self) -> None:
        """Fetch the next page using ``next_token`` and append to
        the accumulator. No-op when ``has_more`` is False or no
        application is selected. Errors map the same way as
        :meth:`refresh`; on error we KEEP the existing accumulated
        runs (no destructive reset on a paging failure)."""
        if self._application_id is None or self._next_token is None:
            return
        # Capture FULL pagination identity BEFORE the await:
        # - app_id: a concurrent set_application landing mid-flight
        #   would let stale rows leak into the new app's accumulator
        # - next_token: a concurrent refresh() (different worker
        #   group ``emr-poll-runs``) replaces _items and resets
        #   _next_token mid-flight. If we only checked app_id, the
        #   late load_more response would still _add_item rows
        #   paginated from the STALE T1 cursor + overwrite
        #   _next_token with the stale T3 continuation, leaving the
        #   accumulator straddling two different pagination
        #   lineages. (Round 14 added the app_id half; this closes
        #   the token half — same root race.)
        target_app_id = self._application_id
        target_token = self._next_token
        try:
            runs, next_token = await self._client.list_job_runs_page(
                target_app_id, start_token=target_token, states=None
            )
        except ProviderError as exc:
            if (self._application_id, self._next_token) != (target_app_id, target_token):
                return  # pagination identity changed mid-flight; drop the stale error
            new_state, self._error_text = map_provider_error(exc)
            self._set_state(new_state)
            return
        if (self._application_id, self._next_token) != (target_app_id, target_token):
            return  # pagination identity changed mid-flight; drop the stale response
        if not runs and next_token is None:
            # Server returned an empty page + no more — just clear
            # the token so the sentinel goes away.
            self._next_token = None
            self._notify("runs")
            return
        for summary in runs:
            self._add_item(summary)
        self._next_token = next_token
        self._notify("runs")

    def has_active_runs(self) -> bool:
        """Used by the page-VM poller to choose between the active
        (60-s) and idle (~6-min) cadences. ``Active'' means any
        pre-terminal state —
        ``SUBMITTED`` / ``PENDING`` / ``SCHEDULED`` / ``QUEUED`` /
        ``RUNNING`` (the user expects rapid updates while these
        churn) and ``CANCELLING`` (the user just hit cancel; the
        state landing on ``CANCELLED`` is the signal they're
        waiting for)."""
        return any(item.summary.state in _ACTIVE_STATES for item in self._items)

    # ── Lifecycle ───────────────────────────────────────────────────────────

    def construct(self) -> None:
        self._inner.construct()

    def dispose(self) -> None:
        if self._disposed:
            return
        self._disposed = True
        for item in self._items:
            item.dispose()
        self._items.clear()
        self._on_property_changed.on_completed()
        self._on_property_changed.dispose()
        self._inner.dispose()

    # ── Internal ────────────────────────────────────────────────────────────

    def _items_equal(self, new_runs: tuple[JobRunSummary, ...]) -> bool:
        """Identity check for the dedup-on-set guard. The first N
        entries of the new page must match our shadow ``_items`` 1:1
        for refresh to be a no-op. Mirror of ApplicationsVM._items_equal.
        """
        if len(self._items) != len(new_runs):
            return False
        for item, summary in zip(self._items, new_runs, strict=True):
            if item.summary != summary:
                return False
        return True

    def _initial_children(self) -> tuple[ComponentVMOf[JobRunSummary], ...]:
        # CompositeVM builder requires a children factory even when the
        # initial population is empty. Items are added at runtime via
        # _add_item; this seed runs once at construct().
        return tuple(item.inner for item in self._items)

    def _clear_items(self) -> None:
        for item in list(self._items):
            if item.inner in self._inner:
                self._inner.remove(item.inner)
            item.dispose()
        self._items.clear()

    def _add_item(self, summary: JobRunSummary) -> None:
        item = JobRunItemVM(summary=summary, hub=self._hub, dispatcher=self._dispatcher)
        self._items.append(item)
        if self._inner.is_constructed:
            item.construct()
        self._inner.append(item.inner)

    def _notify(self, prop: str) -> None:
        """Emit a PropertyChanged event on BOTH the shared hub AND
        the per-VM-instance Observable (round-3 / PR #103 retirement
        path)."""
        self._hub.send(PropertyChangedMessage.create(self, "emr.job_runs", prop))
        self._on_property_changed.on_next(prop)

    def _set_state(self, state: PaneState) -> None:
        if self._state == state:
            return
        self._state = state
        self._notify("state")


__all__ = ["JobRunItemVM", "JobRunsVM"]
