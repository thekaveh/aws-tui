"""Action - keystroke indirection layer.

Every user-visible interaction in aws-tui is named by an action string
(e.g. ``pane.copy``, ``app.command_palette``). The KeymapStore maps an
action to one or more keystrokes; the defaults match the canonical spec
§4.2 keymap. An overlay (typically ``[keybindings]`` from
``config.toml``) replaces the default keys for an action wholesale —
overlay never unions with defaults; the user is in charge.

Adding a wholly new action through the overlay is rejected with
:class:`UnknownAction`: there would be no command anywhere in the app to
bind to, so it would silently do nothing.

An overlay also may not introduce a new same-key action pair. Deliberate
built-in aliases remain valid, but new collisions are rejected with
:class:`KeybindingCollision` instead of relying on binding declaration order.
"""

from __future__ import annotations

import unicodedata
from itertools import combinations
from typing import ClassVar


class UnknownAction(Exception):
    """Raised when :meth:`KeymapStore.resolve` is asked for an action that
    has no default and no overlay binding."""


class KeybindingCollision(ValueError):
    """Raised when an overlay introduces a same-key action collision."""


class InvalidKeybinding(ValueError):
    """Raised when an overlay contains a malformed key token."""


_TEXTUAL_FRIENDLY_KEY_NAMES: dict[str, str] = {
    "solidus": "slash",
    "reverse_solidus": "backslash",
    "commercial_at": "at",
    "hyphen_minus": "minus",
    "plus_sign": "plus",
    "low_line": "underscore",
}


def textual_key_name(key: str) -> str:
    """Return the runtime Textual name for a configured key literal."""
    if len(key) != 1:
        # Every Textual key name and modifier is lowercase, and Textual matches
        # them verbatim, so a configured ``Ctrl+K`` would bind a key no event
        # ever produces — silently unbinding the action, because an overlay
        # replaces the defaults wholesale. ``docs/cookbook.md`` documents
        # exactly that casing, so fold it rather than reject it. Single
        # characters are left alone: ``K`` and ``k`` are genuinely different
        # keys.
        return key.casefold()
    if key.isalnum():
        return key
    try:
        normalized = unicodedata.name(key).lower().replace("-", "_").replace(" ", "_")
    except ValueError:
        normalized = "tab" if key == "\t" else key
    return _TEXTUAL_FRIENDLY_KEY_NAMES.get(normalized, normalized)


def _collision_pairs(
    bindings: dict[str, tuple[str, ...]],
) -> set[tuple[str, str, str]]:
    actions_by_key: dict[str, set[str]] = {}
    for action, keys in bindings.items():
        for key in keys:
            actions_by_key.setdefault(textual_key_name(key), set()).add(action)
    return {
        (key, left, right)
        for key, actions in actions_by_key.items()
        for left, right in combinations(sorted(actions), 2)
    }


def _validate_overlay_keys(action: str, keys: tuple[str, ...]) -> None:
    for key in keys:
        if not key or any(character.isspace() for character in key) or "," in key:
            raise InvalidKeybinding(
                f"invalid keybinding {key!r} for {action!r}; "
                "use one non-empty Textual key token per list item"
            )


