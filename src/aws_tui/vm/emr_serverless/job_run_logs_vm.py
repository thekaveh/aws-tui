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
import re
import time
from collections import OrderedDict, deque
from collections.abc import Iterator
from contextlib import aclosing
from dataclasses import dataclass
from enum import StrEnum
from time import monotonic
from typing import Literal, cast

import reactivex as rx
from vmx import ComponentVMOf, Message, MessageHub, PropertyChangedMessage
from vmx.services.dispatcher import Dispatcher

from aws_tui.domain.emr_cloudwatch_logs import (
    CloudWatchLogEvent,
    CloudWatchLogSnapshot,
    CloudWatchLogStream,
    cloudwatch_location,
    safe_cloudwatch_error,
)
from aws_tui.domain.emr_logs import (
    DEFAULT_LOG_FILTER,
    EmrServerlessLogsClientProtocol,
    FilterMode,
    LogFile,
    LogFileKind,
    LogFilter,
    LogFilterTimeoutError,
    build_run_prefix,
    parse_log_uri,
)
from aws_tui.domain.emr_serverless import CloudWatchLogConfiguration
from aws_tui.domain.filesystem import (
    AuthRequiredError,
    NotFoundError,
    PermissionDeniedError,
    ProviderError,
    ProviderUnreachableError,
    ThrottledError,
    ValidationError,
)
from aws_tui.infra.redaction import redact_text
from aws_tui.vm._observable import ObserverSafeSubject, send_value_free
from aws_tui.vm.emr_serverless._errors import map_provider_error
from aws_tui.vm.file_manager.pane_vm import PaneState
from aws_tui.vm.operation_owner import OperationOwner, OperationSuperseded
from aws_tui.vm.service_diagnostics import report_unexpected_service_error


class LogSource(StrEnum):
    S3 = "s3"
    CLOUDWATCH = "cloudwatch"


class LogSourceState(StrEnum):
    CONFIGURED = "configured"
    UNAVAILABLE = "unavailable"
    UNKNOWN = "unknown"
    ACCESS_DENIED = "access denied"


@dataclass(frozen=True, slots=True)
class LogSourceStatus:
    source: LogSource
    state: LogSourceState
    detail: str


FailureKind = Literal[
    "auth_required",
    "access_denied",
    "throttled",
    "unreachable",
    "not_found",
    "invalid",
    "limit",
    "unexpected",
]
_CW_EVENT_CAP = 5_000
_CW_BYTE_CAP = 4 * 1024 * 1024
_CW_ID_CAP = 20_000
_LINE_BREAK = re.compile(r"\r\n|[\n\r\v\f\x1c-\x1e\x85\u2028\u2029]")
_LIMIT_ERRORS = frozenset(
    {
        "CloudWatch log page limit exceeded",
        "CloudWatch log stream limit exceeded",
        "CloudWatch log read limit exceeded",
        "CloudWatch repeated a continuation token",
        "CloudWatch follow duplicate-id limit exceeded",
    }
)
_sleep = asyncio.sleep


def _now_ms() -> int:
    return int(time.time() * 1000)


