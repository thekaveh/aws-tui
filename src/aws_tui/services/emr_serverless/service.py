"""EmrServerlessService — second concrete :class:`Service` implementation.

PR-A ships the read-only browser. PR-B adds log surface + cancel +
lifecycle. PR-C adds submit. Each PR extends this service's
``build_vm`` return shape additively — no breaking changes
between PRs because :class:`EmrServerlessPageVM` always remains the
hosted root VM.

Construction strategy mirrors :class:`S3Service`: the service holds
the long-lived :class:`MessageHub` and :class:`Dispatcher` and
builds a fresh ``EmrServerlessPageVM`` per ``build_vm`` call.
:class:`ContentHostVM` disposes the previous page VM on swap; never
host this VM as a singleton (see [[vmx-content-host-singleton-trap]])."""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any, ClassVar

import aioboto3
from vmx import Message, MessageHub
from vmx.services.dispatcher import Dispatcher

from aws_tui.domain.emr_serverless import EmrServerlessClient
from aws_tui.infra.connection_resolver import Connection
from aws_tui.vm.services_protocol import ServiceDescriptor

if TYPE_CHECKING:
    from aws_tui.vm.emr_serverless.page_vm import EmrServerlessPageVM


#: Test hook — when provided, replaces real ``EmrServerlessClient`` construction
#: with whatever the factory returns (typically ``_InMemoryEmr``).
EmrClientFactory = Callable[[Connection], Any]


class EmrServerlessService:
    """``Service``-protocol implementation for EMR Serverless.

    Parameters
    ----------
    hub:
        Top-level :class:`MessageHub`. Sub-VMs publish state-change
        notifications on it.
    dispatcher:
        VMx dispatcher used for sub-VMs.
    emr_client_factory:
        Test hook — when supplied, ``build_vm`` calls this instead
        of constructing a real :class:`EmrServerlessClient`. Lets
        integration tests inject :class:`_InMemoryEmr`.
    """

    descriptor: ClassVar[ServiceDescriptor] = ServiceDescriptor(
        id="emr-serverless",
        label="EMR",
        # 🔥 U+1F525 FIRE — SMP single-codepoint emoji with
        # Default_Emoji_Presentation. PR #77 first tried ⚡ U+26A1
        # then ⚡️ U+26A1 + U+FE0F VS-16, but BOTH render
        # unreliably in monospace terminals — even with VS-16 the
        # codepoint frequently falls back to a 1-cell text-style
        # outline because the colour glyph isn't in the user's font
        # fallback chain. The 1-cell width then mis-aligns against
        # the nav-rail's 2-cell emoji column (see ``nav_menu.py``
        # ``_format_collapsed_prompt``), garbling the whole row.
        # SMP single-codepoint emojis (U+1F***) avoid this
        # entirely: every monospace font with emoji support ships
        # them as 2-cell colour glyphs because there's no text
        # presentation alternative. 🔥 is semantically apt for
        # Spark (the framework Apache Spark literally calls its
        # primitive a "spark"; "fire/spark" share the same
        # conceptual root), reliably renders 2-cell colour in
        # every modern terminal, and visually distinct from 🪣
        # bucket / ⚙️ gear / 🖥️ computer.
        #
        # General rule for nav-rail icons going forward: pick from
        # the SMP emoji blocks (U+1F***), NOT BMP-with-VS-16.
        icon="🔥",
    )

    def __init__(
        self,
        *,
        hub: MessageHub[Message],
        dispatcher: Dispatcher,
        emr_client_factory: EmrClientFactory | None = None,
    ) -> None:
        self._hub: MessageHub[Message] = hub
        self._dispatcher: Dispatcher = dispatcher
        self._client_factory: EmrClientFactory | None = emr_client_factory

    # ── Service protocol ────────────────────────────────────────────────────

    def supports(self, connection: Connection) -> bool:
        """EMR Serverless is AWS-only — s3-compatible connections
        never see the ⚡ icon in the nav rail (the NavMenuVM filter
        consults this)."""
        return connection.kind == "aws"

    def build_vm(self, connection: Connection) -> "EmrServerlessPageVM":  # noqa: UP037
        """Build a fresh page VM for ``connection``.

        Lazy-imported because :mod:`aws_tui.vm.emr_serverless.page_vm`
        depends on this module's :class:`ServiceDescriptor`; an eager
        import would cycle."""
        from aws_tui.vm.emr_serverless.page_vm import EmrServerlessPageVM

        client = self._make_client(connection)
        return EmrServerlessPageVM(
            client=client,
            hub=self._hub,
            dispatcher=self._dispatcher,
            connection=connection,
        )

    # ── Internal ────────────────────────────────────────────────────────────

    def _make_client(self, connection: Connection) -> Any:
        if self._client_factory is not None:
            return self._client_factory(connection)
        session = aioboto3.Session(
            profile_name=connection.profile,
            region_name=connection.region,
        )
        return EmrServerlessClient(session=session, region_name=connection.region)


__all__ = ["EmrClientFactory", "EmrServerlessService"]
