from __future__ import annotations

import asyncio
from typing import Any, cast

from reactivex.abc import DisposableBase
from vmx import (
    NULL_MESSAGE_HUB,
    AsyncResourceError,
    AsyncResourceReady,
    AsyncResourceVM,
    ConstructionStatus,
    Dispatcher,
    Message,
    MessageHub,
)

from aws_tui.domain.filesystem import ProviderError
from aws_tui.domain.glue import GlueCrawlerDetail, GlueCrawlerSummary
from aws_tui.vm._component import IsolatedComponentVM
from aws_tui.vm.file_manager.pane_vm import PaneState
from aws_tui.vm.glue._errors import map_provider_error, map_unexpected_error
from aws_tui.vm.glue._guarded import guarded
from aws_tui.vm.glue._lifecycle import GlueOperationOwner, GlueOperationSuperseded
from aws_tui.vm.glue._pager import bounded_glue_pager
from aws_tui.vm.paging import BoundedTokenPagedComposition
from aws_tui.vm.service_diagnostics import report_unexpected_service_error

_MAX_CRAWLER_ITEMS = 1_000


def _classify_detail_error(error: BaseException) -> tuple[PaneState, str | None]:
    if isinstance(error, GlueOperationSuperseded):
        return PaneState.LOADING, None  # shutdown ended the read; the old code also stayed LOADING
    if isinstance(error, ProviderError):
        return map_provider_error(error)
    return map_unexpected_error(error)


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
        self._state_filter: str | None = None
        self._crawler_pager = self._make_crawler_pager()
        self._own(lambda: self._crawler_pager.dispose())
        self._selected_crawler_name: str | None = None
        self._state = PaneState.LOADING
        self._error_text: str | None = None
        self._detail_subscription: DisposableBase | None = None
        self._detail_resource = self._new_detail()
        self._own(self._release_detail)  # reads the current resource when it runs

    @property
    def crawlers(self) -> tuple[GlueCrawlerSummary, ...]:
        return tuple(self._crawler_pager.items)

    @property
    def selected_crawler_name(self) -> str | None:
        return self._selected_crawler_name

    @property
    def crawler_detail(self) -> GlueCrawlerDetail | None:
        current = self._detail_resource.state
        return current.value if isinstance(current, AsyncResourceReady) else None

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
        return self._detail_view()[0]

    @property
    def error_text(self) -> str | None:
        return self._error_text

    @property
    def detail_error_text(self) -> str | None:
        return self._detail_view()[1]

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
        self._reset_detail()  # drops the old detail and cancels an older read
        self._selected_crawler_name = name
        self._notify("selected_crawler_name")
        self._notify("crawler_detail")
        await self._detail_resource.load()

    async def _reload_crawlers(self) -> None:
        self._crawler_generation += 1
        generation = self._crawler_generation
        old_pager = self._crawler_pager
        self._crawler_pager = self._make_crawler_pager()
        old_pager.dispose()
        self._reset_detail()
        self._selected_crawler_name = None
        self._error_text = None
        self._notify("crawlers")
        self._notify("selected_crawler_name")
        self._notify("crawler_detail")
        self._notify("detail_state")
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

    def _set_state(self, state: PaneState) -> None:
        if self._state == state:
            return
        self._state = state
        self._notify("state")

    def _begin_shutdown(self) -> None:
        if self._shutdown_started:
            return
        self._shutdown_started = True
        if self._owns_operations:
            self._operations.close()
        self._invalidate_operations()

    def _invalidate_operations(self) -> None:
        self._crawler_generation += 1

    def _new_detail(self) -> AsyncResourceVM[GlueCrawlerDetail]:
        resource = AsyncResourceVM[GlueCrawlerDetail](
            name="glue.crawlers.detail",
            loader=self._load_detail,
            hub=NULL_MESSAGE_HUB,  # nothing reaches the shared hub; the VM sends its own messages
            dispatcher=self._dispatcher,
        )  # retention stays DISCARD_PREVIOUS: a new crawler never shows the old detail
        resource.construct()
        self._detail_subscription = resource.property_changed.subscribe(
            on_next=self._on_detail_state
        )
        return resource

    def _release_detail(self) -> None:
        subscription, self._detail_subscription = self._detail_subscription, None
        if subscription is not None:
            subscription.dispose()
        self._detail_resource.dispose()  # cancels a read in flight

    def _reset_detail(self) -> None:
        self._release_detail()
        self._detail_resource = self._new_detail()

    async def _load_detail(self) -> GlueCrawlerDetail:
        name = self._selected_crawler_name
        if name is None:
            raise RuntimeError("no crawler selected")  # unreachable: a selection precedes a load
        detail: GlueCrawlerDetail = await self._operations.run(
            lambda: self._client.get_crawler(name)
        )
        return detail

    def _on_detail_state(self, property_name: str) -> None:
        if property_name != "state":
            return
        current = self._detail_resource.state
        if isinstance(current, AsyncResourceError) and not isinstance(
            current.error, ProviderError | GlueOperationSuperseded
        ):
            report_unexpected_service_error(
                self._hub, service="glue", operation="get_crawler", error=current.error
            )
        if isinstance(current, AsyncResourceReady):
            self._notify("crawler_detail")
        self._notify("detail_state")

    def _detail_view(self) -> tuple[PaneState, str | None]:
        if self._selected_crawler_name is None:
            return PaneState.EMPTY, None
        current = self._detail_resource.state
        if isinstance(current, AsyncResourceReady):
            return PaneState.IDLE, None
        if isinstance(current, AsyncResourceError):
            return _classify_detail_error(current.error)
        return PaneState.LOADING, None

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
