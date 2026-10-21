"""Immutable exact-version observations, distinct from mutation ownership."""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Protocol, runtime_checkable
from urllib.parse import urlencode

from aws_tui.domain.filesystem import (
    ConflictError,
    FileEntry,
    PathRef,
    ProgressCallback,
    ProviderError,
)
from aws_tui.domain.s3_object_details import S3ObjectDetails


class BucketVersioning(StrEnum):
    ENABLED = "enabled"
    SUSPENDED = "suspended"
    UNVERSIONED = "unversioned"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True, slots=True)
class S3ObjectVersion:
    path: PathRef
    bucket: str
    key: str
    version_id: str
    modified: datetime
    size: int | None
    is_latest: bool
    is_delete_marker: bool
    etag: str | None = None


@dataclass(frozen=True, slots=True)
class S3VersionListing:
    bucket: str
    key: str
    versions: tuple[S3ObjectVersion, ...]
    versioning: BucketVersioning
    notice: str


@dataclass(frozen=True, slots=True)
class S3RecoveryMetadata:
    headers: tuple[tuple[str, str | datetime], ...]
    metadata: tuple[tuple[str, str], ...]
    tags: tuple[tuple[str, str], ...]

    def creation_args(self) -> dict[str, Any]:
        return {
            **dict(self.headers),
            "Metadata": dict(self.metadata),
            "Tagging": urlencode(self.tags),
        }


@dataclass(slots=True)
class S3RecoveryPublication:
    """Request-local outcome survives cancellation flattened by TransferRuntime."""

    published: bool = False
    uncertain: bool = False
    version_id: str | None = None
    cleanup_error: str | None = None


@dataclass(frozen=True, slots=True)
class S3RecoveryWrite:
    destination: PathRef
    bucket: str
    key: str
    metadata: S3RecoveryMetadata
    publication: S3RecoveryPublication


def recovery_metadata(head: dict[str, Any], tagged: dict[str, Any]) -> S3RecoveryMetadata:
    """Only content/user metadata and tags are copied, never source policy."""
    missing = head.get("MissingMeta", 0)
    user = head.get("Metadata")
    if (
        type(missing) is not int
        or missing != 0
        or not isinstance(user, dict)
        or any(not isinstance(k, str) or not isinstance(v, str) for k, v in user.items())
    ):
        raise ProviderError("recovery metadata is incomplete or malformed; no object was written")
    headers: list[tuple[str, str | datetime]] = []
    for field in (
        "ContentType",
        "ContentEncoding",
        "ContentLanguage",
        "ContentDisposition",
        "CacheControl",
        "Expires",
        "WebsiteRedirectLocation",
    ):
        if field not in head:
            continue
        value = head[field]
        if not isinstance(value, datetime if field == "Expires" else str):
            raise ProviderError(f"recovery metadata header {field} is malformed")
        headers.append((field, value))
    raw_tags = tagged.get("TagSet")
    if not isinstance(raw_tags, list):
        raise ProviderError("recovery tags unavailable or malformed; no object was written")
    tags: dict[str, str] = {}
    for tag in raw_tags:
        if (
            not isinstance(tag, dict)
            or not isinstance(tag.get("Key"), str)
            or not isinstance(tag.get("Value"), str)
            or tag["Key"] in tags
        ):
            raise ProviderError("recovery tags malformed or ambiguous; no object was written")
        tags[tag["Key"]] = tag["Value"]
    return S3RecoveryMetadata(tuple(headers), tuple(user.items()), tuple(tags.items()))


@runtime_checkable
class S3VersionProvider(Protocol):
    def version_location(self, path: PathRef) -> tuple[str, str]: ...

    def canonical_storage_path(self, path: PathRef) -> tuple[object, PathRef] | None: ...

    async def list_versions(self, path: PathRef) -> S3VersionListing: ...

    async def stat_version(self, version: S3ObjectVersion) -> FileEntry: ...

    async def read_version_details(self, version: S3ObjectVersion) -> S3ObjectDetails: ...

    async def read_version_stream(
        self, version: S3ObjectVersion, *, chunk_size: int = 8 * 1024 * 1024
    ) -> AsyncIterator[bytes]: ...

    async def recovery_destination(
        self, version: S3ObjectVersion, destination: PathRef
    ) -> S3RecoveryDestination: ...


