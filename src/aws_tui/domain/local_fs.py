"""LocalFS — :class:`~.filesystem.FileSystemProvider` over the host filesystem.

Uses ``anyio.Path`` (async wrapper on top of pathlib) for control-plane
operations and ``aiofiles`` for streaming I/O. Blocking work like
``shutil.rmtree`` is offloaded to the threadpool via ``anyio.to_thread``.

Errors from the OS layer are mapped to the :class:`ProviderError`
taxonomy so callers can handle them uniformly across providers.
"""

from __future__ import annotations

import errno
import os
import shutil
import stat
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path

import aiofiles
import anyio
from anyio import Path as AnyioPath

from aws_tui.domain.filesystem import (
    ConflictError,
    EntryKind,
    FileEntry,
    NotFoundError,
    PathRef,
    PermissionDeniedError,
    ProgressCallback,
    ProviderError,
    TransferProgress,
)

# Default streaming chunk size. 8 MiB matches S3 multipart minimum-friendly
# blocks and keeps memory bounded on slow disks.
_DEFAULT_CHUNK_SIZE: int = 8 * 1024 * 1024


class LocalFS:
    """A FileSystemProvider over the host filesystem.

    If ``root`` is provided, all :class:`PathRef` arguments are
    interpreted relative to that root and cannot escape it (no ``..``
    traversal). If ``root`` is ``None``, PathRefs are treated as
    absolute (their ``as_posix()`` form is fed to the OS directly).
    """

    def __init__(self, *, root: Path | None = None) -> None:
        self._root: Path | None = root.resolve() if root is not None else None

    # ------------------------------------------------------------------
    # Path resolution
    # ------------------------------------------------------------------

    def _resolve(self, path: PathRef) -> AnyioPath:
        """Map a PathRef to a concrete anyio.Path on the host.

        When ``root`` was provided the docstring contract promises
        "no ``..`` traversal" — but ``Path.joinpath`` does NOT
        normalize ``..`` and the OS interprets it literally, so a
        ``PathRef(segments=("..", "..", "etc", "passwd"))`` would
        escape the sandbox. Resolve the joined path and verify it
        stays under ``_root``; raise ``ProviderError`` otherwise so
        the violation surfaces typed (rather than silently reading
        outside the sandbox or raising a generic OSError later).
        """
        if self._root is not None:
            joined = self._root.joinpath(*path.segments) if path.segments else self._root
            resolved = joined.resolve()
            if resolved != self._root and not resolved.is_relative_to(self._root):
                raise ProviderError(f"path {path.as_posix()!r} escapes sandbox root {self._root}")
            host = resolved
        else:
            host = Path(path.as_posix())
        return AnyioPath(host)

    # ------------------------------------------------------------------
    # Read paths
    # ------------------------------------------------------------------

    async def list(self, path: PathRef) -> list[FileEntry]:
        host = self._resolve(path)
        try:
            children = [child async for child in host.iterdir()]
        except FileNotFoundError as exc:
            raise NotFoundError(host.as_posix()) from exc
        except PermissionError as exc:
            raise PermissionDeniedError(host.as_posix()) from exc
        except NotADirectoryError as exc:
            raise ConflictError(f"not a directory: {host.as_posix()}") from exc
        except OSError as exc:
            raise _map_os_error(exc, host.as_posix()) from exc

        entries: list[FileEntry] = []
        for child in children:
            try:
                entries.append(await _stat_entry(child))
            except FileNotFoundError:
                # Race: entry removed between iterdir and stat. Skip it.
                continue
        entries.sort(key=lambda e: (e.kind != EntryKind.DIRECTORY, e.name))
        return entries

    async def stat(self, path: PathRef) -> FileEntry:
        host = self._resolve(path)
        try:
            return await _stat_entry(host)
        except FileNotFoundError as exc:
            raise NotFoundError(host.as_posix()) from exc
        except PermissionError as exc:
            raise PermissionDeniedError(host.as_posix()) from exc
        except OSError as exc:
            raise _map_os_error(exc, host.as_posix()) from exc

    # ------------------------------------------------------------------
    # Mutating paths
    # ------------------------------------------------------------------

    async def mkdir(self, path: PathRef) -> None:
        host = self._resolve(path)
        try:
            await host.mkdir(parents=True, exist_ok=True)
        except FileExistsError as exc:
            # exist_ok=True suppresses dir-already-exists, but a file at
            # the same name still raises.
            raise ConflictError(host.as_posix()) from exc
        except PermissionError as exc:
            raise PermissionDeniedError(host.as_posix()) from exc
        except OSError as exc:
            raise _map_os_error(exc, host.as_posix()) from exc

    async def delete(self, path: PathRef) -> None:
        host = self._resolve(path)
        try:
            host_stat = await host.lstat()
        except FileNotFoundError as exc:
            raise NotFoundError(host.as_posix()) from exc
        except PermissionError as exc:
            raise PermissionDeniedError(host.as_posix()) from exc
        except OSError as exc:
            # ELOOP / ENAMETOOLONG / EIO / etc. were leaking unmapped
            # through the lstat probe, violating the FileSystemProvider
            # error-taxonomy contract. The second try-block below
            # already had this catch; add the same here.
            raise _map_os_error(exc, host.as_posix()) from exc

        try:
            if stat.S_ISDIR(host_stat.st_mode) and not stat.S_ISLNK(host_stat.st_mode):
                await anyio.to_thread.run_sync(shutil.rmtree, str(host))
            else:
                await host.unlink()
        except FileNotFoundError as exc:
            raise NotFoundError(host.as_posix()) from exc
        except PermissionError as exc:
            raise PermissionDeniedError(host.as_posix()) from exc
        except OSError as exc:
            raise _map_os_error(exc, host.as_posix()) from exc

    async def rename(self, src: PathRef, dst: PathRef) -> None:
        host_src = self._resolve(src)
        host_dst = self._resolve(dst)
        if await host_dst.exists():
            raise ConflictError(host_dst.as_posix())
        try:
            await host_src.rename(host_dst)
        except FileNotFoundError as exc:
            raise NotFoundError(host_src.as_posix()) from exc
        except PermissionError as exc:
            raise PermissionDeniedError(host_src.as_posix()) from exc
        except OSError as exc:
            raise _map_os_error(exc, host_src.as_posix()) from exc

    # ------------------------------------------------------------------
    # Streaming I/O
    # ------------------------------------------------------------------

    async def read_stream(
        self, path: PathRef, *, chunk_size: int = _DEFAULT_CHUNK_SIZE
    ) -> AsyncIterator[bytes]:
        host = self._resolve(path)
        if not await host.is_file():
            # Surface a precise error before opening, so we don't leak a fd.
            if not await host.exists():
                raise NotFoundError(host.as_posix())
            raise ConflictError(f"not a regular file: {host.as_posix()}")
        return _read_chunks(str(host), chunk_size)

    async def write_stream(
        self,
        path: PathRef,
        source: AsyncIterator[bytes],
        *,
        total_size: int | None = None,
        progress: ProgressCallback | None = None,
    ) -> None:
        host = self._resolve(path)
        try:
            async with aiofiles.open(str(host), "wb") as fh:
                bytes_written = 0
                async for chunk in source:
                    await fh.write(chunk)
                    bytes_written += len(chunk)
                    if progress is not None:
                        progress(
                            TransferProgress(
                                bytes_transferred=bytes_written,
                                bytes_total=total_size,
                            )
                        )
        except FileNotFoundError as exc:
            raise NotFoundError(host.as_posix()) from exc
        except IsADirectoryError as exc:
            raise ConflictError(f"is a directory: {host.as_posix()}") from exc
        except PermissionError as exc:
            raise PermissionDeniedError(host.as_posix()) from exc
        except OSError as exc:
            raise _map_os_error(exc, host.as_posix()) from exc


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