class KeymapStore:
    """Resolve action names to keystrokes, with optional overlay merging."""

    APPROVED_ALIAS_PAIRS: ClassVar[frozenset[tuple[str, str]]] = frozenset(
        {
            ("pane.quick_look", "pane.toggle_select"),
            ("auth.authenticate", "pane.select_all"),
            ("emr.clone", "pane.copy"),
            ("athena.query", "glue.catalog"),
            ("athena.history", "glue.jobs"),
            ("athena.results", "glue.crawlers"),
        }
    )

    DEFAULT_BINDINGS: ClassVar[dict[str, tuple[str, ...]]] = {
        "app.quit": ("q", "ctrl+c"),
        "app.command_palette": (":", "ctrl+k"),
        "app.help": ("?",),
        "app.open_settings": (",",),
        "pane.move_up": ("up", "k"),
        "pane.move_down": ("down", "j"),
        "pane.descend": ("enter",),
        "pane.ascend": ("backspace",),
        "pane.modal_left": ("left",),
        "pane.modal_right": ("right",),
        "pane.mark_up": ("shift+up",),
        "pane.mark_down": ("shift+down",),
        "pane.switch_focus": ("tab",),
        "pane.switch_focus_back": ("shift+tab",),
        "pane.quick_look": ("space",),
        "pane.filter": ("/",),
        "pane.fuzzy_find": ("ctrl+p",),
        "pane.enter_multiselect": ("v",),
        "pane.toggle_select": ("space",),
        "pane.select_all": ("a",),
        "pane.copy": ("c",),
        "pane.move": ("m",),
        "pane.delete": ("d",),
        "pane.new": ("n",),
        "pane.refresh": ("r",),
        "app.themes": ("t",),
        "app.cycle_theme": ("T",),
        "app.swap_source": ("S",),
        "emr.next_application": ("A",),
        "auth.authenticate": ("a",),
        # EMR-page-only. The App routes this deliberate alias to clone on
        # EMR and to ``pane.copy`` on the file manager.
        "emr.clone": ("c",),
        "emr.logs.filter": ("f",),
        "glue.catalog": ("1",),
        "glue.jobs": ("2",),
        "glue.crawlers": ("3",),
        "glue.choose_run_state": ("F",),
        "glue.choose_crawler_state": ("G",),
        "glue.copy_table_ref": ("y",),
        "glue.query_in_athena": ("Q",),
        "glue.time_travel_in_athena": ("V",),
        "athena.query": ("1",),
        "athena.history": ("2",),
        "athena.results": ("3",),
        "athena.saved": ("4",),
        "athena.choose_workgroup": ("W",),
        "athena.choose_catalog": ("C",),
        "athena.choose_database": ("D",),
        "athena.insert_table_ref": ("i",),
        "athena.execute": ("ctrl+enter",),
        "athena.cancel": ("escape",),
        "athena.load_more": ("l",),
    }

    def __init__(self, *, overlay: dict[str, str | list[str]] | None = None) -> None:
        merged: dict[str, tuple[str, ...]] = dict(self.DEFAULT_BINDINGS)
        if overlay:
            for action, keys in overlay.items():
                if action not in self.DEFAULT_BINDINGS:
                    raise UnknownAction(
                        f"overlay refers to unknown action {action!r}; "
                        f"valid actions are {sorted(self.DEFAULT_BINDINGS)}"
                    )
                normalized: tuple[str, ...] = (keys,) if isinstance(keys, str) else tuple(keys)
                _validate_overlay_keys(action, normalized)
                merged[action] = normalized
            unapproved = {
                (key, left, right)
                for key, left, right in _collision_pairs(merged)
                if (left, right) not in self.APPROVED_ALIAS_PAIRS
            }
            if unapproved:
                key, left, right = min(unapproved)
                raise KeybindingCollision(
                    f"keybinding overlay assigns {key!r} to both "
                    f"{left!r} and {right!r}; choose distinct keys"
                )
        self._bindings: dict[str, tuple[str, ...]] = merged

    def resolve(self, action: str) -> tuple[str, ...]:
        """Return the keystroke tuple bound to ``action``.

        Raises :class:`UnknownAction` if no default exists for the name.
        """
        try:
            return self._bindings[action]
        except KeyError as exc:
            raise UnknownAction(action) from exc

    def all(self) -> dict[str, tuple[str, ...]]:
        """Return a copy of the full action - keys mapping."""
        return dict(self._bindings)


__all__ = [
    "InvalidKeybinding",
    "KeybindingCollision",
    "KeymapStore",
    "UnknownAction",
    "textual_key_name",
]
