"""S3Service — first concrete :class:`Service` implementation.

Composes a :class:`DualPaneVM` whose left pane is an :class:`S3FS` over
the active connection (AWS or S3-compatible) and whose right pane is a
:class:`LocalFS` rooted at the user's working directory. The service is
agnostic to where the dispatcher / message hub came from — it consumes
them via :meth:`build_vm` and stitches the rest itself.

Construction strategy: the service holds the long-lived ``AwsSession``,
``TransferJournal``, and ``MessageHub`` references. ``build_vm`` is called
by :class:`RootVM` after every connection switch and produces a *fresh*
``DualPaneVM`` for that connection. The caller is responsible for
disposing the previous DualPaneVM via the :class:`ContentHostVM` swap.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import ClassVar

import aioboto3
from vmx import Message, MessageHub
from vmx.services.dispatcher import Dispatcher

from aws_tui.domain.filesystem import FileSystemProvider
from aws_tui.domain.local_fs import LocalFS
from aws_tui.domain.s3_fs import S3FS
from aws_tui.domain.transfer_journal import TransferJournal
from aws_tui.infra.aws_session import AwsSession
from aws_tui.infra.connection_resolver import Connection
from aws_tui.vm.file_manager.dual_pane_vm import DualPaneVM
from aws_tui.vm.file_manager.pane_vm import PaneVM
from aws_tui.vm.services_protocol import ServiceDescriptor

#: Test hook — when provided, replaces the real ``S3FS`` construction.
S3FsFactory = Callable[[Connection], FileSystemProvider]


def _aioboto3_session_for(connection: Connection) -> aioboto3.Session:
    """Build the ``aioboto3.Session`` matching the connection kind.

    Mirrors :meth:`AwsSession.client` but returns the raw session so
    :class:`S3FS` (which expects an ``aioboto3.Session`` directly) can
    open its own clients.
    """
    if connection.kind == "aws":
        return aioboto3.Session(
            profile_name=connection.profile,
            region_name=connection.region,
        )
    if connection.kind == "s3-compatible":
        return aioboto3.Session(
            aws_access_key_id=connection.access_key_id,
            aws_secret_access_key=connection.secret_access_key,
            region_name=connection.region,
        )
    raise ValueError(f"unsupported connection kind: {connection.kind!r}")


class S3Service:
    """``Service``-protocol implementation for AWS S3 and S3-compatible.

    Parameters
    ----------
    aws_session:
        The session factory the higher layer owns (kept for parity with
        future services that need richer aioboto3 client management; the
        S3Service itself doesn't use it for the pane since :class:`S3FS`
        manages its own clients).
    transfer_journal:
        Shared journal — every DualPaneVM the service builds will route
        copy/move bookkeeping through it.
    hub:
        Top-level :class:`MessageHub`. ``DualPaneVM`` publishes
        :class:`TransferProgressMessage` on it; ``TransfersVM``
        (registered separately) subscribes.
    dispatcher:
        VMx dispatcher used for built sub-VMs.
    local_root:
        Filesystem root of the local pane (defaults to the process CWD).
    s3_fs_factory:
        Test hook — when supplied, ``build_vm`` calls this instead of
        constructing a real :class:`S3FS`. Mirrors the ``InMemoryFS`` tier
        used by integration tests so we never reach for boto in unit runs.
    """

    descriptor: ClassVar[ServiceDescriptor] = ServiceDescriptor(
        id="s3",
        label="S3",
        icon="S3",
    )

    def __init__(
        self,
        *,
        aws_session: AwsSession,
        transfer_journal: TransferJournal,
        hub: MessageHub[Message] | None = None,
        dispatcher: Dispatcher,
        local_root: Path | None = None,
        s3_fs_factory: S3FsFactory | None = None,
    ) -> None:
        self._aws_session: AwsSession = aws_session
        self._journal: TransferJournal = transfer_journal
        self._hub: MessageHub[Message] | None = hub
        self._dispatcher: Dispatcher = dispatcher
        self._local_root: Path | None = local_root
        self._s3_fs_factory: S3FsFactory | None = s3_fs_factory

    def bind_hub(self, hub: MessageHub[Message]) -> None:
        """Late-wire the hub (used when the service is registered before
        :class:`RootVM` has constructed its hub).
        """
        self._hub = hub

    # ── Service protocol ────────────────────────────────────────────────────

    def supports(self, connection: Connection) -> bool:
        """S3Service works for both ``aws`` and ``s3-compatible`` connections."""
        return connection.kind in {"aws", "s3-compatible"}

    def build_vm(self, connection: Connection) -> DualPaneVM:
        """Compose a :class:`DualPaneVM` for ``connection``.

        Returned VM is *not* constructed — :class:`ContentHostVM` calls
        ``construct()`` after the swap; callers needing to populate
        listings must subsequently ``await dual.setup()``.
        """
        if self._hub is None:
            raise RuntimeError(
                "S3Service.build_vm called before bind_hub — wire the RootVM hub first"
            )
        hub = self._hub
        s3_provider = self._make_s3_provider(connection)
        local_provider = LocalFS(root=self._local_root) if self._local_root else LocalFS()

        left = PaneVM(
            provider=s3_provider,
            hub=hub,
            dispatcher=self._dispatcher,
            id_prefix="pane.s3",
        )
        right = PaneVM(
            provider=local_provider,
            hub=hub,
            dispatcher=self._dispatcher,
            id_prefix="pane.local",
        )
        return DualPaneVM(
            left=left,
            right=right,
            hub=hub,
            dispatcher=self._dispatcher,
            transfer_journal=self._journal,
        )

    # ── Internal ────────────────────────────────────────────────────────────

    def _make_s3_provider(self, connection: Connection) -> FileSystemProvider:
        """Pull from the test factory, or build a fresh :class:`S3FS`."""
        if self._s3_fs_factory is not None:
            return self._s3_fs_factory(connection)
        session = _aioboto3_session_for(connection)
        return S3FS(
            session=session,
            bucket=None,
            endpoint_url=connection.endpoint_url,
            force_path_style=connection.force_path_style,
        )


__all__ = ["S3FsFactory", "S3Service"]
