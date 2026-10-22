"""Deterministic, read-only local-to-S3 change plans over frozen snapshots."""

from __future__ import annotations

import fnmatch
import shlex
from bisect import bisect_left
from dataclasses import dataclass, replace
from datetime import datetime
from enum import StrEnum
from typing import Protocol, cast

from aws_tui.domain.cross_fs import ConflictResolution
from aws_tui.domain.filesystem import (
    ConflictError,
    EntryKind,
    FileEntry,
    NotFoundError,
    PathRef,
    ProviderError,
    UnsupportedSourceError,
)
from aws_tui.domain.recursive_upload import (
    UploadDestination,
    UploadExclusion,
    UploadObject,
    UploadPlan,
    UploadPlanError,
    UploadSource,
    UploadSourceChangedError,
    UploadTree,
    checked_upload_location,
    inspect_upload_tree,
    validate_upload_file_snapshot,
)
from aws_tui.domain.sync_checksum import SyncChecksum as SyncChecksum
from aws_tui.domain.sync_checksum import check_sync_cancellation, checksum_source


class SyncComparison(StrEnum):
    CHECKSUM = "checksum"
    SIZE_TIME = "size_time"


class SyncLabel(StrEnum):
    CREATE = "create"
    UPDATE = "update"
    UNCHANGED = "unchanged"
    CONFLICT = "conflict"
    UNSUPPORTED = "unsupported"


def _patterns(patterns: tuple[str, ...]) -> tuple[str, ...]:
    if type(patterns) is not tuple or len(patterns) > 64:
        raise UploadPlanError("Sync filters support at most 64 patterns per field")
    for pattern in patterns:
        if (
            not isinstance(pattern, str)
            or not pattern
            or len(pattern) > 1024
            or pattern.startswith("/")
            or "\x00" in pattern
            or any(part in {"", ".", ".."} for part in pattern.split("/"))
        ):
            raise UploadPlanError("Use bounded relative sync globs without traversal components")
    return tuple(sorted(set(patterns)))


def parse_sync_patterns(text: str) -> tuple[str, ...]:
    """Whitespace-separated globs; quote a pattern containing spaces."""
    if not isinstance(text, str) or len(text) > 65_536:
        raise UploadPlanError("Sync filter input is too large")
    try:
        patterns = tuple(shlex.split(text))
    except ValueError as exc:
        raise UploadPlanError("Close the quoted sync filter pattern before replanning") from exc
    _patterns(patterns)
    return patterns


@dataclass(frozen=True, slots=True)
class SyncSettings:
    comparison: SyncComparison = SyncComparison.CHECKSUM
    include: tuple[str, ...] = ("*",)
    exclude: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.comparison, SyncComparison):
            raise UploadPlanError("Unknown sync comparison mode")
        object.__setattr__(self, "include", _patterns(self.include) or ("*",))
        object.__setattr__(self, "exclude", _patterns(self.exclude))

    def includes(self, relative: str) -> bool:
        return any(fnmatch.fnmatchcase(relative, pattern) for pattern in self.include) and not any(
            fnmatch.fnmatchcase(relative, pattern) for pattern in self.exclude
        )


_DEFAULT_SETTINGS = SyncSettings()


@dataclass(frozen=True, slots=True)
class SyncRow:
    relative: str
    source: PathRef
    destination: PathRef
    label: SyncLabel
    reason: str
    evidence: str
    item: UploadObject | None

    @property
    def known_bytes(self) -> int | None:
        return self.item.source_entry.size if self.item is not None else None


@dataclass(frozen=True, slots=True)
class SyncPlan:
    rows: tuple[SyncRow, ...]
    execution: UploadPlan
    settings: SyncSettings
    omitted: tuple[PathRef, ...] = ()

    @property
    def operations(self) -> tuple[UploadObject, ...]:
        return self.execution.objects


