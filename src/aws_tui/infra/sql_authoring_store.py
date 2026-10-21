"""Bounded UTF-8 SQL file I/O, independent of AWS and editor state.

The caller acknowledges an existing file version before replacement. New files
use an exclusive link publication, so a competing newly created target wins.
Existing files are rechecked immediately before atomic replacement; this is not
an OS-wide compare-and-swap guarantee against arbitrary external writers.
"""

from __future__ import annotations

import contextlib
import os
import stat
import tempfile
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from hashlib import sha256
from pathlib import Path
from typing import Literal

MAX_SQL_BYTES = 1_048_576
StorageCode = Literal[
    "path", "missing", "permission", "limit", "encoding", "exists", "changed", "cancelled", "io"
]
_MESSAGES: dict[StorageCode, str] = {
    "path": "Choose a regular local .sql file (symlinks are not supported)",
    "missing": "SQL file is missing",
    "permission": "SQL file permission denied",
    "limit": "SQL file exceeds the 1 MiB size limit",
    "encoding": "SQL file must contain valid UTF-8",
    "exists": "SQL file exists; confirm its replacement first",
    "changed": "SQL file changed; review and confirm its current version",
    "cancelled": "SQL file write cancelled before publication",
    "io": "SQL file could not be read or saved",
}
_FILE_LOCK = threading.RLock()


class SqlStorageError(Exception):
    def __init__(self, code: StorageCode) -> None:
        self.code = code
        super().__init__(_MESSAGES[code])


@dataclass(frozen=True, slots=True)
class SqlFileVersion:
    device: int = field(repr=False)
    inode: int = field(repr=False)
    modified_ns: int = field(repr=False)
    changed_ns: int = field(repr=False)
    size: int = field(repr=False)
    digest: str = field(repr=False)


@dataclass(frozen=True, slots=True)
class SqlFile:
    path: Path = field(repr=False)
    sql: str = field(repr=False)
    # None only for an acknowledged publication whose subsequent metadata
    # read failed. The next Save must inspect/confirm a fresh target version.
    version: SqlFileVersion | None = field(repr=False)


class SqlWritePermit:
    """A cancelled worker may finish staging but must not start publication."""

    def __init__(self) -> None:
        self._cancelled = threading.Event()
        self._publication_lock = threading.Lock()
        self._publication_admitted = False

    def cancel(self) -> None:
        with self._publication_lock:
            self._cancelled.set()

    def check(self) -> None:
        if self._cancelled.is_set():
            raise SqlStorageError("cancelled")

    @property
    def publication_admitted(self) -> bool:
        with self._publication_lock:
            return self._publication_admitted

    @contextmanager
    def publication(self) -> Iterator[None]:
        """Linearize cancellation versus publication, after all target reads.

        Cancellation cannot undo a publication already admitted here. Callers
        must drain their worker and acknowledge its result, even when cancelled.
        """
        with self._publication_lock:
            self.check()
            self._publication_admitted = True
        # No filesystem I/O under this lock: cancel() is safe on the UI loop.
        # An admitted operation may finish; the owner must drain/acknowledge it.
        yield


def _path(path: Path) -> Path:
    try:
        result = path.expanduser().absolute()
        if result.suffix.lower() != ".sql" or "\x00" in str(result):
            raise SqlStorageError("path")
        return result
    except (OSError, ValueError, RuntimeError):
        raise SqlStorageError("path") from None


def _version(info: os.stat_result, payload: bytes) -> SqlFileVersion:
    return SqlFileVersion(
        info.st_dev,
        info.st_ino,
        info.st_mtime_ns,
        info.st_ctime_ns,
        info.st_size,
        sha256(payload).hexdigest(),
    )


def _read(path: Path) -> SqlFile:
    before = path.lstat()
    if not stat.S_ISREG(before.st_mode):
        raise SqlStorageError("path")
    if before.st_size > MAX_SQL_BYTES:
        raise SqlStorageError("limit")
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
    with os.fdopen(fd, "rb") as source:
        opened = os.fstat(source.fileno())
        if not stat.S_ISREG(opened.st_mode) or (opened.st_dev, opened.st_ino) != (
            before.st_dev,
            before.st_ino,
        ):
            raise SqlStorageError("changed")
        payload = source.read(MAX_SQL_BYTES + 1)
        after = os.fstat(source.fileno())
    if len(payload) > MAX_SQL_BYTES:
        raise SqlStorageError("limit")
    if _version(opened, payload) != _version(after, payload) or len(payload) != after.st_size:
        raise SqlStorageError("changed")
    try:
        sql = payload.decode("utf-8")
    except UnicodeError:
        raise SqlStorageError("encoding") from None
    return SqlFile(path, sql, _version(after, payload))


def _check_target(path: Path, expected: SqlFileVersion | None) -> None:
    try:
        info = path.lstat()
    except FileNotFoundError:
        if expected is not None:
            raise SqlStorageError("changed") from None
        return
    if not stat.S_ISREG(info.st_mode):
        raise SqlStorageError("path")
    if expected is None:
        raise SqlStorageError("exists")
    if _read(path).version != expected:
        raise SqlStorageError("changed")


def _mapped_os_error(error: OSError) -> SqlStorageError:
    if isinstance(error, PermissionError):
        return SqlStorageError("permission")
    if isinstance(error, FileNotFoundError):
        return SqlStorageError("missing")
    if isinstance(error, FileExistsError):
        return SqlStorageError("exists")
    return SqlStorageError("io")


class SqlFileStore:
    """Call from an owned thread worker; no UI, credentials or network access."""

    def read(self, path: Path) -> SqlFile:
        try:
            with _FILE_LOCK:
                return _read(_path(path))
        except OSError as error:
            raise _mapped_os_error(error) from None

    def inspect(self, path: Path) -> SqlFile | None:
        """Read the version which a subsequent overwrite decision acknowledges."""
        try:
            return self.read(path)
        except SqlStorageError as error:
            if error.code == "missing":
                return None
            raise

    def write(
        self,
        path: Path,
        sql: str,
        *,
        expected: SqlFileVersion | None = None,
        permit: SqlWritePermit | None = None,
    ) -> SqlFile:
        try:
            payload = sql.encode("utf-8")
        except UnicodeError:
            raise SqlStorageError("encoding") from None
        if len(payload) > MAX_SQL_BYTES:
            raise SqlStorageError("limit")
        permit = permit or SqlWritePermit()
        stage: Path | None = None
        try:
            with _FILE_LOCK:
                permit.check()
                path = _path(path)
                _check_target(path, expected)
                fd, stage_name = tempfile.mkstemp(prefix=".sql-", dir=path.parent)
                stage = Path(stage_name)
                with os.fdopen(fd, "wb") as target:
                    target.write(payload)
                    target.flush()
                    os.fsync(target.fileno())
                permit.check()
                _check_target(path, expected)
                with permit.publication():
                    if expected is None:
                        # Exclusive publication prevents an existence-check race.
                        os.link(stage, path)
                        # Removing the staging hard link changes inode ctime;
                        # finish normal cleanup before capturing its version.
                        with contextlib.suppress(OSError):
                            stage.unlink()
                            stage = None
                    else:
                        stage.replace(path)
                        stage = None
                try:
                    version = _version(path.stat(), payload) if stage is None else None
                except OSError:
                    # Publication succeeded. Do not tell the caller it failed
                    # merely because a later metadata lookup was denied.
                    version = None
                return SqlFile(path, sql, version)
        except OSError as error:
            raise _mapped_os_error(error) from None
        finally:
            if stage is not None:
                with contextlib.suppress(OSError):
                    stage.unlink()
