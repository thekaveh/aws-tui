"""HintLegendVM — derives the bottom contextual command sequence.

The legend lists action chips (``<key> <label>``) for the active service,
followed by always-visible app-level fallbacks (theme, help, quit). Key labels
flow through :class:`KeymapStore` and are re-resolved on every rebuild.
"""

from __future__ import annotations

from dataclasses import dataclass

from vmx import ComponentVM, Message, MessageHub, PropertyChangedMessage
from vmx.lifecycle.status import ConstructionStatus
from vmx.services.dispatcher import Dispatcher

from aws_tui.infra.keymap_store import KeymapStore, UnknownAction

# Always-visible global chips follow the service-specific commands.
_GLOBAL_ACTIONS: tuple[str, ...] = (
    "app.command_palette",
    "app.themes",
    "app.cycle_theme",
    "app.help",
    "app.quit",
)

# Per-service chip sets lead the Commands pane and include the actions
# available for the active service.
_SERVICE_ACTIONS: dict[str, tuple[str, ...]] = {
    "s3": (
        "pane.switch_focus",
        "pane.descend",
        "pane.copy",
        "pane.delete",
        "pane.refresh",
        "app.swap_source",
    ),
    "emr-serverless": (
        "pane.switch_focus",
        "pane.descend",
        "pane.refresh",
        "app.swap_source",
        "emr.next_application",
        "emr.clone",
    ),
    "glue": (
        "glue.catalog",
        "glue.jobs",
        "glue.crawlers",
        "glue.choose_run_state",
        "glue.choose_crawler_state",
        "glue.copy_table_ref",
        "glue.query_in_athena",
        "glue.time_travel_in_athena",
        "pane.refresh",
        "app.swap_source",
    ),
    "athena": (
        "athena.query",
        "athena.history",
        "athena.results",
        "athena.saved",
        "athena.choose_workgroup",
        "athena.choose_catalog",
        "athena.choose_database",
        "athena.insert_table_ref",
        "athena.execute",
        "athena.cancel",
        "athena.load_more",
        "pane.refresh",
        "app.swap_source",
    ),
    # Settings is a static configuration page — no per-item
    # affordances apply. Pre-PR-81 it showed ``pane.refresh`` but
    # there's no handler for it on Settings (no DualPaneVM, no EMR
    # page), so pressing ``r`` did nothing. Better to advertise no
    # service-specific chips than to advertise a chip that no-ops.
    "settings": (),
}

# Fallback for callers that never set ``current_service_id`` (most
# tests, and the early boot window before the first nav selection
# fires). Keeps the existing S3-shaped chip row visible so the
# bottom legend isn't blank — same set the pre-PR-81 hardcoded
# ``_FALLBACK_ACTIONS`` exposed minus the globals.
_FALLBACK_SERVICE_ACTIONS: tuple[str, ...] = _SERVICE_ACTIONS["s3"]

# Human-readable labels per action id. Anything not listed falls back to the
# tail-segment of the action id (e.g. "pane.copy" -> "copy"). Keeping this
# inline avoids a separate config file and lines up with the spec §4.1 chips.
_ACTION_LABELS: dict[str, str] = {
    "app.command_palette": "more",
    "pane.descend": "open",
    "pane.ascend": "up",
    "pane.quick_look": "peek",
    "pane.copy": "copy",
    "pane.move": "move",
    "pane.delete": "delete",
    "pane.new": "new",
    "pane.refresh": "refresh",
    "pane.filter": "filter",
    "pane.switch_focus": "switch pane",
    "pane.select_all": "all",
    "pane.toggle_select": "select",
    "pane.enter_multiselect": "multi",
    "app.help": "help",
    "app.themes": "themes",
    # Compact chrome labels leave room for the renderer's later
    # width-aware selection.
    "app.cycle_theme": "next theme",
    "app.swap_source": "source",
    "emr.next_application": "switch app",
    "app.quit": "quit",
    "auth.authenticate": "sign in",
    "emr.clone": "clone",
    "emr.logs.filter": "filter logs",
    "glue.catalog": "catalog",
    "glue.jobs": "jobs",
    "glue.crawlers": "crawlers",
    "glue.choose_run_state": "run state",
    "glue.choose_crawler_state": "crawler state",
    "glue.copy_table_ref": "copy",
    "glue.query_in_athena": "Athena",
    "glue.time_travel_in_athena": "snapshot",
    "athena.query": "query",
    "athena.history": "history",
    "athena.results": "results",
    "athena.saved": "saved",
    "athena.choose_workgroup": "group",
    "athena.choose_catalog": "catalog",
    "athena.choose_database": "database",
    "athena.insert_table_ref": "table",
    "athena.execute": "run",
    "athena.cancel": "stop",
    "athena.load_more": "more",
}

