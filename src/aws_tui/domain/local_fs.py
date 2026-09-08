"""LocalFS — :class:`~.filesystem.FileSystemProvider` over the host filesystem.

Uses ``anyio.Path`` (async wrapper on top of pathlib) for control-plane
operations and ``aiofiles`` for streaming I/O. Blocking work like
``shutil.rmtree`` is offloaded to the threadpool via ``anyio.to_thread``.

Errors from the OS layer are mapped to the :class:`ProviderError`
taxonomy so callers can handle them uniformly across providers.
"""

from __future__ import annotations

import asyncio
import ctypes
import errno
import ntpath
import os
import shutil
import stat
from collections.abc import AsyncGenerator, AsyncIterator, Callable, Iterator, Sequence
from contextlib import contextmanager, suppress
from ctypes import wintypes
from datetime import UTC, datetime
from pathlib import Path, PureWindowsPath
from string import ascii_uppercase
from typing import Any, cast
from uuid import uuid4

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
    StageManifestEntry,
    TransferProgress,
)

# Default streaming chunk size. 8 MiB matches S3 multipart minimum-friendly
# blocks and keeps memory bounded on slow disks.
_DEFAULT_CHUNK_SIZE: int = 8 * 1024 * 1024
_WINDOWS = os.name == "nt"
_O_DIRECTORY = cast(int, os.__dict__.get("O_DIRECTORY", 0))
_STAGE_IDENTITY_PREFIX = "local-stage:v1:"
# PaneVM consumes one complete listing. Bound the provider-side materialization
# until the shared filesystem protocol exposes continuation tokens.
_MAX_LISTING_ENTRIES: int = 10_000


