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
from aws_tui.vm.emr_serverless.job_run_logs_vm import JobRunLogsVM, LogsState


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
