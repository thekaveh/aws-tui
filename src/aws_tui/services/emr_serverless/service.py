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

from collections.abc import AsyncIterator, Callable
from typing import TYPE_CHECKING, Any, ClassVar, cast

import aioboto3
from vmx import Message, MessageHub
from vmx.services.dispatcher import Dispatcher

from aws_tui.domain.emr_logs import EmrServerlessLogsClient, LogChunk, LogFile, LogFilter
from aws_tui.domain.emr_serverless import (
    EMR_BOTO_CONFIG,
    EmrServerlessClient,
    JobRunState,
    JobRunSummary,
    map_boto_error,
)
from aws_tui.domain.filesystem import ProviderError
from aws_tui.infra.connection_resolver import Connection
from aws_tui.vm.services_protocol import ServiceDescriptor

if TYPE_CHECKING:
    from aws_tui.vm.emr_serverless.page_vm import EmrServerlessPageVM


#: Test hook — when provided, replaces real ``EmrServerlessClient`` construction
#: with whatever the factory returns (typically ``_InMemoryEmr``).
EmrClientFactory = Callable[[Connection], Any]
EmrLogsClientFactory = Callable[[Connection], EmrServerlessLogsClient]


def _map_session_construction_error(exc: BaseException) -> ProviderError:
    mapped = map_boto_error(exc)
    if mapped is not None:
        return mapped
    return ProviderError(str(exc) or type(exc).__name__)


class _FailedEmrLogsClient:
    def __init__(self, error: ProviderError) -> None:
        self._error = error

    async def list_files(self, *, bucket: str, run_prefix: str) -> list[LogFile]:
        raise self._error

    async def stream(
        self,
        *,
        log_file: LogFile,
        bucket: str,
        max_bytes: int,
        filter_: LogFilter,
    ) -> AsyncIterator[LogChunk]:
        raise self._error
        yield  # pragma: no cover


class _FailedEmrClient:
    def __init__(self, error: ProviderError) -> None:
        self._error = error

    async def list_applications(self) -> list[object]:
        raise self._error

    async def list_job_runs_page(
        self,
        application_id: str,
        *,
        start_token: str | None = None,
        states: set[JobRunState] | None = None,
    ) -> tuple[list[JobRunSummary], str | None]:
        raise self._error

    async def list_job_runs(
        self,
        application_id: str,
        *,
        states: set[JobRunState] | None = None,
        max_results: int = 100,
    ) -> list[JobRunSummary]:
        raise self._error

    async def get_job_run(self, application_id: str, job_run_id: str) -> object:
        raise self._error

    async def start_job_run(
        self,
        application_id: str,
        *,
        execution_role_arn: str,
        entry_point: str,
        entry_point_arguments: tuple[str, ...],
        spark_submit_parameters: str | None,
        name: str | None = None,
    ) -> str:
        raise self._error

    def make_logs_client(self) -> _FailedEmrLogsClient:
        return _FailedEmrLogsClient(self._error)


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
    emr_logs_client_factory:
        Test hook for the S3-backed EMR logs facade. Production code
        builds this from the connection directly so the page VM never
        reaches into private attributes on the EMR Serverless client.
    """

    descriptor: ClassVar[ServiceDescriptor] = ServiceDescriptor(
        id="emr-serverless",
        label="EMR",
        # 🔥 U+1F525 FIRE — SMP single-codepoint, renders 2-cell
        # colour reliably and draws to the full bounding box (the
        # 💥 COLLISION glyph that briefly shipped in PR #83 drew
        # to a tighter box and read as smaller than the 🪣 nav
        # peer; user feedback). Fifth icon attempt:
        #   PR #77 ⚡  (BMP U+26A1)         → 1-cell outline, broke layout
        #   PR #79 🔥  (SMP U+1F525)        → 2-cell colour, worked
        #   PR #81 ⚡️ (BMP U+26A1+VS-16)   → broke layout again
        #   PR #83 💥 (SMP U+1F4A5)        → small bounding box vs 🪣
        #         🔥  (SMP U+1F525)        → here, back to the known good
        # Semantically apt for Spark (the framework). Future icon
        # contract: pick from the SMP block (U+1F***) and prefer
        # glyphs that draw to the full bounding box; see
        # nav_menu.py for the 2-cell layout invariant.
        icon="🔥",
    )

    def __init__(
        self,
        *,
        hub: MessageHub[Message],
        dispatcher: Dispatcher,
        emr_client_factory: EmrClientFactory | None = None,
        emr_logs_client_factory: EmrLogsClientFactory | None = None,
    ) -> None:
        self._hub: MessageHub[Message] = hub
        self._dispatcher: Dispatcher = dispatcher
        self._client_factory: EmrClientFactory | None = emr_client_factory
        self._logs_client_factory: EmrLogsClientFactory | None = emr_logs_client_factory

    # ── Service protocol ────────────────────────────────────────────────────

    def supports(self, connection: Connection) -> bool:
        """EMR Serverless is AWS-only — s3-compatible connections
        never see the EMR nav row because the NavMenuVM filter consults
        this predicate."""
        return connection.kind == "aws"

    def build_vm(self, connection: Connection) -> "EmrServerlessPageVM":  # noqa: UP037
        """Build a fresh page VM for ``connection``.

        Lazy-imported because :mod:`aws_tui.vm.emr_serverless.page_vm`
        depends on this module's :class:`ServiceDescriptor`; an eager
        import would cycle."""
        from aws_tui.vm.emr_serverless.page_vm import EmrServerlessPageVM

        client = self._make_client(connection)
        logs_client = self._make_logs_client(connection, client=client)
        return EmrServerlessPageVM(
            client=client,
            logs_client=logs_client,
            hub=self._hub,
            dispatcher=self._dispatcher,
            connection=connection,
        )

    # ── Internal ────────────────────────────────────────────────────────────

    def _make_client(self, connection: Connection) -> Any:
        if self._client_factory is not None:
            return self._client_factory(connection)
        try:
            session = aioboto3.Session(
                profile_name=connection.profile,
                region_name=connection.region,
            )
        except Exception as exc:
            return _FailedEmrClient(_map_session_construction_error(exc))
        return EmrServerlessClient(session=session, region_name=connection.region)

    def _make_logs_client(
        self, connection: Connection, *, client: Any | None = None
    ) -> EmrServerlessLogsClient:
        if self._logs_client_factory is not None:
            return self._logs_client_factory(connection)
        make_logs_client = getattr(client, "make_logs_client", None)
        if callable(make_logs_client):
            return cast("EmrServerlessLogsClient", make_logs_client())
        try:
            session = aioboto3.Session(
                profile_name=connection.profile,
                region_name=connection.region,
            )
        except Exception as exc:
            return cast(
                "EmrServerlessLogsClient",
                _FailedEmrLogsClient(_map_session_construction_error(exc)),
            )
        return EmrServerlessLogsClient(
            session=session,
            region_name=connection.region,
            boto_config=EMR_BOTO_CONFIG,
        )


__all__ = ["EmrClientFactory", "EmrLogsClientFactory", "EmrServerlessService"]
