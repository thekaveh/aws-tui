"""S3 comparison reads full bytes conditionally and closes every owned read."""

from __future__ import annotations

import asyncio
import hashlib
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest
from botocore.exceptions import ClientError

from aws_tui.domain.filesystem import ConflictError, PathRef, PermissionDeniedError
from aws_tui.domain.recursive_upload import UploadPlanError
from aws_tui.domain.s3_fs import S3FS

pytestmark = pytest.mark.unit
KEY = PathRef(("literal", "file"))


class Body:
    def __init__(self, chunks):
        self.chunks = list(chunks)
        self.closed = False
        self.read_sizes = []

    async def read(self, size):
        self.read_sizes.append(size)
        return self.chunks.pop(0) if self.chunks else b""

    def close(self):
        self.closed = True


async def provider(monkeypatch, *, data=b"data", version="v1", body=None):
    head = {
        "ContentLength": len(data),
        "ETag": '"opaque-encrypted-2"',
        "LastModified": datetime(2026, 1, 1, tzinfo=UTC),
    }
    if version is not None:
        head["VersionId"] = version
    body = Body([data]) if body is None else body
    client = MagicMock()
    client.head_object = AsyncMock(return_value=dict(head))
    client.list_objects_v2 = AsyncMock(return_value={"KeyCount": 0})
    client.get_object = AsyncMock(return_value={**head, "Body": body})
    exits = []

    @asynccontextmanager
    async def context():
        try:
            yield client
        finally:
            exits.append(True)

    fs = S3FS(session=MagicMock(), bucket="bucket", prefix="prefix")
    monkeypatch.setattr(fs, "_client", context)
    expected = await fs.inspect_upload_destination(KEY)
    exits.clear()
    return fs, client, body, expected, head, exits


@pytest.mark.parametrize("data", [b"", b"data", b"same size other bytes"])
@pytest.mark.parametrize("version", [None, "v1", "null"])
async def test_full_object_checksum_binds_reviewed_revision_and_closes(monkeypatch, data, version):
    fs, client, body, expected, _, exits = await provider(monkeypatch, data=data, version=version)
    result = await fs.read_sync_checksum(KEY, expected=expected)
    assert (result.algorithm, result.kind) == ("SHA256", "FULL_OBJECT")
    assert result.digest == hashlib.sha256(data).hexdigest()
    assert result.size == len(data)
    assert result.revision == expected.etag
    request = {"Bucket": "bucket", "Key": "prefix/literal/file", "IfMatch": '"opaque-encrypted-2"'}
    if version not in {None, "null"}:
        request["VersionId"] = version
    client.get_object.assert_awaited_once_with(**request)
    assert body.closed
    assert all(0 < size <= 8 * 1024 * 1024 for size in body.read_sizes)
    assert len(exits) == 2  # The read and the current-key reobservation both drain.


@pytest.mark.parametrize(
    "change",
    [
        {"ETag": '"new"'},
        {"VersionId": "v2"},
        {"ContentLength": 5},
        {"LastModified": datetime(2026, 1, 1, tzinfo=UTC) + timedelta(seconds=1)},
    ],
)
async def test_changed_get_response_refuses_even_before_payload_read(monkeypatch, change):
    fs, client, body, expected, head, _ = await provider(monkeypatch)
    client.get_object.return_value = {**head, **change, "Body": body}
    with pytest.raises(ConflictError, match="changed"):
        await fs.read_sync_checksum(KEY, expected=expected)
    assert body.closed
    assert not body.read_sizes


@pytest.mark.parametrize("chunks", [[b"da"], [b"data!"], ["not bytes"]])
async def test_short_overlong_or_malformed_stream_is_not_a_verified_checksum(monkeypatch, chunks):
    fs, _, body, expected, _, _ = await provider(monkeypatch, body=Body(chunks))
    with pytest.raises(UploadPlanError):
        await fs.read_sync_checksum(KEY, expected=expected)
    assert body.closed