class _Destination(UploadDestination, Protocol):
    async def read_sync_checksum(self, path: PathRef, *, expected: FileEntry) -> SyncChecksum: ...


class _CheckedInspection:
    """Fence each source-walk await without changing ordinary upload behavior."""

    def __init__(self, source: UploadSource):
        self.source = source

    async def inspect_upload_entry(self, path: PathRef) -> FileEntry:
        check_sync_cancellation()
        result = await self.source.inspect_upload_entry(path)
        check_sync_cancellation()
        return result

    async def list_upload_directory(self, path: PathRef) -> list[FileEntry]:
        check_sync_cancellation()
        result = await self.source.list_upload_directory(path)
        check_sync_cancellation()
        return result


def _safe_root(root: PathRef) -> None:
    if any(
        not isinstance(segment, str)
        or segment in {"", ".", ".."}
        or "/" in segment
        or "\x00" in segment
        for segment in root.segments
    ):
        raise UploadPlanError("Sync source requires an unambiguous local path")


async def _compare(
    source: UploadSource, destination: UploadDestination, item: UploadObject, settings: SyncSettings
) -> tuple[SyncLabel, str, str]:
    old = item.destination_entry
    if not callable(getattr(destination, "write_upload_stream", None)):
        return SyncLabel.UNSUPPORTED, "Destination lacks conditional upload publication", ""
    if old is None:
        return (
            SyncLabel.CREATE,
            "Destination key is absent; conditional creation required",
            "absence",
        )
    if (
        old.kind != EntryKind.FILE
        or not isinstance(old.etag, str)
        or not old.etag.strip()
        or type(old.size) is not int
        or old.size < 0
    ):
        return SyncLabel.UNSUPPORTED, "Destination has no safe regular-file revision/size", ""
    if old.size != item.source_entry.size:
        return SyncLabel.UPDATE, "Source and destination sizes differ", "size differs"
    if settings.comparison == SyncComparison.SIZE_TIME:
        local_time, remote_time = item.source_entry.modified, old.modified
        if any(
            not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None
            for value in (local_time, remote_time)
        ):
            return (
                SyncLabel.UNSUPPORTED,
                "Size/time comparison lacks modification time",
                "heuristic",
            )
        # S3 LastModified is second precision; this cannot prove byte equality.
        assert isinstance(local_time, datetime)
        assert isinstance(remote_time, datetime)
        try:
            same = int(local_time.timestamp()) <= int(remote_time.timestamp())
        except (ValueError, OverflowError, OSError):
            return SyncLabel.UNSUPPORTED, "Invalid size/time comparison timestamp", "heuristic"
        return (
            SyncLabel.UNCHANGED if same else SyncLabel.UPDATE,
            "Same size and source not newer; same-size retained-time edits may be missed"
            if same
            else "Source is newer than destination",
            "size/time heuristic (whole seconds; not content verification)",
        )
    if not callable(getattr(destination, "read_sync_checksum", None)):
        return SyncLabel.UNSUPPORTED, "Destination lacks checked full-object checksum reads", ""
    local = await checksum_source(source, item.source, item.source_entry)
    remote = await cast(_Destination, destination).read_sync_checksum(
        item.destination, expected=old
    )
    check_sync_cancellation()
    if (
        not isinstance(remote, SyncChecksum)
        or remote.revision != old.etag
        or remote.size != old.size
        or remote.algorithm != local.algorithm
        or remote.kind != local.kind
    ):
        raise UploadPlanError("Destination checksum is not compatible with the reviewed snapshot")
    current_source = await source.inspect_upload_entry(item.source)
    check_sync_cancellation()
    current_destination = await destination.inspect_upload_destination(item.destination)
    check_sync_cancellation()
    if current_source != item.source_entry or current_destination != old:
        raise ConflictError("Sync source or destination changed during comparison; replan")
    same = local.digest == remote.digest
    return (
        SyncLabel.UNCHANGED if same else SyncLabel.UPDATE,
        "Verified matching full-object content"
        if same
        else "Verified differing full-object content",
        "SHA256 FULL_OBJECT",
    )


