from __future__ import annotations

import gzip
import subprocess
import sys
from unittest.mock import AsyncMock

import botocore.exceptions
import pytest

from aws_tui.domain.emr_logs import (
    LogFileKind,
    S3LogLocation,
    build_run_prefix,
    parse_log_uri,
)


def test_pathological_filter_is_bounded_in_an_isolated_process() -> None:
    code = """
from aws_tui.domain.emr_logs import LogFilter
from aws_tui.domain.filesystem import ValidationError
try:
    LogFilter(patterns=(r'(a+)+$',)).matches('a' * 10000 + '!')
except ValidationError as error:
    assert str(error) == 'log filter time limit exceeded; simplify patterns or use Show all'
else:
    raise AssertionError('filter unexpectedly completed')
"""
    subprocess.run([sys.executable, "-c", code], timeout=3, check=True)


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
    # WARN is intentionally NOT in the default pattern set — Spark stderr
    # is dominated by WARN-level noise from third-party libs (Hadoop /
    # Jetty / Netty) that drowns the actually-actionable signal. User
    # feedback (post-PR-#98): "Including WARNs in the deep filter for
    # the stderr may have been a mistake as it results in way too big
    # logs". WARN remains TYPE-able in the filter modal for anyone who
    # wants it back.
    assert not DEFAULT_LOG_FILTER.matches("WARN Spark something noisy")
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
        self.closed = False

    async def read(self, n: int) -> bytes:
        out, self._payload = self._payload[:n], self._payload[n:]
        return out

    def close(self) -> None:
        self.closed = True


class _RaisingBody:
    def __init__(self, exc: BaseException) -> None:
        self._exc = exc

    async def read(self, _n: int) -> bytes:
        raise self._exc


class _StubS3:
    def __init__(self, body: bytes) -> None:
        self.body = _StubBody(body)
        self.get_object = AsyncMock(return_value={"Body": self.body, "ContentLength": len(body)})
        self.exited = False

    async def __aenter__(self) -> _StubS3:
        return self

    async def __aexit__(self, *args: object) -> None:
        self.exited = True
        return None


class _StubSession:
    def __init__(self, stub: _StubS3) -> None:
        self._stub = stub

    def client(self, *_args: object, **_kwargs: object) -> _StubS3:
        return self._stub


class _BodyRaisingS3:
    def __init__(self, exc: BaseException) -> None:
        self.get_object = AsyncMock(return_value={"Body": _RaisingBody(exc)})

    async def __aenter__(self) -> _BodyRaisingS3:
        return self

    async def __aexit__(self, *args: object) -> None:
        return None


class _BodyRaisingSession:
    def __init__(self, exc: BaseException) -> None:
        self._stub = _BodyRaisingS3(exc)

    def client(self, *_args: object, **_kwargs: object) -> _BodyRaisingS3:
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
        ("logs/applications/a/jobs/r/SPARK_EXECUTOR/2/stdout.gz", 1024),
        ("logs/applications/a/jobs/r/SPARK_DRIVER/stderr.gz", 2048),
        ("logs/applications/a/jobs/r/SPARK_EXECUTOR/1/stderr.gz", 512),
        ("logs/applications/a/jobs/r/SPARK_DRIVER/stdout.gz", 1024),
        ("logs/applications/a/jobs/r/SPARK_EXECUTOR/1/stdout.gz", 768),
        ("logs/applications/a/jobs/r/SPARK_EXECUTOR/2/stderr.gz", 256),
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
async def test_list_log_files_supports_retries_rotation_and_hive_workers() -> None:
    from aws_tui.domain.emr_logs import list_log_files

    fake_keys = [
        ("logs/applications/a/jobs/r/attempts/2/SPARK_DRIVER/archived/stderr_1.gz", 10),
        ("logs/applications/a/jobs/r/attempts/2/SPARK_EXECUTOR/7/stdout.gz", 20),
        ("logs/applications/a/jobs/r/attempts/2/HIVE_DRIVER/stderr.gz", 30),
        ("logs/applications/a/jobs/r/attempts/2/TEZ_TASK/4/stdout.gz", 40),
    ]
    files = await list_log_files(  # type: ignore[arg-type]
        session=_StubSessionListObjectsV2(_StubS3ListObjectsV2(fake_keys)),
        region_name="us-east-1",
        bucket="b",
        run_prefix="logs/applications/a/jobs/r",
    )

    assert {file.kind for file in files} == {
        LogFileKind.DRIVER_STDERR,
        LogFileKind.EXECUTOR_STDOUT,
        LogFileKind.HIVE_DRIVER_STDERR,
        LogFileKind.TEZ_TASK_STDOUT,
    }


