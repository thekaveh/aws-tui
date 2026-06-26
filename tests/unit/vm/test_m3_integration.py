"""End-to-end integration test for the VM-shell tree (RootVM + chrome + content host)."""

from __future__ import annotations

import tempfile
from pathlib import Path

from vmx import NULL_DISPATCHER, ComponentVM
from vmx.lifecycle.status import ConstructionStatus

from aws_tui.infra.aws_session import TokenState
from aws_tui.infra.connection_resolver import Connection
from aws_tui.infra.keymap_store import KeymapStore
from aws_tui.infra.log_sink import LogSink
from aws_tui.infra.theme_store import ThemeStore
from aws_tui.vm.messages import ConnectionChangedMessage, ThemeChangedMessage
from aws_tui.vm.root_vm import RootVM
from aws_tui.vm.services_protocol import ServiceDescriptor, ServiceRegistry


class _CountingService:
    def __init__(self, id_: str, *, supports_kind: tuple[str, ...]) -> None:
        self.descriptor = ServiceDescriptor(id=id_, label=id_.upper(), icon="x")
        self._supports = set(supports_kind)
        self.built: list[ComponentVM] = []
        self.dispose_count = 0

    def supports(self, connection: Connection) -> bool:
        return connection.kind in self._supports

    def build_vm(self, connection: Connection) -> ComponentVM:
        original_dispose: list[object] = []
        vm = (
            ComponentVM.builder()
            .name(f"svc.{self.descriptor.id}.{connection.name}")
            .with_null_services()
            .build()
        )
        # Wrap dispose to count.
        outer_self = self
        real = vm.dispose

        def _counting_dispose() -> None:
            outer_self.dispose_count += 1
            real()

        # Setting an instance attribute over a method works for our facade
        # measurement purposes.
        original_dispose.append(vm.dispose)
        vm.dispose = _counting_dispose  # type: ignore[method-assign]
        self.built.append(vm)
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


async def test_m3_full_lifecycle() -> None:
    """Compose the whole shell against fakes, drive a few transitions."""
    registry = ServiceRegistry()
    s3 = _CountingService("s3", supports_kind=("aws", "s3-compatible"))
    ec2 = _CountingService("ec2", supports_kind=("aws",))
    registry.register(s3)
    registry.register(ec2)

    log_dir = tempfile.mkdtemp(prefix="awsTuiTest-log-")
    root = RootVM(
        registry=registry,
        keymap=KeymapStore(),
        theme=ThemeStore(),
        log=LogSink(base_dir=Path(log_dir)),
        dispatcher=NULL_DISPATCHER,
    )
    root.construct()

    # Hub propagation: ConnectionChangedMessage drives the status bar.
    seen_conn: list[ConnectionChangedMessage] = []
    sub_conn = root.message_hub.messages.subscribe(
        on_next=lambda m: seen_conn.append(m) if isinstance(m, ConnectionChangedMessage) else None
    )

    await root.switch_connection_with(_aws_conn(), TokenState.CONNECTED)
    assert root.chrome.status_bar.connection_label == "kaveh-dev (aws)"
    # The connection change message was published exactly once.
    assert len(seen_conn) == 1

    # Both services qualify under aws.
    assert {i.descriptor.id for i in root.services_menu.items} == {"s3", "ec2", "settings"}

    # Switch service builds and hosts the new VM.
    await root.switch_service("s3")
    assert root.content_host.current_id == "s3"
    s3_first_vm = root.content_host.current
    assert s3_first_vm is not None

    # Re-selecting s3 is a no-op (per spec §5.4).
    await root.switch_service("s3")
    assert root.content_host.current is s3_first_vm
    assert len(s3.built) == 1

    # Switch to ec2: previous content disposed, ec2 built and hosted.
    await root.switch_service("ec2")
    assert s3.dispose_count == 1
    assert root.content_host.current_id == "ec2"
    assert len(ec2.built) == 1

    # Switch connection to s3-compat: menu collapses, hosted content cleared.
    await root.switch_connection_with(_minio_conn(), TokenState.CONNECTED)
    assert {i.descriptor.id for i in root.services_menu.items} == {"s3", "settings"}
    assert root.content_host.current is None
    assert ec2.dispose_count == 1

    # Theme switch publishes the right message.
    seen_theme: list[str] = []
    sub_theme = root.message_hub.messages.subscribe(
        on_next=lambda m: seen_theme.append(m.name) if isinstance(m, ThemeChangedMessage) else None
    )
    await root.switch_theme("voidline")
    assert seen_theme == ["voidline"]

    sub_conn.dispose()
    sub_theme.dispose()

    # Shutdown disposes the entire tree.
    root.shutdown()
    assert root.status == ConstructionStatus.DISPOSED
