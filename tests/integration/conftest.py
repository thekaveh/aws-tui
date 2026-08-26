"""Integration-tier shared fixtures.

- :func:`s3_compat_endpoint` — session-scoped Adobe S3Mock container.
  Used by the two real S3-protocol suites (opt-in via ``-m integration``).
- :func:`app_context_factory` — per-test fixture returning a builder
  callable that constructs an :class:`AppContext` for full-app pilot
  tests. Pass ``fs=`` to inject a seeded :class:`InMemoryFS` as the
  S3 service's provider; defaults to an empty in-memory FS.
  Consolidates the boilerplate that would otherwise be duplicated
  across every integration test that needs a real ``AppContext``.
"""

from __future__ import annotations

import contextlib
import os
import shutil
import tempfile
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any, cast

import pytest
from vmx import MessageHub, RxDispatcher

from aws_tui.composition import AppContext
from aws_tui.demo.in_memory_fs import InMemoryFS
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

_S3MOCK_IMAGE = (
    "adobe/s3mock:5.1.0@sha256:65cf60155a2e235fe7d5bf6c633747d6fc7ed93f9f5a6727d86470026b83c2a2"
)

_AWS_CREDENTIAL_ENV_VARS = (
    "AWS_ACCESS_KEY_ID",
    "AWS_SECRET_ACCESS_KEY",
    "AWS_SESSION_TOKEN",
    "AWS_SECURITY_TOKEN",
    "AWS_PROFILE",
    "AWS_DEFAULT_PROFILE",
    "AWS_ROLE_ARN",
    "AWS_WEB_IDENTITY_TOKEN_FILE",
    "AWS_ROLE_SESSION_NAME",
    "AWS_CONTAINER_CREDENTIALS_RELATIVE_URI",
    "AWS_CONTAINER_CREDENTIALS_FULL_URI",
    "AWS_CONTAINER_AUTHORIZATION_TOKEN",
    "AWS_CONTAINER_AUTHORIZATION_TOKEN_FILE",
    "AWS_CREDENTIAL_FILE",
)


@pytest.fixture(autouse=True)
def _isolated_aws_files(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep integration tests independent of host credential providers."""
    for variable in _AWS_CREDENTIAL_ENV_VARS:
        monkeypatch.delenv(variable, raising=False)
    monkeypatch.setenv("AWS_EC2_METADATA_DISABLED", "true")

    for variable, filename in (
        ("AWS_CONFIG_FILE", "aws-config"),
        ("AWS_SHARED_CREDENTIALS_FILE", "aws-credentials"),
        ("BOTO_CONFIG", "boto-config"),
    ):
        path = tmp_path / filename
        path.write_text("", encoding="utf-8")
        monkeypatch.setenv(variable, str(path))


def _s3_compat_unavailable(message: str) -> None:
    """Fail required CI coverage while keeping local Docker tests optional."""
    if os.environ.get("CI"):
        pytest.fail(message)
    pytest.skip(message)


def _start_s3mock_or_unavailable(container: Any) -> None:
    """Start S3Mock or tear down any partially allocated container."""
    try:
        container.start()
    except Exception as exc:  # pragma: no cover - exercised through helper tests
        with contextlib.suppress(Exception):
            container.stop()
        _s3_compat_unavailable(f"could not start S3Mock container (Docker missing?): {exc}")


@pytest.fixture(scope="session")
def s3_compat_endpoint() -> Iterator[tuple[str, str, str]]:
    """Spin up a real Adobe S3Mock container.

    Returns ``(endpoint_url, access_key, secret_key)``. The container is
    reused for every test in the session. Skipped if Docker / the
    container client isn't available.
    """
    try:
        from testcontainers.core.container import DockerContainer
        from testcontainers.core.wait_strategies import HttpWaitStrategy
    except Exception as exc:  # pragma: no cover
        _s3_compat_unavailable(f"testcontainers S3Mock unavailable: {exc}")

    try:
        container = (
            DockerContainer(_S3MOCK_IMAGE)
            .with_exposed_ports(9090)
            .waiting_for(HttpWaitStrategy(9090, "/favicon.ico"))
        )
    except Exception as exc:  # pragma: no cover
        _s3_compat_unavailable(f"could not construct S3Mock container: {exc}")
    _start_s3mock_or_unavailable(container)

    try:
        host = container.get_container_host_ip()
        port = container.get_exposed_port(9090)
        endpoint = f"http://{host}:{port}"
        yield (endpoint, "test", "test")
    finally:
        with contextlib.suppress(Exception):  # pragma: no cover
            container.stop()


# Builder callable shape exposed by ``app_context_factory``.
AppContextBuilder = Callable[..., AppContext]


@pytest.fixture
def app_context_factory() -> Iterator[AppContextBuilder]:
    """Yield a callable that builds a wired :class:`AppContext` for
    integration tests, then clean up every temp directory the builder
    created when the test finishes (pass or fail).

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
    # Track every ``tempfile.mkdtemp`` the builder produces so the
    # fixture's teardown can purge them. Previously the builder
    # leaked one directory per call on every test path — the fixture
    # was a plain ``def`` returning a callable with no teardown hook,
    # so a raise inside the test stranded the dir under ``$TMPDIR``.
    created_tmpdirs: list[Path] = []

    def _build(
        *,
        fs: FileSystemProvider | None = None,
        initial_theme: str = "carbon",
    ) -> AppContext:
        tmp = Path(tempfile.mkdtemp(prefix="aws-tui-ictx-"))
        created_tmpdirs.append(tmp)
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
            transfer_journal=journal,
            hub=hub,
            dispatcher=dispatcher,
            local_root=tmp,
            s3_fs_factory=_factory,
        )

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

    try:
        yield _build
    finally:
        for tmp in created_tmpdirs:
            # ignore_errors=True so a partially-cleaned dir (e.g. a
            # background worker still holding a file open at teardown
            # time on Windows) doesn't fail the whole test.
            shutil.rmtree(tmp, ignore_errors=True)
