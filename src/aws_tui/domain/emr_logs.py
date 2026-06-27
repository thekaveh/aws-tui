"""EMR Serverless log streaming.

Resolves the S3 location declared by a job run's
``s3MonitoringConfiguration.logUri``, lists the per-component log
files under it, and streams the gzipped ``stdout/stderr`` bodies
line-by-line through a compiled filter. Used by ``JobRunLogsVM``;
not consumed elsewhere.
"""

from __future__ import annotations

import gzip
import re
from collections.abc import AsyncIterator
from dataclasses import dataclass
from enum import StrEnum
from io import BytesIO
from typing import TYPE_CHECKING
from urllib.parse import urlparse

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


@dataclass(frozen=True, slots=True)
class LogChunk:
    lines: tuple[str, ...]
    bytes_read: int
    lines_scanned: int
    matched_count: int
    truncated: bool


_LINE_BUFFER_BATCH: int = 200
_STREAM_CHUNK_BYTES: int = 64 * 1024


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
                files.append((sort_idx, LogFile(key=key, kind=kind, size=obj.get("Size"))))
            next_token = resp.get("NextContinuationToken")
            if next_token is None:
                break
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
