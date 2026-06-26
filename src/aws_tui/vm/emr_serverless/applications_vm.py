"""ApplicationsVM — backs the top-strip application picker.

Holds the live application list, the currently-selected application
id, and a coarse :class:`PaneState` so the dropdown can render a
loading spinner / error placeholder."""

from __future__ import annotations

from typing import Any

from vmx import ComponentVMOf, Message, MessageHub, PropertyChangedMessage
from vmx.services.dispatcher import Dispatcher

from aws_tui.domain.emr_serverless import ApplicationSummary
from aws_tui.domain.filesystem import ProviderError
from aws_tui.vm.emr_serverless._errors import map_provider_error
from aws_tui.vm.file_manager.pane_vm import PaneState


class ApplicationsVM:
    """Live application list + selection state."""

    def __init__(
        self,
        *,
        client: Any,  # EmrServerlessClient or _InMemoryEmr — see PR-A spec §1
        hub: MessageHub[Message],
        dispatcher: Dispatcher,
    ) -> None:
        self._client = client
        self._hub: MessageHub[Message] = hub
        self._dispatcher: Dispatcher = dispatcher
        self._applications: tuple[ApplicationSummary, ...] = ()
        self._selected_id: str | None = None
        self._state: PaneState = PaneState.LOADING
        self._error_text: str | None = None
        # VMx component wrapper — gives sub-VM hierarchy + dispose plumbing.
        self._inner: ComponentVMOf[None] = (
            ComponentVMOf[None]
            .builder()
            .name("emr.applications")
            .model(None)
            .services(hub, dispatcher)
            .build()
        )

    # ── Public surface ──────────────────────────────────────────────────────

    @property
    def applications(self) -> tuple[ApplicationSummary, ...]:
        return self._applications

    @property
    def selected_id(self) -> str | None:
        return self._selected_id

    @property
    def state(self) -> PaneState:
        return self._state

    @property
    def error_text(self) -> str | None:
        return self._error_text

    def select(self, app_id: str) -> None:
        """Mark ``app_id`` as the active application. No-op if already selected."""
        if self._selected_id == app_id:
            return
        self._selected_id = app_id
        self._hub.send(PropertyChangedMessage.create(self, "emr.applications", "selected_id"))

    async def refresh(self) -> None:
        """Re-fetch the application list. Updates ``state``,
        ``applications``, and (if the prior selection went missing)
        ``selected_id``."""
        self._set_state(PaneState.LOADING)
        try:
            apps = await self._client.list_applications()
        except ProviderError as exc:
            new_state, self._error_text = map_provider_error(exc)
            self._set_state(new_state)
            return
        self._applications = tuple(apps)
        # Drop a stale selection if the app no longer exists.
        if self._selected_id is not None and not any(a.id == self._selected_id for a in apps):
            self._selected_id = None
            self._hub.send(PropertyChangedMessage.create(self, "emr.applications", "selected_id"))
        self._hub.send(PropertyChangedMessage.create(self, "emr.applications", "applications"))
        self._set_state(PaneState.IDLE if apps else PaneState.EMPTY)

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
        self._hub.send(PropertyChangedMessage.create(self, "emr.applications", "state"))


__all__ = ["ApplicationsVM"]
