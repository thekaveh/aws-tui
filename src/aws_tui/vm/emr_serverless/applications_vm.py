"""ApplicationsVM — backs the top-strip application picker.

Holds the live application list, the currently-selected application
id, and a coarse :class:`PaneState` so the dropdown can render a
loading spinner / error placeholder.

Phase 1 of the toolkit-adoption refactor (see
``docs/superpowers/specs/2026-06-28-vmx-toolkit-adoption-design.md``
§4.2.3, §9.bis.11, §9.bis.9 / Q-A): the VM composes a
:class:`CompositeVM` internally — each :class:`ApplicationSummary` is
lifted into a small :class:`ApplicationItemVM` facade, and the
selected-id state is projected from ``CompositeVM.current``. The
composite is NOT exposed in the public surface (per the round-3
"compose, don't reject" directive). ``refresh()`` does a dedup-on-set
check so a no-change poll emits zero events — the View-side
fingerprint guard added in PR #100(b) becomes redundant.
"""

from __future__ import annotations

from typing import Any

import reactivex as rx
from reactivex.subject import Subject
from vmx import ComponentVMOf, CompositeVM, Message, MessageHub, PropertyChangedMessage
from vmx.lifecycle.status import ConstructionStatus
from vmx.services.dispatcher import Dispatcher

from aws_tui.domain.emr_serverless import ApplicationState, ApplicationSummary
from aws_tui.domain.filesystem import ProviderError
from aws_tui.vm.emr_serverless._errors import map_provider_error
from aws_tui.vm.file_manager.pane_vm import PaneState

#: Single source of truth for the user-facing application order.
#: STARTED first, then transitional (STARTING / STOPPING), then
#: non-active (CREATING / CREATED / STOPPED), then terminal
#: (TERMINATED). The picker dropdown and the Shift+S cycle both
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


