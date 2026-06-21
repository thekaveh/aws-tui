"""Tests for NavMenuVM, Service protocol, and ServiceRegistry."""

from __future__ import annotations

from typing import cast

import pytest
from vmx import NULL_DISPATCHER, MessageHub
from vmx.messages.protocols import Message

from aws_tui.infra.connection_resolver import Connection
from aws_tui.vm.nav_menu_vm import NavItemVM as ServiceItemVM
from aws_tui.vm.nav_menu_vm import NavMenuVM as ServicesMenuVM
from aws_tui.vm.services_protocol import (
    Service,
    ServiceDescriptor,
    ServiceNotFound,
    ServiceRegistry,
)


def _hub() -> MessageHub[Message]:
    return cast("MessageHub[Message]", MessageHub())


def _aws_conn(name: str = "kaveh-dev") -> Connection:
    return Connection(
        name=name,
        kind="aws",
        region="us-east-1",
        source="config",
        profile=name,
    )


def _minio_conn() -> Connection:
    return Connection(
        name="minio-local",
        kind="s3-compatible",
        region="us-east-1",
        source="config",
        endpoint_url="http://localhost:9000",
    )


class FakeService:
    """Fake Service for tests — implements the Service protocol."""

    def __init__(self, id_: str, label: str, *, accepts_aws: bool, accepts_s3_compat: bool) -> None:
        self.descriptor = ServiceDescriptor(id=id_, label=label, icon="s")
        self._accepts_aws = accepts_aws
        self._accepts_s3_compat = accepts_s3_compat

    def supports(self, connection: Connection) -> bool:
        if connection.kind == "aws":
            return self._accepts_aws
        if connection.kind == "s3-compatible":
            return self._accepts_s3_compat
        return False

    def build_vm(self, connection: Connection) -> object:
        return object()


# -------------------- ServiceRegistry --------------------


def test_registry_register_and_all() -> None:
    reg = ServiceRegistry()
    s = FakeService("s3", "S3", accepts_aws=True, accepts_s3_compat=True)
    reg.register(s)
    assert reg.all() == (s,)


def test_registry_get_returns_service() -> None:
    reg = ServiceRegistry()
    s = FakeService("ec2", "EC2", accepts_aws=True, accepts_s3_compat=False)
    reg.register(s)
    assert reg.get("ec2") is s


def test_registry_get_missing_raises() -> None:
    reg = ServiceRegistry()
    with pytest.raises(ServiceNotFound):
        reg.get("nope")


def test_registry_duplicate_register_replaces() -> None:
    reg = ServiceRegistry()
    a = FakeService("s3", "S3 v1", accepts_aws=True, accepts_s3_compat=True)
    b = FakeService("s3", "S3 v2", accepts_aws=True, accepts_s3_compat=True)
    reg.register(a)
    reg.register(b)
    assert reg.get("s3") is b
    assert len(reg.all()) == 1


def test_service_descriptor_is_frozen() -> None:
    d = ServiceDescriptor(id="s3", label="S3", icon="s")
    with pytest.raises(AttributeError):
        d.label = "x"  # type: ignore[misc]


# -------------------- NavMenuVM --------------------


def _menu(registry: ServiceRegistry) -> ServicesMenuVM:
    vm = ServicesMenuVM(registry=registry, hub=_hub(), dispatcher=NULL_DISPATCHER)
    vm.construct()
    return vm


def test_menu_initially_empty() -> None:
    reg = ServiceRegistry()
    menu = _menu(reg)
    assert menu.items == ()
    menu.dispose()


def test_menu_lists_compatible_services_for_aws_connection() -> None:
    reg = ServiceRegistry()
    reg.register(FakeService("s3", "S3", accepts_aws=True, accepts_s3_compat=True))
    reg.register(FakeService("ec2", "EC2", accepts_aws=True, accepts_s3_compat=False))
    reg.register(FakeService("iam", "IAM", accepts_aws=True, accepts_s3_compat=False))
    menu = _menu(reg)
    menu.update_connection(_aws_conn())
    ids = {item.descriptor.id for item in menu.items}
    assert ids == {"s3", "ec2", "iam", "settings"}
    menu.dispose()


def test_menu_collapses_for_s3_compatible_connection() -> None:
    reg = ServiceRegistry()
    reg.register(FakeService("s3", "S3", accepts_aws=True, accepts_s3_compat=True))
    reg.register(FakeService("ec2", "EC2", accepts_aws=True, accepts_s3_compat=False))
    reg.register(FakeService("iam", "IAM", accepts_aws=True, accepts_s3_compat=False))
    menu = _menu(reg)
    menu.update_connection(_minio_conn())
    ids = {item.descriptor.id for item in menu.items}
    assert ids == {"s3", "settings"}
    menu.dispose()


