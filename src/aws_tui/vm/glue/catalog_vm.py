from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any, Literal, cast

import reactivex as rx
from reactivex.subject import Subject
from vmx import ComponentVMOf, Message, MessageHub, PropertyChangedMessage
from vmx.collections.token_paged_composition import TokenPagedComposition
from vmx.lifecycle.status import ConstructionStatus
from vmx.services.dispatcher import Dispatcher

from aws_tui.domain.data_catalog import (
    ColumnStatistics,
    DatabaseSummary,
    PartitionSummary,
    TableDetail,
    TableFormat,
    TableRef,
    TableSummary,
)
from aws_tui.domain.filesystem import ProviderError
from aws_tui.domain.s3_uri import parse_s3_uri
from aws_tui.vm.file_manager.pane_vm import PaneState
from aws_tui.vm.glue._errors import map_provider_error, map_unexpected_error
from aws_tui.vm.glue.iceberg_vm import (
    GlueIcebergVM,
    IcebergInspectorProtocol,
    UnavailableIcebergInspector,
)
from aws_tui.vm.messages import OpenAthenaTableRequest, OpenS3LocationRequest

_DISCOVERY_PAGE_LIMIT = 64
_DISCOVERY_EMPTY_PAGE_LIMIT = 3


class GlueCatalogVM:
    def __init__(
        self,
        *,
        client: Any,
        iceberg_inspector: IcebergInspectorProtocol | None = None,
        hub: MessageHub[Message],
        dispatcher: Dispatcher,
    ) -> None:
        self._client = client
        self._hub = hub
        self._disposed = False
        self._on_property_changed: Subject[str] = Subject()
        self._inner: ComponentVMOf[None] = (
            ComponentVMOf[None]
            .builder()
            .name("glue.catalog")
            .model(None)
            .services(hub, dispatcher)
            .build()
        )
        self.iceberg = GlueIcebergVM(
            inspector=iceberg_inspector or UnavailableIcebergInspector(),
            hub=hub,
            dispatcher=dispatcher,
        )

        self._database_generation = 0
        self._table_generation = 0
        self._detail_generation = 0
        self._partition_generation = 0
        self._database_pager = self._make_database_pager()
        self._table_pager = self._make_table_pager(None)
        self._partition_pager = self._make_partition_pager(None)

        self._selected_database_name: str | None = None
        self._selected_table_name: str | None = None
        self._table_detail: TableDetail | None = None
        self._column_statistics: tuple[ColumnStatistics, ...] = ()

        self._databases_state = PaneState.LOADING
        self._tables_state = PaneState.EMPTY
        self._detail_state = PaneState.EMPTY
        self._partitions_state = PaneState.EMPTY
        self._statistics_state = PaneState.EMPTY
        self._databases_error_text: str | None = None
        self._tables_error_text: str | None = None
        self._detail_error_text: str | None = None
        self._partitions_error_text: str | None = None
        self._statistics_error_text: str | None = None

    @property
    def databases(self) -> tuple[DatabaseSummary, ...]:
        return tuple(self._database_pager.items)

    @property
    def tables(self) -> tuple[TableSummary, ...]:
        return tuple(self._table_pager.items)

    @property
    def partitions(self) -> tuple[PartitionSummary, ...]:
        return tuple(self._partition_pager.items)

    @property
    def table_detail(self) -> TableDetail | None:
        return self._table_detail

    @property
    def column_statistics(self) -> tuple[ColumnStatistics, ...]:
        return self._column_statistics

    @property
    def selected_database_name(self) -> str | None:
        return self._selected_database_name

    @property
    def selected_table_name(self) -> str | None:
        return self._selected_table_name

    @property
    def has_more_databases(self) -> bool:
        return self._database_pager.current_token is not None

    @property
    def has_more_tables(self) -> bool:
        return self._table_pager.current_token is not None

    @property
    def has_more_partitions(self) -> bool:
        return self._partition_pager.current_token is not None

    @property
    def state(self) -> PaneState:
        return self._databases_state

    @property
    def error_text(self) -> str | None:
        return self._databases_error_text

    @property
    def databases_state(self) -> PaneState:
        return self._databases_state

    @property
    def tables_state(self) -> PaneState:
        return self._tables_state

    @property
    def detail_state(self) -> PaneState:
        return self._detail_state

    @property
    def partitions_state(self) -> PaneState:
        return self._partitions_state

    @property
    def statistics_state(self) -> PaneState:
        return self._statistics_state

    @property
    def databases_error_text(self) -> str | None:
        return self._databases_error_text

    @property
    def tables_error_text(self) -> str | None:
        return self._tables_error_text

    @property
    def detail_error_text(self) -> str | None:
        return self._detail_error_text

    @property
    def partitions_error_text(self) -> str | None:
        return self._partitions_error_text

    @property
    def statistics_error_text(self) -> str | None:
        return self._statistics_error_text

    @property
    def status(self) -> ConstructionStatus:
        return self._inner.status

    @property
    def on_property_changed(self) -> rx.Observable[str]:
        return self._on_property_changed

    def construct(self) -> None:
        self._inner.construct()
        self.iceberg.construct()

    def dispose(self) -> None:
        if self._disposed:
            return
        self._disposed = True
        self._database_generation += 1
        self._table_generation += 1
        self._detail_generation += 1
        self._partition_generation += 1
        self._partition_pager.dispose()
        self._table_pager.dispose()
        self._database_pager.dispose()
        self.iceberg.dispose()
        self._on_property_changed.on_completed()
        self._on_property_changed.dispose()
        self._inner.dispose()

    async def shutdown(self) -> None:
        await self.iceberg.shutdown()

    async def setup(self) -> None:
        await self.refresh_databases()

    async def refresh_databases(self) -> None:
        self._database_generation += 1
        old_pager = self._database_pager
        self._database_pager = self._make_database_pager()
        old_pager.dispose()
        self._notify("has_more_databases")
        generation = self._database_generation
        self._databases_error_text = None
        self._set_state("_databases_state", PaneState.LOADING, "state")
        try:
            await self._database_pager.refresh_command.execute_async()
        except ProviderError as exc:
            if generation != self._database_generation:
                return
            state, self._databases_error_text = map_provider_error(exc)
            self._set_state("_databases_state", state, "state")
            return
        except Exception as exc:
            if generation != self._database_generation:
                return
            state, self._databases_error_text = map_unexpected_error(exc)
            self._set_state("_databases_state", state, "state")
            return
        if generation != self._database_generation:
            return
        self._notify("databases")
        self._notify("has_more_databases")
        if not self.databases:
            await self._clear_database_selection()
        self._set_state(
            "_databases_state",
            PaneState.IDLE if self.databases else PaneState.EMPTY,
            "state",
        )

    async def load_more_databases(self) -> None:
        if not self.has_more_databases:
            return
        generation = self._database_generation
        try:
            await self._database_pager.load_more_command.execute_async()
        except ProviderError as exc:
            if generation != self._database_generation:
                return
            state, self._databases_error_text = map_provider_error(exc)
            self._set_state("_databases_state", state, "state")
            return
        except Exception as exc:
            if generation != self._database_generation:
                return
            state, self._databases_error_text = map_unexpected_error(exc)
            self._set_state("_databases_state", state, "state")
            return
        if generation == self._database_generation:
            self._notify("databases")
            self._notify("has_more_databases")

    async def select_database(self, database_name: str) -> None:
        if not any(row.ref.database_name == database_name for row in self.databases):
            return
        self._table_generation += 1
        generation = self._table_generation
        self._selected_database_name = database_name
        self._selected_table_name = None
        await self.iceberg.clear_table()
        self._table_detail = None
        self._column_statistics = ()
        self._detail_generation += 1
        self._replace_table_pager(database_name)
        self._replace_partition_pager(None)
        self._tables_error_text = None
        self._set_state("_tables_state", PaneState.LOADING, "tables_state")
        self._set_state("_detail_state", PaneState.EMPTY, "detail_state")
        self._set_state("_partitions_state", PaneState.EMPTY, "partitions_state")
        self._set_state("_statistics_state", PaneState.EMPTY, "statistics_state")
        self._notify("selected_database_name")
        self._notify("selected_table_name")
        self._notify("tables")
        self._notify("table_detail")
        self._notify("partitions")
        self._notify("column_statistics")
        try:
            await self._table_pager.refresh_command.execute_async()
        except ProviderError as exc:
            if generation != self._table_generation:
                return
            state, self._tables_error_text = map_provider_error(exc)
            self._set_state("_tables_state", state, "tables_state")
            return
        except Exception as exc:
            if generation != self._table_generation:
                return
            state, self._tables_error_text = map_unexpected_error(exc)
            self._set_state("_tables_state", state, "tables_state")
            return
        if generation != self._table_generation:
            return
        self._notify("tables")
        self._notify("has_more_tables")
        self._set_state(
            "_tables_state",
            PaneState.IDLE if self.tables else PaneState.EMPTY,
            "tables_state",
        )

    async def load_more_tables(self) -> None:
        if not self.has_more_tables:
            return
        generation = self._table_generation
        try:
            await self._table_pager.load_more_command.execute_async()
        except ProviderError as exc:
            if generation != self._table_generation:
                return
            state, self._tables_error_text = map_provider_error(exc)
            self._set_state("_tables_state", state, "tables_state")
            return
        except Exception as exc:
            if generation != self._table_generation:
                return
            state, self._tables_error_text = map_unexpected_error(exc)
            self._set_state("_tables_state", state, "tables_state")
            return
        if generation == self._table_generation:
            self._notify("tables")
            self._notify("has_more_tables")

    async def select_table(self, table_name: str) -> None:
        summary = next(
            (row for row in self.tables if row.ref.table_name == table_name),
            None,
        )
        if summary is None:
            return
        self._detail_generation += 1
        generation = self._detail_generation
        self._selected_table_name = table_name
        self._table_detail = None
        self._column_statistics = ()
        await self.iceberg.clear_table()
        self._replace_partition_pager(summary.ref)
        self._detail_error_text = None
        self._partitions_error_text = None
        self._statistics_error_text = None
        self._set_state("_detail_state", PaneState.LOADING, "detail_state")
        self._set_state("_partitions_state", PaneState.LOADING, "partitions_state")
        self._set_state("_statistics_state", PaneState.LOADING, "statistics_state")
        self._notify("selected_table_name")
        self._notify("table_detail")
        self._notify("partitions")
        self._notify("column_statistics")

        detail = await self._load_detail(summary.ref, generation)
        if generation != self._detail_generation:
            return
        if detail is not None:
            self._table_detail = detail
            self._notify("table_detail")
            self._set_state("_detail_state", PaneState.IDLE, "detail_state")
            if detail.table_format is TableFormat.ICEBERG:
                await self.iceberg.bind_table(
                    detail.summary.ref,
                    table_format=detail.table_format,
                )
                if generation != self._detail_generation:
                    return

        await self._load_partitions(generation)
        if generation != self._detail_generation:
            return
        if detail is None:
            self._set_state("_statistics_state", PaneState.EMPTY, "statistics_state")
            return
        await self._load_statistics(detail, generation)

    async def open_table(self, ref: TableRef) -> None:
        """Select one exact table without substituting source identity."""
        if self._disposed:
            raise ValueError("table cannot be opened")

        def database_available() -> bool:
            return any(
                row
                for row in self.databases
                if row.ref.catalog_name == ref.catalog_name
                and row.ref.database_name == ref.database_name
                and row.ref.connection_name == ref.connection_name
                and row.ref.region == ref.region
            )

        database_discovery = await self._load_until_discovered(
            database_available,
            has_more=lambda: self.has_more_databases,
            current_token=lambda: self._database_pager.current_token,
            item_count=lambda: len(self.databases),
            load_more=self.load_more_databases,
        )
        if database_discovery is None:
            raise ProviderError("Glue catalog discovery did not complete")
        if not database_discovery:
            raise ValueError("table is unavailable in the active Glue source")
        if self._selected_database_name != ref.database_name:
            await self.select_database(ref.database_name)

        def table_available() -> bool:
            return any(row.ref == ref for row in self.tables)

        table_discovery = await self._load_until_discovered(
            table_available,
            has_more=lambda: self.has_more_tables,
            current_token=lambda: self._table_pager.current_token,
            item_count=lambda: len(self.tables),
            load_more=self.load_more_tables,
        )
        if table_discovery is None:
            raise ProviderError("Glue catalog discovery did not complete")
        if not table_discovery:
            raise ValueError("table is unavailable in the active Glue source")
        await self.select_table(ref.table_name)
        if self._selected_table_name != ref.table_name:
            raise ValueError("table is unavailable in the active Glue source")

    async def _load_until_discovered(
        self,
        available: Callable[[], bool],
        *,
        has_more: Callable[[], bool],
        current_token: Callable[[], str | None],
        item_count: Callable[[], int],
        load_more: Callable[[], Awaitable[None]],
    ) -> bool | None:
        seen_tokens: set[str] = set()
        empty_pages = 0
        request_count = 0
        while not available() and has_more():
            token = current_token()
            if token is None or token in seen_tokens or request_count >= _DISCOVERY_PAGE_LIMIT:
                return None
            seen_tokens.add(token)
            count_before = item_count()
            await load_more()
            request_count += 1
            if available():
                return True
            count_after = item_count()
            if count_after == count_before:
                empty_pages += 1
                if empty_pages > _DISCOVERY_EMPTY_PAGE_LIMIT:
                    return None
            else:
                empty_pages = 0
            next_token = current_token()
            if next_token is not None and next_token in seen_tokens:
                return None
        return available()

    def query_in_athena(self, snapshot_id: int | None = None) -> bool:
        """Publish the selected table identity for Athena composition."""
        if self._disposed or self._selected_table_name is None:
            return False
        summary = next(
            (row for row in self.tables if row.ref.table_name == self._selected_table_name),
            None,
        )
        if summary is None:
            return False
        self._hub.send(
            OpenAthenaTableRequest(
                table_ref=summary.ref,
                snapshot_id=snapshot_id,
            )
        )
        return True

    async def load_more_partitions(self) -> None:
        if not self.has_more_partitions:
            return
        generation = self._detail_generation
        try:
            await self._partition_pager.load_more_command.execute_async()
        except ProviderError as exc:
            if generation != self._detail_generation:
                return
            state, self._partitions_error_text = map_provider_error(exc)
            self._set_state("_partitions_state", state, "partitions_state")
            return
        except Exception as exc:
            if generation != self._detail_generation:
                return
            state, self._partitions_error_text = map_unexpected_error(exc)
            self._set_state("_partitions_state", state, "partitions_state")
            return
        if generation == self._detail_generation:
            self._notify("partitions")
            self._notify("has_more_partitions")

    def open_s3_location(
        self,
        *,
        preferred_pane: Literal["left", "right"] = "left",
    ) -> bool:
        """Publish a same-identity S3 request for the selected table."""
        detail = self._table_detail
        if self._disposed or detail is None:
            return False
        location = detail.storage.location
        if location is None or parse_s3_uri(location) is None:
            return False
        ref = detail.summary.ref
        self._hub.send(
            OpenS3LocationRequest(
                connection_name=ref.connection_name,
                region=ref.region,
                uri=location,
                preferred_pane=preferred_pane,
            )
        )
        return True

    async def _load_detail(self, ref: TableRef, generation: int) -> TableDetail | None:
        try:
            return cast(TableDetail, await self._client.get_table(ref))
        except ProviderError as exc:
            if generation != self._detail_generation:
                return None
            state, self._detail_error_text = map_provider_error(exc)
            self._set_state("_detail_state", state, "detail_state")
        except Exception as exc:
            if generation != self._detail_generation:
                return None
            state, self._detail_error_text = map_unexpected_error(exc)
            self._set_state("_detail_state", state, "detail_state")
        return None

    async def _load_partitions(self, generation: int) -> None:
        try:
            await self._partition_pager.refresh_command.execute_async()
        except ProviderError as exc:
            if generation != self._detail_generation:
                return
            state, self._partitions_error_text = map_provider_error(exc)
            self._set_state("_partitions_state", state, "partitions_state")
            return
        except Exception as exc:
            if generation != self._detail_generation:
                return
            state, self._partitions_error_text = map_unexpected_error(exc)
            self._set_state("_partitions_state", state, "partitions_state")
            return
        if generation != self._detail_generation:
            return
        self._notify("partitions")
        self._notify("has_more_partitions")
        self._set_state(
            "_partitions_state",
            PaneState.IDLE if self.partitions else PaneState.EMPTY,
            "partitions_state",
        )

    async def _load_statistics(self, detail: TableDetail, generation: int) -> None:
        try:
            rows = await self._client.get_column_statistics(
                detail.summary.ref,
                tuple(column.name for column in detail.columns),
            )
        except ProviderError as exc:
            if generation != self._detail_generation:
                return
            state, self._statistics_error_text = map_provider_error(exc)
            self._set_state("_statistics_state", state, "statistics_state")
            return
        except Exception as exc:
            if generation != self._detail_generation:
                return
            state, self._statistics_error_text = map_unexpected_error(exc)
            self._set_state("_statistics_state", state, "statistics_state")
            return
        if generation != self._detail_generation:
            return
        self._column_statistics = tuple(rows)
        self._notify("column_statistics")
        self._set_state(
            "_statistics_state",
            PaneState.IDLE if self._column_statistics else PaneState.EMPTY,
            "statistics_state",
        )

    def _make_database_pager(self) -> TokenPagedComposition[DatabaseSummary, str]:
        generation = self._database_generation

        async def fetch(token: str | None) -> tuple[list[DatabaseSummary], str | None]:
            rows, next_token = await self._client.list_databases_page(start_token=token)
            if generation != self._database_generation:
                return [], None
            return rows, next_token

        return TokenPagedComposition(fetch)

    def _make_table_pager(
        self,
        database_name: str | None,
    ) -> TokenPagedComposition[TableSummary, str]:
        generation = self._table_generation

        async def fetch(token: str | None) -> tuple[list[TableSummary], str | None]:
            if database_name is None:
                return [], None
            rows, next_token = await self._client.list_tables_page(
                database_name,
                start_token=token,
            )
            if generation != self._table_generation:
                return [], None
            return rows, next_token

        return TokenPagedComposition(fetch)

    def _make_partition_pager(
        self,
        ref: TableRef | None,
    ) -> TokenPagedComposition[PartitionSummary, str]:
        generation = self._partition_generation

        async def fetch(token: str | None) -> tuple[list[PartitionSummary], str | None]:
            if ref is None:
                return [], None
            rows, next_token = await self._client.list_partitions_page(
                ref,
                start_token=token,
            )
            if generation != self._partition_generation:
                return [], None
            return rows, next_token

        return TokenPagedComposition(fetch)

    def _replace_table_pager(self, database_name: str | None) -> None:
        old_pager = self._table_pager
        self._table_pager = self._make_table_pager(database_name)
        old_pager.dispose()

    def _replace_partition_pager(self, ref: TableRef | None) -> None:
        self._partition_generation += 1
        old_pager = self._partition_pager
        self._partition_pager = self._make_partition_pager(ref)
        old_pager.dispose()

    async def _clear_database_selection(self) -> None:
        self._table_generation += 1
        self._detail_generation += 1
        self._selected_database_name = None
        self._selected_table_name = None
        await self.iceberg.clear_table()
        self._table_detail = None
        self._column_statistics = ()
        self._replace_table_pager(None)
        self._replace_partition_pager(None)
        self._tables_error_text = None
        self._detail_error_text = None
        self._partitions_error_text = None
        self._statistics_error_text = None
        self._set_state("_tables_state", PaneState.EMPTY, "tables_state")
        self._set_state("_detail_state", PaneState.EMPTY, "detail_state")
        self._set_state("_partitions_state", PaneState.EMPTY, "partitions_state")
        self._set_state("_statistics_state", PaneState.EMPTY, "statistics_state")
        self._notify("selected_database_name")
        self._notify("selected_table_name")
        self._notify("tables")
        self._notify("has_more_tables")
        self._notify("table_detail")
        self._notify("partitions")
        self._notify("has_more_partitions")
        self._notify("column_statistics")

    def _set_state(self, field: str, state: PaneState, property_name: str) -> None:
        if getattr(self, field) == state:
            return
        setattr(self, field, state)
        self._notify(property_name)

    def _notify(self, property_name: str) -> None:
        if self._disposed:
            return
        self._hub.send(PropertyChangedMessage.create(self, "glue.catalog", property_name))
        self._on_property_changed.on_next(property_name)


__all__ = ["GlueCatalogVM"]
