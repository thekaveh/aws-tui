"""EMR Serverless log streaming.

Resolves the S3 location declared by a job run's
``s3MonitoringConfiguration.logUri``, lists the per-component log
files under it, and streams the gzipped ``stdout/stderr`` bodies
line-by-line through a compiled filter. Used by ``JobRunLogsVM``;
not consumed elsewhere.

S3 boto exceptions raised inside ``list_log_files`` and
``stream_log`` are routed through the sibling
:func:`aws_tui.domain.emr_serverless.map_boto_error` so they reach
the VM as typed :class:`ProviderError` subclasses
(``AuthRequiredError`` / ``ProviderUnreachableError`` /
``PermissionDeniedError`` / etc). Without that mapping the VM's
``ProviderError`` clause would never fire and S3 credential /
network failures would surface only as a generic "unexpected error:
…" placeholder, losing the actionable AUTH_REQUIRED / UNREACHABLE
state distinction that every other EMR pane gets for free.
"""

from __future__ import annotations

import inspect
import re
import zlib
from collections.abc import AsyncIterator
from contextlib import suppress
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse

from aws_tui.domain.emr_serverless import map_boto_error
from aws_tui.domain.filesystem import ProviderError, ValidationError

if TYPE_CHECKING:
    import aioboto3
    from botocore.config import Config as BotoConfig


class FilterMode(StrEnum):
    MATCH = "match"
    PASSTHROUGH = "passthrough"


@dataclass(frozen=True, slots=True)
class LogFilter:
    patterns: tuple[str, ...]
    mode: FilterMode = FilterMode.MATCH
    case_insensitive: bool = True

    def __post_init__(self) -> None:
        # Validate every pattern eagerly so an invalid regex (a
        # stray ``(``, ``[abc``, ``*foo`` etc. in the user's filter
        # text) surfaces as a typed ValueError the modal can catch
        # at Apply time — instead of crashing ``stream_log`` mid-
        # iteration and surfacing through the VM's bottom-of-stack
        # ``except Exception`` as an opaque "unexpected error", with
        # all matches collected so far silently dropped.
        for pattern in self.patterns:
            try:
                re.compile(pattern)
            except re.error as exc:
                raise ValueError(f"invalid regex pattern {pattern!r}: {exc}") from exc

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
    HIVE_DRIVER_STDOUT = "HIVE_DRIVER_STDOUT"
    HIVE_DRIVER_STDERR = "HIVE_DRIVER_STDERR"
    TEZ_TASK_STDOUT = "TEZ_TASK_STDOUT"
    TEZ_TASK_STDERR = "TEZ_TASK_STDERR"


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


@dataclass(frozen=True, slots=True)
class LogChunk:
    lines: tuple[str, ...]
    bytes_read: int
    lines_scanned: int
    matched_count: int
    truncated: bool


_LINE_BUFFER_BATCH: int = 200
_STREAM_CHUNK_BYTES: int = 64 * 1024
_MAX_DECOMPRESSED_BYTES: int = 256 * 1024 * 1024
_MAX_LINE_BYTES: int = 1024 * 1024