class S3RecoveryWriter(Protocol):
    def canonical_storage_path(self, path: PathRef) -> tuple[object, PathRef] | None: ...

    async def stat(self, path: PathRef) -> FileEntry: ...

    async def write_recovery_stream(
        self,
        context: S3RecoveryWrite,
        source: AsyncIterator[bytes],
        *,
        total_size: int | None,
        progress: ProgressCallback | None,
    ) -> None: ...


class S3RecoveryDestination:
    """One no-replace writer with immutable metadata; no shared provider options."""

    atomic_write_replaces = True

    def __init__(self, provider: S3RecoveryWriter, context: S3RecoveryWrite) -> None:
        self.provider = provider
        self.context = context

    @property
    def publication(self) -> S3RecoveryPublication:
        return self.context.publication

    def _check(self, path: PathRef) -> None:
        if path != self.context.destination:
            raise ConflictError("recovery writer is bound to one different destination key")

    def canonical_storage_path(self, path: PathRef) -> tuple[object, PathRef] | None:
        self._check(path)
        return self.provider.canonical_storage_path(path)

    async def stat(self, path: PathRef) -> FileEntry:
        self._check(path)
        return await self.provider.stat(path)

    async def write_stream(
        self,
        path: PathRef,
        source: AsyncIterator[bytes],
        *,
        total_size: int | None = None,
        progress: ProgressCallback | None = None,
        overwrite: bool = True,
        stage_claim: Callable[[str], None] | None = None,
    ) -> None:
        self._check(path)
        if overwrite or stage_claim is not None:
            raise ConflictError("historical recovery cannot overwrite or claim a local stage")
        await self.provider.write_recovery_stream(
            self.context, source, total_size=total_size, progress=progress
        )

    async def read_stream(
        self, path: PathRef, *, chunk_size: int = 8 * 1024 * 1024
    ) -> AsyncIterator[bytes]:
        raise ConflictError("recovery endpoint only creates its selected destination")

    async def list(self, path: PathRef) -> list[FileEntry]:
        raise ConflictError("recovery endpoint cannot enumerate directories")

    async def mkdir(self, path: PathRef) -> None:
        raise ConflictError("recovery endpoint cannot create directories")

    async def delete(self, path: PathRef, *, expected_etag: str | None = None) -> None:
        raise ConflictError("recovery endpoint cannot delete objects")

    async def delete_empty_directory(
        self, path: PathRef, *, expected_etag: str | None = None
    ) -> None:
        raise ConflictError("recovery endpoint cannot delete directories")

    async def rename(self, src: PathRef, dst: PathRef) -> None:
        raise ConflictError("recovery endpoint cannot rename objects")

    async def preflight_move_revision(self, path: PathRef, revision: str | None) -> None:
        raise ConflictError("recovery endpoint cannot move objects")


class S3VersionSource:
    """One read-only historical file; never delegates to current-object reads."""

    def __init__(self, provider: S3VersionProvider, version: S3ObjectVersion) -> None:
        self.provider = provider
        self.version = version

    def _check(self, path: PathRef) -> None:
        if path != self.version.path:
            raise ConflictError("historical reader is bound to one exact S3 key")
        if self.version.is_delete_marker:
            raise ConflictError("an S3 delete marker has no file content")

    def canonical_storage_path(self, path: PathRef) -> tuple[object, PathRef] | None:
        self._check(path)
        return self.provider.canonical_storage_path(path)

    async def stat(self, path: PathRef) -> FileEntry:
        self._check(path)
        return await self.provider.stat_version(self.version)

    async def read_stream(
        self, path: PathRef, *, chunk_size: int = 8 * 1024 * 1024
    ) -> AsyncIterator[bytes]:
        self._check(path)
        return await self.provider.read_version_stream(self.version, chunk_size=chunk_size)

    async def list(self, path: PathRef) -> list[FileEntry]:
        raise ConflictError("historical source is one read-only file")

    async def mkdir(self, path: PathRef) -> None:
        raise ConflictError("historical source is read-only")

    async def delete(self, path: PathRef, *, expected_etag: str | None = None) -> None:
        raise ConflictError("historical source is read-only")

    async def delete_empty_directory(
        self, path: PathRef, *, expected_etag: str | None = None
    ) -> None:
        raise ConflictError("historical source is read-only")

    async def rename(self, src: PathRef, dst: PathRef) -> None:
        raise ConflictError("historical source is read-only")

    async def preflight_move_revision(self, path: PathRef, revision: str | None) -> None:
        raise ConflictError("historical source is read-only; moving it is unsupported")

    async def write_stream(
        self,
        path: PathRef,
        source: AsyncIterator[bytes],
        *,
        total_size: int | None = None,
        progress: ProgressCallback | None = None,
        overwrite: bool = True,
        stage_claim: Callable[[str], None] | None = None,
    ) -> None:
        raise ConflictError("historical source is read-only")


