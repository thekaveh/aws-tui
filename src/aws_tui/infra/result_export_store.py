"""Bounded local staging for result exports, independent of AWS and UI state.

An existing destination version is the caller's explicit overwrite acknowledgment.
New files publish exclusively; existing files are checked before atomic replacement.
The latter is not an OS-wide compare-and-swap against arbitrary external writers.
Call stage methods serially from drained, owned background I/O workers.
"""

from __future__ import annotations

import contextlib
import os
import secrets
import stat
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

StorageCode = Literal["path", "exists", "changed", "permission", "cancelled", "io"]
_MESSAGES: dict[StorageCode, str] = {
    "path": "Choose a regular local export file in an existing directory",
    "exists": "Export file exists; confirm its replacement first",
    "changed": "Export destination changed; review and confirm it again",
    "permission": "Export file permission denied",
    "cancelled": "Export cancelled before publication",
    "io": "Export file could not be saved",
}
_FILE_LOCK = threading.RLock()
# Windows lacks these dir_fd operations. Its fallback still verifies the captured
# parent identity; POSIX also pins all stage operations to an open directory.
_USE_DIR_FD = all(
    operation in os.supports_dir_fd
    for operation in (os.open, os.stat, os.link, os.rename, os.unlink)
)


class ExportStorageError(Exception):
    def __init__(self, code: StorageCode) -> None:
        self.code = code
        self.category = code
        super().__init__(_MESSAGES[code])


@dataclass(frozen=True, slots=True)
class ExportFileVersion:
    device: int = field(repr=False)
    inode: int = field(repr=False)
    size: int = field(repr=False)
    mtime_ns: int = field(repr=False)
    ctime_ns: int = field(repr=False)


@dataclass(frozen=True, slots=True)
class ExportDestination:
    path: Path = field(repr=False)
    version: ExportFileVersion | None = field(repr=False)
    _parent_device: int = field(repr=False)
    _parent_inode: int = field(repr=False)


class ExportPermit:
    """Linearize cancellation against publication without locking filesystem I/O."""

    def __init__(self) -> None:
        self._cancelled = threading.Event()
        self._publication_lock = threading.Lock()
        self._publication_admitted = False

    def cancel(self) -> None:
        with self._publication_lock:
            self._cancelled.set()

    def check(self) -> None:
        if self._cancelled.is_set():
            raise ExportStorageError("cancelled")

    @property
    def publication_admitted(self) -> bool:
        with self._publication_lock:
            return self._publication_admitted

    @contextmanager
    def publication(self) -> Iterator[None]:
        with self._publication_lock:
            self.check()
            self._publication_admitted = True
        # Once admitted, the real publication result must be acknowledged even
        # if cancel() arrives while the filesystem operation is outstanding.
        yield


def _version(info: os.stat_result) -> ExportFileVersion:
    return ExportFileVersion(
        info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns
    )


def _mapped_os_error(error: OSError) -> ExportStorageError:
    if isinstance(error, PermissionError):
        return ExportStorageError("permission")
    if isinstance(error, FileExistsError):
        return ExportStorageError("exists")
    return ExportStorageError("io")


def _target_version(path: Path, parent_fd: int | None = None) -> ExportFileVersion | None:
    try:
        info = (
            path.lstat()
            if parent_fd is None
            else os.stat(path.name, dir_fd=parent_fd, follow_symlinks=False)
        )
    except FileNotFoundError:
        return None
    if not stat.S_ISREG(info.st_mode):
        raise ExportStorageError("path")
    return _version(info)


def _check_parent(destination: ExportDestination) -> None:
    try:
        info = destination.path.parent.lstat()
    except (FileNotFoundError, NotADirectoryError):
        raise ExportStorageError("changed") from None
    if not stat.S_ISDIR(info.st_mode) or (info.st_dev, info.st_ino) != (
        destination._parent_device,
        destination._parent_inode,
    ):
        raise ExportStorageError("changed")


def _check_destination(destination: ExportDestination, parent_fd: int | None) -> None:
    _check_parent(destination)
    actual = _target_version(destination.path, parent_fd)
    if destination.version is None and actual is not None:
        raise ExportStorageError("exists")
    if actual != destination.version:
        raise ExportStorageError("changed")


class ResultExportStore:
    """Inspect destinations without reading their contents; begin private staging."""

    def inspect(self, path: Path) -> ExportDestination:
        try:
            expanded = path.expanduser()
            if expanded.name in ("", ".", "..") or "\x00" in str(expanded):
                raise ExportStorageError("path")
            # Resolve only the parent: the explicit final filename must never
            # follow a symlink, including a dangling link.
            parent = expanded.parent.resolve(strict=True)
            info = parent.lstat()
            if not stat.S_ISDIR(info.st_mode):
                raise ExportStorageError("path")
            canonical = parent / expanded.name
            destination = ExportDestination(
                canonical, _target_version(canonical), info.st_dev, info.st_ino
            )
            _check_parent(destination)
            return destination
        except (ValueError, RuntimeError, FileNotFoundError, NotADirectoryError):
            raise ExportStorageError("path") from None
        except OSError as error:
            raise _mapped_os_error(error) from None

    def begin(
        self, destination: ExportDestination, *, permit: ExportPermit | None = None
    ) -> ExportStage:
        permit = permit or ExportPermit()
        parent_fd: int | None = None
        try:
            with _FILE_LOCK:
                permit.check()
                _check_parent(destination)
                if _USE_DIR_FD:
                    parent_fd = os.open(
                        destination.path.parent,
                        os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0),
                    )
                    opened = os.fstat(parent_fd)
                    if (opened.st_dev, opened.st_ino) != (
                        destination._parent_device,
                        destination._parent_inode,
                    ):
                        raise ExportStorageError("changed")
                _check_destination(destination, parent_fd)
                stage = ExportStage(destination, permit, parent_fd)
                parent_fd = None  # Stage owns the directory descriptor now.
                return stage
        except OSError as error:
            raise _mapped_os_error(error) from None
        finally:
            if parent_fd is not None:
                with contextlib.suppress(OSError):
                    os.close(parent_fd)


