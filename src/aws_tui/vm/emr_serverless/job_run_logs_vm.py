"""JobRunLogsVM — owns the LEFT-half-bottom logs pane state.

Lifecycle is target-driven: the parent ``EmrServerlessPageVM``
calls ``set_target(app_id, run_id)`` whenever the user picks a
run; that flushes the loaded lines and transitions to ``IDLE``
without touching the network. ``load()`` is invoked explicitly
by the user (Enter in the logs pane); it streams the selected
log file's lines through the active ``LogFilter`` and surfaces
matches in batched ``PropertyChangedMessage`` broadcasts.
"""

from __future__ import annotations

import asyncio
from enum import StrEnum
from typing import TYPE_CHECKING

from vmx import ComponentVMOf, Message, MessageHub, PropertyChangedMessage
from vmx.services.dispatcher import Dispatcher

from aws_tui.domain.emr_logs import (
    DEFAULT_LOG_FILTER,
    LogFile,
    LogFileKind,
    LogFilter,
)

if TYPE_CHECKING:
    import aioboto3


class LogsState(StrEnum):
    EMPTY_TARGET = "EMPTY_TARGET"  # no run selected yet
    IDLE = "IDLE"  # target set, not loaded; press Enter
    LOADING = "LOADING"
    READY = "READY"
    NO_LOG_CONFIG = "NO_LOG_CONFIG"  # job had no s3MonitoringConfiguration
    NO_FILES = "NO_FILES"  # config set but no log files yet (likely too early)
    ERROR = "ERROR"
    TRUNCATED = "TRUNCATED"  # ``READY`` variant that hit the byte cap


_MAX_RAW_BYTES: int = 100 * 1024 * 1024
_MAX_MATCHED_LINES: int = 5000


class JobRunLogsVM:
    """Reactive VM for the EMR job-run logs pane."""

    def __init__(
        self,
        *,
        session: aioboto3.Session,
        region_name: str | None,
        hub: MessageHub[Message],
        dispatcher: Dispatcher,
    ) -> None:
        self._session = session
        self._region_name = region_name
        self._hub: MessageHub[Message] = hub
        self._dispatcher: Dispatcher = dispatcher
        self._inner: ComponentVMOf[None] = (
            ComponentVMOf[None]
            .builder()
            .name("emr.job_run_logs")
            .model(None)
            .services(hub, dispatcher)
            .build()
        )
        # Target identity
        self._application_id: str | None = None
        self._job_run_id: str | None = None
        self._log_uri: str | None = None
        # Loaded state
        self._state: LogsState = LogsState.EMPTY_TARGET
        self._error_text: str | None = None
        self._available_files: tuple[LogFile, ...] = ()
        self._current_file: LogFile | None = None
        self._lines: tuple[str, ...] = ()
        self._bytes_read: int = 0
        self._lines_scanned: int = 0
        self._filter: LogFilter = DEFAULT_LOG_FILTER
        # In-flight cancellation token
        self._load_task: asyncio.Task[None] | None = None
        # Cache: key=(app_id, run_id, file_key, filter_hash)
        self._cache: dict[tuple[str, str, str, int], tuple[str, ...]] = {}

    # ── Properties (snapshot accessors) ─────────────────────────────────────

    @property
    def state(self) -> LogsState:
        return self._state

    @property
    def error_text(self) -> str | None:
        return self._error_text

    @property
    def available_files(self) -> tuple[LogFile, ...]:
        return self._available_files

    @property
    def current_file(self) -> LogFile | None:
        return self._current_file

    @property
    def lines(self) -> tuple[str, ...]:
        return self._lines

    @property
    def bytes_read(self) -> int:
        return self._bytes_read

    @property
    def lines_scanned(self) -> int:
        return self._lines_scanned

    @property
    def filter(self) -> LogFilter:
        return self._filter

    @property
    def application_id(self) -> str | None:
        return self._application_id

    @property
    def job_run_id(self) -> str | None:
        return self._job_run_id

    # ── Public mutators ────────────────────────────────────────────────────

    def set_target(self, app_id: str | None, run_id: str | None, log_uri: str | None) -> None:
        """Update the target run; flush loaded state. NOT a fetch."""
        if (
            self._application_id == app_id
            and self._job_run_id == run_id
            and self._log_uri == log_uri
        ):
            return
        self._cancel_load()
        self._application_id = app_id
        self._job_run_id = run_id
        self._log_uri = log_uri
        self._available_files = ()
        self._current_file = None
        self._lines = ()
        self._bytes_read = 0
        self._lines_scanned = 0
        self._error_text = None
        if app_id is None or run_id is None:
            self._set_state(LogsState.EMPTY_TARGET)
        elif log_uri is None:
            self._set_state(LogsState.NO_LOG_CONFIG)
        else:
            self._set_state(LogsState.IDLE)
        self._notify_all()

    def set_filter(self, filter_: LogFilter) -> None:
        if filter_ == self._filter:
            return
        self._filter = filter_
        self._hub.send(PropertyChangedMessage.create(self, "emr.job_run_logs", "filter"))

    def select_log_file(self, kind: LogFileKind) -> None:
        """Pick a file from ``available_files`` by kind. No-op if
        not loaded yet or no file with that kind exists."""
        match = next((f for f in self._available_files if f.kind is kind), None)
        if match is None or match == self._current_file:
            return
        self._current_file = match
        self._lines = ()
        self._bytes_read = 0
        self._lines_scanned = 0
        self._hub.send(PropertyChangedMessage.create(self, "emr.job_run_logs", "current_file"))
        self._hub.send(PropertyChangedMessage.create(self, "emr.job_run_logs", "lines"))

    # ── Lifecycle ──────────────────────────────────────────────────────────

    def construct(self) -> None:
        self._inner.construct()

    def dispose(self) -> None:
        self._cancel_load()
        self._inner.dispose()

    # ── Internal ───────────────────────────────────────────────────────────

    def _set_state(self, new_state: LogsState) -> None:
        if self._state == new_state:
            return
        self._state = new_state
        self._hub.send(PropertyChangedMessage.create(self, "emr.job_run_logs", "state"))

    def _notify_all(self) -> None:
        for prop in ("state", "lines", "current_file", "available_files", "filter"):
            self._hub.send(PropertyChangedMessage.create(self, "emr.job_run_logs", prop))

    def _cancel_load(self) -> None:
        task = self._load_task
        self._load_task = None
        if task is not None and not task.done():
            task.cancel()


__all__ = ["JobRunLogsVM", "LogsState"]
