"""End-to-end file-manager integration test.

Composes a :class:`RootVM` with a :class:`ServiceRegistry` containing
the real :class:`S3Service` (configured with the test ``s3_fs_factory``
so we substitute :class:`InMemoryFS` for the S3 pane) and drives a
``switch_service('s3')`` to land a real :class:`DualPaneVM` in
``ContentHostVM.current``. Connection / service swaps verify the
dispose/reconstruct lifecycle works against the actual VM tree.
"""

from __future__ import annotations

import tempfile
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
from vmx import NULL_DISPATCHER, ComponentVM
from vmx.lifecycle.status import ConstructionStatus

from aws_tui.domain.filesystem import PathRef
from aws_tui.domain.transfer_journal import TransferJournal
from aws_tui.infra.aws_session import TokenState
from aws_tui.infra.connection_resolver import Connection
from aws_tui.infra.keymap_store import KeymapStore
from aws_tui.infra.log_sink import LogSink
from aws_tui.infra.theme_store import ThemeStore
from aws_tui.services.s3 import S3Service
from aws_tui.vm.file_manager.dual_pane_vm import DualPaneVM
from aws_tui.vm.root_vm import RootVM
from aws_tui.vm.services_protocol import ServiceDescriptor, ServiceRegistry
from tests.unit.domain._in_memory_fs import InMemoryFS


async def _astream(payload: bytes) -> AsyncIterator[bytes]:
    yield payload


async def _seed_fs() -> InMemoryFS:
    fs = InMemoryFS()
    await fs.write_stream(PathRef(("hello.txt",)), _astream(b"hello"))
    await fs.mkdir(PathRef(("docs",)))
    return fs


def _aws_conn() -> Connection:
    return Connection(
        name="aws-default",
        kind="aws",
        region="us-east-1",
        source="explicit",
        profile=None,
    )


def _minio_conn() -> Connection:
    return Connection(
        name="minio-local",
        kind="s3-compatible",
        region="us-east-1",
        source="explicit",
        endpoint_url="http://localhost:9000",
        access_key_id="ak",
        secret_access_key="sk",
        force_path_style=True,
        verify_tls=False,
    )


class _CountingService:
    """Tiny placeholder service used to confirm dispose-on-swap behavior."""

    def __init__(self, id_: str) -> None:
        self.descriptor = ServiceDescriptor(id=id_, label=id_.upper(), icon="x")
        self.dispose_count = 0
        self.built: list[ComponentVM] = []

    def supports(self, _connection: Connection) -> bool:
        return True

    def build_vm(self, connection: Connection) -> ComponentVM:
        vm = (
            ComponentVM.builder()
            .name(f"svc.{self.descriptor.id}.{connection.name}")
            .with_null_services()
            .build()
        )
        real_dispose = vm.dispose

        def _counting_dispose() -> None:
            self.dispose_count += 1
            real_dispose()

        vm.dispose = _counting_dispose  # type: ignore[method-assign]
        self.built.append(vm)
        return vm


@pytest.mark.asyncio
async def test_m4_switch_to_s3_hosts_dual_pane_vm(tmp_path: Path) -> None:
    """Switching to S3Service installs a real DualPaneVM in ContentHostVM."""
    (tmp_path / "local").mkdir()
    seeded: list[InMemoryFS] = []

    async def _factory_setup() -> None:
        seeded.append(await _seed_fs())

    await _factory_setup()

    def _s3_factory(_conn: Connection) -> Any:
        return seeded[-1]

    s3_service = S3Service(
        transfer_journal=TransferJournal(base_dir=tmp_path / "journal"),
        # Hub is set later by RootVM — we use RootVM's hub via the service.
        # S3Service is constructed before RootVM here so we pull the hub
        # off it once we have it.
        hub=None,
        dispatcher=NULL_DISPATCHER,
        local_root=tmp_path / "local",
        s3_fs_factory=_s3_factory,
    )
    registry = ServiceRegistry()
    registry.register(s3_service)

    log_dir = tempfile.mkdtemp(prefix="awsTuiTest-m4-")
    root = RootVM(
        registry=registry,
        keymap=KeymapStore(),
        theme=ThemeStore(),
        log=LogSink(base_dir=Path(log_dir)),
        dispatcher=NULL_DISPATCHER,
    )
    # Wire the service to the RootVM's hub now that it exists.
    s3_service.bind_hub(root.message_hub)
    root.construct()

    await root.switch_connection_with(_aws_conn(), TokenState.CONNECTED)
    await root.switch_service("s3")

    assert root.content_host.current_id == "s3"
    dual = root.content_host.current
    assert isinstance(dual, DualPaneVM)
    # The dual pane is constructed by ContentHostVM but setup is deferred.
    await dual.setup()
    # Left = S3 (InMemoryFS via factory), right = LocalFS.
    left_names = [e.entry.name for e in dual.left.entries]
    assert "hello.txt" in left_names

    root.shutdown()
    assert root.status == ConstructionStatus.DISPOSED