class LocalFS:
    """A FileSystemProvider over the host filesystem.

    If ``root`` is provided, all :class:`PathRef` arguments are
    interpreted relative to that root and cannot escape it (no ``..``
    traversal). If ``root`` is ``None``, PathRefs are treated as
    absolute (their ``as_posix()`` form is fed to the OS directly).
    """

    atomic_write_replaces = False

    def __init__(self, *, root: Path | None = None) -> None:
        self._root: Path | None = root.resolve() if root is not None else None
        self._stage_publish_lock = asyncio.Lock()

    @property
    def storage_identity(self) -> tuple[str, str]:
        if self._root is None and _WINDOWS:
            return ("local", "windows-drives")
        return ("local", str(self._root or Path("/").resolve()))

    def canonical_path(self, path: PathRef) -> Path:
        """Return the physical host path used for cross-provider safety checks."""
        if self._root is not None:
            candidate = self._root.joinpath(*path.segments)
        elif _WINDOWS:
            candidate = Path(_windows_path_ref(path))
        else:
            candidate = Path(path.as_posix())
        return candidate.resolve(strict=False)

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
            host = Path(_windows_path_ref(path)) if _WINDOWS else Path(path.as_posix())
        return AnyioPath(host)

    def _resolve_leaf(self, path: PathRef) -> AnyioPath:
        """Resolve a mutation path without following its final symlink."""
        if any(segment in {".", ".."} for segment in path.segments):
            raise ProviderError(f"path {path.as_posix()!r} contains an unsafe dot segment")
        if self._root is None:
            host = Path(_windows_path_ref(path)) if _WINDOWS else Path(path.as_posix())
            return AnyioPath(host)
        if path.is_root:
            return AnyioPath(self._root)
        joined = self._root.joinpath(*path.segments)
        if _WINDOWS:
            _validate_windows_relative(path)
            return AnyioPath(joined)
        parent = joined.parent.resolve()
        if parent != self._root and not parent.is_relative_to(self._root):
            raise ProviderError(f"path {path.as_posix()!r} escapes sandbox root {self._root}")
        return AnyioPath(parent / joined.name)

    # ------------------------------------------------------------------
    # Read paths
    # ------------------------------------------------------------------

    async def list(self, path: PathRef) -> list[FileEntry]:
        if self._root is None and _WINDOWS and path.is_root:
            return await anyio.to_thread.run_sync(_windows_drive_entries)
        if _WINDOWS:
            try:
                windows_rows = await anyio.to_thread.run_sync(
                    _windows_list,
                    self._root,
                    path,
                )
            except FileNotFoundError as exc:
                raise NotFoundError(path.as_posix()) from exc
            except PermissionError as exc:
                raise PermissionDeniedError(path.as_posix()) from exc
            except NotADirectoryError as exc:
                raise ConflictError(f"not a directory: {path.as_posix()}") from exc
            except OSError as exc:
                raise _map_os_error(exc, path.as_posix()) from exc
            windows_entries = [
                _entry_from_stat(name, value, etag=etag) for name, value, etag in windows_rows
            ]
            windows_entries.sort(key=lambda e: (e.kind != EntryKind.DIRECTORY, e.name))
            return windows_entries
        if self._root is not None:
            try:
                rooted_rows = await anyio.to_thread.run_sync(_rooted_list, self._root, path)
            except FileNotFoundError as exc:
                raise NotFoundError(path.as_posix()) from exc
            except PermissionError as exc:
                raise PermissionDeniedError(path.as_posix()) from exc
            except NotADirectoryError as exc:
                raise ConflictError(f"not a directory: {path.as_posix()}") from exc
            except OSError as exc:
                raise _map_os_error(exc, path.as_posix()) from exc
            rooted_entries = [_entry_from_stat(name, value) for name, value in rooted_rows]
            rooted_entries.sort(key=lambda e: (e.kind != EntryKind.DIRECTORY, e.name))
            return rooted_entries
        host = self._resolve(path)
        try:
            children: list[AnyioPath] = []
            async for child in host.iterdir():
                if len(children) >= _MAX_LISTING_ENTRIES:
                    raise ProviderError(
                        f"local directory listing exceeded the listing safety limit "
                        f"of {_MAX_LISTING_ENTRIES} entries"
                    )
                children.append(child)
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
        if self._root is None and _WINDOWS and path.is_root:
            return FileEntry(name="", kind=EntryKind.DIRECTORY, size=None, modified=None)
        host = self._resolve_leaf(path)
        try:
            if _WINDOWS:
                value, etag = await anyio.to_thread.run_sync(
                    _windows_lstat,
                    self._root,
                    path,
                )
                return _entry_from_stat(
                    path.name if not path.is_root else "",
                    value,
                    etag=etag,
                )
            if self._root is not None:
                value = await anyio.to_thread.run_sync(_rooted_lstat, self._root, path)
                return _entry_from_stat(path.name if not path.is_root else "", value)
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
        if self._root is None and _WINDOWS and path.is_root:
            return
        if _WINDOWS:
            try:
                await anyio.to_thread.run_sync(_windows_mkdir, self._root, path)
                return
            except FileExistsError as exc:
                raise ConflictError(path.as_posix()) from exc
            except PermissionError as exc:
                raise PermissionDeniedError(path.as_posix()) from exc
            except OSError as exc:
                raise _map_os_error(exc, path.as_posix()) from exc
        if self._root is not None:
            try:
                await anyio.to_thread.run_sync(_rooted_mkdir, self._root, path)
                return
            except FileExistsError as exc:
                raise ConflictError(path.as_posix()) from exc
            except PermissionError as exc:
                raise PermissionDeniedError(path.as_posix()) from exc
            except OSError as exc:
                raise _map_os_error(exc, path.as_posix()) from exc
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

    async def claim_directory(self, path: PathRef) -> str:
        if path.is_root:
            raise ConflictError(path.as_posix())
        if _WINDOWS:
            try:
                return await anyio.to_thread.run_sync(
                    _windows_claim_directory,
                    self._root,
                    path,
                )
            except FileExistsError as exc:
                raise ConflictError(path.as_posix()) from exc
            except FileNotFoundError as exc:
                raise NotFoundError(path.parent().as_posix()) from exc
            except PermissionError as exc:
                raise PermissionDeniedError(path.as_posix()) from exc
            except OSError as exc:
                raise _map_os_error(exc, path.as_posix()) from exc
        if self._root is not None:
            try:
                return await anyio.to_thread.run_sync(
                    _rooted_claim_directory,
                    self._root,
                    path,
                )
            except FileExistsError as exc:
                raise ConflictError(path.as_posix()) from exc
            except FileNotFoundError as exc:
                raise NotFoundError(path.parent().as_posix()) from exc
            except PermissionError as exc:
                raise PermissionDeniedError(path.as_posix()) from exc
            except OSError as exc:
                raise _map_os_error(exc, path.as_posix()) from exc
        host = self._resolve_leaf(path)
        try:
            return await anyio.to_thread.run_sync(
                _unrooted_claim_directory,
                host.as_posix(),
            )
        except FileExistsError as exc:
            raise ConflictError(host.as_posix()) from exc
        except FileNotFoundError as exc:
            raise NotFoundError(host.parent.as_posix()) from exc
        except PermissionError as exc:
            raise PermissionDeniedError(host.as_posix()) from exc
        except OSError as exc:
            raise _map_os_error(exc, host.as_posix()) from exc

    async def delete(self, path: PathRef, *, expected_etag: str | None = None) -> None:
        if path.is_root:
            raise ProviderError("cannot delete the local filesystem provider root")
        if _WINDOWS:
            try:
                await anyio.to_thread.run_sync(
                    _windows_delete,
                    self._root,
                    path,
                    expected_etag,
                )
                return
            except FileNotFoundError as exc:
                raise NotFoundError(path.as_posix()) from exc
            except FileExistsError as exc:  # pragma: no cover - UUID collision
                raise ConflictError(path.as_posix()) from exc
            except PermissionError as exc:
                raise PermissionDeniedError(path.as_posix()) from exc
            except OSError as exc:
                raise _map_os_error(exc, path.as_posix()) from exc
        if self._root is not None:
            try:
                await anyio.to_thread.run_sync(
                    _rooted_delete,
                    self._root,
                    path,
                    expected_etag,
                )
                return
            except FileNotFoundError as exc:
                raise NotFoundError(path.as_posix()) from exc
            except FileExistsError as exc:  # pragma: no cover - UUID collision
                raise ConflictError(path.as_posix()) from exc
            except PermissionError as exc:
                raise PermissionDeniedError(path.as_posix()) from exc
            except OSError as exc:
                raise _map_os_error(exc, path.as_posix()) from exc
        host = self._resolve_leaf(path)
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

        claimed_original: AnyioPath | None = None
        if expected_etag is not None:
            if not _local_revision_matches(_local_etag(host_stat), expected_etag):
                raise ConflictError(f"source changed: {host.as_posix()}")
            original = host
            claimed_original = original
            quarantine = AnyioPath(
                Path(host.as_posix()).with_name(
                    f".{Path(host.as_posix()).name}.aws-tui-delete-{uuid4().hex}"
                )
            )
            try:
                await anyio.to_thread.run_sync(
                    _rename_no_replace,
                    original.as_posix(),
                    quarantine.as_posix(),
                )
                quarantined_stat = await quarantine.lstat()
            except FileNotFoundError as exc:
                raise NotFoundError(original.as_posix()) from exc
            except FileExistsError as exc:  # pragma: no cover - UUID collision
                raise ConflictError(quarantine.as_posix()) from exc
            except OSError as exc:
                raise _map_os_error(exc, original.as_posix()) from exc
            if _local_stable_identity(quarantined_stat) != _local_stable_identity(host_stat):
                try:
                    await anyio.to_thread.run_sync(
                        _rename_no_replace,
                        quarantine.as_posix(),
                        original.as_posix(),
                    )
                except OSError as restore_exc:
                    raise ProviderError(
                        "source changed during move and could not be restored from "
                        f"{quarantine.as_posix()}: {restore_exc}"
                    ) from restore_exc
                raise ConflictError(f"source changed: {original.as_posix()}")
            host = quarantine
            host_stat = quarantined_stat

        try:
            # The inner block restores the quarantine claim before the outer
            # handlers map the failure, so the error taxonomy is unchanged and
            # a failed delete no longer leaves the caller's entry renamed to a
            # hidden `.<name>.aws-tui-delete-<uuid>` with nothing to put it
            # back. The rooted sibling `_rooted_delete_empty_directory` already
            # does this; reachable in production through `CrossFsMove`, whose
            # source delete passes `expected_etag`.
            try:
                if stat.S_ISDIR(host_stat.st_mode) and not stat.S_ISLNK(host_stat.st_mode):
                    await anyio.to_thread.run_sync(shutil.rmtree, str(host))
                else:
                    await host.unlink()
            except BaseException:
                if claimed_original is not None:
                    try:
                        await anyio.to_thread.run_sync(
                            _rename_no_replace,
                            host.as_posix(),
                            claimed_original.as_posix(),
                        )
                    except OSError as restore_exc:
                        raise ProviderError(
                            "delete failed and the original could not be restored "
                            f"from {host.as_posix()}: {restore_exc}"
                        ) from restore_exc
                raise
        except FileNotFoundError as exc:
            raise NotFoundError(host.as_posix()) from exc
        except PermissionError as exc:
            raise PermissionDeniedError(host.as_posix()) from exc
        except OSError as exc:
            raise _map_os_error(exc, host.as_posix()) from exc

    async def delete_empty_directory(self, path: PathRef) -> None:
        if path.is_root:
            raise ProviderError("cannot delete the local filesystem provider root")
        if _WINDOWS:
            try:
                await anyio.to_thread.run_sync(
                    _windows_delete_empty_directory,
                    self._root,
                    path,
                )
                return
            except FileNotFoundError as exc:
                raise NotFoundError(path.as_posix()) from exc
            except PermissionError as exc:
                raise PermissionDeniedError(path.as_posix()) from exc
            except OSError as exc:
                if exc.errno == errno.ENOTEMPTY:
                    raise ConflictError(f"directory is not empty: {path.as_posix()}") from exc
                raise _map_os_error(exc, path.as_posix()) from exc
        if self._root is not None:
            try:
                await anyio.to_thread.run_sync(
                    _rooted_delete_empty_directory,
                    self._root,
                    path,
                )
                return
            except FileNotFoundError as exc:
                raise NotFoundError(path.as_posix()) from exc
            except PermissionError as exc:
                raise PermissionDeniedError(path.as_posix()) from exc
            except OSError as exc:
                if exc.errno == errno.ENOTEMPTY:
                    raise ConflictError(f"directory is not empty: {path.as_posix()}") from exc
                raise _map_os_error(exc, path.as_posix()) from exc
        host = self._resolve_leaf(path)
        quarantine = Path(host.as_posix()).with_name(
            f".{Path(host.as_posix()).name}.aws-tui-rmdir-{uuid4().hex}"
        )
        try:
            await anyio.to_thread.run_sync(
                _claim_and_remove_empty_directory,
                host.as_posix(),
                str(quarantine),
            )
        except FileNotFoundError as exc:
            raise NotFoundError(host.as_posix()) from exc
        except PermissionError as exc:
            raise PermissionDeniedError(host.as_posix()) from exc
        except OSError as exc:
            if exc.errno == errno.ENOTEMPTY:
                raise ConflictError(f"directory is not empty: {host.as_posix()}") from exc
            raise _map_os_error(exc, host.as_posix()) from exc

    async def rename(self, src: PathRef, dst: PathRef) -> None:
        if src.is_root or dst.is_root:
            raise ProviderError("cannot rename the local filesystem provider root")
        if _WINDOWS:
            try:
                await anyio.to_thread.run_sync(_windows_rename, self._root, src, dst)
                return
            except FileExistsError as exc:
                raise ConflictError(dst.as_posix()) from exc
            except FileNotFoundError as exc:
                raise NotFoundError(src.as_posix()) from exc
            except PermissionError as exc:
                raise PermissionDeniedError(src.as_posix()) from exc
            except OSError as exc:
                raise _map_os_error(exc, src.as_posix()) from exc
        if self._root is not None:
            try:
                await anyio.to_thread.run_sync(_rooted_rename, self._root, src, dst)
                return
            except FileExistsError as exc:
                raise ConflictError(dst.as_posix()) from exc
            except FileNotFoundError as exc:
                raise NotFoundError(src.as_posix()) from exc
            except PermissionError as exc:
                raise PermissionDeniedError(src.as_posix()) from exc
            except OSError as exc:
                raise _map_os_error(exc, src.as_posix()) from exc
        host_src = self._resolve_leaf(src)
        host_dst = self._resolve_leaf(dst)
        try:
            await anyio.to_thread.run_sync(
                _rename_no_replace,
                host_src.as_posix(),
                host_dst.as_posix(),
            )
        except FileExistsError as exc:
            raise ConflictError(host_dst.as_posix()) from exc
        except FileNotFoundError as exc:
            raise NotFoundError(host_src.as_posix()) from exc
        except PermissionError as exc:
            raise PermissionDeniedError(host_src.as_posix()) from exc
        except OSError as exc:
            raise _map_os_error(exc, host_src.as_posix()) from exc

    def supports_atomic_publish(self, kind: EntryKind) -> bool:
        return kind != EntryKind.DIRECTORY or _supports_atomic_directory_publish(
            descriptor_relative=self._root is not None
        )

    async def capture_stage_revision(self, path: PathRef) -> str:
        revision = (await self.stat(path)).etag
        if revision is None:
            raise ProviderError(f"local stage has no revision: {path.as_posix()}")
        return revision

    async def atomic_publish_no_replace(
        self,
        staged: PathRef,
        destination: PathRef,
        *,
        expected_source_revision: str,
    ) -> str:
        if staged.is_root or destination.is_root:
            raise ProviderError("cannot rename the local filesystem provider root")
        try:
            async with self._stage_publish_lock:
                if _WINDOWS:
                    return await anyio.to_thread.run_sync(
                        _windows_atomic_publish_no_replace,
                        self._root,
                        staged,
                        destination,
                        expected_source_revision,
                    )
                if self._root is not None:
                    return await anyio.to_thread.run_sync(
                        _rooted_atomic_publish_no_replace,
                        self._root,
                        staged,
                        destination,
                        expected_source_revision,
                    )
                host_staged = self._resolve_leaf(staged)
                host_destination = self._resolve_leaf(destination)
                return await anyio.to_thread.run_sync(
                    _unrooted_atomic_publish_no_replace,
                    host_staged.as_posix(),
                    host_destination.as_posix(),
                    expected_source_revision,
                )
        except FileExistsError as exc:
            raise ConflictError(destination.as_posix()) from exc
        except FileNotFoundError as exc:
            raise NotFoundError(staged.as_posix()) from exc
        except PermissionError as exc:
            raise PermissionDeniedError(staged.as_posix()) from exc
        except OSError as exc:
            raise _map_os_error(exc, staged.as_posix()) from exc

    async def atomic_publish_directory_no_replace(
        self,
        staged: PathRef,
        destination: PathRef,
        *,
        expected_manifest: tuple[StageManifestEntry, ...],
    ) -> str:
        if staged.is_root or destination.is_root:
            raise ProviderError("cannot rename the local filesystem provider root")
        try:
            async with self._stage_publish_lock:
                if _WINDOWS:
                    return await anyio.to_thread.run_sync(
                        _windows_atomic_publish_directory_no_replace,
                        self._root,
                        staged,
                        destination,
                        expected_manifest,
                    )
                if self._root is not None:
                    return await anyio.to_thread.run_sync(
                        _rooted_atomic_publish_directory_no_replace,
                        self._root,
                        staged,
                        destination,
                        expected_manifest,
                    )
                host_staged = self._resolve_leaf(staged)
                host_destination = self._resolve_leaf(destination)
                return await anyio.to_thread.run_sync(
                    _unrooted_atomic_publish_directory_no_replace,
                    host_staged.as_posix(),
                    host_destination.as_posix(),
                    expected_manifest,
                )
        except FileExistsError as exc:
            raise ConflictError(destination.as_posix()) from exc
        except FileNotFoundError as exc:
            raise NotFoundError(staged.as_posix()) from exc
        except PermissionError as exc:
            raise PermissionDeniedError(staged.as_posix()) from exc
        except OSError as exc:
            raise _map_os_error(exc, staged.as_posix()) from exc

    async def preflight_move_revision(self, path: PathRef, revision: str | None) -> None:
        if revision is None:
            raise ProviderError(f"move source has no revision: {path.as_posix()}")
        observed = await self.stat(path)
        if observed.etag != revision:
            raise ConflictError(f"source changed: {path.as_posix()}")

    # ------------------------------------------------------------------
    # Streaming I/O
    # ------------------------------------------------------------------

    async def read_stream(
        self, path: PathRef, *, chunk_size: int = _DEFAULT_CHUNK_SIZE
    ) -> AsyncIterator[bytes]:
        host = self._resolve_leaf(path)

        async def _iterate() -> AsyncIterator[bytes]:
            fd: int | None = None
            handed_off = False
            try:
                if _WINDOWS:
                    fd = await anyio.to_thread.run_sync(
                        _windows_open,
                        self._root,
                        path,
                        os.O_RDONLY,
                    )
                elif self._root is not None:
                    fd = await anyio.to_thread.run_sync(
                        _rooted_open,
                        self._root,
                        path,
                        os.O_RDONLY,
                    )
                else:
                    fd = await anyio.to_thread.run_sync(
                        _open_nofollow,
                        host.as_posix(),
                        os.O_RDONLY,
                    )
                opened = os.fstat(fd)
                if not stat.S_ISREG(opened.st_mode):
                    raise ConflictError(f"not a regular file: {host.as_posix()}")
                handed_off = True
            except FileNotFoundError as exc:
                raise NotFoundError(host.as_posix()) from exc
            except PermissionError as exc:
                raise PermissionDeniedError(host.as_posix()) from exc
            except OSError as exc:
                if exc.errno == errno.ELOOP:
                    raise ConflictError(f"refusing symlink: {host.as_posix()}") from exc
                raise _map_os_error(exc, host.as_posix()) from exc
            finally:
                if fd is not None and not handed_off:
                    with suppress(OSError):
                        os.close(fd)

            assert fd is not None
            stream = _read_chunks_fd(fd, host.as_posix(), chunk_size)
            try:
                async for chunk in stream:
                    yield chunk
            finally:
                await stream.aclose()

        return _iterate()

    async def write_stream(
        self,
        path: PathRef,
        source: AsyncIterator[bytes],
        *,
        total_size: int | None = None,
        progress: ProgressCallback | None = None,
        overwrite: bool = True,
    ) -> None:
        host = self._resolve_leaf(path)
        flags = os.O_WRONLY | os.O_CREAT | (os.O_TRUNC if overwrite else os.O_EXCL)
        try:
            if _WINDOWS:
                fd = await anyio.to_thread.run_sync(
                    _windows_open,
                    self._root,
                    path,
                    flags,
                )
            elif self._root is not None:
                fd = await anyio.to_thread.run_sync(_rooted_open, self._root, path, flags)
            else:
                fd = await anyio.to_thread.run_sync(_open_nofollow, host.as_posix(), flags)
            async with aiofiles.open(fd, "wb", closefd=True) as fh:
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
        except FileExistsError as exc:
            raise ConflictError(host.as_posix()) from exc
        except FileNotFoundError as exc:
            raise NotFoundError(host.as_posix()) from exc
        except IsADirectoryError as exc:
            raise ConflictError(f"is a directory: {host.as_posix()}") from exc
        except PermissionError as exc:
            raise PermissionDeniedError(host.as_posix()) from exc
        except OSError as exc:
            if exc.errno == errno.ELOOP:
                raise ConflictError(f"refusing symlink: {host.as_posix()}") from exc
            raise _map_os_error(exc, host.as_posix()) from exc


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _windows_path_ref(path: PathRef) -> PureWindowsPath:
    if path.is_root:
        raise ProviderError("the Windows drive list is a virtual filesystem root")
    drive, *rest = path.segments
    if len(drive) != 2 or not drive[0].isalpha() or drive[1] != ":":
        raise ProviderError("an unrooted Windows local path must start with a drive such as C:")
    return PureWindowsPath(f"{drive}\\", *rest)


