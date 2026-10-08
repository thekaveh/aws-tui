"""Context-bound, bounded Athena table listings."""

from __future__ import annotations

import asyncio
from typing import Any

import reactivex as rx
from vmx import Message, MessageHub, PropertyChangedMessage

from aws_tui.domain.data_catalog import TableSummary
from aws_tui.domain.filesystem import ProviderError
from aws_tui.domain.query import QueryContext
from aws_tui.vm._observable import ObserverSafeSubject, send_value_free
from aws_tui.vm.athena._domain_validation import (
    optional_exact_datetime,
    optional_exact_string,
    optional_non_empty_exact_string,
    valid_query_context,
    valid_table_ref,
)
from aws_tui.vm.athena._errors import map_provider_error, map_unexpected_error
from aws_tui.vm.athena._pager_compat import PagerCollectionLimitError, SnapshotTokenPager
from aws_tui.vm.file_manager.pane_vm import PaneState
from aws_tui.vm.service_diagnostics import report_unexpected_service_error

_TABLES_ERROR = "Athena tables request failed"
_MAX_TABLES = 1_000


class AthenaTablesVM:
    def __init__(self, *, client: Any, context: QueryContext, hub: MessageHub[Message]) -> None:
        self._client = client
        self._context = context
        self._hub = hub
        self._disposed = False
        self._shutdown_started = False
        self._generation = 0
        self._context_revision = 0
        self._state = PaneState.EMPTY
        self._error_text: str | None = None
        self._is_loading_more = False
        self._tasks: set[asyncio.Task[Any]] = set()
        self._request_task: asyncio.Task[None] | None = None
        self._on_property_changed = ObserverSafeSubject[str]()
        self._pager = self._make_pager(context, self._generation)

    @property
    def context(self) -> QueryContext:
        return self._context

    @property
    def context_revision(self) -> int:
        """Identify a replaced listing, even when its context values are unchanged."""
        return self._context_revision

    @property
    def items(self) -> tuple[TableSummary, ...]:
        return tuple(self._pager.items)

    @property
    def state(self) -> PaneState:
        return self._state

    @property
    def error_text(self) -> str | None:
        return self._error_text

    @property
    def has_more(self) -> bool:
        return self._ready() and self._pager.has_more

    @property
    def limit_reached(self) -> bool:
        return self._pager.limit_reached

    @property
    def is_loading_more(self) -> bool:
        return self._is_loading_more

    @property
    def on_property_changed(self) -> rx.Observable[str]:
        return self._on_property_changed.observable

    async def refresh(self) -> None:
        if self._disposed or self._shutdown_started:
            return
        self._replace_pager(self._context)
        if not self._ready():
            self._notify_all()
            return
        self._state = PaneState.LOADING
        self._notify_all()
        await self._request(refresh=True)

    async def load_more(self) -> None:
        if not self.has_more or self._request_task is not None:
            return
        self._is_loading_more = True
        self._notify("is_loading_more")
        await self._request(refresh=False)

    def replace_context(self, context: QueryContext, *, notify: bool = True) -> None:
        if self._disposed or self._shutdown_started:
            return
        self._context_revision += 1
        self._replace_pager(context)
        if notify:
            self._notify_all()

    async def shutdown(self) -> None:
        if not self._shutdown_started:
            self._shutdown_started = True
            if not self._disposed:
                self._replace_pager(self._context)
                self._notify_all()
        current = asyncio.current_task()
        tasks = tuple(task for task in self._tasks if task is not current)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    def dispose(self) -> None:
        if self._disposed:
            return
        self._disposed = True
        self._pager.dispose()
        for task in tuple(self._tasks):
            task.cancel()
        self._on_property_changed.on_completed()
        self._on_property_changed.dispose()

    def _ready(self) -> bool:
        return (
            not self._disposed
            and not self._shutdown_started
            and valid_query_context(self._context)
            and all(self._context.cache_key)
        )

    def _replace_pager(self, context: QueryContext) -> None:
        self._generation += 1
        self._pager.dispose()
        if self._request_task is not None:
            self._request_task.cancel()
        self._request_task = None
        self._context = context
        self._pager = self._make_pager(context, self._generation)
        self._state = PaneState.EMPTY
        self._error_text = None
        self._is_loading_more = False

    def _make_pager(
        self, context: QueryContext, generation: int
    ) -> SnapshotTokenPager[TableSummary, str]:
        async def fetch(token: str | None) -> tuple[list[TableSummary], str | None]:
            # VMx may finish its command awaiter before a cancelled provider
            # returns. Keep the actual fetch task owned until shutdown drains it.
            task = asyncio.current_task()
            if task is not None:
                self._tasks.add(task)
                task.add_done_callback(self._tasks.discard)
            rows, next_token = await self._client.list_tables_page(
                context.catalog,
                context.database,
                workgroup=context.workgroup,
                start_token=token,
            )
            if generation != self._generation or not self._ready():
                return [], None
            if not isinstance(rows, (list, tuple)) or not optional_non_empty_exact_string(
                next_token
            ):
                raise ValueError("Invalid Athena table page")
            if len(rows) > _MAX_TABLES:
                raise PagerCollectionLimitError("Athena collection safety limit exceeded")
            for row in rows:
                if not self._valid_table(row, context):
                    raise ValueError("Invalid Athena table identity")
            return list(rows), next_token

        pager: SnapshotTokenPager[TableSummary, str] = SnapshotTokenPager(
            fetch, max_items=_MAX_TABLES
        )
        pager.restore((), None)
        return pager

    @staticmethod
    def _valid_table(row: object, context: QueryContext) -> bool:
        if type(row) is not TableSummary or not valid_table_ref(row.ref):
            return False
        ref = row.ref
        return (
            bool(ref.table_name)
            and ref.connection_name == context.connection_name
            and ref.region == context.region
            and ref.catalog_name == context.catalog
            and ref.database_name == context.database
            and optional_exact_string(row.description)
            and optional_exact_string(row.owner)
            and optional_exact_string(row.table_type)
            and optional_exact_datetime(row.created_at)
            and optional_exact_datetime(row.updated_at)
        )

    async def _request(self, *, refresh: bool) -> None:
        pager, generation = self._pager, self._generation

        def current() -> bool:
            return self._ready() and generation == self._generation and pager is self._pager

        async def run() -> None:
            try:
                command = pager.refresh_command if refresh else pager.load_more_command
                await command.execute_async()
                if current():
                    self._state = PaneState.IDLE if self.items else PaneState.EMPTY
                    self._error_text = None
            except PagerCollectionLimitError:
                if current():
                    pager.restore(pager.items, None, limit_reached=True)
                    self._state = PaneState.IDLE if self.items else PaneState.EMPTY
                    self._error_text = None
            except ProviderError as exc:
                if current():
                    self._state, self._error_text = map_provider_error(exc, fallback=_TABLES_ERROR)
            except Exception as exc:
                if current():
                    report_unexpected_service_error(
                        self._hub, service="athena", operation="list_tables", error=exc
                    )
                    self._state, self._error_text = map_unexpected_error(fallback=_TABLES_ERROR)
            finally:
                if current():
                    self._is_loading_more = False
                    self._notify_all()

        task = asyncio.create_task(run())
        self._request_task = task
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        try:
            await task
        except asyncio.CancelledError:
            if current():
                raise
        finally:
            if self._request_task is task:
                self._request_task = None

    def _notify_all(self) -> None:
        for name in (
            "context",
            "items",
            "state",
            "error_text",
            "has_more",
            "limit_reached",
            "is_loading_more",
        ):
            self._notify(name)

    def _notify(self, name: str) -> None:
        if self._disposed:
            return
        send_value_free(self._hub, PropertyChangedMessage.create(self, "athena.tables", name))
        self._on_property_changed.on_next(name)


__all__ = ["AthenaTablesVM"]