@pytest.mark.asyncio
async def test_m4_switch_connection_clears_dual_pane(tmp_path: Path) -> None:
    """Switching connection re-fires ServicesMenu filter and clears content."""
    (tmp_path / "local").mkdir()
    fs = await _seed_fs()

    s3_service = S3Service(
        transfer_journal=TransferJournal(base_dir=tmp_path / "journal"),
        hub=None,
        dispatcher=NULL_DISPATCHER,
        local_root=tmp_path / "local",
        s3_fs_factory=lambda _c: fs,
    )
    registry = ServiceRegistry()
    registry.register(s3_service)

    log_dir = tempfile.mkdtemp(prefix="awsTuiTest-m4-")
    root = RootVM(
        registry=registry,
        keymap=KeymapStore(),
        theme=ThemeStore(),
        log=LogSink(base_dir=Path(log_dir)),
        dispatcher=NULL_DISPATCHER,
    )
    s3_service.bind_hub(root.message_hub)
    root.construct()

    await root.switch_connection_with(_aws_conn(), TokenState.CONNECTED)
    await root.switch_service("s3")
    assert isinstance(root.content_host.current, DualPaneVM)

    # Switch to a different (s3-compatible) connection — ContentHostVM
    # clears, and the menu still includes s3 (which supports both kinds).
    await root.switch_connection_with(_minio_conn(), TokenState.CONNECTED)
    assert root.content_host.current is None
    assert {i.descriptor.id for i in root.services_menu.items} == {"s3", "settings"}

    # Rebuilding s3 against the new connection produces a fresh DualPaneVM.
    await root.switch_service("s3")
    new_dual = root.content_host.current
    assert isinstance(new_dual, DualPaneVM)

    root.shutdown()


@pytest.mark.asyncio
async def test_m4_switching_to_other_service_disposes_dual_pane(tmp_path: Path) -> None:
    """Switching s3 -> mock service disposes the DualPaneVM."""
    (tmp_path / "local").mkdir()
    fs = await _seed_fs()

    s3_service = S3Service(
        transfer_journal=TransferJournal(base_dir=tmp_path / "journal"),
        hub=None,
        dispatcher=NULL_DISPATCHER,
        local_root=tmp_path / "local",
        s3_fs_factory=lambda _c: fs,
    )
    other = _CountingService("ec2-mock")
    registry = ServiceRegistry()
    registry.register(s3_service)
    registry.register(other)

    log_dir = tempfile.mkdtemp(prefix="awsTuiTest-m4-")
    root = RootVM(
        registry=registry,
        keymap=KeymapStore(),
        theme=ThemeStore(),
        log=LogSink(base_dir=Path(log_dir)),
        dispatcher=NULL_DISPATCHER,
    )
    s3_service.bind_hub(root.message_hub)
    root.construct()

    await root.switch_connection_with(_aws_conn(), TokenState.CONNECTED)
    await root.switch_service("s3")
    dual = root.content_host.current
    assert isinstance(dual, DualPaneVM)

    await root.switch_service("ec2-mock")
    # The DualPaneVM was disposed when ContentHostVM swapped it out.
    assert dual.status == ConstructionStatus.DISPOSED
    assert root.content_host.current_id == "ec2-mock"
    assert len(other.built) == 1

    root.shutdown()