async def test_reading_old_version_does_not_prove_the_current_key_unchanged(monkeypatch):
    fs, client, body, expected, head, _ = await provider(monkeypatch)
    client.head_object.return_value = {**head, "VersionId": "v2"}
    with pytest.raises(ConflictError, match="changed"):
        await fs.read_sync_checksum(KEY, expected=expected)
    assert body.closed


@pytest.mark.parametrize("code", ["NoSuchKey", "NoSuchVersion", "PreconditionFailed"])
async def test_missing_or_changed_read_is_conflict_without_unconditional_fallback(
    monkeypatch, code
):
    fs, client, _, expected, _, _ = await provider(monkeypatch)
    client.get_object.side_effect = ClientError({"Error": {"Code": code}}, "GetObject")
    with pytest.raises(ConflictError):
        await fs.read_sync_checksum(KEY, expected=expected)
    assert client.get_object.await_count == 1


async def test_denied_checksum_read_remains_denied_not_absent(monkeypatch):
    fs, client, _, expected, _, _ = await provider(monkeypatch)
    client.get_object.side_effect = ClientError({"Error": {"Code": "AccessDenied"}}, "GetObject")
    with pytest.raises(PermissionDeniedError):
        await fs.read_sync_checksum(KEY, expected=expected)


@pytest.mark.parametrize("change", [{"ETag": 47}, {"LastModified": "invalid"}, {"VersionId": 47}])
async def test_malformed_response_identity_is_a_named_error_and_closes_body(monkeypatch, change):
    fs, client, body, expected, head, _ = await provider(monkeypatch)
    client.get_object.return_value = {**head, **change, "Body": body}
    with pytest.raises(UploadPlanError, match="Malformed"):
        await fs.read_sync_checksum(KEY, expected=expected)
    assert body.closed
    assert not body.read_sizes


async def test_repeated_cancel_drains_body_and_gated_client_cleanup(monkeypatch):
    entered, closing, release = asyncio.Event(), asyncio.Event(), asyncio.Event()

    class HeldBody(Body):
        async def read(self, size):
            entered.set()
            await asyncio.Event().wait()

    fs, client, body, expected, _, _ = await provider(monkeypatch, body=HeldBody([]))

    @asynccontextmanager
    async def held_context():
        try:
            yield client
        finally:
            closing.set()
            await release.wait()

    monkeypatch.setattr(fs, "_client", held_context)
    task = asyncio.create_task(fs.read_sync_checksum(KEY, expected=expected))
    try:
        await asyncio.wait_for(entered.wait(), 3)
        task.cancel()
        await asyncio.wait_for(closing.wait(), 3)
        task.cancel()
        await asyncio.sleep(0)
        assert body.closed
        assert not task.done()
    finally:
        release.set()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    assert task.cancelled()


@pytest.mark.parametrize("resource", ["body", "client"])
async def test_first_cancellation_during_cleanup_still_drains_resource(monkeypatch, resource):
    closing, release = asyncio.Event(), asyncio.Event()

    class ClosingBody(Body):
        async def close(self):
            closing.set()
            await release.wait()
            self.closed = True

    fs, client, body, expected, _, _ = await provider(
        monkeypatch,
        body=ClosingBody([b"data"]) if resource == "body" else None,
    )
    if resource == "client":

        @asynccontextmanager
        async def context():
            try:
                yield client
            finally:
                closing.set()
                await release.wait()

        monkeypatch.setattr(fs, "_client", context)
    task = asyncio.create_task(fs.read_sync_checksum(KEY, expected=expected))
    try:
        await asyncio.wait_for(closing.wait(), 3)
        task.cancel()
        await asyncio.sleep(0.01)
        assert not task.done()
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)
    assert task.cancelled()
    assert body.closed


