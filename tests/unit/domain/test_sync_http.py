"""Installed SDK sync planning/publication over isolated loopback Moto."""

from __future__ import annotations

from contextlib import asynccontextmanager
from functools import wraps
from threading import RLock

import pytest
from moto.s3.exceptions import PreconditionFailed
from moto.s3.responses import S3Response

from aws_tui.domain.filesystem import ConflictError, PathRef
from aws_tui.domain.local_fs import LocalFS
from aws_tui.domain.recursive_upload import UploadSourceChangedError
from aws_tui.domain.sync_plan import SyncLabel, plan_sync
from aws_tui.domain.upload_batch import UploadBatch, UploadStatus
from tests.unit.domain.test_recursive_upload_http import RecordingS3, session

pytestmark = pytest.mark.unit
MUTATIONS = {
    "PutObject",
    "CreateMultipartUpload",
    "UploadPart",
    "CompleteMultipartUpload",
    "AbortMultipartUpload",
    "DeleteObject",
    "DeleteObjects",
    "RestoreObject",
    "CopyObject",
}


def path(value):
    return PathRef.from_posix(value)


class SyncS3(RecordingS3):
    @asynccontextmanager
    async def _client(self):
        async with super()._client() as client:

            def record(params, model, **_):
                self.calls.append((model.name, dict(params)))

            client.meta.events.register("before-parameter-build.s3", record)
            yield client


@pytest.mark.parametrize("versioned", [False, True])
async def test_actual_sdk_second_run_plans_no_operations_or_mutations(
    s3_endpoint, tmp_path, versioned
):
    async with session().client("s3", endpoint_url=s3_endpoint) as client:
        await client.create_bucket(Bucket="recursive-upload")
        if versioned:
            await client.put_bucket_versioning(
                Bucket="recursive-upload", VersioningConfiguration={"Status": "Enabled"}
            )
        for key, data in (
            ("tree/same", b"same"),
            ("tree/changed", b"prior"),
            ("tree/remote-only", b"keep"),
        ):
            await client.put_object(Bucket="recursive-upload", Key=key, Body=data)
    (tmp_path / "tree").mkdir()
    files = {"new": b"data", "new-empty": b"", "same": b"same", "changed": b"after"}
    for name, data in files.items():
        (tmp_path / "tree" / name).write_bytes(data)
    source, destination = LocalFS(root=tmp_path), SyncS3(s3_endpoint)
    first = await plan_sync(source, destination, path("tree"), path("tree"))
    assert [(row.relative, row.label) for row in first.rows] == [
        ("changed", SyncLabel.UPDATE),
        ("new", SyncLabel.CREATE),
        ("new-empty", SyncLabel.CREATE),
        ("same", SyncLabel.UNCHANGED),
    ]
    assert not MUTATIONS & {name for name, _ in destination.calls}
    assert (await UploadBatch(first.execution).run()).status == UploadStatus.SUCCEEDED
    boundaries = [
        (name, params)
        for name, params in destination.calls
        if name in {"PutObject", "CompleteMultipartUpload"}
    ]
    assert len(boundaries) == 3
    for _, params in boundaries:
        if params["Key"] == "tree/changed":
            assert "IfMatch" in params
        else:
            assert params["IfNoneMatch"] == "*"
    checkpoint = len(destination.calls)
    second = await plan_sync(source, destination, path("tree"), path("tree"))
    assert not second.operations
    assert all(row.label == SyncLabel.UNCHANGED for row in second.rows)
    assert not MUTATIONS & {name for name, _ in destination.calls[checkpoint:]}
    gets = [params for name, params in destination.calls[checkpoint:] if name == "GetObject"]
    assert len(gets) == len(files)
    assert all("IfMatch" in params for params in gets)
    assert all(("VersionId" in params) == versioned for params in gets)
    async with session().client("s3", endpoint_url=s3_endpoint) as client:
        for name, expected in {**files, "remote-only": b"keep"}.items():
            response = await client.get_object(Bucket="recursive-upload", Key="tree/" + name)
            try:
                assert await response["Body"].read() == expected
            finally:
                response["Body"].close()


@pytest.mark.parametrize("changed", ["source", "destination"])
async def test_changed_reviewed_snapshot_is_not_written(s3_endpoint, tmp_path, changed):
    async with session().client("s3", endpoint_url=s3_endpoint) as client:
        await client.create_bucket(Bucket="recursive-upload")
        await client.put_object(Bucket="recursive-upload", Key="tree/file", Body=b"old")
    (tmp_path / "tree").mkdir()
    (tmp_path / "tree/file").write_bytes(b"new")
    destination = SyncS3(s3_endpoint)
    plan = await plan_sync(LocalFS(root=tmp_path), destination, path("tree"), path("tree"))
    if changed == "source":
        (tmp_path / "tree/file").write_bytes(b"edited")
    else:
        async with session().client("s3", endpoint_url=s3_endpoint) as client:
            await client.put_object(Bucket="recursive-upload", Key="tree/file", Body=b"race")
    checkpoint = len(destination.calls)
    result = await UploadBatch(plan.execution).run()
    assert result.status == UploadStatus.FAILED
    assert isinstance(
        result.outcomes[0].error, UploadSourceChangedError if changed == "source" else ConflictError
    )
    assert not MUTATIONS & {name for name, _ in destination.calls[checkpoint:]}


