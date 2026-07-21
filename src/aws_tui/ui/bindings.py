"""BindingResolver — deferred bridge from :class:`KeymapStore` to Textual bindings.

Textual widgets get their key bindings via a ``BINDINGS`` class attribute
that lists :class:`textual.binding.Binding` objects. The app still ships
hard-coded ``AwsTuiApp.BINDINGS`` in v0.8.x; ``config.toml`` keybinding
overlays are parsed and validated but not yet routed through this resolver.

The action description shown by Textual's help footer is taken from a
small label map kept in this module; missing entries fall back to the
tail-segment of the action id (e.g. ``pane.copy`` -> ``copy``).
"""

from __future__ import annotations

from textual.binding import Binding

from aws_tui.infra.keymap_store import KeymapStore, UnknownAction
from aws_tui.ui.actions import ActionRegistry

#: Human-readable label per action id used in Textual binding descriptions.
#: Mirrors (and is intentionally separate from) the chip-label dict in
#: ``vm/chrome/hint_legend_vm.py`` so the chrome and Textual's own help
#: footer can render different copy if needed.
_ACTION_DESCRIPTIONS: dict[str, str] = {
    "app.quit": "Quit",
    "app.command_palette": "Command palette",
    "app.help": "Help",
    "app.themes": "Theme picker",
    "app.cycle_theme": "Cycle theme",
    "app.swap_source": "Swap pane source",
    "pane.move_up": "Up",
    "pane.move_down": "Down",
    "pane.descend": "Open",
    "pane.ascend": "Up",
    "pane.switch_focus": "Switch pane",
    "pane.switch_focus_back": "Switch pane back",
    "pane.quick_look": "Quick look",
    "pane.filter": "Filter",
    "pane.fuzzy_find": "Find",
    "pane.enter_multiselect": "Multi-select",
    "pane.toggle_select": "Select",
    "pane.select_all": "Select all",
    "pane.copy": "Copy",
    "pane.move": "Move",
    "pane.delete": "Delete",
    "pane.new": "New",
    "pane.refresh": "Refresh",
    "auth.authenticate": "Sign in",
    "modal.cancel": "Cancel",
}


def _describe(action_id: str) -> str:
    if action_id in _ACTION_DESCRIPTIONS:
        return _ACTION_DESCRIPTIONS[action_id]
    return action_id.rsplit(".", 1)[-1].replace("_", " ").title()


#: Actions whose first keystroke is shown in Textual's help footer. Cursor
#: moves, back-focus, modal nav, and marks stay hidden — the bottom hint
#: legend renders those context-sensitive chips itself.
_VISIBLE_ACTIONS: frozenset[str] = frozenset(
    {
        "app.quit",
        "pane.switch_focus",
        "pane.move_up",
        "pane.move_down",
        "pane.descend",
        "pane.ascend",
        "pane.refresh",
        "app.help",
        "app.themes",
        "app.cycle_theme",
        "app.open_settings",
        "pane.copy",
        "pane.delete",
        "app.swap_source",
    }
)

#: Every handled action binds with ``priority=True`` (so the App handler wins
#: over Textual's Screen-level focus traversal) EXCEPT quit. Listing only the
#: exception keeps this in step with Textual's ``priority=False`` default.
_NON_PRIORITY_ACTIONS: frozenset[str] = frozenset({"app.quit"})


class BindingResolver:
    """Bridge between Textual's BINDINGS list and our ``KeymapStore``.

    The resolver is constructed once at app composition time with a populated
    :class:`ActionRegistry` and the active :class:`KeymapStore`. It can be
    asked for the Textual binding list or for the action id behind a raw
    keystroke (used by the input router when Textual delivers an unmatched
    key event).
    """

    def __init__(
        self,
        *,
        keymap: KeymapStore,
        actions: ActionRegistry,
    ) -> None:
        self._keymap: KeymapStore = keymap
        self._actions: ActionRegistry = actions

    @property
    def keymap(self) -> KeymapStore:
        return self._keymap

    @property
    def actions(self) -> ActionRegistry:
        return self._actions

    def to_textual_bindings(self) -> list[Binding]:
        """Materialize the Textual ``Binding`` list for the active keymap.

        For each ``(action_id, keys)`` in the keymap we emit one Binding
        per keystroke — but **only when the action has a registered handler**
        (``ActionRegistry.has``). Deferred/unwired actions (e.g.
        ``pane.quick_look``, ``app.command_palette``) stay in the keymap for
        documentation but produce no runtime binding, so no keystroke maps to
        a handler that does not exist.

        ``action`` is the parameterized ``dispatch('<action_id>')`` form;
        ``AwsTuiApp.action_dispatch`` forwards it to the
        :class:`ActionRegistry`, which holds the real handler. Only the first
        keystroke of a chip-worthy action is shown in Textual's footer;
        every handled action binds ``priority=True`` except quit (see
        :data:`_VISIBLE_ACTIONS` / :data:`_NON_PRIORITY_ACTIONS`).
        """
        bindings: list[Binding] = []
        for action_id, keys in self._keymap.all().items():
            if not self._actions.has(action_id):
                continue  # handlerless (deferred) action stays unbound
            description = _describe(action_id)
            priority = action_id not in _NON_PRIORITY_ACTIONS
            visible = action_id in _VISIBLE_ACTIONS
            for index, key in enumerate(keys):
                bindings.append(
                    Binding(
                        key=key,
                        action=f"dispatch({action_id!r})",
                        description=description,
                        show=index == 0 and visible,
                        priority=priority,
                    )
                )
        return bindings

    def resolve_action_id(self, key: str) -> str | None:
        """Return the action id bound to ``key``, or None when unbound."""
        for action_id, keys in self._keymap.all().items():
            if key in keys:
                return action_id
        return None

    def keys_for(self, action_id: str) -> tuple[str, ...]:
        """Return the keys bound to ``action_id`` (empty tuple if unknown)."""
        try:
            return self._keymap.resolve(action_id)
        except UnknownAction:
            return ()


__all__ = ["BindingResolver"]