@pytest.mark.asyncio
async def test_list_log_files_classifies_only_the_run_relative_suffix() -> None:
    from aws_tui.domain.emr_logs import list_log_files

    run_prefix = "SPARK_DRIVER/SPARK_EXECUTOR/team/applications/a/jobs/r"
    fake_keys = [
        (f"{run_prefix}/SPARK_EXECUTOR/7/stdout.gz", 10),
        (f"{run_prefix}/TEZ_TASK/4/stderr.gz", 20),
    ]

    files = await list_log_files(  # type: ignore[arg-type]
        session=_StubSessionListObjectsV2(_StubS3ListObjectsV2(fake_keys)),
        region_name="us-east-1",
        bucket="b",
        run_prefix=run_prefix,
    )

    assert [(file.kind, file.key) for file in files] == [
        (LogFileKind.EXECUTOR_STDOUT, fake_keys[0][0]),
        (LogFileKind.TEZ_TASK_STDERR, fake_keys[1][0]),
    ]


@pytest.mark.parametrize("trailing_slash", [False, True])
@pytest.mark.parametrize("honor_prefix", [False, True])
async def test_list_log_files_excludes_sibling_run_prefixes(
    trailing_slash: bool, honor_prefix: bool
) -> None:
    from aws_tui.domain.emr_logs import list_log_files

    prefix = "logs/applications/a/jobs/r"
    selected = f"{prefix}/SPARK_DRIVER/stderr.gz"
    sibling = f"{prefix}-archive/SPARK_DRIVER/stderr.gz"
    stub = _StubS3ListObjectsV2([(selected, 10), (sibling, 20)])
    calls = []
    original_list = stub.list_objects_v2

    async def list_by_prefix(**kwargs: object) -> dict[str, object]:
        calls.append(kwargs)
        response = await original_list(**kwargs)
        if honor_prefix:
            response["Contents"] = [
                obj for obj in response["Contents"] if obj["Key"].startswith(str(kwargs["Prefix"]))
            ]
        return response

    stub.list_objects_v2 = list_by_prefix  # type: ignore[method-assign]
    files = await list_log_files(  # type: ignore[arg-type]
        session=_StubSessionListObjectsV2(stub),
        region_name="us-east-1",
        bucket="b",
        run_prefix=prefix + ("/" if trailing_slash else ""),
    )

    assert [file.key for file in files] == [selected]
    assert calls == [{"Bucket": "b", "Prefix": prefix + "/"}]


@pytest.mark.asyncio
async def test_list_log_files_rejects_pagination_beyond_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from aws_tui.domain import emr_logs
    from aws_tui.domain.filesystem import ProviderError

    class _PagedS3(_StubS3ListObjectsV2):
        def __init__(self) -> None:
            super().__init__([])
            self.calls = 0

        async def list_objects_v2(self, **kwargs: object) -> dict[str, object]:
            self.calls += 1
            if self.calls > 1:
                return {"Contents": [], "IsTruncated": False}
            return {
                "Contents": [],
                "IsTruncated": True,
                "NextContinuationToken": f"page-{self.calls + 1}",
            }

    stub = _PagedS3()
    monkeypatch.setattr(emr_logs, "_MAX_LOG_DISCOVERY_PAGES", 1, raising=False)

    with pytest.raises(ProviderError, match="pagination safety limit"):
        await emr_logs.list_log_files(  # type: ignore[arg-type]
            session=_StubSessionListObjectsV2(stub),
            region_name="us-east-1",
            bucket="b",
            run_prefix="logs/applications/a/jobs/r",
        )

    assert stub.calls == 1


