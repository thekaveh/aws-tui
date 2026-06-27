"""HintLegendVM — derives the bottom contextual hint row.

The legend lists action chips (``<key> <label>``) appropriate to the focused
VM, followed by always-visible app-level fallbacks (``: cmd``, ``? help``).
The focused VM identifier flows through :class:`FocusChangedMessage`;
key labels flow through :class:`KeymapStore` (re-resolved on every rebuild).

The legend is purely a denormalized projection — when no focus message has
arrived, only the fallback chips are shown.
"""

from __future__ import annotations

from dataclasses import dataclass

from reactivex.abc import DisposableBase
from vmx import ComponentVM, Message, MessageHub, PropertyChangedMessage
from vmx.lifecycle.status import ConstructionStatus
from vmx.services.dispatcher import Dispatcher

from aws_tui.infra.keymap_store import KeymapStore, UnknownAction
from aws_tui.vm.messages import FocusChangedMessage, KeymapChangedMessage

# Always-visible global chips — shown regardless of which service is
# active and what is selected. Themes / help / quit / etc. — the "app
# chrome" controls. User feedback after PR #80 asked for these on the
# RIGHT side of the Commands pane while the service-specific chips
# (S3 / EMR / etc.) sit on the LEFT.
_GLOBAL_ACTIONS: tuple[str, ...] = (
    "app.themes",
    "app.cycle_theme",
    "app.command_palette",
    "app.help",
    "app.quit",
)

# Per-service chip sets — what the user sees on the LEFT side of the
# Commands pane depends on which service is active. Refresh stays on
# every service. PR-B/C will extend the EMR set with the cancel / clone
# / submit / lifecycle action ids once those handlers ship.
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
        "emr.clone",
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
# ``_FALLBACK_ACTIONS`` exposed minus the globals (which now own
# their own right-hand column).
_FALLBACK_SERVICE_ACTIONS: tuple[str, ...] = _SERVICE_ACTIONS["s3"]

# Human-readable labels per action id. Anything not listed falls back to the
# tail-segment of the action id (e.g. "pane.copy" -> "copy"). Keeping this
# inline avoids a separate config file and lines up with the spec §4.1 chips.
_ACTION_LABELS: dict[str, str] = {
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
    "app.command_palette": "cmd",
    "app.help": "help",
    "app.themes": "themes",
    # Both ``app.cycle_theme`` and ``app.swap_source`` semantically
    # "switch X". User feedback: don't compress one to "cycle" and
    # leave the other as "swap src" — make both read the same
    # ("switch") with the noun differing. They sit far apart in the
    # row so the parallel reads naturally; we don't shorten "switch"
    # to "cycle" for either to avoid the "cycle theme is confusing"
    # complaint (cycle theme reads as "cycle a theme attribute" not
    # "rotate to the next theme").
    "app.cycle_theme": "switch theme",
    "app.swap_source": "switch source",
    # Service-specific label overrides handled by ``_label_for`` (the
    # user asked for "switch source" → "switch application" when EMR
    # is active). The generic fallback is now "switch source" for
    # S3 (was "swap src" pre-this-batch).
    "app.quit": "quit",
    "auth.authenticate": "sign in",
    "modal.cancel": "cancel",
    "emr.clone": "clone",
}


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
    enabled: bool = True


