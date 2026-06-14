"""Action registry — string id -> callable.

Every user-visible interaction in aws-tui is named by an action id
(e.g. ``pane.copy``, ``app.command_palette``). The :class:`ActionRegistry`
maps those ids to handler callables that perform the actual work, typically
by calling a VM command. The View layer never invokes a VM command directly
by attribute access; it always goes through the action registry so the
input router stays uniform and keymap customization (via
:class:`KeymapStore`) is free.

Handlers may be synchronous (returns ``None``) or asynchronous (returns
``Awaitable[None]``). The caller decides whether to ``await`` the result.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import TypeAlias

#: Handler signature for an action id. Sync returns ``None``; async
#: returns ``Awaitable[None]`` which the caller may schedule on the loop.
ActionHandler: TypeAlias = Callable[[], None | Awaitable[None]]


class UnknownAction(Exception):
    """Raised when :meth:`ActionRegistry.invoke` is asked for an unregistered id."""


class ActionRegistry:
    """In-memory mapping of action id -> handler callable.

    Registration replaces any previous handler for the same id (last-write
    wins). :meth:`invoke` raises :class:`UnknownAction` for unregistered ids
    so silent no-ops never accumulate.
    """

    def __init__(self) -> None:
        self._handlers: dict[str, ActionHandler] = {}

    def register(self, action_id: str, handler: ActionHandler) -> None:
        """Register or replace ``handler`` for ``action_id``."""
        self._handlers[action_id] = handler

    def unregister(self, action_id: str) -> None:
        """Forget the handler for ``action_id`` (no-op if unknown)."""
        self._handlers.pop(action_id, None)

    def has(self, action_id: str) -> bool:
        """Return True if a handler is registered for ``action_id``."""
        return action_id in self._handlers

    def invoke(self, action_id: str) -> None | Awaitable[None]:
        """Invoke the handler for ``action_id``.

        Returns whatever the handler returned (None or an awaitable).
        Raises :class:`UnknownAction` for unregistered ids.
        """
        try:
            handler = self._handlers[action_id]
        except KeyError as exc:
            raise UnknownAction(action_id) from exc
        return handler()

    def known_actions(self) -> tuple[str, ...]:
        """Return all registered action ids in registration order."""
        return tuple(self._handlers)


__all__ = ["ActionHandler", "ActionRegistry", "UnknownAction"]