async def test_client_entry_swallowing_cancel_cannot_start_get(monkeypatch):
    fs, client, _, expected, _, _ = await provider(monkeypatch)
    entered, release = asyncio.Event(), asyncio.Event()

    @asynccontextmanager
    async def context():
        entered.set()
        try:
            await release.wait()
        except asyncio.CancelledError:
            await release.wait()
        yield client

    monkeypatch.setattr(fs, "_client", context)
    task = asyncio.create_task(fs.read_sync_checksum(KEY, expected=expected))
    try:
        await asyncio.wait_for(entered.wait(), 3)
        task.cancel()
        await asyncio.sleep(0)
        release.set()
        await asyncio.gather(task, return_exceptions=True)
        assert not client.get_object.await_count
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)
    assert task.cancelled()


async def test_swallowed_exit_cancellation_starts_no_current_key_reobservation(monkeypatch):
    fs, client, _, expected, _, _ = await provider(monkeypatch)
    closing, release = asyncio.Event(), asyncio.Event()
    client.head_object.reset_mock()

    @asynccontextmanager
    async def context():
        try:
            yield client
        finally:
            closing.set()
            try:
                await release.wait()
            except asyncio.CancelledError:
                await release.wait()

    monkeypatch.setattr(fs, "_client", context)
    task = asyncio.create_task(fs.read_sync_checksum(KEY, expected=expected))
    try:
        await asyncio.wait_for(closing.wait(), 3)
        task.cancel()
        await asyncio.sleep(0)
        release.set()
        await asyncio.gather(task, return_exceptions=True)
        assert not client.head_object.await_count
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)
    assert task.cancelled()


async def test_malformed_reobservation_is_a_named_provider_error(monkeypatch):
    fs, client, body, expected, _, _ = await provider(monkeypatch)
    client.list_objects_v2.return_value = None
    with pytest.raises(UploadPlanError):
        await fs.read_sync_checksum(KEY, expected=expected)
    assert body.closed


@pytest.mark.parametrize("malformed", [47, [], False, None])
async def test_malformed_current_head_is_not_unknown_absence(monkeypatch, malformed):
    fs, client, body, expected, _, _ = await provider(monkeypatch)
    client.head_object.return_value = malformed
    with pytest.raises(UploadPlanError, match="observation"):
        await fs.read_sync_checksum(KEY, expected=expected)
    assert body.closed


async def test_first_cancel_during_reobservation_exit_drains_that_client(monkeypatch):
    fs, client, body, expected, _, _ = await provider(monkeypatch)
    closing, release = asyncio.Event(), asyncio.Event()
    count = 0

    @asynccontextmanager
    async def context():
        nonlocal count
        count += 1
        current = count
        try:
            yield client
        finally:
            if current == 2:
                closing.set()
                await release.wait()

    monkeypatch.setattr(fs, "_client", context)
    task = asyncio.create_task(fs.read_sync_checksum(KEY, expected=expected))
    try:
        await asyncio.wait_for(closing.wait(), 3)
        task.cancel()
        await asyncio.sleep(0.01)
        assert not task.done()
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)
    assert task.cancelled()
    assert body.closed


async def test_current_head_swallowing_cancel_does_not_start_prefix_list(monkeypatch):
    fs, client, body, expected, head, _ = await provider(monkeypatch)
    entered, release = asyncio.Event(), asyncio.Event()

    async def held_head(**kwargs):
        entered.set()
        try:
            await release.wait()
        except asyncio.CancelledError:
            await release.wait()
        return head

    client.head_object = AsyncMock(side_effect=held_head)
    client.list_objects_v2.reset_mock()
    task = asyncio.create_task(fs.read_sync_checksum(KEY, expected=expected))
    try:
        await asyncio.wait_for(entered.wait(), 3)
        task.cancel()
        await asyncio.sleep(0)
        release.set()
        await asyncio.gather(task, return_exceptions=True)
        assert not client.list_objects_v2.await_count
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)
    assert task.cancelled()
    assert body.closed