def test_menu_switch_service_command() -> None:
    reg = ServiceRegistry()
    reg.register(FakeService("s3", "S3", accepts_aws=True, accepts_s3_compat=True))
    reg.register(FakeService("ec2", "EC2", accepts_aws=True, accepts_s3_compat=False))
    menu = _menu(reg)
    menu.update_connection(_aws_conn())
    assert menu.selected_id is None
    menu.switch_service_command.execute("ec2")
    assert menu.selected_id == "ec2"
    menu.dispose()


def test_menu_switch_service_unknown_id_noop() -> None:
    reg = ServiceRegistry()
    reg.register(FakeService("s3", "S3", accepts_aws=True, accepts_s3_compat=True))
    menu = _menu(reg)
    menu.update_connection(_aws_conn())
    menu.switch_service_command.execute("does-not-exist")
    assert menu.selected_id is None
    menu.dispose()


def test_menu_re_filters_on_connection_change() -> None:
    reg = ServiceRegistry()
    reg.register(FakeService("s3", "S3", accepts_aws=True, accepts_s3_compat=True))
    reg.register(FakeService("ec2", "EC2", accepts_aws=True, accepts_s3_compat=False))
    menu = _menu(reg)
    menu.update_connection(_aws_conn())
    assert len(menu.items) == 3  # s3, ec2, settings
    menu.update_connection(_minio_conn())
    assert {item.descriptor.id for item in menu.items} == {"s3", "settings"}
    menu.dispose()


def test_service_item_vm_construct_dispose() -> None:
    hub = _hub()
    item = ServiceItemVM(
        descriptor=ServiceDescriptor(id="s3", label="S3", icon="s"),
        hub=hub,
        dispatcher=NULL_DISPATCHER,
    )
    item.construct()
    assert item.is_constructed
    item.dispose()


def test_service_item_default_flags() -> None:
    item = ServiceItemVM(
        descriptor=ServiceDescriptor(id="s3", label="S3", icon="s"),
        hub=_hub(),
        dispatcher=NULL_DISPATCHER,
    )
    assert item.is_focused is False
    assert item.is_selected is False


# -------------------- Service protocol (structural) --------------------


def test_fake_service_satisfies_protocol() -> None:
    s: Service = FakeService("s3", "S3", accepts_aws=True, accepts_s3_compat=True)
    assert isinstance(s, Service)
    assert s.descriptor.id == "s3"


# -------------------- ConnectionListChangedMessage subscription --------------------


def test_nav_menu_always_includes_settings_item_last() -> None:
    """Settings is a hard-coded nav peer to the service items; it
    appears as the LAST item in the menu regardless of which services
    are registered."""
    from aws_tui.vm.nav_menu_vm import NavMenuVM

    reg = ServiceRegistry()
    reg.register(FakeService("s3", "S3", accepts_aws=True, accepts_s3_compat=True))
    hub = _hub()
    vm = NavMenuVM(registry=reg, hub=hub, dispatcher=NULL_DISPATCHER)
    vm.construct()
    try:
        vm.update_connection(_aws_conn())
        assert vm.items[-1].descriptor.id == "settings"
        assert vm.items[-1].descriptor.label == "Settings"
        assert vm.items[-1].descriptor.icon == "⚙"
        # select("settings") via the canonical API.
        vm.switch_service_command.execute("settings")
        assert vm.selected_id == "settings"
    finally:
        vm.dispose()


def test_nav_menu_vm_refreshes_on_connection_list_change() -> None:
    """When a ConnectionListChangedMessage arrives on the hub, the
    nav menu re-derives its filter — same path that
    ConnectionChangedMessage already triggers."""
    from unittest.mock import MagicMock

    from aws_tui.vm.messages import ConnectionListChangedMessage

    hub = _hub()
    # Use a MagicMock registry so we can observe re-filter calls.
    registry = MagicMock()
    registry.all.return_value = ()
    vm = ServicesMenuVM(registry=registry, hub=hub, dispatcher=NULL_DISPATCHER)
    vm.construct()
    try:
        # Give the VM an active connection so _desired_service_ids() actually
        # queries the registry (when _connection is None, the early-return
        # skips registry.all() entirely).
        vm.update_connection(_minio_conn())
        registry.all.reset_mock()
        hub.send(ConnectionListChangedMessage(names=("minio-local",), change="updated"))
        # The subscriber must have called the same filter-rebuild path
        # that ConnectionChangedMessage uses — at minimum, registry.all
        # (used by _desired_service_ids) is called.
        assert registry.all.called, (
            "NavMenuVM did not re-derive its filter after ConnectionListChangedMessage"
        )
    finally:
        vm.dispose()