_ACTION_EFFECTS: dict[str, str] = {
    "app.command_palette": (
        "Open commands available for the active service. This does not perform an AWS operation."
    ),
    "app.themes": "Open the theme picker. This changes presentation only.",
    "app.cycle_theme": "Switch to the next theme. This changes presentation only.",
    "app.help": "Open keyboard and workflow help. This does not perform an AWS operation.",
    "app.quit": "Exit aws-tui after the application's normal shutdown sequence.",
    "app.swap_source": (
        "Switch to the next configured source and rebuild the active service context. "
        "This does not write AWS resources."
    ),
    "pane.switch_focus": "Move keyboard focus to the next operational pane.",
    "pane.descend": "Open the selected item or descend into the selected location.",
    "pane.copy": "Copy the selected item through the existing transfer workflow.",
    "pane.delete": "Delete the selected item through the existing confirmation workflow.",
    "pane.refresh": "Reload the active operational surface from its current source.",
    "emr.next_application": (
        "Select the next EMR Serverless application and load its runs. This does not start a job."
    ),
    "emr.clone": "Open the clone workflow for the selected EMR Serverless job run.",
    "glue.catalog": "Show the Glue Catalog view. This performs read-only discovery.",
    "glue.jobs": "Show Glue jobs and their read-only run history.",
    "glue.crawlers": "Show Glue crawlers and their read-only state.",
    "glue.choose_run_state": "Open the Glue job-run state filter.",
    "glue.choose_crawler_state": "Open the Glue crawler-state filter.",
    "glue.copy_table_ref": "Copy the selected Glue table's fully qualified SQL identifier.",
    "glue.query_in_athena": (
        "Open the selected Glue table in Athena and prefill a bounded read-only SELECT. "
        "This does not execute the query."
    ),
    "glue.time_travel_in_athena": (
        "Open the selected Iceberg snapshot in Athena and prefill FOR VERSION AS OF SQL. "
        "This does not execute the query."
    ),
    "athena.query": "Show the Athena query editor.",
    "athena.history": "Show read-only Athena query history.",
    "athena.results": "Show rows for the current Athena execution.",
    "athena.saved": "Show saved Athena queries.",
    "athena.choose_workgroup": "Open the Athena workgroup selector.",
    "athena.choose_catalog": "Open the Athena catalog selector.",
    "athena.choose_database": "Open the Athena database selector.",
    "athena.insert_table_ref": "Insert the same-source copied Glue table at the editor cursor.",
    "athena.execute": "Execute the validated read-only SQL in the active Athena context.",
    "athena.cancel": "Stop the active app-owned Athena query execution.",
    "athena.load_more": "Load the next available page for the active Athena view.",
}

_ACTION_REQUIREMENTS: dict[str, str] = {
    "pane.copy": "Requires a copyable selected item.",
    "pane.delete": "Requires a deletable selected item.",
    "emr.clone": "Requires a selected cloneable job run.",
    "glue.copy_table_ref": "Requires a visible selected Glue table.",
    "glue.query_in_athena": "Requires a visible selected Glue table.",
    "glue.time_travel_in_athena": "Requires a visible selected snapshot row.",
    "athena.insert_table_ref": "Requires a copied table from the active Athena source.",
    "athena.execute": "Requires valid non-empty read-only SQL and an idle query runner.",
    "athena.cancel": "Requires an active app-owned Athena query.",
    "athena.load_more": "Requires another result page in the active Athena view.",
}

_ACTION_PRIORITIES: dict[str, int] = {
    "app.command_palette": 0,
    "app.quit": 0,
    "athena.execute": 10,
    "athena.cancel": 10,
    "glue.query_in_athena": 10,
    "glue.time_travel_in_athena": 10,
    "app.swap_source": 20,
    "pane.refresh": 20,
    "glue.catalog": 80,
    "glue.jobs": 80,
    "glue.crawlers": 80,
    "athena.query": 80,
    "athena.history": 80,
    "athena.results": 80,
    "athena.saved": 80,
    "app.themes": 90,
    "app.cycle_theme": 90,
    "app.help": 90,
}


def _canonical_shortcut(key: str) -> str:
    """Return the user-facing form of a configured key sequence."""
    names = {
        "ctrl": "Control",
        "shift": "Shift",
        "enter": "Enter",
        "escape": "Escape",
    }
    parts = key.split("+")
    implicit_shift = any(len(part) == 1 and part.isupper() for part in parts)
    rendered = [names.get(part.lower(), part.upper() if len(part) == 1 else part) for part in parts]
    if implicit_shift and "Shift" not in rendered:
        rendered.insert(-1, "Shift")
    return " + ".join(rendered)


def _tooltip_for(action_id: str, key: str, *, enabled: bool) -> str:
    lines = [f"Shortcut: {_canonical_shortcut(key)}", "", _ACTION_EFFECTS[action_id]]
    if not enabled and action_id in _ACTION_REQUIREMENTS:
        lines.extend(("", _ACTION_REQUIREMENTS[action_id]))
    return "\n".join(lines)


