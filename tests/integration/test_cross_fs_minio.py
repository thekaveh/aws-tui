"""Integration tests for CrossFsCopy/Move using LocalFS and S3FS on MinIO."""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import aioboto3
import pytest

from aws_tui.domain.cross_fs import CrossFsCopy, CrossFsMove
from aws_tui.domain.filesystem import NotFoundError, PathRef
from aws_tui.domain.local_fs import LocalFS
from aws_tui.domain.s3_fs import S3FS

pytestmark = pytest.mark.integration


def _session(ak: str, sk: str) -> aioboto3.Session:
    return aioboto3.Session(
        region_name="us-east-1",
        aws_access_key_id=ak,
        aws_secret_access_key=sk,
    )


def _s3fs(endpoint: str, ak: str, sk: str, bucket: str) -> S3FS:
    return S3FS(
        session=_session(ak, sk),
        bucket=bucket,
        endpoint_url=endpoint,
        force_path_style=True,
    )


async def _make_bucket(endpoint: str, ak: str, sk: str, name: str) -> None:
    async with _session(ak, sk).client("s3", endpoint_url=endpoint) as s3:
        try:
            await s3.create_bucket(Bucket=name)
        except s3.exceptions.BucketAlreadyOwnedByYou:
            pass
        except s3.exceptions.BucketAlreadyExists:
            pass


async def _drain(it: AsyncIterator[bytes]) -> bytes:
    out = bytearray()
    async for chunk in it:
        out.extend(chunk)
    return bytes(out)


async def test_local_to_minio(minio_endpoint: tuple[str, str, str], tmp_path: Path) -> None:
    endpoint, ak, sk = minio_endpoint
    await _make_bucket(endpoint, ak, sk, "l2sbkt")
    (tmp_path / "file.txt").write_bytes(b"local-to-s3")
    local = LocalFS(root=tmp_path)
    s3 = _s3fs(endpoint, ak, sk, "l2sbkt")
    copier = CrossFsCopy(source=local, destination=s3)
    await copier.copy(PathRef.from_posix("/file.txt"), PathRef.from_posix("/file.txt"))
    out = await _drain(await s3.read_stream(PathRef.from_posix("/file.txt")))
    assert out == b"local-to-s3"


async def test_minio_to_local(minio_endpoint: tuple[str, str, str], tmp_path: Path) -> None:
    endpoint, ak, sk = minio_endpoint
    await _make_bucket(endpoint, ak, sk, "s2lbkt")
    s3 = _s3fs(endpoint, ak, sk, "s2lbkt")

    async def src() -> AsyncIterator[bytes]:
        yield b"minio-to-local"

    await s3.write_stream(PathRef.from_posix("/k"), src())
    local = LocalFS(root=tmp_path)
    copier = CrossFsCopy(source=s3, destination=local)
    await copier.copy(PathRef.from_posix("/k"), PathRef.from_posix("/k"))
    assert (tmp_path / "k").read_bytes() == b"minio-to-local"


async def test_move_local_to_minio(minio_endpoint: tuple[str, str, str], tmp_path: Path) -> None:
    endpoint, ak, sk = minio_endpoint
    await _make_bucket(endpoint, ak, sk, "movbkt")
    (tmp_path / "src.txt").write_bytes(b"moveme")
    local = LocalFS(root=tmp_path)
    s3 = _s3fs(endpoint, ak, sk, "movbkt")
    mover = CrossFsMove(source=local, destination=s3)
    await mover.move(PathRef.from_posix("/src.txt"), PathRef.from_posix("/dst.txt"))
    out = await _drain(await s3.read_stream(PathRef.from_posix("/dst.txt")))
    assert out == b"moveme"
    assert not (tmp_path / "src.txt").exists()


async def test_minio_to_minio_cross_bucket(
    minio_endpoint: tuple[str, str, str],
) -> None:
    endpoint, ak, sk = minio_endpoint
    await _make_bucket(endpoint, ak, sk, "from-bkt")
    await _make_bucket(endpoint, ak, sk, "to-bkt")
    src = _s3fs(endpoint, ak, sk, "from-bkt")
    dst = _s3fs(endpoint, ak, sk, "to-bkt")

    async def body() -> AsyncIterator[bytes]:
        yield b"cross-bucket-streaming"

    await src.write_stream(PathRef.from_posix("/payload"), body())
    copier = CrossFsCopy(source=src, destination=dst)
    await copier.copy(PathRef.from_posix("/payload"), PathRef.from_posix("/payload"))
    out = await _drain(await dst.read_stream(PathRef.from_posix("/payload")))
    assert out == b"cross-bucket-streaming"
    # Source is still there (copy, not move).
    assert (await src.stat(PathRef.from_posix("/payload"))).size == len(out)
    # And missing entries still raise.
    with pytest.raises(NotFoundError):
        await dst.stat(PathRef.from_posix("/missing"))
