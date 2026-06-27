from __future__ import annotations

import gzip
from unittest.mock import AsyncMock

import pytest

from aws_tui.domain.emr_logs import (
    LogFileKind,
    S3LogLocation,
    build_run_prefix,
    parse_log_uri,
)


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


class _StubS3ListObjectsV2:
    """Stub S3 client for list_objects_v2 with pagination support."""

    def __init__(self, keys: list[tuple[str, int]]) -> None:
        """Initialize with a list of (key, size) tuples."""
        self._keys = keys
        self._paginate_idx = 0

    async def list_objects_v2(self, **kwargs: object) -> dict[str, object]:
        """Simulate list_objects_v2 with optional pagination."""
        # Return all contents in one response for simplicity
        # (real aioboto3 would use NextContinuationToken)
        contents = [{"Key": key, "Size": size} for key, size in self._keys]
        return {
            "Contents": contents,
            # No NextContinuationToken means pagination stops
        }

    async def __aenter__(self) -> _StubS3ListObjectsV2:
        return self

    async def __aexit__(self, *args: object) -> None:
        return None


class _StubSessionListObjectsV2:
    """Stub aioboto3.Session for list_objects_v2."""

    def __init__(self, stub: _StubS3ListObjectsV2) -> None:
        self._stub = stub

    def client(self, *_args: object, **_kwargs: object) -> _StubS3ListObjectsV2:
        return self._stub


@pytest.mark.asyncio
async def test_list_log_files_groups_driver_first_then_executors() -> None:
    """Enumerate S3 keys under the run prefix, parse each into a
    ``LogFile`` with the right ``LogFileKind``, sort driver-first."""
    from aws_tui.domain.emr_logs import list_log_files

    fake_keys = [
        ("logs/applications/a/jobs/r/SPARK_EXECUTOR_2/stdout.gz", 1024),
        ("logs/applications/a/jobs/r/SPARK_DRIVER/stderr.gz", 2048),
        ("logs/applications/a/jobs/r/SPARK_EXECUTOR_1/stderr.gz", 512),
        ("logs/applications/a/jobs/r/SPARK_DRIVER/stdout.gz", 1024),
        ("logs/applications/a/jobs/r/SPARK_EXECUTOR_1/stdout.gz", 768),
        ("logs/applications/a/jobs/r/SPARK_EXECUTOR_2/stderr.gz", 256),
    ]
    stub = _StubS3ListObjectsV2(fake_keys)
    session = _StubSessionListObjectsV2(stub)
    files = await list_log_files(  # type: ignore[arg-type]
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
        LogFileKind.EXECUTOR_STDERR,  # idx 2
    ]
    # Driver-first invariant: first two entries are driver.
    assert all(f.kind in (LogFileKind.DRIVER_STDOUT, LogFileKind.DRIVER_STDERR) for f in files[:2])


@pytest.mark.asyncio
async def test_stream_log_yields_matched_lines() -> None:
    from aws_tui.domain.emr_logs import (
        DEFAULT_LOG_FILTER,
        LogFile,
        LogFileKind,
        stream_log,
    )

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
