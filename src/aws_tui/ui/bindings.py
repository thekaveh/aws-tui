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
        per keystroke. ``action`` on the emitted ``Binding`` is the
        Textual-style name produced by :meth:`_textual_action_name`
        (dots → underscores, e.g. ``"app.quit"`` → ``"app_quit"``);
        Textual then invokes the matching ``action_<name>`` method on
        the ``App``. The :class:`ActionRegistry` is the eventual
        indirection point for that dispatch — it is constructed by
        ``AwsTuiApp`` today but the BINDINGS field is still a
        hard-coded ``ClassVar``, so ``ActionRegistry.invoke`` does not
        yet sit on the runtime path (tracked in
        ``CHANGELOG.md`` ▸ ``[0.8.0] Deferred / v0.9 roadmap``).
        Visible only when the action id is among the chip-worthy
        app/pane actions; auxiliary aliases (``ctrl+c`` for quit,
        ``shift+tab`` for back-focus) are emitted with ``show=False``
        so the help footer stays clean.
        """
        bindings: list[Binding] = []
        for action_id, keys in self._keymap.all().items():
            description = _describe(action_id)
            for index, key in enumerate(keys):
                bindings.append(
                    Binding(
                        key=key,
                        action=self._textual_action_name(action_id),
                        description=description,
                        show=index == 0 and self._is_visible_action(action_id),
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

    # ── Internal ────────────────────────────────────────────────────────────

    @staticmethod
    def _textual_action_name(action_id: str) -> str:
        """Convert an aws-tui action id into a Textual action method name.

        Textual treats the binding action as a method name (``action_<name>``
        on the App / Screen). We replace dots with underscores so
        ``pane.copy`` becomes ``pane_copy`` — the App must expose
        ``action_pane_copy``, which simply forwards to the registry.
        """
        return action_id.replace(".", "_")

    @staticmethod
    def _is_visible_action(action_id: str) -> bool:
        # Pane-cursor + modal-cancel bindings are not show-worthy in Textual's
        # built-in footer because our own hint-legend renders the
        # context-sensitive chips already. We surface only the always-visible
        # app-level actions.
        return action_id in {
            "app.quit",
            "app.command_palette",
            "app.help",
            "app.themes",
            "app.cycle_theme",
            "app.swap_source",
            "pane.copy",
            "pane.move",
            "pane.delete",
            "pane.refresh",
        }


__all__ = ["BindingResolver"]
