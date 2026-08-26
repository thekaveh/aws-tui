"""Dict-backed FileSystemProvider — production-grade in-memory fake.

Lives in ``src/aws_tui/demo/`` so it's reachable from both demo mode
(``AwsTuiApp`` consumes it when ``AWS_TUI_DEMO=1``) AND the
test suite (via a re-export shim at
``tests/unit/domain/_in_memory_fs.py``).

Conforms to :class:`aws_tui.domain.filesystem.FileSystemProvider`.
Async methods sleep ~50 ms at entry to surface the UI's loading
placeholders during demo runs.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime

from aws_tui.domain.filesystem import (
    ConflictError,
    EntryKind,
    FileEntry,
    NotFoundError,
    PathRef,
    ProgressCallback,
    ProviderError,
    StageManifestEntry,
    TransferProgress,
)

# Internal node: bytes = file content, None = directory.
_Node = bytes | None

# Per the demo-mode spec, async methods sleep briefly at entry so
# the UI's ``loading…`` placeholders appear. Without this the fake
# responds instantaneously and the demo reads as broken in a
# different way (no spinner, then sudden full render).
_DEMO_LATENCY_SEC: float = 0.05


class InMemoryFS:
    """A FileSystemProvider that lives entirely in a dict.

    The root path (empty tuple) is implicitly a directory.
    """

    atomic_write_replaces = True

    def __init__(self) -> None:
        self._tree: dict[PathRef, _Node] = {PathRef(()): None}
        self._mtime: dict[PathRef, datetime] = {PathRef(()): datetime.now(UTC)}
        self._generation = 0
        self._revision: dict[PathRef, int] = {PathRef(()): self._next_revision()}

    def _next_revision(self) -> int:
        self._generation += 1
        return self._generation

    def _touch(self, path: PathRef) -> None:
        self._mtime[path] = datetime.now(UTC)
        self._revision[path] = self._next_revision()

    @property
    def storage_identity(self) -> tuple[str, int]:
        return ("memory", id(self))

    # ------------------------------------------------------------------
    # Read paths
    # ------------------------------------------------------------------

    async def list(self, path: PathRef) -> list[FileEntry]:
        await asyncio.sleep(_DEMO_LATENCY_SEC)
        if path not in self._tree:
            raise NotFoundError(path.as_posix())
        if self._tree[path] is not None:
            raise ConflictError(f"not a directory: {path.as_posix()}")
        depth = len(path.segments)
        entries: list[FileEntry] = []
        for candidate, node in self._tree.items():
            if candidate == path:
                continue
            if len(candidate.segments) != depth + 1:
                continue
            if candidate.segments[:depth] != path.segments:
                continue
            entries.append(self._entry_for(candidate, node))
        entries.sort(key=lambda e: (e.kind != EntryKind.DIRECTORY, e.name))
        return entries

    async def stat(self, path: PathRef) -> FileEntry:
        await asyncio.sleep(_DEMO_LATENCY_SEC)
        if path not in self._tree:
            raise NotFoundError(path.as_posix())
        return self._entry_for(path, self._tree[path])

    def _entry_for(self, path: PathRef, node: _Node) -> FileEntry:
        revision = self._revision.get(path)
        if revision is None:
            revision = self._next_revision()
            self._revision[path] = revision
        return FileEntry(
            name=path.name if path.segments else "",
            kind=EntryKind.DIRECTORY if node is None else EntryKind.FILE,
            size=None if node is None else len(node),
            modified=self._mtime.get(path),
            etag=str(revision),
        )

    # ------------------------------------------------------------------
    # Mutating paths
    # ------------------------------------------------------------------

    async def mkdir(self, path: PathRef) -> None:
        await asyncio.sleep(_DEMO_LATENCY_SEC)
        if path.is_root:
            return
        # Ensure parents exist (parents=True semantics).
        for i in range(1, len(path.segments)):
            ancestor = PathRef(path.segments[:i])
            if ancestor not in self._tree:
                self._tree[ancestor] = None
                self._touch(ancestor)
            elif self._tree[ancestor] is not None:
                raise ConflictError(f"path component is a file: {ancestor.as_posix()}")
        existing = self._tree.get(path)
        if existing is not None:
            raise ConflictError(f"file exists at: {path.as_posix()}")
        self._tree[path] = None
        self._touch(path)

    async def claim_directory(self, path: PathRef) -> str:
        await asyncio.sleep(_DEMO_LATENCY_SEC)
        if path.is_root or path in self._tree:
            raise ConflictError(path.as_posix())
        parent = path.parent()
        if parent not in self._tree:
            raise NotFoundError(f"parent missing: {parent.as_posix()}")
        if self._tree[parent] is not None:
            raise ConflictError(f"parent is a file: {parent.as_posix()}")
        self._tree[path] = None
        self._touch(path)
        return str(self._revision[path])

    async def delete(self, path: PathRef, *, expected_etag: str | None = None) -> None:
        await asyncio.sleep(_DEMO_LATENCY_SEC)
        if path not in self._tree:
            raise NotFoundError(path.as_posix())
        if path.is_root:
            raise ConflictError("cannot delete root")
        if expected_etag is not None and str(self._revision[path]) != expected_etag:
            raise ConflictError(f"source changed: {path.as_posix()}")
        node = self._tree[path]
        if node is None:
            # Recursive delete of a directory subtree.
            to_remove = [
                p
                for p in self._tree
                if p == path
                or (
                    len(p.segments) > len(path.segments)
                    and p.segments[: len(path.segments)] == path.segments
                )
            ]
            for p in to_remove:
                self._tree.pop(p, None)
                self._mtime.pop(p, None)
                self._revision.pop(p, None)
        else:
            self._tree.pop(path, None)
            self._mtime.pop(path, None)
            self._revision.pop(path, None)

    async def delete_empty_directory(self, path: PathRef) -> None:
        await asyncio.sleep(_DEMO_LATENCY_SEC)
        if path not in self._tree:
            raise NotFoundError(path.as_posix())
        if path.is_root:
            raise ConflictError("cannot delete root")
        if self._tree[path] is not None:
            raise ConflictError(f"not a directory: {path.as_posix()}")
        prefix = path.segments
        if any(
            candidate != path and candidate.segments[: len(prefix)] == prefix
            for candidate in self._tree
        ):
            raise ConflictError(f"directory is not empty: {path.as_posix()}")
        self._tree.pop(path)
        self._mtime.pop(path, None)
        self._revision.pop(path, None)

    async def rename(self, src: PathRef, dst: PathRef) -> None:
        await asyncio.sleep(_DEMO_LATENCY_SEC)
        self._publish_no_replace(src, dst)

    @staticmethod
    def supports_atomic_publish(kind: EntryKind) -> bool:
        del kind
        return True

    async def capture_stage_revision(self, path: PathRef) -> str:
        await asyncio.sleep(_DEMO_LATENCY_SEC)
        if path not in self._tree:
            raise NotFoundError(path.as_posix())
        return str(self._revision[path])

    async def atomic_publish_no_replace(
        self,
        staged: PathRef,
        destination: PathRef,
        *,
        expected_source_revision: str,
    ) -> str:
        await asyncio.sleep(_DEMO_LATENCY_SEC)
        if staged not in self._tree:
            raise NotFoundError(staged.as_posix())
        if str(self._revision[staged]) != expected_source_revision:
            raise ConflictError(f"stage changed: {staged.as_posix()}")
        self._publish_no_replace(staged, destination)
        return str(self._revision[destination])

    async def atomic_publish_directory_no_replace(
        self,
        staged: PathRef,
        destination: PathRef,
        *,
        expected_manifest: tuple[StageManifestEntry, ...],
    ) -> str:
        await asyncio.sleep(_DEMO_LATENCY_SEC)
        prefix = staged.segments
        observed = {
            StageManifestEntry(
                PathRef(path.segments[len(prefix) :]),
                EntryKind.DIRECTORY if node is None else EntryKind.FILE,
                str(self._revision[path]),
            )
            for path, node in self._tree.items()
            if path == staged or path.segments[: len(prefix)] == prefix
        }
        if observed != set(expected_manifest):
            raise ProviderError(f"stage manifest changed: {staged.as_posix()}")
        self._publish_no_replace(staged, destination)
        return str(self._revision[destination])

    def _publish_no_replace(self, src: PathRef, dst: PathRef) -> None:
        if src not in self._tree:
            raise NotFoundError(src.as_posix())
        if dst in self._tree:
            raise ConflictError(f"destination exists: {dst.as_posix()}")
        # Ensure dst's parent exists.
        parent = dst.parent()
        if not parent.is_root and parent not in self._tree:
            raise NotFoundError(f"parent missing: {parent.as_posix()}")
        if not parent.is_root and self._tree[parent] is not None:
            raise ConflictError(f"parent is not a directory: {parent.as_posix()}")
        # Recursive move: rewrite every key prefixed by src.
        affected = [
            p
            for p in list(self._tree)
            if p == src
            or (
                len(p.segments) > len(src.segments)
                and p.segments[: len(src.segments)] == src.segments
            )
        ]
        for p in affected:
            new_segments = dst.segments + p.segments[len(src.segments) :]
            new_path = PathRef(new_segments)
            self._tree[new_path] = self._tree.pop(p)
            mtime = self._mtime.pop(p, None)
            if mtime is not None:
                self._mtime[new_path] = mtime
            revision = self._revision.pop(p, None)
            if revision is not None:
                self._revision[new_path] = revision

    async def preflight_move_revision(self, path: PathRef, revision: str | None) -> None:
        await asyncio.sleep(_DEMO_LATENCY_SEC)
        if revision is None:
            raise ProviderError(f"move source has no revision: {path.as_posix()}")
        if path not in self._tree:
            raise NotFoundError(path.as_posix())
        if str(self._revision[path]) != revision:
            raise ConflictError(f"source changed: {path.as_posix()}")

    # ------------------------------------------------------------------
    # Streaming I/O
    # ------------------------------------------------------------------

    async def read_stream(
        self, path: PathRef, *, chunk_size: int = 8 * 1024 * 1024
    ) -> AsyncIterator[bytes]:
        await asyncio.sleep(_DEMO_LATENCY_SEC)
        if path not in self._tree:
            raise NotFoundError(path.as_posix())
        node = self._tree[path]
        if node is None:
            raise ConflictError(f"is a directory: {path.as_posix()}")
        return self._chunked(node, chunk_size)

    @staticmethod
    async def _chunked(data: bytes, chunk_size: int) -> AsyncIterator[bytes]:
        for i in range(0, len(data), chunk_size):
            yield data[i : i + chunk_size]

    async def write_stream(
        self,
        path: PathRef,
        source: AsyncIterator[bytes],
        *,
        total_size: int | None = None,
        progress: ProgressCallback | None = None,
        overwrite: bool = True,
    ) -> None:
        await asyncio.sleep(_DEMO_LATENCY_SEC)
        if path.is_root:
            raise ConflictError("cannot write to root")
        parent = path.parent()
        if not parent.is_root and parent not in self._tree:
            raise NotFoundError(f"parent missing: {parent.as_posix()}")
        if parent in self._tree and self._tree[parent] is not None:
            raise ConflictError(f"parent is a file: {parent.as_posix()}")
        if path in self._tree and self._tree[path] is None:
            raise ConflictError(f"is a directory: {path.as_posix()}")
        if not overwrite and path in self._tree:
            raise ConflictError(path.as_posix())

        buf = bytearray()
        bytes_written = 0
        async for chunk in source:
            buf.extend(chunk)
            bytes_written += len(chunk)
            if progress is not None:
                progress(TransferProgress(bytes_transferred=bytes_written, bytes_total=total_size))
        self._tree[path] = bytes(buf)
        self._touch(path)


__all__ = ["InMemoryFS"]
