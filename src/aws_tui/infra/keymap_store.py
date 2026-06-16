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
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar


class UnknownAction(Exception):
    """Raised when :meth:`KeymapStore.resolve` is asked for an action that
    has no default and no overlay binding."""


@dataclass(frozen=True, slots=True)
class KeyBinding:
    """A single resolved binding: an action and its keystroke(s)."""

    action: str
    keys: tuple[str, ...]


class KeymapStore:
    """Resolve action names to keystrokes, with optional overlay merging."""

    DEFAULT_BINDINGS: ClassVar[dict[str, tuple[str, ...]]] = {
        "app.quit": ("q", "ctrl+c"),
        "app.command_palette": (":", "ctrl+k"),
        "app.help": ("?",),
        "pane.move_up": ("up", "j"),
        "pane.move_down": ("down", "k"),
        "pane.descend": ("enter",),
        "pane.ascend": ("backspace", "left"),
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
        "auth.authenticate": ("a",),
        "modal.cancel": ("escape",),
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
                if isinstance(keys, str):
                    merged[action] = (keys,)
                else:
                    merged[action] = tuple(keys)
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


__all__ = ["KeyBinding", "KeymapStore", "UnknownAction"]
