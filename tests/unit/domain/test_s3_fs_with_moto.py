"""Unit tests for S3FS, against an in-process moto threaded server.

The shared ``moto_server`` + ``s3_endpoint`` fixtures live in
``tests/unit/domain/conftest.py`` so the three moto-backed suites
(this one, ``test_s3_fs_bucketless_ops.py``, ``test_cross_fs.py``)
share one moto process per pytest run.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator

import aioboto3
import pytest

from aws_tui.domain.filesystem import (
    EntryKind,
    NotFoundError,
    PathRef,
    TransferProgress,
)
from aws_tui.domain.s3_fs import S3FS

pytestmark = pytest.mark.unit


def _session() -> aioboto3.Session:
    return aioboto3.Session(
        region_name="us-east-1",
        aws_access_key_id="testing",
        aws_secret_access_key="testing",
    )


def _fs(endpoint: str, *, bucket: str | None) -> S3FS:
    return S3FS(
        session=_session(),
        bucket=bucket,
        endpoint_url=endpoint,
        force_path_style=True,
    )


async def _drain(it: AsyncIterator[bytes]) -> bytes:
    out = bytearray()
    async for chunk in it:
        out.extend(chunk)
    return bytes(out)


async def _agen(blobs: list[bytes]) -> AsyncIterator[bytes]:
    for b in blobs:
        yield b


async def _make_bucket(endpoint: str, name: str) -> None:
    async with _session().client("s3", endpoint_url=endpoint) as s3:
        await s3.create_bucket(Bucket=name)


async def _put(endpoint: str, bucket: str, key: str, body: bytes) -> None:
    async with _session().client("s3", endpoint_url=endpoint) as s3:
        await s3.put_object(Bucket=bucket, Key=key, Body=body)


# ---------------------------------------------------------------------------
# Service root: list buckets
# ---------------------------------------------------------------------------


async def test_list_buckets_at_service_root(s3_endpoint: str) -> None:
    await _make_bucket(s3_endpoint, "alpha-bucket")
    await _make_bucket(s3_endpoint, "beta-bucket")
    fs = _fs(s3_endpoint, bucket=None)
    entries = await fs.list(PathRef(()))
    names = [e.name for e in entries]
    assert names == ["alpha-bucket", "beta-bucket"]
    assert all(e.kind == EntryKind.DIRECTORY for e in entries)


# ---------------------------------------------------------------------------
# Listing within a bucket
# ---------------------------------------------------------------------------


async def test_list_empty_bucket(s3_endpoint: str) -> None:
    await _make_bucket(s3_endpoint, "mybkt")
    fs = _fs(s3_endpoint, bucket="mybkt")
    assert await fs.list(PathRef(())) == []


async def test_list_with_objects_and_common_prefixes(s3_endpoint: str) -> None:
    await _make_bucket(s3_endpoint, "mybkt")
    await _put(s3_endpoint, "mybkt", "a.txt", b"a")
    await _put(s3_endpoint, "mybkt", "d1/inside.txt", b"x")
    await _put(s3_endpoint, "mybkt", "d1/inside2.txt", b"y")
    await _put(s3_endpoint, "mybkt", "d2/inside.txt", b"z")
    fs = _fs(s3_endpoint, bucket="mybkt")
    entries = await fs.list(PathRef(()))
    by_name = {e.name: e for e in entries}
    assert set(by_name) == {"a.txt", "d1", "d2"}
    assert by_name["a.txt"].kind == EntryKind.FILE
    assert by_name["a.txt"].size == 1
    assert by_name["d1"].kind == EntryKind.DIRECTORY


async def test_list_subprefix(s3_endpoint: str) -> None:
    await _make_bucket(s3_endpoint, "mybkt")
    await _put(s3_endpoint, "mybkt", "d/x.txt", b"x")
    await _put(s3_endpoint, "mybkt", "d/y.txt", b"y")
    fs = _fs(s3_endpoint, bucket="mybkt")
    entries = await fs.list(PathRef.from_posix("/d"))
    assert sorted(e.name for e in entries) == ["x.txt", "y.txt"]


# ---------------------------------------------------------------------------
# stat
# ---------------------------------------------------------------------------


async def test_stat_object(s3_endpoint: str) -> None:
    await _make_bucket(s3_endpoint, "mybkt")
    await _put(s3_endpoint, "mybkt", "k", b"hello")
    fs = _fs(s3_endpoint, bucket="mybkt")
    entry = await fs.stat(PathRef.from_posix("/k"))
    assert entry.size == 5
    assert entry.kind == EntryKind.FILE
    assert entry.etag is not None


async def test_stat_missing_raises(s3_endpoint: str) -> None:
    await _make_bucket(s3_endpoint, "mybkt")
    fs = _fs(s3_endpoint, bucket="mybkt")
    with pytest.raises(NotFoundError):
        await fs.stat(PathRef.from_posix("/missing"))


# ---------------------------------------------------------------------------
# Roundtrip read/write
# ---------------------------------------------------------------------------


async def test_write_then_read_small(s3_endpoint: str) -> None:
    await _make_bucket(s3_endpoint, "mybkt")
    fs = _fs(s3_endpoint, bucket="mybkt")
    await fs.write_stream(PathRef.from_posix("/k"), _agen([b"hello ", b"world"]))
    out = await _drain(await fs.read_stream(PathRef.from_posix("/k")))
    assert out == b"hello world"


async def test_write_then_read_16mb(s3_endpoint: str) -> None:
    """16 MiB round-trip — exercises the upload_fileobj path."""
    await _make_bucket(s3_endpoint, "mybkt")
    fs = _fs(s3_endpoint, bucket="mybkt")
    payload = os.urandom(16 * 1024 * 1024)

    async def src() -> AsyncIterator[bytes]:
        for i in range(0, len(payload), 1 << 20):
            yield payload[i : i + (1 << 20)]

    await fs.write_stream(PathRef.from_posix("/big.bin"), src(), total_size=len(payload))
    out = await _drain(
        await fs.read_stream(PathRef.from_posix("/big.bin"), chunk_size=4 * 1024 * 1024)
    )
    assert out == payload


async def test_progress_callback_invoked(s3_endpoint: str) -> None:
    await _make_bucket(s3_endpoint, "mybkt")
    fs = _fs(s3_endpoint, bucket="mybkt")
    seen: list[int] = []

    def cb(p: TransferProgress) -> None:
        seen.append(p.bytes_transferred)

    await fs.write_stream(
        PathRef.from_posix("/k"),
        _agen([b"abcdefgh"]),
        total_size=8,
        progress=cb,
    )
    assert seen, "progress callback should fire at least once"
    assert seen[-1] == 8


# ---------------------------------------------------------------------------
# mkdir / delete / rename
# ---------------------------------------------------------------------------


async def test_mkdir_creates_marker(s3_endpoint: str) -> None:
    await _make_bucket(s3_endpoint, "mybkt")
    fs = _fs(s3_endpoint, bucket="mybkt")
    await fs.mkdir(PathRef.from_posix("/folder"))
    entries = await fs.list(PathRef(()))
    names = [e.name for e in entries]
    assert "folder" in names


async def test_delete_object(s3_endpoint: str) -> None:
    await _make_bucket(s3_endpoint, "mybkt")
    await _put(s3_endpoint, "mybkt", "k", b"x")
    fs = _fs(s3_endpoint, bucket="mybkt")
    await fs.delete(PathRef.from_posix("/k"))
    with pytest.raises(NotFoundError):
        await fs.stat(PathRef.from_posix("/k"))


async def test_delete_prefix_batches(s3_endpoint: str) -> None:
    await _make_bucket(s3_endpoint, "mybkt")
    for i in range(3):
        await _put(s3_endpoint, "mybkt", f"d/x{i}", b"x")
    fs = _fs(s3_endpoint, bucket="mybkt")
    await fs.delete(PathRef.from_posix("/d"))
    async with _session().client("s3", endpoint_url=s3_endpoint) as s3:
        resp = await s3.list_objects_v2(Bucket="mybkt", Prefix="d/")
        assert resp.get("KeyCount", 0) == 0


async def test_delete_missing_raises(s3_endpoint: str) -> None:
    await _make_bucket(s3_endpoint, "mybkt")
    fs = _fs(s3_endpoint, bucket="mybkt")
    with pytest.raises(NotFoundError):
        await fs.delete(PathRef.from_posix("/nope"))


async def test_rename_preserves_content(s3_endpoint: str) -> None:
    await _make_bucket(s3_endpoint, "mybkt")
    await _put(s3_endpoint, "mybkt", "a", b"hello")
    fs = _fs(s3_endpoint, bucket="mybkt")
    await fs.rename(PathRef.from_posix("/a"), PathRef.from_posix("/b"))
    with pytest.raises(NotFoundError):
        await fs.stat(PathRef.from_posix("/a"))
    out = await _drain(await fs.read_stream(PathRef.from_posix("/b")))
    assert out == b"hello"


# ---------------------------------------------------------------------------
# Error code mapping
# ---------------------------------------------------------------------------


async def test_get_missing_raises_not_found(s3_endpoint: str) -> None:
    await _make_bucket(s3_endpoint, "mybkt")
    fs = _fs(s3_endpoint, bucket="mybkt")
    with pytest.raises(NotFoundError):
        await _drain(await fs.read_stream(PathRef.from_posix("/nope")))
