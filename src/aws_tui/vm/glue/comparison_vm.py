"""Independent, revision-owned loading for two explicit Glue definitions."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Coroutine
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from typing import Literal, TypeVar, cast

import reactivex as rx
from vmx import Message, MessageHub, PropertyChangedMessage
from vmx.services.dispatcher import Dispatcher

from aws_tui.domain.data_catalog import (
    DatabaseRef,
    DatabaseSummary,
    TableDetail,
    TableRef,
    TableSummary,
)
from aws_tui.domain.filesystem import ProviderError, ValidationError
from aws_tui.domain.table_comparison import (
    Side,
    TableComparison,
    TableSnapshot,
    compare_tables,
    export_comparison,
)
from aws_tui.vm._observable import ObserverSafeSubject, send_value_free
from aws_tui.vm.file_manager.pane_vm import PaneState
from aws_tui.vm.glue._errors import map_provider_error, map_unexpected_error
from aws_tui.vm.glue._lifecycle import GlueOperationOwner, GlueOperationSuperseded
from aws_tui.vm.glue.comparison_ports import ComparisonRouter, ResolvedSource
from aws_tui.vm.service_diagnostics import report_unexpected_service_error
from aws_tui.vm.service_source_vm import ServiceSourceContext

T = TypeVar("T")
ListKind = Literal["databases", "tables"]
Stage = Literal["databases", "tables", "detail"]
_MAX_ITEMS = 1_000
_MAX_PAGES = 64
_MAX_EMPTY_PAGES = 3


@dataclass(frozen=True, slots=True)
class ComparisonSideState:
    connection_name: str | None = None
    region: str | None = None
    selected_database: DatabaseRef | None = None
    selected_table: TableRef | None = None
    databases: tuple[DatabaseSummary, ...] = ()
    tables: tuple[TableSummary, ...] = ()
    has_more_databases: bool = False
    has_more_tables: bool = False
    databases_loading: bool = False
    tables_loading: bool = False
    database_limit_reached: bool = False
    table_limit_reached: bool = False
    snapshot: TableSnapshot | None = None
    state: PaneState = PaneState.EMPTY
    error_text: str | None = None
    status_text: str = "Not selected"
    revision: int = 0


@dataclass
class _Pager:
    token: str | None = None
    seen: set[str] = field(default_factory=set)
    requests: int = 0
    empty_pages: int = 0


@dataclass
class _Side:
    state: ComparisonSideState = field(default_factory=ComparisonSideState)
    owner: GlueOperationOwner = field(default_factory=GlueOperationOwner)
    route: ResolvedSource | None = None
    stage: Stage | None = None
    started_revision: int = -1
    databases: _Pager = field(default_factory=_Pager)
    tables: _Pager = field(default_factory=_Pager)


class GlueComparisonVM:
    def __init__(
        self,
        *,
        router: ComparisonRouter,
        hub: MessageHub[Message],
        dispatcher: Dispatcher,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._router = router
        self._hub = hub
        self._clock = clock or (lambda: datetime.now(UTC))
        self._sides: dict[Side, _Side] = {"left": _Side(), "right": _Side()}
        self._differences_only = False
        self._closed = False
        self._disposed = False
        self._shutdown_complete = False
        self._shutdown_lock = asyncio.Lock()
        self._changes = ObserverSafeSubject[str]()
        # No inner lifecycle component: its disposal would publish after close.
        # The dispatcher stays in the constructor contract used by service builders.

    def construct(self) -> None:
        """No eager work: property subscriptions are ready at construction."""

    @property
    def on_property_changed(self) -> rx.Observable[str]:
        return self._changes

    @property
    def sources(self) -> tuple[ServiceSourceContext, ...]:
        return self._router.sources() if not self._closed else ()

    @property
    def actions_available(self) -> bool:
        return not self._closed

    def side(self, side: Side) -> ComparisonSideState:
        return self._sides[side].state

    @property
    def differences_only(self) -> bool:
        return self._differences_only

    @property
    def comparison(self) -> TableComparison | None:
        left, right = self.side("left").snapshot, self.side("right").snapshot
        if self._closed or left is None or right is None:
            return None
        return compare_tables(left.detail, right.detail)

    def _advance(self, side: Side) -> _Side:
        record = self._sides[side]
        record.state = replace(
            record.state,
            revision=record.state.revision + 1,
            databases_loading=False,
            tables_loading=False,
            error_text=None,
        )
        record.owner.cancel()
        record.stage = None
        return record

    def _resolve(self, side: Side) -> bool:
        record = self._sides[side]
        record.route = None
        state = record.state
        if state.connection_name is None or state.region is None:
            return False
        try:
            record.route = self._router.resolve(state.connection_name, state.region)
        except Exception as exc:
            self._fail(side, exc, "resolve_source")
            return False
        record.state = replace(
            record.state, connection_name=record.route.connection_name, region=record.route.region
        )
        return True

    def choose_source(self, side: Side, connection_name: str, region: str) -> int:
        if self._closed:
            return self.side(side).revision
        record = self._advance(side)
        record.databases, record.tables = _Pager(), _Pager()
        record.state = ComparisonSideState(
            connection_name=connection_name,
            region=region.strip(),
            revision=record.state.revision,
            state=PaneState.LOADING,
            status_text="Loading databases",
        )
        if self._resolve(side):
            record.stage = "databases"
            self._emit_side(side)
        return record.state.revision

    def choose_database(self, side: Side, ref: DatabaseRef) -> int:
        if self._closed:
            return self.side(side).revision
        record = self._advance(side)
        record.tables = _Pager()
        record.state = replace(
            record.state,
            selected_database=None,
            selected_table=None,
            tables=(),
            has_more_tables=False,
            table_limit_reached=False,
            snapshot=None,
            state=PaneState.LOADING,
            status_text="Loading tables",
        )
        if record.route is None or not self._owns(record.route, ref):
            self._fail(
                side, ValidationError("comparison database identity is invalid"), "select_database"
            )
        else:
            record.state = replace(record.state, selected_database=ref)
            record.stage = "tables"
            self._emit_side(side)
        return record.state.revision

    def choose_table(self, side: Side, ref: TableRef) -> int:
        if self._closed:
            return self.side(side).revision
        record = self._advance(side)
        record.state = replace(
            record.state,
            selected_table=None,
            snapshot=None,
            state=PaneState.LOADING,
            status_text="Fetching table",
        )
        if (
            record.route is None
            or not self._owns(record.route, ref)
            or ref.database != record.state.selected_database
        ):
            self._fail(
                side, ValidationError("comparison table identity is invalid"), "select_table"
            )
        else:
            record.state = replace(record.state, selected_table=ref)
            record.stage = "detail"
            self._emit_side(side)
        return record.state.revision

    def pin(self, side: Side, ref: TableRef) -> int:
        if self._closed:
            return self.side(side).revision
        record = self._advance(side)
        record.databases, record.tables = _Pager(), _Pager()
        record.state = ComparisonSideState(
            connection_name=ref.connection_name,
            region=ref.region,
            revision=record.state.revision,
            state=PaneState.LOADING,
            status_text="Fetching table",
        )
        if self._resolve(side):
            assert record.route is not None
            if not self._owns(record.route, ref):
                self._fail(
                    side, ValidationError("comparison table identity is invalid"), "pin_table"
                )
            else:
                record.state = replace(
                    record.state, selected_database=ref.database, selected_table=ref
                )
                record.stage = "detail"
                self._emit_side(side)
        return record.state.revision

    def refresh(self, side: Side) -> int:
        if self._closed:
            return self.side(side).revision
        record = self._advance(side)
        if not self._resolve(side):
            return record.state.revision
        state = record.state
        if state.selected_table is not None:
            record.stage = "detail"
            status = "Refreshing; showing previous fetch" if state.snapshot else "Fetching table"
        elif state.selected_database is not None:
            record.stage = "tables"
            record.tables = _Pager()
            record.state = replace(
                state, tables=(), has_more_tables=False, table_limit_reached=False
            )
            status = "Loading tables"
        else:
            record.stage = "databases"
            record.databases = _Pager()
            record.state = replace(
                state, databases=(), has_more_databases=False, database_limit_reached=False
            )
            status = "Loading databases"
        record.state = replace(record.state, state=PaneState.LOADING, status_text=status)
        self._emit_side(side)
        return record.state.revision

    @staticmethod
    def _owns(route: ResolvedSource, ref: DatabaseRef | TableRef) -> bool:
        return (
            ref.catalog_name == "AwsDataCatalog"
            and ref.connection_name == route.connection_name
            and ref.region == route.region
        )

    def _accept(self, side: Side, revision: int, route: ResolvedSource) -> bool:
        record = self._sides[side]
        if self._closed or record.state.revision != revision or record.route is not route:
            return False
        if not self._router.is_current(route):
            record.route = None
            record.state = replace(
                record.state,
                state=PaneState.ERROR,
                error_text="Source changed; refresh or reselect",
                status_text="Source changed; refresh or reselect",
                databases_loading=False,
                tables_loading=False,
            )
            self._emit_side(side)
            return False
        return True

    async def _run(
        self,
        side: Side,
        revision: int,
        route: ResolvedSource,
        operation: Callable[[], Coroutine[object, object, T]],
        name: str,
    ) -> T | None:
        async def guarded() -> T | None:
            if not self._accept(side, revision, route):
                return None
            try:
                return await operation()
            except Exception:
                # Do not leak stale exceptions through the owner's cleanup diagnostics.
                if not self._accept(side, revision, route):
                    return None
                raise

        try:
            result = await self._sides[side].owner.run(guarded)
        except GlueOperationSuperseded:
            return None
        except asyncio.CancelledError:
            if self._closed or self.side(side).revision != revision:
                return None
            raise
        except Exception as exc:
            if self._accept(side, revision, route):
                self._fail(side, exc, name, revision=revision, route=route)
            return None
        return result if self._accept(side, revision, route) else None

    async def load_revision(self, side: Side, revision: int) -> None:
        record = self._sides[side]
        route = record.route
        if (
            route is None
            or record.started_revision == revision
            or not self._accept(side, revision, route)
        ):
            return
        record.started_revision = revision
        if record.stage == "detail":
            ref = record.state.selected_table
            if ref is None:
                return
            detail = await self._run(
                side, revision, route, lambda: route.client.get_table(ref), "get_table"
            )
            if detail is None:
                return
            if not isinstance(detail, TableDetail) or detail.summary.ref != ref:
                self._fail(
                    side,
                    ValidationError("comparison table response identity is invalid"),
                    "get_table",
                    revision=revision,
                    route=route,
                )
                return
            snapshot = TableSnapshot(ref, detail, self._clock())
            if self._accept(side, revision, route):
                record.state = replace(
                    record.state,
                    snapshot=snapshot,
                    state=PaneState.IDLE,
                    error_text=None,
                    status_text="Fetched",
                )
                self._emit_side(side)
        elif record.stage in ("databases", "tables"):
            await self._load_page(side, revision, record.stage)

    async def load_more_databases(self, side: Side, revision: int) -> None:
        await self._more(side, revision, "databases")

    async def load_more_tables(self, side: Side, revision: int) -> None:
        await self._more(side, revision, "tables")

    async def _more(self, side: Side, revision: int, kind: ListKind) -> None:
        state = self.side(side)
        if self._closed or revision != state.revision or state.state is PaneState.LOADING:
            return
        if state.has_more_databases if kind == "databases" else state.has_more_tables:
            await self._load_page(side, revision, kind)

    async def _load_page(self, side: Side, revision: int, kind: ListKind) -> None:
        record = self._sides[side]
        route = record.route
        if route is None or not self._accept(side, revision, route):
            return
        pager = record.databases if kind == "databases" else record.tables
        database = record.state.selected_database
        if kind == "tables" and database is None:
            return
        if pager.requests >= _MAX_PAGES:
            return
        pager.requests += 1
        record.state = replace(
            record.state,
            state=PaneState.LOADING,
            databases_loading=kind == "databases",
            tables_loading=kind == "tables",
        )
        self._emit_side(side)
        result: (
            tuple[list[DatabaseSummary], str | None] | tuple[list[TableSummary], str | None] | None
        )
        if kind == "databases":
            result = await self._run(
                side,
                revision,
                route,
                lambda: route.client.list_databases_page(start_token=pager.token),
                "list_databases",
            )
        else:
            assert database is not None
            result = await self._run(
                side,
                revision,
                route,
                lambda: route.client.list_tables_page(
                    database.database_name, start_token=pager.token
                ),
                "list_tables",
            )
        if result is None:
            return
        rows, token = result
        if any(not self._valid_row(row, kind, route, database) for row in rows):
            self._fail(
                side,
                ValidationError("comparison discovery response identity is invalid"),
                f"list_{kind}",
                revision=revision,
                route=route,
            )
            return
        existing = record.state.databases if kind == "databases" else record.state.tables
        merged = dict.fromkeys(row.ref for row in existing)
        combined: list[DatabaseSummary | TableSummary] = list(existing)
        for row in rows:
            if row.ref not in merged:
                merged[row.ref] = None
                combined.append(row)
        pager.empty_pages = pager.empty_pages + 1 if not rows else 0
        limit = len(combined) > _MAX_ITEMS or (
            token is not None
            and (
                len(combined) >= _MAX_ITEMS
                or pager.requests >= _MAX_PAGES
                or pager.empty_pages >= _MAX_EMPTY_PAGES
                or not isinstance(token, str)
                or not token
                or token in pager.seen
            )
        )
        if isinstance(token, str):
            pager.seen.add(token)
        pager.token = None if limit else token
        record.state = replace(
            record.state,
            state=PaneState.IDLE if combined else PaneState.EMPTY,
            error_text=None,
            status_text=f"Choose a {'database' if kind == 'databases' else 'table'}"
            + ("; listing safety limit reached" if limit else ""),
        )
        if kind == "databases":
            record.state = replace(
                record.state,
                databases=tuple(cast(list[DatabaseSummary], combined[:_MAX_ITEMS])),
                databases_loading=False,
                database_limit_reached=limit,
                has_more_databases=pager.token is not None,
            )
        else:
            record.state = replace(
                record.state,
                tables=tuple(cast(list[TableSummary], combined[:_MAX_ITEMS])),
                tables_loading=False,
                table_limit_reached=limit,
                has_more_tables=pager.token is not None,
            )
        self._emit_side(side)

    def _valid_row(
        self,
        row: DatabaseSummary | TableSummary,
        kind: ListKind,
        route: ResolvedSource,
        database: DatabaseRef | None,
    ) -> bool:
        if kind == "databases":
            return isinstance(row, DatabaseSummary) and self._owns(route, row.ref)
        return (
            isinstance(row, TableSummary)
            and self._owns(route, row.ref)
            and row.ref.database == database
        )

    def _fail(
        self,
        side: Side,
        error: Exception,
        operation: str,
        *,
        revision: int | None = None,
        route: ResolvedSource | None = None,
    ) -> None:
        record = self._sides[side]
        revision = record.state.revision if revision is None else revision
        route = record.route if route is None else route
        if self._closed or record.state.revision != revision:
            return
        if isinstance(error, ProviderError):
            state, text = map_provider_error(error)
        else:
            state, text = map_unexpected_error(error)
            report_unexpected_service_error(
                self._hub, service="glue", operation=operation, error=error
            )
        if self._closed or record.state.revision != revision:
            return
        # Diagnostics notify synchronous subscribers, which may replace/remove
        # configuration without advancing this side's selection revision.
        # Source resolution failures intentionally have no route to validate.
        if route is not None and not self._accept(side, revision, route):
            return
        record.state = replace(
            record.state,
            state=state,
            error_text=text,
            databases_loading=False,
            tables_loading=False,
            status_text="Refresh failed; showing previous fetch"
            if record.state.snapshot
            else "Fetch failed",
        )
        self._emit_side(side)

    def toggle_differences_only(self) -> None:
        if not self._closed:
            self._differences_only = not self._differences_only
            self._notify("differences_only")

    def summary_text(self) -> str | None:
        comparison = self.comparison
        left, right = self.side("left"), self.side("right")
        if comparison is None or left.snapshot is None or right.snapshot is None:
            return None

        def freshness(state: ComparisonSideState) -> str:
            return (
                state.status_text
                if state.status_text.startswith(("Refresh", "Source changed"))
                else "Fetched"
            )

        return (
            export_comparison(left.snapshot, right.snapshot, comparison)
            + f"Left freshness: {freshness(left)}\nRight freshness: {freshness(right)}\n"
        )

    def _emit_side(self, side: Side) -> None:
        self._notify(side)
        self._notify("comparison")

    def _notify(self, name: str) -> None:
        if not self._closed:
            send_value_free(self._hub, PropertyChangedMessage.create(self, "glue.comparison", name))
            if not self._closed:
                self._changes.on_next(name)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        for record in self._sides.values():
            record.state = replace(
                record.state,
                revision=record.state.revision + 1,
                databases_loading=False,
                tables_loading=False,
            )
            record.owner.close()

    def dispose(self) -> None:
        self.close()
        if not self._disposed:
            self._disposed = True
            self._changes.on_completed()
            self._changes.dispose()

    async def shutdown(self) -> None:
        self.close()
        async with self._shutdown_lock:
            if self._shutdown_complete:
                return
            cancelled = False
            for record in self._sides.values():
                try:
                    await record.owner.cancel_and_drain()
                except asyncio.CancelledError:
                    cancelled = True
            self._shutdown_complete = True
            self.dispose()
            if cancelled:
                raise asyncio.CancelledError


__all__ = ["ComparisonSideState", "GlueComparisonVM"]