def validate_version_identity(
    version: S3ObjectVersion, response: dict[str, Any], *, allow_absent: bool = False
) -> None:
    if "VersionId" not in response and (allow_absent or version.version_id == "null"):
        return
    if response.get("VersionId") != version.version_id:
        raise ConflictError("selected S3 version identity changed; list versions again")


def validate_version_observation(version: S3ObjectVersion, response: dict[str, Any]) -> None:
    """Reject replacement observations, including mutable literal-null versions."""
    validate_version_identity(version, response)
    if response.get("DeleteMarker"):
        raise ConflictError("an S3 delete marker has no file content")
    modified = response.get("LastModified")
    if isinstance(modified, datetime):
        modified = (
            modified.replace(tzinfo=UTC) if modified.tzinfo is None else modified.astimezone(UTC)
        )
    if (
        type(response.get("ContentLength")) is not int
        or response["ContentLength"] != version.size
        or modified != version.modified
        or (version.etag is not None and response.get("ETag") != version.etag)
    ):
        raise ConflictError("selected S3 version observation changed; list versions again")


def version_page(
    response: dict[str, Any], *, path: PathRef, bucket: str, key: str
) -> tuple[tuple[S3ObjectVersion, ...], int]:
    """Keep exact-key rows; a Prefix response alone does not establish identity."""
    versions: list[S3ObjectVersion] = []
    scanned = 0
    for collection, marker in (("Versions", False), ("DeleteMarkers", True)):
        rows = response.get(collection, [])
        if not isinstance(rows, list):
            raise ProviderError("invalid S3 version response: entry collection")
        scanned += len(rows)
        for row in rows:
            if not isinstance(row, dict) or not isinstance(row.get("Key"), str):
                raise ProviderError("invalid S3 version response: key")
            if row["Key"] != key:
                continue
            version_id, modified = row.get("VersionId"), row.get("LastModified")
            latest, size, etag = row.get("IsLatest"), row.get("Size"), row.get("ETag")
            if (
                not isinstance(version_id, str)
                or not version_id
                or not isinstance(modified, datetime)
                or not isinstance(latest, bool)
                or (not marker and (type(size) is not int or size < 0))
                or (etag is not None and not isinstance(etag, str))
            ):
                raise ProviderError("invalid S3 version response: required entry fields")
            modified = (
                modified.replace(tzinfo=UTC)
                if modified.tzinfo is None
                else modified.astimezone(UTC)
            )
            versions.append(
                S3ObjectVersion(
                    path=path,
                    bucket=bucket,
                    key=key,
                    version_id=version_id,
                    modified=modified,
                    size=None if marker else size,
                    is_latest=latest,
                    is_delete_marker=marker,
                    etag=None if marker else etag,
                )
            )
    return tuple(versions), scanned


def versioning_state(response: dict[str, Any]) -> tuple[BucketVersioning, str]:
    state = response.get("Status")
    if state == "Enabled":
        return BucketVersioning.ENABLED, "Bucket versioning enabled."
    if state == "Suspended":
        return (
            BucketVersioning.SUSPENDED,
            "Bucket versioning suspended; retained versions remain readable. "
            "The null version can be replaced.",
        )
    if state is None:
        return (
            BucketVersioning.UNVERSIONED,
            "Bucket versioning never enabled; null identifies the current, replaceable object.",
        )
    raise ProviderError("invalid S3 bucket versioning state response")
