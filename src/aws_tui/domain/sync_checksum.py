"""Full-object checksum evidence bound to an inspected provider revision."""

from __future__ import annotations

import asyncio
import hashlib
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Protocol, cast

from aws_tui.domain.filesystem import FileEntry, PathRef
from aws_tui.domain.recursive_upload import UploadPlanError, UploadSource, UploadSourceChangedError


@dataclass(frozen=True, slots=True)
class SyncChecksum:
    digest: str
    size: int
    revision: str
    algorithm: str = "SHA256"
    kind: str = "FULL_OBJECT"

    def __post_init__(self) -> None:
        if (
            self.algorithm != "SHA256"
            or self.kind != "FULL_OBJECT"
            or not isinstance(self.digest, str)
            or len(self.digest) != 64
            or any(char not in "0123456789abcdef" for char in self.digest)
            or type(self.size) is not int
            or self.size < 0
            or not isinstance(self.revision, str)
            or not self.revision
        ):
            raise UploadPlanError("Sync comparison requires a revision-bound full-object SHA256")


class _Source(UploadSource, Protocol):
    async def read_upload_stream(
        self, path: PathRef, *, expected: FileEntry
    ) -> AsyncIterator[bytes]: ...


def check_sync_cancellation() -> None:
    """A provider swallowing cancellation must not revive an obsolete dry run."""
    task = asyncio.current_task()
    if task is not None and task.cancelling():
        raise asyncio.CancelledError


async def checksum_source(source: UploadSource, path: PathRef, expected: FileEntry) -> SyncChecksum:
    """Consume the checked local stream through EOF and own its final closure."""
    if not expected.etag or type(expected.size) is not int:
        raise UploadPlanError("Sync source has no checksumable reviewed snapshot")
    if not callable(getattr(source, "read_upload_stream", None)):
        raise UploadPlanError("Sync source does not support checked content comparison")
    check_sync_cancellation()
    stream = await cast(_Source, source).read_upload_stream(path, expected=expected)
    close = getattr(stream, "aclose", None)
    if not callable(close):
        raise UploadPlanError("Sync source comparison requires owned stream closure")
    digest, count = hashlib.sha256(), 0
    try:
        check_sync_cancellation()
        async for chunk in stream:
            check_sync_cancellation()
            if not isinstance(chunk, bytes):
                raise UploadPlanError("Sync source returned an invalid byte chunk")
            count += len(chunk)
            if count > expected.size:
                raise UploadSourceChangedError("Sync source exceeded its reviewed size")
            digest.update(chunk)
        if count != expected.size or await source.inspect_upload_entry(path) != expected:
            raise UploadSourceChangedError("Sync source changed during checksum comparison")
        check_sync_cancellation()
    finally:
        closing = asyncio.create_task(close())
        canceled = False
        while not closing.done():
            try:
                await asyncio.shield(closing)
            except asyncio.CancelledError:
                canceled = True
        closing.result()
        if canceled:
            raise asyncio.CancelledError
    return SyncChecksum(digest.hexdigest(), count, expected.etag)
