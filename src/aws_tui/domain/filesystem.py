"""Norton-Commander unifier — provider-agnostic filesystem abstraction.

This module defines the :class:`FileSystemProvider` Protocol that the
LocalFS, S3FS, and any future provider must satisfy. Concrete providers
live alongside it (``local_fs.py``, ``s3_fs.py``). The cross-provider
streamer in ``cross_fs.py`` consumes the Protocol from both sides so it
works symmetrically across any pair (local↔local, local↔s3, s3↔s3).

Path semantics (``PathRef``):
- Always forward-slash separated, even on Windows hosts (consumers can
  convert to native via :meth:`PathRef.as_posix`).
- Immutable: methods like :meth:`join` and :meth:`parent` return new
  values; never mutate.
- Empty tuple ``()`` is the root; ``is_root`` returns True for it.

Error taxonomy (per spec §7.2): provider-side failures all surface as
:class:`ProviderError` subclasses so VMs and UIs above can pattern-match
without leaking AWS/OS specifics.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Protocol, runtime_checkable


class EntryKind(StrEnum):
    FILE = "file"
    DIRECTORY = "directory"
    SYMLINK = "symlink"


@dataclass(frozen=True, slots=True)
class FileEntry:
    """One entry in a directory listing or a stat result.

    ``size`` is ``None`` for directories. ``etag`` is populated for S3
    objects and ``None`` elsewhere (LocalFS does not synthesize one).
    """

    name: str
    kind: EntryKind
    size: int | None
    modified: datetime | None
    etag: str | None = None


@dataclass(frozen=True, slots=True)
class PathRef:
    """An immutable, provider-agnostic path.

    Stored as a tuple of segments. The empty tuple denotes the root.
    Always renders with forward slashes.
    """

    segments: tuple[str, ...] = ()

    @property
    def is_root(self) -> bool:
        return len(self.segments) == 0

    def join(self, *parts: str) -> PathRef:
        """Append one or more path components, splitting on '/' if present."""
        extra: list[str] = []
        for part in parts:
            for seg in part.split("/"):
                if seg:
                    extra.append(seg)
        return PathRef(self.segments + tuple(extra))

    def parent(self) -> PathRef:
        if self.is_root:
            return self
        return PathRef(self.segments[:-1])

    @property
    def name(self) -> str:
        """The last segment, or empty string at the root."""
        return self.segments[-1] if self.segments else ""

    def with_name(self, new_name: str) -> PathRef:
        """Return a sibling PathRef with the last segment replaced."""
        if self.is_root:
            return PathRef((new_name,)) if new_name else self
        return PathRef((*self.segments[:-1], new_name))

    def as_posix(self) -> str:
        if self.is_root:
            return "/"
        return "/" + "/".join(self.segments)

    @classmethod
    def from_posix(cls, posix: str) -> PathRef:
        """Parse a posix-style path. Leading/trailing slashes are stripped."""
        segs = tuple(seg for seg in posix.split("/") if seg)
        return cls(segs)

    def __str__(self) -> str:
        return self.as_posix()


@dataclass(frozen=True, slots=True)
class TransferProgress:
    """One progress tick reported by a provider during a transfer."""

    bytes_transferred: int
    bytes_total: int | None
    part_index: int | None = None
    part_count: int | None = None


ProgressCallback = Callable[[TransferProgress], None]


@runtime_checkable
class FileSystemProvider(Protocol):
    """Async filesystem operations every provider must implement.

    All methods are coroutines. Implementations must map underlying
    errors (OSError, botocore ClientError, etc.) to :class:`ProviderError`
    subclasses so callers can handle them uniformly.
    """

    async def list(self, path: PathRef) -> list[FileEntry]: ...

    async def stat(self, path: PathRef) -> FileEntry: ...

    async def mkdir(self, path: PathRef) -> None:
        """Create a directory (or no-op marker for object stores)."""
        ...

    async def delete(self, path: PathRef) -> None:
        """Delete a file or recursively delete a directory."""
        ...

    async def rename(self, src: PathRef, dst: PathRef) -> None:
        """Rename within the same provider. May be implemented as copy+delete."""
        ...

    async def read_stream(
        self, path: PathRef, *, chunk_size: int = 8 * 1024 * 1024
    ) -> AsyncIterator[bytes]:
        """Open ``path`` for reading and yield chunks of at most ``chunk_size`` bytes.

        Implementations typically return an ``async def`` generator, so
        callers ``await`` the method to acquire the iterator and then
        consume it with ``async for``.
        """
        ...

    async def write_stream(
        self,
        path: PathRef,
        source: AsyncIterator[bytes],
        *,
        total_size: int | None = None,
        progress: ProgressCallback | None = None,
    ) -> None:
        """Consume ``source`` and write to ``path``. Overwrites if it exists."""
        ...


# ---------------------------------------------------------------------------
# Error taxonomy
# ---------------------------------------------------------------------------


class ProviderError(Exception):
    """Base class for all provider-surfaced errors. Per spec §7.2."""


class NotFoundError(ProviderError):
    """The requested path does not exist."""


class PermissionDeniedError(ProviderError):
    """Caller is not authorized to perform the operation."""


class ConflictError(ProviderError):
    """The destination already exists (or another invariant clash)."""


class ProviderUnreachableError(ProviderError):
    """The underlying service is unreachable (network, endpoint, DNS)."""


class AuthRequiredError(ProviderError):
    """No usable credentials / expired SSO. UI should suggest
    ``aws sso login --profile X`` or ``$AWS_PROFILE`` setup."""


class ThrottledError(ProviderError):
    """Service throttled (boto ``ThrottlingException``). Callers
    should back off exponentially; user-facing toast is INFO-level."""


class ValidationError(ProviderError):
    """Request failed validation (e.g. boto ``ValidationException``
    on ``StartJobRun``). The boto message is forwarded verbatim."""


__all__ = [
    "AuthRequiredError",
    "ConflictError",
    "EntryKind",
    "FileEntry",
    "FileSystemProvider",
    "NotFoundError",
    "PathRef",
    "PermissionDeniedError",
    "ProgressCallback",
    "ProviderError",
    "ProviderUnreachableError",
    "ThrottledError",
    "TransferProgress",
    "ValidationError",
]
