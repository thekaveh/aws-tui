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

# Always-visible chips appended to the end of the legend; the spec calls them
# "App-level fallbacks" — shown in the hint legend when no widget has
# announced focus. These cover the keyboard bindings actually wired in
# `AwsTuiApp.BINDINGS` so the bottom row tells the user something useful
# even before focus tracking is wired (the full input router is deferred
# per memory `deferred-from-m6`). Keep ordering consistent with the
# spec §4.1 chip sequence.
_FALLBACK_ACTIONS: tuple[str, ...] = (
    "pane.switch_focus",
    "pane.descend",
    "pane.ascend",
    "pane.refresh",
    "app.command_palette",
    "app.help",
    "app.quit",
)

# Human-readable labels per action id. Anything not listed falls back to the
# tail-segment of the action id (e.g. "pane.copy" -> "copy"). Keeping this
# inline avoids a separate config file and lines up with the spec §4.1 chips.
_ACTION_LABELS: dict[str, str] = {
    "pane.descend": "open",
    "pane.ascend": "up",
    "pane.quick_look": "peek",
    "pane.copy": "copy",
    "pane.move": "move",
    "pane.delete": "del",
    "pane.new": "new",
    "pane.refresh": "refresh",
    "pane.filter": "filter",
    "pane.switch_focus": "switch",
    "pane.select_all": "all",
    "pane.toggle_select": "select",
    "pane.enter_multiselect": "multi",
    "app.command_palette": "cmd",
    "app.help": "help",
    "app.quit": "quit",
    "app.transfers_tray": "transfers",
    "auth.authenticate": "sign in",
    "modal.cancel": "cancel",
}


@dataclass(frozen=True, slots=True)
class HintAction:
    """One chip in the hint legend."""

    action_id: str
    key_label: str
    action_label: str


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
        self._actions: tuple[HintAction, ...] = ()

        self._inner: ComponentVM = (
            ComponentVM.builder().name("hint_legend").services(hub, dispatcher).build()
        )
        self._sub: DisposableBase | None = None

    # ── Properties ──────────────────────────────────────────────────────────

    @property
    def actions(self) -> tuple[HintAction, ...]:
        return self._actions

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
        chips: list[HintAction] = []
        focused = self._focused_vm_id
        if focused is not None:
            for action_id in self._registry.get(focused, ()):
                chip = self._resolve(action_id)
                if chip is not None:
                    chips.append(chip)
        for action_id in _FALLBACK_ACTIONS:
            chip = self._resolve(action_id)
            if chip is not None:
                chips.append(chip)
        new_actions = tuple(chips)
        if new_actions == self._actions:
            return
        self._actions = new_actions
        self._hub.send(PropertyChangedMessage.create(self, self.name, "actions"))

    def _resolve(self, action_id: str) -> HintAction | None:
        try:
            keys = self._keymap.resolve(action_id)
        except UnknownAction:
            return None
        if not keys:
            return None
        label = _ACTION_LABELS.get(action_id, action_id.rsplit(".", 1)[-1])
        return HintAction(action_id=action_id, key_label=keys[0], action_label=label)


__all__ = ["HintAction", "HintLegendVM"]
