"""Private, bounded transfer summaries. Call synchronous disk APIs in owned workers.

The default retention is the newest 100 records, ordered by updated UTC time
then id (both descending). Each id owns one JSON file; corrupt or unowned files
are never removed by trimming or clear. No provider/client data belongs here.
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import stat
import tempfile
import threading
from collections.abc import Iterator
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlsplit

Operation = Literal["copy", "move", "delete"]
HistoryStatus = Literal["completed", "skipped", "failed", "cancelled", "outcome_unknown"]
Publication = Literal["never_attempted", "possibly_published", "confirmed_terminal"]
FailureReason = Literal[
    "provider_error",
    "permission_denied",
    "not_found",
    "conflict",
    "connection_changed",
    "cancelled",
    "interrupted",
    "invalid_request",
    "persistence_error",
]
SCHEMA_VERSION = 1
MAX_METADATA_BYTES = 64 * 1024
_OPERATIONS = {"copy", "move", "delete"}
_STATUSES = {"completed", "skipped", "failed", "cancelled", "outcome_unknown"}
_PUBLICATIONS = {"never_attempted", "possibly_published", "confirmed_terminal"}
_FAILURES = {
    "provider_error",
    "permission_denied",
    "not_found",
    "conflict",
    "connection_changed",
    "cancelled",
    "interrupted",
    "invalid_request",
    "persistence_error",
}
_LOCKS: dict[Path, threading.RLock] = {}
_LOCKS_GUARD = threading.Lock()


def validate_transfer_id(value: str) -> None:
    if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{16}", value) is None:
        raise ValueError("transfer_id must be exactly 16 lowercase hexadecimal characters")


def _text(value: object, maximum: int = 8192) -> None:
    if not isinstance(value, str) or len(value) > maximum or "\x00" in value:
        raise ValueError("invalid bounded text")


def _uri(value: str) -> None:
    _text(value)
    parsed = urlsplit(value)
    if (
        parsed.scheme not in {"file", "local", "s3"}
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
    ):
        raise ValueError(
            "history paths must be literal file/local/s3 URIs without credentials or queries"
        )


def _bytes(value: int | None) -> None:
    if value is not None and (type(value) is not int or value < 0):
        raise ValueError("byte count must be a nonnegative integer or None")


def _date(value: datetime) -> None:
    if not isinstance(value, datetime) or value.utcoffset() != timedelta(0):
        raise ValueError("timestamps must be timezone-aware UTC datetimes")


@dataclass(frozen=True, slots=True)
class TransferConnectionIdentity:
    """Original routing identity; fingerprint is SHA256, never raw configuration."""

    kind: str
    name: str
    fingerprint: str

    def __post_init__(self) -> None:
        if self.kind not in {"local", "aws", "s3-compatible"}:
            raise ValueError("connection kind must be local, aws, or s3-compatible")
        _text(self.name, 1024)
        if (
            not isinstance(self.fingerprint, str)
            or re.fullmatch(r"[0-9a-f]{64}", self.fingerprint) is None
        ):
            raise ValueError("connection fingerprint must be a lowercase SHA256 hash")


@dataclass(frozen=True, slots=True)
class TransferHistoryDescriptor:
    """Versioned begin payload; contains only safe, retry-relevant intent."""

    operation: Operation
    source_connection: TransferConnectionIdentity
    destination_connection: TransferConnectionIdentity | None
    source_uri: str
    destination_uri: str | None
    bytes_total: int | None = None

    def __post_init__(self) -> None:
        if self.operation not in _OPERATIONS:
            raise ValueError("invalid operation")
        if not isinstance(self.source_connection, TransferConnectionIdentity):
            raise ValueError("source identity required")
        _uri(self.source_uri)
        if self.operation == "delete":
            if self.destination_connection is not None or self.destination_uri is not None:
                raise ValueError("delete must not have a destination")
        else:
            if not isinstance(
                self.destination_connection, TransferConnectionIdentity
            ) or not isinstance(self.destination_uri, str):
                raise ValueError("copy/move destination identity and URI required")
            _uri(self.destination_uri)
        _bytes(self.bytes_total)


@dataclass(frozen=True, slots=True)
class TransferHistoryRecord:
    id: str
    operation: Operation
    source_connection: TransferConnectionIdentity
    destination_connection: TransferConnectionIdentity | None
    source_uri: str
    destination_uri: str | None
    started_at: datetime
    updated_at: datetime
    finished_at: datetime | None
    bytes_done: int
    bytes_total: int | None
    status: HistoryStatus
    publication: Publication
    failure_reason: FailureReason | None

    def __post_init__(self) -> None:
        validate_transfer_id(self.id)
        TransferHistoryDescriptor(
            self.operation,
            self.source_connection,
            self.destination_connection,
            self.source_uri,
            self.destination_uri,
            self.bytes_total,
        )
        _date(self.started_at)
        _date(self.updated_at)
        if self.updated_at < self.started_at:
            raise ValueError("updated timestamp precedes begin")
        if self.finished_at is not None:
            _date(self.finished_at)
            if not self.started_at <= self.finished_at <= self.updated_at:
                raise ValueError("invalid finished timestamp")
        _bytes(self.bytes_done)
        if (
            self.bytes_done is None
            or self.status not in _STATUSES
            or self.publication not in _PUBLICATIONS
            or (self.failure_reason is not None and self.failure_reason not in _FAILURES)
        ):
            raise ValueError("invalid status, publication, failure category, or bytes_done")
        if (self.status == "outcome_unknown") != (self.finished_at is None):
            raise ValueError("only outcome_unknown lacks a finished timestamp")


def descriptor_to_json(descriptor: TransferHistoryDescriptor) -> dict[str, Any]:
    return {"schema_version": SCHEMA_VERSION, **asdict(descriptor)}


def _identity(value: object) -> TransferConnectionIdentity:
    if not isinstance(value, dict) or set(value) != {"kind", "name", "fingerprint"}:
        raise ValueError("invalid identity schema")
    return TransferConnectionIdentity(**value)


def descriptor_from_json(value: object) -> TransferHistoryDescriptor:
    if (
        not isinstance(value, dict)
        or set(value)
        != {
            "schema_version",
            "operation",
            "source_connection",
            "destination_connection",
            "source_uri",
            "destination_uri",
            "bytes_total",
        }
        or type(value["schema_version"]) is not int
        or value["schema_version"] != SCHEMA_VERSION
    ):
        raise ValueError("unsupported descriptor schema")
    return TransferHistoryDescriptor(
        operation=value["operation"],
        source_connection=_identity(value["source_connection"]),
        destination_connection=None
        if value["destination_connection"] is None
        else _identity(value["destination_connection"]),
        source_uri=value["source_uri"],
        destination_uri=value["destination_uri"],
        bytes_total=value["bytes_total"],
    )


def record_to_json(record: TransferHistoryRecord) -> dict[str, Any]:
    value = {"schema_version": SCHEMA_VERSION, **asdict(record)}
    for key in ("started_at", "updated_at", "finished_at"):
        date = value[key]
        value[key] = None if date is None else date.isoformat()
    return value


def record_from_json(value: object) -> TransferHistoryRecord:
    fields = set(TransferHistoryRecord.__dataclass_fields__) | {"schema_version"}
    if (
        not isinstance(value, dict)
        or set(value) != fields
        or type(value["schema_version"]) is not int
        or value["schema_version"] != SCHEMA_VERSION
    ):
        raise ValueError("unsupported history schema")
    data = value.copy()
    del data["schema_version"]
    data["source_connection"] = _identity(data["source_connection"])
    if data["destination_connection"] is not None:
        data["destination_connection"] = _identity(data["destination_connection"])
    for key in ("started_at", "updated_at", "finished_at"):
        raw = data[key]
        if key == "finished_at" and raw is None:
            continue
        if not isinstance(raw, str):
            raise ValueError("invalid timestamp encoding")
        data[key] = datetime.fromisoformat(raw)
    return TransferHistoryRecord(**data)


def ensure_private_directory(path: Path) -> None:
    # Check existing ancestors as well as the directory itself. Never resolve
    # through a symlink and then treat its target as owned metadata.
    if any(parent.is_symlink() for parent in (path, *path.parents)):
        raise ValueError("metadata directory must not traverse symlinks")
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    if os.name == "posix":
        path.chmod(0o700)


def read_metadata(path: Path) -> str:
    """Read a bounded regular file without following its final symlink."""
    if path.is_symlink():
        raise ValueError("symlink metadata is not owned")
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    with os.fdopen(fd, "rb") as stream:
        info = os.fstat(stream.fileno())
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_size > MAX_METADATA_BYTES
            or info.st_mode & 0o444 == 0
        ):
            raise ValueError("unreadable or oversized metadata")
        raw = stream.read(MAX_METADATA_BYTES + 1)
    if len(raw) > MAX_METADATA_BYTES:
        raise ValueError("oversized metadata")
    return raw.decode("utf-8")


def parse_metadata_json(raw: str) -> Any:
    """Reject duplicate keys rather than letting later values change ownership."""

    def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate metadata key")
            result[key] = value
        return result

    return json.loads(raw, object_pairs_hook=unique_object)


def fsync_directory(path: Path) -> None:
    if os.name != "posix":
        return
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


class TransferHistoryStore:
    """Serialized metadata storage; writes fail visibly, bad reads are skipped."""

    def __init__(self, base_dir: Path, retention_limit: int = 100) -> None:
        if type(retention_limit) is not int or retention_limit < 1:
            raise ValueError("retention_limit must be a positive integer")
        self.base_dir = base_dir.absolute()
        self.retention_limit = retention_limit
        ensure_private_directory(self.base_dir)
        with _LOCKS_GUARD:
            self._lock = _LOCKS.setdefault(self.base_dir, threading.RLock())

    @contextlib.contextmanager
    def _serialized(self) -> Iterator[None]:
        with self._lock:
            ensure_private_directory(self.base_dir)
            # Per-directory advisory lock also serializes separate POSIX processes.
            fd = os.open(
                self.base_dir / ".history.lock",
                os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0),
                0o600,
            )
            try:
                if os.name == "posix":
                    import fcntl

                    os.fchmod(fd, 0o600)
                    fcntl.flock(fd, fcntl.LOCK_EX)
                yield
            finally:
                os.close(fd)

    def save(self, record: TransferHistoryRecord) -> None:
        # Revalidate even records constructed by bypassing frozen dataclass APIs.
        record = record_from_json(record_to_json(record))
        encoded = json.dumps(
            record_to_json(record), ensure_ascii=True, separators=(",", ":")
        ).encode("utf-8")
        if len(encoded) > MAX_METADATA_BYTES:
            raise ValueError("oversized metadata")
        with self._serialized():
            target = self.base_dir / f"{record.id}.json"
            if target.is_symlink():
                raise ValueError("refusing symlink metadata destination")
            fd, temporary = tempfile.mkstemp(prefix=".history-", suffix=".tmp", dir=self.base_dir)
            staging = Path(temporary)
            try:
                with os.fdopen(fd, "wb") as stream:
                    stream.write(encoded)
                    stream.flush()
                    os.fsync(stream.fileno())
                staging.replace(target)
                fsync_directory(self.base_dir)
                for expired in self._load_all()[self.retention_limit :]:
                    self._remove_owned(expired)
                fsync_directory(self.base_dir)
            finally:
                staging.unlink(missing_ok=True)

    def _load_all(self) -> tuple[TransferHistoryRecord, ...]:
        records = []
        for path in self.base_dir.glob("*.json"):
            try:
                validate_transfer_id(path.stem)
                record = record_from_json(parse_metadata_json(read_metadata(path)))
                if record.id != path.stem:
                    continue
                records.append(record)
            except (ValueError, TypeError, KeyError, OSError, UnicodeError, RecursionError):
                continue
        return tuple(sorted(records, key=lambda r: (r.updated_at, r.id), reverse=True))

    def load(self) -> tuple[TransferHistoryRecord, ...]:
        with self._serialized():
            return self._load_all()[: self.retention_limit]

    def _remove_owned(self, record: TransferHistoryRecord) -> None:
        path = self.base_dir / f"{record.id}.json"
        try:
            current = record_from_json(parse_metadata_json(read_metadata(path)))
        except (ValueError, TypeError, KeyError, OSError, UnicodeError, RecursionError):
            return
        if current == record:
            # Bad/unowned reads are skipped, but an owned deletion failing is
            # a real clear/retention failure which the caller must see.
            path.unlink(missing_ok=True)

    def clear(self) -> None:
        with self._serialized():
            for record in self._load_all():
                self._remove_owned(record)
            fsync_directory(self.base_dir)