def _windows_drive_entries() -> list[FileEntry]:
    list_drives = getattr(os, "listdrives", None)
    drives = (
        list_drives()
        if callable(list_drives)
        else [f"{letter}:\\" for letter in ascii_uppercase if Path(f"{letter}:\\").exists()]
    )
    return [
        FileEntry(
            name=drive.rstrip("\\/"),
            kind=EntryKind.DIRECTORY,
            size=None,
            modified=None,
        )
        for drive in drives
    ]


_WINDOWS_FILE_ATTRIBUTE_DIRECTORY = 0x00000010
_WINDOWS_FILE_ATTRIBUTE_REPARSE_POINT = 0x00000400
_WINDOWS_FILE_FLAG_BACKUP_SEMANTICS = 0x02000000
_WINDOWS_FILE_FLAG_OPEN_REPARSE_POINT = 0x00200000
_WINDOWS_FILE_LIST_DIRECTORY = 0x00000001
_WINDOWS_FILE_READ_ATTRIBUTES = 0x00000080
_WINDOWS_DELETE_ACCESS = 0x00010000
_WINDOWS_GENERIC_READ = 0x80000000
_WINDOWS_GENERIC_WRITE = 0x40000000
_WINDOWS_FILE_SHARE_READ = 0x00000001
_WINDOWS_FILE_SHARE_WRITE = 0x00000002
_WINDOWS_CREATE_NEW = 1
_WINDOWS_OPEN_EXISTING = 3
_WINDOWS_FILE_BASIC_INFO = 0
_WINDOWS_FILE_DISPOSITION_INFO = 4
_WINDOWS_FILE_RENAME_INFORMATION = 10
_WINDOWS_FILE_NAME_OPENED = 0x00000008
_WINDOWS_INVALID_HANDLE = ctypes.c_void_p(-1).value
_WINDOWS_RESERVED_NAMES = {
    "AUX",
    "CON",
    "NUL",
    "PRN",
    *(f"COM{number}" for number in range(1, 10)),
    *(f"LPT{number}" for number in range(1, 10)),
    *(f"COM{number}" for number in "¹²³"),
    *(f"LPT{number}" for number in "¹²³"),
}


class _WindowsFileInformation(ctypes.Structure):
    _fields_ = [
        ("dwFileAttributes", wintypes.DWORD),
        ("ftCreationTime", wintypes.FILETIME),
        ("ftLastAccessTime", wintypes.FILETIME),
        ("ftLastWriteTime", wintypes.FILETIME),
        ("dwVolumeSerialNumber", wintypes.DWORD),
        ("nFileSizeHigh", wintypes.DWORD),
        ("nFileSizeLow", wintypes.DWORD),
        ("nNumberOfLinks", wintypes.DWORD),
        ("nFileIndexHigh", wintypes.DWORD),
        ("nFileIndexLow", wintypes.DWORD),
    ]


class _WindowsBasicInformation(ctypes.Structure):
    _fields_ = [
        ("CreationTime", ctypes.c_longlong),
        ("LastAccessTime", ctypes.c_longlong),
        ("LastWriteTime", ctypes.c_longlong),
        ("ChangeTime", ctypes.c_longlong),
        ("FileAttributes", wintypes.DWORD),
    ]


class _WindowsRenameInformation(ctypes.Structure):
    _fields_ = [
        ("ReplaceIfExists", ctypes.c_ubyte),
        ("RootDirectory", wintypes.HANDLE),
        ("FileNameLength", wintypes.DWORD),
        ("FileName", wintypes.WCHAR * 1),
    ]


class _WindowsDispositionInformation(ctypes.Structure):
    _fields_ = [("DeleteFile", ctypes.c_ubyte)]


class _WindowsIOStatusBlock(ctypes.Structure):
    _fields_ = [
        ("Status", wintypes.LONG),
        ("Information", ctypes.c_size_t),
    ]


def _raise_windows_error_code(code: int, path: str) -> None:
    format_error = cast(Callable[[int], str], ctypes.__dict__["FormatError"])
    message = format_error(code)
    if code in {2, 3}:
        raise FileNotFoundError(errno.ENOENT, message, path)
    if code in {5, 32, 33}:
        raise PermissionError(errno.EACCES, message, path)
    if code in {80, 183}:
        raise FileExistsError(errno.EEXIST, message, path)
    if code == 145:
        raise OSError(errno.ENOTEMPTY, message, path)
    if code == 267:
        raise NotADirectoryError(errno.ENOTDIR, message, path)
    if code == 17:
        raise OSError(errno.EXDEV, message, path)
    raise OSError(errno.EIO, f"WinError {code}: {message}", path)


