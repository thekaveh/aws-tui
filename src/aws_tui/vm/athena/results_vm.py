from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any, Literal

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
from aws_tui.domain.query import QueryContext, QueryExecutionDetail, QueryState, ResultColumn
from aws_tui.domain.s3_uri import parse_s3_uri
from aws_tui.vm.athena._errors import map_provider_error, map_unexpected_error
from aws_tui.vm.file_manager.pane_vm import PaneState
from aws_tui.vm.messages import OpenS3LocationRequest

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


@dataclass(eq=False)
class _PagerGeneration:
    generation: int
    execution_id: str | None = field(repr=False)
    columns: tuple[ResultColumn, ...] = field(default=(), repr=False)
    tasks: set[asyncio.Task[Any]] = field(default_factory=set, repr=False)
    retired: bool = False
    pager: TokenPagedComposition[ResultRow, str] = field(init=False, repr=False)
    load_more_command: AsyncRelayCommand = field(init=False, repr=False)


class AthenaResultsVM:
    def __init__(
        self,
        *,
        client: Any,
        context: QueryContext,
        hub: MessageHub[Message],
        dispatcher: Dispatcher,
    ) -> None:
        self._client = client
        self._context = context
        self._hub = hub
        self._disposed = False
        self._shutdown_started = False
        self._shutdown_complete = False
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
        self._is_loading_more = False
        self._workers: set[_PagerGeneration] = set()
        self._worker = self._make_worker(None, self._generation)
        self._pager = self._worker.pager

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
    def is_loading_more(self) -> bool:
        return self._is_loading_more

    @property
    def load_more_command(self) -> AsyncRelayCommand:
        return self._worker.load_more_command

    @property
    def status(self) -> ConstructionStatus:
        return self._inner.status

    @property
    def on_property_changed(self) -> rx.Observable[str]:
        return self._on_property_changed

    def construct(self) -> None:
        self._inner.construct()

    async def load(self, execution_id: str) -> None:
        if self._disposed or self._shutdown_started:
            return
        self._generation += 1
        generation = self._generation
        self._execution_id = execution_id
        self._columns = ()
        self._error_text = None
        worker = self._replace_worker(execution_id, generation)
        self._set_state(PaneState.LOADING)
        self._notify_all()
        task = self._track_current_task(worker)
        try:
            await worker.pager.refresh_command.execute_async()
        except _ResultColumnsChangedError:
            if not self._is_current(worker):
                return
            self._error_text = _COLUMN_ERROR
            self._set_state(PaneState.ERROR)
            self._notify("error_text")
            return
        except ProviderError as exc:
            if not self._is_current(worker):
                return
            self._state, self._error_text = map_provider_error(
                exc,
                fallback=_RESULTS_ERROR,
            )
            self._notify("state")
            self._notify("error_text")
            return
        except Exception:
            if not self._is_current(worker):
                return
            self._state, self._error_text = map_unexpected_error(
                fallback=_RESULTS_ERROR,
            )
            self._notify("state")
            self._notify("error_text")
            return
        finally:
            self._untrack_task(worker, task)
        if self._is_current(worker):
            self._notify_all()
            self._set_state(PaneState.IDLE if self.rows else PaneState.EMPTY)

    async def load_more(self) -> None:
        await self._worker.load_more_command.execute_async()

    async def open_s3_location(
        self,
        *,
        preferred_pane: Literal["left", "right"] = "left",
    ) -> bool:
        """Reload execution metadata and publish its authoritative S3 URI."""
        if self._disposed or self._shutdown_started or self._execution_id is None:
            return False
        generation = self._generation
        execution_id = self._execution_id
        try:
            detail = await self._client.get_query_execution(execution_id)
        except Exception:
            return False
        if (
            self._disposed
            or self._shutdown_started
            or generation != self._generation
            or execution_id != self._execution_id
            or detail.summary.ref.execution_id != execution_id
            or detail.summary.state is not QueryState.SUCCEEDED
            or not _execution_identity_belongs_to(detail, self._context)
            or parse_s3_uri(detail.output_location) is None
        ):
            return False
        ref = detail.summary.ref
        assert detail.output_location is not None
        self._hub.send(
            OpenS3LocationRequest(
                connection_name=ref.connection_name,
                region=ref.region,
                uri=detail.output_location,
                preferred_pane=preferred_pane,
                reveal_object=True,
            )
        )
        return True

    def set_context(self, context: QueryContext) -> None:
        self._context = context

    def clear(self) -> None:
        if self._disposed or self._shutdown_started:
            return
        self._generation += 1
        self._execution_id = None
        self._columns = ()
        self._error_text = None
        self._replace_worker(None, self._generation)
        self._state = PaneState.EMPTY
        self._notify_all()

    async def shutdown(self) -> None:
        if self._shutdown_complete:
            return
        self._shutdown_started = True
        self._generation += 1
        self._execution_id = None
        self._columns = ()
        self._error_text = None
        current = self._replace_worker(None, self._generation)
        self._state = PaneState.EMPTY
        self._notify_all()
        for worker in tuple(self._workers):
            self._retire_worker(worker)
        await self._drain_workers()
        self._worker = current
        self._pager = current.pager
        self._shutdown_complete = True

    def dispose(self) -> None:
        if self._disposed:
            return
        self._disposed = True
        self._generation += 1
        if not self._shutdown_started:
            self._execution_id = None
            self._columns = ()
            self._error_text = None
            current = self._replace_worker(None, self._generation)
            self._worker = current
            self._pager = current.pager
        for worker in tuple(self._workers):
            self._retire_worker(worker)
        self._on_property_changed.on_completed()
        self._on_property_changed.dispose()
        self._inner.dispose()

    async def _load_more(self, worker: _PagerGeneration) -> None:
        if not self._can_load_more(worker):
            return
        self._is_loading_more = True
        self._notify("is_loading_more")
        task = self._track_current_task(worker)
        try:
            await worker.pager.load_more_command.execute_async()
        except _ResultColumnsChangedError:
            if not self._is_current(worker):
                return
            self._error_text = _COLUMN_ERROR
            self._set_state(PaneState.ERROR)
            self._notify("error_text")
            return
        except ProviderError as exc:
            if not self._is_current(worker):
                return
            self._state, self._error_text = map_provider_error(
                exc,
                fallback=_RESULTS_ERROR,
            )
            self._notify("state")
            self._notify("error_text")
            return
        except Exception:
            if not self._is_current(worker):
                return
            self._state, self._error_text = map_unexpected_error(
                fallback=_RESULTS_ERROR,
            )
            self._notify("state")
            self._notify("error_text")
            return
        finally:
            self._untrack_task(worker, task)
            self._is_loading_more = False
            self._notify("is_loading_more")
        if not self._is_current(worker):
            return
        self._error_text = None
        self._notify("error_text")
        self._notify("rows")
        self._notify("rendered_rows")
        self._notify("has_more")
        self._set_state(PaneState.IDLE if self.rows else PaneState.EMPTY)

    def _can_load_more(self, worker: _PagerGeneration) -> bool:
        return (
            not self._disposed
            and not self._shutdown_started
            and self._is_current(worker)
            and worker.execution_id is not None
            and worker.pager.current_token is not None
        )

    def _make_worker(
        self,
        execution_id: str | None,
        generation: int,
    ) -> _PagerGeneration:
        worker = _PagerGeneration(generation, execution_id)

        async def fetch(token: str | None) -> tuple[list[ResultRow], str | None]:
            if execution_id is None:
                return [], None
            page = await self._client.get_results_page(
                execution_id,
                start_token=token,
            )
            if not self._is_current(worker):
                return [], None
            if token is None:
                worker.columns = page.columns
                self._columns = page.columns
            elif page.columns != worker.columns:
                raise _ResultColumnsChangedError
            return list(page.rows), page.next_token

        worker.pager = TokenPagedComposition(fetch)
        worker.load_more_command = (
            AsyncRelayCommand.builder()
            .predicate(lambda: self._can_load_more(worker))
            .triggers(self._on_property_changed)
            .task(lambda: self._load_more(worker))
            .build()
        )
        self._workers.add(worker)
        return worker

    def _replace_worker(
        self,
        execution_id: str | None,
        generation: int,
    ) -> _PagerGeneration:
        old_worker = self._worker
        worker = self._make_worker(execution_id, generation)
        self._worker = worker
        self._pager = worker.pager
        self._retire_worker(old_worker)
        return worker

    def _retire_worker(self, worker: _PagerGeneration) -> None:
        if worker.retired:
            return
        worker.retired = True
        worker.load_more_command.dispose()
        worker.pager.dispose()
        if not worker.tasks and worker is not self._worker:
            self._workers.discard(worker)

    def _track_current_task(
        self,
        worker: _PagerGeneration,
    ) -> asyncio.Task[Any] | None:
        task = asyncio.current_task()
        if task is not None:
            worker.tasks.add(task)
        return task

    def _untrack_task(
        self,
        worker: _PagerGeneration,
        task: asyncio.Task[Any] | None,
    ) -> None:
        if task is not None:
            worker.tasks.discard(task)
        if worker.retired and not worker.tasks and worker is not self._worker:
            self._workers.discard(worker)

    async def _drain_workers(self) -> None:
        current = asyncio.current_task()
        while True:
            tasks = {
                task
                for worker in self._workers
                for task in worker.tasks
                if task is not current and not task.done()
            }
            if not tasks:
                return
            await asyncio.gather(*tasks, return_exceptions=True)

    def _is_current(self, worker: _PagerGeneration) -> bool:
        return (
            worker is self._worker
            and worker.generation == self._generation
            and not worker.retired
            and not self._disposed
            and not self._shutdown_started
        )

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


def _execution_identity_belongs_to(
    detail: QueryExecutionDetail,
    expected: QueryContext,
) -> bool:
    ref = detail.summary.ref
    return (
        ref.connection_name == detail.context.connection_name
        and ref.region == detail.context.region
        and ref.workgroup == detail.context.workgroup
        and detail.context == expected
    )