@pytest.mark.asyncio
async def test_list_log_files_rejects_more_files_than_ui_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from aws_tui.domain import emr_logs
    from aws_tui.domain.filesystem import ProviderError

    monkeypatch.setattr(emr_logs, "_MAX_LOG_DISCOVERY_FILES", 1, raising=False)
    stub = _StubS3ListObjectsV2(
        [
            ("logs/applications/a/jobs/r/SPARK_DRIVER/stdout.gz", 10),
            ("logs/applications/a/jobs/r/SPARK_DRIVER/stderr.gz", 20),
        ]
    )

    with pytest.raises(ProviderError, match="file safety limit"):
        await emr_logs.list_log_files(  # type: ignore[arg-type]
            session=_StubSessionListObjectsV2(stub),
            region_name="us-east-1",
            bucket="b",
            run_prefix="logs/applications/a/jobs/r",
        )


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
        "Caused by: java.lang.NullPointerException",
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
    assert all_lines == [
        "ERROR something broke",
        "Caused by: java.lang.NullPointerException",
    ]
    # Total scanned matches the input line count.
    assert chunks[-1].lines_scanned == 5
    # Not truncated for this small input.
    assert chunks[-1].truncated is False


@pytest.mark.asyncio
async def test_stream_log_bounds_decompressed_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import aws_tui.domain.emr_logs as emr_logs

    monkeypatch.setattr(emr_logs, "_MAX_DECOMPRESSED_BYTES", 1024)
    payload = gzip.compress(("ERROR highly compressible line\n" * 10_000).encode())
    stub = _StubS3(payload)
    log_file = emr_logs.LogFile(
        key="logs/applications/a/jobs/r/SPARK_DRIVER/stderr.gz",
        kind=emr_logs.LogFileKind.DRIVER_STDERR,
    )

    chunks = [
        chunk
        async for chunk in emr_logs.stream_log(
            session=_StubSession(stub),  # type: ignore[arg-type]
            region_name="us-east-1",
            log_file=log_file,
            bucket="b",
            max_bytes=1024 * 1024,
            filter_=emr_logs.DEFAULT_LOG_FILTER,
        )
    ]

    assert chunks[-1].truncated is True
    assert sum(len(line.encode()) for chunk in chunks for line in chunk.lines) <= 1024


@pytest.mark.asyncio
async def test_stream_log_exact_compressed_limit_is_not_truncated() -> None:
    from aws_tui.domain.emr_logs import DEFAULT_LOG_FILTER, LogFile, stream_log

    payload = gzip.compress(b"ERROR complete\n")
    chunks = [
        chunk
        async for chunk in stream_log(
            session=_StubSession(_StubS3(payload)),  # type: ignore[arg-type]
            region_name="us-east-1",
            log_file=LogFile(key="complete.gz", kind=LogFileKind.DRIVER_STDERR),
            bucket="b",
            max_bytes=len(payload),
            filter_=DEFAULT_LOG_FILTER,
        )
    ]

    assert chunks[-1].lines == ("ERROR complete",)
    assert chunks[-1].truncated is False


@pytest.mark.parametrize("read_size", [1, 7, 65536])
async def test_stream_log_reads_concatenated_gzip_members(read_size: int) -> None:
    from aws_tui.domain.emr_logs import DEFAULT_LOG_FILTER, LogFile, stream_log

    payload = gzip.compress(b"ERROR first\nERR") + gzip.compress(b"OR second\n")
    stub = _StubS3(payload)
    read = stub.body.read

    async def bounded_read(n: int) -> bytes:
        return await read(min(n, read_size))

    stub.body.read = bounded_read  # type: ignore[method-assign]
    chunks = [
        chunk
        async for chunk in stream_log(
            session=_StubSession(stub),  # type: ignore[arg-type]
            region_name="us-east-1",
            log_file=LogFile(key="members.gz", kind=LogFileKind.DRIVER_STDERR),
            bucket="b",
            max_bytes=len(payload),
            filter_=DEFAULT_LOG_FILTER,
        )
    ]

    assert [line for chunk in chunks for line in chunk.lines] == ["ERROR first", "ERROR second"]
    assert chunks[-1].bytes_read == len(payload)
    assert chunks[-1].lines_scanned == 2
    assert chunks[-1].truncated is False
    assert stub.body.closed


@pytest.mark.parametrize("declared_length", ["actual", None, "invalid"])
async def test_stream_log_flags_unread_member_at_exact_compressed_budget(declared_length) -> None:
    from aws_tui.domain.emr_logs import DEFAULT_LOG_FILTER, LogFile, stream_log

    first = gzip.compress(b"ERROR first\n")
    payload = first + gzip.compress(b"ERROR second\n")
    stub = _StubS3(payload)
    if declared_length is None:
        stub.get_object.return_value.pop("ContentLength")
    else:
        stub.get_object.return_value["ContentLength"] = (
            len(payload) if declared_length == "actual" else declared_length
        )
    chunks = [
        chunk
        async for chunk in stream_log(
            session=_StubSession(stub),  # type: ignore[arg-type]
            region_name="us-east-1",
            log_file=LogFile(key="members.gz", kind=LogFileKind.DRIVER_STDERR),
            bucket="b",
            max_bytes=len(first),
            filter_=DEFAULT_LOG_FILTER,
        )
    ]

    assert chunks[-1].lines == ("ERROR first",)
    assert chunks[-1].bytes_read == len(first)
    assert chunks[-1].truncated is True
    assert stub.body.closed