def _raise_windows_error(path: str) -> None:
    get_last_error = cast(Callable[[], int], ctypes.__dict__["get_last_error"])
    _raise_windows_error_code(get_last_error(), path)


class _WindowsAPI:
    """Typed, minimal wrapper around the Win32 calls LocalFS requires."""

    def __init__(self) -> None:
        dll_factory = getattr(ctypes, "WinDLL", None)
        if dll_factory is None:  # pragma: no cover - guarded by _WINDOWS
            raise ProviderError("Win32 APIs are unavailable on this platform")
        self._dll: Any = dll_factory("kernel32", use_last_error=True)
        self._ntdll: Any = dll_factory("ntdll")
        self._dll.CreateFileW.argtypes = [
            wintypes.LPCWSTR,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.LPVOID,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.HANDLE,
        ]
        self._dll.CreateFileW.restype = wintypes.HANDLE
        self._dll.CloseHandle.argtypes = [wintypes.HANDLE]
        self._dll.CloseHandle.restype = wintypes.BOOL
        self._dll.GetFileInformationByHandle.argtypes = [
            wintypes.HANDLE,
            ctypes.POINTER(_WindowsFileInformation),
        ]
        self._dll.GetFileInformationByHandle.restype = wintypes.BOOL
        self._dll.GetFileInformationByHandleEx.argtypes = [
            wintypes.HANDLE,
            ctypes.c_int,
            wintypes.LPVOID,
            wintypes.DWORD,
        ]
        self._dll.GetFileInformationByHandleEx.restype = wintypes.BOOL
        self._dll.SetFileInformationByHandle.argtypes = [
            wintypes.HANDLE,
            ctypes.c_int,
            wintypes.LPVOID,
            wintypes.DWORD,
        ]
        self._dll.SetFileInformationByHandle.restype = wintypes.BOOL
        self._dll.GetFinalPathNameByHandleW.argtypes = [
            wintypes.HANDLE,
            wintypes.LPWSTR,
            wintypes.DWORD,
            wintypes.DWORD,
        ]
        self._dll.GetFinalPathNameByHandleW.restype = wintypes.DWORD
        self._dll.CreateDirectoryW.argtypes = [wintypes.LPCWSTR, wintypes.LPVOID]
        self._dll.CreateDirectoryW.restype = wintypes.BOOL
        self._ntdll.NtSetInformationFile.argtypes = [
            wintypes.HANDLE,
            ctypes.POINTER(_WindowsIOStatusBlock),
            wintypes.LPVOID,
            wintypes.ULONG,
            ctypes.c_int,
        ]
        self._ntdll.NtSetInformationFile.restype = wintypes.LONG
        self._ntdll.RtlNtStatusToDosError.argtypes = [wintypes.LONG]
        self._ntdll.RtlNtStatusToDosError.restype = wintypes.ULONG

    def open(self, path: str, *, access: int, disposition: int) -> int:
        share_mode = _WINDOWS_FILE_SHARE_READ
        if access & _WINDOWS_FILE_LIST_DIRECTORY:
            # A handle-relative rename opens its target against the locked
            # directory with write access. Permit that while continuing to
            # withhold delete sharing, which prevents path replacement.
            share_mode |= _WINDOWS_FILE_SHARE_WRITE
        handle = self._dll.CreateFileW(
            path,
            access,
            # Keep the opened entry stable while its path is validated and used.
            # Withholding delete sharing blocks reparse mutation and path replacement.
            share_mode,
            None,
            disposition,
            _WINDOWS_FILE_FLAG_BACKUP_SEMANTICS | _WINDOWS_FILE_FLAG_OPEN_REPARSE_POINT,
            None,
        )
        if handle == _WINDOWS_INVALID_HANDLE:
            _raise_windows_error(path)
        return int(handle)

    def close(self, handle: int) -> None:
        if not self._dll.CloseHandle(handle):  # pragma: no cover - defensive cleanup
            _raise_windows_error("handle")

    def attributes(self, handle: int, path: str) -> int:
        return int(self.file_information(handle, path).dwFileAttributes)

    def file_information(self, handle: int, path: str) -> _WindowsFileInformation:
        information = _WindowsFileInformation()
        if not self._dll.GetFileInformationByHandle(handle, ctypes.byref(information)):
            _raise_windows_error(path)
        return information

    def basic_information(self, handle: int, path: str) -> _WindowsBasicInformation:
        information = _WindowsBasicInformation()
        if not self._dll.GetFileInformationByHandleEx(
            handle,
            _WINDOWS_FILE_BASIC_INFO,
            ctypes.byref(information),
            ctypes.sizeof(information),
        ):
            _raise_windows_error(path)
        return information

    def revision(self, handle: int, path: str) -> str:
        return _windows_revision(
            self.file_information(handle, path),
            self.basic_information(handle, path),
        )

    def final_path(self, handle: int, path: str) -> str:
        def query(flags: int) -> str:
            return self._query_final_path(handle, path, flags)

        return _normalize_windows_final_path(_windows_final_path_with_fallback(query))

    def _query_final_path(self, handle: int, path: str, flags: int) -> str:
        required = int(self._dll.GetFinalPathNameByHandleW(handle, None, 0, flags))
        if required == 0:
            _raise_windows_error(path)
        buffer = ctypes.create_unicode_buffer(required + 1)
        written = int(self._dll.GetFinalPathNameByHandleW(handle, buffer, len(buffer), flags))
        if written == 0 or written >= len(buffer):
            _raise_windows_error(path)
        return buffer.value

    def rename_handle(self, handle: int, parent_handle: int, name: str, path: str) -> None:
        encoded_name = name.encode("utf-16-le")
        name_offset = _WindowsRenameInformation.FileName.offset
        # Windows requires the allocation to include the complete base
        # structure in addition to the variable-length filename payload.
        buffer_size = ctypes.sizeof(_WindowsRenameInformation) + len(encoded_name)
        buffer = ctypes.create_string_buffer(buffer_size)
        information = ctypes.cast(
            buffer,
            ctypes.POINTER(_WindowsRenameInformation),
        ).contents
        information.ReplaceIfExists = 0
        information.RootDirectory = parent_handle
        information.FileNameLength = len(encoded_name)
        ctypes.memmove(ctypes.addressof(buffer) + name_offset, encoded_name, len(encoded_name))
        io_status = _WindowsIOStatusBlock()
        status = int(
            self._ntdll.NtSetInformationFile(
                handle,
                ctypes.byref(io_status),
                buffer,
                buffer_size,
                _WINDOWS_FILE_RENAME_INFORMATION,
            )
        )
        if status < 0:
            code = int(self._ntdll.RtlNtStatusToDosError(status))
            _raise_windows_error_code(code, path)

    def delete_handle(self, handle: int, path: str) -> None:
        information = _WindowsDispositionInformation(DeleteFile=1)
        if not self._dll.SetFileInformationByHandle(
            handle,
            _WINDOWS_FILE_DISPOSITION_INFO,
            ctypes.byref(information),
            ctypes.sizeof(information),
        ):
            _raise_windows_error(path)

    def mkdir(self, path: str) -> None:
        if not self._dll.CreateDirectoryW(path, None):
            _raise_windows_error(path)


_WINDOWS_API: _WindowsAPI | None = None


def _windows_api() -> _WindowsAPI:
    global _WINDOWS_API
    if not _WINDOWS:
        raise ProviderError("Win32 APIs are unavailable on this platform")
    if _WINDOWS_API is None:
        _WINDOWS_API = _WindowsAPI()
    return _WINDOWS_API


def _windows_final_path_with_fallback(query: Callable[[int], str]) -> str:
    try:
        return query(0)
    except PermissionError:
        return query(_WINDOWS_FILE_NAME_OPENED)


def _windows_revision(file_information: Any, basic_information: Any) -> str:
    file_index = int(file_information.nFileIndexHigh) << 32 | int(file_information.nFileIndexLow)
    file_size = int(file_information.nFileSizeHigh) << 32 | int(file_information.nFileSizeLow)
    return ":".join(
        (
            "windows",
            str(file_information.dwVolumeSerialNumber),
            str(file_index),
            str(file_size),
            str(basic_information.LastWriteTime),
            str(basic_information.ChangeTime),
        )
    )


def _normalize_windows_final_path(path: str) -> str:
    if path.startswith("\\\\?\\UNC\\"):
        path = f"\\\\{path[8:]}"
    elif path.startswith("\\\\?\\") or path.startswith("\\??\\"):
        path = path[4:]
    return ntpath.normcase(ntpath.normpath(path))


def _windows_path_is_contained(path: str, root: str) -> bool:
    normalized_path = _normalize_windows_final_path(path)
    normalized_root = _normalize_windows_final_path(root)
    try:
        return ntpath.commonpath((normalized_path, normalized_root)) == normalized_root
    except ValueError:
        return False


def _validate_windows_relative(path: PathRef) -> None:
    for segment in path.segments:
        device_name = segment.split(".", 1)[0].upper()
        if (
            segment in {"", ".", ".."}
            or segment[-1:] in {" ", "."}
            or any(character in '<>:"/\\|?*' or ord(character) < 32 for character in segment)
            or device_name in _WINDOWS_RESERVED_NAMES
        ):
            raise ProviderError(f"path {path.as_posix()!r} contains an unsafe Windows segment")


def _windows_base_and_segments(root: Path | None, path: PathRef) -> tuple[str, tuple[str, ...]]:
    if root is not None:
        _validate_windows_relative(path)
        return str(root), path.segments
    if any(segment in {".", ".."} for segment in path.segments):
        raise ProviderError(f"path {path.as_posix()!r} contains an unsafe dot segment")
    native = _windows_path_ref(path)
    relative = PathRef(tuple(path.segments[1:]))
    _validate_windows_relative(relative)
    return str(PureWindowsPath(native.anchor)), relative.segments


