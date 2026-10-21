"""Private named SQL snippets, separate from drafts and AWS saved queries."""

from __future__ import annotations

import contextlib
import json
import os
import stat
import tempfile
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from aws_tui.infra.config_store import ConfigError, ConfigStore
from aws_tui.infra.paths import config_home, ensure_private_dir
from aws_tui.infra.sql_authoring_store import SqlStorageError, SqlWritePermit

MAX_SNIPPETS = 100
MAX_SNIPPET_BYTES = 65_536
MAX_LIBRARY_BYTES = 8_388_608


class SqlSnippetError(Exception):
    def __init__(self, code: str) -> None:
        self.code = code
        message = {
            "invalid": "Local snippet library or input is invalid",
            "unsupported": "Local snippet library version is unsupported",
            "limit": "Local snippet library size or count limit reached",
            "exists": "A local snippet with that name already exists",
            "missing": "Local snippet no longer exists",
            "changed": "Local snippet library changed; refresh and retry",
            "cancelled": "Local snippet write cancelled before publication",
            "io": "Local snippet library could not be read or saved",
        }.get(code, "Local snippet operation failed")
        super().__init__(message)


@dataclass(frozen=True, slots=True)
class SqlSnippet:
    id: str = field(repr=False)
    name: str = field(repr=False)
    sql: str = field(repr=False)
    provenance: str = field(repr=False)
    created_at: str = field(repr=False)


def _text(value: object, maximum: int) -> bool:
    if type(value) is not str:
        return False
    try:
        value.encode("utf-8")
    except UnicodeError:
        return False
    return (
        bool(value.strip())
        and len(value) <= maximum
        and all(ord(char) >= 32 and ord(char) != 127 for char in value)
    )


def _valid(record: SqlSnippet) -> bool:
    try:
        return (
            len(record.id) == 32
            and all(c in "0123456789abcdef" for c in record.id)
            and _text(record.name, 80)
            and _text(record.provenance, 256)
            and type(record.sql) is str
            and len(record.sql.encode("utf-8")) <= MAX_SNIPPET_BYTES
            and type(record.created_at) is str
            and datetime.fromisoformat(record.created_at).tzinfo is not None
        )
    except (UnicodeError, ValueError):
        return False


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    obj: dict[str, object] = {}
    for key, value in pairs:
        if key in obj:
            raise ValueError("duplicate snippet field")
        obj[key] = value
    return obj


