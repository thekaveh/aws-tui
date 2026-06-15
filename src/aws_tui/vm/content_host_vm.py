"""ContentHostVM — holds the active service's VM tree.

``set_content(new_vm, service_id=...)`` disposes the previous content tree
(synchronously, depth-first via VMx) and constructs the new one. Re-setting
with the same ``service_id`` is a no-op so the menu can publish "selected
service" updates without rebuilding the world.
"""

from __future__ import annotations

import inspect
from typing import Any

from vmx import ComponentVM, Message, MessageHub, PropertyChangedMessage
from vmx.lifecycle.status import ConstructionStatus
from vmx.services.dispatcher import Dispatcher


class ContentHostVM:
    """Owns the currently active service VM and orchestrates the swap."""

    def __init__(
        self,
        *,
        hub: MessageHub[Message],
        dispatcher: Dispatcher,
    ) -> None:
        self._hub: MessageHub[Message] = hub
        self._dispatcher: Dispatcher = dispatcher

        self._current: Any | None = None
        self._current_id: str | None = None

        self._inner: ComponentVM = (
            ComponentVM.builder().name("content_host").services(hub, dispatcher).build()
        )

    # ── Properties ──────────────────────────────────────────────────────────

    @property
    def current(self) -> Any | None:
        return self._current

    @property
    def current_id(self) -> str | None:
        return self._current_id

    @property
    def status(self) -> ConstructionStatus:
        return self._inner.status

    @property
    def is_constructed(self) -> bool:
        return self._inner.is_constructed

    @property
    def name(self) -> str:
        return self._inner.name

    # ── Lifecycle ───────────────────────────────────────────────────────────

    def construct(self) -> None:
        self._inner.construct()

    def destruct(self) -> None:
        if self._current is not None:
            self._current.destruct()
        self._inner.destruct()

    def dispose(self) -> None:
        if self._current is not None:
            self._current.dispose()
            self._current = None
            self._current_id = None
        self._inner.dispose()

    # ── Public API ─────────────────────────────────────────────────────────

    async def set_content(self, vm: Any | None, *, service_id: str | None) -> None:
        """Swap the hosted VM. Idempotent on equal ``service_id``."""
        if self._current_id == service_id and service_id is not None:
            # Same active service — no-op per spec §5.4.
            return
        # Dispose the previous content first so its subscriptions / tasks
        # tear down before the new one wires up.
        if self._current is not None:
            self._current.dispose()
            self._current = None
            self._current_id = None
            self._hub.send(PropertyChangedMessage.create(self, self.name, "current"))

        if vm is None:
            return

        # Construct the new one and announce the swap.
        vm.construct()
        # If the service VM exposes an async ``setup()`` (e.g. ``DualPaneVM``
        # which calls ``provider.list()`` on each pane), drive it now so the
        # listings populate before the View layer renders. Skipping this is
        # how every pane ends up empty after a launch / service switch.
        setup = getattr(vm, "setup", None)
        if callable(setup):
            result = setup()
            if inspect.isawaitable(result):
                await result
        self._current = vm
        self._current_id = service_id
        self._hub.send(PropertyChangedMessage.create(self, self.name, "current"))


__all__ = ["ContentHostVM"]
