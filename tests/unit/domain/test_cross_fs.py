"""Unit tests for CrossFsCopy / CrossFsMove across provider pairs."""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import aioboto3
import pytest
from moto.server import ThreadedMotoServer

from aws_tui.domain.cross_fs import ConflictResolution, CrossFsCopy, CrossFsMove
from aws_tui.domain.filesystem import (
    ConflictError,
    FileSystemProvider,
    NotFoundError,
    PathRef,
    TransferProgress,
)
from aws_tui.domain.local_fs import LocalFS
from aws_tui.domain.s3_fs import S3FS
from tests.unit.domain._in_memory_fs import InMemoryFS

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _agen(blobs: list[bytes]) -> AsyncIterator[bytes]:
    for b in blobs:
        yield b


async def _drain(it: AsyncIterator[bytes]) -> bytes:
    out = bytearray()
    async for chunk in it:
        out.extend(chunk)
    return bytes(out)


async def _put_file(fs: FileSystemProvider, path: PathRef, data: bytes) -> None:
    await fs.write_stream(path, _agen([data]))


async def _read_file(fs: FileSystemProvider, path: PathRef) -> bytes:
    return await _drain(await fs.read_stream(path))


# ---------------------------------------------------------------------------
# Moto fixtures (shared with other domain tests)
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def moto_server() -> Iterator[str]:
    server = ThreadedMotoServer(port=0)
    server.start()
    host, port = server.get_host_and_port()
    yield f"http://{host}:{port}"
    server.stop()


@pytest.fixture
def s3_endpoint(moto_server: str, monkeypatch: pytest.MonkeyPatch) -> str:
    import urllib.request

    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    urllib.request.urlopen(
        urllib.request.Request(f"{moto_server}/moto-api/reset", method="POST")
    ).read()
    return moto_server


def _s3_session() -> aioboto3.Session:
    return aioboto3.Session(
        region_name="us-east-1",
        aws_access_key_id="testing",
        aws_secret_access_key="testing",
    )


async def _make_s3fs(endpoint: str, bucket: str) -> S3FS:
    async with _s3_session().client("s3", endpoint_url=endpoint) as s3:
        await s3.create_bucket(Bucket=bucket)
    return S3FS(
        session=_s3_session(),
        bucket=bucket,
        endpoint_url=endpoint,
        force_path_style=True,
    )


# ---------------------------------------------------------------------------
# InMemory ↔ InMemory
# ---------------------------------------------------------------------------


async def test_inmem_to_inmem_file_copy() -> None:
    src = InMemoryFS()
    dst = InMemoryFS()
    await _put_file(src, PathRef.from_posix("/a.txt"), b"hello")
    copier = CrossFsCopy(source=src, destination=dst)
    await copier.copy(PathRef.from_posix("/a.txt"), PathRef.from_posix("/a.txt"))
    assert await _read_file(dst, PathRef.from_posix("/a.txt")) == b"hello"


async def test_inmem_directory_copy_recursive() -> None:
    src = InMemoryFS()
    dst = InMemoryFS()
    await src.mkdir(PathRef.from_posix("/d"))
    await src.mkdir(PathRef.from_posix("/d/sub"))
    await _put_file(src, PathRef.from_posix("/d/a"), b"A")
    await _put_file(src, PathRef.from_posix("/d/sub/b"), b"B")
    copier = CrossFsCopy(source=src, destination=dst)
    await copier.copy(PathRef.from_posix("/d"), PathRef.from_posix("/d"))
    assert await _read_file(dst, PathRef.from_posix("/d/a")) == b"A"
    assert await _read_file(dst, PathRef.from_posix("/d/sub/b")) == b"B"


# ---------------------------------------------------------------------------
# LocalFS ↔ LocalFS
# ---------------------------------------------------------------------------


async def test_local_to_local_roundtrip(tmp_path: Path) -> None:
    src_root = tmp_path / "src"
    dst_root = tmp_path / "dst"
    src_root.mkdir()
    dst_root.mkdir()
    (src_root / "hi.txt").write_bytes(b"hello")

    src = LocalFS(root=src_root)
    dst = LocalFS(root=dst_root)
    copier = CrossFsCopy(source=src, destination=dst)
    await copier.copy(PathRef.from_posix("/hi.txt"), PathRef.from_posix("/hi.txt"))
    assert (dst_root / "hi.txt").read_bytes() == b"hello"


# ---------------------------------------------------------------------------
# LocalFS ↔ S3FS (moto)
# ---------------------------------------------------------------------------


async def test_local_to_s3_roundtrip(s3_endpoint: str, tmp_path: Path) -> None:
    local = LocalFS(root=tmp_path)
    (tmp_path / "x.txt").write_bytes(b"hello-s3")
    s3 = await _make_s3fs(s3_endpoint, "destbucket")
    copier = CrossFsCopy(source=local, destination=s3)
    await copier.copy(PathRef.from_posix("/x.txt"), PathRef.from_posix("/x.txt"))
    assert await _read_file(s3, PathRef.from_posix("/x.txt")) == b"hello-s3"


