"""Exact S3 upload observations and collision plans over controlled endpoints."""

from __future__ import annotations

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock

import aioboto3
import pytest
from botocore.exceptions import ClientError

from aws_tui.domain.filesystem import PathRef, PermissionDeniedError
from aws_tui.domain.local_fs import LocalFS
from aws_tui.domain.recursive_upload import UploadPlanError, plan_upload
from aws_tui.domain.s3_fs import S3FS

pytestmark = pytest.mark.unit


def provider(monkeypatch, *, bucket="bucket", prefix=""):
    client = MagicMock()
    client.head_object = AsyncMock()
    client.list_objects_v2 = AsyncMock(return_value={"KeyCount": 0})

    @asynccontextmanager
    async def context():
        yield client

    fs = S3FS(session=MagicMock(), bucket=bucket, prefix=prefix)
    monkeypatch.setattr(fs, "_client", context)
    return fs, client


def service_error(code, operation="HeadObject"):
    return ClientError({"Error": {"Code": code, "Message": "controlled"}}, operation)


@pytest.mark.parametrize(
    ("bucket", "prefix", "path", "expected"),
    [
        ("bucket", "", PathRef(("a", "", "b")), ("bucket", "a//b")),
        (None, "prefix", PathRef(("bucket", "a")), ("bucket", "prefix/a")),
        ("bucket", "prefix", PathRef(), ("bucket", "prefix")),
    ],
)
async def test_pure_upload_location_preserves_literal_key_components(
    monkeypatch, bucket, prefix, path, expected
):
    fs, client = provider(monkeypatch, bucket=bucket, prefix=prefix)
    assert fs.upload_location(path) == expected
    assert not client.mock_calls


async def test_exact_object_observation_keeps_revision_and_never_reads_payload(monkeypatch):
    fs, client = provider(monkeypatch)
    client.head_object.return_value = {"ContentLength": 7, "ETag": '"revision"'}
    entry = await fs.inspect_upload_destination(PathRef(("exact",)))
    assert entry.size == 7
    assert entry.etag
    client.head_object.assert_awaited_once_with(Bucket="bucket", Key="exact")
    client.list_objects_v2.assert_awaited_once_with(Bucket="bucket", Prefix="exact/", MaxKeys=1)
    assert {row[0] for row in client.mock_calls} <= {"head_object", "list_objects_v2"}


@pytest.mark.parametrize("code", ["404", "NoSuchKey", "NotFound"])
async def test_only_confirmed_missing_exact_key_is_absent(monkeypatch, code):
    fs, client = provider(monkeypatch)
    client.head_object.side_effect = service_error(code)
    assert await fs.inspect_upload_destination(PathRef(("missing",))) is None


async def test_denied_head_is_not_a_missing_key(monkeypatch):
    fs, client = provider(monkeypatch)
    client.head_object.side_effect = service_error("AccessDenied")
    with pytest.raises(PermissionDeniedError):
        await fs.inspect_upload_destination(PathRef(("denied",)))
    client.list_objects_v2.assert_not_awaited()


@pytest.mark.parametrize("head", [{"ContentLength": 7, "ETag": '"revision"'}, None])
async def test_prefix_collision_is_named_instead_of_creating_ambiguous_pane_entry(
    monkeypatch, head
):
    fs, client = provider(monkeypatch)
    if head is None:
        client.head_object.side_effect = service_error("404")
    else:
        client.head_object.return_value = head
    client.list_objects_v2.return_value = {"Contents": [{"Key": "name/child"}], "KeyCount": 1}
    with pytest.raises(UploadPlanError, match="prefix"):
        await fs.inspect_upload_destination(PathRef(("name",)))


@pytest.mark.parametrize("head", [{}, {"ContentLength": -1}, {"ContentLength": "7"}])
async def test_malformed_head_is_not_an_uploadable_observation(monkeypatch, head):
    fs, client = provider(monkeypatch)
    client.head_object.return_value = head
    with pytest.raises(UploadPlanError, match="observation"):
        await fs.inspect_upload_destination(PathRef(("name",)))


async def test_malformed_timestamp_is_a_named_observation_error(monkeypatch):
    fs, client = provider(monkeypatch)
    client.head_object.return_value = {
        "ContentLength": 7,
        "ETag": '"revision"',
        "LastModified": "not-a-datetime",
    }
    with pytest.raises(UploadPlanError, match="observation"):
        await fs.inspect_upload_destination(PathRef(("name",)))


@pytest.mark.parametrize(
    "observation",
    [{}, {"KeyCount": 1}, {"KeyCount": 0, "Contents": "bad"}, {"KeyCount": True}],
)
async def test_malformed_prefix_observation_cannot_establish_absence(monkeypatch, observation):
    fs, client = provider(monkeypatch)
    client.head_object.side_effect = service_error("404")
    client.list_objects_v2.return_value = observation
    with pytest.raises(UploadPlanError, match="observation"):
        await fs.inspect_upload_destination(PathRef(("name",)))


async def test_moto_collision_plan_uses_only_metadata_calls(s3_endpoint, tmp_path):
    session = aioboto3.Session(
        region_name="us-east-1", aws_access_key_id="testing", aws_secret_access_key="testing"
    )
    async with session.client("s3", endpoint_url=s3_endpoint) as client:
        await client.create_bucket(Bucket="recursive-plans")
        await client.put_object(Bucket="recursive-plans", Key="tree/one", Body=b"existing")
    (tmp_path / "tree").mkdir()
    (tmp_path / "tree/one").write_bytes(b"replacement")
    (tmp_path / "tree/two").write_bytes(b"new")
    calls = []

    class RecordingFS(S3FS):
        @asynccontextmanager
        async def _client(self):
            async with super()._client() as client:
                client.meta.events.register(
                    "before-parameter-build.s3", lambda model, **_: calls.append(model.name)
                )
                yield client

    fs = RecordingFS(session=session, bucket="recursive-plans", endpoint_url=s3_endpoint)
    plan = await plan_upload(LocalFS(root=tmp_path), fs, PathRef(("tree",)), PathRef(("tree",)))
    assert plan.conflicts == 1
    assert plan.known_bytes == 14
    assert [row.conflict for row in plan.objects] == [True, False]
    assert set(calls) == {"HeadObject", "ListObjectsV2"}
    async with session.client("s3", endpoint_url=s3_endpoint) as client:
        response = await client.get_object(Bucket="recursive-plans", Key="tree/one")
        assert await response["Body"].read() == b"existing"
        with pytest.raises(ClientError):
            await client.head_object(Bucket="recursive-plans", Key="tree/two")