async def _stat_entry(host: AnyioPath) -> FileEntry:
    """Build a FileEntry from a host path (symlink-aware)."""
    lstat = await host.lstat()
    mode = lstat.st_mode
    if stat.S_ISLNK(mode):
        kind = EntryKind.SYMLINK
        size = lstat.st_size
    elif stat.S_ISDIR(mode):
        kind = EntryKind.DIRECTORY
        size = None
    else:
        kind = EntryKind.FILE
        size = lstat.st_size
    return FileEntry(
        name=host.name,
        kind=kind,
        size=size,
        modified=datetime.fromtimestamp(lstat.st_mtime, tz=UTC),
    )


async def _read_chunks(filename: str, chunk_size: int) -> AsyncIterator[bytes]:
    """Async generator yielding ``chunk_size`` blocks from a local file."""
    try:
        async with aiofiles.open(filename, "rb") as fh:
            while True:
                chunk = await fh.read(chunk_size)
                if not chunk:
                    return
                yield chunk
    except FileNotFoundError as exc:
        raise NotFoundError(filename) from exc
    except PermissionError as exc:
        raise PermissionDeniedError(filename) from exc
    except OSError as exc:
        raise _map_os_error(exc, filename) from exc


def _map_os_error(exc: OSError, target: str) -> ProviderError:
    """Fallback mapping for OSErrors not caught more specifically."""
    if exc.errno in {errno.ENOENT}:
        return NotFoundError(target)
    if exc.errno in {errno.EACCES, errno.EPERM}:
        return PermissionDeniedError(target)
    if exc.errno in {errno.EEXIST, errno.ENOTEMPTY, errno.EISDIR, errno.ENOTDIR}:
        return ConflictError(target)
    return ProviderError(f"{os.strerror(exc.errno or 0)}: {target}")


__all__ = ["LocalFS"]