def _windows_reject_reparse(attributes: int, path: str) -> None:
    if attributes & _WINDOWS_FILE_ATTRIBUTE_REPARSE_POINT:
        raise ConflictError(f"refusing reparse point traversal: {path}")


def _windows_require_directory(attributes: int, path: str) -> None:
    if not attributes & _WINDOWS_FILE_ATTRIBUTE_DIRECTORY:
        raise NotADirectoryError(errno.ENOTDIR, os.strerror(errno.ENOTDIR), path)


def _windows_assert_contained(api: _WindowsAPI, handle: int, path: str, root: str) -> None:
    final_path = api.final_path(handle, path)
    if not _windows_path_is_contained(final_path, root):
        raise ProviderError(f"path {path!r} escapes Windows filesystem root {root}")


@contextmanager
def _windows_locked_walk(
    root: Path | None,
    path: PathRef,
    *,
    include_leaf: bool,
) -> Iterator[tuple[str, int, str]]:
    api = _windows_api()
    base, segments = _windows_base_and_segments(root, path)
    selected = segments if include_leaf else segments[:-1]
    handles: list[int] = []
    current = base
    try:
        base_handle = api.open(
            base,
            access=_WINDOWS_FILE_LIST_DIRECTORY | _WINDOWS_FILE_READ_ATTRIBUTES,
            disposition=_WINDOWS_OPEN_EXISTING,
        )
        handles.append(base_handle)
        base_attributes = api.attributes(base_handle, base)
        _windows_reject_reparse(base_attributes, base)
        _windows_require_directory(base_attributes, base)
        anchor = api.final_path(base_handle, base)
        for segment in selected:
            current = ntpath.join(current, segment)
            handle = api.open(
                current,
                access=_WINDOWS_FILE_LIST_DIRECTORY | _WINDOWS_FILE_READ_ATTRIBUTES,
                disposition=_WINDOWS_OPEN_EXISTING,
            )
            handles.append(handle)
            attributes = api.attributes(handle, current)
            _windows_reject_reparse(attributes, current)
            _windows_require_directory(attributes, current)
            _windows_assert_contained(api, handle, current, anchor)
        yield current, handles[-1], anchor
    finally:
        for handle in reversed(handles):
            api.close(handle)


@contextmanager
def _windows_locked_parent(root: Path | None, path: PathRef) -> Iterator[tuple[str, int, str, str]]:
    if path.is_root:
        raise ProviderError("the provider root has no parent entry")
    with _windows_locked_walk(root, path, include_leaf=False) as (parent, handle, anchor):
        yield parent, handle, path.name, anchor


def _windows_list(root: Path | None, path: PathRef) -> list[tuple[str, os.stat_result, str]]:
    api = _windows_api()
    with _windows_locked_walk(root, path, include_leaf=True) as (directory, _handle, anchor):
        rows: list[tuple[str, os.stat_result, str]] = []
        for child in Path(directory).iterdir():
            child_path = str(child)
            try:
                handle = api.open(
                    child_path,
                    access=_WINDOWS_FILE_READ_ATTRIBUTES,
                    disposition=_WINDOWS_OPEN_EXISTING,
                )
            except FileNotFoundError:
                continue
            try:
                attributes = api.attributes(handle, child_path)
                if not attributes & _WINDOWS_FILE_ATTRIBUTE_REPARSE_POINT:
                    _windows_assert_contained(api, handle, child_path, anchor)
                value = child.lstat()
                revision = api.revision(handle, child_path)
            finally:
                api.close(handle)
            if len(rows) >= _MAX_LISTING_ENTRIES:
                raise ProviderError(
                    f"local directory listing exceeded the listing safety limit "
                    f"of {_MAX_LISTING_ENTRIES} entries"
                )
            rows.append((child.name, value, revision))
        return rows


def _windows_lstat(root: Path | None, path: PathRef) -> tuple[os.stat_result, str]:
    api = _windows_api()
    if path.is_root:
        with _windows_locked_walk(root, path, include_leaf=True) as (host, handle, _anchor):
            return Path(host).lstat(), api.revision(handle, host)
    with _windows_locked_parent(root, path) as (parent, _parent_handle, name, anchor):
        host = ntpath.join(parent, name)
        handle = api.open(
            host,
            access=_WINDOWS_FILE_READ_ATTRIBUTES,
            disposition=_WINDOWS_OPEN_EXISTING,
        )
        try:
            attributes = api.attributes(handle, host)
            if not attributes & _WINDOWS_FILE_ATTRIBUTE_REPARSE_POINT:
                _windows_assert_contained(api, handle, host, anchor)
            return Path(host).lstat(), api.revision(handle, host)
        finally:
            api.close(handle)


def _windows_mkdir(root: Path | None, path: PathRef) -> None:
    api = _windows_api()
    base, segments = _windows_base_and_segments(root, path)
    handles: list[int] = []
    current = base
    try:
        base_handle = api.open(
            base,
            access=_WINDOWS_FILE_LIST_DIRECTORY | _WINDOWS_FILE_READ_ATTRIBUTES,
            disposition=_WINDOWS_OPEN_EXISTING,
        )
        handles.append(base_handle)
        attributes = api.attributes(base_handle, base)
        _windows_reject_reparse(attributes, base)
        _windows_require_directory(attributes, base)
        anchor = api.final_path(base_handle, base)
        for segment in segments:
            current = ntpath.join(current, segment)
            with suppress(FileExistsError):
                api.mkdir(current)
            handle = api.open(
                current,
                access=_WINDOWS_FILE_LIST_DIRECTORY | _WINDOWS_FILE_READ_ATTRIBUTES,
                disposition=_WINDOWS_OPEN_EXISTING,
            )
            handles.append(handle)
            attributes = api.attributes(handle, current)
            _windows_reject_reparse(attributes, current)
            _windows_require_directory(attributes, current)
            _windows_assert_contained(api, handle, current, anchor)
    finally:
        for handle in reversed(handles):
            api.close(handle)


def _windows_claim_directory(root: Path | None, path: PathRef) -> str:
    api = _windows_api()
    with _windows_locked_parent(root, path) as (parent, _parent_handle, name, anchor):
        host = ntpath.join(parent, name)
        api.mkdir(host)
        handle = api.open(
            host,
            access=_WINDOWS_FILE_READ_ATTRIBUTES,
            disposition=_WINDOWS_OPEN_EXISTING,
        )
        try:
            attributes = api.attributes(handle, host)
            _windows_reject_reparse(attributes, host)
            _windows_require_directory(attributes, host)
            _windows_assert_contained(api, handle, host, anchor)
            return api.revision(handle, host)
        finally:
            api.close(handle)


def _windows_open(root: Path | None, path: PathRef, flags: int) -> int:
    api = _windows_api()
    writing = bool(flags & (os.O_WRONLY | os.O_RDWR))
    access = _WINDOWS_GENERIC_WRITE if writing else _WINDOWS_GENERIC_READ
    with _windows_locked_parent(root, path) as (parent, _parent_handle, name, anchor):
        host = ntpath.join(parent, name)
        if writing and flags & os.O_EXCL:
            handle = api.open(host, access=access, disposition=_WINDOWS_CREATE_NEW)
        elif writing and flags & os.O_CREAT:
            try:
                handle = api.open(host, access=access, disposition=_WINDOWS_OPEN_EXISTING)
            except FileNotFoundError:
                try:
                    handle = api.open(host, access=access, disposition=_WINDOWS_CREATE_NEW)
                except FileExistsError:
                    handle = api.open(host, access=access, disposition=_WINDOWS_OPEN_EXISTING)
        else:
            handle = api.open(host, access=access, disposition=_WINDOWS_OPEN_EXISTING)
        try:
            attributes = api.attributes(handle, host)
            _windows_reject_reparse(attributes, host)
            if attributes & _WINDOWS_FILE_ATTRIBUTE_DIRECTORY:
                raise IsADirectoryError(errno.EISDIR, os.strerror(errno.EISDIR), host)
            _windows_assert_contained(api, handle, host, anchor)
            import msvcrt

            open_osfhandle = cast(Callable[[int, int], int], msvcrt.__dict__["open_osfhandle"])
            binary_flag = cast(int, os.__dict__.get("O_BINARY", 0))
            descriptor_flags = (os.O_WRONLY if writing else os.O_RDONLY) | binary_flag
            fd = open_osfhandle(handle, descriptor_flags)
            handle = -1
            try:
                if writing and flags & os.O_TRUNC:
                    os.ftruncate(fd, 0)
            except BaseException:
                os.close(fd)
                raise
            return fd
        except BaseException:
            if handle != -1:
                api.close(handle)
            raise


def _windows_restore_claim(handle: int, parent_handle: int, name: str, path: str) -> None:
    try:
        _windows_api().rename_handle(handle, parent_handle, name, path)
    except OSError as restore_exc:
        raise ProviderError(
            "filesystem mutation failed and the claimed entry could not be restored from "
            f"{path}: {restore_exc}"
        ) from restore_exc


def _windows_remove_tree_handle(handle: int, path: str, anchor: str) -> None:
    api = _windows_api()
    attributes = api.attributes(handle, path)
    if not attributes & _WINDOWS_FILE_ATTRIBUTE_REPARSE_POINT:
        _windows_assert_contained(api, handle, path, anchor)
    if (
        attributes & _WINDOWS_FILE_ATTRIBUTE_DIRECTORY
        and not attributes & _WINDOWS_FILE_ATTRIBUTE_REPARSE_POINT
    ):
        for child in Path(path).iterdir():
            child_path = str(child)
            child_handle = api.open(
                child_path,
                access=_WINDOWS_DELETE_ACCESS | _WINDOWS_FILE_READ_ATTRIBUTES,
                disposition=_WINDOWS_OPEN_EXISTING,
            )
            try:
                _windows_remove_tree_handle(child_handle, child_path, anchor)
            finally:
                api.close(child_handle)
    api.delete_handle(handle, path)


