"""VMx familiarization smoke (Task 1 acceptance).

Sanity-constructs and disposes the core VMx primitives the M3 facades will
use. If VMx changes shape such that these break, the rest of the vm/ layer
will need updating; this is the early-warning canary.
"""

from __future__ import annotations

from vmx import (
    NULL_DISPATCHER,
    NULL_MESSAGE_HUB,
    AggregateVMBuilder3,
    ComponentVM,
    CompositeVM,
    ConstructionStatus,
    Message,
    MessageHub,
    PropertyChangedMessage,
)


def test_component_vm_construct_dispose() -> None:
    vm = ComponentVM.builder().name("smoke").with_null_services().build()
    assert vm.status == ConstructionStatus.DESTRUCTED
    vm.construct()
    assert vm.is_constructed
    vm.destruct()
    assert vm.status == ConstructionStatus.DESTRUCTED
    vm.dispose()
    assert vm.status == ConstructionStatus.DISPOSED


def test_composite_vm_with_children_cascades_dispose() -> None:
    composite = (
        CompositeVM.builder()
        .name("parent")
        .services(NULL_MESSAGE_HUB, NULL_DISPATCHER)
        .children(
            lambda: (
                ComponentVM.builder().name("c1").with_null_services().build(),
                ComponentVM.builder().name("c2").with_null_services().build(),
            )
        )
        .build()
    )
    composite.construct()
    assert composite.count == 2
    assert all(child.is_constructed for child in composite)

    composite.dispose()
    # Disposes cascade depth-first.
    assert all(child.status == ConstructionStatus.DISPOSED for child in composite)


def test_aggregate_vm3_lazy_factories() -> None:
    seen: list[str] = []

    def fac(name: str) -> ComponentVM:
        return ComponentVM.builder().name(name).with_null_services().build()

    agg = (
        AggregateVMBuilder3[ComponentVM, ComponentVM, ComponentVM]()
        .name("agg3")
        .services(NULL_MESSAGE_HUB, NULL_DISPATCHER)
        .component_1(lambda: (seen.append("1"), fac("c1"))[1])
        .component_2(lambda: (seen.append("2"), fac("c2"))[1])
        .component_3(lambda: (seen.append("3"), fac("c3"))[1])
        .build()
    )
    # Factories are not invoked until construct().
    assert seen == []
    assert agg.component_1 is None
    agg.construct()
    assert seen == ["1", "2", "3"]
    assert agg.component_1 is not None
    assert agg.component_1.is_constructed
    assert agg.component_2 is not None
    assert agg.component_2.is_constructed
    assert agg.component_3 is not None
    assert agg.component_3.is_constructed
    agg.dispose()
    assert agg.component_1.status == ConstructionStatus.DISPOSED


def test_message_hub_publishes_custom_message() -> None:
    from dataclasses import dataclass

    @dataclass(frozen=True, slots=True)
    class CustomMessage:
        payload: str
        sender_name: str = "fake"

        @property
        def sender_object(self) -> object:
            return self

    hub: MessageHub[Message] = MessageHub()
    received: list[CustomMessage] = []
    sub = hub.messages.subscribe(on_next=lambda m: received.append(m))  # type: ignore[arg-type]
    hub.send(CustomMessage(payload="hello"))
    assert received == [CustomMessage(payload="hello")]
    sub.dispose()
    hub.dispose()


def test_property_changed_message_create() -> None:
    msg = PropertyChangedMessage.create(sender="x", sender_name="x", property_name="foo")
    assert msg.property_name == "foo"
    assert msg.sender_name == "x"
    assert msg.sender_object == "x"
