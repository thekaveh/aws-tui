"""Synchronous, private local Athena SQL drafts with fixed failure codes.

Records deliberately contain no provider/domain objects or execution state.
Every enabled disk operation shares the configuration transaction lock.
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import stat
import tempfile
import threading
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from typing import Literal, cast

from aws_tui.infra.config_store import ConfigStore
from aws_tui.infra.paths import ensure_private_dir

DraftContext = tuple[str, str, str, str, str]
DraftCode = Literal["disabled", "read_only", "invalid", "unsupported", "limit", "io", "cancelled"]


@dataclass(frozen=True, slots=True)
class SqlDraft:
    id: str = field(repr=False)
    context: DraftContext = field(repr=False)
    sql: str = field(repr=False)
    created_at: datetime = field(repr=False)
    updated_at: datetime = field(repr=False)


@dataclass(frozen=True, slots=True)
class DraftStoreResult:
    records: tuple[SqlDraft, ...] = field(default=(), repr=False)
    code: DraftCode | None = None
    skipped: int = 0
    enabled: bool | None = None


SCHEMA_VERSION = 1
MAX_DRAFTS = 50
MAX_TOTAL_BYTES = 8_388_608
MAX_SQL_BYTES = 262_144
MAX_RECORD_BYTES = 278_528
CONTEXT_KEYS = ("connection_name", "region", "workgroup", "catalog", "database")


def draft_id(context: DraftContext) -> str:
    body = json.dumps(context, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return sha256(body).hexdigest()


def _timestamp(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _encode_record(record: SqlDraft) -> bytes:
    body = {
        "schema_version": SCHEMA_VERSION,
        "id": record.id,
        "created_at": _timestamp(record.created_at),
        "updated_at": _timestamp(record.updated_at),
        "context": dict(zip(CONTEXT_KEYS, record.context, strict=True)),
        "sql": record.sql,
    }
    return json.dumps(body, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode(
        "utf-8"
    )


class DraftPermit:
    def __init__(self) -> None:
        self._cancelled = threading.Event()

    def cancel(self) -> None:
        self._cancelled.set()

    @property
    def cancelled(self) -> bool:
        return self._cancelled.is_set()


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    value: dict[str, object] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("Invalid draft record")
        value[key] = item
    return value


def _reject_json_constant(value: str) -> object:
    raise ValueError("Invalid draft record")


def _decode_record(payload: bytes, expected_id: str) -> tuple[SqlDraft | None, DraftCode | None]:
    try:
        if len(payload) > MAX_RECORD_BYTES:
            return None, "limit"
        body = json.loads(
            payload.decode("utf-8"),
            object_pairs_hook=_unique_object,
            parse_constant=_reject_json_constant,
        )
        if type(body) is not dict or set(body) != {
            "schema_version",
            "id",
            "created_at",
            "updated_at",
            "context",
            "sql",
        }:
            return None, "invalid"
        if type(body["schema_version"]) is not int:
            return None, "invalid"
        if body["schema_version"] != SCHEMA_VERSION:
            return None, "unsupported"
        body = cast(dict[str, object], body)
        fields = body["context"]
        if type(fields) is not dict or set(fields) != set(CONTEXT_KEYS):
            return None, "invalid"
        fields = cast(dict[str, object], fields)
        values = tuple(fields[key] for key in CONTEXT_KEYS)
        if any(
            type(value) is not str or not value or len(value.encode("utf-8")) > 1_024
            for value in values
        ):
            return None, "invalid"
        context = cast(DraftContext, values)
        if (
            type(body["id"]) is not str
            or body["id"] != expected_id
            or draft_id(context) != expected_id
        ):
            return None, "invalid"
        sql = body["sql"]
        if type(sql) is not str or not sql.strip():
            return None, "invalid"
        if len(sql.encode("utf-8")) > MAX_SQL_BYTES:
            return None, "limit"
        times: list[datetime] = []
        for key in ("created_at", "updated_at"):
            raw = body[key]
            if type(raw) is not str or not raw.endswith("Z"):
                return None, "invalid"
            parsed = datetime.fromisoformat(raw[:-1] + "+00:00")
            if parsed.tzinfo is None or _timestamp(parsed) != raw:
                return None, "invalid"
            times.append(parsed)
        if times[0] > times[1]:
            return None, "invalid"
        return SqlDraft(expected_id, context, sql, times[0], times[1]), None
    except Exception:
        return None, "invalid"


_OWNED_RECORD = re.compile(r"[0-9a-f]{64}\.json\Z")


class _UnsafeDraftPath(Exception):
    pass


def _private_directory(directory: Path, *, create: bool) -> bool:
    try:
        before = directory.lstat()
    except FileNotFoundError:
        if not create:
            return False
    else:
        if not stat.S_ISDIR(before.st_mode) or stat.S_ISLNK(before.st_mode):
            raise _UnsafeDraftPath("Draft storage path is unavailable")
    # The refusal above happens BEFORE ensure_private_dir can chmod/follow a link.
    ensure_private_dir(directory)
    after = directory.lstat()
    if not stat.S_ISDIR(after.st_mode) or stat.S_ISLNK(after.st_mode):
        raise _UnsafeDraftPath("Draft storage path is unavailable")
    return True


def _read_regular_record(path: Path) -> bytes | None:
    try:
        before = path.lstat()
        if not stat.S_ISREG(before.st_mode) or before.st_size > MAX_RECORD_BYTES:
            return None
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
        descriptor = os.open(path, flags)
        try:
            opened = os.fstat(descriptor)
            after = path.lstat()
            if (
                not stat.S_ISREG(opened.st_mode)
                or not stat.S_ISREG(after.st_mode)
                or (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino)
                or (opened.st_dev, opened.st_ino) != (after.st_dev, after.st_ino)
                or opened.st_size > MAX_RECORD_BYTES
            ):
                return None
            chunks: list[bytes] = []
            remaining = MAX_RECORD_BYTES + 1
            while remaining:
                chunk = os.read(descriptor, min(remaining, 65_536))
                if not chunk:
                    break
                chunks.append(chunk)
                remaining -= len(chunk)
            payload = b"".join(chunks)
            return payload if len(payload) <= MAX_RECORD_BYTES else None
        finally:
            os.close(descriptor)
    except (OSError, ValueError):
        return None


def _atomic_record(directory: Path, name: str, payload: bytes, permit: DraftPermit) -> bool:
    if not _OWNED_RECORD.fullmatch(name):
        raise _UnsafeDraftPath("Draft storage path is unavailable")
    descriptor, raw_path = tempfile.mkstemp(prefix=".draft-", suffix=".tmp", dir=directory)
    temporary = Path(raw_path)
    try:
        with os.fdopen(descriptor, "wb") as output:
            output.write(payload)
            output.flush()
            os.fsync(output.fileno())
        if permit.cancelled:
            return False
        _private_directory(directory, create=False)
        if permit.cancelled:
            return False
        # Cancellation cannot roll back a physical replace once entered.
        temporary.replace(directory / name)
        if os.name == "posix":
            flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
            directory_fd = os.open(directory, flags)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        return True
    finally:
        with contextlib.suppress(FileNotFoundError):
            temporary.unlink()


def mutate_records(
    *,
    config: ConfigStore,
    directory: Path,
    operation: str,
    record: SqlDraft | None,
    selected_id: str | None,
    permit: DraftPermit,
    decode: Callable[[bytes, str], tuple[SqlDraft | None, DraftCode | None]],
    encode: Callable[[SqlDraft], bytes],
) -> DraftStoreResult:
    try:
        if config.read_only:
            return DraftStoreResult(code="read_only", enabled=False)
        if operation == "list" and not config.load().athena_sql_drafts:
            return DraftStoreResult(enabled=False)
        with config.transaction():
            enabled = config.load().athena_sql_drafts
            if operation == "save" and (not enabled or permit.cancelled):
                return DraftStoreResult(
                    code="cancelled" if permit.cancelled else "disabled", enabled=enabled
                )
            if operation == "enable":
                config.set_athena_sql_drafts(True)
                try:
                    _private_directory(directory, create=True)
                except Exception:
                    confirmed: bool | None = None
                    try:
                        config.set_athena_sql_drafts(False)
                        confirmed = False
                    except Exception:
                        pass
                    return DraftStoreResult(code="io", enabled=confirmed)
                enabled = True
            if operation == "disable":
                config.set_athena_sql_drafts(False)
                enabled = False
            exists = _private_directory(directory, create=operation == "save")
            if not exists:
                return DraftStoreResult(enabled=enabled)
            paths = [path for path in directory.iterdir() if _OWNED_RECORD.fullmatch(path.name)]
            if operation in {"delete", "clear", "disable"}:
                for path in paths:
                    if operation != "delete" or path.stem == selected_id:
                        if not stat.S_ISDIR(path.lstat().st_mode):
                            path.unlink()  # Unlink an owned symlink itself; never its target.
                        else:
                            return DraftStoreResult(code="io", enabled=enabled)
            if operation == "save":
                if record is None:
                    return DraftStoreResult(code="invalid", enabled=enabled)
                payload = encode(record)
                if (
                    len(payload) > MAX_RECORD_BYTES
                    or len(record.sql.encode("utf-8")) > MAX_SQL_BYTES
                ):
                    return DraftStoreResult(code="limit", enabled=enabled)
                target = directory / (record.id + ".json")
                replacement = next((path for path in paths if path.name == target.name), None)
                if replacement is not None:
                    old_bytes = _read_regular_record(replacement)
                    if old_bytes is None:
                        return DraftStoreResult(code="invalid", enabled=enabled)
                    old, issue = decode(old_bytes, replacement.stem)
                    if old is None:
                        return DraftStoreResult(code=issue or "invalid", enabled=enabled)
                    record = replace(record, created_at=old.created_at)
                    payload = encode(record)
                    confirmed_record, issue = decode(payload, record.id)
                    if confirmed_record is None:
                        return DraftStoreResult(code=issue or "invalid", enabled=enabled)
                projected_count = len(paths) + (replacement is None)
                projected_bytes = len(payload)
                for path in paths:
                    if path != replacement:
                        info = path.lstat()
                        projected_bytes += (
                            info.st_size if stat.S_ISREG(info.st_mode) else MAX_RECORD_BYTES + 1
                        )
                if projected_count > MAX_DRAFTS or projected_bytes > MAX_TOTAL_BYTES:
                    return DraftStoreResult(code="limit", enabled=enabled)
                if not _atomic_record(directory, target.name, payload, permit):
                    return DraftStoreResult(code="cancelled", enabled=enabled)
            for temporary in directory.iterdir():
                if (
                    temporary.name.startswith(".draft-")
                    and temporary.name.endswith(".tmp")
                    and not stat.S_ISDIR(temporary.lstat().st_mode)
                ):
                    temporary.unlink()
            records: list[SqlDraft] = []
            skipped = 0
            for path in directory.iterdir():
                if not _OWNED_RECORD.fullmatch(path.name):
                    continue
                read_payload = _read_regular_record(path)
                parsed, issue = (
                    decode(read_payload, path.stem)
                    if read_payload is not None
                    else (None, "invalid")
                )
                if parsed is None:
                    skipped += 1
                else:
                    records.append(parsed)
                    with contextlib.suppress(OSError, NotImplementedError):
                        path.chmod(0o600, follow_symlinks=False)
            if not any(directory.iterdir()):
                directory.rmdir()
            return DraftStoreResult(
                records=tuple(
                    sorted(records, key=lambda row: (row.updated_at, row.id), reverse=True)
                ),
                skipped=skipped,
                enabled=enabled,
            )
    except Exception:
        return DraftStoreResult(code="io")


class AthenaDraftStore:
    def __init__(self, *, config: ConfigStore, directory: Path) -> None:
        self._config = config
        self._directory = directory

    def _call(
        self,
        operation: str,
        record: SqlDraft | None = None,
        selected_id: str | None = None,
        permit: DraftPermit | None = None,
    ) -> DraftStoreResult:
        return mutate_records(
            config=self._config,
            directory=self._directory,
            operation=operation,
            record=record,
            selected_id=selected_id,
            permit=permit or DraftPermit(),
            decode=_decode_record,
            encode=_encode_record,
        )

    def list(self) -> DraftStoreResult:
        return self._call("list")

    def save(self, record: SqlDraft, *, permit: DraftPermit) -> DraftStoreResult:
        try:
            if type(record) is not SqlDraft or type(record.sql) is not str:
                return DraftStoreResult(code="invalid")
            if any(
                type(value) is not datetime or value.tzinfo is None
                for value in (record.created_at, record.updated_at)
            ):
                return DraftStoreResult(code="invalid")
            if type(record.context) is not tuple or len(record.context) != 5:
                return DraftStoreResult(code="invalid")
            if len(record.sql.encode("utf-8")) > MAX_SQL_BYTES:
                return DraftStoreResult(code="limit")
            parsed, issue = _decode_record(_encode_record(record), record.id)
        except Exception:
            return DraftStoreResult(code="invalid")
        if parsed is None:
            return DraftStoreResult(code=issue or "invalid")
        return self._call("save", record=parsed, permit=permit)

    def delete(self, draft_id: str) -> DraftStoreResult:
        if type(draft_id) is not str or not re.fullmatch(r"[0-9a-f]{64}", draft_id):
            return DraftStoreResult(code="invalid")
        return self._call("delete", selected_id=draft_id)

    def clear(self) -> DraftStoreResult:
        return self._call("clear")

    def set_enabled(self, enabled: bool) -> DraftStoreResult:
        if type(enabled) is not bool:
            return DraftStoreResult(code="invalid")
        result = self._call("enable" if enabled else "disable")
        if result.code is None or result.enabled is not None:
            return result
        try:
            confirmed = self._config.load().athena_sql_drafts
        except Exception:
            confirmed = None
        return replace(result, enabled=confirmed)
