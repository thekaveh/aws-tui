"""Unit tests for LocalFS provider."""

from __future__ import annotations

import os
import stat
import sys
from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from aws_tui.domain.filesystem import (
    ConflictError,
    EntryKind,
    NotFoundError,
    PathRef,
    PermissionDeniedError,
    TransferProgress,
)
from aws_tui.domain.local_fs import LocalFS

pytestmark = pytest.mark.unit


async def _drain(it: AsyncIterator[bytes]) -> bytes:
    out = bytearray()
    async for chunk in it:
        out.extend(chunk)
    return bytes(out)


async def _agen(blobs: list[bytes]) -> AsyncIterator[bytes]:
    for b in blobs:
        yield b


def _make_fs(tmp_path: Path) -> LocalFS:
    return LocalFS(root=tmp_path)


# ---------------------------------------------------------------------------
# list / stat
# ---------------------------------------------------------------------------


async def test_list_empty_dir(tmp_path: Path) -> None:
    fs = _make_fs(tmp_path)
    assert await fs.list(PathRef(())) == []


async def test_list_with_file_and_dir(tmp_path: Path) -> None:
    (tmp_path / "f.txt").write_bytes(b"abc")
    (tmp_path / "d").mkdir()
    fs = _make_fs(tmp_path)
    entries = await fs.list(PathRef(()))
    names = sorted(e.name for e in entries)
    assert names == ["d", "f.txt"]
    kinds = {e.name: e.kind for e in entries}
    assert kinds["d"] == EntryKind.DIRECTORY
    assert kinds["f.txt"] == EntryKind.FILE
    sizes = {e.name: e.size for e in entries}
    assert sizes["f.txt"] == 3
    assert sizes["d"] is None


async def test_list_missing_dir_raises_not_found(tmp_path: Path) -> None:
    fs = _make_fs(tmp_path)
    with pytest.raises(NotFoundError):
        await fs.list(PathRef.from_posix("/missing"))


async def test_stat_file(tmp_path: Path) -> None:
    (tmp_path / "x").write_bytes(b"x")
    fs = _make_fs(tmp_path)
    entry = await fs.stat(PathRef.from_posix("/x"))
    assert entry.kind == EntryKind.FILE
    assert entry.size == 1
    assert entry.modified is not None


async def test_stat_missing_raises(tmp_path: Path) -> None:
    fs = _make_fs(tmp_path)
    with pytest.raises(NotFoundError):
        await fs.stat(PathRef.from_posix("/nope"))


# ---------------------------------------------------------------------------
# mkdir / delete / rename
# ---------------------------------------------------------------------------


async def test_mkdir_creates_nested(tmp_path: Path) -> None:
    fs = _make_fs(tmp_path)
    await fs.mkdir(PathRef.from_posix("/a/b/c"))
    assert (tmp_path / "a" / "b" / "c").is_dir()


async def test_mkdir_idempotent_for_dirs(tmp_path: Path) -> None:
    fs = _make_fs(tmp_path)
    p = PathRef.from_posix("/x")
    await fs.mkdir(p)
    await fs.mkdir(p)  # exist_ok=True ⇒ no raise


async def test_mkdir_conflicts_with_file(tmp_path: Path) -> None:
    (tmp_path / "f").write_bytes(b"x")
    fs = _make_fs(tmp_path)
    with pytest.raises(ConflictError):
        await fs.mkdir(PathRef.from_posix("/f"))


async def test_delete_file(tmp_path: Path) -> None:
    p = tmp_path / "f"
    p.write_bytes(b"x")
    fs = _make_fs(tmp_path)
    await fs.delete(PathRef.from_posix("/f"))
    assert not p.exists()


async def test_delete_directory_recursive(tmp_path: Path) -> None:
    (tmp_path / "d" / "e").mkdir(parents=True)
    (tmp_path / "d" / "x.txt").write_bytes(b"x")
    (tmp_path / "d" / "e" / "y.txt").write_bytes(b"y")
    fs = _make_fs(tmp_path)
    await fs.delete(PathRef.from_posix("/d"))
    assert not (tmp_path / "d").exists()


async def test_delete_missing_raises(tmp_path: Path) -> None:
    fs = _make_fs(tmp_path)
    with pytest.raises(NotFoundError):
        await fs.delete(PathRef.from_posix("/nope"))


