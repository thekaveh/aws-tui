"""AWS Glue service composition."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import ClassVar, Protocol

from vmx import Message, MessageHub
from vmx.services.dispatcher import Dispatcher

from aws_tui.domain.data_catalog import (
    ColumnStatistics,
    DatabaseSummary,
    PartitionSummary,
    TableDetail,
    TableRef,
    TableSummary,
)
from aws_tui.domain.glue import (
    GlueClient,
    GlueCrawlerDetail,
    GlueCrawlerMetrics,
    GlueCrawlerSummary,
    GlueJobRunSummary,
    GlueJobSummary,
)
from aws_tui.infra.aws_session import AwsSession
from aws_tui.infra.connection_resolver import Connection
from aws_tui.vm.glue.page_vm import GluePageVM
from aws_tui.vm.service_source_vm import ServiceSelectionStore
from aws_tui.vm.services_protocol import ServiceDescriptor


class GlueClientProtocol(Protocol):
    async def list_databases_page(
        self,
        *,
        start_token: str | None = None,
    ) -> tuple[list[DatabaseSummary], str | None]: ...

    async def list_tables_page(
        self,
        database: str,
        *,
        start_token: str | None = None,
    ) -> tuple[list[TableSummary], str | None]: ...

    async def get_table(self, ref: TableRef) -> TableDetail: ...

    async def list_partitions_page(
        self,
        ref: TableRef,
        *,
        start_token: str | None = None,
    ) -> tuple[list[PartitionSummary], str | None]: ...

    async def get_column_statistics(
        self,
        ref: TableRef,
        columns: Sequence[str],
    ) -> tuple[ColumnStatistics, ...]: ...

    async def list_jobs_page(
        self,
        *,
        start_token: str | None = None,
    ) -> tuple[list[GlueJobSummary], str | None]: ...

    async def list_job_runs_page(
        self,
        job_name: str,
        *,
        start_token: str | None = None,
        states: Sequence[str] = (),
    ) -> tuple[list[GlueJobRunSummary], str | None]: ...

    async def list_crawlers_page(
        self,
        *,
        start_token: str | None = None,
        state: str | None = None,
    ) -> tuple[list[GlueCrawlerSummary], str | None]: ...

    async def get_crawler(self, name: str) -> GlueCrawlerDetail: ...

    async def get_crawler_metrics(self, name: str) -> GlueCrawlerMetrics | None: ...


GlueClientFactory = Callable[[Connection], GlueClientProtocol]


class GlueService:
    descriptor: ClassVar[ServiceDescriptor] = ServiceDescriptor(
        id="glue",
        label="Glue",
        icon="🔗",
    )

    def __init__(
        self,
        *,
        hub: MessageHub[Message],
        dispatcher: Dispatcher,
        aws_session: AwsSession,
        glue_client_factory: GlueClientFactory | None = None,
    ) -> None:
        self._hub = hub
        self._dispatcher = dispatcher
        self._aws_session = aws_session
        self._client_factory = glue_client_factory
        self._selections = ServiceSelectionStore()

    def supports(self, connection: Connection) -> bool:
        return connection.kind == "aws"

    def build_vm(self, connection: Connection) -> GluePageVM:
        client: GlueClientProtocol = (
            self._client_factory(connection)
            if self._client_factory is not None
            else GlueClient(aws_session=self._aws_session, connection=connection)
        )
        return GluePageVM(
            client=client,
            connection=connection,
            selection_store=self._selections,
            hub=self._hub,
            dispatcher=self._dispatcher,
        )


__all__ = ["GlueClientFactory", "GlueClientProtocol", "GlueService"]
