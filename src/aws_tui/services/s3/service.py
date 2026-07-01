"""S3Service — first concrete :class:`Service` implementation.

Composes a :class:`DualPaneVM` whose left pane is an :class:`S3FS` over
the active connection (AWS or S3-compatible) and whose right pane is a
:class:`LocalFS` rooted at the user's working directory. The service is
agnostic to where the dispatcher / message hub came from — it consumes
them via :meth:`build_vm` and stitches the rest itself.

Construction strategy: the service holds the long-lived
``TransferJournal`` and ``MessageHub`` references. ``build_vm`` is called
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
from aws_tui.infra.connection_resolver import Connection
from aws_tui.infra.redaction import safe_endpoint_display
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
            aws_session_token=connection.session_token,
            region_name=connection.region,
        )
    raise ValueError(f"unsupported connection kind: {connection.kind!r}")


class S3Service:
    """``Service``-protocol implementation for AWS S3 and S3-compatible.

    Parameters
    ----------
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
        # Bucket emoji — thematic for S3 (the service is "Simple
        # Storage Service" and buckets are its primary abstraction).
        # U+1FAA3 BUCKET — true emoji codepoint, renders as a colored
        # picture in any terminal with a modern emoji font (Apple
        # Color Emoji on macOS, Noto Color Emoji on Linux, Segoe UI
        # Emoji on Windows). The previous ``☁`` (U+2601 CLOUD)
        # without a variation selector rendered as the text-style
        # outline, often as a tiny grey line-art glyph — not what
        # the user asked for.
        icon="🪣",
    )

    def __init__(
        self,
        *,
        transfer_journal: TransferJournal,
        hub: MessageHub[Message] | None = None,
        dispatcher: Dispatcher,
        local_root: Path | None = None,
        s3_fs_factory: S3FsFactory | None = None,
    ) -> None:
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

        Precondition: ``bind_hub`` has been called. Composition wires it
        immediately after :class:`RootVM` construction, so by the time
        any service-switch command can fire (the only path to
        ``build_vm``), the hub is always present. The runtime check
        below is a wiring-bug assertion for the test surface, not a
        user-reachable error path — hence ``RuntimeError`` rather than
        a typed :class:`ConfigError` / :class:`ConnectionNotFound`,
        which the user-facing flows already raise when *they* are the
        broken precondition.
        """
        if self._hub is None:
            raise RuntimeError("S3Service.build_vm called before bind_hub — composition wiring bug")
        hub = self._hub
        s3_provider = self._make_s3_provider(connection)
        local_provider = LocalFS(root=self._local_root) if self._local_root else LocalFS()

        left = PaneVM(
            provider=s3_provider,
            hub=hub,
            dispatcher=self._dispatcher,
            id_prefix="pane.s3",
            identity_label=_format_pane_title(connection),
            path_protocol="s3:",
            connection_key=(connection.kind, connection.name),
        )
        right = PaneVM(
            provider=local_provider,
            hub=hub,
            dispatcher=self._dispatcher,
            id_prefix="pane.local",
            identity_label="local",
            path_protocol="",
            connection_key=None,
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
            verify_tls=connection.verify_tls,
        )


def _strip_scheme(url: str | None) -> str | None:
    """Strip ``http://`` / ``https://`` so the endpoint reads cleanly in
    the pane's border subtitle (``localhost:64093`` instead of
    ``http://localhost:64093``), while also dropping URL userinfo,
    query strings, and fragments from user-visible labels."""
    return safe_endpoint_display(url)


def _format_pane_title(connection: Connection) -> str:
    """Build the connection-identity string rendered in the pane's
    bottom border subtitle.

    Format depends on the connection ``kind``:

    - ``aws``            → ``aws s3 · {profile} · {region}``
    - ``s3-compatible``  → ``s3-compatible · {name} · {endpoint}``

    For ``s3-compatible`` the ``region`` is intentionally *not* shown —
    MinIO and friends don't have a meaningful region, and surfacing the
    internal default (``us-east-1`` for SigV4) is misleading. The
    user-defined connection ``name`` and ``endpoint_url`` carry the
    actual identity. The endpoint's ``http(s)://`` scheme is stripped
    for compactness."""
    if connection.kind == "aws":
        parts: list[str] = ["aws s3"]
        if connection.profile:
            parts.append(connection.profile)
        if connection.region:
            parts.append(connection.region)
        return " · ".join(parts)
    if connection.kind == "s3-compatible":
        parts = ["s3-compatible", connection.name]
        endpoint = _strip_scheme(connection.endpoint_url)
        if endpoint:
            parts.append(endpoint)
        return " · ".join(parts)
    # Fallback for any future kind — keep the old shape.
    parts = [connection.kind]
    if connection.profile:
        parts.append(connection.profile)
    if connection.region:
        parts.append(connection.region)
    return " · ".join(parts)


__all__ = ["S3FsFactory", "S3Service"]
