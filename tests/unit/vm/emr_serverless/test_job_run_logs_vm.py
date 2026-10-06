from __future__ import annotations

import asyncio
from typing import cast

import pytest
from vmx import NULL_DISPATCHER, MessageHub
from vmx.messages.protocols import Message

from aws_tui.domain.emr_logs import (
    DEFAULT_LOG_FILTER,
    EmrServerlessLogsClient,
    LogChunk,
    LogFile,
    LogFileKind,
)
from aws_tui.domain.filesystem import (
    AuthRequiredError,
    PermissionDeniedError,
    ProviderUnreachableError,
)
from aws_tui.vm.emr_serverless.job_run_logs_vm import JobRunLogsVM, LogsState
from aws_tui.vm.file_manager.pane_vm import PaneState


def _hub() -> MessageHub[Message]:
    return cast("MessageHub[Message]", MessageHub())


def _make() -> JobRunLogsVM:
    hub = _hub()
    # Create a stub client with a dummy session (not used by set_target paths).
    stub_client = EmrServerlessLogsClient(
        session=cast("object", None),
        region_name="us-east-1",
    )
    vm = JobRunLogsVM(
        client=stub_client,
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


# ---------------------------------------------------------------------------
# load() tests — written first per TDD before implementation exists
# ---------------------------------------------------------------------------

_STDERR_FILE = LogFile(
    key="logs/applications/app1/jobs/run1/SPARK_DRIVER/stderr.gz",
    kind=LogFileKind.DRIVER_STDERR,
    size=200,
)
_STDOUT_FILE = LogFile(
    key="logs/applications/app1/jobs/run1/SPARK_DRIVER/stdout.gz",
    kind=LogFileKind.DRIVER_STDOUT,
    size=100,
)
_ONE_CHUNK = LogChunk(
    lines=("ERROR: something exploded",),
    bytes_read=50,
    lines_scanned=10,
    matched_count=1,
    truncated=False,
)
_LOG_URI = "s3://my-bucket/logs/"


async def test_load_preconditions_not_met_returns_without_state_change() -> None:
    """load() with None app_id / run_id / log_uri is a silent no-op."""
    vm = _make()
    # No set_target call — app_id, run_id, log_uri are all None.
    initial_state = vm.state
    await vm.load()
    assert vm.state is initial_state
    vm.dispose()


async def test_shutdown_cancels_and_drains_blocked_log_stream(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    started = asyncio.Event()
    cancelled = asyncio.Event()

    async def _list_files(**_kwargs: object) -> list[LogFile]:
        return [_STDERR_FILE]

    async def _blocked_stream(**_kwargs: object):  # type: ignore[return]
        started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancelled.set()
            raise
        yield _ONE_CHUNK

    monkeypatch.setattr("aws_tui.domain.emr_logs.list_log_files", _list_files)
    monkeypatch.setattr("aws_tui.domain.emr_logs.stream_log", _blocked_stream)
    vm = _make()
    vm.set_target("app1", "run1", _LOG_URI)
    load = asyncio.create_task(vm.load())
    await asyncio.wait_for(started.wait(), timeout=1)

    await vm.shutdown()
    await load

    assert cancelled.is_set()
    assert not vm._operations.tasks  # type: ignore[attr-defined]
    vm.dispose()


async def test_load_while_already_loading_proceeds(monkeypatch: pytest.MonkeyPatch) -> None:
    """A second load() call while state == LOADING must NOT short-circuit.

    The VM-level "if state is LOADING: return" guard used to live
    here but stranded the pane on the LOADING placeholder forever
    after a CancelledError (round-23): the prior task raised out
    WITHOUT resetting state, the new worker hit the guard and
    no-oped, the user was stuck. The canonical idempotence guard
    now lives at the View layer via ``run_worker(exclusive=True,
    group="emr-logs")`` — it cancels the prior worker before
    re-entering, so a "second load while LOADING" IS the
    cancellation re-entry path. This test pins that the VM proceeds
    so the new worker can re-establish target + lines + cache.
    """
    list_call_count = 0

    async def _list_files(**kwargs: object) -> list[LogFile]:
        nonlocal list_call_count
        list_call_count += 1
        return [_STDERR_FILE]

    monkeypatch.setattr(
        "aws_tui.domain.emr_logs.list_log_files",
        _list_files,
        raising=False,
    )

    vm = _make()
    vm.set_target("app1", "run1", _LOG_URI)
    # Force into LOADING state directly to simulate a cancelled-but-
    # state-not-reset prior task. The VM-level guard is intentionally
    # gone (see docstring) so load() proceeds.
    vm._state = LogsState.LOADING  # type: ignore[assignment]
    await vm.load()
    assert list_call_count == 1
    vm.dispose()


async def test_load_happy_path_ready_with_lines(monkeypatch: pytest.MonkeyPatch) -> None:
    """Happy path: IDLE → LOADING → READY; lines are populated from the chunk."""

    async def _list_files(**kwargs: object) -> list[LogFile]:
        return [_STDERR_FILE]

    async def _stream_log(**kwargs: object):  # type: ignore[return]
        yield _ONE_CHUNK

    monkeypatch.setattr(
        "aws_tui.domain.emr_logs.list_log_files",
        _list_files,
        raising=False,
    )
    monkeypatch.setattr(
        "aws_tui.domain.emr_logs.stream_log",
        _stream_log,
        raising=False,
    )

    vm = _make()
    vm.set_target("app1", "run1", _LOG_URI)
    assert vm.state is LogsState.IDLE

    await vm.load()

    assert vm.state is LogsState.READY
    assert vm.lines == ("ERROR: something exploded",)
    assert vm.current_file == _STDERR_FILE
    vm.dispose()


async def test_load_prefers_driver_stderr_over_stdout(monkeypatch: pytest.MonkeyPatch) -> None:
    """Default file selection picks DRIVER_STDERR first."""

    async def _list_files(**kwargs: object) -> list[LogFile]:
        return [_STDOUT_FILE, _STDERR_FILE]  # stderr is second

    async def _stream_log(**kwargs: object):  # type: ignore[return]
        yield _ONE_CHUNK

    monkeypatch.setattr(
        "aws_tui.domain.emr_logs.list_log_files",
        _list_files,
        raising=False,
    )
    monkeypatch.setattr(
        "aws_tui.domain.emr_logs.stream_log",
        _stream_log,
        raising=False,
    )

    vm = _make()
    vm.set_target("app1", "run1", _LOG_URI)
    await vm.load()

    assert vm.current_file is not None
    assert vm.current_file.kind is LogFileKind.DRIVER_STDERR
    vm.dispose()


async def test_load_falls_back_to_driver_stdout_when_no_stderr(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Falls back to DRIVER_STDOUT when no DRIVER_STDERR exists."""

    async def _list_files(**kwargs: object) -> list[LogFile]:
        return [_STDOUT_FILE]

    async def _stream_log(**kwargs: object):  # type: ignore[return]
        yield _ONE_CHUNK

    monkeypatch.setattr(
        "aws_tui.domain.emr_logs.list_log_files",
        _list_files,
        raising=False,
    )
    monkeypatch.setattr(
        "aws_tui.domain.emr_logs.stream_log",
        _stream_log,
        raising=False,
    )

    vm = _make()
    vm.set_target("app1", "run1", _LOG_URI)
    await vm.load()

    assert vm.current_file is not None
    assert vm.current_file.kind is LogFileKind.DRIVER_STDOUT
    vm.dispose()


def test_select_log_file_key_distinguishes_multiple_executors() -> None:
    vm = _make()
    first = LogFile("logs/SPARK_EXECUTOR/1/stderr.gz", LogFileKind.EXECUTOR_STDERR)
    second = LogFile("logs/SPARK_EXECUTOR/2/stderr.gz", LogFileKind.EXECUTOR_STDERR)
    vm._available_files = (first, second)

    vm.select_log_file_key(second.key)

    assert vm.current_file is second
    vm.dispose()


async def test_load_no_files_transitions_to_no_files(monkeypatch: pytest.MonkeyPatch) -> None:
    """If list_log_files returns [], state becomes NO_FILES."""

    async def _list_empty(**kwargs: object) -> list[LogFile]:
        return []

    monkeypatch.setattr(
        "aws_tui.domain.emr_logs.list_log_files",
        _list_empty,
        raising=False,
    )

    vm = _make()
    vm.set_target("app1", "run1", _LOG_URI)
    await vm.load()

    assert vm.state is LogsState.NO_FILES
    vm.dispose()


async def test_load_truncated_chunk_transitions_to_truncated(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A chunk with truncated=True causes state == TRUNCATED."""
    truncated_chunk = LogChunk(
        lines=("WARN: truncated",),
        bytes_read=100 * 1024 * 1024,
        lines_scanned=5000,
        matched_count=1,
        truncated=True,
    )

    async def _list_files(**kwargs: object) -> list[LogFile]:
        return [_STDERR_FILE]

    async def _stream_truncated(**kwargs: object):  # type: ignore[return]
        yield truncated_chunk

    monkeypatch.setattr(
        "aws_tui.domain.emr_logs.list_log_files",
        _list_files,
        raising=False,
    )
    monkeypatch.setattr(
        "aws_tui.domain.emr_logs.stream_log",
        _stream_truncated,
        raising=False,
    )

    vm = _make()
    vm.set_target("app1", "run1", _LOG_URI)
    await vm.load()

    assert vm.state is LogsState.TRUNCATED
    vm.dispose()


async def test_load_cache_hit_skips_stream_on_second_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A second load() for the same target+filter uses the cache; stream_log not called again."""
    stream_call_count = 0

    async def _list_files(**kwargs: object) -> list[LogFile]:
        return [_STDERR_FILE]

    async def _stream_log(**kwargs: object):  # type: ignore[return]
        nonlocal stream_call_count
        stream_call_count += 1
        yield _ONE_CHUNK

    monkeypatch.setattr(
        "aws_tui.domain.emr_logs.list_log_files",
        _list_files,
        raising=False,
    )
    monkeypatch.setattr(
        "aws_tui.domain.emr_logs.stream_log",
        _stream_log,
        raising=False,
    )

    vm = _make()
    vm.set_target("app1", "run1", _LOG_URI)
    await vm.load()  # first call — populates cache
    assert vm.state is LogsState.READY
    assert stream_call_count == 1

    await vm.load()  # second call — should hit cache
    assert vm.state is LogsState.READY
    assert stream_call_count == 1  # still 1 — stream not called again
    assert vm.lines == ("ERROR: something exploded",)
    assert vm.bytes_read == _ONE_CHUNK.bytes_read
    assert vm.lines_scanned == _ONE_CHUNK.lines_scanned
    vm.dispose()


async def test_explicit_refresh_bypasses_same_size_cache(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stream_call_count = 0

    async def _list_files(**kwargs: object) -> list[LogFile]:
        return [_STDERR_FILE]

    async def _stream_log(**kwargs: object):  # type: ignore[return]
        nonlocal stream_call_count
        stream_call_count += 1
        yield _ONE_CHUNK

    monkeypatch.setattr("aws_tui.domain.emr_logs.list_log_files", _list_files, raising=False)
    monkeypatch.setattr("aws_tui.domain.emr_logs.stream_log", _stream_log, raising=False)
    vm = _make()
    vm.set_target("app1", "run1", _LOG_URI)

    await vm.load()
    await vm.load(use_cache=False)

    assert stream_call_count == 2
    vm.dispose()


async def test_reload_replaces_selected_file_missing_from_fresh_listing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    listings = iter(([_STDERR_FILE], [_STDOUT_FILE]))
    streamed_keys: list[str] = []

    async def _list_files(**kwargs: object) -> list[LogFile]:
        return next(listings)

    async def _stream_log(**kwargs: object):  # type: ignore[return]
        streamed_keys.append(cast("LogFile", kwargs["log_file"]).key)
        yield _ONE_CHUNK

    monkeypatch.setattr("aws_tui.domain.emr_logs.list_log_files", _list_files, raising=False)
    monkeypatch.setattr("aws_tui.domain.emr_logs.stream_log", _stream_log, raising=False)

    vm = _make()
    vm.set_target("app1", "run1", _LOG_URI)
    await vm.load()
    await vm.load()

    assert vm.current_file == _STDOUT_FILE
    assert streamed_keys == [_STDERR_FILE.key, _STDOUT_FILE.key]
    vm.dispose()


async def test_reload_clears_selected_file_when_listing_becomes_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    listings = iter(([_STDERR_FILE], []))

    async def _list_files(**kwargs: object) -> list[LogFile]:
        return next(listings)

    async def _stream_log(**kwargs: object):  # type: ignore[return]
        yield _ONE_CHUNK

    monkeypatch.setattr("aws_tui.domain.emr_logs.list_log_files", _list_files, raising=False)
    monkeypatch.setattr("aws_tui.domain.emr_logs.stream_log", _stream_log, raising=False)

    vm = _make()
    vm.set_target("app1", "run1", _LOG_URI)
    await vm.load()
    await vm.load()

    assert vm.state is LogsState.NO_FILES
    assert vm.current_file is None
    vm.dispose()


async def test_reload_restreams_same_key_when_listed_size_grows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    grown = LogFile(key=_STDERR_FILE.key, kind=_STDERR_FILE.kind, size=_STDERR_FILE.size + 1)
    listings = iter(([_STDERR_FILE], [grown]))
    stream_count = 0

    async def _list_files(**kwargs: object) -> list[LogFile]:
        return next(listings)

    async def _stream_log(**kwargs: object):  # type: ignore[return]
        nonlocal stream_count
        stream_count += 1
        yield _ONE_CHUNK

    monkeypatch.setattr("aws_tui.domain.emr_logs.list_log_files", _list_files, raising=False)
    monkeypatch.setattr("aws_tui.domain.emr_logs.stream_log", _stream_log, raising=False)

    vm = _make()
    vm.set_target("app1", "run1", _LOG_URI)
    await vm.load()
    await vm.load()

    assert vm.current_file == grown
    assert stream_count == 2
    vm.dispose()


async def test_load_provider_error_transitions_to_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """ProviderError during list_log_files → state ERROR, error_text populated."""
    from aws_tui.domain.filesystem import ProviderUnreachableError

    async def _list_raises(**kwargs: object) -> list[LogFile]:
        raise ProviderUnreachableError("network blip")

    monkeypatch.setattr(
        "aws_tui.domain.emr_logs.list_log_files",
        _list_raises,
        raising=False,
    )

    vm = _make()
    vm.set_target("app1", "run1", _LOG_URI)
    await vm.load()

    assert vm.state is LogsState.ERROR
    assert vm.error_text is not None
    vm.dispose()


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (AuthRequiredError("missing"), PaneState.AUTH_REQUIRED),
        (PermissionDeniedError("denied"), PaneState.FORBIDDEN),
        (ProviderUnreachableError("offline"), PaneState.UNREACHABLE),
    ],
)
async def test_load_retains_typed_provider_failure_for_credential_recovery(
    monkeypatch: pytest.MonkeyPatch,
    error: Exception,
    expected: PaneState,
) -> None:
    async def _list_raises(**kwargs: object) -> list[LogFile]:
        raise error

    monkeypatch.setattr(
        "aws_tui.domain.emr_logs.list_log_files",
        _list_raises,
        raising=False,
    )
    vm = _make()
    vm.set_target("app1", "run1", _LOG_URI)

    await vm.load()

    assert vm.credential_recovery_state is expected
    vm.dispose()


async def test_load_provider_error_during_stream_transitions_to_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ProviderError raised inside stream_log → state ERROR, error_text populated."""
    from aws_tui.domain.filesystem import PermissionDeniedError

    async def _list_files(**kwargs: object) -> list[LogFile]:
        return [_STDERR_FILE]

    async def _stream_raises(**kwargs: object):  # type: ignore[return]
        raise PermissionDeniedError("access denied")
        yield  # pragma: no cover — make it an async generator

    monkeypatch.setattr(
        "aws_tui.domain.emr_logs.list_log_files",
        _list_files,
        raising=False,
    )
    monkeypatch.setattr(
        "aws_tui.domain.emr_logs.stream_log",
        _stream_raises,
        raising=False,
    )

    vm = _make()
    vm.set_target("app1", "run1", _LOG_URI)
    await vm.load()

    assert vm.state is LogsState.ERROR
    assert vm.error_text is not None
    vm.dispose()


async def test_load_cancelled_error_propagates(monkeypatch: pytest.MonkeyPatch) -> None:
    """CancelledError is NOT swallowed — it is re-raised out of load()."""

    async def _list_cancelled(**kwargs: object) -> list[LogFile]:
        raise asyncio.CancelledError

    monkeypatch.setattr(
        "aws_tui.domain.emr_logs.list_log_files",
        _list_cancelled,
        raising=False,
    )

    vm = _make()
    vm.set_target("app1", "run1", _LOG_URI)

    with pytest.raises(asyncio.CancelledError):
        await vm.load()

    vm.dispose()


async def test_load_unexpected_exception_transitions_to_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Unhandled exception → state ERROR with 'unexpected error: ...' text."""

    async def _list_boom(**kwargs: object) -> list[LogFile]:
        raise RuntimeError("boom!")

    monkeypatch.setattr(
        "aws_tui.domain.emr_logs.list_log_files",
        _list_boom,
        raising=False,
    )

    vm = _make()
    vm.set_target("app1", "run1", _LOG_URI)
    await vm.load()

    assert vm.state is LogsState.ERROR
    assert vm.error_text is not None
    assert "unexpected error" in (vm.error_text or "")
    vm.dispose()


async def test_load_lines_capped_at_max_matched_lines(monkeypatch: pytest.MonkeyPatch) -> None:
    """Lines buffer is capped to _MAX_MATCHED_LINES; tail is kept on overflow."""
    from aws_tui.vm.emr_serverless.job_run_logs_vm import _MAX_MATCHED_LINES

    # Two chunks whose combined length exceeds the cap by 1 line.
    over = _MAX_MATCHED_LINES + 1
    first_lines = tuple(f"line-{i}" for i in range(over))
    first_chunk = LogChunk(
        lines=first_lines,
        bytes_read=1024,
        lines_scanned=over,
        matched_count=over,
        truncated=False,
    )
    last_line = "final-line"
    second_chunk = LogChunk(
        lines=(last_line,),
        bytes_read=2048,
        lines_scanned=over + 1,
        matched_count=1,
        truncated=False,
    )

    async def _list_files(**kwargs: object) -> list[LogFile]:
        return [_STDERR_FILE]

    async def _stream_two_chunks(**kwargs: object):  # type: ignore[return]
        yield first_chunk
        yield second_chunk

    monkeypatch.setattr(
        "aws_tui.domain.emr_logs.list_log_files",
        _list_files,
        raising=False,
    )
    monkeypatch.setattr(
        "aws_tui.domain.emr_logs.stream_log",
        _stream_two_chunks,
        raising=False,
    )

    vm = _make()
    vm.set_target("app1", "run1", _LOG_URI)
    await vm.load()

    assert vm.state is LogsState.READY
    assert len(vm.lines) == _MAX_MATCHED_LINES
    # The tail is kept — the last line of the second chunk must be present.
    assert vm.lines[-1] == last_line
    # The retained count is NOT the answer to the user's filter. Reporting
    # len(lines) as "matches" understated it by every dropped line, and for an
    # error-first filter the dropped head is the originating stack trace.
    assert vm.matched_count == over + 1
    assert vm.matches_capped is True
    vm.dispose()


async def test_matched_count_is_not_capped_when_under_the_display_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _list_files(**kwargs: object) -> list[LogFile]:
        return [_STDERR_FILE]

    async def _stream(**kwargs: object):  # type: ignore[return]
        yield _ONE_CHUNK

    monkeypatch.setattr("aws_tui.domain.emr_logs.list_log_files", _list_files, raising=False)
    monkeypatch.setattr("aws_tui.domain.emr_logs.stream_log", _stream, raising=False)

    vm = _make()
    vm.set_target("app1", "run1", _LOG_URI)
    await vm.load()

    assert vm.matched_count == len(vm.lines)
    assert vm.matches_capped is False
    vm.dispose()


async def test_cache_hit_preserves_truncation_state(monkeypatch: pytest.MonkeyPatch) -> None:
    """A second load() for the same target+filter preserves TRUNCATED state from cache."""
    truncated_chunk = LogChunk(
        lines=("WARN: truncated",),
        bytes_read=100 * 1024 * 1024,
        lines_scanned=5000,
        matched_count=1,
        truncated=True,
    )
    stream_call_count = 0

    async def _list_files(**kwargs: object) -> list[LogFile]:
        return [_STDERR_FILE]

    async def _stream_truncated(**kwargs: object):  # type: ignore[return]
        nonlocal stream_call_count
        stream_call_count += 1
        yield truncated_chunk

    monkeypatch.setattr(
        "aws_tui.domain.emr_logs.list_log_files",
        _list_files,
        raising=False,
    )
    monkeypatch.setattr(
        "aws_tui.domain.emr_logs.stream_log",
        _stream_truncated,
        raising=False,
    )

    vm = _make()
    vm.set_target("app1", "run1", _LOG_URI)
    await vm.load()  # first call — populates cache with truncated=True

    assert vm.state is LogsState.TRUNCATED
    assert stream_call_count == 1

    await vm.load()  # second call — should hit cache and preserve TRUNCATED

    assert vm.state is LogsState.TRUNCATED
    assert stream_call_count == 1  # still 1 — stream not called again
    assert vm.lines == ("WARN: truncated",)
    vm.dispose()


# ── LRU cache eviction + app-switch clearing ──────────────────────────────────


async def test_cache_evicts_oldest_entry_when_cap_exceeded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """LRU cap pinning: after loading more distinct (file, filter)
    targets than ``_CACHE_MAX_ENTRIES``, the OLDEST entry must be
    evicted so the cache size stays bounded. Without this the cache
    grew unbounded across a long EMR session — user feedback
    (post-PR-#92): "we need to make sure those logs get pulled into
    a temp space and get disposed every once in a while so they
    don't accumulate"."""
    from aws_tui.vm.emr_serverless.job_run_logs_vm import _CACHE_MAX_ENTRIES

    async def _list_files(**kwargs: object) -> list[LogFile]:
        return [_STDERR_FILE]

    stream_calls: list[str] = []

    async def _stream_log(**kwargs: object):  # type: ignore[return]
        stream_calls.append(str(kwargs.get("log_file")))
        yield _ONE_CHUNK

    monkeypatch.setattr("aws_tui.domain.emr_logs.list_log_files", _list_files, raising=False)
    monkeypatch.setattr("aws_tui.domain.emr_logs.stream_log", _stream_log, raising=False)

    vm = _make()
    # Load more distinct runs than the cap. Each set_target+load
    # populates one cache entry. Same application_id throughout so
    # the app-switch clear path doesn't fire — this isolates the
    # cap-driven eviction.
    target_count = _CACHE_MAX_ENTRIES + 2
    for i in range(target_count):
        vm.set_target("app1", f"run-{i}", _LOG_URI)
        await vm.load()
    # Cache must NOT grow unbounded.
    assert len(vm._cache) == _CACHE_MAX_ENTRIES, (  # type: ignore[attr-defined]
        f"LRU cap broken: cache holds {len(vm._cache)} entries "  # type: ignore[attr-defined]
        f"after {target_count} loads, cap is {_CACHE_MAX_ENTRIES}."
    )
    # The two oldest (run-0, run-1) must be evicted; the newest
    # _CACHE_MAX_ENTRIES (run-2 … run-N) must remain.
    cached_run_ids = {key[1] for key in vm._cache}  # type: ignore[attr-defined]
    assert "run-0" not in cached_run_ids
    assert "run-1" not in cached_run_ids
    for i in range(2, target_count):
        assert f"run-{i}" in cached_run_ids
    vm.dispose()


async def test_set_target_clears_cache_on_application_switch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """User feedback drove the LRU cap; the symmetric concern is
    "cross-application leakage". Switching applications must drop
    the prior app's cache entries entirely — they can't be revisited
    in this UI session and only bloat memory."""

    async def _list_files(**kwargs: object) -> list[LogFile]:
        return [_STDERR_FILE]

    async def _stream_log(**kwargs: object):  # type: ignore[return]
        yield _ONE_CHUNK

    monkeypatch.setattr("aws_tui.domain.emr_logs.list_log_files", _list_files, raising=False)
    monkeypatch.setattr("aws_tui.domain.emr_logs.stream_log", _stream_log, raising=False)

    vm = _make()
    vm.set_target("app1", "run1", _LOG_URI)
    await vm.load()
    assert len(vm._cache) == 1  # type: ignore[attr-defined]
    # Switch to a different application — the cache must clear.
    vm.set_target("app2", "run1", _LOG_URI)
    assert vm._cache == {}, "Cache must clear on application switch."  # type: ignore[attr-defined]
    vm.dispose()


async def test_dispose_clears_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    """dispose() must flush the cache so a recycled VM (test
    harnesses, future content-host reuse) doesn't carry stale
    entries forward."""

    async def _list_files(**kwargs: object) -> list[LogFile]:
        return [_STDERR_FILE]

    async def _stream_log(**kwargs: object):  # type: ignore[return]
        yield _ONE_CHUNK

    monkeypatch.setattr("aws_tui.domain.emr_logs.list_log_files", _list_files, raising=False)
    monkeypatch.setattr("aws_tui.domain.emr_logs.stream_log", _stream_log, raising=False)

    vm = _make()
    vm.set_target("app1", "run1", _LOG_URI)
    await vm.load()
    assert len(vm._cache) == 1  # type: ignore[attr-defined]
    vm.dispose()
    assert vm._cache == {}  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_a_target_switch_mid_list_drops_the_previous_runs_file_list(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The stale-list guard after ``list_files`` was pinned by nothing.

    ``load()`` captures ``target`` before awaiting ``list_files`` and re-checks
    it afterwards — the comment calls it "target changed mid-flight; drop the
    stale list". Neutralising that check survived the whole repo suite: run A's
    file list would be written into ``_available_files`` after the user had
    already switched to run B, driving B's file selection and poisoning the LRU
    cache under B's key, so the logs pane showed another run's output.
    """
    switched = asyncio.Event()
    released = asyncio.Event()

    async def _list_files(**_kwargs: object) -> list[LogFile]:
        switched.set()
        await released.wait()
        return [_STDERR_FILE]

    monkeypatch.setattr(
        "aws_tui.domain.emr_logs.list_log_files",
        _list_files,
        raising=False,
    )

    vm = _make()
    vm.set_target("app1", "run1", _LOG_URI)
    load_task = asyncio.create_task(vm.load())

    # Let run1's list_files start, then switch the pane to run2 while it is
    # still in flight.
    await asyncio.wait_for(switched.wait(), timeout=2)
    vm.set_target("app1", "run2", _LOG_URI)
    released.set()
    await asyncio.wait_for(load_task, timeout=2)

    assert vm.available_files == (), "run1's file list was applied after the pane switched to run2"
    assert vm.current_file is None


@pytest.mark.asyncio
async def test_set_target_with_unchanged_ids_keeps_the_loaded_log_buffer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A no-op ``set_target`` must not blank the pane.

    The page re-invokes ``set_target`` with unchanged ids on every
    detail-to-logs sync, so this early return runs constantly. Removing it
    survived the whole repo suite while each poll cleared ``_lines``,
    ``_bytes_read``, ``_lines_scanned`` and ``_available_files`` and reset the
    state — the user's loaded log buffer blanked underneath them.
    """

    async def _list_files(**_kwargs: object) -> list[LogFile]:
        return [_STDERR_FILE]

    monkeypatch.setattr(
        "aws_tui.domain.emr_logs.list_log_files",
        _list_files,
        raising=False,
    )

    vm = _make()
    vm.set_target("app1", "run1", _LOG_URI)
    await vm.load()

    loaded_files = vm.available_files
    loaded_state = vm.state
    assert loaded_files, "precondition: the pane has a loaded file list"

    # Exactly what the page's detail->logs sync does on every poll.
    vm.set_target("app1", "run1", _LOG_URI)

    assert vm.available_files == loaded_files, "a no-op target sync cleared the pane"
    assert vm.state is loaded_state


def _cw_vm(monkeypatch, *, config=None, uri=None, empty=False):
    from aws_tui.demo.in_memory_emr import InMemoryEmr
    from aws_tui.domain.emr_cloudwatch_logs import CloudWatchLogEvent, CloudWatchLogStream
    from aws_tui.domain.emr_serverless import CloudWatchLogConfiguration
    from aws_tui.vm.emr_serverless import job_run_logs_vm as module

    monkeypatch.setattr(module, "_now_ms", lambda: 100_000)
    fake = InMemoryEmr()
    cfg = config or CloudWatchLogConfiguration(True)
    group = cfg.log_group_name or "/aws/emr-serverless"
    root = (cfg.log_stream_name_prefix or "") + "/applications/a/jobs/r/"
    names = [
        root + suffix
        for suffix in (
            "SPARK_DRIVER",
            "attempts/1/SPARK_DRIVER",
            "attempts/2/SPARK_DRIVER",
            "SPARK_EXECUTOR/1",
            "SPARK_EXECUTOR/2",
        )
    ]
    if not empty:
        for i, name in enumerate(names):
            fake.add_cloudwatch_stream(
                application_id="a",
                job_run_id="r",
                log_group_name=group,
                stream=CloudWatchLogStream(
                    name, "SPARK_DRIVER" if i < 3 else "SPARK_EXECUTOR", i if 0 < i < 3 else None
                ),
                events=(CloudWatchLogEvent(str(i), 1000, 1001, f"ERROR CloudWatch {i}"),),
            )
    vm = JobRunLogsVM(client=fake, hub=_hub(), dispatcher=NULL_DISPATCHER)
    vm.construct()
    vm.set_target("a", "r", uri, cloudwatch=cfg, run_created_at_ms=1000)
    return vm, fake, names


async def test_cloudwatch_only_load_and_exact_stream_selection(monkeypatch):
    from aws_tui.vm.emr_serverless.job_run_logs_vm import LogSource, LogSourceState

    vm, _fake, names = _cw_vm(monkeypatch)
    assert vm.state is LogsState.IDLE
    assert vm.selected_source is LogSource.CLOUDWATCH
    assert [s.state for s in vm.sources] == [LogSourceState.UNAVAILABLE, LogSourceState.CONFIGURED]
    await vm.load()
    assert vm.state is LogsState.READY
    assert vm.lines == ("ERROR CloudWatch 0",)
    assert vm.current_stream.name == names[0]
    for name in names[1:]:
        vm.select_cloudwatch_stream(name)
        await vm.load()
        assert vm.current_stream.name == name
    assert vm.last_successful_read_at_ms == 100_000
    assert vm.last_event_at_ms == 1000
    await vm.shutdown()
    vm.dispose()


async def test_source_matrix_pending_disabled_both_and_missing_retry(monkeypatch):
    from aws_tui.domain.emr_cloudwatch_logs import CloudWatchLogEvent, CloudWatchLogStream
    from aws_tui.domain.emr_serverless import CloudWatchLogConfiguration
    from aws_tui.vm.emr_serverless.job_run_logs_vm import LogSource, LogSourceState

    vm, fake, names = _cw_vm(
        monkeypatch, uri="s3://b/logs/", config=CloudWatchLogConfiguration(False)
    )
    assert vm.selected_source is LogSource.S3
    vm.select_source(LogSource.CLOUDWATCH)
    await vm.load()
    assert not any("cloudwatch" in call[0] for call in fake.calls)
    vm.set_target("a", "r", None, metadata_known=False)
    assert all(s.state is LogSourceState.UNKNOWN for s in vm.sources)
    assert vm.state is LogsState.IDLE
    vm.set_target("a", "r", "s3://b/logs/", cloudwatch=CloudWatchLogConfiguration(True))
    assert vm.selected_source is LogSource.S3
    vm.select_source(LogSource.CLOUDWATCH)
    fake._cloudwatch_events.clear()
    await vm.load()
    assert vm.state is LogsState.NO_FILES
    assert vm.sources[1].state is LogSourceState.UNAVAILABLE
    fake.add_cloudwatch_stream(
        application_id="a",
        job_run_id="r",
        log_group_name="/aws/emr-serverless",
        stream=CloudWatchLogStream(names[0], "SPARK_DRIVER", None),
        events=(CloudWatchLogEvent("new", 1000, 1000, "ERROR appeared"),),
    )
    await vm.load()
    assert vm.lines == ("ERROR appeared",)
    assert vm.sources[1].state is LogSourceState.CONFIGURED
    await vm.shutdown()
    vm.dispose()


async def test_cloudwatch_filters_reproject_without_network_and_retain_literal_multiline(
    monkeypatch,
):
    from aws_tui.domain.emr_cloudwatch_logs import CloudWatchLogEvent
    from aws_tui.domain.emr_logs import FilterMode, LogFilter

    vm, fake, names = _cw_vm(monkeypatch)
    fake._cloudwatch_events[("/aws/emr-serverless", names[0])] = (
        CloudWatchLogEvent("x", 1000, 1000, "[arbitrary] ERROR\nwarn low\n\ninfo"),
    )
    await vm.load()
    calls = len(fake.calls)
    assert vm.lines == ("[arbitrary] ERROR",)
    vm.set_filter(LogFilter(patterns=("ERROR", "WARN"), case_insensitive=True))
    assert vm.lines == ("[arbitrary] ERROR", "warn low")
    vm.set_filter(LogFilter(patterns=(), mode=FilterMode.PASSTHROUGH))
    assert vm.lines == ("[arbitrary] ERROR", "warn low", "", "info")
    assert len(fake.calls) == calls
    await vm.shutdown()
    vm.dispose()


async def test_cloudwatch_recovery_preference_never_substitutes_missing_stream(monkeypatch):
    vm, fake, names = _cw_vm(monkeypatch)
    await vm.load(preferred_cloudwatch_stream_name=names[2])
    assert vm.current_stream.name == names[2]
    reads = sum(call[0] == "read_cloudwatch_events" for call in fake.calls)
    del fake._cloudwatch_events[("/aws/emr-serverless", names[2])]
    await vm.load(preferred_cloudwatch_stream_name=names[2])
    assert vm.state is LogsState.NO_FILES
    assert sum(call[0] == "read_cloudwatch_events" for call in fake.calls) == reads
    await vm.shutdown()
    vm.dispose()


async def test_follow_owned_dedup_freshness_stop_and_overlap(monkeypatch):
    from aws_tui.domain.emr_cloudwatch_logs import CloudWatchLogEvent
    from aws_tui.vm.emr_serverless import job_run_logs_vm as module
    from tests.helpers import wait_until

    vm, fake, names = _cw_vm(monkeypatch)
    ticks = asyncio.Queue()
    waiting = asyncio.Event()
    clock = [100_000]
    monkeypatch.setattr(module, "_now_ms", lambda: clock[0])

    async def sleep(seconds):
        assert seconds == 2.0
        waiting.set()
        await ticks.get()

    monkeypatch.setattr(module, "_sleep", sleep)
    task = asyncio.create_task(vm.follow())
    await waiting.wait()
    assert vm.following
    initial = vm.lines
    fake.append_cloudwatch_event(
        log_group_name="/aws/emr-serverless",
        stream_name=names[0],
        event=CloudWatchLogEvent("boundary", 100_000, 100_001, "ERROR end"),
    )
    fake.append_cloudwatch_event(
        log_group_name="/aws/emr-serverless",
        stream_name=names[0],
        event=CloudWatchLogEvent("distinct", 100_000, 100_002, "ERROR end"),
    )
    clock[0] = 102_000
    ticks.put_nowait(None)
    await wait_until(lambda: vm.last_successful_read_at_ms == 102_000, what="first follow poll")
    assert vm.lines == (*initial, "ERROR end", "ERROR end")
    clock[0] = 101_000  # rollback never shrinks last successful interval
    ticks.put_nowait(None)
    await wait_until(
        lambda: sum(c[0] == "read_cloudwatch_events" for c in fake.calls) == 3, what="rollback poll"
    )
    assert vm.lines == (*initial, "ERROR end", "ERROR end")
    assert vm.last_successful_read_at_ms == 102_000
    vm.stop_follow()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not vm.following
    assert not vm._operations.tasks
    await vm.shutdown()
    vm.dispose()


@pytest.mark.parametrize(
    "kind", ["auth", "denied", "throttle", "unreachable", "missing", "invalid", "unexpected"]
)
async def test_cloudwatch_errors_are_typed_and_payload_free(monkeypatch, caplog, kind):
    import traceback

    from aws_tui.domain.filesystem import (
        AuthRequiredError,
        NotFoundError,
        PermissionDeniedError,
        ProviderUnreachableError,
        ThrottledError,
        ValidationError,
    )
    from aws_tui.vm.emr_serverless.job_run_logs_vm import LogSource
    from aws_tui.vm.messages import ServiceOperationFailedMessage

    vm, fake, _ = _cw_vm(monkeypatch, uri="s3://b/logs")
    vm.select_source(LogSource.CLOUDWATCH)
    await vm.load()
    old = vm.lines
    classes = {
        "auth": AuthRequiredError,
        "denied": PermissionDeniedError,
        "throttle": ThrottledError,
        "unreachable": ProviderUnreachableError,
        "missing": NotFoundError,
        "invalid": ValidationError,
        "unexpected": RuntimeError,
    }
    messages = []
    subscription = vm._hub.messages.subscribe(messages.append)

    async def fail(**kwargs):
        try:
            raise ValueError("body-only-261-sentinel")
        except ValueError as error:
            raise classes[kind]("body-only-261-sentinel") from error

    monkeypatch.setattr(fake, "list_cloudwatch_streams", fail)
    await vm.load(use_cache=False)
    assert vm.lines == old
    assert (
        vm.failure_kind
        == {
            "auth": "auth_required",
            "denied": "access_denied",
            "throttle": "throttled",
            "unreachable": "unreachable",
            "missing": "not_found",
            "invalid": "invalid",
            "unexpected": "unexpected",
        }[kind]
    )
    assert vm.state is (LogsState.NO_FILES if kind == "missing" else LogsState.ERROR)
    diagnostics = [m for m in messages if isinstance(m, ServiceOperationFailedMessage)]
    assert len(diagnostics) == (1 if kind == "unexpected" else 0)
    for diagnostic in diagnostics:
        assert "body-only-261-sentinel" not in repr(diagnostic)
        from dataclasses import fields

        for value in (getattr(diagnostic, field.name) for field in fields(diagnostic)):
            assert "body-only-261-sentinel" not in repr(value)
            if isinstance(value, BaseException):
                assert value.__cause__ is None
                assert value.__context__ is None
                assert value.__traceback__ is None
                assert "body-only-261-sentinel" not in "".join(traceback.format_exception(value))
    assert "body-only-261-sentinel" not in vm.error_text
    assert "body-only-261-sentinel" not in caplog.text
    assert vm.source_selectable(LogSource.S3)
    subscription.dispose()
    await vm.shutdown()
    vm.dispose()


async def test_cloudwatch_event_and_display_retention_exact_limits(monkeypatch):
    from aws_tui.domain.emr_cloudwatch_logs import CloudWatchLogEvent, CloudWatchLogSnapshot
    from aws_tui.domain.emr_logs import FilterMode, LogFilter

    vm, fake, _ = _cw_vm(monkeypatch)
    snapshots = [
        CloudWatchLogSnapshot(
            tuple(
                CloudWatchLogEvent(str(i), 1000 + i, 1000 + i, "ERROR " + str(i))
                for i in range(5_001)
            ),
            1,
            100_000,
        )
    ]

    async def read(**kwargs):
        return snapshots[0]

    monkeypatch.setattr(fake, "read_cloudwatch_events", read)
    await vm.load()
    assert len(vm._raw_events) == 5_000
    assert vm.lines[0] == "ERROR 1"
    assert vm.buffer_capped
    vm.set_filter(LogFilter(patterns=(), mode=FilterMode.PASSTHROUGH))
    snapshots[0] = CloudWatchLogSnapshot(
        tuple(CloudWatchLogEvent(str(i), 1000 + i, 1000 + i, "é" * (512 * 1024)) for i in range(4)),
        4 * 1024 * 1024,
        100_000,
    )
    await vm.load()
    assert vm.bytes_read == 4 * 1024 * 1024
    assert len(vm._raw_events) == 4
    assert sum(len(s.encode()) for s in vm.lines) + max(0, len(vm.lines) - 1) <= 4 * 1024 * 1024
    assert vm.buffer_capped  # separators require dropping one display line
    snapshots[0] = CloudWatchLogSnapshot(
        (CloudWatchLogEvent("newlines", 1000, 1000, "\n" * (1024 * 1024)),), 1024 * 1024, 100_000
    )
    await vm.load()
    assert vm.matched_count == 1024 * 1024
    assert len(vm.lines) == 5_000
    assert vm.buffer_capped
    await vm.shutdown()
    vm.dispose()


@pytest.mark.parametrize("transition", ["run", "source", "stream", "config"])
async def test_cloudwatch_aba_never_publishes_late_read_or_error(monkeypatch, transition):
    from aws_tui.domain.emr_cloudwatch_logs import CloudWatchLogEvent, CloudWatchLogSnapshot
    from aws_tui.domain.emr_serverless import CloudWatchLogConfiguration
    from aws_tui.vm.emr_serverless.job_run_logs_vm import LogSource

    vm, fake, names = _cw_vm(monkeypatch, uri="s3://b/logs")
    vm.select_source(LogSource.CLOUDWATCH)
    entered, release, cleaned = asyncio.Event(), asyncio.Event(), asyncio.Event()
    original = fake.read_cloudwatch_events
    calls = 0

    async def read(**kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            entered.set()
            try:
                await release.wait()
            except asyncio.CancelledError:
                await release.wait()
            cleaned.set()
            return CloudWatchLogSnapshot(
                (CloudWatchLogEvent("old", 1000, 1000, "ERROR old-run-sentinel"),), 1, 100_000
            )
        return await original(**kwargs)

    monkeypatch.setattr(fake, "read_cloudwatch_events", read)
    task = asyncio.create_task(vm.load())
    await entered.wait()
    if transition == "run":
        vm.set_target("a", "other", None, cloudwatch=CloudWatchLogConfiguration(True))
        vm.set_target(
            "a", "r", None, cloudwatch=CloudWatchLogConfiguration(True), run_created_at_ms=1000
        )
    elif transition == "source":
        vm.select_source(LogSource.S3)
        vm.select_source(LogSource.CLOUDWATCH)
    elif transition == "stream":
        vm.select_cloudwatch_stream(names[1])
        vm.select_cloudwatch_stream(names[0])
    else:
        vm.set_target("a", "r", None, cloudwatch=CloudWatchLogConfiguration(True, "/other"))
        vm.set_target(
            "a", "r", None, cloudwatch=CloudWatchLogConfiguration(True), run_created_at_ms=1000
        )
    await vm.load()
    state, lines, stamp = vm.state, vm.lines, vm.last_successful_read_at_ms
    release.set()
    await task
    assert cleaned.is_set()
    assert vm.state == state
    assert vm.lines == lines
    assert vm.last_successful_read_at_ms == stamp
    assert not any("old-run-sentinel" in line for line in vm.lines)
    assert not vm._operations.tasks
    await vm.shutdown()
    vm.dispose()


async def test_follow_exact_duplicate_id_limit_and_overflow_preserves_data(monkeypatch):
    from aws_tui.domain.emr_cloudwatch_logs import CloudWatchLogEvent, CloudWatchLogSnapshot
    from aws_tui.vm.emr_serverless import job_run_logs_vm as module
    from tests.helpers import wait_until

    vm, fake, _ = _cw_vm(monkeypatch)
    ticks = asyncio.Queue()
    entered = asyncio.Event()
    calls = 0

    async def sleep(seconds):
        entered.set()
        await ticks.get()

    async def read(**kwargs):
        nonlocal calls
        calls += 1
        offset = (calls - 1) * 10_000
        size = 1 if calls == 3 else 10_000
        return CloudWatchLogSnapshot(
            tuple(
                CloudWatchLogEvent(str(i), 50_000, 50_000, f"ERROR {i}")
                for i in range(offset, offset + size)
            ),
            size,
            100_000,
        )

    monkeypatch.setattr(module, "_sleep", sleep)
    monkeypatch.setattr(fake, "read_cloudwatch_events", read)
    task = asyncio.create_task(vm.follow())
    await entered.wait()
    ticks.put_nowait(None)
    await wait_until(lambda: vm.lines[-1] == "ERROR 9999" and calls == 2, what="20k ids commit")
    assert vm.following
    complete = vm.lines
    ticks.put_nowait(None)
    await task
    assert not vm.following
    assert vm.failure_kind == "limit"
    assert vm.error_text == "CloudWatch follow duplicate-id limit exceeded"
    assert vm.lines == complete
    assert vm.last_successful_read_at_ms == 100_000
    await vm.shutdown()
    vm.dispose()


async def test_s3_uri_change_same_run_key_size_cannot_reuse_other_bucket(monkeypatch):
    reads = []

    async def files(**_kwargs):
        return [_STDERR_FILE]

    async def stream(**kwargs):
        reads.append(kwargs["bucket"])
        yield LogChunk((f"ERROR {kwargs['bucket']}",), 50, 1, 1, False)

    monkeypatch.setattr("aws_tui.domain.emr_logs.list_log_files", files)
    monkeypatch.setattr("aws_tui.domain.emr_logs.stream_log", stream)
    vm = _make()
    vm.set_target("app1", "run1", "s3://bucket-a/logs/")
    await vm.load()
    await vm.load()
    assert reads == ["bucket-a"]
    vm.set_target("app1", "run1", "s3://bucket-b/logs/")
    await vm.load()
    assert reads == ["bucket-a", "bucket-b"]
    assert vm.lines == ("ERROR bucket-b",)
    await vm.shutdown()
    vm.dispose()


@pytest.mark.parametrize(
    ("failure", "expected"),
    [
        (PermissionDeniedError("denied"), "access denied"),
        (AuthRequiredError("auth"), "unknown"),
        (ProviderUnreachableError("offline"), "unknown"),
        (None, "unavailable"),
    ],
)
async def test_s3_source_status_failure_then_successful_retry_and_cache(
    monkeypatch, failure, expected
):
    from aws_tui.domain.emr_serverless import CloudWatchLogConfiguration
    from aws_tui.vm.emr_serverless.job_run_logs_vm import LogSource, LogSourceState

    failed = [True]

    async def files(**_kwargs):
        if failed[0]:
            if failure is not None:
                raise failure
            return []
        return [_STDERR_FILE]

    async def stream(**_kwargs):
        yield _ONE_CHUNK

    monkeypatch.setattr("aws_tui.domain.emr_logs.list_log_files", files)
    monkeypatch.setattr("aws_tui.domain.emr_logs.stream_log", stream)
    vm = _make()
    vm.set_target("app1", "run1", _LOG_URI, cloudwatch=CloudWatchLogConfiguration(True))
    await vm.load()
    assert vm.sources[0].state.value == expected
    assert vm.source_selectable(LogSource.CLOUDWATCH)
    assert vm.sources[1].state is LogSourceState.CONFIGURED
    failed[0] = False
    await vm.load()
    assert vm.sources[0].state is LogSourceState.CONFIGURED
    await vm.load()
    assert vm.sources[0].state is LogSourceState.CONFIGURED
    assert vm.lines == _ONE_CHUNK.lines
    await vm.shutdown()
    vm.dispose()


async def test_follow_missing_appears_double_start_then_disappears_and_restart(monkeypatch):
    from aws_tui.domain.emr_cloudwatch_logs import CloudWatchLogEvent, CloudWatchLogStream
    from tests.helpers import wait_until

    ticks = asyncio.Queue()

    async def sleep(_delay):
        await ticks.get()

    monkeypatch.setattr("aws_tui.vm.emr_serverless.job_run_logs_vm._sleep", sleep)
    vm, fake, names = _cw_vm(monkeypatch, empty=True)
    follow = asyncio.create_task(vm.follow())
    await wait_until(lambda: vm.state is LogsState.NO_FILES, what="missing initial stream")
    assert vm.following
    await vm.follow()  # idempotent: no overlapping owner/read
    assert len(vm._operations.tasks) == 1
    fake.add_cloudwatch_stream(
        application_id="a",
        job_run_id="r",
        log_group_name="/aws/emr-serverless",
        stream=CloudWatchLogStream(names[0], "SPARK_DRIVER", None),
        events=(CloudWatchLogEvent("new", 1000, 1000, "ERROR appeared"),),
    )
    ticks.put_nowait(None)
    await wait_until(lambda: vm.state is LogsState.READY, what="discovery retry appeared")
    assert vm.lines == ("ERROR appeared",)
    stamp = vm.last_successful_read_at_ms
    fake._cloudwatch_events.clear()
    ticks.put_nowait(None)
    await follow
    assert not vm.following
    assert vm.state is LogsState.NO_FILES
    assert vm.lines == ("ERROR appeared",)
    assert vm.last_successful_read_at_ms == stamp
    fake.add_cloudwatch_stream(
        application_id="a",
        job_run_id="r",
        log_group_name="/aws/emr-serverless",
        stream=CloudWatchLogStream(names[0], "SPARK_DRIVER", None),
        events=(CloudWatchLogEvent("again", 1000, 1000, "ERROR restarted"),),
    )
    restarted = asyncio.create_task(vm.follow())
    await wait_until(lambda: vm.lines == ("ERROR restarted",), what="explicit follow restart")
    vm.stop_follow()
    with pytest.raises(asyncio.CancelledError):
        await restarted
    assert not vm._operations.tasks
    await vm.shutdown()
    vm.dispose()


async def test_follow_prunes_only_before_overlap_preserves_empty_freshness_and_late_events(
    monkeypatch,
):
    from aws_tui.domain.emr_cloudwatch_logs import CloudWatchLogEvent
    from tests.helpers import wait_until

    vm, fake, names = _cw_vm(monkeypatch)
    clock = [100_000]
    monkeypatch.setattr("aws_tui.vm.emr_serverless.job_run_logs_vm._now_ms", lambda: clock[0])
    ticks = asyncio.Queue()

    async def sleep(_delay):
        await ticks.get()

    monkeypatch.setattr("aws_tui.vm.emr_serverless.job_run_logs_vm._sleep", sleep)
    fake._cloudwatch_events[("/aws/emr-serverless", names[0])] = (
        CloudWatchLogEvent("start", 40_000, 40_000, "ERROR boundary"),
    )
    task = asyncio.create_task(vm.follow())
    await wait_until(lambda: vm.state is LogsState.READY, what="initial boundary event")
    # A late event inside the overlap appears; a timestamp before it is excluded.
    for event in (
        CloudWatchLogEvent("late", 40_001, 110_000, "ERROR late"),
        CloudWatchLogEvent("too-old", 39_999, 110_000, "ERROR outside overlap"),
    ):
        fake.append_cloudwatch_event(
            log_group_name="/aws/emr-serverless", stream_name=names[0], event=event
        )
    clock[0] = 110_000
    ticks.put_nowait(None)
    await wait_until(lambda: vm.last_successful_read_at_ms == 110_000, what="late poll")
    assert vm.lines == ("ERROR boundary", "ERROR late")
    assert vm.last_event_at_ms == 40_001
    # Same IDs remain suppressed at the inclusive overlap boundary.
    clock[0] = 120_000
    ticks.put_nowait(None)
    await wait_until(
        lambda: vm.last_successful_read_at_ms == 120_000, what="empty successful poll freshness"
    )
    assert vm.lines == ("ERROR boundary", "ERROR late")
    vm.stop_follow()
    with pytest.raises(asyncio.CancelledError):
        await task
    await vm.shutdown()
    vm.dispose()


async def test_stale_cloudwatch_failure_has_no_diagnostics_or_finally_writes(monkeypatch, caplog):
    from aws_tui.domain.emr_serverless import CloudWatchLogConfiguration
    from aws_tui.vm.messages import ServiceOperationFailedMessage

    vm, fake, _ = _cw_vm(monkeypatch)
    entered, release = asyncio.Event(), asyncio.Event()
    messages = []
    sub = vm._hub.messages.subscribe(messages.append)

    async def read(**_kwargs):
        entered.set()
        try:
            await release.wait()
        except asyncio.CancelledError:
            await release.wait()
        raise RuntimeError("BODY-SENTINEL-stale")

    monkeypatch.setattr(fake, "read_cloudwatch_events", read)
    task = asyncio.create_task(vm.follow())
    await entered.wait()
    vm.set_target("a", "other", None, cloudwatch=CloudWatchLogConfiguration(True))
    vm.set_target(
        "a", "r", None, cloudwatch=CloudWatchLogConfiguration(True), run_created_at_ms=1000
    )
    release.set()
    await task
    assert vm.state is LogsState.IDLE
    assert not vm.following
    assert vm.lines == ()
    assert vm.last_successful_read_at_ms is None
    assert vm.error_text is None
    assert not any(isinstance(m, ServiceOperationFailedMessage) for m in messages)
    assert "BODY-SENTINEL-stale" not in caplog.text
    sub.dispose()
    await vm.shutdown()
    vm.dispose()


async def test_s3_pending_detail_other_run_then_return_keeps_same_source_lru(monkeypatch):
    reads = []

    async def files(**_kwargs):
        return [_STDERR_FILE]

    async def stream(**kwargs):
        reads.append(kwargs["bucket"])
        yield _ONE_CHUNK

    monkeypatch.setattr("aws_tui.domain.emr_logs.list_log_files", files)
    monkeypatch.setattr("aws_tui.domain.emr_logs.stream_log", stream)
    vm = _make()
    vm.set_target("app1", "run1", _LOG_URI)
    await vm.load()
    vm.set_target("app1", "run2", None, metadata_known=False)
    vm.set_target("app1", "run1", None, metadata_known=False)
    vm.set_target("app1", "run1", _LOG_URI)
    await vm.load()
    assert reads == ["my-bucket"]
    assert vm.lines == _ONE_CHUNK.lines
    await vm.shutdown()
    vm.dispose()


async def test_stop_cloudwatch_follow_read_drains_awaited_cleanup(monkeypatch):
    vm, fake, _ = _cw_vm(monkeypatch)
    entered, cleanup, release, closed = (asyncio.Event() for _ in range(4))
    original = fake.read_cloudwatch_events

    async def read(**kwargs):
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            cleanup.set()
            await release.wait()
            closed.set()
        return await original(**kwargs)

    monkeypatch.setattr(fake, "read_cloudwatch_events", read)
    task = asyncio.create_task(vm.follow())
    await entered.wait()
    vm.stop_follow()
    assert not vm.following
    await cleanup.wait()
    assert not closed.is_set()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert closed.is_set()
    assert not vm._operations.tasks
    assert vm.state is LogsState.IDLE
    await vm.shutdown()
    vm.dispose()


async def test_cloudwatch_cleanup_failure_actual_root_logger_fields_are_body_free(
    tmp_path, monkeypatch
):
    import logging
    import traceback

    from aws_tui.composition import build_app_context
    from aws_tui.domain.emr_serverless import CloudWatchLogConfiguration
    from aws_tui.vm.messages import ServiceOperationFailedMessage

    unused, fake, _names = _cw_vm(monkeypatch)
    await unused.shutdown()
    unused.dispose()
    ctx = build_app_context(config_dir=tmp_path / "config", cache_dir=tmp_path / "cache", demo=True)
    ctx.root_vm.construct()
    vm = JobRunLogsVM(client=fake, hub=ctx.hub, dispatcher=ctx.dispatcher)
    vm.construct()
    vm.set_target(
        "a", "r", None, cloudwatch=CloudWatchLogConfiguration(True), run_created_at_ms=1000
    )
    records = []
    messages = []
    errors = []
    from aws_tui.vm.emr_serverless import job_run_logs_vm as module

    original_report = module.report_unexpected_service_error

    def capture_error(hub, *, error, **kwargs):
        errors.append(error)
        original_report(hub, error=error, **kwargs)

    monkeypatch.setattr(module, "report_unexpected_service_error", capture_error)

    class Capture(logging.Handler):
        def emit(self, record):
            records.append(record)

    handler = Capture()
    ctx.log_sink._logger.addHandler(handler)
    subscription = ctx.hub.messages.subscribe(messages.append)

    async def cleanup_failed(**_kwargs):
        try:
            raise ValueError("BODY-ONLY-CLEANUP-SENTINEL")
        finally:
            raise RuntimeError("BODY-ONLY-CLEANUP-SENTINEL")

    monkeypatch.setattr(fake, "read_cloudwatch_events", cleanup_failed)
    try:
        await vm.load()
        assert vm.failure_kind == "unexpected"
        diagnostics = [m for m in messages if isinstance(m, ServiceOperationFailedMessage)]
        assert len(diagnostics) == 1
        assert diagnostics[0].safe_error == "CloudWatch log read failed"
        assert diagnostics[0].error_type == "ProviderError"
        assert len(errors) == 1
        from aws_tui.domain.filesystem import ProviderError

        assert type(errors[0]) is ProviderError
        assert errors[0].__traceback__ is None
        assert errors[0].__context__ is None
        assert errors[0].__cause__ is None
        assert "BODY-ONLY-CLEANUP-SENTINEL" not in "".join(traceback.format_exception(errors[0]))
        assert records
        assert any(r.json_fields.get("operation") == "load_cloudwatch_logs" for r in records)
        for record in records:
            assert "BODY-ONLY-CLEANUP-SENTINEL" not in repr(record.__dict__)
            if record.exc_info:
                assert "BODY-ONLY-CLEANUP-SENTINEL" not in "".join(
                    traceback.format_exception(*record.exc_info)
                )
        assert "BODY-ONLY-CLEANUP-SENTINEL" not in ctx.log_sink.path.read_text()
        assert "BODY-ONLY-CLEANUP-SENTINEL" not in repr(diagnostics)
    finally:
        subscription.dispose()
        ctx.log_sink._logger.removeHandler(handler)
        await vm.shutdown()
        vm.dispose()
        await ctx.root_vm.content_host.shutdown()
        ctx.root_vm.dispose()
        ctx.log_sink.close()


@pytest.mark.parametrize("prefix", ["custom", "SPARK_EXECUTOR/7/attempts/99/jobs/fake"])
async def test_cloudwatch_custom_prefix_keeps_exact_attempt_worker_identity(monkeypatch, prefix):
    from aws_tui.domain.emr_serverless import CloudWatchLogConfiguration

    vm, _fake, names = _cw_vm(
        monkeypatch, config=CloudWatchLogConfiguration(True, "/group", prefix)
    )
    await vm.load()
    assert tuple(s.name for s in vm.available_streams) == tuple(names)
    for index in (2, 3, 4):
        vm.select_cloudwatch_stream(names[index])
        await vm.load()
        assert vm.current_stream.name == names[index]
        assert vm.lines == (f"ERROR CloudWatch {index}",)
    await vm.shutdown()
    vm.dispose()