def _classify_key(key: str) -> tuple[LogFileKind | None, int]:
    """Map an S3 key path to a ``LogFileKind`` + sort index.

    Driver-first sort (sort_idx ``0`` and ``1``); executor logs get
    ``2 + executor_idx * 2`` for stdout, ``+1`` for stderr.
    Keys that don't match the expected pattern return ``(None, 0)``
    so the caller skips them silently.
    """
    segments = key.split("/")
    filename = segments[-1]
    stream = (
        "stdout"
        if filename.startswith("stdout")
        else "stderr"
        if filename.startswith("stderr")
        else None
    )
    if stream is None or not filename.endswith(".gz"):
        return None, 0

    for worker, stdout_kind, stderr_kind, base_sort in (
        ("SPARK_DRIVER", LogFileKind.DRIVER_STDOUT, LogFileKind.DRIVER_STDERR, 0),
        ("HIVE_DRIVER", LogFileKind.HIVE_DRIVER_STDOUT, LogFileKind.HIVE_DRIVER_STDERR, 2),
    ):
        if worker in segments:
            return (stdout_kind if stream == "stdout" else stderr_kind), base_sort + (
                stream == "stderr"
            )

    for worker, stdout_kind, stderr_kind, base_sort in (
        ("SPARK_EXECUTOR", LogFileKind.EXECUTOR_STDOUT, LogFileKind.EXECUTOR_STDERR, 10),
        ("TEZ_TASK", LogFileKind.TEZ_TASK_STDOUT, LogFileKind.TEZ_TASK_STDERR, 1000),
    ):
        if worker not in segments:
            continue
        worker_index = segments.index(worker)
        try:
            instance = int(segments[worker_index + 1])
        except (IndexError, ValueError):
            return None, 0
        return (
            stdout_kind if stream == "stdout" else stderr_kind,
            base_sort + instance * 2 + (stream == "stderr"),
        )
    return None, 0


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
    try:
        async with session.client("s3", **kwargs) as s3:
            next_token: str | None = None
            seen_tokens: set[str] = set()
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
                    files.append((sort_idx, LogFile(key=key, kind=kind, size=obj.get("Size"))))
                next_token = resp.get("NextContinuationToken")
                if not resp.get("IsTruncated"):
                    break
                if not next_token or next_token in seen_tokens:
                    raise ProviderError(
                        "S3 returned a truncated log listing without a new continuation token"
                    )
                seen_tokens.add(next_token)
    except Exception as exc:
        mapped = map_boto_error(exc)
        if mapped is None:
            raise
        raise mapped from exc
    files.sort(key=lambda pair: pair[0])
    return [f for _, f in files]


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
    body: Any = None
    try:
        async with session.client("s3", **kwargs) as s3:
            resp = await s3.get_object(Bucket=bucket, Key=log_file.key)
            body = resp["Body"]
            bytes_read = 0
            decompressed_bytes = 0
            truncated = False
            decompressor = zlib.decompressobj(wbits=31)
            pending = bytearray()
            lines_scanned = 0
            matched: list[str] = []
            while True:
                remaining_compressed = max_bytes - bytes_read
                if remaining_compressed <= 0:
                    truncated = not decompressor.eof
                    break
                chunk = await body.read(min(_STREAM_CHUNK_BYTES, remaining_compressed))
                if not chunk:
                    break
                bytes_read += len(chunk)
                remaining_decompressed = _MAX_DECOMPRESSED_BYTES - decompressed_bytes
                if remaining_decompressed <= 0:
                    truncated = True
                    break
                try:
                    raw_output = decompressor.decompress(chunk, remaining_decompressed + 1)
                except zlib.error:
                    raise ValidationError("corrupt EMR log gzip stream") from None
                if len(raw_output) > remaining_decompressed:
                    raw_output = raw_output[:remaining_decompressed]
                    truncated = True
                decompressed_bytes += len(raw_output)
                pending.extend(raw_output)

                while True:
                    newline = pending.find(b"\n")
                    if newline < 0:
                        break
                    raw_line = bytes(pending[:newline])
                    del pending[: newline + 1]
                    if len(raw_line) > _MAX_LINE_BYTES:
                        truncated = True
                        break
                    line = raw_line.decode("utf-8", errors="replace").rstrip("\r")
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
                if truncated:
                    break
                if len(pending) > _MAX_LINE_BYTES:
                    pending.clear()
                    truncated = True
                    break
                if decompressed_bytes >= _MAX_DECOMPRESSED_BYTES:
                    truncated = True
                    break
                if bytes_read >= max_bytes and not decompressor.eof:
                    truncated = True
                    break

            if not truncated:
                try:
                    tail = decompressor.flush(_MAX_DECOMPRESSED_BYTES - decompressed_bytes)
                except zlib.error:
                    raise ValidationError("corrupt EMR log gzip stream") from None
                else:
                    pending.extend(tail)
                    decompressed_bytes += len(tail)
            if pending and not truncated:
                if len(pending) > _MAX_LINE_BYTES:
                    pending.clear()
                    truncated = True
                else:
                    line = bytes(pending).decode("utf-8", errors="replace").rstrip("\r")
                    lines_scanned += 1
                    if filter_.matches(line):
                        matched.append(line)
            if not decompressor.eof:
                truncated = True
            yield LogChunk(
                lines=tuple(matched),
                bytes_read=bytes_read,
                lines_scanned=lines_scanned,
                matched_count=len(matched),
                truncated=truncated,
            )
    except ProviderError:
        # Already typed — preserve it untouched so the VM's
        # ``ProviderError`` catch can route via map_provider_error.
        raise
    except Exception as exc:
        mapped = map_boto_error(exc)
        if mapped is None:
            raise
        raise mapped from exc
    finally:
        if body is not None:
            with suppress(Exception):
                await _close_streaming_body(body)


async def _close_streaming_body(body: object) -> None:
    close = getattr(body, "aclose", None)
    if not callable(close):
        close = getattr(body, "close", None)
    if not callable(close):
        return
    result = close()
    if inspect.isawaitable(result):
        await result


@dataclass(frozen=True, slots=True)
class EmrServerlessLogsClient:
    """Domain-layer facade for log fetching. The VM consumes
    this; the session+region+BotoConfig stay below the VM
    layer."""

    session: aioboto3.Session  # aioboto3 is already imported at module level
    region_name: str | None
    boto_config: BotoConfig | None = None  # optional; may be used by callers

    async def list_files(self, *, bucket: str, run_prefix: str) -> list[LogFile]:
        """List log files under the run's S3 prefix."""
        return await list_log_files(
            session=self.session,
            region_name=self.region_name,
            bucket=bucket,
            run_prefix=run_prefix,
            boto_config=self.boto_config,
        )

    async def stream(
        self,
        *,
        log_file: LogFile,
        bucket: str,
        max_bytes: int,
        filter_: LogFilter,
    ) -> AsyncIterator[LogChunk]:
        """Stream the gzipped body of a log file line-by-line."""
        async for chunk in stream_log(
            session=self.session,
            region_name=self.region_name,
            log_file=log_file,
            bucket=bucket,
            max_bytes=max_bytes,
            filter_=filter_,
            boto_config=self.boto_config,
        ):
            yield chunk


__all__ = [
    "DEFAULT_LOG_FILTER",
    "EmrServerlessLogsClient",
    "FilterMode",
    "LogChunk",
    "LogFile",
    "LogFileKind",
    "LogFilter",
    "S3LogLocation",
    "build_run_prefix",
    "list_log_files",
    "parse_log_uri",
    "stream_log",
]
