"""Read-only, bounded plans for a separate local-tree S3 upload workflow.

This does not relax CrossFsCopy's directory publication contract. Destination
inspection is metadata-only; payload reads and mutations belong to execution.
"""

from __future__ import annotations

import posixpath
from dataclasses import dataclass, field
from typing import Protocol

from aws_tui.domain.cross_fs import ConflictResolution
from aws_tui.domain.filesystem import (
    EntryKind,
    FileEntry,
    PathRef,
    ProviderError,
    UnsupportedSourceError,
)


class UploadPlanError(ProviderError):
    """The requested upload cannot be mapped into an unambiguous safe plan."""


class UploadSourceChangedError(UploadPlanError):
    """A reviewed local source is no longer the same regular-file snapshot."""


class UploadSource(Protocol):
    async def inspect_upload_entry(self, path: PathRef) -> FileEntry: ...

    async def list_upload_directory(self, path: PathRef) -> list[FileEntry]: ...


class UploadDestination(Protocol):
    def upload_location(self, path: PathRef) -> tuple[str, str]: ...

    async def inspect_upload_destination(self, path: PathRef) -> FileEntry | None: ...


@dataclass(frozen=True, slots=True)
class UploadExclusion:
    path: PathRef
    reason: str


@dataclass(frozen=True, slots=True)
class UploadObject:
    source: PathRef
    requested_destination: PathRef
    destination: PathRef
    source_entry: FileEntry
    destination_entry: FileEntry | None
    conflict: bool
    location: tuple[str, str]


@dataclass(frozen=True, slots=True)
class UploadPlan:
    source_root: PathRef
    destination_prefix: PathRef
    policy: ConflictResolution
    objects: tuple[UploadObject, ...]
    excluded: tuple[UploadExclusion, ...]
    empty_directories: tuple[PathRef, ...]
    source_root_entry: FileEntry
    source: UploadSource = field(repr=False, compare=False)
    destination: UploadDestination = field(repr=False, compare=False)
    reserved_paths: tuple[PathRef, ...] = ()

    @property
    def known_bytes(self) -> int:
        return sum(row.source_entry.size or 0 for row in self.objects)

    @property
    def conflicts(self) -> int:
        return sum(row.conflict for row in self.objects)


def _location(destination: UploadDestination, path: PathRef, *, file: bool) -> tuple[str, str]:
    bucket, key = destination.upload_location(path)
    if not isinstance(bucket, str) or not bucket or not isinstance(key, str):
        raise UploadPlanError("Choose an unambiguous S3 bucket and prefix")
    if "\x00" in key or (file and (not key or key.endswith("/"))):
        raise UploadPlanError("Upload destination must name one exact object key")
    try:
        length = len(key.encode("utf-8"))
    except UnicodeError as exc:
        raise UploadPlanError("Upload destination key is not valid UTF-8") from exc
    if length > 1024:
        raise UploadPlanError("S3 object keys cannot exceed 1024 UTF-8 bytes")
    return bucket, key


def renamed_upload_path(path: PathRef, attempt: int) -> PathRef:
    """Use the existing file-copy suffix convention without path normalization."""
    stem, suffix = posixpath.splitext(path.name)
    return path.with_name(f"{stem} ({attempt}){suffix}")


def checked_upload_location(destination: UploadDestination, path: PathRef) -> tuple[str, str]:
    """Validate a runtime rename with the same exact-key limits as planning."""
    return _location(destination, path, file=True)


def _validate_file_snapshot(path: PathRef, entry: FileEntry) -> None:
    if (
        entry.kind != EntryKind.FILE
        or type(entry.size) is not int
        or not 0 <= entry.size <= 5 * 1024**4
        or not isinstance(entry.etag, str)
        or not entry.etag
    ):
        raise UploadPlanError(f"Upload source has no safe regular-file snapshot: {path.as_posix()}")