@pytest.mark.asyncio
async def test_stream_log_maps_corrupt_gzip_and_closes_resources() -> None:
    from aws_tui.domain.emr_logs import DEFAULT_LOG_FILTER, LogFile, stream_log
    from aws_tui.domain.filesystem import ValidationError

    stub = _StubS3(b"not-a-gzip-stream")

    with pytest.raises(ValidationError, match="corrupt EMR log gzip stream"):
        async for _chunk in stream_log(
            session=_StubSession(stub),  # type: ignore[arg-type]
            region_name="us-east-1",
            log_file=LogFile(key="corrupt.gz", kind=LogFileKind.DRIVER_STDERR),
            bucket="b",
            max_bytes=1024,
            filter_=DEFAULT_LOG_FILTER,
        ):
            pass

    assert stub.body.closed
    assert stub.exited


@pytest.mark.asyncio
async def test_stream_log_bounds_unterminated_line(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import aws_tui.domain.emr_logs as emr_logs

    monkeypatch.setattr(emr_logs, "_MAX_LINE_BYTES", 128)
    payload = gzip.compress(b"ERROR " + b"x" * 10_000)
    stub = _StubS3(payload)
    log_file = emr_logs.LogFile(
        key="logs/applications/a/jobs/r/SPARK_DRIVER/stderr.gz",
        kind=emr_logs.LogFileKind.DRIVER_STDERR,
    )

    chunks = [
        chunk
        async for chunk in emr_logs.stream_log(
            session=_StubSession(stub),  # type: ignore[arg-type]
            region_name="us-east-1",
            log_file=log_file,
            bucket="b",
            max_bytes=1024 * 1024,
            filter_=emr_logs.DEFAULT_LOG_FILTER,
        )
    ]

    assert chunks[-1].truncated is True
    assert all(len(line.encode()) <= 128 for chunk in chunks for line in chunk.lines)


# ── boto-error mapping (regression-guard for the silent-swallow audit) ────


class _RaisingS3:
    """Stub S3 client whose ``list_objects_v2`` / ``get_object`` raise
    a configurable exception. Used to verify ``list_log_files`` and
    ``stream_log`` route boto exceptions through
    :func:`aws_tui.domain.emr_serverless.map_boto_error` so the VM
    sees a typed :class:`ProviderError` and can map to AUTH_REQUIRED
    / UNREACHABLE / FORBIDDEN — instead of the generic catch-all
    "unexpected error: …" placeholder that bypassed the proper
    in-pane error state.
    """

    def __init__(self, exc: BaseException) -> None:
        self._exc = exc

    async def list_objects_v2(self, **_kwargs: object) -> dict[str, object]:
        raise self._exc

    async def get_object(self, **_kwargs: object) -> dict[str, object]:
        raise self._exc

    async def __aenter__(self) -> _RaisingS3:
        return self

    async def __aexit__(self, *args: object) -> None:
        return None


class _RaisingSession:
    def __init__(self, exc: BaseException) -> None:
        self._stub = _RaisingS3(exc)

    def client(self, *_args: object, **_kwargs: object) -> _RaisingS3:
        return self._stub


@pytest.mark.asyncio
async def test_list_log_files_wraps_no_credentials_error_as_auth_required() -> None:
    """The VM's ``ProviderError`` clause routes via
    ``map_provider_error`` to ``PaneState.AUTH_REQUIRED``; pre-fix
    a raw ``NoCredentialsError`` slipped past as a bare ``Exception``
    and the user got "unexpected error: Unable to locate credentials"
    instead of the actionable "authentication required …" placeholder.
    """
    from aws_tui.domain.emr_logs import list_log_files
    from aws_tui.domain.filesystem import AuthRequiredError

    session = _RaisingSession(botocore.exceptions.NoCredentialsError())
    with pytest.raises(AuthRequiredError):
        await list_log_files(  # type: ignore[arg-type]
            session=session,
            region_name="us-east-1",
            bucket="b",
            run_prefix="logs/applications/a/jobs/r",
        )


@pytest.mark.asyncio
async def test_list_log_files_wraps_endpoint_unreachable() -> None:
    from aws_tui.domain.emr_logs import list_log_files
    from aws_tui.domain.filesystem import ProviderUnreachableError

    session = _RaisingSession(
        botocore.exceptions.EndpointConnectionError(endpoint_url="https://s3.example/")
    )
    with pytest.raises(ProviderUnreachableError):
        await list_log_files(  # type: ignore[arg-type]
            session=session,
            region_name="us-east-1",
            bucket="b",
            run_prefix="logs/applications/a/jobs/r",
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "exc",
    [
        botocore.exceptions.ConnectionClosedError(endpoint_url="https://s3.example/"),
        botocore.exceptions.ProxyConnectionError(proxy_url="https://proxy.example"),
        botocore.exceptions.SSLError(endpoint_url="https://s3.example/", error="tls"),
    ],
)
async def test_list_log_files_wraps_transport_failures(exc: BaseException) -> None:
    from aws_tui.domain.emr_logs import list_log_files
    from aws_tui.domain.filesystem import ProviderUnreachableError

    session = _RaisingSession(exc)
    with pytest.raises(ProviderUnreachableError):
        await list_log_files(  # type: ignore[arg-type]
            session=session,
            region_name="us-east-1",
            bucket="b",
            run_prefix="logs/applications/a/jobs/r",
        )


@pytest.mark.asyncio
async def test_list_log_files_wraps_access_denied_client_error() -> None:
    from aws_tui.domain.emr_logs import list_log_files
    from aws_tui.domain.filesystem import PermissionDeniedError

    err = botocore.exceptions.ClientError(
        {"Error": {"Code": "AccessDeniedException", "Message": "no list rights"}},
        "ListObjectsV2",
    )
    session = _RaisingSession(err)
    with pytest.raises(PermissionDeniedError):
        await list_log_files(  # type: ignore[arg-type]
            session=session,
            region_name="us-east-1",
            bucket="b",
            run_prefix="logs/applications/a/jobs/r",
        )


@pytest.mark.asyncio
async def test_list_log_files_wraps_expired_token_client_error() -> None:
    from aws_tui.domain.emr_logs import list_log_files
    from aws_tui.domain.filesystem import AuthRequiredError

    err = botocore.exceptions.ClientError(
        {"Error": {"Code": "ExpiredToken", "Message": "session expired"}},
        "ListObjectsV2",
    )
    with pytest.raises(AuthRequiredError):
        await list_log_files(  # type: ignore[arg-type]
            session=_RaisingSession(err),
            region_name="us-east-1",
            bucket="b",
            run_prefix="logs/applications/a/jobs/r",
        )


@pytest.mark.asyncio
async def test_list_log_files_propagates_unknown_non_boto_exception() -> None:
    """A non-boto, non-ProviderError raise (e.g. programmer error)
    should re-raise UNCHANGED — the VM's defensive ``Exception``
    clause then catches it as the catch-all. Pinning this prevents
    the mapper from accidentally wrapping every exception."""
    from aws_tui.domain.emr_logs import list_log_files

    session = _RaisingSession(RuntimeError("bug in caller"))
    with pytest.raises(RuntimeError, match="bug in caller"):
        await list_log_files(  # type: ignore[arg-type]
            session=session,
            region_name="us-east-1",
            bucket="b",
            run_prefix="logs/applications/a/jobs/r",
        )


@pytest.mark.asyncio
async def test_stream_log_wraps_no_credentials_error_as_auth_required() -> None:
    from aws_tui.domain.emr_logs import (
        DEFAULT_LOG_FILTER,
        LogFile,
        LogFileKind,
        stream_log,
    )
    from aws_tui.domain.filesystem import AuthRequiredError

    session = _RaisingSession(botocore.exceptions.NoCredentialsError())
    log_file = LogFile(
        key="logs/applications/a/jobs/r/SPARK_DRIVER/stderr.gz",
        kind=LogFileKind.DRIVER_STDERR,
    )
    with pytest.raises(AuthRequiredError):
        async for _chunk in stream_log(
            session=session,  # type: ignore[arg-type]
            region_name="us-east-1",
            log_file=log_file,
            bucket="b",
            max_bytes=1024,
            filter_=DEFAULT_LOG_FILTER,
        ):
            pass


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "exc",
    [
        botocore.exceptions.ConnectionClosedError(endpoint_url="https://s3.example/"),
        botocore.exceptions.ProxyConnectionError(proxy_url="https://proxy.example"),
        botocore.exceptions.SSLError(endpoint_url="https://s3.example/", error="tls"),
    ],
)
async def test_stream_log_wraps_transport_failures(exc: BaseException) -> None:
    from aws_tui.domain.emr_logs import (
        DEFAULT_LOG_FILTER,
        LogFile,
        LogFileKind,
        stream_log,
    )
    from aws_tui.domain.filesystem import ProviderUnreachableError

    session = _RaisingSession(exc)
    log_file = LogFile(
        key="logs/applications/a/jobs/r/SPARK_DRIVER/stderr.gz",
        kind=LogFileKind.DRIVER_STDERR,
    )
    with pytest.raises(ProviderUnreachableError):
        async for _chunk in stream_log(
            session=session,  # type: ignore[arg-type]
            region_name="us-east-1",
            log_file=log_file,
            bucket="b",
            max_bytes=1024,
            filter_=DEFAULT_LOG_FILTER,
        ):
            pass


