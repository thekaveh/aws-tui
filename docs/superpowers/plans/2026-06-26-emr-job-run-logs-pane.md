# EMR Job-Run Logs Pane — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Show grep-filtered S3 logs for the selected job run in the lower half of the existing right-side details column. On-demand fetch (Enter in the logs pane), streaming gunzip + line-by-line filter, session-only filter customization. Page layout reshaped to 1:2 horizontal (LEFT:RIGHT) so the RIGHT column has room for both the existing detail rows AND the new logs scroll body.

**Architecture:** New `JobRunLogsVM` orchestrated by `EmrServerlessPageVM`. A new `JobRunLogsPane` widget renders state placeholders / filter chip strip / scrollable line view. Domain layer adds a thin `EmrServerlessLogs` facade over aioboto3's S3 client (gunzip-stream + line-buffer) so the VM stays narrow. Page-level layout reworked: LEFT (apps box + runs pane) gets 1fr; RIGHT gets 2fr and splits 50/50 vertically into upper (detail) and lower (logs). Tab cycle becomes 3-slot.

**Tech Stack:** Python 3.11+, Textual (Widget, OptionList, Static, set_interval, run_worker exclusive groups), aioboto3 (S3 get_object streaming body), `gzip.GzipFile` over an `io.BufferedReader` wrapper of the boto body, VMx MVVM, pytest tiers.

## Global Constraints

