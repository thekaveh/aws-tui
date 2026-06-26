"""JobRunDetailVM — RIGHT pane's detail view.

PR-A holds detail only; PR-B adds the log surface as a child VM
under this one. The detail is re-fetched on each ``refresh()`` so
the page-VM's 5-s poller can refresh while the run is non-terminal."""

from __future__ import annotations

from typing import Any

from vmx import ComponentVMOf, Message, MessageHub, PropertyChangedMessage
from vmx.services.dispatcher import Dispatcher

from aws_tui.domain.emr_serverless import JobRunDetail, JobRunState
from aws_tui.domain.filesystem import (
    AuthRequiredError,
    PermissionDeniedError,
    ProviderError,
    ProviderUnreachableError,
)
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
        self._dispatcher: Dispatcher = dispatcher
        self._application_id: str | None = None
        self._job_run_id: str | None = None
        self._detail: JobRunDetail | None = None
        self._state: PaneState = PaneState.EMPTY
        self._error_text: str | None = None
        self._inner: ComponentVMOf[None] = (
            ComponentVMOf[None]
            .builder()
            .name("emr.job_run_detail")
            .model(None)
            .services(hub, dispatcher)
            .build()
        )

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
        self._hub.send(PropertyChangedMessage.create(self, "emr.job_run_detail", "detail"))

    def is_terminal_state(self) -> bool:
        return self._detail is not None and self._detail.state in _TERMINAL_STATES

    async def refresh(self) -> None:
        if self._application_id is None or self._job_run_id is None:
            self._set_state(PaneState.EMPTY)
            return
        self._set_state(PaneState.LOADING)
        try:
            d = await self._client.get_job_run(self._application_id, self._job_run_id)
        except AuthRequiredError as exc:
            self._error_text = str(exc) or None
            self._set_state(PaneState.AUTH_REQUIRED)
            return
        except ProviderUnreachableError as exc:
            self._error_text = str(exc) or None
            self._set_state(PaneState.UNREACHABLE)
            return
        except PermissionDeniedError as exc:
            self._error_text = str(exc) or None
            self._set_state(PaneState.FORBIDDEN)
            return
        except ProviderError as exc:
            self._error_text = str(exc) or None
            self._set_state(PaneState.ERROR)
            return
        self._detail = d
        self._hub.send(PropertyChangedMessage.create(self, "emr.job_run_detail", "detail"))
        self._set_state(PaneState.IDLE)

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
        self._hub.send(PropertyChangedMessage.create(self, "emr.job_run_detail", "state"))


__all__ = ["JobRunDetailVM"]
