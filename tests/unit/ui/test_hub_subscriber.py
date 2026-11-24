"""HubSubscriberMixin binds several VMs through one subscription (#582)."""

from __future__ import annotations

from vmx import Message, MessageHub, PropertyChangedMessage

from aws_tui.ui.widgets._subscriber import HubSubscriberMixin


class _Probe(HubSubscriberMixin):
    pass


def _send(hub: MessageHub[Message], sender: object, name: str) -> None:
    hub.send(PropertyChangedMessage.create(sender, "probe", name))


def test_subscribe_to_vms_covers_each_pair_and_ignores_others() -> None:
    hub: MessageHub[Message] = MessageHub()
    first, second, other = object(), object(), object()
    seen: list[str] = []
    probe = _Probe()
    probe.subscribe_to_vms(
        hub=hub,
        bindings=((first, ("a", "b")), (second, ("c",))),
        on_property_changed=seen.append,
    )
    _send(hub, first, "a")
    _send(hub, first, "b")
    _send(hub, second, "c")
    _send(hub, first, "c")  # unlisted name for this sender
    _send(hub, other, "a")  # unlisted sender
    assert seen == ["a", "b", "c"]
    probe.unsubscribe_from_vm()
    _send(hub, first, "a")
    assert seen == ["a", "b", "c"]
    hub.dispose()
