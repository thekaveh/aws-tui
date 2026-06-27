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
