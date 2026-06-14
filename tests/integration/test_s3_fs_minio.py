"""Integration tests for S3FS against a real MinIO container."""

from __future__ import annotations

import os
from collections.abc import AsyncIterator

import aioboto3
import pytest

from aws_tui.domain.filesystem import EntryKind, NotFoundError, PathRef
from aws_tui.domain.s3_fs import S3FS

pytestmark = pytest.mark.integration


def _session(access_key: str, secret_key: str) -> aioboto3.Session:
    return aioboto3.Session(
        region_name="us-east-1",
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
    )


def _fs(endpoint: str, access_key: str, secret_key: str, bucket: str | None) -> S3FS:
    return S3FS(
        session=_session(access_key, secret_key),
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


async def _create_bucket(endpoint: str, access_key: str, secret_key: str, name: str) -> None:
    async with _session(access_key, secret_key).client("s3", endpoint_url=endpoint) as s3:
        try:
            await s3.create_bucket(Bucket=name)
        except s3.exceptions.BucketAlreadyOwnedByYou:
            pass
        except s3.exceptions.BucketAlreadyExists:
            pass


async def test_minio_roundtrip_small(minio_endpoint: tuple[str, str, str]) -> None:
    endpoint, ak, sk = minio_endpoint
    await _create_bucket(endpoint, ak, sk, "smallbkt")
    fs = _fs(endpoint, ak, sk, "smallbkt")
    await fs.write_stream(PathRef.from_posix("/hello"), _agen([b"hello minio"]))
    out = await _drain(await fs.read_stream(PathRef.from_posix("/hello")))
    assert out == b"hello minio"


async def test_minio_list_after_write(minio_endpoint: tuple[str, str, str]) -> None:
    endpoint, ak, sk = minio_endpoint
    await _create_bucket(endpoint, ak, sk, "listbkt")
    fs = _fs(endpoint, ak, sk, "listbkt")
    await fs.write_stream(PathRef.from_posix("/x"), _agen([b"x"]))
    await fs.write_stream(PathRef.from_posix("/y"), _agen([b"yy"]))
    entries = await fs.list(PathRef(()))
    by_name = {e.name: e for e in entries}
    assert set(by_name) == {"x", "y"}
    assert by_name["x"].size == 1
    assert by_name["y"].size == 2
    assert all(e.kind == EntryKind.FILE for e in entries)


async def test_minio_delete_then_missing(minio_endpoint: tuple[str, str, str]) -> None:
    endpoint, ak, sk = minio_endpoint
    await _create_bucket(endpoint, ak, sk, "delbkt")
    fs = _fs(endpoint, ak, sk, "delbkt")
    await fs.write_stream(PathRef.from_posix("/k"), _agen([b"x"]))
    await fs.delete(PathRef.from_posix("/k"))
    with pytest.raises(NotFoundError):
        await fs.stat(PathRef.from_posix("/k"))


async def test_minio_multipart_16mb(minio_endpoint: tuple[str, str, str]) -> None:
    """Real multipart upload via MinIO — sanity check the upload_fileobj path."""
    endpoint, ak, sk = minio_endpoint
    await _create_bucket(endpoint, ak, sk, "bigbkt")
    fs = _fs(endpoint, ak, sk, "bigbkt")
    payload = os.urandom(16 * 1024 * 1024)

    async def src() -> AsyncIterator[bytes]:
        for i in range(0, len(payload), 1 << 20):
            yield payload[i : i + (1 << 20)]

    await fs.write_stream(PathRef.from_posix("/big.bin"), src(), total_size=len(payload))
    out = await _drain(
        await fs.read_stream(PathRef.from_posix("/big.bin"), chunk_size=4 * 1024 * 1024)
    )
    assert out == payload


async def test_minio_list_buckets_at_service_root(
    minio_endpoint: tuple[str, str, str],
) -> None:
    endpoint, ak, sk = minio_endpoint
    await _create_bucket(endpoint, ak, sk, "alpha-bucket")
    await _create_bucket(endpoint, ak, sk, "beta-bucket")
    fs = _fs(endpoint, ak, sk, bucket=None)
    entries = await fs.list(PathRef(()))
    names = {e.name for e in entries}
    assert {"alpha-bucket", "beta-bucket"}.issubset(names)
