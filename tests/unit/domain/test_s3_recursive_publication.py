"""Purpose-bound upload publication must never compensate with object deletion."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock

import pytest
from botocore.exceptions import ClientError

from aws_tui.domain.filesystem import ConflictError, EntryKind, FileEntry, PathRef
from aws_tui.domain.recursive_upload import UploadPlanError, UploadSourceChangedError
from aws_tui.domain.s3_fs import S3FS, _s3_revision_token
from aws_tui.domain.s3_upload import UploadPublication, UploadPublicationUnknownError

pytestmark = pytest.mark.unit
KEY = PathRef(("object",))


async def payload(data):
    if data:
        yield data


class Client:
    def __init__(self):
        self.calls = []
        self.published = asyncio.Event()
        self.release = asyncio.Event()
        self.release.set()
        self.error = None
        self.response = {"ETag": '"done"', "VersionId": "owned-version"}
        self.created = {"UploadId": "owned-upload"}
        self.part_response = {"ETag": '"part"'}
        self.abort_multipart_upload = AsyncMock(return_value={})
        self.delete_object = AsyncMock(side_effect=AssertionError("no compensating deletion"))

    async def create_multipart_upload(self, **kwargs):
        self.calls.append(("create", kwargs))
        return self.created

    async def upload_part(self, **kwargs):
        self.calls.append(("part", kwargs))
        return self.part_response

    async def publish(self, name, kwargs):
        self.calls.append((name, kwargs))
        self.published.set()
        await self.release.wait()
        if self.error:
            raise self.error
        return self.response

    async def put_object(self, **kwargs):
        return await self.publish("put", kwargs)

    async def complete_multipart_upload(self, **kwargs):
        return await self.publish("complete", kwargs)


def fs_and_client(monkeypatch):
    fs = S3FS(session=MagicMock(), bucket="bucket")
    client = Client()

    @asynccontextmanager
    async def context():
        yield client

    monkeypatch.setattr(fs, "_upload_client", context)
    return fs, client


def reviewed():
    return FileEntry(
        "object", EntryKind.FILE, 3, None, _s3_revision_token({"ContentLength": 3, "ETag": '"old"'})
    )


@pytest.mark.parametrize("data", [b"", b"new payload"])
async def test_default_creation_is_conditional_and_receipt_records_publication(monkeypatch, data):
    fs, client = fs_and_client(monkeypatch)
    receipt = UploadPublication()
    await fs.write_upload_stream(KEY, payload(data), total_size=len(data), publication=receipt)
    name, args = client.calls[-1]
    assert name == ("put" if not data else "complete")
    assert args["IfNoneMatch"] == "*"
    assert "IfMatch" not in args
    assert receipt.attempted
    assert receipt.published
    assert not receipt.uncertain
    assert receipt.version_id == "owned-version"
    client.delete_object.assert_not_awaited()


@pytest.mark.parametrize("data", [b"", b"new payload"])
async def test_reviewed_overwrite_is_explicit_and_conditioned_on_reviewed_etag(monkeypatch, data):
    fs, client = fs_and_client(monkeypatch)
    receipt = UploadPublication()
    await fs.write_upload_stream(
        KEY,
        payload(data),
        total_size=len(data),
        publication=receipt,
        reviewed=reviewed(),
        overwrite=True,
    )
    _, args = client.calls[-1]
    assert args["IfMatch"] == '"old"'
    assert "IfNoneMatch" not in args


async def test_known_collision_cannot_be_overwritten_by_default(monkeypatch):
    fs, client = fs_and_client(monkeypatch)
    with pytest.raises(ConflictError):
        await fs.write_upload_stream(
            KEY, payload(b"new"), total_size=3, publication=UploadPublication(), reviewed=reviewed()
        )
    assert not client.calls


@pytest.mark.parametrize("data", [b"", b"new payload"])
async def test_canceled_successful_publication_is_retained_without_delete(monkeypatch, data):
    fs, client = fs_and_client(monkeypatch)
    client.release.clear()
    receipt = UploadPublication()
    task = asyncio.create_task(
        fs.write_upload_stream(KEY, payload(data), total_size=len(data), publication=receipt)
    )
    await asyncio.wait_for(client.published.wait(), 1)
    task.cancel()
    client.release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert receipt.published
    assert not receipt.uncertain
    assert receipt.version_id == "owned-version"
    client.delete_object.assert_not_awaited()
    client.abort_multipart_upload.assert_not_awaited()


@pytest.mark.parametrize("code", ["PreconditionFailed", "ConditionalRequestConflict"])
async def test_single_attempt_authoritative_conflict_is_not_uncertain(monkeypatch, code):
    fs, client = fs_and_client(monkeypatch)
    client.error = ClientError({"Error": {"Code": code}}, "CompleteMultipartUpload")
    receipt = UploadPublication()
    with pytest.raises(ConflictError):
        await fs.write_upload_stream(KEY, payload(b"new"), total_size=3, publication=receipt)
    assert receipt.attempted
    assert not receipt.published
    assert not receipt.uncertain
    client.abort_multipart_upload.assert_awaited_once_with(
        Bucket="bucket", Key="object", UploadId="owned-upload"
    )
    client.delete_object.assert_not_awaited()


@pytest.mark.parametrize("data", [b"", b"new"])
async def test_lost_publication_response_is_unknown_and_never_rolled_back(monkeypatch, data):
    fs, client = fs_and_client(monkeypatch)
    client.error = RuntimeError("response lost")
    receipt = UploadPublication()
    with pytest.raises(UploadPublicationUnknownError):
        await fs.write_upload_stream(KEY, payload(data), total_size=len(data), publication=receipt)
    assert receipt.attempted
    assert receipt.uncertain
    assert not receipt.published
    client.delete_object.assert_not_awaited()


async def test_new_publication_client_disables_transparent_retry_and_preserves_endpoint_settings():
    session = MagicMock()
    fs = S3FS(
        session=session, bucket="bucket", endpoint_url="http://127.0.0.1:9000", verify_tls=False
    )
    fs._upload_client()
    kwargs = session.client.call_args.kwargs
    assert kwargs["endpoint_url"] == "http://127.0.0.1:9000"
    assert kwargs["verify"] is False
    assert kwargs["config"].retries["total_max_attempts"] == 1
    assert kwargs["config"].connect_timeout == fs._config.connect_timeout
    assert fs._config.retries["total_max_attempts"] == 6


async def test_receipt_cannot_be_reused_while_first_source_read_is_pending(monkeypatch):
    fs, client = fs_and_client(monkeypatch)
    entered = asyncio.Event()

    async def held_source():
        entered.set()
        await asyncio.Event().wait()
        yield b"never"

    receipt = UploadPublication()
    source = held_source()
    first = asyncio.create_task(
        fs.write_upload_stream(KEY, source, total_size=5, publication=receipt)
    )
    try:
        await asyncio.wait_for(entered.wait(), 1)
        with pytest.raises(ConflictError, match="replayed"):
            await fs.write_upload_stream(KEY, payload(b""), total_size=0, publication=receipt)
        assert not client.calls
    finally:
        first.cancel()
        await asyncio.gather(first, return_exceptions=True)
        await source.aclose()


@pytest.mark.parametrize(("data", "size"), [(b"", 3), (b"abc", 0), (b"abc", 8)])
async def test_wrong_byte_count_never_publishes_an_object(monkeypatch, data, size):
    fs, client = fs_and_client(monkeypatch)
    receipt = UploadPublication()
    with pytest.raises(UploadSourceChangedError):
        await fs.write_upload_stream(KEY, payload(data), total_size=size, publication=receipt)
    assert not receipt.attempted
    assert not receipt.published
    assert not any(name in {"put", "complete"} for name, _ in client.calls)
    client.delete_object.assert_not_awaited()


@pytest.mark.parametrize("data", [b"", b"abc"])
@pytest.mark.parametrize(
    "response", [None, {}, {"ETag": ""}, {"ETag": 7}, {"ETag": '"ok"', "VersionId": 7}]
)
async def test_malformed_final_response_is_unknown_not_success(monkeypatch, data, response):
    fs, client = fs_and_client(monkeypatch)
    client.response = response
    receipt = UploadPublication()
    with pytest.raises(UploadPublicationUnknownError):
        await fs.write_upload_stream(KEY, payload(data), total_size=len(data), publication=receipt)
    assert receipt.uncertain
    assert not receipt.published
    client.delete_object.assert_not_awaited()


@pytest.mark.parametrize("upload_id", [None, 7, ""])
async def test_malformed_multipart_id_is_not_claimed_used_or_aborted(monkeypatch, upload_id):
    fs, client = fs_and_client(monkeypatch)
    client.created = {"UploadId": upload_id}
    receipt = UploadPublication()
    with pytest.raises(UploadPlanError, match="identity"):
        await fs.write_upload_stream(KEY, payload(b"abc"), total_size=3, publication=receipt)
    assert not receipt.attempted
    assert receipt.cleanup_error
    assert [name for name, _ in client.calls] == ["create"]
    client.abort_multipart_upload.assert_not_awaited()


async def test_absent_multipart_cleanup_does_not_mask_unknown_publication(monkeypatch):
    fs, client = fs_and_client(monkeypatch)
    client.error = RuntimeError("publication response lost")
    client.abort_multipart_upload.side_effect = ClientError(
        {"Error": {"Code": "NoSuchUpload"}}, "AbortMultipartUpload"
    )
    receipt = UploadPublication()
    with pytest.raises(UploadPublicationUnknownError):
        await fs.write_upload_stream(KEY, payload(b"abc"), total_size=3, publication=receipt)
    assert receipt.uncertain
    assert not receipt.published
    assert receipt.cleanup_error is None
    client.delete_object.assert_not_awaited()


@pytest.mark.parametrize("response", [None, {"ETag": 7}, {"ETag": {}}, {"ETag": b"bad"}])
async def test_malformed_part_is_refused_before_final_publication(monkeypatch, response):
    fs, client = fs_and_client(monkeypatch)
    client.part_response = response
    receipt = UploadPublication()
    with pytest.raises(UploadPlanError, match="part"):
        await fs.write_upload_stream(KEY, payload(b"abc"), total_size=3, publication=receipt)
    assert not receipt.attempted
    assert not receipt.published
    assert [name for name, _ in client.calls] == ["create", "part"]
    client.abort_multipart_upload.assert_awaited_once_with(
        Bucket="bucket", Key="object", UploadId="owned-upload"
    )