@pytest.mark.asyncio
async def test_stream_log_wraps_transport_failures_from_body_read() -> None:
    from aws_tui.domain.emr_logs import (
        DEFAULT_LOG_FILTER,
        LogFile,
        LogFileKind,
        stream_log,
    )
    from aws_tui.domain.filesystem import ProviderUnreachableError

    exc = botocore.exceptions.ConnectionClosedError(endpoint_url="https://s3.example/")
    session = _BodyRaisingSession(exc)
    log_file = LogFile(
        key="logs/applications/a/jobs/r/SPARK_DRIVER/stderr.gz",
        kind=LogFileKind.DRIVER_STDERR,
    )
    with pytest.raises(ProviderUnreachableError):
        async for _chunk in stream_log(
            session=session,  # type: ignore[arg-type]
            region_name="us-east-1",
            log_file=log_file,
            bucket="b",
            max_bytes=1024,
            filter_=DEFAULT_LOG_FILTER,
        ):
            pass


@pytest.mark.asyncio
async def test_stream_log_wraps_access_denied_client_error() -> None:
    from aws_tui.domain.emr_logs import (
        DEFAULT_LOG_FILTER,
        LogFile,
        LogFileKind,
        stream_log,
    )
    from aws_tui.domain.filesystem import PermissionDeniedError

    err = botocore.exceptions.ClientError(
        {"Error": {"Code": "AccessDeniedException", "Message": "no get rights"}},
        "GetObject",
    )
    session = _RaisingSession(err)
    log_file = LogFile(
        key="logs/applications/a/jobs/r/SPARK_DRIVER/stderr.gz",
        kind=LogFileKind.DRIVER_STDERR,
    )
    with pytest.raises(PermissionDeniedError):
        async for _chunk in stream_log(
            session=session,  # type: ignore[arg-type]
            region_name="us-east-1",
            log_file=log_file,
            bucket="b",
            max_bytes=1024,
            filter_=DEFAULT_LOG_FILTER,
        ):
            pass


