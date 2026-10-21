"""Operation-local S3 publication identity and nontransactional outcome."""

from __future__ import annotations

from dataclasses import dataclass

from aws_tui.domain.filesystem import PathRef, ProviderError


class UploadPublicationUnknownError(ProviderError):
    """A final write may have committed; automatic replay is unsafe."""


@dataclass(slots=True)
class UploadPublication:
    started: bool = False
    attempted: bool = False
    published: bool = False
    uncertain: bool = False
    version_id: str | None = None
    cleanup_error: str | None = None


@dataclass(frozen=True, slots=True)
class S3UploadWrite:
    destination: PathRef
    bucket: str
    key: str
    if_match: str | None
    publication: UploadPublication
