"""VMx component base for page-owned collaborators.

A collaborator derives from :class:`IsolatedComponentVM` instead of taking
``notify`` and ``is_current`` callbacks. It publishes one property name per
changed property, and its observers cannot break it or leak values into logs.
"""

from __future__ import annotations

import reactivex as rx
from vmx import ComponentVM, Dispatcher, Message, MessageHubProto

from aws_tui.vm._observable import isolated, value_free_hub_sends


class IsolatedComponentVM(ComponentVM):
    """A ComponentVM that publishes named properties to the hub and locally.

    Subscribe through :attr:`on_property_changed`, which isolates each
    subscriber. Publish through :meth:`_publish`, which arms the value-free
    redaction filter for the hub send. Two VMx hazards are handled here: the
    base's own ``status`` and ``is_current`` names are never reused by
    subclasses, and a subscriber cannot stop later subscribers from running.
    """

    def __init__(self, *, name: str, hub: MessageHubProto[Message], dispatcher: Dispatcher) -> None:
        super().__init__(name=name, hint="", hub=hub, dispatcher=dispatcher)
        self._channel = name

    @property
    def on_property_changed(self) -> rx.Observable[str]:
        return isolated(self.property_changed, self._channel)

    def _publish(self, *names: str) -> None:
        with value_free_hub_sends():
            for name in names:
                self._notify_property_changed(name)


__all__ = ["IsolatedComponentVM"]