async def final_write_case(s3_endpoint, tmp_path, *, existing, data, race=True):
    initial_etag = None
    async with session().client("s3", endpoint_url=s3_endpoint) as client:
        await client.create_bucket(Bucket="recursive-upload")
        if existing:
            initial = await client.put_object(
                Bucket="recursive-upload", Key="tree/file", Body=b"old"
            )
            initial_etag = initial["ETag"]

    class Racing(SyncS3):
        async def write_upload_stream(self, value, stream, **kwargs):
            if race:
                async with session().client("s3", endpoint_url=s3_endpoint) as client:
                    await client.put_object(
                        Bucket="recursive-upload", Key="tree/file", Body=b"racing"
                    )
            return await super().write_upload_stream(value, stream, **kwargs)

    (tmp_path / "tree").mkdir()
    (tmp_path / "tree/file").write_bytes(data)
    destination = Racing(s3_endpoint)
    plan = await plan_sync(LocalFS(root=tmp_path), destination, path("tree"), path("tree"))
    result = await UploadBatch(plan.execution).run()
    assert result.status == (UploadStatus.FAILED if race else UploadStatus.SUCCEEDED)
    if race:
        assert isinstance(result.outcomes[0].error, ConflictError)
    assert result.outcomes[0].published != race
    assert not result.outcomes[0].uncertain
    final_name = "CompleteMultipartUpload" if data else "PutObject"
    final = [params for name, params in destination.calls if name == final_name]
    assert len(final) == 1
    assert ("IfMatch" in final[0]) == existing
    assert (final[0].get("IfNoneMatch") == "*") != existing
    assert not {"DeleteObject", "DeleteObjects"} & {name for name, _ in destination.calls}
    async with session().client("s3", endpoint_url=s3_endpoint) as client:
        response = await client.get_object(Bucket="recursive-upload", Key="tree/file")
        try:
            assert await response["Body"].read() == (b"racing" if race else data)
        finally:
            response["Body"].close()
        assert not (await client.list_multipart_uploads(Bucket="recursive-upload")).get("Uploads")
    return initial_etag


@pytest.mark.parametrize(("existing", "data"), [(False, b""), (False, b"replacement"), (True, b"")])
async def test_native_moto_final_guards_preserve_racing_key(s3_endpoint, tmp_path, existing, data):
    await final_write_case(s3_endpoint, tmp_path, existing=existing, data=data)


@pytest.fixture
def aws_completion_condition(monkeypatch):
    """Controlled HTTP contract, separate from unpatched Moto acceptance.

    Moto5.2.3's completion handler checks If-None-Match but ignores If-Match.
    Enforce the actual wire condition at this test's server publication point,
    with serialized key writes, rather than weakening the client or its test.
    """
    lock, observations = RLock(), []
    original = S3Response._key_response_post

    def post(self, request, body, bucket_name, query, key_name):
        with lock:
            if (
                bucket_name == "recursive-upload"
                and key_name == "tree/file"
                and query.get("uploadId")
            ):
                condition = request.headers.get("If-Match")
                if condition is not None:
                    current = self.backend.get_object(bucket_name, key_name)
                    current_etag = current.etag if current is not None else None
                    observations.append({"condition": condition, "current": current_etag})
                    if condition != current_etag:
                        raise PreconditionFailed("If-Match")
            return original(self, request, body, bucket_name, query, key_name)

    def serialize(method):
        @wraps(method)
        def wrapped(*args, **kwargs):
            with lock:
                return method(*args, **kwargs)

        return wrapped

    monkeypatch.setattr(S3Response, "_key_response_post", post)
    for name in ("_key_response_put", "_key_response_delete"):
        monkeypatch.setattr(S3Response, name, serialize(getattr(S3Response, name)))
    return observations


@pytest.mark.parametrize("race", [False, True], ids=["matching-condition", "changed-condition"])
async def test_controlled_http_completion_uses_actual_if_match_header(
    s3_endpoint,
    tmp_path,
    aws_completion_condition,
    race,
):
    initial = await final_write_case(
        s3_endpoint, tmp_path, existing=True, data=b"replacement", race=race
    )
    assert len(aws_completion_condition) == 1
    observation = aws_completion_condition[0]
    assert observation["condition"] == initial
    assert (observation["condition"] != observation["current"]) == race