- **Horizontal split**: LEFT column = `1fr`, RIGHT column = `2fr` (was `1fr` / `1fr`). User feedback: "the left job runs pane … to occupy 1/3 of the horizontal space … the job details pane to occupy the remaining 2/3". The `#main-area` and per-page margins stay unchanged — only the page's internal column widths change.
- **RIGHT column vertical split**: 50/50 between detail (top) and logs (bottom). ``JobRunDetailPane`` gets ``height: 1fr`` (was driven by content rows); ``JobRunLogsPane`` gets ``height: 1fr`` below it. Both halves scroll independently if content overflows.
- **JobRunsPane column alignment** (already in place since PR #80) must survive the LEFT column narrowing to ~1/3. Verify the STATUS/NAME/TIME columns still line up cleanly under each other at the new width — the NAME column (`1fr` flex) shrinks, STATUS and TIME stay fixed-width. If the new width causes name truncation problems, the plan calls for a per-row `text-overflow: ellipsis` or similar; do not destroy the column alignment.
- **Tab cycle stays 2-slot**: LEFT (runs) ↔ RIGHT. User feedback: "we still have two panes" — the visible halves are detail (passive display) + logs (interactive); they share a single Tab slot.
- **RIGHT-focus = logs is interactive.** When the user Tabs into RIGHT, Textual focus lands on the LOGS half (``JobRunLogsPane.can_focus = True``); the detail half is non-focusable (``JobRunDetailPane.can_focus = False``) — there's no interactive cursor in detail, only KV rows, so making it non-focusable keeps the Tab cycle honest. ``r`` while RIGHT-focused reloads logs (was: refresh detail); detail keeps refreshing on its 5-s poller as before. Detail's existing ``RefreshRequested`` message + binding are removed — there's no manual-refresh path for detail in this design; the poller is enough.
- On-demand fetch only — do NOT auto-fetch on cursor change. Logs pane starts in IDLE state with "(press Enter to load logs)" placeholder. Enter triggers ``JobRunLogsVM.load()``.
- Streaming, not slurp. Gunzip the S3 body via ``GzipFile(fileobj=…)`` reading 64 KB chunks; line-buffer and filter incrementally. Cap raw bytes at **100 MB per file** (truncate with banner), cap matched lines in memory at **5000**.
- Default file: ``SPARK_DRIVER/stderr.gz``. A dropdown lets the user switch to ``SPARK_DRIVER/stdout.gz`` or ``SPARK_EXECUTOR_<n>/{stdout,stderr}.gz``.
- Default filter patterns (case-insensitive regex): ``ERROR``, ``WARN``, ``FAIL``, ``Exception``, ``Caused by``, ``Traceback``, ``Killed``, ``OutOfMemoryError``. User can edit via the ``f`` modal during the session; reset on next launch (no config.toml in v1).
- Filter modes: **match** (only matching lines) and **passthrough** (all lines). Toggle via the filter modal's "Show all" switch.
- Session cache: loaded lines are kept per ``(app_id, job_run_id, log_file_key)`` tuple inside ``JobRunLogsVM``. Switching runs doesn't dispose; ``r`` re-fetches; switching files within the same run is also cached.
- **Filter changes invalidate the cache and trigger a re-fetch.** The cache key in Task 5 is ``(app_id, run_id, file_key, filter_hash)`` — different filter ⇒ different cache key ⇒ re-fetch. v1 is honest about this; in-memory re-filter without re-download is v1.1 polish.
- Cancellation: switching panes or runs while a load is in flight cancels the worker via ``exclusive=True, group="emr-logs"``.
- Per-theme tcss: every new chip/strip/placeholder class needs a per-theme rule across all 10 themes (byte-identical block).
- Snapshot guards: every new snapshot needs a paired content-presence guard (per PR #53/#63 lesson).
- ``s3MonitoringConfiguration`` may be absent on a job run. State ``NO_LOG_CONFIG`` with placeholder text "(no log monitoring configured for this job)" — do NOT crash.
- Memory caps in the VM are constants tunable in one place (``_MAX_RAW_BYTES``, ``_MAX_MATCHED_LINES``); do not scatter magic numbers.
- Quality gates: ``uv run pytest`` (1198+ default + 214+ snapshots), ``uv run ruff check src tests``, ``uv run ruff format --check src tests``, ``uv run mypy src`` must all stay green after each task.

---

## File Structure

**Create:**

- ``src/aws_tui/domain/emr_logs.py`` — Log-file model + S3 path resolver + streaming-decompression + line-buffered iterator + ``LogFilter`` compiled pattern matcher.
- ``src/aws_tui/vm/emr_serverless/job_run_logs_vm.py`` — ``JobRunLogsVM`` (state, lines, cache, ``set_target`` / ``load`` / ``cancel`` / ``set_filter`` / ``select_log_file``).
- ``src/aws_tui/ui/widgets/emr_serverless/job_run_logs_pane.py`` — ``JobRunLogsPane`` widget (Header bar with filter chip strip + log-file selector + scrollable line view + status footer).
- ``src/aws_tui/ui/widgets/emr_serverless/log_filter_modal.py`` — ``LogFilterModal`` (editable regex list TextArea + "Show all" switch + Apply / Cancel buttons).
- ``tests/unit/domain/test_emr_logs.py`` — Parsing tests + streaming + filter contract.
- ``tests/unit/vm/emr_serverless/test_job_run_logs_vm.py`` — VM state-machine + cache + cancel.
- ``tests/unit/ui/emr_serverless/test_job_run_logs_pane.py`` — Widget interaction (Enter triggers load, file selector dispatches, filter modal opens).
- ``tests/snapshot/apps/emr_logs.py`` — Snapshot fixtures (IDLE / LOADING / LOADED / NO_CONFIG / ERROR states).
- ``tests/snapshot/test_emr_logs.py`` — Snapshot tests + content-presence guards.

**Modify:**

- ``src/aws_tui/domain/emr_serverless.py`` — Extend ``JobRunDetail`` with ``s3_monitoring_log_uri: str | None`` parsed from ``get_job_run`` response. Bump ``JobRunDetail`` dataclass signature; cascade through test fakes.
- ``src/aws_tui/vm/emr_serverless/page_vm.py`` — Add ``job_run_logs: JobRunLogsVM`` child; construct + dispose; ``select_job_run`` calls ``logs.set_target(app_id, run_id)`` (clears state, does NOT fetch).
- ``src/aws_tui/ui/widgets/emr_serverless/page.py`` — Stack ``JobRunDetailPane`` + ``JobRunLogsPane`` in a new ``Vertical`` right-column container; 3-slot Tab cycle (LEFT → DETAIL → LOGS → LEFT); ``BINDINGS`` gains ``r`` forwarding when LOGS focused.
- ``src/aws_tui/app.py`` — ``_emr_active_pane`` handles three panes (LEFT, DETAIL, LOGS); ``_move_cursor`` / ``action_descend`` / ``action_refresh`` route to logs pane when focused.
- ``src/aws_tui/vm/chrome/hint_legend_vm.py`` — ``_SERVICE_ACTIONS["emr-serverless"]`` gains ``"emr.logs.filter"`` (chip ``f`` "filter"); ``_ACTION_LABELS`` extended.
- ``src/aws_tui/infra/keymap_store.py`` — ``DEFAULT_BINDINGS["emr.logs.filter"] = ("f",)``.
- ``tests/unit/domain/_in_memory_emr.py`` — Add fake S3 log files (key → bytes mapping) + ``head_object`` / ``get_object`` stubs aligned with the streaming reader contract.
- 10 × ``src/aws_tui/ui/themes/*.tcss`` — Per-theme block for ``JobRunLogsPane`` chrome (filter strip, log-file selector, line container, status footer, placeholder, in-progress spinner row, match-highlight class).
- ``CHANGELOG.md`` — ``[Unreleased] ### Added`` block for the logs pane.
- ``docs/keybindings.md`` §1.8 — EMR section gains rows: ``Enter`` on LOGS (load), ``r`` on LOGS (reload), ``f`` (filter modal).
- ``docs/architecture.md`` — ``vm/emr_serverless/`` adds ``JobRunLogsVM``.
- ``docs/superpowers/specs/2026-06-25-emr-serverless-service-design.md`` — Status amendment: logs shipped under PR-B-logs naming, scoped to ``stdout/stderr`` streaming.

---

## Task 1: Domain — S3 log path resolver + ``LogFile`` model

**Files:**
- Create: ``src/aws_tui/domain/emr_logs.py``
- Modify: ``src/aws_tui/domain/emr_serverless.py`` (extend ``JobRunDetail`` + ``get_job_run``)
- Test: ``tests/unit/domain/test_emr_logs.py``

**Interfaces:**
- Produces: ``S3LogLocation(bucket: str, prefix: str)``, ``LogFile(key: str, kind: LogFileKind, size: int | None)``, ``LogFileKind`` enum (``DRIVER_STDOUT``, ``DRIVER_STDERR``, ``EXECUTOR_STDOUT``, ``EXECUTOR_STDERR``), ``parse_log_uri(s3_uri: str) -> S3LogLocation``, ``build_run_prefix(loc: S3LogLocation, application_id: str, job_run_id: str) -> str``.
- Consumes: ``JobRunDetail.s3_monitoring_log_uri`` (new field).

- [ ] **Step 1: Write failing test for ``parse_log_uri``**

```python
# tests/unit/domain/test_emr_logs.py
from __future__ import annotations

import pytest

from aws_tui.domain.emr_logs import S3LogLocation, parse_log_uri


def test_parse_log_uri_extracts_bucket_and_prefix() -> None:
    """``s3MonitoringConfiguration.logUri`` is a string like
    ``s3://my-bucket/emr-logs/`` (with or without trailing slash).
    We split it into bucket + key prefix; the prefix has any
    trailing slash stripped so callers can join with confidence."""
    loc = parse_log_uri("s3://my-bucket/emr-logs/")
    assert loc == S3LogLocation(bucket="my-bucket", prefix="emr-logs")
    loc = parse_log_uri("s3://my-bucket/emr-logs")
    assert loc == S3LogLocation(bucket="my-bucket", prefix="emr-logs")


def test_parse_log_uri_bucket_only_has_empty_prefix() -> None:
    loc = parse_log_uri("s3://my-bucket/")
    assert loc == S3LogLocation(bucket="my-bucket", prefix="")


def test_parse_log_uri_rejects_non_s3_scheme() -> None:
    with pytest.raises(ValueError, match="not an s3:// URI"):
        parse_log_uri("https://my-bucket/path")
```

- [ ] **Step 2: Run test to verify it fails**

Run: ``uv run pytest tests/unit/domain/test_emr_logs.py -v``
Expected: FAIL with ``ImportError`` (no module yet).

- [ ] **Step 3: Implement ``S3LogLocation`` + ``parse_log_uri``**

```python
# src/aws_tui/domain/emr_logs.py
"""EMR Serverless log streaming.

Resolves the S3 location declared by a job run's
``s3MonitoringConfiguration.logUri``, lists the per-component log
files under it, and streams the gzipped ``stdout/stderr`` bodies
line-by-line through a compiled filter. Used by ``JobRunLogsVM``;
not consumed elsewhere.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from urllib.parse import urlparse


@dataclass(frozen=True, slots=True)
class S3LogLocation:
    """Parsed ``s3://bucket/prefix`` reference. Prefix has no
    leading or trailing slash."""

    bucket: str
    prefix: str


def parse_log_uri(uri: str) -> S3LogLocation:
    parsed = urlparse(uri)
    if parsed.scheme != "s3":
        raise ValueError(f"not an s3:// URI: {uri!r}")
    return S3LogLocation(bucket=parsed.netloc, prefix=parsed.path.strip("/"))


class LogFileKind(StrEnum):
    DRIVER_STDOUT = "DRIVER_STDOUT"
    DRIVER_STDERR = "DRIVER_STDERR"
    EXECUTOR_STDOUT = "EXECUTOR_STDOUT"
    EXECUTOR_STDERR = "EXECUTOR_STDERR"


@dataclass(frozen=True, slots=True)
class LogFile:
    """One log file under the job run's S3 prefix.

    ``key`` is the absolute S3 key; ``kind`` is the parsed role
    (driver vs executor, stdout vs stderr); ``size`` is the
    object's content length in bytes (None if not known yet)."""

    key: str
    kind: LogFileKind
    size: int | None = None


def build_run_prefix(loc: S3LogLocation, application_id: str, job_run_id: str) -> str:
    """Build the S3 key prefix under which a specific run's logs
    live. Format: ``<loc.prefix>/applications/<app>/jobs/<run>``.
    Used as the ListObjectsV2 prefix when enumerating log files."""
    base = f"{loc.prefix}/" if loc.prefix else ""
    return f"{base}applications/{application_id}/jobs/{job_run_id}"


__all__ = [
    "LogFile",
    "LogFileKind",
    "S3LogLocation",
    "build_run_prefix",
    "parse_log_uri",
]
```

- [ ] **Step 4: Run test to verify it passes**

Run: ``uv run pytest tests/unit/domain/test_emr_logs.py -v``
Expected: PASS.

- [ ] **Step 5: Add ``s3_monitoring_log_uri`` to ``JobRunDetail``**

In ``src/aws_tui/domain/emr_serverless.py``:

```python
@dataclass(frozen=True, slots=True)
class JobRunDetail:
    application_id: str
    job_run_id: str
    name: str | None
    state: JobRunState
    created_at: datetime
    updated_at: datetime
    entry_point: str | None
    entry_point_arguments: tuple[str, ...]
    spark_submit_parameters: str | None
    execution_role_arn: str
    duration_ms: int | None
    # New: parsed from response ``configurationOverrides
    # .monitoringConfiguration.s3MonitoringConfiguration.logUri``.
    # ``None`` when the job didn't set up S3 log monitoring (no
    # monitoringConfiguration block, or no s3MonitoringConfiguration
    # block within it). The logs pane shows the NO_LOG_CONFIG
    # placeholder in that case.
    s3_monitoring_log_uri: str | None
```

Update ``EmrServerlessClient.get_job_run`` to extract it:

```python
log_uri = (
    r.get("configurationOverrides", {})
    .get("monitoringConfiguration", {})
    .get("s3MonitoringConfiguration", {})
    .get("logUri")
)
# ... in the JobRunDetail(...) construction:
s3_monitoring_log_uri=log_uri,
```

- [ ] **Step 6: Cascade through the in-memory fake**

In ``tests/unit/domain/_in_memory_emr.py`` ``add_job_run_detail`` signature, add ``s3_monitoring_log_uri: str | None = None``. Plumb it into the returned ``JobRunDetail``. Any existing test that builds a detail will keep working (``None`` default).

- [ ] **Step 7: Add a parametrize test for ``build_run_prefix``**

```python
@pytest.mark.parametrize(
    ("loc", "expected"),
    [
        (S3LogLocation(bucket="b", prefix=""), "applications/00abc/jobs/r-001"),
        (S3LogLocation(bucket="b", prefix="logs"), "logs/applications/00abc/jobs/r-001"),
        (S3LogLocation(bucket="b", prefix="a/b"), "a/b/applications/00abc/jobs/r-001"),
    ],
)
def test_build_run_prefix(loc: S3LogLocation, expected: str) -> None:
    assert build_run_prefix(loc, "00abc", "r-001") == expected
```

- [ ] **Step 8: Run all new tests + ruff + mypy**

Run: ``uv run pytest tests/unit/domain/test_emr_logs.py tests/unit/domain/test_emr_serverless.py -v && uv run ruff check src tests && uv run mypy src``
Expected: all green.

- [ ] **Step 9: Commit**

```bash
git add src/aws_tui/domain/emr_logs.py src/aws_tui/domain/emr_serverless.py tests/unit/domain/test_emr_logs.py tests/unit/domain/_in_memory_emr.py
git commit -m "feat(emr): domain log-file model + S3 path resolver + JobRunDetail log URI"
```

---

## Task 2: Domain — Streaming gunzip + line buffer + ``LogFilter``

**Files:**
- Modify: ``src/aws_tui/domain/emr_logs.py`` (add the streamer + filter)
- Test: ``tests/unit/domain/test_emr_logs.py``

**Interfaces:**
- Produces: ``LogFilter(patterns: tuple[str, ...], mode: FilterMode, case_insensitive: bool=True)``, ``LogFilter.matches(line: str) -> bool``, ``LogFilter.with_(mode=…, patterns=…)`` for immutable update, ``DEFAULT_LOG_FILTER`` constant.
- Produces: ``async def stream_log(*, session: aioboto3.Session, region_name: str | None, log_file: LogFile, bucket: str, max_bytes: int, filter_: LogFilter) -> AsyncIterator[LogChunk]`` — yields chunks of ``LogChunk(lines: tuple[str, ...], bytes_read: int, lines_scanned: int, matched_count: int, truncated: bool)``.
- Consumes: aioboto3 client + ``gzip.GzipFile`` over the body.

- [ ] **Step 1: Write failing test for ``LogFilter``**

```python
def test_log_filter_default_matches_common_indicators() -> None:
    from aws_tui.domain.emr_logs import DEFAULT_LOG_FILTER, FilterMode

    assert DEFAULT_LOG_FILTER.mode is FilterMode.MATCH
    assert DEFAULT_LOG_FILTER.matches("2026-06-26 12:00:00 ERROR something broke")
    assert DEFAULT_LOG_FILTER.matches("Caused by: java.lang.NullPointerException")
    assert DEFAULT_LOG_FILTER.matches("WARN Spark something noisy")
    assert not DEFAULT_LOG_FILTER.matches("INFO Spark startup complete")


def test_log_filter_passthrough_mode_matches_everything() -> None:
    from aws_tui.domain.emr_logs import DEFAULT_LOG_FILTER, FilterMode

    pt = DEFAULT_LOG_FILTER.with_(mode=FilterMode.PASSTHROUGH)
    assert pt.matches("INFO whatever")
    assert pt.matches("")


def test_log_filter_with_swaps_patterns() -> None:
    from aws_tui.domain.emr_logs import DEFAULT_LOG_FILTER

    custom = DEFAULT_LOG_FILTER.with_(patterns=("KILL",))
    assert custom.matches("the job was KILLed by the watchdog")
    assert not custom.matches("ERROR not in the custom set")
```

- [ ] **Step 2: Implement ``FilterMode`` + ``LogFilter`` + ``DEFAULT_LOG_FILTER``**

```python
import re
from enum import StrEnum


class FilterMode(StrEnum):
    MATCH = "match"
    PASSTHROUGH = "passthrough"


@dataclass(frozen=True, slots=True)
class LogFilter:
    patterns: tuple[str, ...]
    mode: FilterMode = FilterMode.MATCH
    case_insensitive: bool = True

    def matches(self, line: str) -> bool:
        if self.mode is FilterMode.PASSTHROUGH:
            return True
        flags = re.IGNORECASE if self.case_insensitive else 0
        return any(re.search(p, line, flags) for p in self.patterns)

    def with_(
        self,
        *,
        patterns: tuple[str, ...] | None = None,
        mode: FilterMode | None = None,
        case_insensitive: bool | None = None,
    ) -> LogFilter:
        return LogFilter(
            patterns=patterns if patterns is not None else self.patterns,
            mode=mode if mode is not None else self.mode,
            case_insensitive=case_insensitive
            if case_insensitive is not None
            else self.case_insensitive,
        )


DEFAULT_LOG_FILTER: LogFilter = LogFilter(
    patterns=(
        r"ERROR",
        r"WARN",
        r"FAIL",
        r"Exception",
        r"Caused by",
        r"Traceback",
        r"Killed",
        r"OutOfMemoryError",
    ),
    mode=FilterMode.MATCH,
    case_insensitive=True,
)
```

- [ ] **Step 3: Test + write ``stream_log`` happy path**

Add to the same file:

```python
import gzip
from collections.abc import AsyncIterator
from io import BytesIO
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import aioboto3
    from botocore.config import Config as BotoConfig


@dataclass(frozen=True, slots=True)
class LogChunk:
    lines: tuple[str, ...]
    bytes_read: int
    lines_scanned: int
    matched_count: int
    truncated: bool


_LINE_BUFFER_BATCH: int = 200
_STREAM_CHUNK_BYTES: int = 64 * 1024


async def stream_log(
    *,
    session: aioboto3.Session,
    region_name: str | None,
    log_file: LogFile,
    bucket: str,
    max_bytes: int,
    filter_: LogFilter,
    boto_config: BotoConfig | None = None,
) -> AsyncIterator[LogChunk]:
    """Stream the gzipped body of ``log_file.key`` and yield
    ``LogChunk``s of matched lines in batches of ``_LINE_BUFFER_BATCH``.

    Stops when the gzip stream ends OR when ``bytes_read >= max_bytes``
    (sets ``truncated=True`` on the final chunk so the caller can
    surface a banner). Never loads the full body into memory.
    """
    kwargs: dict[str, object] = {"region_name": region_name}
    if boto_config is not None:
        kwargs["config"] = boto_config
    async with session.client("s3", **kwargs) as s3:
        resp = await s3.get_object(Bucket=bucket, Key=log_file.key)
        body = resp["Body"]
        buf = BytesIO()
        bytes_read = 0
        truncated = False
        while True:
            chunk = await body.read(_STREAM_CHUNK_BYTES)
            if not chunk:
                break
            bytes_read += len(chunk)
            buf.write(chunk)
            if bytes_read >= max_bytes:
                truncated = True
                break
        buf.seek(0)
        decompressed = gzip.GzipFile(fileobj=buf, mode="rb")
        lines_scanned = 0
        matched: list[str] = []
        for raw in decompressed:
            line = raw.decode("utf-8", errors="replace").rstrip("\n")
            lines_scanned += 1
            if filter_.matches(line):
                matched.append(line)
            if len(matched) >= _LINE_BUFFER_BATCH:
                yield LogChunk(
                    lines=tuple(matched),
                    bytes_read=bytes_read,
                    lines_scanned=lines_scanned,
                    matched_count=len(matched),
                    truncated=False,
                )
                matched = []
        yield LogChunk(
            lines=tuple(matched),
            bytes_read=bytes_read,
            lines_scanned=lines_scanned,
            matched_count=len(matched),
            truncated=truncated,
        )
```

- [ ] **Step 4: Write failing test for ``stream_log`` with a stub session**

```python
import gzip
from io import BytesIO
from unittest.mock import AsyncMock

from aws_tui.domain.emr_logs import (
    DEFAULT_LOG_FILTER,
    LogFile,
    LogFileKind,
    stream_log,
)


class _StubBody:
    def __init__(self, payload: bytes) -> None:
        self._payload = payload

    async def read(self, n: int) -> bytes:
        out, self._payload = self._payload[:n], self._payload[n:]
        return out


class _StubS3:
    def __init__(self, body: bytes) -> None:
        self.get_object = AsyncMock(return_value={"Body": _StubBody(body)})

    async def __aenter__(self) -> _StubS3:
        return self

    async def __aexit__(self, *args: object) -> None:
        return None


class _StubSession:
    def __init__(self, stub: _StubS3) -> None:
        self._stub = stub

    def client(self, *_args: object, **_kwargs: object) -> _StubS3:
        return self._stub


@pytest.mark.asyncio
async def test_stream_log_yields_matched_lines() -> None:
    log_lines = [
        "INFO startup",
        "ERROR something broke",
        "INFO ignore",
        "WARN noisy",
        "INFO bye",
    ]
    gz_payload = gzip.compress("\n".join(log_lines).encode())
    stub = _StubS3(gz_payload)
    session = _StubSession(stub)
    log_file = LogFile(
        key="logs/applications/a/jobs/r/SPARK_DRIVER/stderr.gz",
        kind=LogFileKind.DRIVER_STDERR,
    )
    chunks = []
    async for chunk in stream_log(
        session=session,  # type: ignore[arg-type]
        region_name="us-east-1",
        log_file=log_file,
        bucket="b",
        max_bytes=1024 * 1024,
        filter_=DEFAULT_LOG_FILTER,
    ):
        chunks.append(chunk)
    # All matched lines are surfaced; the INFO lines are dropped
    # by the default filter.
    all_lines = [line for c in chunks for line in c.lines]
    assert all_lines == ["ERROR something broke", "WARN noisy"]
    # Total scanned matches the input line count.
    assert chunks[-1].lines_scanned == 5
    # Not truncated for this small input.
    assert chunks[-1].truncated is False
```

- [ ] **Step 5: Run all tests + quality gates**

```
uv run pytest tests/unit/domain/test_emr_logs.py -v
uv run ruff check src tests && uv run ruff format --check src tests && uv run mypy src
```

- [ ] **Step 6: Commit**

```bash
git commit -am "feat(emr): streaming gunzip log reader + default match filter"
```

---

## Task 3: Domain — Log-file enumeration via ListObjectsV2

**Files:**
- Modify: ``src/aws_tui/domain/emr_logs.py``
- Test: ``tests/unit/domain/test_emr_logs.py``

**Interfaces:**
- Produces: ``async def list_log_files(*, session, region_name, bucket, run_prefix, boto_config=None) -> list[LogFile]`` — lists ``<run_prefix>/SPARK_DRIVER/{stdout,stderr}.gz`` and ``<run_prefix>/SPARK_EXECUTOR_<n>/{stdout,stderr}.gz``; parses the file role from the key path; returns ``LogFile`` instances sorted DRIVER first, then EXECUTOR by index.

- [ ] **Step 1: Failing test**

```python
@pytest.mark.asyncio
async def test_list_log_files_groups_driver_first_then_executors() -> None:
    """Enumerate S3 keys under the run prefix, parse each into a
    ``LogFile`` with the right ``LogFileKind``, sort driver-first."""

    fake_keys = [
        ("logs/applications/a/jobs/r/SPARK_EXECUTOR_2/stdout.gz", 1024),
        ("logs/applications/a/jobs/r/SPARK_DRIVER/stderr.gz", 2048),
        ("logs/applications/a/jobs/r/SPARK_EXECUTOR_1/stderr.gz", 512),
        ("logs/applications/a/jobs/r/SPARK_DRIVER/stdout.gz", 1024),
    ]
    # Build a stub paginator + S3 client
    # ... (will hand-roll an aioboto3-shaped stub)
    files = await list_log_files(  # noqa
        session=session,
        region_name="us-east-1",
        bucket="b",
        run_prefix="logs/applications/a/jobs/r",
    )
    kinds = [f.kind for f in files]
    assert kinds == [
        LogFileKind.DRIVER_STDOUT,
        LogFileKind.DRIVER_STDERR,
        LogFileKind.EXECUTOR_STDOUT,  # idx 1
        LogFileKind.EXECUTOR_STDERR,  # idx 1
        LogFileKind.EXECUTOR_STDOUT,  # idx 2
        LogFileKind.EXECUTOR_STDERR,  # idx 2 — alternating order depends on real keys
    ]
    # Driver-first invariant: first two entries are driver.
    assert all(f.kind in (LogFileKind.DRIVER_STDOUT, LogFileKind.DRIVER_STDERR) for f in files[:2])
```

(Note: this test will need a richer stub paginator. The implementer should hand-roll it from the patterns in ``test_emr_serverless.py``.)

- [ ] **Step 2: Implementation**

```python
async def list_log_files(
    *,
    session: aioboto3.Session,
    region_name: str | None,
    bucket: str,
    run_prefix: str,
    boto_config: BotoConfig | None = None,
) -> list[LogFile]:
    """List all log files under the run's S3 prefix. Returns
    ``LogFile``s with ``kind`` parsed from the key path and ``size``
    from each object's ``Size`` field. Driver-first sort so the
    default selection (``DRIVER_STDERR``) is at a stable index."""
    kwargs: dict[str, object] = {"region_name": region_name}
    if boto_config is not None:
        kwargs["config"] = boto_config
    files: list[tuple[int, LogFile]] = []
    async with session.client("s3", **kwargs) as s3:
        next_token: str | None = None
        while True:
            list_kwargs: dict[str, object] = {"Bucket": bucket, "Prefix": run_prefix}
            if next_token is not None:
                list_kwargs["ContinuationToken"] = next_token
            resp = await s3.list_objects_v2(**list_kwargs)
            for obj in resp.get("Contents", []):
                key = obj["Key"]
                kind, sort_idx = _classify_key(key)
                if kind is None:
                    continue
                files.append(
                    (sort_idx, LogFile(key=key, kind=kind, size=obj.get("Size")))
                )
            next_token = resp.get("NextContinuationToken")
            if next_token is None:
                break
    files.sort(key=lambda pair: pair[0])
    return [f for _, f in files]


def _classify_key(key: str) -> tuple[LogFileKind | None, int]:
    """Map an S3 key path to a ``LogFileKind`` + sort index.

    Driver-first sort (sort_idx ``0`` and ``1``); executor logs get
    ``2 + executor_idx * 2`` for stdout, ``+1`` for stderr.
    Keys that don't match the expected pattern return ``(None, 0)``
    so the caller skips them silently.
    """
    if "/SPARK_DRIVER/" in key:
        if key.endswith("stdout.gz"):
            return LogFileKind.DRIVER_STDOUT, 0
        if key.endswith("stderr.gz"):
            return LogFileKind.DRIVER_STDERR, 1
        return None, 0
    if "/SPARK_EXECUTOR_" in key:
        # Extract the executor index from the path segment.
        seg = next((s for s in key.split("/") if s.startswith("SPARK_EXECUTOR_")), None)
        if seg is None:
            return None, 0
        try:
            idx = int(seg.removeprefix("SPARK_EXECUTOR_"))
        except ValueError:
            return None, 0
        if key.endswith("stdout.gz"):
            return LogFileKind.EXECUTOR_STDOUT, 2 + idx * 2
        if key.endswith("stderr.gz"):
            return LogFileKind.EXECUTOR_STDERR, 2 + idx * 2 + 1
    return None, 0
```

- [ ] **Step 3: Run + commit**

```bash
git commit -am "feat(emr): list_log_files enumerates S3 prefix + driver-first sort"
```

---

## Task 4: VM — ``JobRunLogsVM`` skeleton (state + set_target)

**Files:**
- Create: ``src/aws_tui/vm/emr_serverless/job_run_logs_vm.py``
- Test: ``tests/unit/vm/emr_serverless/test_job_run_logs_vm.py``

**Interfaces:**
- Consumes: ``EmrServerlessClient`` (existing), ``LogFilter`` (Task 2), ``stream_log`` + ``list_log_files`` (Tasks 2, 3).
- Produces: ``JobRunLogsVM`` with ``state: LogsState``, ``set_target(app_id, run_id)`` (clears state, no fetch), ``select_log_file(kind)``, ``set_filter(filter_)``, ``load()`` async (drives the worker), ``cancel()``, ``r``-aware ``reload()`` (clears cache then load).
- Hub messages: PropertyChanged on ``"state"``, ``"lines"``, ``"progress"``, ``"current_file"``, ``"filter"``, ``"available_files"``.

States: ``IDLE``, ``LOADING``, ``READY``, ``ERROR``, ``EMPTY_TARGET``, ``NO_LOG_CONFIG``, ``NO_FILES``, ``TRUNCATED`` (terminal-ready variant when ``max_bytes`` hit).

- [ ] **Step 1: Define the state enum + skeleton**

```python
# src/aws_tui/vm/emr_serverless/job_run_logs_vm.py
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
    build_run_prefix,
    list_log_files,
    parse_log_uri,
    stream_log,
)
from aws_tui.domain.filesystem import ProviderError
from aws_tui.vm.emr_serverless._errors import map_provider_error
from aws_tui.vm.file_manager.pane_vm import PaneState

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
    def state(self) -> LogsState: return self._state

    @property
    def error_text(self) -> str | None: return self._error_text

    @property
    def available_files(self) -> tuple[LogFile, ...]: return self._available_files

    @property
    def current_file(self) -> LogFile | None: return self._current_file

    @property
    def lines(self) -> tuple[str, ...]: return self._lines

    @property
    def bytes_read(self) -> int: return self._bytes_read

    @property
    def lines_scanned(self) -> int: return self._lines_scanned

    @property
    def filter(self) -> LogFilter: return self._filter

    @property
    def application_id(self) -> str | None: return self._application_id

    @property
    def job_run_id(self) -> str | None: return self._job_run_id

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
```

- [ ] **Step 2: Tests for ``set_target`` + state transitions**

```python
# tests/unit/vm/emr_serverless/test_job_run_logs_vm.py
from __future__ import annotations

from typing import cast

import pytest
from vmx import NULL_DISPATCHER, MessageHub
from vmx.messages.protocols import Message

from aws_tui.domain.emr_logs import DEFAULT_LOG_FILTER
from aws_tui.vm.emr_serverless.job_run_logs_vm import JobRunLogsVM, LogsState


def _hub() -> MessageHub[Message]:
    return cast("MessageHub[Message]", MessageHub())


def _make() -> JobRunLogsVM:
    hub = _hub()
    vm = JobRunLogsVM(
        session=cast("object", None),  # not used by set_target paths
        region_name="us-east-1",
        hub=hub,
        dispatcher=NULL_DISPATCHER,
    )
    vm.construct()
    return vm


def test_initial_state_is_empty_target() -> None:
    vm = _make()
    assert vm.state is LogsState.EMPTY_TARGET
    assert vm.lines == ()
    vm.dispose()


def test_set_target_with_log_uri_transitions_to_idle() -> None:
    vm = _make()
    vm.set_target("a1", "r1", "s3://b/logs/")
    assert vm.state is LogsState.IDLE
    assert vm.application_id == "a1"
    assert vm.job_run_id == "r1"
    vm.dispose()


def test_set_target_without_log_uri_transitions_to_no_config() -> None:
    vm = _make()
    vm.set_target("a1", "r1", None)
    assert vm.state is LogsState.NO_LOG_CONFIG
    vm.dispose()


def test_set_target_to_none_returns_to_empty_target() -> None:
    vm = _make()
    vm.set_target("a1", "r1", "s3://b/")
    assert vm.state is LogsState.IDLE
    vm.set_target(None, None, None)
    assert vm.state is LogsState.EMPTY_TARGET
    vm.dispose()


def test_set_filter_emits_property_change() -> None:
    vm = _make()
    changes: list[str] = []
    vm._hub.messages.subscribe(  # type: ignore[attr-defined]
        on_next=lambda m: changes.append(getattr(m, "property_name", ""))
    )
    new_filter = DEFAULT_LOG_FILTER.with_(patterns=("FATAL",))
    vm.set_filter(new_filter)
    assert "filter" in changes
    vm.dispose()
```

- [ ] **Step 3: Run + commit**

```bash
git commit -am "feat(emr): JobRunLogsVM skeleton — state machine + set_target + select_log_file"
```

---

## Task 5: VM — ``JobRunLogsVM.load()`` — fetch + stream + buffer

**Files:**
- Modify: ``src/aws_tui/vm/emr_serverless/job_run_logs_vm.py``
- Test: ``tests/unit/vm/emr_serverless/test_job_run_logs_vm.py``

**Interfaces:**
- Produces: ``async def load(self) -> None``.
- Behaviour: from IDLE → LOADING → READY (or TRUNCATED / ERROR). Resolves log files, sets ``current_file = first DRIVER_STDERR`` (fallback ``DRIVER_STDOUT`` if no stderr), streams, batches lines into ``_lines`` (capped at ``_MAX_MATCHED_LINES``), emits ``PropertyChangedMessage("lines")`` every batch.

- [ ] **Step 1: Add ``load()`` implementation**

```python
    async def load(self) -> None:
        """Fetch + stream the selected log file. Idempotent — a
        second call while already loading is a no-op."""
        if self._state is LogsState.LOADING:
            return
        if (
            self._application_id is None
            or self._job_run_id is None
            or self._log_uri is None
        ):
            return
        self._set_state(LogsState.LOADING)
        self._lines = ()
        self._bytes_read = 0
        self._lines_scanned = 0
        try:
            loc = parse_log_uri(self._log_uri)
            run_prefix = build_run_prefix(loc, self._application_id, self._job_run_id)
            files = await list_log_files(
                session=self._session,
                region_name=self._region_name,
                bucket=loc.bucket,
                run_prefix=run_prefix,
            )
            self._available_files = tuple(files)
            self._hub.send(
                PropertyChangedMessage.create(self, "emr.job_run_logs", "available_files")
            )
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
                self._hub.send(
                    PropertyChangedMessage.create(self, "emr.job_run_logs", "current_file")
                )
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
                self._lines = self._cache[cache_key]
                self._hub.send(
                    PropertyChangedMessage.create(self, "emr.job_run_logs", "lines")
                )
                self._set_state(LogsState.READY)
                return
            buffered: list[str] = []
            async for chunk in stream_log(
                session=self._session,
                region_name=self._region_name,
                log_file=self._current_file,
                bucket=loc.bucket,
                max_bytes=_MAX_RAW_BYTES,
                filter_=self._filter,
            ):
                buffered.extend(chunk.lines)
                if len(buffered) > _MAX_MATCHED_LINES:
                    buffered = buffered[-_MAX_MATCHED_LINES:]
                self._lines = tuple(buffered)
                self._bytes_read = chunk.bytes_read
                self._lines_scanned = chunk.lines_scanned
                self._hub.send(
                    PropertyChangedMessage.create(self, "emr.job_run_logs", "lines")
                )
                self._hub.send(
                    PropertyChangedMessage.create(self, "emr.job_run_logs", "progress")
                )
                truncated = chunk.truncated
            self._cache[cache_key] = self._lines
            self._set_state(LogsState.TRUNCATED if truncated else LogsState.READY)
        except ProviderError as exc:
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
            self._error_text = f"unexpected error: {exc}"
            self._set_state(LogsState.ERROR)
```

- [ ] **Step 2: Tests covering the happy path + truncation + cancellation**

Use a stub session that returns canned gzip bytes and a stub ``list_objects_v2`` (mirror the Task 2 + 3 pattern). Drive ``vm.set_target(...)`` then ``await vm.load()`` and assert:
- ``state is READY`` after a clean stream
- ``state is TRUNCATED`` if the stream hit ``_MAX_RAW_BYTES``
- ``lines`` contains exactly the matched lines
- A subsequent ``load()`` for the same target hits the cache (no second ``get_object`` call)
- ``cancel()`` mid-stream returns to non-``LOADING`` state

- [ ] **Step 3: Run + commit**

```bash
git commit -am "feat(emr): JobRunLogsVM.load() — stream + buffer + cache + truncate"
```

---

## Task 6: VM — Wire ``JobRunLogsVM`` into ``EmrServerlessPageVM``

**Files:**
- Modify: ``src/aws_tui/vm/emr_serverless/page_vm.py``
- Test: ``tests/unit/vm/emr_serverless/test_page_vm.py``

- [ ] **Step 1: Add as a public child VM**

```python
        # In EmrServerlessPageVM.__init__, after job_run_detail construction:
        self.job_run_logs: JobRunLogsVM = JobRunLogsVM(
            session=client._session,  # type: ignore[attr-defined]  # reuses the EMR client's aioboto3 session
            region_name=client._region_name,  # type: ignore[attr-defined]
            hub=hub,
            dispatcher=dispatcher,
        )
        self.job_run_logs.construct()
```

Update ``dispose`` to dispose ``job_run_logs`` LAST (reverse construct order).

- [ ] **Step 2: ``select_job_run`` calls ``set_target`` on logs VM**

```python
    async def select_job_run(self, run_id: str) -> None:
        self.job_runs.select(run_id)
        self.job_run_detail.set_target(self.applications.selected_id, run_id)
        await self.job_run_detail.refresh()
        # Update logs target — does NOT fetch (user has to press
        # Enter in the logs pane). Reads the s3 log uri off the
        # freshly-refreshed detail. If detail is None or has no
        # uri, the logs VM transitions to NO_LOG_CONFIG.
        detail = self.job_run_detail.detail
        self.job_run_logs.set_target(
            self.applications.selected_id,
            run_id,
            detail.s3_monitoring_log_uri if detail is not None else None,
        )
```

- [ ] **Step 3: Test + commit**

Add a test that drives ``page.select_job_run("r")`` and asserts ``page.job_run_logs.state is LogsState.IDLE`` when the detail has a ``s3_monitoring_log_uri`` and ``LogsState.NO_LOG_CONFIG`` when it doesn't.

```bash
git commit -am "feat(emr): page VM wires JobRunLogsVM, select_job_run cascades log target"
```

---

## Task 7: UI — ``JobRunLogsPane`` widget skeleton

**Files:**
- Create: ``src/aws_tui/ui/widgets/emr_serverless/job_run_logs_pane.py``
- Test: ``tests/unit/ui/emr_serverless/test_job_run_logs_pane.py``

**Interfaces:**
- Consumes: ``JobRunLogsVM``, hub for PropertyChanged subscription.
- Posts: ``JobRunLogsPane.LoadRequested`` Textual message (Enter), ``RefreshRequested`` (``r``), ``OpenFilterRequested`` (``f``), ``LogFileSelected(kind)`` (file selector).
- Render: chip strip (log-file selector, e.g. ``[DRIVER stderr]`` highlighted) + scroll area (lines, with ``-match`` class on matched chunks) + status footer (state-aware text).

- [ ] **Step 1: Skeleton + state-aware rendering**

```python
"""JobRunLogsPane — RIGHT-bottom pane of the EMR page.

Renders the state-machine of ``JobRunLogsVM``:

    EMPTY_TARGET   →  ``(no run selected)``
    IDLE           →  ``(press Enter to load logs)``
    LOADING        →  ``loading <log_file>: N MB read, M lines, K matches`` + spinner
    READY          →  scrollable line list
    TRUNCATED      →  same, with banner ``(truncated at 100 MB — press r to reload)``
    NO_LOG_CONFIG  →  ``(no log monitoring configured for this job)``
    NO_FILES       →  ``(no log files yet — try again once the run starts logging)``
    ERROR          →  red placeholder + error text

Filter and file-selector chips are above the body; the file
selector shows the currently-loaded LogFile and dispatches a
``LogFileSelected`` message when the user changes it.
"""

# Class layout:
#   chip strip (Horizontal) — filter chip + file chips
#   body (VerticalScroll) — lines OR placeholder
#   footer (Static) — state line ("READY · 1.2 MB · 142 matches")
#
# Bindings (when this widget has focus):
#   "enter" -> action_load (posts LoadRequested)
#   "r"     -> action_reload (posts RefreshRequested)
#   "f"     -> action_open_filter (posts OpenFilterRequested)
#   "up/k", "down/j" -> action_scroll_up/down (delegates to body scroll)
```

(Full implementation should follow the JobRunsPane / JobRunDetailPane structure — same hub subscription pattern, same ``call_after_refresh`` rerender.)

- [ ] **Step 2: Tests**

- A widget mounted with a fresh VM shows ``(no run selected)``.
- After ``vm.set_target("a", "r", "s3://b/")``, the widget shows ``(press Enter to load logs)``.
- Pressing Enter posts ``LoadRequested`` (capture via `app.message_log` or a stub subscriber).
- Rendering NO_LOG_CONFIG shows the right placeholder.

- [ ] **Step 3: Commit**

```bash
git commit -am "feat(emr): JobRunLogsPane widget — state placeholders + chip strip skeleton"
```

---

## Task 8: UI — ``LogFilterModal``

**Files:**
- Create: ``src/aws_tui/ui/widgets/emr_serverless/log_filter_modal.py``
- Test: ``tests/unit/ui/emr_serverless/test_log_filter_modal.py``

**Interfaces:**
- Push-screen modal returning ``LogFilter | None`` (None = cancel).
- Body: TextArea (one regex per line, pre-filled from current filter), "Show all" switch (sets ``mode=PASSTHROUGH``), "Match case" switch.
- Buttons: Apply (primary), Reset to defaults (default), Cancel (default).

- [ ] **Step 1: Build the modal mirroring ``JobRunCloneModal``** (pattern is established — same ``ModalButton`` + form layout).

- [ ] **Step 2: Wire from the page** — ``EmrServerlessPage.action_open_log_filter`` pushes the modal, awaits, calls ``page_vm.job_run_logs.set_filter(new_filter)`` on Apply.

- [ ] **Step 3: Tests + snapshot**

- [ ] **Step 4: Commit**

```bash
git commit -am "feat(emr): LogFilterModal — edit regex list + show-all toggle + match-case"
```

---

## Task 9: Page integration — 1:2 horizontal split + 50/50 right-column vertical split + 2-slot Tab cycle (unchanged)

**Files:**
- Modify: ``src/aws_tui/ui/widgets/emr_serverless/page.py``
- Modify: ``src/aws_tui/ui/widgets/emr_serverless/job_run_detail_pane.py`` (drop ``can_focus = True`` + the ``r`` binding + the ``RefreshRequested`` message)
- Modify: ``src/aws_tui/app.py``
- Test: ``tests/integration/test_emr_page.py``

**Interfaces:**
- Page widget: LEFT column at ``width: 1fr`` (was already ``1fr``; the change is on the RIGHT side). RIGHT becomes a ``Vertical(classes="emr-right-column")`` at ``width: 2fr``, wrapping ``JobRunDetailPane`` (``height: 1fr``, non-focusable) + ``JobRunLogsPane`` (``height: 1fr``, focusable) — clean 50/50 split.
- ``_cycle`` stays 2-slot: LEFT (``JobRunsPane``) ↔ RIGHT (``JobRunLogsPane`` — the interactive half; detail is passive display).
- ``_emr_active_pane`` returns ``JobRunsPane`` when LEFT focused or ``JobRunLogsPane`` when RIGHT focused. The detail pane is never the "active" pane in this design (no Tab slot, no Textual focus).
- App-level priority bindings forward to the active pane (logs pane's ``action_scroll_up``/``action_scroll_down`` for ``up/down``, ``action_load`` for ``enter``, ``action_reload`` for ``r``).
- ``JobRunDetailPane`` loses its ``r``-refresh path entirely. The 5-s poller already drives detail updates; manual refresh of detail wasn't a documented affordance and removing it keeps the ``r`` semantic clean (LEFT → re-list runs; RIGHT → reload logs).

- [ ] **Step 1: Compose change**

```python
    def compose(self) -> ComposeResult:
        self._picker = ApplicationPicker(self._vm.applications, hub=self._hub, id="emr-app-picker")
        self._left = JobRunsPane(self._vm.job_runs, hub=self._hub, id="emr-runs-pane")
        self._right_detail = JobRunDetailPane(
            self._vm.job_run_detail, hub=self._hub, id="emr-detail-pane"
        )
        self._right_logs = JobRunLogsPane(
            self._vm.job_run_logs, hub=self._hub, id="emr-logs-pane"
        )
        with Vertical(classes="emr-left-column"):
            with Horizontal(classes="emr-app-box", id="emr-app-box"):
                yield self._picker
            yield self._left
        with Vertical(classes="emr-right-column"):
            yield self._right_detail
            yield self._right_logs
```

CSS update — change the existing block AND add the right-column rules. The page CSS currently has:

```css
EmrServerlessPage > .emr-left-column {
    width: 1fr;   /* keep */
}
EmrServerlessPage > JobRunDetailPane {
    width: 1fr;   /* DELETE — detail is now inside the right column */
}
```

Replace with:

```css
/* User feedback: "the left job runs pane … to occupy 1/3 of the
   horizontal space … the job details pane to occupy the remaining
   2/3". 1fr:2fr is the 1:3 / 2:3 ratio the user asked for. */
EmrServerlessPage > .emr-left-column {
    width: 1fr;
    height: 1fr;
    layout: vertical;
}
EmrServerlessPage > .emr-right-column {
    width: 2fr;
    height: 1fr;
    layout: vertical;
}
/* 50/50 vertical split inside the right column. Both halves get
   ``height: 1fr`` so each takes exactly half regardless of how
   tall the detail content is (it scrolls within its half rather
   than pushing the logs pane off-screen). */
EmrServerlessPage > .emr-right-column > JobRunDetailPane {
    height: 1fr;
}
EmrServerlessPage > .emr-right-column > JobRunLogsPane {
    height: 1fr;
}
```

- [ ] **Step 1b: Verify the JobRunsPane columns survive the LEFT-column shrink — apply Option B (ellipsis truncation)**

Run the EMR page snapshot tests against the new 1fr/2fr ratio. Apply **Option B**: keep STATUS / NAME / TIME widths the same; ensure NAME truncates with an ellipsis when its content overflows the narrower column. Implementation: ensure the row Static (rendered via ``_format_run_row``) gets a CSS rule like ``text-overflow: ellipsis`` and the column's flex width holds. Regenerate the 10 EMR populated snapshots after the change. (Options A — shrink STATUS/TIME — and C — drop TIME from LEFT — were considered and explicitly rejected; do NOT apply them.)

- [ ] **Step 2: 3-slot ``_cycle``**

Track an explicit ``_focus_order: tuple[Widget, ...]`` and step forward / back through it; ``call_after_refresh`` to focus the new slot.

- [ ] **Step 3: ``_emr_active_pane`` returns LOGS when it has focus**

- [ ] **Step 4: App-level priority forwards**

Add a logs-pane branch to ``_move_cursor`` (Up/Down → ``action_scroll_up``/``scroll_down``), ``action_descend`` (Enter → ``action_load``), and ``action_refresh`` (``r`` → ``action_reload``).

- [ ] **Step 5: Integration tests**

Pin: 3-slot Tab cycle hits all three panes; the keys above reach the logs pane when it's focused; logs pane stays IDLE after ``select_job_run`` (does NOT auto-fetch).

- [ ] **Step 6: Commit**

```bash
git commit -am "feat(emr): page layout — right column stacked + 3-slot Tab + priority forwards"
```

---

## Task 10: Per-theme CSS for the logs pane

**Files:**
- Modify: 10 × ``src/aws_tui/ui/themes/*.tcss``

**Interfaces:**
- Selectors needed: ``JobRunLogsPane``, ``JobRunLogsPane:focus-within``, ``JobRunLogsPane > .logs-chip-row``, ``JobRunLogsPane > .logs-chip-row > .logs-chip``, ``JobRunLogsPane > .logs-chip-row > .logs-chip.-active``, ``JobRunLogsPane > VerticalScroll``, ``JobRunLogsPane .logs-line``, ``JobRunLogsPane .logs-line.-match``, ``JobRunLogsPane > .logs-placeholder``, ``JobRunLogsPane > .logs-footer``, ``#emr-app-picker-dropdown`` (drop the deprecated dual selector this loop introduced and rolled back).

- [ ] **Step 1: Author the block for one theme (nord)** matching ``JobRunsPane``'s chrome (``$bg``, ``border: solid $rule-dim``, ``:focus-within → $accent``).

- [ ] **Step 2: Batch-apply the block to all 10 themes** via a one-shot script (see PR #75 / PR #81 for the pattern).

- [ ] **Step 3: Run snapshot tests + regenerate** the 20 EMR populated snapshots + 10 main-screen snapshots.

- [ ] **Step 4: Commit**

```bash
git commit -am "feat(emr): per-theme tcss for JobRunLogsPane chrome (10 themes)"
```

---

## Task 11: Snapshot tests + content-presence guards

**Files:**
- Create: ``tests/snapshot/apps/emr_logs.py``
- Create: ``tests/snapshot/test_emr_logs.py``
- Modify: ``tests/snapshot/__snapshots__/`` — new ``test_emr_logs/`` subtree

**Interfaces:**
- Snapshot fixtures (one ``App`` per state): EMPTY_TARGET, IDLE, LOADING, READY (with a few sample lines), NO_LOG_CONFIG, ERROR.

- [ ] **Step 1: Each snapshot has a paired content-presence guard** asserting the placeholder text (per the PR #53/#63 invariant — uniformly-blank rendering must not pass parity).

- [ ] **Step 2: Snapshot regeneration**

```bash
uv run pytest tests/snapshot/test_emr_logs.py --snapshot-update
```

- [ ] **Step 3: Commit**

```bash
git commit -am "test(emr): JobRunLogsPane snapshots — 6 state placeholders × 10 themes"
```

---

## Task 12: HintLegend + keymap chip wiring

**Files:**
- Modify: ``src/aws_tui/vm/chrome/hint_legend_vm.py``
- Modify: ``src/aws_tui/infra/keymap_store.py``
- Test: ``tests/unit/vm/chrome/test_hint_legend.py``

- [ ] **Step 1: Add ``emr.logs.filter`` to ``_SERVICE_ACTIONS["emr-serverless"]`` and ``_ACTION_LABELS``**

```python
_SERVICE_ACTIONS["emr-serverless"] = (
    "pane.switch_focus",
    "pane.descend",
    "pane.refresh",
    "app.swap_source",
    "emr.clone",
    "emr.logs.filter",  # new
)

_ACTION_LABELS["emr.logs.filter"] = "filter logs"
```

- [ ] **Step 2: ``KeymapStore.DEFAULT_BINDINGS["emr.logs.filter"] = ("f",)``**

- [ ] **Step 3: Update existing hint-legend label assertion test** for the new chip presence.

- [ ] **Step 4: Commit**

```bash
git commit -am "feat(emr): HintLegend gains 'filter logs' chip + 'f' keybinding"
```

---

## Task 13: Documentation catchup

**Files:**
- Modify: ``CHANGELOG.md`` (``[Unreleased] ### Added``)
- Modify: ``docs/keybindings.md`` §1.8 — Logs section + ``Enter`` / ``r`` / ``f`` rows + 3-slot Tab cycle update
- Modify: ``docs/architecture.md`` — ``vm/emr_serverless/`` list adds ``JobRunLogsVM``
- Modify: ``docs/superpowers/specs/2026-06-25-emr-serverless-service-design.md`` — Status amendment block

- [ ] **Step 1-4 (per file): apply the edits**

- [ ] **Step 5: Commit**

```bash
git commit -am "docs: EMR logs pane — CHANGELOG, keybindings, architecture, spec amendment"
```

---

## Task 14: End-to-end smoke test + final validation

**Files:**
- Modify: ``tests/integration/test_emr_page.py``

- [ ] **Step 1: Add a Tab-cycle integration test** — mount the page, assert Tab visits all 3 slots in order, assert Enter on the LOGS slot drives the load worker (use the existing ``_InMemoryEmr`` extended with stub log content from Task 2's test pattern).

- [ ] **Step 2: Final full-suite validation**

```bash
uv run pytest
uv run ruff check src tests
uv run ruff format --check src tests
uv run mypy src
bash scripts/check-layers.sh
```

Expected: 1200+ default-tier tests + 230+ snapshots green; ruff + mypy clean.

- [ ] **Step 3: Final commit + push + PR**

```bash
git commit -am "test(emr): end-to-end Tab cycle through 3-slot + Enter-on-logs smoke"
git push -u origin feat/emr-job-run-logs-pane
gh pr create --title "feat(emr): job-run logs pane — on-demand S3 stream + grep filter" --body "..."
```

---

## Out of scope (v1, deferred to follow-ups)

- Config-file persistence for custom filter patterns (v1 is session-only).
- CloudWatch Logs as an alternative source (only S3 monitoring logs in v1).
- "Tail follow" mode for actively-running jobs.
- Per-executor log multiplex (v1 picks one file at a time).
- Searching within already-loaded lines (separate slash-search UI in a follow-up).
- Multi-line stack-trace folding.
- Color-coded log levels.

These are all reasonable v1.1 polish items; v1 prioritises the user's explicit ask: "pull / unzip / grep / display, on demand, with progress feedback".