class ApplicationItemVM:
    """A single application row backed by a VMx ``ComponentVMOf``.

    The facade lets ``ApplicationsVM`` parent the application
    summaries into its inner ``CompositeVM`` (CompositeVM's children
    must be ``_ComponentVMBase`` instances). The summary is the
    public payload; ``inner`` is the composite's child handle.
    """

    def __init__(
        self,
        *,
        summary: ApplicationSummary,
        hub: MessageHub[Message],
        dispatcher: Dispatcher,
    ) -> None:
        self._summary: ApplicationSummary = summary
        self._inner: ComponentVMOf[ApplicationSummary] = (
            ComponentVMOf[ApplicationSummary]
            .builder()
            .name(f"emr.application.{summary.id}")
            .model(summary)
            .services(hub, dispatcher)
            .build()
        )

    @property
    def summary(self) -> ApplicationSummary:
        return self._summary

    @property
    def inner(self) -> ComponentVMOf[ApplicationSummary]:
        return self._inner

    @property
    def is_constructed(self) -> bool:
        return self._inner.is_constructed

    def construct(self) -> None:
        self._inner.construct()

    def dispose(self) -> None:
        self._inner.dispose()


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
        self._items: list[ApplicationItemVM] = []
        self._state: PaneState = PaneState.LOADING
        self._error_text: str | None = None
        self._disposed: bool = False
        # Per-VM Observable (round-3 / PR #103 retirement path): fires
        # the name of the property that just changed, scoped to THIS
        # VM instance. Views can subscribe here instead of filtering
        # ``MessageHub`` events by ``sender_object``.
        self._on_property_changed: Subject[str] = Subject()
        # CompositeVM holds the per-row inners; auto_construct_on_add lets
        # the composite construct each new row when added post-construct().
        # Selection lives in the composite's ``current`` slot — exposed
        # publicly only as ``selected_id`` (derived).
        self._inner: CompositeVM[ComponentVMOf[ApplicationSummary]] = (
            CompositeVM[ComponentVMOf[ApplicationSummary]]
            .builder()
            .name("emr.applications")
            .services(hub, dispatcher)
            .children(self._initial_children)
            .auto_construct_on_add(True)
            .build()
        )

    # ── Public surface ──────────────────────────────────────────────────────

    @property
    def applications(self) -> tuple[ApplicationSummary, ...]:
        return tuple(item.summary for item in self._items)

    @property
    def sorted_applications(self) -> tuple[ApplicationSummary, ...]:
        """Applications sorted by state group then name.

        Single source of truth for the user-facing application order:
        STARTED first, then transitional / idle / terminated groups,
        alphabetical within each group. The picker dropdown and the
        Shift+S cycle both consume this property — listing and
        cycling stay in lockstep.
        """
        return tuple(
            sorted(
                self.applications,
                key=lambda a: (_APP_STATE_SORT.get(a.state, 99), a.name),
            )
        )

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
        if self.selected_id == app_id:
            return
        match: ApplicationItemVM | None = None
        for item in self._items:
            if item.summary.id == app_id:
                match = item
                break
        if match is None:
            # Unknown id — silent no-op (matches pre-Phase-1 behaviour).
            return
        self._inner.current = match.inner
        self._notify("selected_id")

    async def refresh(self) -> None:
        """Re-fetch the application list. Updates ``state``,
        ``applications``, and (if the prior selection went missing)
        ``selected_id``.

        Dedup-on-set: if the freshly-fetched list of (id, state, name)
        triples matches the current items, the composite is NOT
        mutated and no PropertyChanged / on_collection_changed events
        fire. This relocates the View-side fingerprint guard added in
        PR #100(b) into the VM layer (round-3 directive §9.bis.11 +
        Q-A in §9.bis.9 / round 2): the View no longer needs its own
        no-change guard.
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
            self._error_text = f"unexpected error: {exc}"
            self._set_state(PaneState.ERROR)
            return
        new_apps: tuple[ApplicationSummary, ...] = tuple(apps)
        prior_selected_id = self.selected_id

        if not self._items_equal(new_apps):
            # Real change — rebuild the composite. Drop the current
            # children first; CompositeVM._remove_at clears
            # ``current`` automatically when its referenced child
            # leaves (composite_vm.py:264-272). After repopulate the
            # composite's identity-based ``current`` does NOT survive
            # (each new ApplicationItemVM has a fresh inner), so we
            # restore the prior selection by id — mirrors JobRunsVM's
            # restore loop. Without this every poll silently
            # unselects the user's chosen app.
            self._clear_items()
            for summary in new_apps:
                self._add_item(summary)
            if prior_selected_id is not None:
                for item in self._items:
                    if item.summary.id == prior_selected_id:
                        self._inner.current = item.inner
                        break
            self._notify("applications")

        # Emit the selected_id event only when the post-rebuild
        # selection differs from prior — covers both the
        # "selection vanished" and "selection survived" cases.
        if self.selected_id != prior_selected_id:
            self._notify("selected_id")

        self._set_state(PaneState.IDLE if new_apps else PaneState.EMPTY)

    # ── Lifecycle ───────────────────────────────────────────────────────────

    def construct(self) -> None:
        self._inner.construct()

    def dispose(self) -> None:
        if self._disposed:
            return
        self._disposed = True
        # Clear our shadow list so disposal cascades cleanly. Composite
        # owns its inner children's lifecycle; the facades dispose
        # alongside.
        for item in self._items:
            item.dispose()
        self._items.clear()
        self._on_property_changed.on_completed()
        self._on_property_changed.dispose()
        self._inner.dispose()

    # ── Internal ────────────────────────────────────────────────────────────

    def _initial_children(self) -> tuple[ComponentVMOf[ApplicationSummary], ...]:
        # CompositeVM builder requires a children factory even when the
        # initial population is empty. We populate at construct() time
        # via _add_item; this seed runs once.
        return tuple(item.inner for item in self._items)

    def _notify(self, prop: str) -> None:
        """Emit a PropertyChanged event on BOTH the shared hub AND
        the per-VM-instance Observable (round-3 / PR #103 retirement
        path). Mirrors the helper on JobRunsVM / JobRunLogsVM."""
        self._hub.send(PropertyChangedMessage.create(self, "emr.applications", prop))
        self._on_property_changed.on_next(prop)

    def _set_state(self, state: PaneState) -> None:
        if self._state == state:
            return
        self._state = state
        self._notify("state")

    def _items_equal(self, new_apps: tuple[ApplicationSummary, ...]) -> bool:
        """Identity check for the dedup-on-set guard."""
        if len(self._items) != len(new_apps):
            return False
        for item, summary in zip(self._items, new_apps, strict=True):
            if item.summary != summary:
                return False
        return True

    def _clear_items(self) -> None:
        """Detach + dispose every current ``ApplicationItemVM``."""
        for item in list(self._items):
            if item.inner in self._inner:
                self._inner.remove(item.inner)
            item.dispose()
        self._items.clear()

    def _add_item(self, summary: ApplicationSummary) -> None:
        item = ApplicationItemVM(
            summary=summary,
            hub=self._hub,
            dispatcher=self._dispatcher,
        )
        self._items.append(item)
        if self._inner.is_constructed:
            item.construct()
        self._inner.append(item.inner)


__all__ = ["ApplicationItemVM", "ApplicationsVM"]
