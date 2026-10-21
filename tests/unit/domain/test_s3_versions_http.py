"""Versioned compatible-endpoint acceptance over real SDK/loopback HTTP calls."""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import aioboto3
import pytest

from aws_tui.domain.cross_fs import CrossFsCopy
from aws_tui.domain.filesystem import ConflictError, NotFoundError, PathRef
from aws_tui.domain.local_fs import LocalFS
from aws_tui.domain.s3_fs import S3FS

pytestmark = pytest.mark.unit
BUCKET = "historical-versions"
KEY = "prefix/object"
OBJECT = PathRef((BUCKET, "prefix", "object"))


def session() -> aioboto3.Session:
    return aioboto3.Session(
        region_name="us-east-1", aws_access_key_id="testing", aws_secret_access_key="testing"
    )


class RecordingHTTPFS(S3FS):
    def __init__(self, endpoint: str) -> None:
        super().__init__(
            session=session(), bucket=None, endpoint_url=endpoint, force_path_style=True
        )
        self.calls: list[tuple[str, dict[str, Any]]] = []

    @asynccontextmanager
    async def _client(self):
        async with super()._client() as client:

            def record(params, model, **kwargs):
                if model.name == "ListObjectVersions":
                    params["MaxKeys"] = 1  # Exercise actual SDK dual-marker HTTP paging.
                self.calls.append((model.name, dict(params)))

            client.meta.events.register("before-parameter-build.s3", record)
            yield client


async def test_deleted_current_and_interleaved_markers_inspect_download_and_recover(
    s3_endpoint: str, tmp_path: Path
) -> None:
    async with session().client("s3", endpoint_url=s3_endpoint) as client:
        await client.create_bucket(Bucket=BUCKET)
        await client.put_bucket_versioning(
            Bucket=BUCKET, VersioningConfiguration={"Status": "Enabled"}
        )
        first = await client.put_object(
            Bucket=BUCKET,
            Key=KEY,
            Body=b"historical payload",
            ContentType="text/plain",
            CacheControl="max-age=12",
            Metadata={"owner": "original"},
            Tagging=urlencode({"a + b": "literal +/="}),
            WebsiteRedirectLocation="/archive",
        )
        marker_one = await client.delete_object(Bucket=BUCKET, Key=KEY)
        second = await client.put_object(Bucket=BUCKET, Key=KEY, Body=b"new content")
        marker_two = await client.delete_object(Bucket=BUCKET, Key=KEY)
        await client.put_object(Bucket=BUCKET, Key=KEY + "-neighbour", Body=b"not selected")
        before = await client.list_object_versions(Bucket=BUCKET, Prefix=KEY)

    provider = RecordingHTTPFS(s3_endpoint)
    assert "object" not in {
        entry.name for entry in await provider.list(PathRef((BUCKET, "prefix")))
    }
    with pytest.raises(NotFoundError):
        await provider.stat(OBJECT)
    listing = await provider.list_versions(OBJECT)
    assert {entry.version_id for entry in listing.versions} == {
        first["VersionId"],
        marker_one["VersionId"],
        second["VersionId"],
        marker_two["VersionId"],
    }
    assert sum(entry.is_delete_marker for entry in listing.versions) == 2
    assert (
        next(entry for entry in listing.versions if entry.is_latest).version_id
        == marker_two["VersionId"]
    )
    pages = [kwargs for op, kwargs in provider.calls if op == "ListObjectVersions"]
    assert len(pages) >= 4
    assert all("KeyMarker" in kwargs and "VersionIdMarker" in kwargs for kwargs in pages[1:])

    old = next(entry for entry in listing.versions if entry.version_id == first["VersionId"])
    details = await provider.read_version_details(old)
    assert details.version_id == first["VersionId"]
    assert dict(details.metadata or ()) == {"owner": "original"}
    assert dict(details.tags or ()) == {"a + b": "literal +/="}
    local = LocalFS(root=tmp_path)
    destination = PathRef(("historical.bin",))
    progress = []
    assert await CrossFsCopy(source=provider.version_source(old), destination=local).copy(
        OBJECT, destination, progress=progress.append
    )
    assert (tmp_path / "historical.bin").read_bytes() == b"historical payload"
    assert progress[-1].bytes_transferred == len(b"historical payload")
    with pytest.raises(ConflictError):
        await CrossFsCopy(source=provider.version_source(old), destination=local).copy(
            OBJECT, destination
        )

    recovered_path = PathRef((BUCKET, "prefix", "recovered"))
    recovered = await provider.recovery_destination(old, recovered_path)
    assert await CrossFsCopy(source=provider.version_source(old), destination=recovered).copy(
        OBJECT, recovered_path
    )
    assert recovered.publication.published
    with pytest.raises(ConflictError):
        await CrossFsCopy(source=provider.version_source(old), destination=recovered).copy(
            OBJECT, recovered_path
        )
    marker = next(entry for entry in listing.versions if entry.is_delete_marker)
    count = len(provider.calls)
    with pytest.raises(ConflictError, match="delete marker"):
        await provider.version_source(marker).read_stream(OBJECT)
    assert len(provider.calls) == count
    assert all(op != "DeleteObject" for op, _ in provider.calls)

    async with session().client("s3", endpoint_url=s3_endpoint) as client:
        result = await client.get_object(Bucket=BUCKET, Key="prefix/recovered")
        assert await result["Body"].read() == b"historical payload"
        assert result["Metadata"] == {"owner": "original"}
        assert result["ContentType"] == "text/plain"
        assert result["CacheControl"] == "max-age=12"
        assert result["WebsiteRedirectLocation"] == "/archive"
        tagged = await client.get_object_tagging(Bucket=BUCKET, Key="prefix/recovered")
        assert {row["Key"]: row["Value"] for row in tagged["TagSet"]} == {"a + b": "literal +/="}
        after = await client.list_object_versions(Bucket=BUCKET, Prefix=KEY)
    assert after["Versions"] == before["Versions"]
    assert after["DeleteMarkers"] == before["DeleteMarkers"]
    with pytest.raises(NotFoundError):
        await provider.stat(OBJECT)


async def test_retained_null_version_is_read_exactly_after_versioning_is_enabled(
    s3_endpoint: str,
) -> None:
    async with session().client("s3", endpoint_url=s3_endpoint) as client:
        await client.create_bucket(Bucket=BUCKET)
        await client.put_object(Bucket=BUCKET, Key=KEY, Body=b"original")
        await client.put_bucket_versioning(
            Bucket=BUCKET, VersioningConfiguration={"Status": "Enabled"}
        )
        await client.put_object(Bucket=BUCKET, Key=KEY, Body=b"replacement")
    provider = RecordingHTTPFS(s3_endpoint)
    listing = await provider.list_versions(OBJECT)
    assert listing.versioning == "enabled"
    old = next(version for version in listing.versions if version.version_id == "null")
    assert old.version_id == "null"
    stream = await provider.version_source(old).read_stream(OBJECT)
    assert b"".join([chunk async for chunk in stream]) == b"original"
    source_calls = [kwargs for op, kwargs in provider.calls if op in {"HeadObject", "GetObject"}]
    assert source_calls
    assert all(kwargs["VersionId"] == "null" for kwargs in source_calls)
