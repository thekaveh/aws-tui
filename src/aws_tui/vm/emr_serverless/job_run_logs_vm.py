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
from collections import OrderedDict
from enum import StrEnum

import reactivex as rx
from reactivex.subject import Subject
from vmx import ComponentVMOf, Message, MessageHub, PropertyChangedMessage
from vmx.services.dispatcher import Dispatcher

from aws_tui.domain.emr_logs import (
    DEFAULT_LOG_FILTER,
    EmrServerlessLogsClient,
    LogFile,
    LogFileKind,
    LogFilter,
    build_run_prefix,
    parse_log_uri,
)
from aws_tui.domain.filesystem import ProviderError
from aws_tui.vm.emr_serverless._errors import map_provider_error


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
#: Cap on the LRU response cache. User feedback (post-PR-#92):
#: "we need to make sure those logs get pulled into a temp space
#: and get disposed every once in a while so they don't
#: accumulate". A 5-entry LRU keeps the user's recent navigation
#: snappy (immediate cache hit on flipping back to the previous
#: file / run) while bounding the in-memory footprint — each
#: entry holds at most :data:`_MAX_MATCHED_LINES` decoded strings.
#: Older entries fall off the LRU AND the cache is cleared
#: entirely on application switch (see :meth:`set_target`).
_CACHE_MAX_ENTRIES: int = 5


