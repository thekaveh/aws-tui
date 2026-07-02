"""Helpers for bridging VMx ``MessageHub`` events to Textual widgets.

Each widget that binds to a VM subscribes to its hub for
``PropertyChangedMessage`` events whose ``sender_object`` matches the VM.
This module factors that subscription into a tiny mixin so individual
widgets stay focused on rendering.

The mixin exposes :meth:`unsubscribe_from_vm` so the consuming widget
can release the subscription from its own ``on_unmount`` hook — the
mixin does NOT install an ``on_unmount`` itself (overriding Textual's
unmount path from a mixin would force every consumer into a
``super().on_unmount()`` discipline, which is a more error-prone
contract than the explicit call). A widget that forgets the call
leaks one subscription per mount.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import Any

import reactivex as rx
from reactivex.abc import DisposableBase
from vmx import Message, MessageHub, when_property_changed


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
        property_names: Iterable[str],
        on_property_changed: Callable[[str], None],
    ) -> None:
        """Subscribe to ``hub`` and invoke ``on_property_changed`` per match."""
        if self._hub_subscription is not None:
            self._hub_subscription.dispose()

        streams = tuple(
            when_property_changed(hub, vm, property_name) for property_name in property_names
        )
        self._hub_subscription = rx.merge(*streams).subscribe(
            on_next=lambda msg: on_property_changed(msg.property_name)
        )

    def unsubscribe_from_vm(self) -> None:
        if self._hub_subscription is not None:
            self._hub_subscription.dispose()
            self._hub_subscription = None


__all__ = ["HubSubscriberMixin"]
