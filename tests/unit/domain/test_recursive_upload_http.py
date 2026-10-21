"""Real installed SDK publication and owned MPU cancellation on loopback Moto."""

from __future__ import annotations

import asyncio
from contextlib import aclosing, asynccontextmanager

import aioboto3
import pytest

from aws_tui.domain.cross_fs import ConflictResolution
from aws_tui.domain.filesystem import ConflictError, PathRef
from aws_tui.domain.local_fs import LocalFS
from aws_tui.domain.recursive_upload import plan_upload
from aws_tui.domain.s3_fs import S3FS
from aws_tui.domain.s3_upload import UploadPublication
from aws_tui.domain.upload_batch import UploadBatch, UploadStatus, replan_failed

pytestmark = pytest.mark.unit


def session():
    return aioboto3.Session(
        region_name="us-east-1", aws_access_key_id="testing", aws_secret_access_key="testing"
    )


class RecordingS3(S3FS):
    def __init__(self, endpoint):
        super().__init__(session=session(), bucket="recursive-upload", endpoint_url=endpoint)
        self.calls = []

    @asynccontextmanager
    async def _upload_client(self):
        async with super()._upload_client() as client:
            assert client.meta.config.retries["total_max_attempts"] == 1

            def record(params, model, **_):
                self.calls.append((model.name, dict(params)))

            client.meta.events.register("before-parameter-build.s3", record)
            yield client


@pytest.mark.parametrize("data", [b"", b"new payload"])
async def test_concurrent_key_after_plan_is_never_replaced(s3_endpoint, tmp_path, data):
    async with session().client("s3", endpoint_url=s3_endpoint) as client:
        await client.create_bucket(Bucket="recursive-upload")
    (tmp_path / "tree").mkdir()
    (tmp_path / "tree/object").write_bytes(data)
    source = LocalFS(root=tmp_path)
    destination = RecordingS3(s3_endpoint)
    plan = await plan_upload(source, destination, PathRef(("tree",)), PathRef(("tree",)))
    item = plan.objects[0]
    assert not item.conflict
    async with session().client("s3", endpoint_url=s3_endpoint) as client:
        await client.put_object(Bucket="recursive-upload", Key="tree/object", Body=b"concurrent")
    publication = UploadPublication()
    stream = await source.read_upload_stream(item.source, expected=item.source_entry)
    try:
        with pytest.raises(ConflictError):
            await destination.write_upload_stream(
                item.destination, stream, total_size=len(data), publication=publication
            )
    finally:
        await stream.aclose()
    assert not publication.published
    assert not publication.uncertain
    calls = dict(destination.calls)
    final = "CompleteMultipartUpload" if data else "PutObject"
    assert calls[final]["IfNoneMatch"] == "*"
    assert not {"DeleteObject", "DeleteObjects"} & calls.keys()
    async with session().client("s3", endpoint_url=s3_endpoint) as client:
        response = await client.get_object(Bucket="recursive-upload", Key="tree/object")
        assert await response["Body"].read() == b"concurrent"
        assert not (await client.list_multipart_uploads(Bucket="recursive-upload")).get("Uploads")


async def test_real_mid_multipart_cancel_aborts_owned_upload_and_deletes_nothing(s3_endpoint):
    async with session().client("s3", endpoint_url=s3_endpoint) as client:
        await client.create_bucket(Bucket="recursive-upload")
        await client.put_object(Bucket="recursive-upload", Key="keep", Body=b"keep")
    destination = RecordingS3(s3_endpoint)
    first_part = asyncio.Event()
    source_closed = asyncio.Event()

    async def stream():
        try:
            yield b"x" * (8 * 1024 * 1024)
            await asyncio.Event().wait()
        finally:
            source_closed.set()

    publication = UploadPublication()
    source = stream()
    task = asyncio.create_task(
        destination.write_upload_stream(
            PathRef(("new",)),
            source,
            total_size=16 * 1024 * 1024,
            publication=publication,
            progress=lambda _: first_part.set(),
        )
    )
    try:
        await asyncio.wait_for(first_part.wait(), 15)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    finally:
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        await source.aclose()
    assert source_closed.is_set()
    assert not publication.attempted
    calls = dict(destination.calls)
    assert calls["AbortMultipartUpload"]["UploadId"] == calls["UploadPart"]["UploadId"]
    assert not {"DeleteObject", "DeleteObjects", "CompleteMultipartUpload"} & calls.keys()
    async with session().client("s3", endpoint_url=s3_endpoint) as client:
        assert not (await client.list_multipart_uploads(Bucket="recursive-upload")).get("Uploads")
        response = await client.get_object(Bucket="recursive-upload", Key="keep")
        assert await response["Body"].read() == b"keep"


