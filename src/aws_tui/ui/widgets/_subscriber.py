"""Helpers for bridging VMx ``MessageHub`` events to Textual widgets.

Each widget that binds to a VM subscribes to its hub for
``PropertyChangedMessage`` events whose ``sender_object`` matches the VM.
This module factors that subscription into a tiny mixin so individual
widgets stay focused on rendering.

The mixin also handles ``unmount`` cleanup — we keep a strong reference
to the reactivex ``DisposableBase`` returned by ``hub.messages.subscribe``
and dispose it when the widget unmounts.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from reactivex.abc import DisposableBase
from vmx import Message, MessageHub, PropertyChangedMessage


class HubSubscriberMixin:
    """Mixin that subscribes to a VM's hub and dispatches property changes.

    Subclasses override :meth:`_on_property_changed` to react to specific
    property names; the mixin filters messages on ``sender_object``.
    """

    _hub_subscription: DisposableBase | None = None

    def subscribe_to_vm(
        self,
        *,
        hub: MessageHub[Message],
        vm: Any,
        on_property_changed: Callable[[str], None],
    ) -> None:
        """Subscribe to ``hub`` and invoke ``on_property_changed`` per match."""
        if self._hub_subscription is not None:
            self._hub_subscription.dispose()

        def _on_message(msg: object) -> None:
            if not isinstance(msg, PropertyChangedMessage):
                return
            if msg.sender_object is not vm:
                return
            on_property_changed(msg.property_name)

        self._hub_subscription = hub.messages.subscribe(on_next=_on_message)

    def unsubscribe_from_vm(self) -> None:
        if self._hub_subscription is not None:
            self._hub_subscription.dispose()
            self._hub_subscription = None


__all__ = ["HubSubscriberMixin"]