def _windows_delete(
    root: Path | None,
    path: PathRef,
    expected_etag: str | None,
) -> None:
    api = _windows_api()
    with _windows_locked_parent(root, path) as (parent, parent_handle, name, anchor):
        host = ntpath.join(parent, name)
        handle = api.open(
            host,
            access=_WINDOWS_DELETE_ACCESS | _WINDOWS_FILE_READ_ATTRIBUTES,
            disposition=_WINDOWS_OPEN_EXISTING,
        )
        try:
            observed_revision = api.revision(handle, host)
            if expected_etag is not None and not _local_revision_matches(
                observed_revision, expected_etag
            ):
                raise ConflictError(f"source changed: {path.as_posix()}")
            attributes = api.attributes(handle, host)
            if not attributes & _WINDOWS_FILE_ATTRIBUTE_REPARSE_POINT:
                _windows_assert_contained(api, handle, host, anchor)
            quarantine_name = f".{name}.aws-tui-delete-{uuid4().hex}"
            quarantine = ntpath.join(parent, quarantine_name)
            api.rename_handle(handle, parent_handle, quarantine_name, host)
            _windows_remove_tree_handle(handle, quarantine, anchor)
        finally:
            api.close(handle)


def _windows_delete_empty_directory(root: Path | None, path: PathRef) -> None:
    api = _windows_api()
    with _windows_locked_parent(root, path) as (parent, parent_handle, name, anchor):
        host = ntpath.join(parent, name)
        handle = api.open(
            host,
            access=_WINDOWS_DELETE_ACCESS | _WINDOWS_FILE_READ_ATTRIBUTES,
            disposition=_WINDOWS_OPEN_EXISTING,
        )
        try:
            attributes = api.attributes(handle, host)
            _windows_reject_reparse(attributes, host)
            _windows_require_directory(attributes, host)
            _windows_assert_contained(api, handle, host, anchor)
            quarantine_name = f".{name}.aws-tui-rmdir-{uuid4().hex}"
            quarantine = ntpath.join(parent, quarantine_name)
            api.rename_handle(handle, parent_handle, quarantine_name, host)
            try:
                api.delete_handle(handle, quarantine)
            except BaseException:
                _windows_restore_claim(handle, parent_handle, name, quarantine)
                raise
        finally:
            api.close(handle)


def _windows_rename(root: Path | None, src: PathRef, dst: PathRef) -> None:
    api = _windows_api()
    with (
        _windows_locked_parent(root, src) as (
            src_parent,
            _src_parent_handle,
            src_name,
            src_anchor,
        ),
        _windows_locked_parent(root, dst) as (
            _dst_parent,
            dst_parent_handle,
            dst_name,
            _dst_anchor,
        ),
    ):
        src_host = ntpath.join(src_parent, src_name)
        handle = api.open(
            src_host,
            access=_WINDOWS_DELETE_ACCESS | _WINDOWS_FILE_READ_ATTRIBUTES,
            disposition=_WINDOWS_OPEN_EXISTING,
        )
        try:
            attributes = api.attributes(handle, src_host)
            if not attributes & _WINDOWS_FILE_ATTRIBUTE_REPARSE_POINT:
                _windows_assert_contained(api, handle, src_host, src_anchor)
            api.rename_handle(handle, dst_parent_handle, dst_name, src_host)
        finally:
            api.close(handle)


def _windows_kind_from_attributes(attributes: int) -> EntryKind:
    if attributes & _WINDOWS_FILE_ATTRIBUTE_REPARSE_POINT:
        return EntryKind.SYMLINK
    if attributes & _WINDOWS_FILE_ATTRIBUTE_DIRECTORY:
        return EntryKind.DIRECTORY
    return EntryKind.FILE


def _windows_atomic_publish_no_replace(
    root: Path | None,
    src: PathRef,
    dst: PathRef,
    expected_source_revision: str,
) -> str:
    api = _windows_api()
    with (
        _windows_locked_parent(root, src) as (
            src_parent,
            _src_parent_handle,
            src_name,
            src_anchor,
        ),
        _windows_locked_parent(root, dst) as (
            dst_parent,
            dst_parent_handle,
            dst_name,
            _dst_anchor,
        ),
    ):
        src_host = ntpath.join(src_parent, src_name)
        dst_host = ntpath.join(dst_parent, dst_name)
        handle = api.open(
            src_host,
            access=_WINDOWS_DELETE_ACCESS | _WINDOWS_FILE_READ_ATTRIBUTES,
            disposition=_WINDOWS_OPEN_EXISTING,
        )
        try:
            observed_revision = api.revision(handle, src_host)
            if not _local_revision_matches(observed_revision, expected_source_revision):
                raise ConflictError(f"stage changed: {src.as_posix()}")
            attributes = api.attributes(handle, src_host)
            kind = _windows_kind_from_attributes(attributes)
            _validate_atomic_publish_kind(kind, descriptor_relative=True)
            if not attributes & _WINDOWS_FILE_ATTRIBUTE_REPARSE_POINT:
                _windows_assert_contained(api, handle, src_host, src_anchor)
            api.rename_handle(handle, dst_parent_handle, dst_name, src_host)
            return api.revision(handle, dst_host)
        finally:
            api.close(handle)


def _windows_atomic_publish_directory_no_replace(
    root: Path | None,
    src: PathRef,
    dst: PathRef,
    expected_manifest: Sequence[StageManifestEntry],
) -> str:
    api = _windows_api()
    expected_by_path = {entry.relative_path: entry for entry in expected_manifest}
    root_expected = expected_by_path.get(PathRef(()))
    if (
        len(expected_by_path) != len(expected_manifest)
        or root_expected is None
        or root_expected.kind != EntryKind.DIRECTORY
    ):
        raise ConflictError(f"stage manifest changed: {src.as_posix()}")

    with (
        _windows_locked_parent(root, src) as (
            src_parent,
            _src_parent_handle,
            src_name,
            src_anchor,
        ),
        _windows_locked_parent(root, dst) as (
            dst_parent,
            dst_parent_handle,
            dst_name,
            _dst_anchor,
        ),
    ):
        src_host = ntpath.join(src_parent, src_name)
        dst_host = ntpath.join(dst_parent, dst_name)
        root_handle = api.open(
            src_host,
            access=(
                _WINDOWS_DELETE_ACCESS
                | _WINDOWS_FILE_LIST_DIRECTORY
                | _WINDOWS_FILE_READ_ATTRIBUTES
            ),
            disposition=_WINDOWS_OPEN_EXISTING,
        )
        child_handles: list[tuple[int, str, StageManifestEntry]] = []
        seen: set[PathRef] = set()
        try:
            _windows_validate_stage_tree(
                api,
                root_handle,
                src_host,
                PathRef(()),
                src_anchor,
                expected_by_path,
                seen,
                child_handles,
                src,
            )
            if seen != set(expected_by_path):
                raise ConflictError(f"stage manifest changed: {src.as_posix()}")
            for handle, host, expected in child_handles:
                _windows_validate_stage_entry(api, handle, host, expected, src_anchor, src)
            _windows_validate_stage_entry(
                api,
                root_handle,
                src_host,
                root_expected,
                src_anchor,
                src,
            )
            api.rename_handle(root_handle, dst_parent_handle, dst_name, src_host)
            return api.revision(root_handle, dst_host)
        finally:
            for handle, _host, _expected in reversed(child_handles):
                api.close(handle)
            api.close(root_handle)


def _windows_validate_stage_tree(
    api: _WindowsAPI,
    handle: int,
    host: str,
    relative: PathRef,
    anchor: str,
    expected_by_path: dict[PathRef, StageManifestEntry],
    seen: set[PathRef],
    retained_handles: list[tuple[int, str, StageManifestEntry]],
    stage: PathRef,
) -> None:
    expected = expected_by_path.get(relative)
    if expected is None or relative in seen:
        raise ConflictError(f"stage manifest changed: {stage.as_posix()}")
    _windows_validate_stage_entry(api, handle, host, expected, anchor, stage)
    kind = expected.kind
    seen.add(relative)

    if kind != EntryKind.DIRECTORY:
        return
    try:
        children = sorted(Path(host).iterdir(), key=lambda entry: entry.name)
    except (FileNotFoundError, NotADirectoryError, PermissionError) as exc:
        raise ConflictError(f"stage manifest changed: {stage.as_posix()}") from exc
    for child in children:
        child_host = str(child)
        try:
            child_handle = api.open(
                child_host,
                access=_WINDOWS_FILE_READ_ATTRIBUTES,
                disposition=_WINDOWS_OPEN_EXISTING,
            )
        except (FileNotFoundError, PermissionError) as exc:
            raise ConflictError(f"stage manifest changed: {stage.as_posix()}") from exc
        child_expected = expected_by_path.get(relative.join(child.name))
        if child_expected is None:
            api.close(child_handle)
            raise ConflictError(f"stage manifest changed: {stage.as_posix()}")
        retained_handles.append((child_handle, child_host, child_expected))
        _windows_validate_stage_tree(
            api,
            child_handle,
            child_host,
            relative.join(child.name),
            anchor,
            expected_by_path,
            seen,
            retained_handles,
            stage,
        )


