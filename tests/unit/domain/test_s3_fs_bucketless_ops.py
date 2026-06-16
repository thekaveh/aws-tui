"""S3FS in bucketless mode — every operation accepts a path whose first
segment is the bucket.

Pass-9 root cause: the service-level ``S3FS(bucket=None)`` raised
``ProviderError`` for stat/read_stream/write_stream/delete/mkdir/rename
the moment anyone passed a non-root path. This made every S3→local copy
crash the app. The fix added :meth:`S3FS._resolve` and updated every
op; these tests lock that in.

Backed by the same moto threaded server the rest of the s3_fs suite
uses (in-process ``mock_aws`` monkey-patching doesn't compose with
aiobotocore's awaited response body).
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator

import aioboto3
import pytest
from moto.server import ThreadedMotoServer

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


@pytest.fixture(scope="module")
def moto_server() -> Iterator[str]:
    server = ThreadedMotoServer(port=0)
    server.start()
    host, port = server.get_host_and_port()
    yield f"http://{host}:{port}"
    server.stop()


@pytest.fixture
def s3_endpoint(moto_server: str, monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    import urllib.request

    urllib.request.urlopen(
        urllib.request.Request(f"{moto_server}/moto-api/reset", method="POST")
    ).read()
    return moto_server


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
    """Pass-9 crash path: read_stream used to raise ProviderError when
    bucket is None. Now it peels the bucket off the first segment."""
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
