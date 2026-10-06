"""Read-only provider seams for the independent Glue comparison VM."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from aws_tui.domain.data_catalog import DatabaseSummary, TableDetail, TableRef, TableSummary
from aws_tui.infra.connection_resolver import Connection
from aws_tui.vm.service_source_vm import ServiceSourceContext


class ComparisonCatalogClient(Protocol):
    async def list_databases_page(
        self, *, start_token: str | None = None
    ) -> tuple[list[DatabaseSummary], str | None]: ...

    async def list_tables_page(
        self, database: str, *, start_token: str | None = None
    ) -> tuple[list[TableSummary], str | None]: ...

    async def get_table(self, ref: TableRef) -> TableDetail: ...


@dataclass(frozen=True, slots=True)
class ResolvedSource:
    connection: Connection = field(repr=False)
    client: ComparisonCatalogClient = field(repr=False)

    @property
    def connection_name(self) -> str:
        return self.connection.name

    @property
    def region(self) -> str:
        return self.connection.region


class ComparisonRouter(Protocol):
    def sources(self) -> tuple[ServiceSourceContext, ...]: ...

    def resolve(self, connection_name: str, region: str) -> ResolvedSource: ...

    def is_current(self, source: ResolvedSource) -> bool: ...


__all__ = ["ComparisonCatalogClient", "ComparisonRouter", "ResolvedSource"]