class SqlSnippetStore:
    def __init__(self, *, path: Path | None = None) -> None:
        self._path = path if path is not None else config_home() / "sql-snippets.json"
        # The existing public configuration transaction supplies the same
        # portable OS/process lock behavior, scoped to this separate library.
        self._transaction = ConfigStore(path=self._path)

    def _records(self) -> tuple[SqlSnippet, ...]:
        try:
            before = self._path.lstat()
        except FileNotFoundError:
            return ()
        if not stat.S_ISREG(before.st_mode):
            raise SqlSnippetError("invalid")
        if before.st_size > MAX_LIBRARY_BYTES:
            raise SqlSnippetError("limit")
        fd = os.open(
            self._path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
        )
        with os.fdopen(fd, "rb") as source:
            opened = os.fstat(source.fileno())
            if not stat.S_ISREG(opened.st_mode) or (opened.st_dev, opened.st_ino) != (
                before.st_dev,
                before.st_ino,
            ):
                raise SqlSnippetError("changed")
            payload = source.read(MAX_LIBRARY_BYTES + 1)
            after = os.fstat(source.fileno())
        if len(payload) > MAX_LIBRARY_BYTES:
            raise SqlSnippetError("limit")
        if (opened.st_size, opened.st_mtime_ns, opened.st_ctime_ns) != (
            after.st_size,
            after.st_mtime_ns,
            after.st_ctime_ns,
        ) or len(payload) != after.st_size:
            raise SqlSnippetError("changed")
        try:
            body = json.loads(payload.decode("utf-8"), object_pairs_hook=_unique_object)
            if (
                type(body) is not dict
                or set(body) != {"schema_version", "snippets"}
                or type(body["schema_version"]) is not int
            ):
                raise SqlSnippetError("invalid")
            if body["schema_version"] != 1:
                raise SqlSnippetError("unsupported")
            items = body["snippets"]
            if type(items) is not list:
                raise SqlSnippetError("invalid")
            if len(items) > MAX_SNIPPETS:
                raise SqlSnippetError("limit")
            records: list[SqlSnippet] = []
            for item in items:
                if (
                    type(item) is not dict
                    or set(item) != {"id", "name", "sql", "provenance", "created_at"}
                    or any(type(v) is not str for v in item.values())
                ):
                    raise SqlSnippetError("invalid")
                record = SqlSnippet(**item)
                if not _valid(record):
                    raise SqlSnippetError("invalid")
                records.append(record)
            if len({r.id for r in records}) != len(records) or len(
                {r.name.casefold() for r in records}
            ) != len(records):
                raise SqlSnippetError("invalid")
            return tuple(records)
        except (UnicodeError, ValueError, TypeError, RecursionError):
            raise SqlSnippetError("invalid") from None

    def list(self) -> tuple[SqlSnippet, ...]:
        try:
            if not self._path.exists():
                return ()
            with self._transaction.transaction():
                return self._records()
        except (OSError, ConfigError):
            raise SqlSnippetError("io") from None

    def _write(
        self,
        records: tuple[SqlSnippet, ...],
        previous: tuple[SqlSnippet, ...],
        permit: SqlWritePermit,
    ) -> None:
        payload = json.dumps(
            {
                "schema_version": 1,
                "snippets": [
                    {
                        "id": r.id,
                        "name": r.name,
                        "sql": r.sql,
                        "provenance": r.provenance,
                        "created_at": r.created_at,
                    }
                    for r in records
                ],
            },
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
        ).encode("utf-8")
        if len(payload) > MAX_LIBRARY_BYTES:
            raise SqlSnippetError("limit")
        stage: Path | None = None
        try:
            permit.check()
            fd, stage_name = tempfile.mkstemp(prefix=".snippet-", dir=self._path.parent)
            stage = Path(stage_name)
            with os.fdopen(fd, "wb") as target:
                target.write(payload)
                target.flush()
                os.fsync(target.fileno())
            permit.check()
            if self._records() != previous:
                raise SqlSnippetError("changed")
            with permit.publication():
                stage.replace(self._path)
                stage = None
        finally:
            if stage is not None:
                with contextlib.suppress(OSError):
                    stage.unlink()

    def add(
        self, name: str, sql: str, *, provenance: str, permit: SqlWritePermit | None = None
    ) -> SqlSnippet:
        permit = permit or SqlWritePermit()
        record = SqlSnippet(uuid4().hex, name, sql, provenance, datetime.now(UTC).isoformat())
        try:
            if type(sql) is str and len(sql.encode("utf-8")) > MAX_SNIPPET_BYTES:
                raise SqlSnippetError("limit")
            if not _valid(record):
                raise SqlSnippetError("invalid")
            permit.check()
            ensure_private_dir(self._path.parent)
            with self._transaction.transaction():
                previous = self._records()
                if any(r.name.casefold() == name.casefold() for r in previous):
                    raise SqlSnippetError("exists")
                if len(previous) >= MAX_SNIPPETS:
                    raise SqlSnippetError("limit")
                self._write((*previous, record), previous, permit)
            return record
        except SqlStorageError as error:
            raise SqlSnippetError(error.code) from None
        except (OSError, ConfigError):
            raise SqlSnippetError("io") from None
        except UnicodeError:
            raise SqlSnippetError("invalid") from None

    def delete(self, snippet_id: str, *, permit: SqlWritePermit | None = None) -> None:
        permit = permit or SqlWritePermit()
        try:
            permit.check()
            if not self._path.exists():
                raise SqlSnippetError("missing")
            with self._transaction.transaction():
                previous = self._records()
                records = tuple(r for r in previous if r.id != snippet_id)
                if len(records) == len(previous):
                    raise SqlSnippetError("missing")
                self._write(records, previous, permit)
        except SqlStorageError as error:
            raise SqlSnippetError(error.code) from None
        except (OSError, ConfigError):
            raise SqlSnippetError("io") from None
