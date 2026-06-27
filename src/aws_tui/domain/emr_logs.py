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


__all__ = [
    "DEFAULT_LOG_FILTER",
    "FilterMode",
    "LogChunk",
    "LogFile",
    "LogFileKind",
    "LogFilter",
    "S3LogLocation",
    "build_run_prefix",
    "parse_log_uri",
    "stream_log",
]
