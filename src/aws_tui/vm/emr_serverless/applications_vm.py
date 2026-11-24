"""ApplicationsVM — backs the top-strip application picker.

Holds the live application list, the currently-selected application
id, and a coarse :class:`PaneState` so the dropdown can render a
loading spinner / error placeholder.

The private :class:`CompositeVM` owns one :class:`ComponentVMOf` row per
application id. Refresh updates surviving rows in place and projects the
selected id from ``CompositeVM.current``.
"""

from __future__ import annotations

import asyncio

import reactivex as rx
from vmx import (
    ComponentVMOf,
    CompositeVM,
    ConstructionStatus,
    Dispatcher,
    Message,
    MessageHub,
    PropertyChangedMessage,
)

from aws_tui.domain.emr_serverless import (
    ApplicationState,
    ApplicationSummary,
    EmrServerlessClientProtocol,
)
from aws_tui.domain.filesystem import ProviderError
from aws_tui.infra.redaction import redact_text
from aws_tui.vm._observable import ObserverSafeSubject, send_value_free
from aws_tui.vm._reconcile import reconcile_rows
from aws_tui.vm.emr_serverless._errors import map_provider_error
from aws_tui.vm.file_manager.pane_vm import PaneState
from aws_tui.vm.service_diagnostics import report_unexpected_service_error

#: Single source of truth for the user-facing application order.
#: STARTED first, then transitional (STARTING / STOPPING), then
#: non-active (CREATING / CREATED / STOPPED), then terminal
#: (TERMINATED). The picker dropdown and the Shift+A cycle both
#: consume :attr:`ApplicationsVM.sorted_applications` so the order
#: the user sees in the dropdown is the same order they cycle
#: through with the keybinding.
_APP_STATE_SORT: dict[ApplicationState, int] = {
    ApplicationState.STARTED: 0,
    ApplicationState.STARTING: 1,
    ApplicationState.STOPPING: 2,
    ApplicationState.CREATING: 3,
    ApplicationState.CREATED: 4,
    ApplicationState.STOPPED: 5,
    ApplicationState.TERMINATED: 6,
}


def _application_sort_key(application: ApplicationSummary) -> tuple[int, str, str]:
    return (
        _APP_STATE_SORT.get(application.state, 99),
        application.name,
        application.id,
    )


