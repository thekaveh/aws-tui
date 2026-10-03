"""Optional read-only S3 details capability, separate from filesystem listings."""

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol, runtime_checkable

from aws_tui.domain.filesystem import PathRef


@dataclass(frozen=True, slots=True)
class S3ObjectDetails:
    """Reported object fields; missing sections remain distinct from empty ones."""

    bucket: str
    key: str
    content_type: str | None = None
    content_encoding: str | None = None
    size: int | None = None
    modified: datetime | None = None
    storage_class: str | None = None
    etag: str | None = None
    version_id: str | None = None
    encryption: tuple[tuple[str, str], ...] | None = None
    metadata: tuple[tuple[str, str], ...] | None = None
    tags: tuple[tuple[str, str], ...] | None = None
    tags_error: str | None = None
    checksums: tuple[tuple[str, str], ...] | None = None
    checksum_type: str | None = None
    checksums_error: str | None = None


@runtime_checkable
class S3ObjectDetailsProvider(Protocol):
    """A provider that can read details on demand without downloading an object."""

    async def read_object_details(self, path: PathRef) -> S3ObjectDetails: ...