class JobRunLogsVM:
    """Reactive VM for the EMR job-run logs pane."""

    def __init__(
        self,
        *,
        client: EmrServerlessLogsClient,
        hub: MessageHub[Message],
        dispatcher: Dispatcher,
    ) -> None:
        self._client: EmrServerlessLogsClient = client
        self._hub: MessageHub[Message] = hub
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
        self._disposed: bool = False
        # Per-VM Observable (round-3 §9.bis.11 / PR #103 retirement
        # path): fires the name of the property that just changed,
        # scoped to THIS VM instance. The logs-pane view subscribes
        # here instead of filtering shared MessageHub events by
        # ``sender_object``.
        self._on_property_changed: Subject[str] = Subject()
        # LRU response cache: key=(app_id, run_id, file_key, filter_hash);
        # value=(lines, truncated). Capped at :data:`_CACHE_MAX_ENTRIES`
        # so recent navigation stays snappy without unbounded memory
        # growth. Cleared on application switch in :meth:`set_target`.
        self._cache: OrderedDict[tuple[str, str, str, int], tuple[tuple[str, ...], bool]] = (
            OrderedDict()
        )

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
    def on_property_changed(self) -> rx.Observable[str]:
        """Per-VM-instance Observable scoped to THIS logs VM. PR
        #103 retirement path — Views subscribing here are immune to
        cross-VM `state` collisions on the shared hub."""
        return self._on_property_changed

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
        # Drop the cache on application switch — entries for the
        # previous application can't be revisited from this UI
        # session in a useful way and keeping them around just
        # bloats memory. Run-switch within the same application
        # keeps the cache so flipping between recent runs stays
        # snappy.
        if app_id != self._application_id:
            self._cache.clear()
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
        self._notify("filter")

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
        self._notify("current_file")
        self._notify("lines")

    # ── Network actions ───────────────────────────────────────────────────

    async def load(self) -> None:
        """Fetch + stream the selected log file.

        View-side ``exclusive=True, group="emr-logs"`` cancels any
        in-flight load before re-entering, so a second call while
        ``state is LOADING`` is the cancellation path — the prior
        worker is being torn down. Do NOT early-return on LOADING
        here: that would strand the pane on the LOADING placeholder
        because the prior task's ``CancelledError`` raises out
        WITHOUT resetting state, then this fresh worker would no-op
        on the stale flag. The fresh worker re-establishes target
        identity below before any state mutation.
        """
        if self._application_id is None or self._job_run_id is None or self._log_uri is None:
            return
        # Capture target identity BEFORE any await — a concurrent
        # set_target(new_app, new_run, new_uri) landing mid-stream
        # must not let the previous run's lines pollute the new
        # target's state nor poison the LRU cache under the prior
        # key. The load worker and set_target run in different
        # contexts (Textual worker vs synchronous VM call), and
        # set_target deliberately leaves the load() worker alive
        # (cancellation is a view-side concern via worker group
        # ``emr-logs``).
        target = (self._application_id, self._job_run_id, self._log_uri)
        self._set_state(LogsState.LOADING)
        self._lines = ()
        self._bytes_read = 0
        self._lines_scanned = 0
        try:
            loc = parse_log_uri(self._log_uri)
            run_prefix = build_run_prefix(loc, self._application_id, self._job_run_id)
            files = await self._client.list_files(
                bucket=loc.bucket,
                run_prefix=run_prefix,
            )
            if (self._application_id, self._job_run_id, self._log_uri) != target:
                return  # target changed mid-flight; drop the stale list
            self._available_files = tuple(files)
            self._notify("available_files")
            if not files:
                self._set_state(LogsState.NO_FILES)
                return
            if self._current_file is None:
                self._current_file = next(
                    (f for f in files if f.kind is LogFileKind.DRIVER_STDERR),
                    next(
                        (f for f in files if f.kind is LogFileKind.DRIVER_STDOUT),
                        files[0],
                    ),
                )
                self._notify("current_file")
            truncated = False
            cache_key = (
                self._application_id,
                self._job_run_id,
                self._current_file.key,
                hash(
                    (
                        self._filter.patterns,
                        self._filter.mode,
                        self._filter.case_insensitive,
                    )
                ),
            )
            if cache_key in self._cache:
                # LRU bump: move the freshly-accessed entry to the
                # "newest" end so eviction targets the least-recently
                # used entry when the cache fills up.
                self._cache.move_to_end(cache_key)
                cached_lines, cached_truncated = self._cache[cache_key]
                self._lines = cached_lines
                self._notify("lines")
                self._set_state(LogsState.TRUNCATED if cached_truncated else LogsState.READY)
                return
            buffered: list[str] = []
            async for chunk in self._client.stream(
                log_file=self._current_file,
                bucket=loc.bucket,
                max_bytes=_MAX_RAW_BYTES,
                filter_=self._filter,
            ):
                # Re-check target on EVERY chunk — set_target runs
                # in a different worker group (emr-select-run /
                # emr-select-app) and does NOT cancel emr-logs, so
                # the stream can keep feeding chunks AFTER the user
                # moved on. Without this guard, ``_notify("lines")``
                # would paint the OLD run's lines under the NEW
                # run's pane header. (Post-loop guard only catches
                # the cache-write — the per-chunk paints already
                # shipped to the view.)
                if (self._application_id, self._job_run_id, self._log_uri) != target:
                    return
                buffered.extend(chunk.lines)
                if len(buffered) > _MAX_MATCHED_LINES:
                    buffered = buffered[-_MAX_MATCHED_LINES:]
                self._lines = tuple(buffered)
                self._bytes_read = chunk.bytes_read
                self._lines_scanned = chunk.lines_scanned
                self._notify("lines")
                self._notify("progress")
                truncated = chunk.truncated
            if (self._application_id, self._job_run_id, self._log_uri) != target:
                # Target changed during stream — drop the cache write
                # (would key under the wrong target) and the state
                # transition (caller already moved on).
                return
            self._cache[cache_key] = (self._lines, truncated)
            # LRU eviction: drop the oldest entry until back under cap.
            while len(self._cache) > _CACHE_MAX_ENTRIES:
                self._cache.popitem(last=False)
            self._set_state(LogsState.TRUNCATED if truncated else LogsState.READY)
        except ProviderError as exc:
            # Identity guard on the error path too — set_target is
            # synchronous and does NOT cancel the in-flight load
            # worker. Without this check the OLD target's error
            # text would stomp the NEW target's state, leaving the
            # logs pane stuck on ERROR with an error message
            # describing the prior run's failure.
            if (self._application_id, self._job_run_id, self._log_uri) != target:
                return
            new_state, self._error_text = map_provider_error(exc)
            # Re-map the file-pane states the EMR mapper returns to a
            # logs-specific state. UNREACHABLE / AUTH_REQUIRED /
            # FORBIDDEN / ERROR all collapse to LogsState.ERROR for
            # the pane — error_text carries the detail.
            _ = new_state
            self._set_state(LogsState.ERROR)
        except asyncio.CancelledError:
            # User switched panes or runs — leave state where it is
            # so the placeholder reflects the most recent intent.
            raise
        except Exception as exc:  # defensive
            # Same identity guard as the ProviderError branch above.
            if (self._application_id, self._job_run_id, self._log_uri) != target:
                return
            self._error_text = f"unexpected error: {exc}"
            self._set_state(LogsState.ERROR)

    # ── Lifecycle ──────────────────────────────────────────────────────────

    def construct(self) -> None:
        self._inner.construct()

    def dispose(self) -> None:
        if self._disposed:
            return
        self._disposed = True
        # Drop the response cache so a recycled VM (e.g. test
        # harnesses or future content-host reuse) doesn't carry
        # stale entries forward. In-flight ``load()`` workers are
        # owned by the view (Textual's ``run_worker(group="emr-logs")``
        # auto-cancels on widget unmount) — the VM holds no task
        # handle of its own.
        self._cache.clear()
        self._on_property_changed.on_completed()
        self._on_property_changed.dispose()
        self._inner.dispose()

    # ── Internal ───────────────────────────────────────────────────────────

    def _set_state(self, new_state: LogsState) -> None:
        if self._state == new_state:
            return
        self._state = new_state
        self._notify("state")

    def _notify(self, prop: str) -> None:
        """Emit a PropertyChanged event on BOTH the shared hub AND
        the per-VM-instance Observable (round-3 / PR #103 retirement
        path)."""
        self._hub.send(PropertyChangedMessage.create(self, "emr.job_run_logs", prop))
        self._on_property_changed.on_next(prop)

    def _notify_all(self) -> None:
        for prop in ("state", "lines", "current_file", "available_files", "filter"):
            self._notify(prop)


__all__ = ["JobRunLogsVM", "LogsState"]