class ExportStage:
    """A private streaming file. abort() only ever removes its owned staging name."""

    def __init__(
        self, destination: ExportDestination, permit: ExportPermit, parent_fd: int | None
    ) -> None:
        self.destination = destination
        self._permit = permit
        self._parent_fd = parent_fd
        self._fd: int | None = None
        self._stage_name: str | None = None
        self._stage_identity: tuple[int, int] | None = None
        self._bytes = 0
        self._published = False
        try:
            name = ".aws-tui-export-" + secrets.token_hex(16)
            self._fd = os.open(
                destination.path.parent / name if parent_fd is None else name,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0),
                0o600,
                dir_fd=parent_fd,
            )
            self._stage_name = name
            info = os.fstat(self._fd)
            self._stage_identity = (info.st_dev, info.st_ino)
            _check_parent(destination)
        except BaseException:
            # The store retains ownership of parent_fd until construction returns.
            self._cleanup_stage()
            self._parent_fd = None
            raise

    @property
    def published(self) -> bool:
        return self._published

    def write(self, chunk: bytes) -> None:
        try:
            self._permit.check()
            if self._fd is None:
                raise ExportStorageError("io")
            pending = memoryview(chunk)
            while pending:
                self._permit.check()
                written = os.write(self._fd, pending)
                if written <= 0:
                    raise ExportStorageError("io")
                self._bytes += written
                pending = pending[written:]
        except OSError as error:
            self.abort()
            raise _mapped_os_error(error) from None
        except ExportStorageError:
            self.abort()
            raise

    def publish(self) -> int:
        if self._published:
            return self._bytes
        try:
            with _FILE_LOCK:
                self._permit.check()
                if self._fd is None or self._stage_name is None:
                    raise ExportStorageError("io")
                # os.write is unbuffered; fsync flushes all staged bytes before
                # the descriptor closes and before any final name is admitted.
                os.fsync(self._fd)
                try:
                    staged = self._stage_info()
                except FileNotFoundError:
                    raise ExportStorageError("changed") from None
                if (
                    not stat.S_ISREG(staged.st_mode)
                    or (staged.st_dev, staged.st_ino) != self._stage_identity
                    or staged.st_size != self._bytes
                ):
                    raise ExportStorageError("changed")
                fd, self._fd = self._fd, None
                os.close(fd)
                _check_destination(self.destination, self._parent_fd)
                source: str | Path = self._stage_name
                target: str | Path = self.destination.path.name
                if self._parent_fd is None:
                    source = self.destination.path.parent / source
                    target = self.destination.path
                with self._permit.publication():
                    if self.destination.version is None:
                        os.link(
                            source,
                            target,
                            src_dir_fd=self._parent_fd,
                            dst_dir_fd=self._parent_fd,
                        )
                    else:
                        os.replace(
                            source,
                            target,
                            src_dir_fd=self._parent_fd,
                            dst_dir_fd=self._parent_fd,
                        )
                        self._stage_name = None
                    self._published = True
                return self._bytes
        except OSError as error:
            raise _mapped_os_error(error) from None
        finally:
            # Cleanup cannot turn a successful publication into reported failure.
            self.abort()

    def _stage_info(self) -> os.stat_result:
        if self._stage_name is None:
            raise ExportStorageError("io")
        if self._parent_fd is not None:
            return os.stat(self._stage_name, dir_fd=self._parent_fd, follow_symlinks=False)
        _check_parent(self.destination)
        return (self.destination.path.parent / self._stage_name).lstat()

    def _cleanup_stage(self) -> None:
        if self._fd is not None:
            fd, self._fd = self._fd, None
            with contextlib.suppress(OSError):
                os.close(fd)
        if self._stage_name is None:
            return
        with contextlib.suppress(OSError, ExportStorageError):
            info = self._stage_info()
            identity = (info.st_dev, info.st_ino)
            if self._stage_identity is not None and identity != self._stage_identity:
                return
            if self._parent_fd is not None:
                os.unlink(self._stage_name, dir_fd=self._parent_fd)
            else:
                # Without a pinned directory, never delete a replacement file
                # reached through a rebound parent or a substituted stage name.
                _check_parent(self.destination)
                path = self.destination.path.parent / self._stage_name
                path.unlink()
            self._stage_name = None

    def abort(self) -> None:
        self._cleanup_stage()
        if self._parent_fd is not None:
            fd, self._parent_fd = self._parent_fd, None
            with contextlib.suppress(OSError):
                os.close(fd)
