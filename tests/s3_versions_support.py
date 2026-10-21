"""Controlled historical provider for owned VM and actual application flows."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any

from aws_tui.demo.in_memory_fs import InMemoryFS
from aws_tui.domain.filesystem import EntryKind, FileEntry, PathRef, ProviderError
from aws_tui.domain.s3_object_details import S3ObjectDetails
from aws_tui.domain.s3_versions import (
    BucketVersioning,
    S3ObjectVersion,
    S3RecoveryDestination,
    S3RecoveryMetadata,
    S3RecoveryPublication,
    S3RecoveryWrite,
    S3VersionListing,
)


class ReadGate:
    def __init__(self, *, resistant: bool = False) -> None:
        self.entered = asyncio.Event()
        self.release = asyncio.Event()
        self.cancelled = asyncio.Event()
        self.resistant = resistant

    async def wait(self) -> None:
        self.entered.set()
        try:
            await self.release.wait()
        except asyncio.CancelledError:
            self.cancelled.set()
            if not self.resistant:
                raise
            await self.release.wait()


class VersionsFS(InMemoryFS):
    def __init__(self) -> None:
        super().__init__()
        self.reads: list[tuple[str, Any]] = []
        self.list_gate: ReadGate | None = None
        self.detail_gate: ReadGate | None = None
        self.stream_gate: ReadGate | None = None
        self.publish_gate: ReadGate | None = None
        self.list_error: Exception | None = None
        self.stream_closed = 0

    def version_location(self, path: PathRef) -> tuple[str, str]:
        if not path.segments:
            raise ProviderError("Choose a bucket first")
        return path.segments[0], "/".join(path.segments[1:])

    def canonical_storage_path(self, path: PathRef):
        bucket, key = self.version_location(path)
        return (("s3", "fixture", bucket), PathRef(tuple(key.split("/"))))

    def versions(self, path: PathRef) -> tuple[S3ObjectVersion, ...]:
        bucket, key = self.version_location(path)
        old = S3ObjectVersion(
            path,
            bucket,
            key,
            "old +/=",
            datetime(2026, 10, 21, tzinfo=UTC),
            7,
            False,
            False,
            '"old-etag"',
        )
        return (
            replace(old, version_id="deleted", size=None, is_latest=True, is_delete_marker=True),
            old,
        )

    async def list_versions(self, path: PathRef) -> S3VersionListing:
        self.reads.append(("list", path))
        if self.list_gate is not None:
            await self.list_gate.wait()
        if self.list_error is not None:
            raise self.list_error
        bucket, key = self.version_location(path)
        return S3VersionListing(
            bucket, key, self.versions(path), BucketVersioning.ENABLED, "Bucket versioning enabled."
        )

    async def stat_version(self, version: S3ObjectVersion) -> FileEntry:
        self.reads.append(("stat", version))
        return FileEntry(
            version.path.name, EntryKind.FILE, version.size, version.modified, version.etag
        )

    async def read_version_details(self, version: S3ObjectVersion) -> S3ObjectDetails:
        self.reads.append(("details", version))
        if self.detail_gate is not None:
            await self.detail_gate.wait()
        return S3ObjectDetails(
            bucket=version.bucket,
            key=version.key,
            version_id=version.version_id,
            content_type="text/plain",
            metadata=(("owner", "fixture"),),
        )

    async def read_version_stream(
        self, version: S3ObjectVersion, *, chunk_size: int = 8 * 1024 * 1024
    ) -> AsyncIterator[bytes]:
        self.reads.append(("stream", version))

        async def content():
            try:
                if self.stream_gate is not None:
                    await self.stream_gate.wait()
                yield b"payload"
            finally:
                self.stream_closed += 1

        return content()

    async def recovery_destination(
        self, version: S3ObjectVersion, destination: PathRef
    ) -> S3RecoveryDestination:
        if self.version_location(destination) == (version.bucket, version.key):
            raise ProviderError("Choose a different recovery key")
        self.reads.append(("prepare", version))
        if self.detail_gate is not None:
            await self.detail_gate.wait()
        bucket, key = self.version_location(destination)
        context = S3RecoveryWrite(
            destination,
            bucket,
            key,
            S3RecoveryMetadata((("ContentType", "text/plain"),), (("owner", "fixture"),), ()),
            S3RecoveryPublication(),
        )
        return S3RecoveryDestination(self, context)

    async def write_recovery_stream(self, context, source, *, total_size, progress):
        await self.write_stream(
            context.destination, source, total_size=total_size, progress=progress, overwrite=False
        )
        context.publication.published = True
        context.publication.version_id = "recovered"
        if self.publish_gate is not None:
            await self.publish_gate.wait()