async def plan_sync(
    source: UploadSource,
    destination: UploadDestination,
    source_root: PathRef,
    destination_prefix: PathRef,
    *,
    settings: SyncSettings = _DEFAULT_SETTINGS,
) -> SyncPlan:
    """Read current evidence; executable operations are only creates/updates."""
    _validate_sync_request(source, destination, source_root, destination_prefix, settings)
    tree = await inspect_upload_tree(_CheckedInspection(source), source_root)
    return await _build_sync_plan(
        source, destination, source_root, destination_prefix, settings, tree
    )


def _validate_sync_request(
    source: UploadSource,
    destination: UploadDestination,
    source_root: PathRef,
    destination_prefix: PathRef,
    settings: SyncSettings,
) -> None:
    if not isinstance(settings, SyncSettings):
        raise UploadPlanError("Sync requires validated immutable settings")
    check_sync_cancellation()
    _safe_root(source_root)
    identity = getattr(source, "storage_identity", None)
    if source is cast(object, destination) or (
        identity is not None and identity == getattr(destination, "storage_identity", None)
    ):
        raise UploadPlanError("Sync source and destination overlap the same physical storage")
    if not callable(getattr(destination, "upload_location", None)) or not callable(
        getattr(destination, "inspect_upload_destination", None)
    ):
        raise UploadPlanError("Destination does not support exact sync key observations")
    bucket, prefix = destination.upload_location(destination_prefix)
    if not isinstance(bucket, str) or not bucket or not isinstance(prefix, str):
        raise UploadPlanError("Choose one exact S3 sync destination prefix")


async def _build_sync_plan(
    source: UploadSource,
    destination: UploadDestination,
    source_root: PathRef,
    destination_prefix: PathRef,
    settings: SyncSettings,
    tree: UploadTree,
) -> SyncPlan:
    files = sorted(tree.files, key=lambda pair: pair[0].segments)
    mapped: list[UploadObject] = []
    disclosures = list(tree.excluded)
    blocked: dict[PathRef, ProviderError] = {}
    identities: set[tuple[str, str]] = set()
    for src, entry in files:
        relative = "/".join(src.segments[len(source_root.segments) :])
        if not settings.includes(relative):
            disclosures.append(UploadExclusion(src, "Excluded by sync filter"))
            continue
        dst = PathRef((*destination_prefix.segments, *src.segments[len(source_root.segments) :]))
        location = checked_upload_location(destination, dst)
        if location in identities:
            raise UploadPlanError("Overlapping sync destination mappings")
        identities.add(location)
        # Collect and validate every mapping before any destination read.
        mapped.append(UploadObject(src, dst, dst, entry, None, False, location))
    ordered = sorted(identities)
    for bucket, key in ordered:
        prefix = key + "/"
        index = bisect_left(ordered, (bucket, prefix))
        if (
            index < len(ordered)
            and ordered[index][0] == bucket
            and ordered[index][1].startswith(prefix)
        ):
            raise UploadPlanError("Overlapping object and descendant sync mappings")
    observed: list[UploadObject] = []
    for item in mapped:
        check_sync_cancellation()
        try:
            old = await destination.inspect_upload_destination(item.destination)
            if old is not None and not isinstance(old, FileEntry):
                raise UploadPlanError("Malformed sync destination observation")
            observed.append(replace(item, destination_entry=old, conflict=old is not None))
        except ProviderError as exc:
            blocked[item.source] = exc
            observed.append(item)
        check_sync_cancellation()
    rows: list[SyncRow] = []
    for item in observed:
        check_sync_cancellation()
        try:
            if item.source in blocked:
                raise blocked[item.source]
            label, reason, evidence = await _compare(source, destination, item, settings)
        except (ConflictError, UploadSourceChangedError) as exc:
            label, reason, evidence = SyncLabel.CONFLICT, str(exc), ""
        except ProviderError as exc:
            label, reason, evidence = SyncLabel.UNSUPPORTED, str(exc), ""
        relative = "/".join(item.source.segments[len(source_root.segments) :])
        rows.append(SyncRow(relative, item.source, item.destination, label, reason, evidence, item))
        check_sync_cancellation()
    for exclusion in tree.excluded:
        relative_parts = exclusion.path.segments[len(source_root.segments) :]
        relative = "/".join(relative_parts)
        if settings.includes(relative):
            rows.append(
                SyncRow(
                    relative,
                    exclusion.path,
                    PathRef((*destination_prefix.segments, *relative_parts)),
                    SyncLabel.UNSUPPORTED,
                    exclusion.reason,
                    "",
                    None,
                )
            )
    rows.sort(key=lambda row: row.relative)
    operations = tuple(
        row.item
        for row in rows
        if row.label in {SyncLabel.CREATE, SyncLabel.UPDATE} and row.item is not None
    )
    execution = UploadPlan(
        source_root,
        destination_prefix,
        ConflictResolution.OVERWRITE,
        operations,
        tuple(sorted(disclosures, key=lambda row: row.path.segments)),
        tuple(sorted(tree.empty_directories, key=lambda row: row.segments)),
        tree.root_entry,
        source,
        destination,
    )
    return SyncPlan(tuple(rows), execution, settings)