@dataclass(frozen=True, slots=True)
class HintAction:
    """One chip in the hint legend.

    ``enabled`` controls whether the chip renders in the active
    style (``True``) or greyed-out (``False``). The widget reads
    this to apply the ``.-disabled`` CSS class. The actual key
    binding is NOT suppressed when ``enabled=False`` — the
    rendering hint and the binding live in different layers; the
    enabled-state contract is "this chip is currently a no-op given
    the selection state" rather than "this key is unbound right
    now". App-level handlers do the actual no-op check.
    """

    action_id: str
    key_label: str
    action_label: str
    tooltip: str
    priority: int = 50
    overflow_only: bool = False
    enabled: bool = True


class HintLegendVM:
    """Reactive service-level hint-legend viewmodel."""

    def __init__(
        self,
        *,
        hub: MessageHub[Message],
        dispatcher: Dispatcher,
        keymap: KeymapStore,
    ) -> None:
        self._hub: MessageHub[Message] = hub
        self._keymap: KeymapStore = keymap

        self._current_service_id: str | None = None
        self._actions: tuple[HintAction, ...] = ()
        self._global_actions: tuple[HintAction, ...] = ()
        # Action ids the app currently considers a no-op given the
        # selection state (e.g. ``pane.copy`` / ``pane.delete`` when
        # the cursor is on a ``..`` parent row). Chips for these ids
        # render greyed-out; the actual key binding is not
        # suppressed but the app-level handler short-circuits.
        self._disabled_actions: frozenset[str] = frozenset()

        self._inner: ComponentVM = (
            ComponentVM.builder().name("hint_legend").services(hub, dispatcher).build()
        )

    # ── Properties ──────────────────────────────────────────────────────────

    @property
    def actions(self) -> tuple[HintAction, ...]:
        """Service-specific chips shown before the global commands.

        Includes the active service's chip set (S3, EMR, Glue, Athena,
        Settings, or fallback)."""
        return self._actions

    @property
    def global_actions(self) -> tuple[HintAction, ...]:
        """Always-visible globals shown after the service commands.

        Themes / help / quit / etc. — the app-chrome controls that
        apply regardless of which service is active."""
        return self._global_actions

    def set_current_service(self, service_id: str | None) -> None:
        """Caller pushes the active service id whenever the nav rail
        selection changes. Triggers a chip rebuild."""
        if self._current_service_id == service_id:
            return
        self._current_service_id = service_id
        self._rebuild_actions()

    @property
    def disabled_actions(self) -> frozenset[str]:
        return self._disabled_actions

    def set_disabled_actions(self, action_ids: frozenset[str]) -> None:
        """Push the set of action ids that are no-ops given the
        current selection. Triggers a chip rebuild so the widget
        re-renders with the new disabled flags."""
        if action_ids == self._disabled_actions:
            return
        self._disabled_actions = action_ids
        self._rebuild_actions()

    @property
    def status(self) -> ConstructionStatus:
        return self._inner.status

    @property
    def name(self) -> str:
        return self._inner.name

    # ── Lifecycle ───────────────────────────────────────────────────────────

    def construct(self) -> None:
        self._inner.construct()
        self._rebuild_actions()

    def destruct(self) -> None:
        self._inner.destruct()

    def dispose(self) -> None:
        self._inner.dispose()

    # ── Internal ────────────────────────────────────────────────────────────

    def _rebuild_actions(self) -> None:
        # ── Service-specific (LEFT column) ──────────────────────────
        #
        # ``seen`` deduplicates service actions and globals.
        seen: set[str] = set()
        chips: list[HintAction] = []
        service_set = _SERVICE_ACTIONS.get(
            self._current_service_id or "", _FALLBACK_SERVICE_ACTIONS
        )
        for action_id in service_set:
            if action_id in seen:
                continue
            chip = self._resolve(action_id)
            if chip is not None:
                chips.append(chip)
                seen.add(action_id)
        new_actions = tuple(chips)
        # ── Globals (RIGHT column) ──────────────────────────────────
        global_chips: list[HintAction] = []
        for action_id in _GLOBAL_ACTIONS:
            if action_id in seen:
                continue
            chip = self._resolve(action_id)
            if chip is not None:
                global_chips.append(chip)
                seen.add(action_id)
        new_globals = tuple(global_chips)
        changed = False
        if new_actions != self._actions:
            self._actions = new_actions
            changed = True
        if new_globals != self._global_actions:
            self._global_actions = new_globals
            changed = True
        if changed:
            self._hub.send(PropertyChangedMessage.create(self, self.name, "actions"))

    def _resolve(self, action_id: str) -> HintAction | None:
        try:
            keys = self._keymap.resolve(action_id)
        except UnknownAction:
            return None
        if not keys:
            return None
        label = self._label_for(action_id)
        return HintAction(
            action_id=action_id,
            key_label=keys[0],
            action_label=label,
            tooltip=_tooltip_for(
                action_id,
                keys[0],
                enabled=action_id not in self._disabled_actions,
            ),
            priority=_ACTION_PRIORITIES.get(action_id, 50),
            overflow_only=action_id == "app.command_palette",
            enabled=action_id not in self._disabled_actions,
        )

    def _label_for(self, action_id: str) -> str:
        return _ACTION_LABELS.get(action_id, action_id.rsplit(".", 1)[-1])


__all__ = ["HintAction", "HintLegendVM"]