def _windows_validate_stage_entry(
    api: _WindowsAPI,
    handle: int,
    host: str,
    expected: StageManifestEntry,
    anchor: str,
    stage: PathRef,
) -> None:
    attributes = api.attributes(handle, host)
    kind = _windows_kind_from_attributes(attributes)
    observed = StageManifestEntry(expected.relative_path, kind, api.revision(handle, host))
    if observed != expected:
        raise ConflictError(f"stage manifest changed: {stage.as_posix()}")
    if kind != EntryKind.SYMLINK:
        _windows_assert_contained(api, handle, host, anchor)


async def _stat_entry(host: AnyioPath) -> FileEntry:
    """Build a FileEntry from a host path (symlink-aware)."""
    lstat = await host.lstat()
    return _entry_from_stat(host.name, lstat)


def _entry_from_stat(
    name: str,
    lstat: os.stat_result,
    *,
    etag: str | None = None,
) -> FileEntry:
    mode = lstat.st_mode
    windows_attributes = getattr(lstat, "st_file_attributes", 0)
    if stat.S_ISLNK(mode) or windows_attributes & _WINDOWS_FILE_ATTRIBUTE_REPARSE_POINT:
        kind = EntryKind.SYMLINK
        size = lstat.st_size
    elif stat.S_ISDIR(mode):
        kind = EntryKind.DIRECTORY
        size = None
    else:
        kind = EntryKind.FILE
        size = lstat.st_size
    return FileEntry(
        name=name,
        kind=kind,
        size=size,
        modified=datetime.fromtimestamp(lstat.st_mtime, tz=UTC),
        etag=etag if etag is not None else _local_etag(lstat),
    )


def _local_etag(value: os.stat_result) -> str:
    """Opaque local revision token used to guard move-source deletion."""
    return ":".join(
        str(part)
        for part in (
            value.st_dev,
            value.st_ino,
            value.st_size,
            value.st_mtime_ns,
            value.st_ctime_ns,
        )
    )


def _local_stage_identity_token(revision: str) -> str:
    parts = revision.split(":")
    if parts[0] == "windows" and len(parts) >= 3:
        identity = ":".join(parts[:3])
    elif len(parts) >= 2:
        identity = ":".join(parts[:2])
    else:  # pragma: no cover - LocalFS creates every revision token itself
        raise ProviderError("invalid local revision token")
    return _STAGE_IDENTITY_PREFIX + identity


def _local_revision_matches(observed: str, expected: str) -> bool:
    if expected.startswith(_STAGE_IDENTITY_PREFIX):
        return _local_stage_identity_token(observed) == expected
    return observed == expected


def _validate_atomic_publish_kind(
    kind: EntryKind,
    *,
    descriptor_relative: bool,
) -> None:
    if kind == EntryKind.DIRECTORY and not _supports_atomic_directory_publish(
        descriptor_relative=descriptor_relative
    ):
        raise ProviderError("this platform lacks atomic no-replace directory publication")


def _validate_publish_source(
    observed: os.stat_result,
    expected_revision: str,
    path: PathRef,
    *,
    descriptor_relative: bool,
) -> None:
    if not _local_revision_matches(_local_etag(observed), expected_revision):
        raise ConflictError(f"stage changed: {path.as_posix()}")
    if stat.S_ISLNK(observed.st_mode):
        kind = EntryKind.SYMLINK
    elif stat.S_ISDIR(observed.st_mode):
        kind = EntryKind.DIRECTORY
    else:
        kind = EntryKind.FILE
    _validate_atomic_publish_kind(kind, descriptor_relative=descriptor_relative)


def _local_stable_identity(value: os.stat_result) -> tuple[int, int, int, int]:
    """Fields that remain stable when the same entry is renamed."""
    return (value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns)


async def _read_chunks_fd(fd: int, filename: str, chunk_size: int) -> AsyncGenerator[bytes, None]:
    """Async generator yielding ``chunk_size`` blocks from a local file."""
    try:
        async with aiofiles.open(fd, "rb", closefd=True) as fh:
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


def _supports_secure_dir_fd() -> bool:
    required = {os.open, os.stat, os.unlink, os.rename, os.mkdir, os.rmdir}
    return (
        os.name != "nt"
        and hasattr(os, "O_NOFOLLOW")
        and hasattr(os, "O_DIRECTORY")
        and required.issubset(os.supports_dir_fd)
        and os.listdir in os.supports_fd
    )


def _require_secure_dir_fd() -> None:
    if not _supports_secure_dir_fd():
        raise ProviderError(
            "rooted LocalFS requires secure relative filesystem operations on this platform"
        )


def _validate_relative(path: PathRef) -> None:
    if any(
        segment in {"", ".", ".."} or "/" in segment or "\\" in segment for segment in path.segments
    ):
        raise ProviderError(f"path {path.as_posix()!r} contains an unsafe segment")


def _open_nofollow(path: str, flags: int, *, dir_fd: int | None = None) -> int:
    nofollow = getattr(os, "O_NOFOLLOW", None)
    if nofollow is None:
        raise ProviderError("this platform does not support secure no-follow file opens")
    if dir_fd is not None and os.open not in os.supports_dir_fd:
        raise ProviderError("this platform does not support secure relative filesystem operations")
    return os.open(path, flags | nofollow, 0o666, dir_fd=dir_fd)


def _open_root(root: Path) -> int:
    _require_secure_dir_fd()
    return _open_nofollow(str(root), os.O_RDONLY | _O_DIRECTORY)


@contextmanager
def _rooted_parent(root: Path, path: PathRef) -> Iterator[tuple[int, str]]:
    _require_secure_dir_fd()
    _validate_relative(path)
    if path.is_root:
        raise ProviderError("the provider root has no parent entry")
    current = _open_root(root)
    try:
        for segment in path.segments[:-1]:
            following = _open_nofollow(
                segment,
                os.O_RDONLY | _O_DIRECTORY,
                dir_fd=current,
            )
            os.close(current)
            current = following
        yield current, path.name
    finally:
        os.close(current)


def _open_rooted_directory(root: Path, path: PathRef) -> int:
    _require_secure_dir_fd()
    _validate_relative(path)
    current = _open_root(root)
    try:
        for segment in path.segments:
            following = _open_nofollow(
                segment,
                os.O_RDONLY | _O_DIRECTORY,
                dir_fd=current,
            )
            os.close(current)
            current = following
        return current
    except BaseException:
        os.close(current)
        raise


def _rooted_open(root: Path, path: PathRef, flags: int) -> int:
    with _rooted_parent(root, path) as (parent_fd, name):
        return _open_nofollow(name, flags, dir_fd=parent_fd)


def _rooted_lstat(root: Path, path: PathRef) -> os.stat_result:
    if path.is_root:
        fd = _open_root(root)
        try:
            return os.fstat(fd)
        finally:
            os.close(fd)
    with _rooted_parent(root, path) as (parent_fd, name):
        return os.stat(name, dir_fd=parent_fd, follow_symlinks=False)


def _rooted_list(root: Path, path: PathRef) -> list[tuple[str, os.stat_result]]:
    directory_fd = _open_rooted_directory(root, path)
    try:
        rows: list[tuple[str, os.stat_result]] = []
        with os.scandir(directory_fd) as children:
            for child in children:
                try:
                    value = child.stat(follow_symlinks=False)
                except FileNotFoundError:
                    continue
                if len(rows) >= _MAX_LISTING_ENTRIES:
                    raise ProviderError(
                        f"local directory listing exceeded the listing safety limit "
                        f"of {_MAX_LISTING_ENTRIES} entries"
                    )
                rows.append((child.name, value))
        return rows
    finally:
        os.close(directory_fd)


def _rooted_mkdir(root: Path, path: PathRef) -> None:
    _require_secure_dir_fd()
    _validate_relative(path)
    current = _open_root(root)
    try:
        for segment in path.segments:
            with suppress(FileExistsError):
                os.mkdir(segment, dir_fd=current)
            following = _open_nofollow(
                segment,
                os.O_RDONLY | _O_DIRECTORY,
                dir_fd=current,
            )
            os.close(current)
            current = following
    finally:
        os.close(current)


def _rooted_claim_directory(root: Path, path: PathRef) -> str:
    with _rooted_parent(root, path) as (parent_fd, name):
        os.mkdir(name, mode=0o700, dir_fd=parent_fd)
        created = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
        return _local_etag(created)


def _rename_no_replace_at(src_fd: int, src: str, dst_fd: int, dst: str) -> None:
    """Atomically rename entries relative to already validated directories."""
    libc = ctypes.CDLL(None, use_errno=True)
    src_bytes = os.fsencode(src)
    dst_bytes = os.fsencode(dst)
    if hasattr(libc, "renameatx_np"):
        result = libc.renameatx_np(
            src_fd,
            src_bytes,
            dst_fd,
            dst_bytes,
            0x00000004,
        )  # RENAME_EXCL
    elif hasattr(libc, "renameat2"):
        result = libc.renameat2(
            src_fd,
            src_bytes,
            dst_fd,
            dst_bytes,
            0x1,
        )  # RENAME_NOREPLACE
    else:
        source = os.stat(src, dir_fd=src_fd, follow_symlinks=False)
        if stat.S_ISDIR(source.st_mode):
            raise ProviderError("this platform lacks atomic no-replace directory rename support")
        os.link(
            src,
            dst,
            src_dir_fd=src_fd,
            dst_dir_fd=dst_fd,
            follow_symlinks=False,
        )
        os.unlink(src, dir_fd=src_fd)
        return
    if result != 0:
        code = ctypes.get_errno()
        raise OSError(code, os.strerror(code), dst)


