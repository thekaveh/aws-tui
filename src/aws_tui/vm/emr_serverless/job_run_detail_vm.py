"""JobRunDetailVM — RIGHT pane's detail view.

PR-A holds detail only; PR-B adds the log surface as a child VM
under this one. The detail is re-fetched on each ``refresh()`` so
the page-VM's 5-s poller can refresh while the run is non-terminal."""

from __future__ import annotations

from typing import Any

import reactivex as rx
from reactivex.subject import Subject
from vmx import ComponentVMOf, Message, MessageHub, PropertyChangedMessage
from vmx.services.dispatcher import Dispatcher

from aws_tui.domain.emr_serverless import JobRunDetail, JobRunState
from aws_tui.domain.filesystem import ProviderError
from aws_tui.vm.emr_serverless._errors import map_provider_error
from aws_tui.vm.file_manager.pane_vm import PaneState

_TERMINAL_STATES: frozenset[JobRunState] = frozenset(
    {JobRunState.SUCCESS, JobRunState.FAILED, JobRunState.CANCELLED}
)


class JobRunDetailVM:
    def __init__(
        self,
        *,
        client: Any,
        hub: MessageHub[Message],
        dispatcher: Dispatcher,
    ) -> None:
        self._client = client
        self._hub: MessageHub[Message] = hub
        self._application_id: str | None = None
        self._job_run_id: str | None = None
        self._detail: JobRunDetail | None = None
        self._state: PaneState = PaneState.EMPTY
        self._error_text: str | None = None
        self._disposed: bool = False
        self._inner: ComponentVMOf[None] = (
            ComponentVMOf[None]
            .builder()
            .name("emr.job_run_detail")
            .model(None)
            .services(hub, dispatcher)
            .build()
        )
        # Per-VM Observable (round-3 §9.bis.11 / PR #103 retirement
        # path): fires the name of the property that just changed,
        # scoped to THIS VM instance. The detail-pane view subscribes
        # here instead of filtering shared MessageHub events by
        # ``sender_object``.
        self._on_property_changed: Subject[str] = Subject()

    # ── Public surface ──────────────────────────────────────────────────────

    @property
    def detail(self) -> JobRunDetail | None:
        return self._detail

    @property
    def state(self) -> PaneState:
        return self._state

    @property
    def error_text(self) -> str | None:
        return self._error_text

    @property
    def on_property_changed(self) -> rx.Observable[str]:
        """Per-VM-instance Observable scoped to THIS detail VM. PR
        #103 retirement path — Views subscribing here are immune to
        cross-VM `state` collisions on the shared hub."""
        return self._on_property_changed

    def set_target(self, application_id: str | None, job_run_id: str | None) -> None:
        """Re-point the detail view at a new run. If either id is
        None, clears."""
        if (application_id, job_run_id) == (self._application_id, self._job_run_id):
            return
        self._application_id = application_id
        self._job_run_id = job_run_id
        self._detail = None
        if application_id is None or job_run_id is None:
            self._set_state(PaneState.EMPTY)
        else:
            self._set_state(PaneState.LOADING)
        self._notify("detail")

    def is_terminal_state(self) -> bool:
        return self._detail is not None and self._detail.state in _TERMINAL_STATES

    async def refresh(self) -> None:
        if self._application_id is None or self._job_run_id is None:
            self._set_state(PaneState.EMPTY)
            return
        self._set_state(PaneState.LOADING)
        try:
            d = await self._client.get_job_run(self._application_id, self._job_run_id)
        except ProviderError as exc:
            new_state, self._error_text = map_provider_error(exc)
            self._set_state(new_state)
            return
        self._detail = d
        self._notify("detail")
        self._set_state(PaneState.IDLE)

    # ── Lifecycle ───────────────────────────────────────────────────────────

    def construct(self) -> None:
        self._inner.construct()

    def dispose(self) -> None:
        if self._disposed:
            return
        self._disposed = True
        self._on_property_changed.on_completed()
        self._on_property_changed.dispose()
        self._inner.dispose()

    # ── Internal ────────────────────────────────────────────────────────────

    def _notify(self, prop: str) -> None:
        """Emit a PropertyChanged event on BOTH the shared hub AND
        the per-VM-instance Observable (round-3 / PR #103 retirement
        path). Mirrors the helper on the other EMR VMs."""
        self._hub.send(PropertyChangedMessage.create(self, "emr.job_run_detail", prop))
        self._on_property_changed.on_next(prop)

    def _set_state(self, state: PaneState) -> None:
        if self._state == state:
            return
        self._state = state
        self._notify("state")


__all__ = ["JobRunDetailVM"]
