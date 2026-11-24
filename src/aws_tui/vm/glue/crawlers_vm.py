from __future__ import annotations

import asyncio
from typing import Any, cast

from vmx import (
    ConstructionStatus,
    Dispatcher,
    Message,
    MessageHub,
)

from aws_tui.domain.glue import GlueCrawlerDetail, GlueCrawlerSummary
from aws_tui.vm._component import IsolatedComponentVM
from aws_tui.vm.file_manager.pane_vm import PaneState
from aws_tui.vm.glue._guarded import guarded
from aws_tui.vm.glue._lifecycle import GlueOperationOwner
from aws_tui.vm.glue._pager import bounded_glue_pager
from aws_tui.vm.paging import BoundedTokenPagedComposition

_MAX_CRAWLER_ITEMS = 1_000


class GlueCrawlersVM(IsolatedComponentVM):
    def __init__(
        self,
        *,
        client: Any,
        hub: MessageHub[Message],
        dispatcher: Dispatcher,
        _operations: GlueOperationOwner | None = None,
    ) -> None:
        super().__init__(name="glue.crawlers", hub=hub, dispatcher=dispatcher)
        self._client = client
        self._hub: MessageHub[Message] = hub  # keeps the narrow type the helpers need
        self._shutdown_started = False
        self._shutdown_complete = False
        self._shutdown_lock = asyncio.Lock()
        self._operations = _operations or GlueOperationOwner()
        self._owns_operations = _operations is None
        self._crawler_generation = 0
        self._detail_generation = 0
        self._state_filter: str | None = None
        self._crawler_pager = self._make_crawler_pager()
        self._own(lambda: self._crawler_pager.dispose())
        self._selected_crawler_name: str | None = None
        self._crawler_detail: GlueCrawlerDetail | None = None
        self._state = PaneState.LOADING
        self._detail_state = PaneState.EMPTY
        self._error_text: str | None = None
        self._detail_error_text: str | None = None

    @property
    def crawlers(self) -> tuple[GlueCrawlerSummary, ...]:
        return tuple(self._crawler_pager.items)

    @property
    def selected_crawler_name(self) -> str | None:
        return self._selected_crawler_name

    @property
    def crawler_detail(self) -> GlueCrawlerDetail | None:
        return self._crawler_detail

    @property
    def state_filter(self) -> str | None:
        return self._state_filter

    @property
    def has_more_crawlers(self) -> bool:
        return self._crawler_pager.has_more

    @property
    def limit_reached(self) -> bool:
        return self._crawler_pager.limit_reached

    @property
    def state(self) -> PaneState:
        return self._state

    @property
    def detail_state(self) -> PaneState:
        return self._detail_state

    @property
    def error_text(self) -> str | None:
        return self._error_text

    @property
    def detail_error_text(self) -> str | None:
        return self._detail_error_text

    def construct(self) -> None:
        if self._is_alive():
            super().construct()

    def _on_dispose(self) -> None:
        if self._owns_operations:
            self._operations.close()
        self._invalidate_operations()

    async def shutdown(self) -> None:
        self._begin_shutdown()
        async with self._shutdown_lock:
            if self._shutdown_complete:
                return
            if self._owns_operations:
                await self._operations.cancel_and_drain()
            self._shutdown_complete = True

    async def setup(self) -> None:
        if not self._is_alive():
            return
        await self._reload_crawlers()

    async def set_state_filter(self, state: str | None) -> None:
        if not self._is_alive() or state == self._state_filter:
            return
        self._state_filter = state
        self._notify("state_filter")
        await self._reload_crawlers()

    async def load_more_crawlers(self) -> None:
        if not self._is_alive() or not self.has_more_crawlers:
            return
        generation = self._crawler_generation
        ok, _ = await guarded(
            self._crawler_pager.load_more_command.execute_async,
            hub=self._hub,
            operation="list_crawlers",
            is_current=lambda: generation == self._crawler_generation,
            on_failure=self._fail_list,
        )
        if not ok:
            return
        self._notify("crawlers")
        self._notify("has_more_crawlers")

    async def select_crawler(self, name: str) -> None:
        if not self._is_alive() or not any(crawler.name == name for crawler in self.crawlers):
            return
        self._detail_generation += 1
        generation = self._detail_generation
        self._selected_crawler_name = name
        self._crawler_detail = None
        self._detail_error_text = None
        self._notify("selected_crawler_name")
        self._notify("crawler_detail")
        self._set_detail_state(PaneState.LOADING)
        ok, detail = await guarded(
            lambda: self._operations.run(lambda: self._client.get_crawler(name)),
            hub=self._hub,
            operation="get_crawler",
            is_current=lambda: generation == self._detail_generation,
            on_failure=self._fail_detail,
        )
        if not ok:
            return
        self._crawler_detail = detail
        self._notify("crawler_detail")
        self._set_detail_state(PaneState.IDLE)

    async def _reload_crawlers(self) -> None:
        self._crawler_generation += 1
        generation = self._crawler_generation
        old_pager = self._crawler_pager
        self._crawler_pager = self._make_crawler_pager()
        old_pager.dispose()
        self._detail_generation += 1
        self._selected_crawler_name = None
        self._crawler_detail = None
        self._error_text = None
        self._detail_error_text = None
        self._notify("crawlers")
        self._notify("selected_crawler_name")
        self._notify("crawler_detail")
        self._set_detail_state(PaneState.EMPTY)
        self._set_state(PaneState.LOADING)
        ok, _ = await guarded(
            self._crawler_pager.refresh_command.execute_async,
            hub=self._hub,
            operation="list_crawlers",
            is_current=lambda: generation == self._crawler_generation,
            on_failure=self._fail_list,
        )
        if not ok:
            return
        self._notify("crawlers")
        self._notify("has_more_crawlers")
        self._set_state(PaneState.IDLE if self.crawlers else PaneState.EMPTY)

    def _make_crawler_pager(
        self,
    ) -> BoundedTokenPagedComposition[GlueCrawlerSummary, str]:
        generation = self._crawler_generation
        state_filter = self._state_filter

        async def list_page(token: str | None) -> tuple[list[GlueCrawlerSummary], str | None]:
            return cast(
                tuple[list[GlueCrawlerSummary], str | None],
                await self._client.list_crawlers_page(start_token=token, state=state_filter),
            )

        return bounded_glue_pager(
            self._operations,
            list_page,
            is_current=lambda: generation == self._crawler_generation,
            cycle_message="Glue repeated a crawler continuation token",
            max_items=_MAX_CRAWLER_ITEMS,
        )

    def _fail_list(self, state: PaneState, text: str | None) -> None:
        self._error_text = text
        self._set_state(state)

    def _fail_detail(self, state: PaneState, text: str | None) -> None:
        self._detail_error_text = text
        self._set_detail_state(state)

    def _set_state(self, state: PaneState) -> None:
        if self._state == state:
            return
        self._state = state
        self._notify("state")

    def _set_detail_state(self, state: PaneState) -> None:
        if self._detail_state == state:
            return
        self._detail_state = state
        self._notify("detail_state")

    def _begin_shutdown(self) -> None:
        if self._shutdown_started:
            return
        self._shutdown_started = True
        if self._owns_operations:
            self._operations.close()
        self._invalidate_operations()

    def _invalidate_operations(self) -> None:
        self._crawler_generation += 1
        self._detail_generation += 1

    def _is_alive(self) -> bool:
        return (
            self.status is not ConstructionStatus.DISPOSED
            and not self._shutdown_started
            and self._operations.accepting
        )

    def _notify(self, property_name: str) -> None:
        if self._is_alive():
            self._publish(property_name)


__all__ = ["GlueCrawlersVM"]
