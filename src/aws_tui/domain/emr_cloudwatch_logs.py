"""Bounded, read-only CloudWatch logs for one explicitly selected EMR run.

Provider exceptions may contain log bodies. Only fresh, fixed-message failures
leave this module, including failures while closing an SDK client.
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import Callable, Coroutine, Iterable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, TypeVar

from aws_tui.domain.emr_serverless import (
    CloudWatchLogConfiguration,
    map_boto_error,
    parse_cloudwatch_monitoring,
)
from aws_tui.domain.filesystem import (
    AuthRequiredError,
    NotFoundError,
    PermissionDeniedError,
    ProviderError,
    ProviderUnreachableError,
    ThrottledError,
    ValidationError,
)

if TYPE_CHECKING:
    import aioboto3
    from botocore.config import Config as BotoConfig

DEFAULT_LOG_GROUP = "/aws/emr-serverless"
MAX_DISCOVERY_PAGES = 100
MAX_STREAMS = 200
MAX_EVENT_PAGES = 100
MAX_EVENTS = 10_000
MAX_READ_BYTES = 8 * 1024 * 1024
MAX_EVENT_BYTES = 1024 * 1024
READ_TIMEOUT_SECONDS = 30.0


@dataclass(frozen=True, slots=True)
class CloudWatchLogStream:
    name: str
    component: str
    attempt: int | None


@dataclass(frozen=True, slots=True)
class CloudWatchLogEvent:
    event_id: str
    timestamp_ms: int
    ingestion_time_ms: int
    message: str = field(repr=False)


@dataclass(frozen=True, slots=True)
class CloudWatchLogSnapshot:
    events: tuple[CloudWatchLogEvent, ...] = field(repr=False)
    bytes_read: int
    end_time_ms: int


_LIMIT_MESSAGES = frozenset(
    {
        "CloudWatch log page limit exceeded",
        "CloudWatch log stream limit exceeded",
        "CloudWatch log read limit exceeded",
        "CloudWatch repeated a continuation token",
    }
)


class _SafetyLimit(ProviderError):
    """Only module-owned fixed messages may cross the safe error boundary."""


def safe_cloudwatch_error(error: BaseException) -> ProviderError:
    """Classify without forwarding provider strings, bodies, args or chains."""
    if type(error) is _SafetyLimit and str(error) in _LIMIT_MESSAGES:
        return ProviderError(str(error))
    if isinstance(error, TimeoutError):
        return ProviderUnreachableError("CloudWatch logs unreachable")
    classified = error if isinstance(error, ProviderError) else map_boto_error(error)
    for kind, message in (
        (AuthRequiredError, "CloudWatch authentication required"),
        (PermissionDeniedError, "CloudWatch log access denied"),
        (ThrottledError, "CloudWatch log requests throttled"),
        (ProviderUnreachableError, "CloudWatch logs unreachable"),
        (NotFoundError, "CloudWatch logs not created yet"),
        (ValidationError, "CloudWatch log response invalid"),
    ):
        if isinstance(classified, kind):
            return kind(message)
    return ProviderError("CloudWatch log read failed")


_T = TypeVar("_T")


async def _safe_call(operation: Callable[[], Coroutine[Any, Any, _T]]) -> _T:
    failure: ProviderError | None = None
    cancelled = False
    try:
        async with asyncio.timeout(READ_TIMEOUT_SECONDS):
            return await operation()
    except asyncio.CancelledError:
        cancelled = True
    except Exception as error:
        failure = safe_cloudwatch_error(error)
    # Outside the handler: `from None` alone still retains __context__.
    if cancelled:
        raise asyncio.CancelledError from None
    assert failure is not None
    raise failure from None


def _text(value: object, *, max_bytes: int, max_chars: int | None = None) -> str:
    if not isinstance(value, str) or not value:
        raise ValidationError("CloudWatch log response invalid")
    if len(value) > max_bytes or (max_chars is not None and len(value) > max_chars):
        raise ValidationError("CloudWatch log response invalid")
    if len(value.encode("utf-8")) > max_bytes:
        raise ValidationError("CloudWatch log response invalid")
    return value


def _stream_name(value: object) -> str:
    name = _text(value, max_bytes=2048, max_chars=512)
    if ":" in name or "*" in name:
        raise ValidationError("CloudWatch log response invalid")
    return name


def _timestamp(value: object) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ValidationError("CloudWatch log response invalid")
    return value


def _rows(response: object, key: str) -> list[Any]:
    if not isinstance(response, dict):
        raise ValidationError("CloudWatch log response invalid")
    rows = response.get(key, [])
    if not isinstance(rows, list):
        raise ValidationError("CloudWatch log response invalid")
    return rows


def _next_token(response: Any, seen: set[str]) -> str | None:
    token = response.get("nextToken")
    if token is None:
        return None
    token = _text(token, max_bytes=8192)
    if token in seen:
        raise _SafetyLimit("CloudWatch repeated a continuation token")
    seen.add(token)
    return token


def cloudwatch_location(
    configuration: CloudWatchLogConfiguration,
    application_id: str,
    job_run_id: str,
) -> tuple[str, str]:
    """Return effective group and a literal, bounded discovery prefix."""
    parsed = parse_cloudwatch_monitoring(
        {
            "monitoringConfiguration": {
                "cloudWatchLoggingConfiguration": {
                    "enabled": configuration.enabled,
                    "logGroupName": configuration.log_group_name,
                    "logStreamNamePrefix": configuration.log_stream_name_prefix,
                }
            }
        }
    )
    if parsed is None or parsed.enabled is not True:
        raise ValidationError("CloudWatch logging is not enabled")
    for identifier in (application_id, job_run_id):
        if not identifier or re.fullmatch(r"[A-Za-z0-9-]+", identifier) is None:
            raise ValidationError("CloudWatch log target invalid")
    root = f"/applications/{application_id}/jobs/{job_run_id}/"
    prefix = parsed.log_stream_name_prefix if parsed.log_stream_name_prefix is not None else root
    _stream_name(prefix)
    return parsed.log_group_name or DEFAULT_LOG_GROUP, prefix


def classify_cloudwatch_stream(
    name: str,
    *,
    configuration: CloudWatchLogConfiguration,
    application_id: str,
    job_run_id: str,
) -> CloudWatchLogStream | None:
    """Parse only a run root outside the literal configured prefix."""
    _stream_name(name)
    _, prefix = cloudwatch_location(configuration, application_id, job_run_id)
    if not name.startswith(prefix):
        return None
    root = f"/applications/{application_id}/jobs/{job_run_id}/"
    if configuration.log_stream_name_prefix is None:
        suffix = name[len(root) :]
        if root in suffix:
            return None
    else:
        remaining = name[len(prefix) :]
        positions = [match.start() for match in re.finditer(re.escape(root), remaining)]
        if remaining.startswith(root[1:]):
            positions.insert(0, -1)
        if len(positions) != 1:
            return None
        suffix = remaining[positions[0] + len(root) :]
        # A different app/run before the selected root is ambiguous ownership.
        before = remaining[: max(0, positions[0])]
        if "applications/" in before or "jobs/" in before:
            return None
    segments = suffix.split("/")
    if not segments or any(not segment for segment in segments):
        return None
    attempt = None
    if segments[0] == "attempts":
        if len(segments) < 3 or not segments[1].isascii() or not segments[1].isdigit():
            return None
        attempt = int(segments[1])
        if attempt < 1:
            return None
        segments = segments[2:]
    return CloudWatchLogStream(name, segments[0], attempt)


def _stream_order(stream: CloudWatchLogStream) -> tuple[bool, str, int, str]:
    return (
        stream.component not in {"SPARK_DRIVER", "HIVE_DRIVER"},
        stream.component,
        stream.attempt or 0,
        stream.name,
    )


def cloudwatch_stream_listing(
    names: Iterable[str],
    *,
    configuration: CloudWatchLogConfiguration,
    application_id: str,
    job_run_id: str,
) -> tuple[CloudWatchLogStream, ...]:
    """Shared bounded classification for real and demo discovered identities."""
    streams: dict[str, CloudWatchLogStream] = {}
    for count, name in enumerate(names, start=1):
        if count > MAX_STREAMS:
            raise _SafetyLimit("CloudWatch log stream limit exceeded")
        stream = classify_cloudwatch_stream(
            name, configuration=configuration, application_id=application_id, job_run_id=job_run_id
        )
        if stream is not None:
            streams[stream.name] = stream
    return tuple(sorted(streams.values(), key=_stream_order))


async def list_cloudwatch_streams(
    *,
    session: aioboto3.Session,
    region_name: str | None,
    configuration: CloudWatchLogConfiguration,
    application_id: str,
    job_run_id: str,
    boto_config: BotoConfig | None = None,
) -> tuple[CloudWatchLogStream, ...]:
    async def operation() -> tuple[CloudWatchLogStream, ...]:
        group, prefix = cloudwatch_location(configuration, application_id, job_run_id)
        options: dict[str, Any] = {"region_name": region_name}
        if boto_config is not None:
            options["config"] = boto_config
        names: list[str] = []
        token = None
        seen: set[str] = set()
        async with session.client("logs", **options) as client:
            for _ in range(MAX_DISCOVERY_PAGES):
                request: dict[str, Any] = dict(
                    logGroupName=group,
                    logStreamNamePrefix=prefix,
                    orderBy="LogStreamName",
                    limit=50,
                )
                if token is not None:
                    request["nextToken"] = token
                response = await client.describe_log_streams(**request)
                for row in _rows(response, "logStreams"):
                    if len(names) >= MAX_STREAMS:
                        raise _SafetyLimit("CloudWatch log stream limit exceeded")
                    if not isinstance(row, dict):
                        raise ValidationError("CloudWatch log response invalid")
                    names.append(_stream_name(row.get("logStreamName")))
                token = _next_token(response, seen)
                if token is None:
                    break
            else:
                raise _SafetyLimit("CloudWatch log page limit exceeded")
        return cloudwatch_stream_listing(
            names, configuration=configuration, application_id=application_id, job_run_id=job_run_id
        )

    return await _safe_call(operation)


def cloudwatch_event_snapshot(
    events: Iterable[CloudWatchLogEvent],
    *,
    start_time_ms: int,
    end_time_ms: int,
) -> CloudWatchLogSnapshot:
    """Validate and bound already decoded events, including duplicates in budgets."""
    _timestamp(start_time_ms)
    _timestamp(end_time_ms)
    if start_time_ms > end_time_ms:
        raise ValidationError("CloudWatch log interval invalid")
    unique: dict[str, CloudWatchLogEvent] = {}
    total_bytes = 0
    for count, event in enumerate(events, start=1):
        _text(event.event_id, max_bytes=1024)
        timestamp = _timestamp(event.timestamp_ms)
        _timestamp(event.ingestion_time_ms)
        if not start_time_ms <= timestamp <= end_time_ms or not isinstance(event.message, str):
            raise ValidationError("CloudWatch log response invalid")
        if len(event.message) > MAX_EVENT_BYTES:
            raise _SafetyLimit("CloudWatch log read limit exceeded")
        size = len(event.message.encode("utf-8"))
        total_bytes += size
        if count > MAX_EVENTS or size > MAX_EVENT_BYTES or total_bytes > MAX_READ_BYTES:
            raise _SafetyLimit("CloudWatch log read limit exceeded")
        unique.setdefault(event.event_id, event)
    return CloudWatchLogSnapshot(tuple(unique.values()), total_bytes, end_time_ms)


async def read_cloudwatch_events(
    *,
    session: aioboto3.Session,
    region_name: str | None,
    log_group_name: str,
    stream_name: str,
    start_time_ms: int,
    end_time_ms: int,
    boto_config: BotoConfig | None = None,
) -> CloudWatchLogSnapshot:
    async def operation() -> CloudWatchLogSnapshot:
        config = parse_cloudwatch_monitoring(
            {
                "monitoringConfiguration": {
                    "cloudWatchLoggingConfiguration": {
                        "enabled": True,
                        "logGroupName": log_group_name,
                    }
                }
            }
        )
        if config is None or config.enabled is not True:
            raise ValidationError("CloudWatch log target invalid")
        _stream_name(stream_name)
        _timestamp(start_time_ms)
        _timestamp(end_time_ms)
        if start_time_ms > end_time_ms:
            raise ValidationError("CloudWatch log interval invalid")
        options: dict[str, Any] = {"region_name": region_name}
        if boto_config is not None:
            options["config"] = boto_config
        events: list[CloudWatchLogEvent] = []
        bytes_read = 0
        token = None
        seen: set[str] = set()
        async with session.client("logs", **options) as client:
            for _ in range(MAX_EVENT_PAGES):
                request: dict[str, Any] = dict(
                    logGroupName=log_group_name,
                    logStreamNames=[stream_name],
                    startTime=start_time_ms,
                    endTime=end_time_ms,
                    limit=1000,
                )
                if token is not None:
                    request["nextToken"] = token
                response = await client.filter_log_events(**request)
                for row in _rows(response, "events"):
                    if not isinstance(row, dict) or row.get("logStreamName") != stream_name:
                        raise ValidationError("CloudWatch log response invalid")
                    message = row.get("message")
                    if not isinstance(message, str):
                        raise ValidationError("CloudWatch log response invalid")
                    event = CloudWatchLogEvent(
                        _text(row.get("eventId"), max_bytes=1024),
                        _timestamp(row.get("timestamp")),
                        _timestamp(row.get("ingestionTime")),
                        message,
                    )
                    checked = cloudwatch_event_snapshot(
                        (event,), start_time_ms=start_time_ms, end_time_ms=end_time_ms
                    )
                    bytes_read += checked.bytes_read
                    if len(events) >= MAX_EVENTS or bytes_read > MAX_READ_BYTES:
                        raise _SafetyLimit("CloudWatch log read limit exceeded")
                    events.append(event)
                token = _next_token(response, seen)
                if token is None:
                    break
            else:
                raise _SafetyLimit("CloudWatch log page limit exceeded")
        return cloudwatch_event_snapshot(
            events, start_time_ms=start_time_ms, end_time_ms=end_time_ms
        )

    return await _safe_call(operation)
