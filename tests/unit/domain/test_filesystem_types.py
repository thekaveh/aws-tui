"""Unit tests for the domain filesystem types + Protocol + InMemoryFS contract."""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest

from aws_tui.demo.in_memory_fs import InMemoryFS
from aws_tui.domain.filesystem import (
    ConflictError,
    EntryKind,
    FileSystemProvider,
    NotFoundError,
    PathRef,
    ProviderError,
    TransferProgress,
)

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# PathRef
# ---------------------------------------------------------------------------


def test_path_ref_root_round_trip() -> None:
    root = PathRef.from_posix("/")
    assert root.is_root is True
    assert root.as_posix() == "/"
    assert root.parent() == root


def test_path_ref_roundtrip_nested() -> None:
    p = PathRef.from_posix("/a/b/c")
    assert p.as_posix() == "/a/b/c"
    assert p.segments == ("a", "b", "c")
    assert p.parent().as_posix() == "/a/b"
    assert p.parent().parent().as_posix() == "/a"
    assert p.parent().parent().parent().is_root is True


def test_path_ref_join_splits_slashes() -> None:
    p = PathRef.from_posix("/a").join("b/c", "d")
    assert p.as_posix() == "/a/b/c/d"


def test_path_ref_strips_empty_segments() -> None:
    assert PathRef.from_posix("//a///b//").as_posix() == "/a/b"


def test_path_ref_name_and_with_name() -> None:
    p = PathRef.from_posix("/a/b.txt")
    assert p.name == "b.txt"
    assert p.with_name("c.txt").as_posix() == "/a/c.txt"


def test_path_ref_immutable_join_returns_new() -> None:
    p = PathRef.from_posix("/a")
    q = p.join("b")
    assert p.as_posix() == "/a"
    assert q.as_posix() == "/a/b"


def test_path_ref_str_is_posix() -> None:
    assert str(PathRef.from_posix("/x/y")) == "/x/y"


# ---------------------------------------------------------------------------
# Protocol sanity: an implementor type-checks structurally
# ---------------------------------------------------------------------------


def test_protocol_is_runtime_checkable() -> None:
    fs = InMemoryFS()
    assert isinstance(fs, FileSystemProvider)


# ---------------------------------------------------------------------------
# TransferProgress dataclass
# ---------------------------------------------------------------------------


def test_transfer_progress_frozen() -> None:
    tp = TransferProgress(bytes_transferred=10, bytes_total=100)
    with pytest.raises(AttributeError):
        tp.bytes_transferred = 20  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Generic provider contract — InMemoryFS edition
# ---------------------------------------------------------------------------


async def _drain(it: AsyncIterator[bytes]) -> bytes:
    out = bytearray()
    async for chunk in it:
        out.extend(chunk)
    return bytes(out)


async def _agen(blobs: list[bytes]) -> AsyncIterator[bytes]:
    for b in blobs:
        yield b


async def test_inmemory_list_root_empty_initially() -> None:
    fs = InMemoryFS()
    assert await fs.list(PathRef(())) == []


async def test_inmemory_mkdir_then_list() -> None:
    fs = InMemoryFS()
    await fs.mkdir(PathRef.from_posix("/foo"))
    listing = await fs.list(PathRef(()))
    assert len(listing) == 1
    assert listing[0].name == "foo"
    assert listing[0].kind == EntryKind.DIRECTORY


async def test_inmemory_write_then_read_roundtrip() -> None:
    fs = InMemoryFS()
    await fs.write_stream(PathRef.from_posix("/a.txt"), _agen([b"hello ", b"world"]))
    content = await _drain(await fs.read_stream(PathRef.from_posix("/a.txt")))
    assert content == b"hello world"


async def test_inmemory_stat_file_and_dir() -> None:
    fs = InMemoryFS()
    await fs.mkdir(PathRef.from_posix("/d"))
    await fs.write_stream(PathRef.from_posix("/d/x.bin"), _agen([b"abc"]))
    d_stat = await fs.stat(PathRef.from_posix("/d"))
    assert d_stat.kind == EntryKind.DIRECTORY
    assert d_stat.size is None
    f_stat = await fs.stat(PathRef.from_posix("/d/x.bin"))
    assert f_stat.kind == EntryKind.FILE
    assert f_stat.size == 3


async def test_inmemory_delete_file() -> None:
    fs = InMemoryFS()
    p = PathRef.from_posix("/a.txt")
    await fs.write_stream(p, _agen([b"x"]))
    await fs.delete(p)
    with pytest.raises(NotFoundError):
        await fs.stat(p)