def _message_lines(message: str) -> Iterator[str]:
    start = 0
    for match in _LINE_BREAK.finditer(message):
        yield message[start : match.start()]
        start = match.end()
    if start < len(message):
        yield message[start:]


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
        client: EmrServerlessLogsClientProtocol,
        hub: MessageHub[Message],
        dispatcher: Dispatcher,
    ) -> None:
        self._client: EmrServerlessLogsClientProtocol = client
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
        self._generation = 0
        self._cloudwatch: CloudWatchLogConfiguration | None = None
        self._metadata_known = True
        self._run_created_at_ms = 0
        self._selected_source: LogSource | None = None
        self._sources: tuple[LogSourceStatus, ...] = ()
        self._available_streams: tuple[CloudWatchLogStream, ...] = ()
        self._current_stream: CloudWatchLogStream | None = None
        self._raw_events: deque[CloudWatchLogEvent] = deque()
        self._following = False
        self._last_successful_read_at_ms: int | None = None
        self._last_event_at_ms: int | None = None
        self._buffer_capped = False
        self._failure_kind: FailureKind | None = None
        # Loaded state
        self._state: LogsState = LogsState.EMPTY_TARGET
        self._failure_state: PaneState | None = None
        self._error_text: str | None = None
        self._available_files: tuple[LogFile, ...] = ()
        self._current_file: LogFile | None = None
        self._lines: tuple[str, ...] = ()
        self._bytes_read: int = 0
        self._lines_scanned: int = 0
        # Total matches the stream reported, which exceeds len(self._lines)
        # once the line cap trims. Reporting the trimmed length as "matches"
        # gave a wrong answer to the question the user's filter asked.
        self._matched_count: int = 0
        self._filter: LogFilter = DEFAULT_LOG_FILTER
        self._disposed: bool = False
        self._operations = OperationOwner()
        # Per-VM Observable (round-3 §9.bis.11 / PR #103 retirement
        # path): fires the name of the property that just changed,
        # scoped to THIS VM instance. The logs-pane view subscribes
        # here instead of filtering shared MessageHub events by
        # ``sender_object``.
        self._on_property_changed = ObserverSafeSubject[str]()
        # LRU response cache: key=(app_id, run_id, bucket, file_key, size, patterns,
        # mode, case_insensitive); value=(lines, truncated, bytes_read,
        # lines_scanned). Use the
        # raw filter triple instead of hash(triple) — hash() collapses
        # structurally-distinct filters to a single int, so two
        # different filter configurations can collide and the second
        # would silently hit a cache entry built from the first,
        # serving the wrong filtered line set. LogFilter is a frozen
        # dataclass so the tuple is hashable directly. Cleared on
        # application switch in :meth:`set_target`.
        self._cache: OrderedDict[
            tuple[str, str, str, str, int | None, tuple[str, ...], FilterMode, bool],
            tuple[tuple[str, ...], bool, int, int, int],
        ] = OrderedDict()

    # ── Properties (snapshot accessors) ─────────────────────────────────────

    @property
    def state(self) -> LogsState:
        return self._state

    @property
    def error_text(self) -> str | None:
        return self._error_text

    @property
    def credential_recovery_state(self) -> PaneState:
        """Return the typed terminal state used by credential recovery."""
        if self._state is LogsState.ERROR:
            return self._failure_state or PaneState.ERROR
        if self._state in {
            LogsState.EMPTY_TARGET,
            LogsState.NO_LOG_CONFIG,
            LogsState.NO_FILES,
        }:
            return PaneState.EMPTY
        return PaneState.IDLE

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
    def matched_count(self) -> int:
        """Total matches found, including any the display cap dropped."""
        return self._matched_count

    @property
    def matches_capped(self) -> bool:
        """True when the retained lines are only the tail of the matches."""
        return self._matched_count > len(self._lines)

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

    @property
    def sources(self) -> tuple[LogSourceStatus, ...]:
        return self._sources

    @property
    def selected_source(self) -> LogSource | None:
        return self._selected_source

    @property
    def available_streams(self) -> tuple[CloudWatchLogStream, ...]:
        return self._available_streams

    @property
    def current_stream(self) -> CloudWatchLogStream | None:
        return self._current_stream

    @property
    def cloudwatch_configuration(self) -> CloudWatchLogConfiguration | None:
        return self._cloudwatch

    @property
    def following(self) -> bool:
        return self._following

    @property
    def can_follow(self) -> bool:
        return (
            self._selected_source is LogSource.CLOUDWATCH
            and self._cloudwatch is not None
            and self._cloudwatch.enabled is True
            and not self._disposed
            and self._operations.accepting
        )

    @property
    def last_successful_read_at_ms(self) -> int | None:
        return self._last_successful_read_at_ms

    @property
    def last_event_at_ms(self) -> int | None:
        return self._last_event_at_ms

    @property
    def buffer_capped(self) -> bool:
        return self._buffer_capped

    @property
    def failure_kind(self) -> FailureKind | None:
        return self._failure_kind

    def _invalidate(self) -> int:
        self._generation += 1
        self._operations.cancel()
        self._following = False
        self._notify("following")
        return self._generation

    def _current(self, generation: int) -> bool:
        return not self._disposed and self._operations.accepting and generation == self._generation

    def _clear_loaded(self) -> None:
        self._available_files = ()
        self._current_file = None
        self._available_streams = ()
        self._current_stream = None
        self._raw_events.clear()
        self._lines = ()
        self._bytes_read = self._lines_scanned = self._matched_count = 0
        self._last_successful_read_at_ms = self._last_event_at_ms = None
        self._buffer_capped = False
        self._error_text = self._failure_state = self._failure_kind = None

    def set_target(
        self,
        app_id: str | None,
        run_id: str | None,
        log_uri: str | None,
        *,
        cloudwatch: CloudWatchLogConfiguration | None = None,
        run_created_at_ms: int = 0,
        metadata_known: bool = True,
    ) -> None:
        """Capture exact metadata without fetching; equal detail polls are a no-op."""
        if (
            self._application_id,
            self._job_run_id,
            self._log_uri,
            self._cloudwatch,
            self._run_created_at_ms,
            self._metadata_known,
        ) == (app_id, run_id, log_uri, cloudwatch, run_created_at_ms, metadata_known):
            return
        self._invalidate()
        if app_id != self._application_id:
            self._cache.clear()
        self._application_id, self._job_run_id, self._log_uri = app_id, run_id, log_uri
        self._cloudwatch, self._run_created_at_ms, self._metadata_known = (
            cloudwatch,
            run_created_at_ms,
            metadata_known,
        )
        self._clear_loaded()
        self._sources = (
            (
                LogSourceStatus(
                    LogSource.S3,
                    LogSourceState.UNKNOWN
                    if not metadata_known
                    else LogSourceState.CONFIGURED
                    if log_uri
                    else LogSourceState.UNAVAILABLE,
                    "" if log_uri or not metadata_known else "not configured",
                ),
                LogSourceStatus(
                    LogSource.CLOUDWATCH,
                    LogSourceState.UNKNOWN
                    if not metadata_known or (cloudwatch is not None and cloudwatch.enabled is None)
                    else LogSourceState.CONFIGURED
                    if cloudwatch is not None and cloudwatch.enabled is True
                    else LogSourceState.UNAVAILABLE,
                    "disabled"
                    if cloudwatch is not None and cloudwatch.enabled is False
                    else "not configured"
                    if cloudwatch is None and metadata_known
                    else "",
                ),
            )
            if app_id is not None and run_id is not None
            else ()
        )
        self._selected_source = (
            LogSource.S3
            if log_uri
            else LogSource.CLOUDWATCH
            if cloudwatch is not None and cloudwatch.enabled is True
            else None
        )
        self._set_state(
            LogsState.EMPTY_TARGET
            if not app_id or not run_id
            else LogsState.IDLE
            if self._selected_source is not None
            or any(s.state is LogSourceState.UNKNOWN for s in self._sources)
            else LogsState.NO_LOG_CONFIG
        )
        self._notify_all()

    def select_source(self, source: LogSource) -> None:
        if source == self._selected_source or not self.source_selectable(source):
            return
        self._invalidate()
        self._selected_source = source
        self._clear_loaded()
        self._set_state(LogsState.IDLE)
        self._notify_all()

    def source_selectable(self, source: LogSource) -> bool:
        return (
            bool(self._log_uri)
            if source is LogSource.S3
            else self._cloudwatch is not None and self._cloudwatch.enabled is True
        )

    def select_cloudwatch_stream(self, name: str) -> None:
        stream = next((s for s in self._available_streams if s.name == name), None)
        if stream is None or stream == self._current_stream:
            return
        self._invalidate()
        self._current_stream = stream
        self._raw_events.clear()
        self._lines = ()
        self._last_successful_read_at_ms = self._last_event_at_ms = None
        self._buffer_capped = False
        self._bytes_read = self._lines_scanned = self._matched_count = 0
        self._set_state(LogsState.IDLE)
        self._notify_all()

    def set_filter(self, filter_: LogFilter) -> None:
        if filter_ == self._filter and self._error_text != str(LogFilterTimeoutError()):
            return
        prior_filter = self._filter
        self._filter = filter_
        if self._selected_source is LogSource.CLOUDWATCH:
            try:
                self._project_cloudwatch()
            except LogFilterTimeoutError as error:
                self._filter = prior_filter
                generation = self._invalidate()
                self._cloudwatch_failure(error, generation)
                return
            if self._error_text == str(LogFilterTimeoutError()):
                self._error_text = self._failure_state = self._failure_kind = None
                self._set_state(
                    LogsState.READY if self._current_stream is not None else self._state
                )
        else:
            self._invalidate()
        self._notify("filter")

    def select_log_file_key(self, key: str) -> None:
        """Pick one exact object, preserving executor and retry identity."""
        match = next((file for file in self._available_files if file.key == key), None)
        if match is None or match == self._current_file:
            return
        self._invalidate()
        self._current_file = match
        self._lines = ()
        self._bytes_read = 0
        self._lines_scanned = 0
        self._matched_count = 0
        self._notify("current_file")
        self._notify("lines")

    # ── Network actions ───────────────────────────────────────────────────

    async def load(
        self,
        *,
        use_cache: bool = True,
        preferred_file_key: str | None = None,
        preferred_cloudwatch_stream_name: str | None = None,
    ) -> None:
        generation = self._invalidate()
        try:
            await self._operations.run(
                lambda: (
                    self._load_cloudwatch(generation, preferred_cloudwatch_stream_name)
                    if self._selected_source is LogSource.CLOUDWATCH
                    else self._load(
                        use_cache=use_cache,
                        preferred_file_key=preferred_file_key,
                        generation=generation,
                    )
                )
            )
        except OperationSuperseded:
            return
        except asyncio.CancelledError:
            caller = asyncio.current_task()
            if caller is not None and caller.cancelling():
                raise
            if self._current(generation):
                raise
        finally:
            if self._current(generation) and self._state is LogsState.LOADING:
                self._set_state(LogsState.READY if self._lines else LogsState.IDLE)

    async def _load(
        self, *, use_cache: bool, preferred_file_key: str | None, generation: int
    ) -> None:
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
        # Target changes cancel the owned operation; guards also reject a
        # provider that suppresses cancellation and completes late.
        target = (self._application_id, self._job_run_id, self._log_uri)
        # Do NOT eagerly wipe self._lines / _bytes_read / _lines_scanned
        # here — if list_files / stream raise (transient
        # ProviderUnreachableError on ``r`` for example) we'd land in
        # ERROR with an empty _lines, losing the 4000 lines the user
        # was looking at a moment ago. Mirror the round-35 load_more
        # discipline: preserve prior READY state until we KNOW we
        # have new content to write. The cache-hit fast path and
        # per-chunk writes below overwrite atomically.
        self._set_state(LogsState.LOADING)
        try:
            loc = parse_log_uri(self._log_uri)
            run_prefix = build_run_prefix(loc, self._application_id, self._job_run_id)
            files = await self._client.list_files(
                bucket=loc.bucket,
                run_prefix=run_prefix,
            )
            if (
                not self._current(generation)
                or (self._application_id, self._job_run_id, self._log_uri) != target
            ):
                return  # target changed mid-flight; drop the stale list
            # Past list_files, about to commit to a fresh stream —
            # NOW it's safe to drop the prior accumulator.
            self._lines = ()
            self._bytes_read = 0
            self._lines_scanned = 0
            # ``_matched_count`` belongs in this reset too. Without it every
            # cache-miss reload (``r``, a filter edit, Shift+F) accumulated on
            # top of the previous run's total, so a file with one match
            # rendered "showing last 1 of 3 matches" -- and the inflated value
            # was then written into the LRU cache.
            self._matched_count = 0
            self._available_files = tuple(files)
            self._notify("available_files")
            if not files:
                if self._current_file is not None:
                    self._current_file = None
                    self._notify("current_file")
                self._failure_state = None
                self._source_status(LogSource.S3, LogSourceState.UNAVAILABLE, "not created yet")
                self._set_state(LogsState.NO_FILES)
                return
            current_key = (
                preferred_file_key
                if preferred_file_key is not None
                else self._current_file.key
                if self._current_file is not None
                else None
            )
            selected_file = next((file for file in files if file.key == current_key), None)
            if selected_file is None:
                selected_file = next(
                    (f for f in files if f.kind is LogFileKind.DRIVER_STDERR),
                    next(
                        (f for f in files if f.kind is LogFileKind.HIVE_DRIVER_STDERR),
                        next(
                            (f for f in files if f.kind is LogFileKind.DRIVER_STDOUT),
                            files[0],
                        ),
                    ),
                )
            if selected_file != self._current_file:
                self._current_file = selected_file
                self._notify("current_file")
            assert self._current_file is not None
            truncated = False
            cache_key = (
                self._application_id,
                self._job_run_id,
                loc.bucket,
                self._current_file.key,
                self._current_file.size,
                self._filter.patterns,
                self._filter.mode,
                self._filter.case_insensitive,
            )
            if use_cache and cache_key in self._cache:
                # LRU bump: move the freshly-accessed entry to the
                # "newest" end so eviction targets the least-recently
                # used entry when the cache fills up.
                self._cache.move_to_end(cache_key)
                (
                    cached_lines,
                    cached_truncated,
                    cached_bytes,
                    cached_scanned,
                    cached_matched,
                ) = self._cache[cache_key]
                self._lines = cached_lines
                self._bytes_read = cached_bytes
                self._lines_scanned = cached_scanned
                self._matched_count = cached_matched
                self._notify("lines")
                self._notify("matched_count")
                self._notify("progress")
                self._failure_state = None
                self._source_status(LogSource.S3, LogSourceState.CONFIGURED)
                self._set_state(LogsState.TRUNCATED if cached_truncated else LogsState.READY)
                return
            buffered: list[str] = []
            # ``aclosing``: the per-chunk guard below returns out of this loop
            # by design, in addition to owned cancellation. A bare ``async for``
            # previously abandoned the
            # generator on every job-run switch, stranding an open S3
            # connection until the GC hook collected it.
            async with aclosing(
                self._client.stream(
                    log_file=self._current_file,
                    bucket=loc.bucket,
                    max_bytes=_MAX_RAW_BYTES,
                    filter_=self._filter,
                )
            ) as source:
                async for chunk in source:
                    # Re-check target on EVERY chunk — a provider can
                    # suppress owned cancellation after set_target runs
                    # in another worker group and keep feeding chunks
                    # after the user moved on. Without this guard, ``_notify("lines")``
                    # would paint the OLD run's lines under the NEW
                    # run's pane header. (Post-loop guard only catches
                    # the cache-write — the per-chunk paints already
                    # shipped to the view.)
                    if (
                        not self._current(generation)
                        or (self._application_id, self._job_run_id, self._log_uri) != target
                    ):
                        return
                    buffered.extend(chunk.lines)
                    self._matched_count += chunk.matched_count
                    if len(buffered) > _MAX_MATCHED_LINES:
                        buffered = buffered[-_MAX_MATCHED_LINES:]
                    self._lines = tuple(buffered)
                    self._bytes_read = chunk.bytes_read
                    self._lines_scanned = chunk.lines_scanned
                    self._notify("lines")
                    self._notify("matched_count")
                    self._notify("progress")
                    truncated = chunk.truncated
            if (
                not self._current(generation)
                or (self._application_id, self._job_run_id, self._log_uri) != target
            ):
                # Target changed during stream — drop the cache write
                # (would key under the wrong target) and the state
                # transition (caller already moved on).
                return
            self._cache[cache_key] = (
                self._lines,
                truncated,
                self._bytes_read,
                self._lines_scanned,
                self._matched_count,
            )
            # LRU eviction: drop the oldest entry until back under cap.
            while len(self._cache) > _CACHE_MAX_ENTRIES:
                self._cache.popitem(last=False)
            self._failure_state = None
            self._source_status(LogSource.S3, LogSourceState.CONFIGURED)
            self._set_state(LogsState.TRUNCATED if truncated else LogsState.READY)
        except ProviderError as exc:
            # Identity guard on the error path too — set_target is
            # synchronous; cancellation-resistant providers can still complete
            # late. Without this check the OLD target's error
            # text would stomp the NEW target's state, leaving the
            # logs pane stuck on ERROR with an error message
            # describing the prior run's failure.
            if (
                not self._current(generation)
                or (self._application_id, self._job_run_id, self._log_uri) != target
            ):
                return
            self._failure_state, self._error_text = map_provider_error(exc)
            self._source_status(
                LogSource.S3,
                LogSourceState.ACCESS_DENIED
                if isinstance(exc, PermissionDeniedError)
                else LogSourceState.UNKNOWN,
            )
            # Re-map the file-pane states the EMR mapper returns to a
            # logs-specific state. UNREACHABLE / AUTH_REQUIRED /
            # FORBIDDEN / ERROR all collapse to LogsState.ERROR for
            # the pane — error_text carries the detail.
            self._set_state(LogsState.ERROR)
        except asyncio.CancelledError:
            # User switched panes or runs — leave state where it is
            # so the placeholder reflects the most recent intent.
            raise
        except Exception as exc:  # defensive
            # Same identity guard as the ProviderError branch above.
            if (
                not self._current(generation)
                or (self._application_id, self._job_run_id, self._log_uri) != target
            ):
                return
            self._source_status(LogSource.S3, LogSourceState.UNKNOWN)
            self._error_text = redact_text(f"unexpected error: {exc}")
            self._failure_state = PaneState.ERROR
            report_unexpected_service_error(
                self._hub, service="emr-serverless", operation="load_job_logs", error=exc
            )
            self._set_state(LogsState.ERROR)

    def _cloudwatch_status(self, state: LogSourceState, detail: str = "") -> None:
        self._source_status(LogSource.CLOUDWATCH, state, detail)

    def _source_status(self, source: LogSource, state: LogSourceState, detail: str = "") -> None:
        self._sources = tuple(
            LogSourceStatus(row.source, state, detail) if row.source is source else row
            for row in self._sources
        )
        self._notify("sources")

    def _cloudwatch_failure(self, error: Exception, generation: int) -> None:
        if not self._current(generation):
            return
        safe = error if isinstance(error, LogFilterTimeoutError) else safe_cloudwatch_error(error)
        self._failure_kind = "unexpected"
        for cls, kind in (
            (AuthRequiredError, "auth_required"),
            (PermissionDeniedError, "access_denied"),
            (ThrottledError, "throttled"),
            (ProviderUnreachableError, "unreachable"),
            (NotFoundError, "not_found"),
            (ValidationError, "invalid"),
        ):
            if isinstance(safe, cls):
                self._failure_kind = cast("FailureKind", kind)
                break
        if isinstance(error, ProviderError) and str(error) in _LIMIT_ERRORS:
            safe = ProviderError(str(error))
            self._failure_kind = "limit"
        self._error_text = str(safe)
        self._failure_state, _ = map_provider_error(safe)
        self._cloudwatch_status(
            LogSourceState.ACCESS_DENIED
            if self._failure_kind == "access_denied"
            else LogSourceState.UNAVAILABLE
            if self._failure_kind == "not_found"
            else LogSourceState.UNKNOWN,
            "not created yet" if self._failure_kind == "not_found" else "",
        )
        if self._failure_kind == "unexpected":
            report_unexpected_service_error(
                self._hub,
                service="emr-serverless",
                operation="load_cloudwatch_logs",
                error=ProviderError("CloudWatch log read failed"),
            )
        self._set_state(
            LogsState.NO_FILES if self._failure_kind == "not_found" else LogsState.ERROR
        )
        self._notify_all()

    def _prepare_events(
        self, events: tuple[CloudWatchLogEvent, ...]
    ) -> tuple[deque[CloudWatchLogEvent], bool]:
        retained = deque(
            sorted(events, key=lambda e: (e.timestamp_ms, e.ingestion_time_ms, e.event_id))
        )
        size = sum(len(e.message.encode("utf-8")) for e in retained)
        capped = False
        while len(retained) > _CW_EVENT_CAP or size > _CW_BYTE_CAP:
            size -= len(retained.popleft().message.encode("utf-8"))
            capped = True
        return retained, capped

    def _project_cloudwatch(self) -> None:
        display: deque[tuple[str, int]] = deque()
        size = scanned = matched = 0
        deadline = monotonic() + 0.1
        for event in self._raw_events:
            for line in _message_lines(event.message):
                scanned += 1
                if self._filter.mode is FilterMode.MATCH and monotonic() >= deadline:
                    raise LogFilterTimeoutError
                if not self._filter.matches(line, deadline=deadline):
                    continue
                matched += 1
                n = len(line.encode("utf-8")) + 1
                display.append((line, n))
                size += n
                while len(display) > _MAX_MATCHED_LINES or size - 1 > _CW_BYTE_CAP:
                    size -= display.popleft()[1]
        self._lines = tuple(line for line, _ in display)
        self._lines_scanned, self._matched_count = scanned, matched
        self._bytes_read = sum(len(e.message.encode("utf-8")) for e in self._raw_events)
        self._buffer_capped = self._buffer_capped or matched > len(display)
        self._notify("lines")
        self._notify("progress")
        self._notify("buffer_capped")

    async def _load_cloudwatch(
        self, generation: int, preferred: str | None = None
    ) -> CloudWatchLogSnapshot | None:
        configuration = self._cloudwatch
        if (
            not self._current(generation)
            or not self.can_follow
            or configuration is None
            or self._application_id is None
            or self._job_run_id is None
        ):
            return None
        self._set_state(LogsState.LOADING)
        try:
            group, _ = cloudwatch_location(configuration, self._application_id, self._job_run_id)
            streams = await self._client.list_cloudwatch_streams(
                configuration=configuration,
                application_id=self._application_id,
                job_run_id=self._job_run_id,
            )
            if not self._current(generation):
                return None
            selected_name = (
                preferred
                if preferred is not None
                else self._current_stream.name
                if self._current_stream is not None
                else None
            )
            selected = (
                next((row for row in streams if row.name == selected_name), None)
                if selected_name is not None
                else next(iter(streams), None)
            )
            self._available_streams = streams
            self._current_stream = selected
            self._notify("available_streams")
            self._notify("current_stream")
            if selected is None:
                self._cloudwatch_status(LogSourceState.UNAVAILABLE, "not created yet")
                self._set_state(LogsState.NO_FILES)
                return None
            end = max(self._run_created_at_ms, _now_ms())
            snapshot = await self._client.read_cloudwatch_events(
                log_group_name=group,
                stream_name=selected.name,
                start_time_ms=self._run_created_at_ms,
                end_time_ms=end,
            )
            prepared, capped = self._prepare_events(snapshot.events)
            if not self._current(generation):
                return None
            self._raw_events, self._buffer_capped = prepared, capped
            self._project_cloudwatch()
            self._last_successful_read_at_ms = snapshot.end_time_ms
            self._last_event_at_ms = max((e.timestamp_ms for e in snapshot.events), default=None)
            self._error_text = self._failure_state = self._failure_kind = None
            self._cloudwatch_status(LogSourceState.CONFIGURED)
            self._set_state(LogsState.READY)
            self._notify_all()
            return snapshot
        except Exception as error:
            self._cloudwatch_failure(error, generation)
            return None

    async def follow(self) -> None:
        if not self.can_follow or self._following:
            return
        generation = self._invalidate()
        self._following = True
        self._notify("following")
        try:
            await self._operations.run(lambda: self._follow(generation))
        except OperationSuperseded:
            return
        finally:
            if self._current(generation):
                self._following = False
                if self._state is LogsState.LOADING:
                    self._set_state(LogsState.READY if self._lines else LogsState.IDLE)
                self._notify("following")

    def stop_follow(self) -> None:
        self._invalidate()
        if self._state is LogsState.LOADING:
            self._set_state(LogsState.READY if self._lines else LogsState.IDLE)

    async def _follow(self, generation: int) -> None:
        seen: dict[str, int] = {}
        last_end = self._run_created_at_ms
        initialized = False
        try:
            while self._current(generation) and self._following:
                if not initialized:
                    preferred = self._current_stream.name if self._current_stream else None
                    initial = await self._load_cloudwatch(generation, preferred)
                    if not self._current(generation):
                        return
                    if initial is None:
                        if self._state is not LogsState.NO_FILES or preferred is not None:
                            return
                        await _sleep(2.0)
                        continue
                    seen = {e.event_id: e.timestamp_ms for e in initial.events}
                    last_end = initial.end_time_ms
                    initialized = True
                await _sleep(2.0)
                if (
                    not self._current(generation)
                    or not self._following
                    or self._current_stream is None
                    or self._cloudwatch is None
                ):
                    return
                stream, configuration = self._current_stream, self._cloudwatch
                group, _ = cloudwatch_location(
                    configuration, self._application_id or "", self._job_run_id or ""
                )
                start = max(self._run_created_at_ms, last_end - 60_000)
                end = max(last_end, _now_ms())
                snapshot = await self._client.read_cloudwatch_events(
                    log_group_name=group,
                    stream_name=stream.name,
                    start_time_ms=start,
                    end_time_ms=end,
                )
                if not self._current(generation):
                    return
                ids = {key: stamp for key, stamp in seen.items() if stamp >= start}
                incoming = []
                for event in snapshot.events:
                    if event.event_id not in ids:
                        ids[event.event_id] = event.timestamp_ms
                        incoming.append(event)
                if len(ids) > _CW_ID_CAP:
                    raise ProviderError("CloudWatch follow duplicate-id limit exceeded")
                prepared, capped = self._prepare_events((*self._raw_events, *incoming))
                if not self._current(generation):
                    return
                self._raw_events, self._buffer_capped = prepared, self._buffer_capped or capped
                self._project_cloudwatch()
                seen, last_end = ids, end
                self._last_successful_read_at_ms = end
                self._last_event_at_ms = (
                    max((e.timestamp_ms for e in snapshot.events), default=self._last_event_at_ms)
                    if self._last_event_at_ms is None
                    else max(
                        self._last_event_at_ms,
                        max(
                            (e.timestamp_ms for e in snapshot.events),
                            default=self._last_event_at_ms,
                        ),
                    )
                )
                self._cloudwatch_status(LogSourceState.CONFIGURED)
                self._set_state(LogsState.READY)
                self._notify_all()
        except Exception as error:
            self._cloudwatch_failure(error, generation)

    # ── Lifecycle ──────────────────────────────────────────────────────────

    def construct(self) -> None:
        self._inner.construct()

    def dispose(self) -> None:
        if self._disposed:
            return
        self._invalidate()
        self._disposed = True
        self._operations.close()
        # Drop the response cache so a recycled VM (e.g. test
        # harnesses or future content-host reuse) doesn't carry
        # stale entries forward. Owned operations are cancelled above;
        # shutdown awaits their cleanup before connection replacement.
        self._cache.clear()
        self._on_property_changed.on_completed()
        self._on_property_changed.dispose()
        self._inner.dispose()

    async def shutdown(self) -> None:
        self._invalidate()
        self._operations.close()
        await self._operations.cancel_and_drain()

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
        if self._disposed:
            # The Athena and Glue VMs have always guarded this; these four
            # did not, so a late callback could publish a property change
            # for a disposed view model.
            return
        send_value_free(self._hub, PropertyChangedMessage.create(self, "emr.job_run_logs", prop))
        self._on_property_changed.on_next(prop)

    def _notify_all(self) -> None:
        for prop in (
            "state",
            "lines",
            "current_file",
            "available_files",
            "filter",
            "sources",
            "selected_source",
            "available_streams",
            "current_stream",
            "following",
            "last_successful_read_at_ms",
            "last_event_at_ms",
            "buffer_capped",
            "failure_kind",
        ):
            self._notify(prop)


__all__ = ["JobRunLogsVM", "LogSource", "LogSourceState", "LogSourceStatus", "LogsState"]