@pytest.mark.asyncio
async def test_stream_log_wraps_access_denied_from_body_read() -> None:
    from aws_tui.domain.emr_logs import (
        DEFAULT_LOG_FILTER,
        LogFile,
        LogFileKind,
        stream_log,
    )
    from aws_tui.domain.filesystem import PermissionDeniedError

    err = botocore.exceptions.ClientError(
        {"Error": {"Code": "AccessDeniedException", "Message": "body read denied"}},
        "ReadObjectBody",
    )
    session = _BodyRaisingSession(err)
    log_file = LogFile(
        key="logs/applications/a/jobs/r/SPARK_DRIVER/stderr.gz",
        kind=LogFileKind.DRIVER_STDERR,
    )
    with pytest.raises(PermissionDeniedError):
        async for _chunk in stream_log(
            session=session,  # type: ignore[arg-type]
            region_name="us-east-1",
            log_file=log_file,
            bucket="b",
            max_bytes=1024,
            filter_=DEFAULT_LOG_FILTER,
        ):
            pass


async def test_stream_log_batches_do_not_duplicate_or_inflate_matches() -> None:
    """Crossing the batch boundary must reset the buffer, or counts fabricate.

    `stream_log` yields a chunk every `_LINE_BUFFER_BATCH` (200) matches and
    then clears the buffer. With the clear removed, every subsequent chunk
    re-carried all earlier matches: a log with exactly 250 matching lines
    reported 11,725 matches across 52 chunks. No existing test crossed the
    boundary, so the reset was unpinned. This one uses 250 matches and asserts
    the batching arithmetic exactly.
    """
    from aws_tui.domain.emr_logs import (
        DEFAULT_LOG_FILTER,
        LogFile,
        LogFileKind,
        stream_log,
    )

    log_lines = [f"ERROR failure {index}" for index in range(250)]
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

    all_lines = [line for chunk in chunks for line in chunk.lines]
    assert len(all_lines) == 250, f"emitted {len(all_lines)} lines for 250 matches"
    assert all_lines == log_lines, "lines were duplicated or reordered"
    assert sum(chunk.matched_count for chunk in chunks) == 250, (
        f"chunks reported {sum(c.matched_count for c in chunks)} matches for 250"
    )
    # 250 matches at a batch size of 200 is exactly one full chunk + one tail.
    assert [chunk.matched_count for chunk in chunks] == [200, 50]


