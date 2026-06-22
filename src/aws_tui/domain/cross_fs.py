"""Cross-provider stream copy/move.

A :class:`CrossFsCopy` reads from any :class:`FileSystemProvider` and
writes to any other (or the same) provider. The two roles are
symmetric: the same code path runs for local↔local, local↔s3, s3↔s3,
and any future pair.

For directories, the copy is recursive. Conflict resolution is
configurable per call:

- ``ERROR``: raise :class:`ConflictError` if the destination exists.
- ``OVERWRITE``: replace whatever is at the destination.
- ``SKIP``: no-op if the destination exists.
- ``RENAME``: append ``" (1)"``, ``" (2)"``, ... to the leaf name until
  a free slot is found.
"""

from __future__ import annotations

import contextlib
from enum import StrEnum
from typing import Final

from aws_tui.domain.filesystem import (
    ConflictError,
    EntryKind,
    FileSystemProvider,
    NotFoundError,
    PathRef,
    ProgressCallback,
)

#: Maximum ``" (N)"`` suffixes the ``RENAME`` conflict resolver will try
#: before giving up. 1000 is plenty for any plausible user-driven copy
#: workflow and bounds the loop in pathological mass-collision cases.
_MAX_RENAME_ATTEMPTS: Final[int] = 1000


class ConflictResolution(StrEnum):
    ERROR = "error"
    OVERWRITE = "overwrite"
    SKIP = "skip"
    RENAME = "rename"


class CrossFsCopy:
    """Streaming copy between two providers (possibly the same one)."""

    def __init__(
        self,
        *,
        source: FileSystemProvider,
        destination: FileSystemProvider,
    ) -> None:
        self._source = source
        self._destination = destination

    async def copy(
        self,
        src: PathRef,
        dst: PathRef,
        *,
        progress: ProgressCallback | None = None,
        on_conflict: ConflictResolution = ConflictResolution.ERROR,
    ) -> None:
        """Copy ``src`` to ``dst``. Recurses if ``src`` is a directory.

        Notes
        -----
        - ``progress`` is forwarded to the destination's ``write_stream``
          for *each* file copied (not aggregated across a recursive
          subtree — that aggregation is the caller's responsibility).
        - ``on_conflict`` is consulted *before* opening the source stream
          so we don't waste bandwidth.
        """
        src_entry = await self._source.stat(src)
        if src_entry.kind == EntryKind.DIRECTORY:
            await self._copy_directory(src, dst, progress=progress, on_conflict=on_conflict)
            return

        effective_dst = await self._resolve_conflict(dst, on_conflict)
        if effective_dst is None:
            return  # SKIP

        stream = await self._source.read_stream(src)
        try:
            await self._destination.write_stream(
                effective_dst,
                stream,
                total_size=src_entry.size,
                progress=progress,
            )
        finally:
            # Explicit aclose if the stream is an async generator —
            # write_stream may have raised before/after fully iterating
            # it, and Python's GC-based aclose can race with event-loop
            # shutdown. Belt-and-suspenders: deterministic close here,
            # GC as fallback for non-generator iterables.
            aclose = getattr(stream, "aclose", None)
            if callable(aclose):
                with contextlib.suppress(Exception):
                    await aclose()

    async def _copy_directory(
        self,
        src: PathRef,
        dst: PathRef,
        *,
        progress: ProgressCallback | None,
        on_conflict: ConflictResolution,
    ) -> None:
        # Materialize the destination directory. ConflictResolution applies
        # only to the directory's contents — the directory itself is
        # created lazily.
        try:
            dst_entry = await self._destination.stat(dst)
        except NotFoundError:
            await self._destination.mkdir(dst)
        else:
            if dst_entry.kind != EntryKind.DIRECTORY:
                if on_conflict == ConflictResolution.ERROR:
                    raise ConflictError(dst.as_posix())
                if on_conflict == ConflictResolution.SKIP:
                    return
                # OVERWRITE/RENAME on a file destination: delete and remake.
                await self._destination.delete(dst)
                await self._destination.mkdir(dst)

        for child in await self._source.list(src):
            await self.copy(
                src.join(child.name),
                dst.join(child.name),
                progress=progress,
                on_conflict=on_conflict,
            )

    async def _resolve_conflict(
        self,
        dst: PathRef,
        on_conflict: ConflictResolution,
    ) -> PathRef | None:
        """Decide what to do when ``dst`` may already exist.

        Returns the effective destination, or ``None`` to skip.
        """
        try:
            await self._destination.stat(dst)
        except NotFoundError:
            return dst
        # Destination exists.
        if on_conflict == ConflictResolution.ERROR:
            raise ConflictError(dst.as_posix())
        if on_conflict == ConflictResolution.SKIP:
            return None
        if on_conflict == ConflictResolution.OVERWRITE:
            return dst
        # RENAME: try " (1)", " (2)", ... up to the safety bound.
        for i in range(1, _MAX_RENAME_ATTEMPTS + 1):
            candidate = dst.with_name(self._rename(dst.name, i))
            try:
                await self._destination.stat(candidate)
            except NotFoundError:
                return candidate
        raise ConflictError(f"could not rename to a free slot: {dst.as_posix()}")

    @staticmethod
    def _rename(name: str, idx: int) -> str:
        """Append ``" (idx)"`` to a filename, preserving the extension."""
        if "." in name and not name.startswith("."):
            stem, _, ext = name.rpartition(".")
            return f"{stem} ({idx}).{ext}"
        return f"{name} ({idx})"


class CrossFsMove(CrossFsCopy):
    """Copy + delete source. The source is only deleted after the copy succeeds."""

    async def move(
        self,
        src: PathRef,
        dst: PathRef,
        *,
        progress: ProgressCallback | None = None,
        on_conflict: ConflictResolution = ConflictResolution.ERROR,
    ) -> None:
        await self.copy(src, dst, progress=progress, on_conflict=on_conflict)
        await self._source.delete(src)


__all__ = ["ConflictResolution", "CrossFsCopy", "CrossFsMove"]