async def test_real_batch_mixed_outcomes_and_reviewed_overwrite_retry_only_failed_object(
    s3_endpoint, tmp_path
):
    async with session().client("s3", endpoint_url=s3_endpoint) as client:
        await client.create_bucket(Bucket="recursive-upload")
        await client.put_object(Bucket="recursive-upload", Key="tree/a", Body=b"old")
    (tmp_path / "tree/nested").mkdir(parents=True)
    for name, data in (("a", b"replacement"), ("empty", b""), ("nested/c", b"child")):
        (tmp_path / "tree" / name).write_bytes(data)
    destination = RecordingS3(s3_endpoint)
    plan = await plan_upload(
        LocalFS(root=tmp_path), destination, PathRef(("tree",)), PathRef(("tree",))
    )
    batch = UploadBatch(plan)
    result = await batch.run()
    assert result.status == UploadStatus.FAILED
    assert sum(row.status == UploadStatus.SUCCEEDED for row in result.outcomes) == 2
    writes_before = len(destination.calls)
    retry = await replan_failed(batch, policy=ConflictResolution.OVERWRITE)
    assert [item.source for item in retry.objects] == [PathRef(("tree", "a"))]
    assert (await UploadBatch(retry).run()).status == UploadStatus.SUCCEEDED
    final = [
        params
        for name, params in destination.calls[writes_before:]
        if name == "CompleteMultipartUpload"
    ]
    assert len(final) == 1
    assert final[0]["Key"] == "tree/a"
    assert "IfMatch" in final[0]
    async with session().client("s3", endpoint_url=s3_endpoint) as client:
        for name, data in (("a", b"replacement"), ("empty", b""), ("nested/c", b"child")):
            response = await client.get_object(Bucket="recursive-upload", Key=f"tree/{name}")
            assert await response["Body"].read() == data
    assert not {"DeleteObject", "DeleteObjects"} & {name for name, _ in destination.calls}


async def test_real_batch_cancel_preserves_completed_objects_and_aborts_active_multipart(
    s3_endpoint, tmp_path
):
    async with session().client("s3", endpoint_url=s3_endpoint) as client:
        await client.create_bucket(Bucket="recursive-upload")
    (tmp_path / "tree").mkdir()
    (tmp_path / "tree/fast").write_bytes(b"preserved")
    (tmp_path / "tree/slow").write_bytes(b"x" * (8 * 1024 * 1024 + 1))
    slow_reading = asyncio.Event()

    class Source(LocalFS):
        async def read_upload_stream(self, value, **kwargs):
            original = await super().read_upload_stream(value, **kwargs)
            if value.name != "slow":
                return original

            async def held():
                async with aclosing(original):
                    async for chunk in original:
                        yield chunk
                        slow_reading.set()
                        await asyncio.Event().wait()

            return held()

    destination = RecordingS3(s3_endpoint)
    plan = await plan_upload(
        Source(root=tmp_path), destination, PathRef(("tree",)), PathRef(("tree",))
    )
    fast_published = asyncio.Event()

    def changed():
        if any(row.item.source.name == "fast" and row.published for row in batch.snapshot.outcomes):
            fast_published.set()

    batch = UploadBatch(plan, on_change=changed)
    task = asyncio.create_task(batch.run())
    try:
        await asyncio.wait_for(slow_reading.wait(), 15)
        await asyncio.wait_for(fast_published.wait(), 15)
        batch.cancel()
        result = await task
    finally:
        if not task.done():
            batch.cancel()
            await task
    assert result.status == UploadStatus.CANCELED
    assert next(row for row in result.outcomes if row.item.source.name == "fast").published
    calls = dict(destination.calls)
    assert "AbortMultipartUpload" in calls
    assert not {"DeleteObject", "DeleteObjects"} & calls.keys()
    async with session().client("s3", endpoint_url=s3_endpoint) as client:
        response = await client.get_object(Bucket="recursive-upload", Key="tree/fast")
        assert await response["Body"].read() == b"preserved"
        assert not (await client.list_multipart_uploads(Bucket="recursive-upload")).get("Uploads")
