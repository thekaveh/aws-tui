from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import reactivex as rx
from reactivex.subject import Subject
from vmx import (
    AsyncRelayCommand,
    ComponentVMOf,
    Message,
    MessageHub,
    PropertyChangedMessage,
)
from vmx.collections.token_paged_composition import TokenPagedComposition
from vmx.lifecycle.status import ConstructionStatus
from vmx.services.dispatcher import Dispatcher

from aws_tui.domain.filesystem import ProviderError
from aws_tui.domain.query import ResultColumn
from aws_tui.vm.athena._errors import map_provider_error, map_unexpected_error
from aws_tui.vm.file_manager.pane_vm import PaneState

_RESULTS_ERROR = "Athena results request failed"
_COLUMN_ERROR = "Athena returned inconsistent result columns"

ResultRow = tuple[str | None, ...]


@dataclass(frozen=True, slots=True)
class RenderedResultCell:
    """Display metadata that keeps null distinct without repr data leakage."""

    text: str = field(repr=False)
    is_null: bool


class _ResultColumnsChangedError(Exception):
    pass


class AthenaResultsVM:
    def __init__(
        self,
        *,
        client: Any,
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
            .name("athena.results")
            .model(None)
            .services(hub, dispatcher)
            .build()
        )
        self._generation = 0
        self._execution_id: str | None = None
        self._columns: tuple[ResultColumn, ...] = ()
        self._state = PaneState.EMPTY
        self._error_text: str | None = None
        self._pager = self._make_pager(None, self._generation)
        self._load_more_command: AsyncRelayCommand = (
            AsyncRelayCommand.builder()
            .predicate(self._can_load_more)
            .triggers(self._on_property_changed)
            .task(self._load_more)
            .build()
        )

    @property
    def execution_id(self) -> str | None:
        return self._execution_id

    @property
    def columns(self) -> tuple[ResultColumn, ...]:
        return self._columns

    @property
    def rows(self) -> tuple[ResultRow, ...]:
        return tuple(self._pager.items)

    @property
    def rendered_rows(self) -> tuple[tuple[RenderedResultCell, ...], ...]:
        return tuple(
            tuple(
                RenderedResultCell(
                    text="NULL" if value is None else value,
                    is_null=value is None,
                )
                for value in row
            )
            for row in self.rows
        )

    @property
    def has_more(self) -> bool:
        return self._execution_id is not None and self._pager.current_token is not None

    @property
    def state(self) -> PaneState:
        return self._state

    @property
    def error_text(self) -> str | None:
        return self._error_text

    @property
    def load_more_command(self) -> AsyncRelayCommand:
        return self._load_more_command

    @property
    def status(self) -> ConstructionStatus:
        return self._inner.status

    @property
    def on_property_changed(self) -> rx.Observable[str]:
        return self._on_property_changed

    def construct(self) -> None:
        self._inner.construct()

    async def load(self, execution_id: str) -> None:
        if self._disposed:
            return
        self._generation += 1
        generation = self._generation
        self._execution_id = execution_id
        self._columns = ()
        self._error_text = None
        self._replace_pager(execution_id, generation)
        self._set_state(PaneState.LOADING)
        self._notify_all()
        try:
            await self._pager.refresh_command.execute_async()
        except _ResultColumnsChangedError:
            if generation != self._generation:
                return
            self._error_text = _COLUMN_ERROR
            self._set_state(PaneState.ERROR)
            self._notify("error_text")
            return
        except ProviderError as exc:
            if generation != self._generation:
                return
            self._state, self._error_text = map_provider_error(
                exc,
                fallback=_RESULTS_ERROR,
            )
            self._notify("state")
            self._notify("error_text")
            return
        except Exception:
            if generation != self._generation:
                return
            self._state, self._error_text = map_unexpected_error(
                fallback=_RESULTS_ERROR,
            )
            self._notify("state")
            self._notify("error_text")
            return
        if generation != self._generation:
            return
        self._notify_all()
        self._set_state(PaneState.IDLE if self.rows else PaneState.EMPTY)

    async def load_more(self) -> None:
        await self._load_more_command.execute_async()

    def clear(self) -> None:
        if self._disposed:
            return
        self._generation += 1
        self._execution_id = None
        self._columns = ()
        self._error_text = None
        self._replace_pager(None, self._generation)
        self._state = PaneState.EMPTY
        self._notify_all()

    def dispose(self) -> None:
        if self._disposed:
            return
        self._disposed = True
        self._generation += 1
        self._load_more_command.dispose()
        self._pager.dispose()
        self._on_property_changed.on_completed()
        self._on_property_changed.dispose()
        self._inner.dispose()

    async def _load_more(self) -> None:
        generation = self._generation
        if not self._can_load_more():
            return
        self._error_text = None
        self._notify("error_text")
        try:
            await self._pager.load_more_command.execute_async()
        except _ResultColumnsChangedError:
            if generation != self._generation:
                return
            self._error_text = _COLUMN_ERROR
            self._set_state(PaneState.ERROR)
            self._notify("error_text")
            return
        except ProviderError as exc:
            if generation != self._generation:
                return
            self._state, self._error_text = map_provider_error(
                exc,
                fallback=_RESULTS_ERROR,
            )
            self._notify("state")
            self._notify("error_text")
            return
        except Exception:
            if generation != self._generation:
                return
            self._state, self._error_text = map_unexpected_error(
                fallback=_RESULTS_ERROR,
            )
            self._notify("state")
            self._notify("error_text")
            return
        if generation != self._generation:
            return
        self._notify("rows")
        self._notify("rendered_rows")
        self._notify("has_more")
        self._set_state(PaneState.IDLE if self.rows else PaneState.EMPTY)

    def _can_load_more(self) -> bool:
        return (
            not self._disposed
            and self._execution_id is not None
            and self._pager.current_token is not None
        )

    def _make_pager(
        self,
        execution_id: str | None,
        generation: int,
    ) -> TokenPagedComposition[ResultRow, str]:
        async def fetch(token: str | None) -> tuple[list[ResultRow], str | None]:
            if execution_id is None:
                return [], None
            page = await self._client.get_results_page(
                execution_id,
                start_token=token,
            )
            if generation != self._generation or self._disposed:
                return [], None
            if token is None:
                self._columns = page.columns
            elif page.columns != self._columns:
                raise _ResultColumnsChangedError
            return list(page.rows), page.next_token

        return TokenPagedComposition(fetch)

    def _replace_pager(self, execution_id: str | None, generation: int) -> None:
        old_pager = self._pager
        self._pager = self._make_pager(execution_id, generation)
        old_pager.dispose()

    def _notify_all(self) -> None:
        for property_name in (
            "execution_id",
            "columns",
            "rows",
            "rendered_rows",
            "has_more",
            "state",
            "error_text",
        ):
            self._notify(property_name)

    def _set_state(self, state: PaneState) -> None:
        if self._state == state:
            return
        self._state = state
        self._notify("state")

    def _notify(self, property_name: str) -> None:
        if self._disposed:
            return
        self._hub.send(
            PropertyChangedMessage.create(
                self,
                "athena.results",
                property_name,
            )
        )
        self._on_property_changed.on_next(property_name)


__all__ = ["AthenaResultsVM", "RenderedResultCell"]