async def test_s3_to_local_roundtrip(s3_endpoint: str, tmp_path: Path) -> None:
    s3 = await _make_s3fs(s3_endpoint, "srcbucket")
    await _put_file(s3, PathRef.from_posix("/y.txt"), b"hello-local")
    local = LocalFS(root=tmp_path)
    copier = CrossFsCopy(source=s3, destination=local)
    await copier.copy(PathRef.from_posix("/y.txt"), PathRef.from_posix("/y.txt"))
    assert (tmp_path / "y.txt").read_bytes() == b"hello-local"


async def test_s3_to_s3_roundtrip(s3_endpoint: str) -> None:
    src = await _make_s3fs(s3_endpoint, "fromb")
    dst = await _make_s3fs(s3_endpoint, "tob")
    await _put_file(src, PathRef.from_posix("/k"), b"cross-bucket")
    copier = CrossFsCopy(source=src, destination=dst)
    await copier.copy(PathRef.from_posix("/k"), PathRef.from_posix("/k"))
    assert await _read_file(dst, PathRef.from_posix("/k")) == b"cross-bucket"


# ---------------------------------------------------------------------------
# Conflict resolution
# ---------------------------------------------------------------------------


async def test_conflict_error_raises() -> None:
    src = InMemoryFS()
    dst = InMemoryFS()
    await _put_file(src, PathRef.from_posix("/a"), b"src")
    await _put_file(dst, PathRef.from_posix("/a"), b"dst")
    copier = CrossFsCopy(source=src, destination=dst)
    with pytest.raises(ConflictError):
        await copier.copy(
            PathRef.from_posix("/a"),
            PathRef.from_posix("/a"),
            on_conflict=ConflictResolution.ERROR,
        )


async def test_conflict_overwrite_replaces() -> None:
    src = InMemoryFS()
    dst = InMemoryFS()
    await _put_file(src, PathRef.from_posix("/a"), b"src")
    await _put_file(dst, PathRef.from_posix("/a"), b"dst")
    copier = CrossFsCopy(source=src, destination=dst)
    await copier.copy(
        PathRef.from_posix("/a"),
        PathRef.from_posix("/a"),
        on_conflict=ConflictResolution.OVERWRITE,
    )
    assert await _read_file(dst, PathRef.from_posix("/a")) == b"src"


async def test_conflict_skip_no_op() -> None:
    src = InMemoryFS()
    dst = InMemoryFS()
    await _put_file(src, PathRef.from_posix("/a"), b"src")
    await _put_file(dst, PathRef.from_posix("/a"), b"dst")
    copier = CrossFsCopy(source=src, destination=dst)
    await copier.copy(
        PathRef.from_posix("/a"),
        PathRef.from_posix("/a"),
        on_conflict=ConflictResolution.SKIP,
    )
    assert await _read_file(dst, PathRef.from_posix("/a")) == b"dst"


async def test_conflict_rename_appends_suffix() -> None:
    src = InMemoryFS()
    dst = InMemoryFS()
    await _put_file(src, PathRef.from_posix("/a.txt"), b"src")
    await _put_file(dst, PathRef.from_posix("/a.txt"), b"dst")
    copier = CrossFsCopy(source=src, destination=dst)
    await copier.copy(
        PathRef.from_posix("/a.txt"),
        PathRef.from_posix("/a.txt"),
        on_conflict=ConflictResolution.RENAME,
    )
    assert await _read_file(dst, PathRef.from_posix("/a.txt")) == b"dst"
    assert await _read_file(dst, PathRef.from_posix("/a (1).txt")) == b"src"


# ---------------------------------------------------------------------------
# Progress + Move
# ---------------------------------------------------------------------------


async def test_progress_monotonic() -> None:
    src = InMemoryFS()
    dst = InMemoryFS()
    await _put_file(src, PathRef.from_posix("/big"), b"x" * 1024)
    copier = CrossFsCopy(source=src, destination=dst)
    seen: list[int] = []

    def cb(p: TransferProgress) -> None:
        seen.append(p.bytes_transferred)

    await copier.copy(PathRef.from_posix("/big"), PathRef.from_posix("/big"), progress=cb)
    assert seen, "progress should fire"
    assert seen == sorted(seen)  # monotonic non-decreasing


async def test_move_deletes_source() -> None:
    src = InMemoryFS()
    dst = InMemoryFS()
    await _put_file(src, PathRef.from_posix("/a"), b"hi")
    mover = CrossFsMove(source=src, destination=dst)
    await mover.move(PathRef.from_posix("/a"), PathRef.from_posix("/a"))
    assert await _read_file(dst, PathRef.from_posix("/a")) == b"hi"
    with pytest.raises(NotFoundError):
        await src.stat(PathRef.from_posix("/a"))


async def test_move_does_not_delete_on_conflict() -> None:
    src = InMemoryFS()
    dst = InMemoryFS()
    await _put_file(src, PathRef.from_posix("/a"), b"hi")
    await _put_file(dst, PathRef.from_posix("/a"), b"existing")
    mover = CrossFsMove(source=src, destination=dst)
    with pytest.raises(ConflictError):
        await mover.move(PathRef.from_posix("/a"), PathRef.from_posix("/a"))
    # Source must still exist because the conflict raised before delete.
    assert (await src.stat(PathRef.from_posix("/a"))).size == 2
