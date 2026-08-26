from __future__ import annotations

import asyncio
from typing import Any

import reactivex as rx
from vmx import ComponentVMOf, Message, MessageHub, PropertyChangedMessage
from vmx.collections.token_paged_composition import TokenPagedComposition
from vmx.lifecycle.status import ConstructionStatus
from vmx.services.dispatcher import Dispatcher

from aws_tui.domain.filesystem import ProviderError
from aws_tui.domain.glue import GlueCrawlerDetail, GlueCrawlerSummary
from aws_tui.vm._observable import ObserverSafeSubject
from aws_tui.vm._token_paging import reject_token_cycles
from aws_tui.vm.file_manager.pane_vm import PaneState
from aws_tui.vm.glue._errors import map_provider_error, map_unexpected_error
from aws_tui.vm.glue._lifecycle import GlueOperationOwner, GlueOperationSuperseded
from aws_tui.vm.service_diagnostics import report_unexpected_service_error


class GlueCrawlersVM:
    def __init__(
        self,
        *,
        client: Any,
        hub: MessageHub[Message],
        dispatcher: Dispatcher,
        _operations: GlueOperationOwner | None = None,
    ) -> None:
        self._client = client
        self._hub = hub
        self._disposed = False
        self._shutdown_started = False
        self._shutdown_complete = False
        self._shutdown_lock = asyncio.Lock()
        self._operations = _operations or GlueOperationOwner()
        self._owns_operations = _operations is None
        self._on_property_changed = ObserverSafeSubject[str]()
        self._inner: ComponentVMOf[None] = (
            ComponentVMOf[None]
            .builder()
            .name("glue.crawlers")
            .model(None)
            .services(hub, dispatcher)
            .build()
        )
        self._crawler_generation = 0
        self._detail_generation = 0
        self._state_filter: str | None = None
        self._crawler_pager = self._make_crawler_pager()
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
        return self._crawler_pager.current_token is not None

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

    @property
    def status(self) -> ConstructionStatus:
        return self._inner.status

    @property
    def on_property_changed(self) -> rx.Observable[str]:
        return self._on_property_changed

    def construct(self) -> None:
        if not self._is_alive():
            return
        self._inner.construct()

    def dispose(self) -> None:
        if self._disposed:
            return
        self._disposed = True
        if self._owns_operations:
            self._operations.close()
        self._invalidate_operations()
        self._crawler_pager.dispose()
        self._on_property_changed.on_completed()
        self._on_property_changed.dispose()
        self._inner.dispose()

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
        try:
            await self._crawler_pager.load_more_command.execute_async()
        except GlueOperationSuperseded:
            return
        except ProviderError as exc:
            if generation != self._crawler_generation:
                return
            state, self._error_text = map_provider_error(exc)
            self._set_state(state)
            return
        except Exception as exc:
            if generation != self._crawler_generation:
                return
            state, self._error_text = map_unexpected_error(exc)
            report_unexpected_service_error(
                self._hub, service="glue", operation="list_crawlers", error=exc
            )
            self._set_state(state)
            return
        if generation == self._crawler_generation:
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
        try:
            detail = await self._operations.run(lambda: self._client.get_crawler(name))
        except GlueOperationSuperseded:
            return
        except ProviderError as exc:
            if generation != self._detail_generation:
                return
            state, self._detail_error_text = map_provider_error(exc)
            self._set_detail_state(state)
            return
        except Exception as exc:
            if generation != self._detail_generation:
                return
            state, self._detail_error_text = map_unexpected_error(exc)
            report_unexpected_service_error(
                self._hub, service="glue", operation="get_crawler", error=exc
            )
            self._set_detail_state(state)
            return
        if generation != self._detail_generation:
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
        try:
            await self._crawler_pager.refresh_command.execute_async()
        except GlueOperationSuperseded:
            return
        except ProviderError as exc:
            if generation != self._crawler_generation:
                return
            state, self._error_text = map_provider_error(exc)
            self._set_state(state)
            return
        except Exception as exc:
            if generation != self._crawler_generation:
                return
            state, self._error_text = map_unexpected_error(exc)
            report_unexpected_service_error(
                self._hub, service="glue", operation="list_crawlers", error=exc
            )
            self._set_state(state)
            return
        if generation != self._crawler_generation:
            return
        self._notify("crawlers")
        self._notify("has_more_crawlers")
        self._set_state(PaneState.IDLE if self.crawlers else PaneState.EMPTY)

    def _make_crawler_pager(self) -> TokenPagedComposition[GlueCrawlerSummary, str]:
        generation = self._crawler_generation
        state_filter = self._state_filter

        async def fetch(token: str | None) -> tuple[list[GlueCrawlerSummary], str | None]:
            rows, next_token = await self._operations.run(
                lambda: self._client.list_crawlers_page(
                    start_token=token,
                    state=state_filter,
                )
            )
            if generation != self._crawler_generation:
                return [], None
            return rows, next_token

        return TokenPagedComposition(
            reject_token_cycles(
                fetch,
                message="Glue repeated a crawler continuation token",
            )
        )

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
        return not self._disposed and not self._shutdown_started and self._operations.accepting

    def _notify(self, property_name: str) -> None:
        if not self._is_alive():
            return
        self._hub.send(PropertyChangedMessage.create(self, "glue.crawlers", property_name))
        self._on_property_changed.on_next(property_name)


__all__ = ["GlueCrawlersVM"]