async def test_a_torn_gzip_body_is_flagged_truncated() -> None:
    """A gzip stream that never reaches EOF must carry the truncation flag.

    S3 can serve a log object whose gzip body was torn mid-upload. The lines
    that decompressed cleanly are still shown, but without the flag the pane
    presents a partial log as complete — the user reads 12 of 50 lines with no
    banner saying anything is missing. Neutralising `truncated = True` on the
    not-EOF path survived the whole suite: no test fed the stream a torn body.
    """
    from aws_tui.domain.emr_logs import (
        DEFAULT_LOG_FILTER,
        LogFile,
        LogFileKind,
        stream_log,
    )

    log_lines = [f"ERROR line {index}" for index in range(50)]
    full = gzip.compress("\n".join(log_lines).encode())
    torn = full[: len(full) // 2]  # cut mid-stream: decompressor never sees EOF
    stub = _StubS3(torn)
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

    assert chunks, "a torn body should still deliver what decompressed"
    delivered = [line for chunk in chunks for line in chunk.lines]
    assert 0 < len(delivered) < 50, "precondition: the tear lost some lines"
    assert chunks[-1].truncated is True, (
        f"{len(delivered)} of 50 lines shown with no truncation banner"
    )


def test_log_filter_rejects_an_invalid_regex_at_construction() -> None:
    """`LogFilter.__post_init__` validates eagerly so the Apply modal can catch it.

    Without the eager compile, `LogFilter(patterns=("ERROR", "[unclosed"))`
    constructs fine and `stream_log` then crashes mid-iteration with
    `re.error: unterminated character set` — surfacing through the VM's
    bottom-of-stack handler as an opaque "unexpected error" with every match
    collected so far dropped. Exactly the failure the source comment says the
    validation exists to prevent; both its mutants survived.
    """
    from aws_tui.domain.emr_logs import FilterMode, LogFilter

    with pytest.raises(ValueError, match="invalid regex"):
        LogFilter(mode=FilterMode.MATCH, patterns=("ERROR", "[unclosed"))

    # Positive control: valid patterns construct and match.
    valid = LogFilter(mode=FilterMode.MATCH, patterns=("ERROR",))
    assert valid.matches("ERROR boom")
    assert not valid.matches("INFO fine")


async def test_client_stream_closes_the_inner_generator_when_abandoned(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Abandoning the stream must release the S3 connection it holds.

    ``stream_log`` keeps ``async with session.client("s3", ...)`` open across
    its yields, holding a live connection and an open ``StreamingBody``. The
    log VM deliberately ``return``s out of its ``async for`` whenever the user
    selects a different job run -- ``set_target`` runs in a different worker
    group and does not cancel the log worker -- so without an explicit close
    each switch stranded one connection until the async-generator GC hook
    happened to run.
    """
    from contextlib import aclosing

    from aws_tui.domain import emr_logs

    closed = False

    async def fake_stream_log(**_kwargs: object):
        nonlocal closed
        try:
            yield emr_logs.LogChunk(
                lines=("first",), bytes_read=5, lines_scanned=1, matched_count=1, truncated=False
            )
            yield emr_logs.LogChunk(
                lines=("second",), bytes_read=11, lines_scanned=2, matched_count=2, truncated=False
            )
        finally:
            closed = True

    monkeypatch.setattr(emr_logs, "stream_log", fake_stream_log)
    client = emr_logs.EmrServerlessLogsClient(
        session=AsyncMock(), region_name="us-east-1", boto_config=None
    )
    log_file = emr_logs.LogFile(key="logs/a.gz", kind=LogFileKind.DRIVER_STDOUT, size=10)

    async with aclosing(
        client.stream(
            log_file=log_file,
            bucket="b",
            max_bytes=1024,
            filter_=emr_logs.DEFAULT_LOG_FILTER,
        )
    ) as source:
        async for _chunk in source:
            break  # the consumer walks away mid-stream

    assert closed, "the inner stream_log generator was never closed"


@pytest.mark.parametrize(
    ("pattern", "line", "case_insensitive"),
    [
        (r"(?i:error)", "ERROR", False),
        (r"(ab)\1", "abab", False),
        (r"(?<=start )error(?= end)", "start error end", False),
        (r"^ERROR$", "ERROR\n", False),
        (r"(?>a+)b", "aaab", False),
        (r"a++b", "aaab", False),
        (r"\bERROR\b", "notERROR", True),
    ],
)
def test_bounded_filter_preserves_supported_regex_examples(pattern, line, case_insensitive):
    import re

    from aws_tui.domain.emr_logs import LogFilter

    expected = bool(re.search(pattern, line, re.IGNORECASE if case_insensitive else 0))
    assert LogFilter((pattern,), case_insensitive=case_insensitive).matches(line) is expected


def test_filter_shares_one_deadline_between_patterns(monkeypatch):
    from aws_tui.domain import emr_logs

    calls = []

    class NoMatch:
        def search(self, _line, *, timeout):
            calls.append(timeout)
            return None

    filter_ = emr_logs.LogFilter(("first", "second"))
    object.__setattr__(filter_, "_compiled", (NoMatch(), NoMatch()))
    ticks = iter((1.0, 1.01, 1.03))
    monkeypatch.setattr(emr_logs, "monotonic", lambda: next(ticks))
    assert filter_.matches("line", deadline=1.04) is False
    assert calls == pytest.approx((0.03, 0.01))


@pytest.mark.parametrize("patterns", [("x",) * 65, ("x" * 4097,)])
def test_filter_rejects_excessive_pattern_inputs(patterns):
    from aws_tui.domain.emr_logs import LogFilter

    with pytest.raises(ValueError, match="at most 64 patterns"):
        LogFilter(patterns)


@pytest.mark.parametrize("newline", [True, False])
async def test_filter_timeout_preserves_pending_matches_and_closes_stream(monkeypatch, newline):
    from aws_tui.domain.emr_logs import (
        DEFAULT_LOG_FILTER,
        LogFile,
        LogFilter,
        LogFilterTimeoutError,
        stream_log,
    )

    original = LogFilter.matches

    def matches(self, line, **kwargs):
        if line == "slow":
            raise LogFilterTimeoutError
        return original(self, line, **kwargs)

    monkeypatch.setattr(LogFilter, "matches", matches)
    stub = _StubS3(gzip.compress(b"ERROR retained\nslow" + (b"\n" if newline else b"")))
    chunks = []

    async def collect() -> None:
        async for chunk in stream_log(
            session=_StubSession(stub),
            region_name="us-east-1",
            log_file=LogFile("log.gz", LogFileKind.DRIVER_STDERR),
            bucket="b",
            max_bytes=1024,
            filter_=DEFAULT_LOG_FILTER,
        ):
            chunks.append(chunk)

    with pytest.raises(LogFilterTimeoutError):
        await collect()
    assert chunks[-1].lines == ("ERROR retained",)
    assert chunks[-1].truncated
    assert stub.body.closed


def test_bounded_filter_documents_scoped_ascii_case_folding() -> None:
    from aws_tui.domain.emr_logs import LogFilter

    # VERSION0 uses the timeout engine's Unicode case tables for IGNORECASE.
    assert LogFilter((r"(?a:i)",), case_insensitive=True).matches("İ")
    assert not LogFilter((r"(?a:i)",), case_insensitive=False).matches("İ")


@pytest.mark.parametrize("pattern", [r"a{e<=x}", r"a{4294967295}"])
def test_timeout_engine_compile_failure_is_inline_validation_error(pattern: str) -> None:
    from aws_tui.domain.emr_logs import LogFilter

    with pytest.raises(ValueError, match="invalid regex pattern"):
        LogFilter((r"a{e<=x}",))