class HintLegendVM:
    """Reactive hint-legend viewmodel.

    Callers register focusable VMs and their action-id sequences via
    :meth:`register_focusable`; subsequent :class:`FocusChangedMessage` events
    drive the visible chips. :class:`KeymapChangedMessage` triggers a rebuild
    so re-bindings show up immediately.
    """

    def __init__(
        self,
        *,
        hub: MessageHub[Message],
        dispatcher: Dispatcher,
        keymap: KeymapStore,
    ) -> None:
        self._hub: MessageHub[Message] = hub
        self._dispatcher: Dispatcher = dispatcher
        self._keymap: KeymapStore = keymap

        self._registry: dict[str, tuple[str, ...]] = {}
        self._focused_vm_id: str | None = None
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
        self._sub: DisposableBase | None = None

    # ── Properties ──────────────────────────────────────────────────────────

    @property
    def actions(self) -> tuple[HintAction, ...]:
        """Service-specific chips — LEFT side of the Commands pane.

        Includes any focused-VM-registered ids and the active
        service's chip set (S3 / EMR / Settings / fallback)."""
        return self._actions

    @property
    def global_actions(self) -> tuple[HintAction, ...]:
        """Always-visible globals — RIGHT side of the Commands pane.

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
    def focused_vm_id(self) -> str | None:
        return self._focused_vm_id

    @property
    def status(self) -> ConstructionStatus:
        return self._inner.status

    @property
    def name(self) -> str:
        return self._inner.name

    # ── Lifecycle ───────────────────────────────────────────────────────────

    def construct(self) -> None:
        self._inner.construct()
        if self._sub is None:
            self._sub = self._hub.messages.subscribe(on_next=self._on_message)
        self._rebuild_actions()

    def destruct(self) -> None:
        if self._sub is not None:
            self._sub.dispose()
            self._sub = None
        self._inner.destruct()

    def dispose(self) -> None:
        if self._sub is not None:
            self._sub.dispose()
            self._sub = None
        self._inner.dispose()

    # ── Registration API ───────────────────────────────────────────────────

    def register_focusable(self, vm_id: str, action_ids: tuple[str, ...]) -> None:
        """Associate a focusable VM with an ordered tuple of action ids.

        Re-registering replaces the prior tuple. Action ids the keymap doesn't
        know about are silently dropped at render time.
        """
        self._registry[vm_id] = action_ids
        if self._focused_vm_id == vm_id:
            self._rebuild_actions()

    def unregister_focusable(self, vm_id: str) -> None:
        self._registry.pop(vm_id, None)
        if self._focused_vm_id == vm_id:
            self._focused_vm_id = None
            self._rebuild_actions()

    # ── Internal ────────────────────────────────────────────────────────────

    def _on_message(self, msg: object) -> None:
        if isinstance(msg, FocusChangedMessage):
            if self._focused_vm_id == msg.focused_vm_id:
                return
            self._focused_vm_id = msg.focused_vm_id
            self._rebuild_actions()
        elif isinstance(msg, KeymapChangedMessage):
            self._rebuild_actions()

    def _rebuild_actions(self) -> None:
        # ── Service-specific (LEFT column) ──────────────────────────
        #
        # ``seen`` dedups across the focused-pane block, the service
        # block, and (downstream) the globals — without it a chip
        # registered both as focused-pane and as a service action
        # would render twice. The focused-pane registration is
        # exercised only by tests today (BindingResolver wiring is
        # deferred per the [[deferred-from-m6]] memo) but kept so
        # the contract is honest.
        seen: set[str] = set()
        chips: list[HintAction] = []
        focused = self._focused_vm_id
        if focused is not None:
            for action_id in self._registry.get(focused, ()):
                if action_id in seen:
                    continue
                chip = self._resolve(action_id)
                if chip is not None:
                    chips.append(chip)
                    seen.add(action_id)
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
            enabled=action_id not in self._disabled_actions,
        )

    def _label_for(self, action_id: str) -> str:
        """Service-aware label lookup.

        User feedback: "I also expect the switch source command to
        become switch application command [when EMR is active]". The
        action_id stays ``app.swap_source`` (binding routing is by
        id), but the chip label flips to ``switch app`` when EMR is
        the active service. Generic fallback is the ``_ACTION_LABELS``
        table.
        """
        if action_id == "app.swap_source" and self._current_service_id == "emr-serverless":
            return "switch app"
        return _ACTION_LABELS.get(action_id, action_id.rsplit(".", 1)[-1])


__all__ = ["HintAction", "HintLegendVM"]