async def test_rename_file(tmp_path: Path) -> None:
    (tmp_path / "a").write_bytes(b"x")
    fs = _make_fs(tmp_path)
    await fs.rename(PathRef.from_posix("/a"), PathRef.from_posix("/b"))
    assert (tmp_path / "b").read_bytes() == b"x"
    assert not (tmp_path / "a").exists()


async def test_rename_into_subdir(tmp_path: Path) -> None:
    (tmp_path / "a").write_bytes(b"x")
    (tmp_path / "sub").mkdir()
    fs = _make_fs(tmp_path)
    await fs.rename(PathRef.from_posix("/a"), PathRef.from_posix("/sub/b"))
    assert (tmp_path / "sub" / "b").read_bytes() == b"x"


async def test_rename_conflict(tmp_path: Path) -> None:
    (tmp_path / "a").write_bytes(b"x")
    (tmp_path / "b").write_bytes(b"y")
    fs = _make_fs(tmp_path)
    with pytest.raises(ConflictError):
        await fs.rename(PathRef.from_posix("/a"), PathRef.from_posix("/b"))


# ---------------------------------------------------------------------------
# Streaming I/O
# ---------------------------------------------------------------------------


async def test_write_then_read_small(tmp_path: Path) -> None:
    fs = _make_fs(tmp_path)
    await fs.write_stream(PathRef.from_posix("/h.txt"), _agen([b"hi"]))
    out = await _drain(await fs.read_stream(PathRef.from_posix("/h.txt")))
    assert out == b"hi"


async def test_write_then_read_roundtrip_16mb(tmp_path: Path) -> None:
    """16 MiB round-trip — exercises multi-chunk read path."""
    fs = _make_fs(tmp_path)
    payload = os.urandom(16 * 1024 * 1024)

    async def src() -> AsyncIterator[bytes]:
        for i in range(0, len(payload), 1 << 20):
            yield payload[i : i + (1 << 20)]

    await fs.write_stream(PathRef.from_posix("/big.bin"), src(), total_size=len(payload))
    out = await _drain(
        await fs.read_stream(PathRef.from_posix("/big.bin"), chunk_size=4 * 1024 * 1024)
    )
    assert out == payload


async def test_progress_callback_called(tmp_path: Path) -> None:
    fs = _make_fs(tmp_path)
    seen: list[int] = []

    def cb(p: TransferProgress) -> None:
        seen.append(p.bytes_transferred)

    await fs.write_stream(
        PathRef.from_posix("/p"),
        _agen([b"abc", b"def", b"gh"]),
        total_size=8,
        progress=cb,
    )
    assert seen == [3, 6, 8]


async def test_read_missing_raises(tmp_path: Path) -> None:
    fs = _make_fs(tmp_path)
    with pytest.raises(NotFoundError):
        await fs.read_stream(PathRef.from_posix("/nope"))


# ---------------------------------------------------------------------------
# Symlinks
# ---------------------------------------------------------------------------


@pytest.mark.skipif(sys.platform == "win32", reason="symlinks need admin on Windows")
async def test_symlink_reported_with_symlink_kind(tmp_path: Path) -> None:
    target = tmp_path / "target.txt"
    target.write_bytes(b"x")
    link = tmp_path / "link.txt"
    link.symlink_to(target)
    fs = _make_fs(tmp_path)
    entries = await fs.list(PathRef(()))
    by_name = {e.name: e for e in entries}
    assert by_name["link.txt"].kind == EntryKind.SYMLINK


# ---------------------------------------------------------------------------
# Permissions
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    sys.platform == "win32" or os.geteuid() == 0,
    reason="root bypasses permission bits; Windows perms differ",
)
async def test_permission_denied_on_unreadable_dir(tmp_path: Path) -> None:
    secret = tmp_path / "secret"
    secret.mkdir()
    (secret / "inside.txt").write_bytes(b"x")
    secret.chmod(0)
    try:
        fs = _make_fs(tmp_path)
        with pytest.raises(PermissionDeniedError):
            await fs.list(PathRef.from_posix("/secret"))
    finally:
        # Restore so pytest can clean up tmp_path.
        secret.chmod(stat.S_IRWXU)