class ApplicationsVM:
    """Live application list + selection state."""

    def __init__(
        self,
        *,
        client: EmrServerlessClientProtocol,
        hub: MessageHub[Message],
        dispatcher: Dispatcher,
    ) -> None:
        self._client = client
        self._hub: MessageHub[Message] = hub
        self._dispatcher: Dispatcher = dispatcher
        self._state: PaneState = PaneState.LOADING
        self._error_text: str | None = None
        self._disposed: bool = False
        self._refresh_lock = asyncio.Lock()
        # Per-VM Observable (round-3 / PR #103 retirement path): fires
        # the name of the property that just changed, scoped to THIS
        # VM instance. Views can subscribe here instead of filtering
        # ``MessageHub`` events by ``sender_object``.
        self._on_property_changed = ObserverSafeSubject[str]()
        self._inner: CompositeVM[ComponentVMOf[ApplicationSummary]] = (
            CompositeVM[ComponentVMOf[ApplicationSummary]]
            .builder()
            .name("emr.applications")
            .services(hub, dispatcher)
            .children(lambda: ())
            .auto_construct_on_add(True)
            .build()
        )

    # ── Public surface ──────────────────────────────────────────────────────

    @property
    def applications(self) -> tuple[ApplicationSummary, ...]:
        # The composite retains its children after cascading disposal.
        if self._disposed:
            return ()
        return tuple(row.model for row in self._inner)

    @property
    def sorted_applications(self) -> tuple[ApplicationSummary, ...]:
        """Applications sorted by state group then name.

        Single source of truth for the user-facing application order:
        STARTED first, then transitional / idle / terminated groups,
        alphabetical within each group. The picker dropdown and the
        Shift+A cycle both consume this property — listing and
        cycling stay in lockstep.
        """
        return self.applications

    @property
    def selected_id(self) -> str | None:
        current = self._inner.current
        if current is None:
            return None
        return current.model.id

    @property
    def state(self) -> PaneState:
        return self._state

    @property
    def error_text(self) -> str | None:
        return self._error_text

    @property
    def status(self) -> ConstructionStatus:
        return self._inner.status

    @property
    def is_constructed(self) -> bool:
        return self._inner.is_constructed

    @property
    def on_property_changed(self) -> rx.Observable[str]:
        """Per-VM-instance Observable that fires the name of the
        property that just changed. Subscribing here scopes view
        widgets to THIS VM's events only — no shared-hub
        ``sender_object`` filtering needed (round-3 / PR #103
        retirement path)."""
        return self._on_property_changed

    def select(self, app_id: str) -> None:
        """Mark ``app_id`` as the active application. No-op if already selected."""
        if self._disposed or self.selected_id == app_id:
            return
        for row in self._inner:
            if row.model.id == app_id:
                self._inner.current = row
                self._notify("selected_id")
                return

    async def refresh(self) -> None:
        """Re-fetch applications without allowing stale responses to win."""
        async with self._refresh_lock:
            await self._refresh_serialized()

    async def _refresh_serialized(self) -> None:
        """Re-fetch the application list. Updates ``state``,
        ``applications``, and (if the prior selection went missing)
        ``selected_id``.

        An equal sequence of summaries leaves the composite unchanged
        and skips the ``applications`` notification.
        """
        self._set_state(PaneState.LOADING)
        try:
            apps = await self._client.list_applications()
        except ProviderError as exc:
            new_state, self._error_text = map_provider_error(exc)
            self._set_state(new_state)
            return
        except Exception as exc:  # defensive
            # Non-ProviderError escape — botocore parameter-validation
            # bypassing the facade's mapping, an OSError from the
            # socket layer, a programmer error, etc. Without this net
            # the worker exception is swallowed by Textual's
            # ``run_worker`` machinery and the pane is permanently
            # stuck on LOADING with no user path to recovery (a
            # manual ``r`` re-enters LOADING and re-throws). Same
            # shield JobRunLogsVM.load already has; mirror it here.
            self._error_text = redact_text(f"unexpected error: {exc}")
            report_unexpected_service_error(
                self._hub,
                service="emr-serverless",
                operation="list_applications",
                error=exc,
            )
            self._set_state(PaneState.ERROR)
            return
        new_apps = tuple(sorted(apps, key=_application_sort_key))
        prior_selected_id = self.selected_id

        if self.applications != new_apps:
            reconcile_rows(self._inner, new_apps, key=lambda app: app.id, create=self._new_row)
            self._notify("applications")

        if self.selected_id != prior_selected_id:
            self._notify("selected_id")

        # Success path — drop any error text carried forward from
        # a prior failed poll. Sibling parity with PaneVM._reload
        # (which clears at the top of success). Without this a
        # successful recovery after an error would keep stale
        # error_text alongside IDLE state.
        self._error_text = None
        self._set_state(PaneState.IDLE if new_apps else PaneState.EMPTY)

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

    def _new_row(self, summary: ApplicationSummary) -> ComponentVMOf[ApplicationSummary]:
        return (
            ComponentVMOf[ApplicationSummary]
            .builder()
            .name(f"emr.application.{summary.id}")
            .model(summary)
            .services(self._hub, self._dispatcher)
            .build()
        )

    def _notify(self, prop: str) -> None:
        """Emit a PropertyChanged event on BOTH the shared hub AND
        the per-VM-instance Observable (round-3 / PR #103 retirement
        path). Mirrors the helper on JobRunsVM / JobRunLogsVM."""
        if self._disposed:
            # The Athena and Glue VMs have always guarded this; these four
            # did not, so a late callback could publish a property change
            # for a disposed view model.
            return
        send_value_free(self._hub, PropertyChangedMessage.create(self, "emr.applications", prop))
        self._on_property_changed.on_next(prop)

    def _set_state(self, state: PaneState) -> None:
        if self._state == state:
            return
        self._state = state
        self._notify("state")


__all__ = ["ApplicationsVM"]
