"""Tests for the RootVM."""

from __future__ import annotations

import pytest
from vmx import NULL_DISPATCHER, ComponentVM
from vmx.lifecycle.status import ConstructionStatus

from aws_tui.infra.aws_session import TokenState
from aws_tui.infra.connection_resolver import Connection
from aws_tui.infra.keymap_store import KeymapStore
from aws_tui.infra.log_sink import LogSink
from aws_tui.infra.theme_store import ThemeStore
from aws_tui.vm.root_vm import RootVM
from aws_tui.vm.services_protocol import ServiceDescriptor, ServiceRegistry


class _FakeService:
    def __init__(self, id_: str, *, accepts_aws: bool = True, accepts_s3: bool = True) -> None:
        self.descriptor = ServiceDescriptor(id=id_, label=id_.upper(), icon="x")
        self.constructed: list[ComponentVM] = []
        self._accepts_aws = accepts_aws
        self._accepts_s3 = accepts_s3

    def supports(self, connection: Connection) -> bool:
        if connection.kind == "aws":
            return self._accepts_aws
        if connection.kind == "s3-compatible":
            return self._accepts_s3
        return False

    def build_vm(self, connection: Connection) -> ComponentVM:
        vm = (
            ComponentVM.builder().name(f"content.{self.descriptor.id}").with_null_services().build()
        )
        self.constructed.append(vm)
        return vm


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


def _build_root(*services: _FakeService, tmp_path_factory: object = None) -> RootVM:
    import tempfile

    registry = ServiceRegistry()
    for s in services:
        registry.register(s)
    log_dir = tempfile.mkdtemp(prefix="awsTuiTest-log-")
    from pathlib import Path

    root = RootVM(
        registry=registry,
        keymap=KeymapStore(),
        theme=ThemeStore(),
        log=LogSink(base_dir=Path(log_dir)),
        dispatcher=NULL_DISPATCHER,
    )
    root.construct()
    return root


def test_root_constructs_three_aggregates() -> None:
    root = _build_root()
    assert root.services_menu is not None
    assert root.content_host is not None
    assert root.chrome is not None
    root.dispose()


async def test_switch_connection_updates_status_and_menu() -> None:
    s3 = _FakeService("s3")
    ec2 = _FakeService("ec2", accepts_s3=False)
    root = _build_root(s3, ec2)
    await root.switch_connection_with(_aws_conn(), TokenState.CONNECTED)
    assert root.chrome.status_bar.connection_label == "kaveh-dev (aws)"
    ids = {item.descriptor.id for item in root.services_menu.items}
    assert ids == {"s3", "ec2"}
    root.dispose()


async def test_switch_connection_to_s3_collapses_menu() -> None:
    s3 = _FakeService("s3")
    ec2 = _FakeService("ec2", accepts_s3=False)
    root = _build_root(s3, ec2)
    await root.switch_connection_with(_minio_conn(), TokenState.CONNECTED)
    ids = {item.descriptor.id for item in root.services_menu.items}
    assert ids == {"s3"}
    root.dispose()


async def test_switch_service_builds_and_hosts() -> None:
    s3 = _FakeService("s3")
    ec2 = _FakeService("ec2", accepts_s3=False)
    root = _build_root(s3, ec2)
    await root.switch_connection_with(_aws_conn(), TokenState.CONNECTED)
    await root.switch_service("ec2")
    assert root.content_host.current_id == "ec2"
    assert ec2.constructed
    assert ec2.constructed[0].is_constructed
    root.dispose()


async def test_switch_service_same_id_is_noop() -> None:
    s3 = _FakeService("s3")
    root = _build_root(s3)
    await root.switch_connection_with(_aws_conn(), TokenState.CONNECTED)
    await root.switch_service("s3")
    first_vm = root.content_host.current
    await root.switch_service("s3")
    assert root.content_host.current is first_vm
    # Service is asked to build_vm only once.
    assert len(s3.constructed) == 1
    root.dispose()


async def test_switch_service_replaces_old_content() -> None:
    s3 = _FakeService("s3")
    ec2 = _FakeService("ec2")
    root = _build_root(s3, ec2)
    await root.switch_connection_with(_aws_conn(), TokenState.CONNECTED)
    await root.switch_service("s3")
    s3_vm = s3.constructed[0]
    await root.switch_service("ec2")
    assert s3_vm.status == ConstructionStatus.DISPOSED
    assert root.content_host.current_id == "ec2"
    root.dispose()


async def test_switch_connection_disposes_active_service_content() -> None:
    s3 = _FakeService("s3")
    root = _build_root(s3)
    await root.switch_connection_with(_aws_conn(), TokenState.CONNECTED)
    await root.switch_service("s3")
    first_vm = s3.constructed[0]
    await root.switch_connection_with(_aws_conn("kaveh-prod"), TokenState.CONNECTED)
    assert first_vm.status == ConstructionStatus.DISPOSED
    assert root.content_host.current is None
    root.dispose()


async def test_switch_service_unknown_id_raises() -> None:
    from aws_tui.vm.services_protocol import ServiceNotFound

    root = _build_root(_FakeService("s3"))
    await root.switch_connection_with(_aws_conn(), TokenState.CONNECTED)
    with pytest.raises(ServiceNotFound):
        await root.switch_service("does-not-exist")
    root.dispose()


async def test_switch_theme_publishes_message() -> None:
    root = _build_root()
    seen: list[str] = []
    from aws_tui.vm.messages import ThemeChangedMessage

    sub = root.message_hub.messages.subscribe(
        on_next=lambda m: seen.append(m.name) if isinstance(m, ThemeChangedMessage) else None
    )
    await root.switch_theme("voidline")
    sub.dispose()
    assert "voidline" in seen
    root.dispose()


async def test_shutdown_disposes_full_tree() -> None:
    s3 = _FakeService("s3")
    root = _build_root(s3)
    await root.switch_connection_with(_aws_conn(), TokenState.CONNECTED)
    await root.switch_service("s3")
    content_vm = root.content_host.current
    await root.shutdown()
    assert root.status == ConstructionStatus.DISPOSED
    assert content_vm is not None
    assert content_vm.status == ConstructionStatus.DISPOSED
