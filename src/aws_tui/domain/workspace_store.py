"""Private workspace records; domain persistence uses the shared infrastructure lock."""

from __future__ import annotations

import contextlib
import json
import os
import secrets
import stat
import tempfile
import threading
from collections.abc import Callable, Iterator
from dataclasses import replace
from pathlib import Path
from typing import Any

from aws_tui.domain.navigation_workspace import (
    MAX_WORKSPACES,
    Bookmark,
    FileLocation,
    NavigationTarget,
    Workspace,
    validate_workspace_name,
)
from aws_tui.domain.navigation_workspace import (
    WorkspaceError as WorkspaceError,
)
from aws_tui.infra.config_store import ConfigError, ConfigStore
from aws_tui.infra.paths import config_home

MAX_LIBRARY_BYTES = 1_048_576


def _reparse(info: os.stat_result) -> bool:
    # Junctions/mount-point reparse entries can report ordinary directory or
    # regular-file modes on Windows; the mode alone does not prove no alias.
    return bool(getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT)


class WorkspaceWritePermit:
    """Cancellation before admission; admitted filesystem work must drain."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._canceled = self._admitted = self._published = False

    def _check(self) -> None:
        if self._canceled:
            raise WorkspaceError("cancelled")
        if self._admitted:
            raise WorkspaceError("invalid")

    def check(self) -> None:
        with self._lock:
            self._check()

    def admit(self) -> None:
        with self._lock:
            self._check()
            self._admitted = True

    def cancel(self) -> bool:
        with self._lock:
            if self._admitted:
                return False
            self._canceled = True
            return True

    def mark_published(self) -> None:
        with self._lock:
            self._published = True

    @property
    def published(self) -> bool:
        with self._lock:
            return self._published


def _object(value: object, fields: set[str]) -> dict[str, Any]:
    if type(value) is not dict or set(value) != fields:
        raise WorkspaceError("invalid")
    return value


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    value: dict[str, object] = {}
    for key, item in pairs:
        if key in value:
            raise WorkspaceError("invalid")
        value[key] = item
    return value


def _target_json(target: NavigationTarget) -> dict[str, object]:
    body: dict[str, object] = {
        "service": target.service,
        "connection": target.connection,
        "region": target.region,
    }
    if target.location is not None:
        body["location"] = (
            {"local": target.location.local}
            if target.location.local is not None
            else {"s3": list(target.location.s3 or ())}
        )
    else:
        body["selectors"] = dict(target.selectors)
    return body


def _target(value: object) -> NavigationTarget:
    if type(value) is not dict:
        raise WorkspaceError("invalid")
    fields = {"service", "connection", "region"}
    body = _object(
        value, fields | ({"location"} if value.get("service") == "s3" else {"selectors"})
    )
    if body["service"] == "s3":
        raw = body["location"]
        if type(raw) is not dict:
            raise WorkspaceError("invalid")
        if set(raw) == {"local"}:
            location = FileLocation(local=raw["local"])
        elif set(raw) == {"s3"} and type(raw["s3"]) is list:
            location = FileLocation(s3=tuple(raw["s3"]))
        else:
            raise WorkspaceError("invalid")
        return NavigationTarget(
            body["service"], body["connection"], body["region"], location=location
        )
    if type(body["selectors"]) is not dict:
        raise WorkspaceError("invalid")
    return NavigationTarget(
        body["service"], body["connection"], body["region"], tuple(body["selectors"].items())
    )


def _workspace(value: object) -> Workspace:
    body = _object(
        value,
        {"name", "active_service", "connection", "region", "file_panes", "scopes", "bookmarks"},
    )
    if type(body["scopes"]) is not list or type(body["bookmarks"]) is not list:
        raise WorkspaceError("invalid")
    panes = body["file_panes"]
    if panes is not None and (type(panes) is not list or len(panes) != 2):
        raise WorkspaceError("invalid")
    bookmarks = []
    for raw in body["bookmarks"]:
        bookmark = _object(raw, {"name", "destination"})
        bookmarks.append(Bookmark(bookmark["name"], _target(bookmark["destination"])))
    return Workspace(
        body["name"],
        body["active_service"],
        body["connection"],
        body["region"],
        (_target(panes[0]), _target(panes[1])) if panes is not None else None,
        tuple(_target(target) for target in body["scopes"]),
        tuple(bookmarks),
    )


def _workspace_json(record: Workspace) -> dict[str, object]:
    return {
        "name": record.name,
        "active_service": record.active_service,
        "connection": record.connection,
        "region": record.region,
        "file_panes": [_target_json(target) for target in record.file_panes]
        if record.file_panes
        else None,
        "scopes": [_target_json(target) for target in record.scopes],
        "bookmarks": [
            {"name": bookmark.name, "destination": _target_json(bookmark.destination)}
            for bookmark in record.bookmarks
        ],
    }


class WorkspaceStore:
    def __init__(self, *, path: Path | None = None):
        self.path = path if path is not None else config_home() / "workspaces.json"
        self._transaction = ConfigStore(path=self.path)

    def _check_parent(self, expected: os.stat_result) -> None:
        current = self.path.parent.lstat()
        if (
            _reparse(current)
            or not stat.S_ISDIR(current.st_mode)
            or (current.st_dev, current.st_ino)
            != (
                expected.st_dev,
                expected.st_ino,
            )
        ):
            raise WorkspaceError("changed")

    @contextlib.contextmanager
    def _parent(self, *, create: bool) -> Iterator[tuple[int | None, os.stat_result] | None]:
        # Refuse an alias BEFORE directory creation, chmod or ConfigStore's lock
        # can follow it. A held POSIX descriptor also owns staging/cleanup when
        # the original directory is renamed during filesystem I/O.
        try:
            before = self.path.parent.lstat()
        except FileNotFoundError:
            if not create:
                yield None
                return
            self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            before = self.path.parent.lstat()
        if not stat.S_ISDIR(before.st_mode) or _reparse(before):
            raise WorkspaceError("invalid")
        directory_fd = None
        try:
            if os.name != "nt":
                directory_fd = os.open(
                    self.path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
                )
                opened = os.fstat(directory_fd)
                self._check_parent(opened)
                if (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino):
                    raise WorkspaceError("changed")
                os.fchmod(directory_fd, 0o700)
            else:
                self._check_parent(before)
            yield directory_fd, before
        finally:
            if directory_fd is not None:
                os.close(directory_fd)

    def _read(self, directory_fd: int | None = None) -> tuple[Workspace, ...]:
        path = self.path.name if directory_fd is not None else self.path
        try:
            before = os.stat(path, dir_fd=directory_fd, follow_symlinks=False)
        except FileNotFoundError:
            return ()
        if not stat.S_ISREG(before.st_mode) or _reparse(before):
            raise WorkspaceError("invalid")
        if before.st_size > MAX_LIBRARY_BYTES:
            raise WorkspaceError("limit")
        fd = os.open(
            path,
            os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0),
            dir_fd=directory_fd,
        )
        with os.fdopen(fd, "rb") as source:
            opened = os.fstat(source.fileno())
            if (
                _reparse(opened)
                or not stat.S_ISREG(opened.st_mode)
                or (opened.st_dev, opened.st_ino)
                != (
                    before.st_dev,
                    before.st_ino,
                )
            ):
                raise WorkspaceError("changed")
            payload = source.read(MAX_LIBRARY_BYTES + 1)
            after = os.fstat(source.fileno())
        if len(payload) > MAX_LIBRARY_BYTES:
            raise WorkspaceError("limit")
        if (opened.st_size, opened.st_mtime_ns, opened.st_ctime_ns) != (
            after.st_size,
            after.st_mtime_ns,
            after.st_ctime_ns,
        ) or len(payload) != after.st_size:
            raise WorkspaceError("changed")
        try:
            body = _object(
                json.loads(payload.decode("utf-8"), object_pairs_hook=_unique_object),
                {"schema_version", "workspaces"},
            )
            if type(body["schema_version"]) is not int:
                raise WorkspaceError("invalid")
            if body["schema_version"] != 1:
                raise WorkspaceError("unsupported")
            if type(body["workspaces"]) is not list:
                raise WorkspaceError("invalid")
            if len(body["workspaces"]) > MAX_WORKSPACES:
                raise WorkspaceError("limit")
            records = tuple(_workspace(value) for value in body["workspaces"])
            if len({record.name.casefold() for record in records}) != len(records):
                raise WorkspaceError("invalid")
            return tuple(sorted(records, key=lambda record: record.name.casefold()))
        except (ValueError, TypeError, UnicodeError, RecursionError):
            raise WorkspaceError("invalid") from None

    def list(self) -> tuple[Workspace, ...]:
        try:
            with self._parent(create=False) as parent:
                if parent is None:
                    return ()
                directory_fd, identity = parent
                self._check_parent(identity)
                with self._transaction.transaction(directory_fd=directory_fd, strict_leaf=True):
                    self._check_parent(identity)
                    records = self._read(directory_fd)
                    self._check_parent(identity)
                    return records
        except (OSError, ConfigError):
            raise WorkspaceError("io") from None

    def _write(
        self,
        records: tuple[Workspace, ...],
        previous: tuple[Workspace, ...],
        permit: WorkspaceWritePermit,
        directory_fd: int | None,
        parent_identity: os.stat_result,
    ) -> None:
        if len(records) > MAX_WORKSPACES:
            raise WorkspaceError("limit")
        payload = json.dumps(
            {"schema_version": 1, "workspaces": [_workspace_json(record) for record in records]},
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
        ).encode("utf-8")
        if len(payload) > MAX_LIBRARY_BYTES:
            raise WorkspaceError("limit")
        stage: str | Path | None = None
        owned: os.stat_result | None = None
        try:
            permit.check()
            self._check_parent(parent_identity)
            if directory_fd is not None:
                name = f".workspace-{secrets.token_hex(16)}"
                fd = os.open(
                    name,
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                    0o600,
                    dir_fd=directory_fd,
                )
                stage = name
            else:
                fd, name = tempfile.mkstemp(prefix=".workspace-", dir=self.path.parent)
                stage = Path(name)
            with os.fdopen(fd, "wb") as output:
                owned = os.fstat(output.fileno())
                output.write(payload)
                output.flush()
                os.fsync(output.fileno())
            permit.check()
            self._check_parent(parent_identity)
            if self._read(directory_fd) != previous:
                raise WorkspaceError("changed")
            current_stage = os.stat(stage, dir_fd=directory_fd, follow_symlinks=False)
            if (
                _reparse(current_stage)
                or not stat.S_ISREG(current_stage.st_mode)
                or (current_stage.st_dev, current_stage.st_ino) != (owned.st_dev, owned.st_ino)
            ):
                raise WorkspaceError("changed")
            # Admission holds no filesystem I/O under the short cancellation
            # lock. Once admitted, the owner must drain and report the result.
            permit.admit()
            target = self.path.name if directory_fd is not None else self.path
            try:
                os.replace(stage, target, src_dir_fd=directory_fd, dst_dir_fd=directory_fd)
            except OSError:
                # Some filesystem wrappers can fail after the actual rename.
                # Only our owned inode proves publication, never equal bytes.
                current = os.stat(target, dir_fd=directory_fd, follow_symlinks=False)
                if (
                    _reparse(current)
                    or not stat.S_ISREG(current.st_mode)
                    or (
                        current.st_dev,
                        current.st_ino,
                    )
                    != (owned.st_dev, owned.st_ino)
                ):
                    raise
            permit.mark_published()
            stage = None
            self._check_parent(parent_identity)
        finally:
            if stage is not None:
                with contextlib.suppress(OSError):
                    current_stage = os.stat(stage, dir_fd=directory_fd, follow_symlinks=False)
                    if (
                        owned is not None
                        and not _reparse(current_stage)
                        and (current_stage.st_dev, current_stage.st_ino)
                        == (
                            owned.st_dev,
                            owned.st_ino,
                        )
                    ):
                        os.unlink(stage, dir_fd=directory_fd)

    def _mutate(
        self,
        change: Callable[[tuple[Workspace, ...]], tuple[Workspace, ...]],
        permit: WorkspaceWritePermit | None,
    ) -> None:
        permit = permit or WorkspaceWritePermit()
        try:
            permit.check()
            with self._parent(create=True) as parent:
                assert parent is not None
                directory_fd, identity = parent
                self._check_parent(identity)
                with self._transaction.transaction(directory_fd=directory_fd, strict_leaf=True):
                    permit.check()
                    self._check_parent(identity)
                    previous = self._read(directory_fd)
                    records = change(previous)
                    self._write(
                        tuple(sorted(records, key=lambda row: row.name.casefold())),
                        previous,
                        permit,
                        directory_fd,
                        identity,
                    )
        except (OSError, ConfigError):
            raise WorkspaceError("io") from None

    @staticmethod
    def _find(
        records: tuple[Workspace, ...],
        name: str,
        expected: Workspace | None = None,
    ) -> Workspace:
        validate_workspace_name(name)
        record = next((row for row in records if row.name.casefold() == name.casefold()), None)
        if record is None:
            raise WorkspaceError("missing")
        if expected is not None and record != expected:
            raise WorkspaceError("changed")
        return record

    def save(self, record: Workspace, *, permit: WorkspaceWritePermit | None = None) -> None:
        if not isinstance(record, Workspace):
            raise WorkspaceError("invalid")

        def add(previous: tuple[Workspace, ...]) -> tuple[Workspace, ...]:
            if any(row.name.casefold() == record.name.casefold() for row in previous):
                raise WorkspaceError("exists")
            return (*previous, record)

        self._mutate(add, permit)

    def rename(
        self,
        name: str,
        new_name: str,
        *,
        expected: Workspace | None = None,
        permit: WorkspaceWritePermit | None = None,
    ) -> None:
        validate_workspace_name(new_name)

        def change(previous: tuple[Workspace, ...]) -> tuple[Workspace, ...]:
            target = self._find(previous, name, expected)
            if any(
                row is not target and row.name.casefold() == new_name.casefold() for row in previous
            ):
                raise WorkspaceError("exists")
            return tuple(replace(row, name=new_name) if row is target else row for row in previous)

        self._mutate(change, permit)

    def delete(
        self,
        name: str,
        *,
        expected: Workspace | None = None,
        permit: WorkspaceWritePermit | None = None,
    ) -> None:
        def change(previous: tuple[Workspace, ...]) -> tuple[Workspace, ...]:
            target = self._find(previous, name, expected)
            return tuple(row for row in previous if row is not target)

        self._mutate(change, permit)

    def _update_bookmarks(
        self,
        name: str,
        update: Callable[[tuple[Bookmark, ...]], tuple[Bookmark, ...]],
        expected: Workspace | None,
        permit: WorkspaceWritePermit | None,
    ) -> None:
        def change(previous: tuple[Workspace, ...]) -> tuple[Workspace, ...]:
            target = self._find(previous, name, expected)
            updated = replace(target, bookmarks=update(target.bookmarks))
            return tuple(updated if row is target else row for row in previous)

        self._mutate(change, permit)

    def add_bookmark(
        self,
        name: str,
        bookmark: Bookmark,
        *,
        expected: Workspace | None = None,
        permit: WorkspaceWritePermit | None = None,
    ) -> None:
        if not isinstance(bookmark, Bookmark):
            raise WorkspaceError("invalid")

        def add(bookmarks: tuple[Bookmark, ...]) -> tuple[Bookmark, ...]:
            if any(row.name.casefold() == bookmark.name.casefold() for row in bookmarks):
                raise WorkspaceError("exists")
            return (*bookmarks, bookmark)

        self._update_bookmarks(name, add, expected, permit)

    @staticmethod
    def _bookmark(bookmarks: tuple[Bookmark, ...], name: str) -> Bookmark:
        validate_workspace_name(name)
        bookmark = next((row for row in bookmarks if row.name.casefold() == name.casefold()), None)
        if bookmark is None:
            raise WorkspaceError("missing")
        return bookmark

    def rename_bookmark(
        self,
        name: str,
        bookmark_name: str,
        new_name: str,
        *,
        expected: Workspace | None = None,
        permit: WorkspaceWritePermit | None = None,
    ) -> None:
        validate_workspace_name(new_name)

        def rename(bookmarks: tuple[Bookmark, ...]) -> tuple[Bookmark, ...]:
            target = self._bookmark(bookmarks, bookmark_name)
            if any(
                row is not target and row.name.casefold() == new_name.casefold()
                for row in bookmarks
            ):
                raise WorkspaceError("exists")
            return tuple(replace(row, name=new_name) if row is target else row for row in bookmarks)

        self._update_bookmarks(name, rename, expected, permit)

    def delete_bookmark(
        self,
        name: str,
        bookmark_name: str,
        *,
        expected: Workspace | None = None,
        permit: WorkspaceWritePermit | None = None,
    ) -> None:
        def delete(bookmarks: tuple[Bookmark, ...]) -> tuple[Bookmark, ...]:
            target = self._bookmark(bookmarks, bookmark_name)
            return tuple(row for row in bookmarks if row is not target)

        self._update_bookmarks(name, delete, expected, permit)
