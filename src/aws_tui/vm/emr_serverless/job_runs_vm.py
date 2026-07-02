"""JobRunsVM — LEFT pane's run-list state.

Scoped to one application at a time. State-filter chips are
multi-select with all-on default; toggling a chip re-applies the
filter against the cached list — no API call. ``refresh()`` is the
only thing that re-fetches.

Phase 2 of the toolkit-adoption refactor (spec §4.2.1, §9.bis.2,
§9.bis.11): the VM composes a :class:`CompositeVM` internally over a
:class:`JobRunItemVM` facade per run. Selection lives in the
composite's ``current`` slot — exposed publicly only as
``selected_id`` (derived). Forward-only AWS ``nextToken`` pagination
is delegated to VMx ``TokenPagedComposition`` while this facade keeps
target-staleness guards, filtering, selection restoration, and
PropertyChanged notifications. Neither the pager nor the composite is
exposed in the public surface (round-3 directive §9.bis.11).

Closes the §9.bis.9 acceptance criteria for PR #99(b)
(no ``selected_id`` PropertyChanged for a hub-watch to mis-route)
and PR #100(a) (single ``on_current_changed`` on selection move).
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import reactivex as rx
from reactivex.subject import Subject
from vmx import (
    ComponentVMOf,
    CompositeVM,
    Message,
    MessageHub,
    PropertyChangedMessage,
    TokenPagedComposition,
)
from vmx.lifecycle.status import ConstructionStatus
from vmx.services.dispatcher import Dispatcher

from aws_tui.domain.emr_serverless import JobRunState, JobRunSummary
from aws_tui.domain.filesystem import ProviderError
from aws_tui.infra.redaction import redact_text
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
        self._pager_refresh_existing_count: int = 0
        self._paging_identity: tuple[str | None, str | None] | None = None
        self._pager: TokenPagedComposition[JobRunItemVM, str] = self._new_pager()
        self._has_more_suppressed: bool = False
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
    def _items(self) -> list[JobRunItemVM]:
        return self._pager.items

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
        return self._pager.current_token is not None and not self._has_more_suppressed

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
        self._has_more_suppressed = False
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
        self._has_more_suppressed = False
        prior_items = self._items
        prior_selected_id = self.selected_id
        self._pager_refresh_existing_count = len(prior_items)
        self._paging_identity = (target_app_id, None)
        try:
            await self._pager.refresh_command.execute_async()
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
            self._error_text = redact_text(f"unexpected error: {exc}")
            self._set_state(PaneState.ERROR)
            return
        finally:
            self._paging_identity = None
        if self._application_id != target_app_id:
            return  # target changed mid-flight; drop the stale response

        # Dedup-on-set parity with ApplicationsVM (§9.bis.9 Q-A): if the
        # pager kept the same item objects, the composite is NOT mutated and
        # no `runs` / `selected_id` events fire. The token may still differ;
        # TokenPagedComposition persists it internally.
        unchanged = self._items == prior_items
        if not unchanged:
            self._sync_inner_to_pager(prior_items, prior_selected_id=prior_selected_id)
            self._notify("runs")
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
        if self._application_id is None or not self.has_more:
            return
        # Capture the app + token lineage before the await; concurrent
        # refresh/application switches must not append stale pages.
        target_app_id = self._application_id
        target_token = self._pager.current_token
        prior_items = self._items
        self._paging_identity = (target_app_id, target_token)
        try:
            await self._pager.load_more_command.execute_async()
        except ProviderError as exc:
            # load_more is APPEND-only — a failure on the next-page
            # fetch must NOT destroy the already-loaded rows by
            # transitioning the whole pane to ERROR/UNREACHABLE/
            # FORBIDDEN (the view would early-return on every
            # non-IDLE state and render only the placeholder,
            # hiding the user's existing 50 rows and cursor). Keep
            # the prior IDLE state intact; record the error text so
            # a future "retry pagination" surface can show it; drop
            # the token so the broken sentinel goes away. The user
            # can hit ``r`` to retry the whole list if they want
            # pagination back.
            if (self._application_id, self._pager.current_token) != (
                target_app_id,
                target_token,
            ):
                return  # pagination identity changed mid-flight; drop the stale error
            text = str(exc)
            self._error_text = redact_text(text) if text else None
            self._has_more_suppressed = True
            self._notify("runs")
            return
        except Exception as exc:  # defensive
            # Non-ProviderError escape (botocore param-validation,
            # OSError from socket layer, programmer bug). Same
            # shield the four refresh paths got in round 33. Without
            # it the worker exception is silently swallowed by
            # Textual's run_worker and PgDn appears to do nothing
            # with zero diagnostic.
            if (self._application_id, self._pager.current_token) != (
                target_app_id,
                target_token,
            ):
                return
            self._error_text = redact_text(f"unexpected error: {exc}")
            self._has_more_suppressed = True
            self._notify("runs")
            return
        finally:
            self._paging_identity = None
        if (self._application_id, target_token) != (target_app_id, target_token):
            return  # pagination identity changed mid-flight; drop the stale response
        if self._items == prior_items:
            # Server returned an empty page or a stale no-op; just refresh the
            # sentinel if the token changed.
            self._notify("runs")
            return
        self._sync_inner_to_pager(prior_items, prior_selected_id=self.selected_id)
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
        self._clear_items()
        self._pager.dispose()
        self._on_property_changed.on_completed()
        self._on_property_changed.dispose()
        self._inner.dispose()

    # ── Internal ────────────────────────────────────────────────────────────

    def _new_pager(self) -> TokenPagedComposition[JobRunItemVM, str]:
        return TokenPagedComposition(
            self._fetch_page,
            pages_equal=self._pages_equal,
        )

    def _pages_equal(
        self,
        fresh: Sequence[JobRunItemVM],
        current_prefix: Sequence[JobRunItemVM],
    ) -> bool:
        if self._pager_refresh_existing_count != len(fresh):
            return False
        return [item.summary for item in fresh] == [item.summary for item in current_prefix]

    async def _fetch_page(self, token: str | None) -> tuple[tuple[JobRunItemVM, ...], str | None]:
        target_app_id = self._application_id
        runs, next_token = await self._client.list_job_runs_page(
            target_app_id,
            start_token=token,
            states=None,
        )
        if self._paging_identity is not None:
            app_id, expected_token = self._paging_identity
            if token is None and self._application_id != app_id:
                return tuple(self._pager.items), self._pager.current_token
            if token is not None and (self._application_id, self._pager.current_token) != (
                app_id,
                expected_token,
            ):
                return (), self._pager.current_token
        items = tuple(
            JobRunItemVM(summary=summary, hub=self._hub, dispatcher=self._dispatcher)
            for summary in runs
        )
        return items, next_token

    def _initial_children(self) -> tuple[ComponentVMOf[JobRunSummary], ...]:
        return tuple(item.inner for item in self._items)

    def _clear_items(self) -> None:
        for item in list(self._items):
            if item.inner in self._inner:
                self._inner.remove(item.inner)
            item.dispose()
        self._pager.dispose()
        self._pager = self._new_pager()

    def _sync_inner_to_pager(
        self,
        prior_items: Sequence[JobRunItemVM],
        *,
        prior_selected_id: str | None,
    ) -> None:
        current_items = self._items
        current_set = set(current_items)
        for item in prior_items:
            if item in current_set:
                continue
            if item.inner in self._inner:
                self._inner.remove(item.inner)
            item.dispose()
        for item in current_items:
            if item.inner in self._inner:
                continue
            if self._inner.is_constructed:
                item.construct()
            self._inner.append(item.inner)
        if prior_selected_id is None:
            return
        restored = False
        for item in current_items:
            if item.summary.job_run_id == prior_selected_id:
                self._inner.current = item.inner
                restored = True
                break
        if not restored:
            self._notify("selected_id")

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
