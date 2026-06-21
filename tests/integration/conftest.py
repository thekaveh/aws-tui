"""Integration-tier shared fixtures.

- :func:`minio_endpoint` — session-scoped MinIO testcontainer. Used by
  the two MinIO smoke suites (opt-in via ``-m integration``).
- :func:`app_context_factory` — per-test fixture returning a builder
  callable that constructs an :class:`AppContext` for full-app pilot
  tests. Pass ``fs=`` to inject a seeded :class:`InMemoryFS` as the
  S3 service's provider; defaults to an empty in-memory FS.
  Consolidates the boilerplate that would otherwise be duplicated
  across every integration test that needs a real ``AppContext``.
"""

from __future__ import annotations

import contextlib
import tempfile
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import cast

import pytest
from vmx import MessageHub, RxDispatcher

from aws_tui.composition import AppContext
from aws_tui.domain.filesystem import FileSystemProvider
from aws_tui.domain.transfer_journal import TransferJournal
from aws_tui.infra.aws_session import AwsSession
from aws_tui.infra.config_store import ConfigStore
from aws_tui.infra.connection_resolver import Connection, ConnectionResolver
from aws_tui.infra.keymap_store import KeymapStore
from aws_tui.infra.log_sink import LogSink
from aws_tui.infra.theme_store import ThemeStore
from aws_tui.services.s3 import S3Service
from aws_tui.vm.chrome.command_palette_vm import CommandPaletteVM
from aws_tui.vm.chrome.confirm_vm import ConfirmationVM
from aws_tui.vm.chrome.quick_look_vm import QuickLookVM
from aws_tui.vm.file_manager.transfers_vm import TransfersVM
from aws_tui.vm.root_vm import RootVM
from aws_tui.vm.services_protocol import Service, ServiceRegistry
from aws_tui.vm.settings.s3_connections_vm import S3ConnectionsVM
from tests.unit.domain._in_memory_fs import InMemoryFS


@pytest.fixture(scope="session")
def minio_endpoint() -> Iterator[tuple[str, str, str]]:
    """Spin up a real MinIO container.

    Returns ``(endpoint_url, access_key, secret_key)``. The container is
    reused for every test in the session. Skipped if Docker / the
    container client isn't available.
    """
    try:
        from testcontainers.minio import MinioContainer  # lazy import
    except Exception as exc:  # pragma: no cover
        pytest.skip(f"testcontainers MinIO unavailable: {exc}")

    try:
        container = MinioContainer()
        container.start()
    except Exception as exc:  # pragma: no cover
        pytest.skip(f"could not start MinIO container (Docker missing?): {exc}")

    try:
        host = container.get_container_host_ip()
        port = container.get_exposed_port(9000)
        endpoint = f"http://{host}:{port}"
        access_key = container.access_key
        secret_key = container.secret_key
        yield (endpoint, access_key, secret_key)
    finally:
        with contextlib.suppress(Exception):  # pragma: no cover
            container.stop()


# Builder callable shape exposed by ``app_context_factory``.
AppContextBuilder = Callable[..., AppContext]


@pytest.fixture
def app_context_factory() -> AppContextBuilder:
    """Return a callable that builds a wired :class:`AppContext` for
    integration tests.

    Usage::

        async def test_thing(app_context_factory):
            fs = await _seed_fs()
            ctx = app_context_factory(fs=fs)
            app = AwsTuiApp(ctx)
            async with app.run_test(size=(120, 40)) as pilot:
                ...

    Parameters:
        fs: pre-seeded :class:`FileSystemProvider` used as the S3
            service's provider via ``s3_fs_factory``. Defaults to a
            fresh empty :class:`InMemoryFS`. The same provider is
            returned for every connection — tests typically only need
            one.
        initial_theme: theme name set on the returned context.
            Defaults to ``"carbon"``.
    """

    def _build(
        *,
        fs: FileSystemProvider | None = None,
        initial_theme: str = "carbon",
    ) -> AppContext:
        tmp = Path(tempfile.mkdtemp(prefix="aws-tui-ictx-"))
        hub: MessageHub = MessageHub()
        dispatcher = RxDispatcher.immediate()

        log = LogSink(base_dir=tmp / "log")
        config_store = ConfigStore(path=tmp / "config.toml")
        keymap = KeymapStore()
        theme = ThemeStore()
        aws_session = AwsSession()
        journal = TransferJournal(base_dir=tmp / "transfers")
        resolver = ConnectionResolver(
            config_store=config_store,
            aws_config_path=tmp / "aws-config",
            aws_credentials_path=tmp / "aws-credentials",
        )

        provider = fs if fs is not None else InMemoryFS()

        def _factory(_conn: Connection) -> FileSystemProvider:
            return provider

        svc = S3Service(
            aws_session=aws_session,
            transfer_journal=journal,
            hub=hub,
            dispatcher=dispatcher,
            s3_fs_factory=_factory,
        )
        svc._local_root = tmp  # type: ignore[attr-defined]

        registry = ServiceRegistry()
        registry.register(cast(Service, svc))

        root = RootVM(
            registry=registry,
            keymap=keymap,
            theme=theme,
            log=log,
            dispatcher=dispatcher,
            hub=hub,
        )

        # Seed a single default connection so AwsTuiApp.on_mount can
        # resolve one and the panes mount entries.
        config_store.path.write_text(
            '[defaults]\nconnection = "test"\n\n'
            '[connections.test]\nkind = "aws"\nprofile = "test"\nregion = "us-east-1"\n'
        )

        s3_connections_vm = S3ConnectionsVM(
            resolver=resolver,
            config_store=config_store,
            hub=hub,
            dispatcher=dispatcher,
        )

        return AppContext(
            root_vm=root,
            registry=registry,
            config_store=config_store,
            log_sink=log,
            keymap_store=keymap,
            theme_store=theme,
            connection_resolver=resolver,
            aws_session=aws_session,
            transfers_vm=TransfersVM(hub=hub, dispatcher=dispatcher),
            confirm_vm=ConfirmationVM(hub=hub, dispatcher=dispatcher),
            quick_look_vm=QuickLookVM(hub=hub, dispatcher=dispatcher),
            command_palette_vm=CommandPaletteVM(hub=hub, dispatcher=dispatcher),
            transfer_journal=journal,
            hub=hub,
            dispatcher=dispatcher,
            initial_theme=initial_theme,
            s3_connections_vm=s3_connections_vm,
        )

    return _build
