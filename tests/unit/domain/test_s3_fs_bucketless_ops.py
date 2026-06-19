"""S3FS in bucketless mode — every operation accepts a path whose first
segment is the bucket.

Regression guard: the service-level ``S3FS(bucket=None)`` used to raise
``ProviderError`` for stat/read_stream/write_stream/delete/mkdir/rename
the moment anyone passed a non-root path. That made every S3→local copy
crash the app. :meth:`S3FS._resolve` peels the bucket off the first
segment; these tests lock that in.

Backed by the same moto threaded server the rest of the s3_fs suite
uses (in-process ``mock_aws`` monkey-patching doesn't compose with
aiobotocore's awaited response body).
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import aioboto3
import pytest

from aws_tui.domain.filesystem import EntryKind, PathRef
from aws_tui.domain.s3_fs import S3FS

pytestmark = pytest.mark.unit

_BUCKET = "test-bucket"


async def _astream(data: bytes) -> AsyncIterator[bytes]:
    yield data


async def _collect(it: AsyncIterator[bytes]) -> bytes:
    out = bytearray()
    async for chunk in it:
        out.extend(chunk)
    return bytes(out)


def _session() -> aioboto3.Session:
    return aioboto3.Session(
        region_name="us-east-1",
        aws_access_key_id="testing",
        aws_secret_access_key="testing",
    )


def _bucketless_fs(endpoint: str) -> S3FS:
    return S3FS(
        session=_session(),
        bucket=None,
        endpoint_url=endpoint,
        force_path_style=True,
    )


async def _bootstrap(endpoint: str) -> None:
    """Create the test bucket via a one-shot client."""
    async with _session().client("s3", endpoint_url=endpoint, region_name="us-east-1") as client:
        await client.create_bucket(Bucket=_BUCKET)


# ── Tests ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_bucketless_list_at_root_returns_buckets(s3_endpoint: str) -> None:
    await _bootstrap(s3_endpoint)
    fs = _bucketless_fs(s3_endpoint)
    entries = await fs.list(PathRef(()))
    names = [e.name for e in entries]
    assert _BUCKET in names
    assert all(e.kind is EntryKind.DIRECTORY for e in entries)


@pytest.mark.asyncio
async def test_bucketless_list_in_bucket_returns_objects(s3_endpoint: str) -> None:
    await _bootstrap(s3_endpoint)
    async with _session().client("s3", endpoint_url=s3_endpoint, region_name="us-east-1") as client:
        await client.put_object(Bucket=_BUCKET, Key="alpha.txt", Body=b"alpha")
        await client.put_object(Bucket=_BUCKET, Key="folder/beta.txt", Body=b"beta")

    fs = _bucketless_fs(s3_endpoint)
    entries = await fs.list(PathRef((_BUCKET,)))
    names = {e.name for e in entries}
    assert "alpha.txt" in names
    assert "folder" in names


@pytest.mark.asyncio
async def test_bucketless_read_stream_via_first_segment(s3_endpoint: str) -> None:
    """Regression: read_stream used to raise ProviderError when bucket
    is None. Now it peels the bucket off the first segment."""
    await _bootstrap(s3_endpoint)
    async with _session().client("s3", endpoint_url=s3_endpoint, region_name="us-east-1") as client:
        await client.put_object(Bucket=_BUCKET, Key="alpha.txt", Body=b"hello-world")

    fs = _bucketless_fs(s3_endpoint)
    body = await _collect(await fs.read_stream(PathRef((_BUCKET, "alpha.txt"))))
    assert body == b"hello-world"


@pytest.mark.asyncio
async def test_bucketless_write_stream_via_first_segment(s3_endpoint: str) -> None:
    await _bootstrap(s3_endpoint)
    fs = _bucketless_fs(s3_endpoint)
    await fs.write_stream(PathRef((_BUCKET, "new.txt")), _astream(b"data"))

    async with _session().client("s3", endpoint_url=s3_endpoint, region_name="us-east-1") as client:
        resp = await client.get_object(Bucket=_BUCKET, Key="new.txt")
        body = await resp["Body"].read()
        assert body == b"data"


@pytest.mark.asyncio
async def test_bucketless_stat_via_first_segment(s3_endpoint: str) -> None:
    await _bootstrap(s3_endpoint)
    async with _session().client("s3", endpoint_url=s3_endpoint, region_name="us-east-1") as client:
        await client.put_object(Bucket=_BUCKET, Key="alpha.txt", Body=b"x")

    fs = _bucketless_fs(s3_endpoint)
    entry = await fs.stat(PathRef((_BUCKET, "alpha.txt")))
    assert entry.kind is EntryKind.FILE
    assert entry.size == 1


@pytest.mark.asyncio
async def test_bucketless_delete_via_first_segment(s3_endpoint: str) -> None:
    await _bootstrap(s3_endpoint)
    async with _session().client("s3", endpoint_url=s3_endpoint, region_name="us-east-1") as client:
        await client.put_object(Bucket=_BUCKET, Key="doomed.txt", Body=b"x")

    fs = _bucketless_fs(s3_endpoint)
    await fs.delete(PathRef((_BUCKET, "doomed.txt")))

    async with _session().client("s3", endpoint_url=s3_endpoint, region_name="us-east-1") as client:
        resp = await client.list_objects_v2(Bucket=_BUCKET)
        keys = [o["Key"] for o in resp.get("Contents", [])]
        assert "doomed.txt" not in keys


@pytest.mark.asyncio
async def test_bucketless_mkdir_via_first_segment(s3_endpoint: str) -> None:
    await _bootstrap(s3_endpoint)
    fs = _bucketless_fs(s3_endpoint)
    await fs.mkdir(PathRef((_BUCKET, "newdir")))

    async with _session().client("s3", endpoint_url=s3_endpoint, region_name="us-east-1") as client:
        resp = await client.list_objects_v2(Bucket=_BUCKET, Prefix="newdir")
        keys = [o["Key"] for o in resp.get("Contents", [])]
        assert any(k.startswith("newdir/") for k in keys)


@pytest.mark.asyncio
async def test_bucketless_rename_within_bucket(s3_endpoint: str) -> None:
    await _bootstrap(s3_endpoint)
    async with _session().client("s3", endpoint_url=s3_endpoint, region_name="us-east-1") as client:
        await client.put_object(Bucket=_BUCKET, Key="a.txt", Body=b"x")

    fs = _bucketless_fs(s3_endpoint)
    await fs.rename(PathRef((_BUCKET, "a.txt")), PathRef((_BUCKET, "b.txt")))

    async with _session().client("s3", endpoint_url=s3_endpoint, region_name="us-east-1") as client:
        resp = await client.list_objects_v2(Bucket=_BUCKET)
        keys = {o["Key"] for o in resp.get("Contents", [])}
        assert keys == {"b.txt"}


def test_to_aware_coerces_naive_to_utc() -> None:
    """``_to_aware`` must promote a naïve datetime to UTC-aware so
    downstream sort/format code never has to mix tz-aware and tz-naïve
    values (older MinIO releases historically returned naïve
    ``LastModified`` timestamps).
    """
    from datetime import UTC, datetime, timedelta, timezone

    from aws_tui.domain.s3_fs import _to_aware

    # None passes through unchanged.
    assert _to_aware(None) is None

    # Naïve → UTC-aware.
    naive = datetime(2026, 1, 1, 12, 30, 45)
    coerced = _to_aware(naive)
    assert coerced is not None
    assert coerced.tzinfo is UTC
    assert coerced.replace(tzinfo=None) == naive

    # Already aware: returned unchanged (same instance — no copy).
    aware = datetime(2026, 1, 1, 12, 30, 45, tzinfo=UTC)
    assert _to_aware(aware) is aware

    # Non-UTC aware: also returned unchanged.
    other_tz = timezone(timedelta(hours=5))
    aware_other = datetime(2026, 1, 1, 12, 30, 45, tzinfo=other_tz)
    assert _to_aware(aware_other) is aware_other
