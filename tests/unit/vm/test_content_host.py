"""Tests for the ContentHostVM."""

from __future__ import annotations

from typing import cast

from vmx import NULL_DISPATCHER, ComponentVM, MessageHub
from vmx.lifecycle.status import ConstructionStatus
from vmx.messages.protocols import Message

from aws_tui.vm.content_host_vm import ContentHostVM


def _hub() -> MessageHub[Message]:
    return cast("MessageHub[Message]", MessageHub())


def _component() -> ComponentVM:
    return ComponentVM.builder().name("test").with_null_services().build()


def _build() -> ContentHostVM:
    vm = ContentHostVM(hub=_hub(), dispatcher=NULL_DISPATCHER)
    vm.construct()
    return vm


async def test_set_content_constructs_new_vm() -> None:
    host = _build()
    child = _component()
    await host.set_content(child, service_id="ec2")
    assert host.current is child
    assert host.current_id == "ec2"
    assert child.is_constructed
    host.dispose()


async def test_set_content_disposes_old_vm() -> None:
    host = _build()
    first = _component()
    second = _component()
    await host.set_content(first, service_id="ec2")
    await host.set_content(second, service_id="s3")
    assert first.status == ConstructionStatus.DISPOSED
    assert second.is_constructed
    assert host.current is second
    assert host.current_id == "s3"
    host.dispose()


async def test_set_content_same_service_is_noop() -> None:
    host = _build()
    first = _component()
    second = _component()
    await host.set_content(first, service_id="ec2")
    await host.set_content(second, service_id="ec2")
    # Second call is a no-op: first stays, second is NOT constructed.
    assert host.current is first
    assert first.is_constructed
    assert second.status == ConstructionStatus.DESTRUCTED
    host.dispose()


async def test_dispose_disposes_current_content() -> None:
    host = _build()
    child = _component()
    await host.set_content(child, service_id="ec2")
    host.dispose()
    assert child.status == ConstructionStatus.DISPOSED


async def test_set_content_awaits_setup_when_vm_defines_one() -> None:
    """Regression: production-path `DualPaneVM` exposes async `setup()` that
    runs `provider.list()` on each pane. The host must await it after
    `construct()` or the panes render empty. Cf. `S3Service.build_vm`'s
    docstring requiring this contract.
    """

    class _SetupVM:
        def __init__(self) -> None:
            self.constructed = False
            self.setup_called = False
            self.is_constructed = False
            self.status = ConstructionStatus.DESTRUCTED

        def construct(self) -> None:
            self.constructed = True
            self.is_constructed = True
            self.status = ConstructionStatus.CONSTRUCTED

        async def setup(self) -> None:
            assert self.constructed, "setup must run after construct"
            self.setup_called = True

        def dispose(self) -> None:
            self.status = ConstructionStatus.DISPOSED

    host = _build()
    vm = _SetupVM()
    await host.set_content(cast("ComponentVM", vm), service_id="s3")
    assert vm.constructed
    assert vm.setup_called
    host.dispose()


async def test_set_content_none_clears_current() -> None:
    host = _build()
    child = _component()
    await host.set_content(child, service_id="ec2")
    await host.set_content(None, service_id=None)
    assert host.current is None
    assert host.current_id is None
    assert child.status == ConstructionStatus.DISPOSED
    host.dispose()


def test_initial_state() -> None:
    host = _build()
    assert host.current is None
    assert host.current_id is None
    host.dispose()