async def test_inmemory_delete_directory_recursive() -> None:
    fs = InMemoryFS()
    await fs.mkdir(PathRef.from_posix("/d"))
    await fs.write_stream(PathRef.from_posix("/d/x"), _agen([b"x"]))
    await fs.write_stream(PathRef.from_posix("/d/y"), _agen([b"y"]))
    await fs.delete(PathRef.from_posix("/d"))
    with pytest.raises(NotFoundError):
        await fs.stat(PathRef.from_posix("/d"))
    with pytest.raises(NotFoundError):
        await fs.stat(PathRef.from_posix("/d/x"))


async def test_inmemory_rename_file() -> None:
    fs = InMemoryFS()
    src = PathRef.from_posix("/a.txt")
    dst = PathRef.from_posix("/b.txt")
    await fs.write_stream(src, _agen([b"hi"]))
    await fs.rename(src, dst)
    with pytest.raises(NotFoundError):
        await fs.stat(src)
    assert (await fs.stat(dst)).size == 2


async def test_inmemory_not_found_on_missing() -> None:
    fs = InMemoryFS()
    with pytest.raises(NotFoundError):
        await fs.stat(PathRef.from_posix("/nope"))


async def test_inmemory_progress_callback_invoked() -> None:
    fs = InMemoryFS()
    seen: list[int] = []

    def cb(p: TransferProgress) -> None:
        seen.append(p.bytes_transferred)

    await fs.write_stream(
        PathRef.from_posix("/p"),
        _agen([b"ab", b"cd", b"ef"]),
        total_size=6,
        progress=cb,
    )
    assert seen == [2, 4, 6]


async def test_inmemory_write_into_missing_parent_raises() -> None:
    fs = InMemoryFS()
    with pytest.raises(NotFoundError):
        await fs.write_stream(PathRef.from_posix("/missing/x"), _agen([b"x"]))


async def test_inmemory_listing_rejects_results_beyond_safety_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from aws_tui.demo import in_memory_fs

    monkeypatch.setattr(in_memory_fs, "_MAX_LISTING_ENTRIES", 1, raising=False)
    fs = InMemoryFS()
    await fs.write_stream(PathRef(("one",)), _agen([b"1"]))
    await fs.write_stream(PathRef(("two",)), _agen([b"2"]))

    with pytest.raises(ProviderError, match="listing safety limit"):
        await fs.list(PathRef(()))


async def test_inmemory_write_rejects_known_oversized_object_before_reading(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from aws_tui.demo import in_memory_fs

    monkeypatch.setattr(in_memory_fs, "_MAX_OBJECT_BYTES", 2, raising=False)
    read = False

    async def source() -> AsyncIterator[bytes]:
        nonlocal read
        read = True
        yield b"abc"

    fs = InMemoryFS()
    with pytest.raises(ProviderError, match="object safety limit"):
        await fs.write_stream(PathRef(("large",)), source(), total_size=3)

    assert not read
    with pytest.raises(NotFoundError):
        await fs.stat(PathRef(("large",)))


async def test_inmemory_write_rejects_unknown_oversized_object_without_mutation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from aws_tui.demo import in_memory_fs

    monkeypatch.setattr(in_memory_fs, "_MAX_OBJECT_BYTES", 2, raising=False)
    fs = InMemoryFS()

    with pytest.raises(ProviderError, match="object safety limit"):
        await fs.write_stream(PathRef(("large",)), _agen([b"ab", b"c"]))

    with pytest.raises(NotFoundError):
        await fs.stat(PathRef(("large",)))


async def test_inmemory_write_enforces_aggregate_storage_budget_on_overwrite(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from aws_tui.demo import in_memory_fs

    monkeypatch.setattr(in_memory_fs, "_MAX_OBJECT_BYTES", 10, raising=False)
    monkeypatch.setattr(in_memory_fs, "_MAX_STORAGE_BYTES", 4, raising=False)
    fs = InMemoryFS()
    await fs.write_stream(PathRef(("first",)), _agen([b"abc"]))

    with pytest.raises(ProviderError, match="storage safety limit"):
        await fs.write_stream(PathRef(("second",)), _agen([b"de"]))
    await fs.write_stream(PathRef(("first",)), _agen([b"wxyz"]), overwrite=True)

    assert await _drain(await fs.read_stream(PathRef(("first",)))) == b"wxyz"
    with pytest.raises(NotFoundError):
        await fs.stat(PathRef(("second",)))


async def test_inmemory_rename_conflict() -> None:
    fs = InMemoryFS()
    await fs.write_stream(PathRef.from_posix("/a"), _agen([b"a"]))
    await fs.write_stream(PathRef.from_posix("/b"), _agen([b"b"]))
    with pytest.raises(ConflictError):
        await fs.rename(PathRef.from_posix("/a"), PathRef.from_posix("/b"))