async def plan_upload(
    source: UploadSource,
    destination: UploadDestination,
    source_root: PathRef,
    destination_prefix: PathRef,
    *,
    policy: ConflictResolution = ConflictResolution.ERROR,
    max_entries: int = 10_000,
    max_depth: int = 128,
) -> UploadPlan:
    """Walk first, then inspect exact destination keys, without opening payloads.

    The limits count the root and excluded entries as the generic copy walk
    does. Caller-supplied smaller limits support bounded contexts; larger limits
    cannot bypass the application's existing traversal limits.
    """
    if not isinstance(policy, ConflictResolution):
        raise UploadPlanError("Unknown upload conflict policy")
    if type(max_entries) is not int or not 1 <= max_entries <= 10_000:
        raise UploadPlanError("Upload entry safety limit must be between 1 and 10000")
    if type(max_depth) is not int or not 0 <= max_depth <= 128:
        raise UploadPlanError("Upload depth safety limit must be between 0 and 128")
    if not callable(getattr(source, "inspect_upload_entry", None)) or not callable(
        getattr(source, "list_upload_directory", None)
    ):
        raise UploadPlanError("Source does not support local upload inspection")
    if not callable(getattr(destination, "upload_location", None)) or not callable(
        getattr(destination, "inspect_upload_destination", None)
    ):
        raise UploadPlanError("Destination does not support reviewed S3 uploads")
    _location(destination, destination_prefix, file=False)
    try:
        root = await source.inspect_upload_entry(source_root)
    except ProviderError as exc:
        raise UploadPlanError(f"Cannot inspect upload source: {exc}") from exc
    if root.kind != EntryKind.DIRECTORY:
        raise UploadPlanError("Choose a local directory for recursive upload")

    files: list[tuple[PathRef, FileEntry]] = []
    excluded: list[UploadExclusion] = []
    empty: list[PathRef] = []
    count = 0

    async def walk(path: PathRef, depth: int) -> None:
        nonlocal count
        count += 1
        if count > max_entries:
            raise UploadPlanError(f"Upload entry safety limit exceeded at {path.as_posix()}")
        if depth > max_depth:
            raise UploadPlanError(f"Upload depth safety limit exceeded at {path.as_posix()}")
        try:
            entry = await source.inspect_upload_entry(path)
        except UnsupportedSourceError as exc:
            if depth == 0:
                raise UploadPlanError("Upload source root changed during planning") from exc
            excluded.append(UploadExclusion(path, str(exc)))
            return
        if depth == 0 and entry != root:
            raise UploadPlanError("Upload source root changed during planning")
        if entry.kind == EntryKind.SYMLINK:
            excluded.append(UploadExclusion(path, "Symlink or reparse point; not followed"))
            return
        if entry.kind == EntryKind.DIRECTORY:
            children = await source.list_upload_directory(path)
            names: set[str] = set()
            for child in children:
                name = child.name
                if (
                    not isinstance(name, str)
                    or name in {"", ".", ".."}
                    or "/" in name
                    or "\x00" in name
                ):
                    raise UploadPlanError(f"Ambiguous upload entry name at {path.as_posix()}")
                if name in names:
                    raise UploadPlanError(f"Duplicate upload entry name at {path.as_posix()}")
                names.add(name)
            if not children:
                empty.append(path)
            for child in children:
                await walk(PathRef((*path.segments, child.name)), depth + 1)
            if (await source.inspect_upload_entry(path)) != entry:
                raise UploadPlanError(f"Directory changed during planning: {path.as_posix()}")
            return
        _validate_file_snapshot(path, entry)
        files.append((path, entry))

    try:
        await walk(source_root, 0)
    except UploadPlanError:
        raise
    except ProviderError as exc:
        raise UploadPlanError(f"Upload source changed or is unavailable: {exc}") from exc

    objects = await _map_uploads(source_root, destination_prefix, files, destination, policy)
    return UploadPlan(
        source_root,
        destination_prefix,
        policy,
        objects,
        tuple(excluded),
        tuple(empty),
        root,
        source,
        destination,
    )


async def _map_uploads(
    source_root: PathRef,
    destination_prefix: PathRef,
    files: list[tuple[PathRef, FileEntry]],
    destination: UploadDestination,
    policy: ConflictResolution,
    reserved_paths: tuple[PathRef, ...] = (),
) -> tuple[UploadObject, ...]:
    mapped: list[tuple[PathRef, PathRef, FileEntry]] = []
    reserved = {_location(destination, path, file=True) for path in reserved_paths}
    mapped_locations: set[tuple[str, str]] = set()
    for src, observed in files:
        relative = src.segments[len(source_root.segments) :]
        dst = PathRef((*destination_prefix.segments, *relative))
        identity = _location(destination, dst, file=True)
        if identity in mapped_locations:
            raise UploadPlanError(f"Duplicate upload destination key: {dst.as_posix()}")
        reserved.add(identity)
        mapped_locations.add(identity)
        mapped.append((src, dst, observed))

    objects: list[UploadObject] = []
    for src, requested, observed in mapped:
        dst = requested
        existing = await destination.inspect_upload_destination(dst)
        conflict = existing is not None
        if existing is not None and existing.kind != EntryKind.FILE:
            raise UploadPlanError(f"Ambiguous upload destination: {dst.as_posix()}")
        if existing is not None and policy == ConflictResolution.OVERWRITE and not existing.etag:
            raise UploadPlanError(f"Upload destination has no reviewed revision: {dst.as_posix()}")
        if conflict and policy == ConflictResolution.RENAME:
            for attempt in range(1, 1001):
                candidate = renamed_upload_path(requested, attempt)
                identity = _location(destination, candidate, file=True)
                if identity in reserved:
                    continue
                if await destination.inspect_upload_destination(candidate) is None:
                    dst, existing = candidate, None
                    reserved.add(identity)
                    break
            else:
                raise UploadPlanError(
                    f"No available upload destination name: {requested.as_posix()}"
                )
        objects.append(
            UploadObject(
                src,
                requested,
                dst,
                observed,
                existing,
                conflict,
                _location(destination, dst, file=True),
            )
        )
    return tuple(objects)


async def replan_upload_objects(
    plan: UploadPlan, sources: tuple[PathRef, ...], *, reserved_paths: tuple[PathRef, ...] = ()
) -> UploadPlan:
    """Fresh metadata for only a previously planned subset, never a new tree walk."""
    permitted = {row.source for row in plan.objects}
    if len(set(sources)) != len(sources) or not set(sources) <= permitted:
        raise UploadPlanError("Retry must name a unique subset of previously planned objects")
    root = await plan.source.inspect_upload_entry(plan.source_root)
    if root.kind != EntryKind.DIRECTORY:
        raise UploadPlanError("Upload source root is no longer a directory")
    files = []
    for path in sources:
        entry = await plan.source.inspect_upload_entry(path)
        _validate_file_snapshot(path, entry)
        files.append((path, entry))
    reserved_paths = tuple(
        dict.fromkeys(
            (
                *plan.reserved_paths,
                *reserved_paths,
                *(row.requested_destination for row in plan.objects),
            )
        )
    )
    objects = await _map_uploads(
        plan.source_root,
        plan.destination_prefix,
        files,
        plan.destination,
        plan.policy,
        reserved_paths,
    )
    return UploadPlan(
        plan.source_root,
        plan.destination_prefix,
        plan.policy,
        objects,
        (),
        (),
        root,
        plan.source,
        plan.destination,
        reserved_paths,
    )