def select_sync_operations(plan: SyncPlan, *, excluded: tuple[PathRef, ...]) -> SyncPlan:
    candidates = {
        row.source for row in plan.rows if row.label in {SyncLabel.CREATE, SyncLabel.UPDATE}
    }
    if len(set(excluded)) != len(excluded) or not set(excluded) <= candidates:
        raise UploadPlanError("Omit only a unique subset of reviewed sync operations")
    omitted = tuple(sorted(excluded, key=lambda row: row.segments))
    objects = tuple(
        row.item
        for row in plan.rows
        if row.source in candidates and row.source not in omitted and row.item is not None
    )
    return replace(plan, execution=replace(plan.execution, objects=objects), omitted=omitted)


async def replan_sync(plan: SyncPlan, sources: tuple[PathRef, ...]) -> SyncPlan:
    permitted = {item.source for item in plan.operations}
    if len(set(sources)) != len(sources) or not set(sources) <= permitted:
        raise UploadPlanError("Retry only a unique subset of previously selected sync operations")
    old = plan.execution
    _validate_sync_request(
        old.source, old.destination, old.source_root, old.destination_prefix, plan.settings
    )
    inspection = _CheckedInspection(old.source)
    root = await inspection.inspect_upload_entry(old.source_root)
    if root.kind != EntryKind.DIRECTORY:
        raise UploadPlanError("Sync source root is no longer a directory")
    prior = {row.source: row for row in plan.rows}
    files, blocked = [], []
    for path in sorted(sources, key=lambda value: value.segments):
        try:
            entry = await inspection.inspect_upload_entry(path)
            validate_upload_file_snapshot(path, entry)
            files.append((path, entry))
        except ProviderError as exc:
            check_sync_cancellation()
            label = (
                SyncLabel.CONFLICT
                if isinstance(exc, (NotFoundError, UnsupportedSourceError, UploadPlanError))
                else SyncLabel.UNSUPPORTED
            )
            blocked.append(
                replace(
                    prior[path],
                    label=label,
                    reason=f"Cannot replan selected source: {exc}",
                    evidence="",
                )
            )
    fresh = await _build_sync_plan(
        old.source,
        old.destination,
        old.source_root,
        old.destination_prefix,
        plan.settings,
        UploadTree(root, tuple(files), (), ()),
    )
    return replace(fresh, rows=tuple(sorted((*fresh.rows, *blocked), key=lambda row: row.relative)))
