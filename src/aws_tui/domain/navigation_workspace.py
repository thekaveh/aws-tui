"""Immutable navigation intent, with no providers, credentials or operation state."""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from pathlib import PurePosixPath, PureWindowsPath

MAX_WORKSPACES = 100
MAX_SCOPES = 32
MAX_BOOKMARKS = 100
SELECTORS = {
    "athena": frozenset({"workgroup", "catalog", "database"}),
    "glue": frozenset({"database", "table"}),
    "emr-serverless": frozenset({"application"}),
}
SERVICES = frozenset({"s3", *SELECTORS})


class WorkspaceError(Exception):
    def __init__(self, code: str):
        self.code = code
        super().__init__(
            {
                "invalid": "Workspace library or navigation intent is invalid",
                "unsupported": "Workspace library schema version is unsupported",
                "limit": "Workspace library size or count limit reached",
                "exists": "A workspace or bookmark with that name already exists",
                "missing": "The selected workspace or bookmark no longer exists",
                "changed": "Workspace library changed; refresh and retry",
                "cancelled": "Workspace write canceled before publication",
                "io": "Workspace library could not be read or saved",
            }.get(code, "Workspace operation failed")
        )


def _text(value: object, maximum: int, *, literal: bool = False, empty: bool = False) -> bool:
    if type(value) is not str or len(value) > maximum or "\x00" in value:
        return False
    try:
        value.encode("utf-8")
    except UnicodeError:
        return False
    return (empty or bool(value.strip())) and (
        literal or not any(unicodedata.category(char) == "Cc" for char in value)
    )


def validate_workspace_name(name: str) -> None:
    if not _text(name, 80) or name != name.strip():
        raise WorkspaceError("invalid")


@dataclass(frozen=True, slots=True)
class FileLocation:
    local: str | None = None
    s3: tuple[str, ...] | None = None

    def __post_init__(self) -> None:
        if (self.local is None) == (self.s3 is None):
            raise WorkspaceError("invalid")
        if self.local is not None:
            if not _text(self.local, 32768, literal=True) or not (
                PurePosixPath(self.local).is_absolute() or PureWindowsPath(self.local).is_absolute()
            ):
                raise WorkspaceError("invalid")
        elif (
            type(self.s3) is not tuple
            or len(self.s3) > 1024
            or any(
                not _text(part, 4096, literal=True, empty=True) or "/" in part for part in self.s3
            )
            or (self.s3 and not self.s3[0])
            or sum(len(part.encode("utf-8")) for part in self.s3) > 16384
        ):
            raise WorkspaceError("invalid")


@dataclass(frozen=True, slots=True)
class NavigationTarget:
    service: str
    connection: str | None = None
    region: str | None = None
    selectors: tuple[tuple[str, str], ...] = ()
    location: FileLocation | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.service, str) or self.service not in SERVICES:
            raise WorkspaceError("invalid")
        if (self.connection is None) != (self.region is None) or (
            self.connection is not None
            and (not _text(self.connection, 256) or not _text(self.region, 128))
        ):
            raise WorkspaceError("invalid")
        if self.service == "s3":
            if (
                not isinstance(self.location, FileLocation)
                or self.selectors != ()
                or (self.location.local is not None) != (self.connection is None)
            ):
                raise WorkspaceError("invalid")
            return
        if (
            self.connection is None
            or self.location is not None
            or type(self.selectors) is not tuple
        ):
            raise WorkspaceError("invalid")
        allowed = SELECTORS[self.service]
        if len(self.selectors) > len(allowed) or any(
            type(pair) is not tuple
            or len(pair) != 2
            or type(pair[0]) is not str
            or pair[0] not in allowed
            or not _text(pair[1], 1024, literal=True)
            for pair in self.selectors
        ):
            raise WorkspaceError("invalid")
        selected = dict(self.selectors)
        if len(selected) != len(self.selectors):
            raise WorkspaceError("invalid")
        if self.service == "athena" and (
            ("catalog" in selected and "workgroup" not in selected)
            or ("database" in selected and not {"workgroup", "catalog"} <= selected.keys())
        ):
            raise WorkspaceError("invalid")
        if self.service == "glue" and "table" in selected and "database" not in selected:
            raise WorkspaceError("invalid")
        object.__setattr__(self, "selectors", tuple(sorted(self.selectors)))


@dataclass(frozen=True, slots=True)
class Bookmark:
    name: str
    destination: NavigationTarget

    def __post_init__(self) -> None:
        validate_workspace_name(self.name)
        if not isinstance(self.destination, NavigationTarget):
            raise WorkspaceError("invalid")


@dataclass(frozen=True, slots=True)
class Workspace:
    name: str
    active_service: str
    connection: str | None = None
    region: str | None = None
    file_panes: tuple[NavigationTarget, NavigationTarget] | None = None
    scopes: tuple[NavigationTarget, ...] = ()
    bookmarks: tuple[Bookmark, ...] = ()

    def __post_init__(self) -> None:
        validate_workspace_name(self.name)
        if (
            type(self.active_service) is not str
            or self.active_service not in SERVICES
            or (self.connection is None) != (self.region is None)
        ):
            raise WorkspaceError("invalid")
        if self.connection is not None and (
            not _text(self.connection, 256) or not _text(self.region, 128)
        ):
            raise WorkspaceError("invalid")
        if self.file_panes is not None and (
            type(self.file_panes) is not tuple
            or len(self.file_panes) != 2
            or any(
                not isinstance(target, NavigationTarget) or target.service != "s3"
                for target in self.file_panes
            )
        ):
            raise WorkspaceError("invalid")
        if (
            type(self.scopes) is not tuple
            or len(self.scopes) > MAX_SCOPES
            or any(
                not isinstance(target, NavigationTarget) or target.service == "s3"
                for target in self.scopes
            )
            or type(self.bookmarks) is not tuple
            or len(self.bookmarks) > MAX_BOOKMARKS
            or any(not isinstance(bookmark, Bookmark) for bookmark in self.bookmarks)
        ):
            raise WorkspaceError("invalid")
        scope_keys = {(target.service, target.connection, target.region) for target in self.scopes}
        if len(scope_keys) != len(self.scopes) or len(
            {b.name.casefold() for b in self.bookmarks}
        ) != len(self.bookmarks):
            raise WorkspaceError("invalid")
        if (self.active_service == "s3" and self.file_panes is None) or (
            self.active_service != "s3"
            and (self.active_service, self.connection, self.region) not in scope_keys
        ):
            raise WorkspaceError("invalid")
