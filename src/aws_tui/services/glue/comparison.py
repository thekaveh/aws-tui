"""Resolve explicit comparison environments without changing the active page."""

from __future__ import annotations

import unicodedata
from collections.abc import Callable, Sequence
from dataclasses import replace

from aws_tui.domain.data_catalog import (
    DatabaseRef,
    DatabaseSummary,
    TableDetail,
    TableRef,
    TableSummary,
)
from aws_tui.domain.filesystem import ValidationError
from aws_tui.infra.connection_resolver import Connection
from aws_tui.vm.glue.comparison_ports import ComparisonCatalogClient, ResolvedSource
from aws_tui.vm.service_source_vm import ServiceSourceContext


class BoundComparisonCatalog:
    """Expose only catalog reads, checking identities at both ends of each read."""

    def __init__(self, connection: Connection, client: ComparisonCatalogClient) -> None:
        self._connection = connection
        self._client = client

    def _owns(self, ref: DatabaseRef | TableRef) -> bool:
        return (
            isinstance(ref, (DatabaseRef, TableRef))
            and ref.catalog_name == "AwsDataCatalog"
            and ref.connection_name == self._connection.name
            and ref.region == self._connection.region
        )

    async def list_databases_page(
        self, *, start_token: str | None = None
    ) -> tuple[list[DatabaseSummary], str | None]:
        rows, token = await self._client.list_databases_page(start_token=start_token)
        if any(not isinstance(row, DatabaseSummary) or not self._owns(row.ref) for row in rows):
            raise ValidationError("comparison database response identity is invalid")
        return rows, token

    async def list_tables_page(
        self, database: str, *, start_token: str | None = None
    ) -> tuple[list[TableSummary], str | None]:
        rows, token = await self._client.list_tables_page(database, start_token=start_token)
        if any(
            not isinstance(row, TableSummary)
            or not self._owns(row.ref)
            or row.ref.database_name != database
            for row in rows
        ):
            raise ValidationError("comparison table response identity is invalid")
        return rows, token

    async def get_table(self, ref: TableRef) -> TableDetail:
        if not isinstance(ref, TableRef) or not self._owns(ref):
            raise ValidationError("comparison table request identity is invalid")
        detail = await self._client.get_table(ref)
        if not isinstance(detail, TableDetail) or detail.summary.ref != ref:
            raise ValidationError("comparison table response identity is invalid")
        return detail


class GlueComparisonRouter:
    """Bind each route to its chosen connection and region, without Athena."""

    def __init__(
        self,
        *,
        connections: Callable[[], Sequence[Connection]],
        client_factory: Callable[[Connection], ComparisonCatalogClient],
    ) -> None:
        self._connections = connections
        self._client_factory = client_factory

    def _configured_sources(self) -> tuple[Connection, ...]:
        try:
            return tuple(source for source in self._connections() if source.kind == "aws")
        except Exception:
            # Configuration discovery errors can include credentials or endpoints.
            raise ValidationError("comparison sources are unavailable") from None

    def sources(self) -> tuple[ServiceSourceContext, ...]:
        return tuple(
            ServiceSourceContext.from_connection(source) for source in self._configured_sources()
        )

    def _effective_connection(self, connection_name: str, region: str) -> Connection:
        region = region.strip()
        if not region or any(
            character.isspace() or unicodedata.category(character).startswith("C")
            for character in region
        ):
            raise ValidationError("comparison region is invalid")
        candidates = tuple(
            source for source in self._configured_sources() if source.name == connection_name
        )
        if len(candidates) != 1:
            raise ValidationError("comparison source is unavailable or ambiguous")
        return replace(candidates[0], region=region)

    def resolve(self, connection_name: str, region: str) -> ResolvedSource:
        connection = self._effective_connection(connection_name, region)
        client = BoundComparisonCatalog(connection, self._client_factory(connection))
        return ResolvedSource(connection, client)

    def is_current(self, source: ResolvedSource) -> bool:
        try:
            current = self._effective_connection(source.connection_name, source.region)
        except ValidationError:
            return False
        return current == source.connection


__all__ = ["BoundComparisonCatalog", "GlueComparisonRouter"]
