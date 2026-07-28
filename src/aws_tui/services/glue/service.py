"""AWS Glue service composition."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Sequence
from datetime import datetime
from typing import Any, ClassVar, Protocol, cast

from vmx import Message, MessageHub
from vmx.services.dispatcher import Dispatcher

from aws_tui.domain.athena import AthenaClient, AthenaWorkgroupSummary
from aws_tui.domain.athena_runner import AthenaQueryRunner
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
from aws_tui.domain.iceberg import (
    IcebergDataFile,
    IcebergHistoryEntry,
    IcebergInspector,
    IcebergManifest,
    IcebergPartition,
    IcebergReference,
    IcebergSnapshot,
)
from aws_tui.domain.query import QueryContext
from aws_tui.domain.sql_policy import ReadOnlySqlPolicy
from aws_tui.infra.aws_session import AwsSession
from aws_tui.infra.connection_resolver import Connection
from aws_tui.vm.glue.iceberg_vm import IcebergInspectionUnavailableError
from aws_tui.vm.glue.page_vm import GluePageVM
from aws_tui.vm.service_source_vm import SelectionScope, ServiceSelectionStore
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
AthenaClientFactory = Callable[[Connection], Any]
_WORKGROUP_PAGE_LIMIT = 64
_WORKGROUP_EMPTY_PAGE_LIMIT = 3


class _ContextualIcebergInspector:
    """Resolve one profile-local workgroup and bind each Glue table context."""

    def __init__(
        self,
        *,
        client: Any,
        connection: Connection,
        selections: ServiceSelectionStore,
    ) -> None:
        self._client = client
        self._connection = connection
        self._selections = selections
        self._scope = SelectionScope("athena", connection.name, connection.region)
        self._runner = AthenaQueryRunner(client, ReadOnlySqlPolicy())
        self._resolved_workgroup: str | None = None
        self._resolve_lock = asyncio.Lock()

    async def list_snapshots(self, table_ref: TableRef) -> tuple[IcebergSnapshot, ...]:
        return await (await self._inspector(table_ref)).list_snapshots(table_ref)

    async def list_history(self, table_ref: TableRef) -> tuple[IcebergHistoryEntry, ...]:
        return await (await self._inspector(table_ref)).list_history(table_ref)

    async def list_manifests(self, table_ref: TableRef) -> tuple[IcebergManifest, ...]:
        return await (await self._inspector(table_ref)).list_manifests(table_ref)

    async def list_files(self, table_ref: TableRef) -> tuple[IcebergDataFile, ...]:
        return await (await self._inspector(table_ref)).list_files(table_ref)

    async def list_partitions(self, table_ref: TableRef) -> tuple[IcebergPartition, ...]:
        return await (await self._inspector(table_ref)).list_partitions(table_ref)

    async def list_refs(self, table_ref: TableRef) -> tuple[IcebergReference, ...]:
        return await (await self._inspector(table_ref)).list_refs(table_ref)

    async def _inspector(self, table_ref: TableRef) -> IcebergInspector:
        if (
            table_ref.connection_name != self._connection.name
            or table_ref.region != self._connection.region
        ):
            raise IcebergInspectionUnavailableError("Iceberg table context is invalid")
        workgroup = await self._workgroup()
        return IcebergInspector(
            runner=self._runner,
            context=QueryContext(
                connection_name=self._connection.name,
                region=self._connection.region,
                workgroup=workgroup,
                catalog=table_ref.catalog_name,
                database=table_ref.database_name,
            ),
        )

    async def _workgroup(self) -> str:
        if self._resolved_workgroup is not None:
            return self._resolved_workgroup
        async with self._resolve_lock:
            if self._resolved_workgroup is not None:
                return self._resolved_workgroup
            workgroup = await self._resolve_available_workgroup()
            self._resolved_workgroup = workgroup
            self._selections.set(self._scope, "workgroup", workgroup)
            return workgroup

    async def _resolve_available_workgroup(self) -> str:
        token: str | None = None
        seen_tokens: set[str] = set()
        empty_pages = 0
        all_rows: list[AthenaWorkgroupSummary] = []
        complete = False
        for _request in range(_WORKGROUP_PAGE_LIMIT):
            rows, next_token = await self._client.list_workgroups_page(start_token=token)
            if type(rows) is not list or any(not _valid_workgroup_summary(row) for row in rows):
                raise IcebergInspectionUnavailableError("Athena workgroup response is invalid")
            typed_rows = cast(list[AthenaWorkgroupSummary], rows)
            all_rows.extend(typed_rows)
            empty_pages = empty_pages + 1 if not rows else 0
            if next_token is None:
                complete = True
                break
            if (
                type(next_token) is not str
                or not next_token
                or next_token in seen_tokens
                or empty_pages > _WORKGROUP_EMPTY_PAGE_LIMIT
            ):
                raise IcebergInspectionUnavailableError("Athena workgroup pagination is invalid")
            seen_tokens.add(next_token)
            token = next_token
        if not complete:
            raise IcebergInspectionUnavailableError("Athena workgroup pagination is incomplete")
        names = tuple(row.name for row in all_rows)
        if len(names) != len(set(names)):
            raise IcebergInspectionUnavailableError("Athena workgroup response is invalid")
        enabled = tuple(row.name for row in all_rows if row.state == "ENABLED")
        selected = self._selections.get(self._scope, "workgroup")
        if selected in enabled:
            return selected
        if enabled:
            return enabled[0]
        self._selections.discard(self._scope, "workgroup")
        raise IcebergInspectionUnavailableError("Athena workgroup unavailable")


def _valid_workgroup_summary(value: object) -> bool:
    return (
        type(value) is AthenaWorkgroupSummary
        and type(value.name) is str
        and bool(value.name.strip())
        and value.name == value.name.strip()
        and value.state in {"ENABLED", "DISABLED"}
        and (value.description is None or type(value.description) is str)
        and (value.created_at is None or type(value.created_at) is datetime)
    )


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
        athena_client_factory: AthenaClientFactory | None = None,
        selection_store: ServiceSelectionStore | None = None,
    ) -> None:
        self._hub = hub
        self._dispatcher = dispatcher
        self._aws_session = aws_session
        self._client_factory = glue_client_factory
        self._athena_client_factory = athena_client_factory
        self._selections = selection_store or ServiceSelectionStore()

    def supports(self, connection: Connection) -> bool:
        return connection.kind == "aws"

    def build_vm(self, connection: Connection) -> GluePageVM:
        client: GlueClientProtocol = (
            self._client_factory(connection)
            if self._client_factory is not None
            else GlueClient(aws_session=self._aws_session, connection=connection)
        )
        athena_client = (
            self._athena_client_factory(connection)
            if self._athena_client_factory is not None
            else AthenaClient(aws_session=self._aws_session, connection=connection)
        )
        return GluePageVM(
            client=client,
            iceberg_inspector=_ContextualIcebergInspector(
                client=athena_client,
                connection=connection,
                selections=self._selections,
            ),
            connection=connection,
            selection_store=self._selections,
            hub=self._hub,
            dispatcher=self._dispatcher,
        )


__all__ = [
    "AthenaClientFactory",
    "GlueClientFactory",
    "GlueClientProtocol",
    "GlueService",
]