def _remove_tree_at(parent_fd: int, name: str, value: os.stat_result) -> None:
    if not stat.S_ISDIR(value.st_mode) or stat.S_ISLNK(value.st_mode):
        os.unlink(name, dir_fd=parent_fd)
        return
    directory_fd = _open_nofollow(
        name,
        os.O_RDONLY | _O_DIRECTORY,
        dir_fd=parent_fd,
    )
    try:
        for child in os.listdir(directory_fd):  # noqa: PTH208 - descriptor-relative API
            child_stat = os.stat(child, dir_fd=directory_fd, follow_symlinks=False)
            _remove_tree_at(directory_fd, child, child_stat)
    finally:
        os.close(directory_fd)
    os.rmdir(name, dir_fd=parent_fd)


def _restore_claim(parent_fd: int, quarantine: str, original: str) -> None:
    try:
        _rename_no_replace_at(parent_fd, quarantine, parent_fd, original)
    except OSError as restore_exc:
        raise ProviderError(
            "filesystem mutation failed and the claimed entry could not be restored from "
            f"{quarantine}: {restore_exc}"
        ) from restore_exc


def _rooted_delete(
    root: Path,
    path: PathRef,
    expected_etag: str | None,
) -> None:
    with _rooted_parent(root, path) as (parent_fd, name):
        observed = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
        if expected_etag is not None and not _local_revision_matches(
            _local_etag(observed), expected_etag
        ):
            raise ConflictError(f"source changed: {path.as_posix()}")
        quarantine = f".{name}.aws-tui-delete-{uuid4().hex}"
        _rename_no_replace_at(parent_fd, name, parent_fd, quarantine)
        claimed = os.stat(quarantine, dir_fd=parent_fd, follow_symlinks=False)
        if _local_stable_identity(claimed) != _local_stable_identity(observed):
            _restore_claim(parent_fd, quarantine, name)
            raise ConflictError(f"source changed: {path.as_posix()}")
        _remove_tree_at(parent_fd, quarantine, claimed)


def _rooted_delete_empty_directory(root: Path, path: PathRef) -> None:
    with _rooted_parent(root, path) as (parent_fd, name):
        observed = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
        quarantine = f".{name}.aws-tui-rmdir-{uuid4().hex}"
        _rename_no_replace_at(parent_fd, name, parent_fd, quarantine)
        claimed = os.stat(quarantine, dir_fd=parent_fd, follow_symlinks=False)
        if _local_stable_identity(claimed) != _local_stable_identity(observed):
            _restore_claim(parent_fd, quarantine, name)
            raise ConflictError(f"source changed: {path.as_posix()}")
        try:
            os.rmdir(quarantine, dir_fd=parent_fd)
        except BaseException:
            _restore_claim(parent_fd, quarantine, name)
            raise


def _rooted_rename(root: Path, src: PathRef, dst: PathRef) -> None:
    with (
        _rooted_parent(root, src) as (src_fd, src_name),
        _rooted_parent(root, dst) as (dst_fd, dst_name),
    ):
        _rename_no_replace_at(src_fd, src_name, dst_fd, dst_name)


def _rooted_atomic_publish_no_replace(
    root: Path,
    src: PathRef,
    dst: PathRef,
    expected_source_revision: str,
) -> str:
    with (
        _rooted_parent(root, src) as (src_fd, src_name),
        _rooted_parent(root, dst) as (dst_fd, dst_name),
    ):
        observed = os.stat(src_name, dir_fd=src_fd, follow_symlinks=False)
        _validate_publish_source(
            observed,
            expected_source_revision,
            src,
            descriptor_relative=True,
        )
        _rename_no_replace_at(src_fd, src_name, dst_fd, dst_name)
        published = os.stat(dst_name, dir_fd=dst_fd, follow_symlinks=False)
        return _local_etag(published)


def _kind_from_stat(value: os.stat_result) -> EntryKind:
    if stat.S_ISLNK(value.st_mode):
        return EntryKind.SYMLINK
    if stat.S_ISDIR(value.st_mode):
        return EntryKind.DIRECTORY
    return EntryKind.FILE


def _rooted_stage_manifest(
    parent_fd: int,
    name: str,
    relative: PathRef | None = None,
) -> tuple[StageManifestEntry, ...]:
    relative = PathRef(()) if relative is None else relative
    observed = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    kind = _kind_from_stat(observed)
    result = [StageManifestEntry(relative, kind, _local_etag(observed))]
    if kind != EntryKind.DIRECTORY:
        return tuple(result)
    directory_fd = _open_nofollow(
        name,
        os.O_RDONLY | _O_DIRECTORY,
        dir_fd=parent_fd,
    )
    try:
        for child in sorted(os.listdir(directory_fd)):  # noqa: PTH208
            result.extend(
                _rooted_stage_manifest(
                    directory_fd,
                    child,
                    relative.join(child),
                )
            )
    finally:
        os.close(directory_fd)
    return tuple(result)


def _unrooted_stage_manifest(
    path: Path,
    relative: PathRef | None = None,
) -> tuple[StageManifestEntry, ...]:
    relative = PathRef(()) if relative is None else relative
    observed = path.lstat()
    kind = _kind_from_stat(observed)
    result = [StageManifestEntry(relative, kind, _local_etag(observed))]
    if kind == EntryKind.DIRECTORY:
        for child in sorted(path.iterdir(), key=lambda item: item.name):
            result.extend(_unrooted_stage_manifest(child, relative.join(child.name)))
    return tuple(result)


def _validate_stage_manifest(
    observed: tuple[StageManifestEntry, ...],
    expected: tuple[StageManifestEntry, ...],
    path: PathRef,
) -> None:
    if set(observed) != set(expected):
        raise ProviderError(f"stage manifest changed: {path.as_posix()}")


def _rooted_atomic_publish_directory_no_replace(
    root: Path,
    src: PathRef,
    dst: PathRef,
    expected_manifest: tuple[StageManifestEntry, ...],
) -> str:
    with (
        _rooted_parent(root, src) as (src_fd, src_name),
        _rooted_parent(root, dst) as (dst_fd, dst_name),
    ):
        # POSIX has no revision-conditioned rename. CrossFs places this entry in
        # an exclusive 0700 container; this synchronous descriptor operation
        # validates that private tree and performs the atomic no-replace rename.
        observed = _rooted_stage_manifest(src_fd, src_name)
        _validate_stage_manifest(observed, expected_manifest, src)
        _rename_no_replace_at(src_fd, src_name, dst_fd, dst_name)
        published = os.stat(dst_name, dir_fd=dst_fd, follow_symlinks=False)
        return _local_etag(published)


def _unrooted_claim_directory(path: str) -> str:
    host = Path(path)
    host.mkdir(mode=0o700)
    created = host.lstat()
    return _local_etag(created)


def _unrooted_atomic_publish_no_replace(
    src: str,
    dst: str,
    expected_source_revision: str,
) -> str:
    observed = os.lstat(src)
    _validate_publish_source(
        observed,
        expected_source_revision,
        PathRef.from_posix(src),
        descriptor_relative=False,
    )
    _rename_no_replace(src, dst)
    return _local_etag(os.lstat(dst))


def _unrooted_atomic_publish_directory_no_replace(
    src: str,
    dst: str,
    expected_manifest: tuple[StageManifestEntry, ...],
) -> str:
    source = Path(src)
    observed = _unrooted_stage_manifest(source)
    _validate_stage_manifest(observed, expected_manifest, PathRef.from_posix(src))
    _rename_no_replace(src, dst)
    return _local_etag(os.lstat(dst))


def _rename_no_replace(src: str, dst: str) -> None:
    """Atomically rename without replacing an existing destination."""
    if os.name == "nt":
        Path(src).rename(dst)
        return

    libc = ctypes.CDLL(None, use_errno=True)
    src_bytes = os.fsencode(src)
    dst_bytes = os.fsencode(dst)
    if hasattr(libc, "renamex_np"):
        result = libc.renamex_np(src_bytes, dst_bytes, 0x00000004)  # RENAME_EXCL
    elif hasattr(libc, "renameat2"):
        result = libc.renameat2(-100, src_bytes, -100, dst_bytes, 0x1)  # RENAME_NOREPLACE
    else:
        # File fallback remains atomic through link creation. Directory
        # support requires a platform rename-with-flags primitive.
        if not Path(src).is_dir():
            os.link(src, dst, follow_symlinks=False)
            Path(src).unlink()
            return
        if os.path.lexists(dst):
            raise FileExistsError(errno.EEXIST, os.strerror(errno.EEXIST), dst)
        Path(src).rename(dst)
        return
    if result != 0:
        code = ctypes.get_errno()
        raise OSError(code, os.strerror(code), dst)


def _supports_atomic_directory_publish(*, descriptor_relative: bool) -> bool:
    if _WINDOWS:
        return True
    libc = ctypes.CDLL(None)
    if descriptor_relative:
        return hasattr(libc, "renameatx_np") or hasattr(libc, "renameat2")
    return hasattr(libc, "renamex_np") or hasattr(libc, "renameat2")


def _claim_and_remove_empty_directory(src: str, quarantine: str) -> None:
    """Atomically claim ``src`` and remove it only when it is empty."""
    _rename_no_replace(src, quarantine)
    try:
        Path(quarantine).rmdir()
    except BaseException as exc:
        try:
            _rename_no_replace(quarantine, src)
        except OSError as restore_exc:
            raise ProviderError(
                "empty-directory deletion failed and the claimed directory could not be "
                f"restored from {quarantine}: {restore_exc}"
            ) from restore_exc
        raise exc


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
