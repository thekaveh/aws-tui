from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from aws_tui.domain.filesystem import PathRef, ProviderError
from aws_tui.domain.s3_fs import S3FS, _multipart_part_size

pytestmark = pytest.mark.unit


async def _source() -> AsyncIterator[bytes]:
    yield b"payload"


class _BlockingMultipartClient:
    def __init__(self) -> None:
        self.upload_started = asyncio.Event()
        self.release_upload = asyncio.Event()
        self.abort_multipart_upload = AsyncMock(return_value={})

    async def create_multipart_upload(self, **_kwargs: Any) -> dict[str, str]:
        return {"UploadId": "upload-1"}

    async def upload_part(self, **_kwargs: Any) -> dict[str, str]:
        self.upload_started.set()
        await self.release_upload.wait()
        return {"ETag": '"etag-1"'}


class _AbortFailingClient:
    abort_multipart_upload = AsyncMock(side_effect=RuntimeError("abort unavailable"))

    async def create_multipart_upload(self, **_kwargs: Any) -> dict[str, str]:
        return {"UploadId": "upload-orphaned"}

    async def upload_part(self, **_kwargs: Any) -> dict[str, str]:
        raise RuntimeError("part failed")


class _EmptyUploadClient:
    def __init__(self) -> None:
        self.put_object = AsyncMock(return_value={})
        self.create_multipart_upload = AsyncMock(side_effect=AssertionError("unexpected MPU"))
        self.abort_multipart_upload = AsyncMock(side_effect=AssertionError("unexpected abort"))


class _SuccessfulMultipartClient:
    def __init__(self) -> None:
        self.create_multipart_upload = AsyncMock(return_value={"UploadId": "upload-1"})
        self.upload_part = AsyncMock(return_value={"ETag": '"etag-1"'})
        self.complete_multipart_upload = AsyncMock(return_value={})
        self.abort_multipart_upload = AsyncMock(return_value={})


class _BlockingCreateClient:
    def __init__(self) -> None:
        self.create_started = asyncio.Event()
        self.release_create = asyncio.Event()
        self.abort_multipart_upload = AsyncMock(return_value={})

    async def create_multipart_upload(self, **_kwargs: Any) -> dict[str, str]:
        self.create_started.set()
        await self.release_create.wait()
        return {"UploadId": "created-after-cancel"}


class _BlockingAbortClient(_BlockingMultipartClient):
    def __init__(self) -> None:
        super().__init__()
        del self.abort_multipart_upload
        self.abort_started = asyncio.Event()
        self.release_abort = asyncio.Event()

    async def abort_multipart_upload(self, **_kwargs: Any) -> dict[str, str]:
        self.abort_started.set()
        await self.release_abort.wait()
        return {}


class _FailedUploadBlockingAbortClient(_BlockingAbortClient):
    async def upload_part(self, **_kwargs: Any) -> dict[str, str]:
        raise RuntimeError("part failed")


class _BlockingPutResponseClient(_EmptyUploadClient):
    def __init__(self, *, version_id: str | None) -> None:
        super().__init__()
        del self.put_object
        self.version_id = version_id
        self.committed = asyncio.Event()
        self.release_response = asyncio.Event()
        self.delete_object = AsyncMock(return_value={})
        self.put_args: dict[str, Any] = {}

    async def put_object(self, **kwargs: Any) -> dict[str, str]:
        self.put_args = kwargs
        self.committed.set()
        await self.release_response.wait()
        response = {"ETag": '"empty-etag"'}
        if self.version_id is not None:
            response["VersionId"] = self.version_id
        return response


class _BlockingCompleteResponseClient(_SuccessfulMultipartClient):
    def __init__(self, *, version_id: str | None) -> None:
        super().__init__()
        del self.complete_multipart_upload
        self.version_id = version_id
        self.committed = asyncio.Event()
        self.release_response = asyncio.Event()
        self.delete_object = AsyncMock(return_value={})
        self.complete_args: dict[str, Any] = {}

    async def complete_multipart_upload(self, **kwargs: Any) -> dict[str, str]:
        self.complete_args = kwargs
        self.committed.set()
        await self.release_response.wait()
        response = {"ETag": '"multipart-etag"'}
        if self.version_id is not None:
            response["VersionId"] = self.version_id
        return response


class _FailingBlockingPutClient(_BlockingPutResponseClient):
    async def put_object(self, **kwargs: Any) -> dict[str, str]:
        self.put_args = kwargs
        self.committed.set()
        await self.release_response.wait()
        raise RuntimeError("put response unavailable")


class _FailingBlockingCompleteClient(_BlockingCompleteResponseClient):
    async def complete_multipart_upload(self, **kwargs: Any) -> dict[str, str]:
        self.complete_args = kwargs
        self.committed.set()
        await self.release_response.wait()
        raise RuntimeError("complete response unavailable")


class _FailingRollbackPutClient(_BlockingPutResponseClient):
    def __init__(self) -> None:
        super().__init__(version_id="owned-version")
        self.delete_object = AsyncMock(side_effect=RuntimeError("rollback unavailable"))


class _BlockingRollbackPutClient(_BlockingPutResponseClient):
    def __init__(self) -> None:
        super().__init__(version_id="owned-version")
        del self.delete_object
        self.delete_started = asyncio.Event()
        self.release_delete = asyncio.Event()

    async def delete_object(self, **_kwargs: Any) -> dict[str, str]:
        self.delete_started.set()
        await self.release_delete.wait()
        return {}


async def test_cancelled_write_aborts_multipart_upload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _BlockingMultipartClient()

    @asynccontextmanager
    async def client_context() -> AsyncIterator[_BlockingMultipartClient]:
        yield client

    fs = S3FS(session=MagicMock(), bucket="bucket")
    monkeypatch.setattr(fs, "_client", client_context)
    task = asyncio.create_task(fs.write_stream(PathRef.from_posix("/key"), _source(), total_size=7))
    await asyncio.wait_for(client.upload_started.wait(), timeout=1)

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    client.abort_multipart_upload.assert_awaited_once_with(
        Bucket="bucket", Key="key", UploadId="upload-1"
    )


async def test_repeated_cancellation_durably_drains_multipart_abort(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _BlockingAbortClient()

    @asynccontextmanager
    async def client_context() -> AsyncIterator[_BlockingAbortClient]:
        yield client

    fs = S3FS(session=MagicMock(), bucket="bucket")
    monkeypatch.setattr(fs, "_client", client_context)
    write = asyncio.create_task(
        fs.write_stream(PathRef.from_posix("/key"), _source(), total_size=7)
    )
    await client.upload_started.wait()

    write.cancel()
    await client.abort_started.wait()
    write.cancel()
    await asyncio.sleep(0)
    assert not write.done()

    client.release_abort.set()
    with pytest.raises(asyncio.CancelledError):
        await write


async def test_cancellation_during_failed_upload_abort_takes_priority(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _FailedUploadBlockingAbortClient()

    @asynccontextmanager
    async def client_context() -> AsyncIterator[_FailedUploadBlockingAbortClient]:
        yield client

    fs = S3FS(session=MagicMock(), bucket="bucket")
    monkeypatch.setattr(fs, "_client", client_context)
    write = asyncio.create_task(
        fs.write_stream(PathRef.from_posix("/key"), _source(), total_size=7)
    )
    await client.abort_started.wait()

    write.cancel()
    client.release_abort.set()
    with pytest.raises(asyncio.CancelledError):
        await write


async def test_failed_abort_surfaces_upload_id(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _AbortFailingClient()

    @asynccontextmanager
    async def client_context() -> AsyncIterator[_AbortFailingClient]:
        yield client

    fs = S3FS(session=MagicMock(), bucket="bucket")
    monkeypatch.setattr(fs, "_client", client_context)

    with pytest.raises(ProviderError, match=r"upload-orphaned.*manual abort"):
        await fs.write_stream(PathRef.from_posix("/key"), _source(), total_size=7)


def test_multipart_part_size_adapts_to_known_large_object() -> None:
    total_size = 80 * 1024 * 1024 * 1024

    part_size = _multipart_part_size(total_size)

    assert part_size > 8 * 1024 * 1024
    assert (total_size + part_size - 1) // part_size <= 10_000


def test_multipart_part_size_rejects_object_over_s3_limit() -> None:
    maximum_size = 10_000 * 5 * 1024**3

    assert _multipart_part_size(maximum_size) == 5 * 1024**3
    with pytest.raises(ProviderError, match="50 TB"):
        _multipart_part_size(maximum_size + 1)


async def test_empty_write_uses_put_object_without_multipart(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _EmptyUploadClient()

    async def empty_source() -> AsyncIterator[bytes]:
        if False:
            yield b""

    @asynccontextmanager
    async def client_context() -> AsyncIterator[_EmptyUploadClient]:
        yield client

    fs = S3FS(session=MagicMock(), bucket="bucket")
    monkeypatch.setattr(fs, "_client", client_context)
    await fs.write_stream(PathRef.from_posix("/empty"), empty_source(), total_size=0)

    client.put_object.assert_awaited_once_with(Bucket="bucket", Key="empty", Body=b"")
    client.create_multipart_upload.assert_not_awaited()
    client.abort_multipart_upload.assert_not_awaited()


async def test_exclusive_empty_write_uses_s3_precondition(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _EmptyUploadClient()

    async def empty_source() -> AsyncIterator[bytes]:
        if False:
            yield b""

    @asynccontextmanager
    async def client_context() -> AsyncIterator[_EmptyUploadClient]:
        yield client

    fs = S3FS(session=MagicMock(), bucket="bucket")
    monkeypatch.setattr(fs, "_client", client_context)
    await fs.write_stream(
        PathRef.from_posix("/empty"), empty_source(), total_size=0, overwrite=False
    )

    client.put_object.assert_awaited_once_with(
        Bucket="bucket", Key="empty", Body=b"", IfNoneMatch="*"
    )


async def test_exclusive_multipart_write_conditions_completion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _SuccessfulMultipartClient()

    @asynccontextmanager
    async def client_context() -> AsyncIterator[_SuccessfulMultipartClient]:
        yield client

    fs = S3FS(session=MagicMock(), bucket="bucket")
    monkeypatch.setattr(fs, "_client", client_context)
    await fs.write_stream(PathRef.from_posix("/key"), _source(), total_size=7, overwrite=False)

    client.complete_multipart_upload.assert_awaited_once_with(
        Bucket="bucket",
        Key="key",
        UploadId="upload-1",
        MultipartUpload={"Parts": [{"ETag": '"etag-1"', "PartNumber": 1}]},
        IfNoneMatch="*",
    )
    client.abort_multipart_upload.assert_not_awaited()


async def test_cancel_during_create_drains_response_and_aborts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _BlockingCreateClient()

    @asynccontextmanager
    async def client_context() -> AsyncIterator[_BlockingCreateClient]:
        yield client

    fs = S3FS(session=MagicMock(), bucket="bucket")
    monkeypatch.setattr(fs, "_client", client_context)
    write = asyncio.create_task(fs.write_stream(PathRef.from_posix("/key"), _source()))
    await client.create_started.wait()
    write.cancel()
    client.release_create.set()
    with pytest.raises(asyncio.CancelledError):
        await write

    client.abort_multipart_upload.assert_awaited_once_with(
        Bucket="bucket", Key="key", UploadId="created-after-cancel"
    )


@pytest.mark.parametrize("overwrite", [False, True])
@pytest.mark.parametrize("version_id", [None, "owned-version"])
async def test_cancel_after_put_commit_rolls_back_only_owned_version(
    monkeypatch: pytest.MonkeyPatch,
    overwrite: bool,
    version_id: str | None,
) -> None:
    client = _BlockingPutResponseClient(version_id=version_id)

    async def empty_source() -> AsyncIterator[bytes]:
        if False:
            yield b""

    @asynccontextmanager
    async def client_context() -> AsyncIterator[_BlockingPutResponseClient]:
        yield client

    fs = S3FS(session=MagicMock(), bucket="bucket")
    monkeypatch.setattr(fs, "_client", client_context)
    write = asyncio.create_task(
        fs.write_stream(
            PathRef.from_posix("/key"),
            empty_source(),
            total_size=0,
            overwrite=overwrite,
        )
    )
    await client.committed.wait()

    write.cancel()
    client.release_response.set()
    with pytest.raises(asyncio.CancelledError) as raised:
        await write

    assert client.put_args.get("IfNoneMatch") == (None if overwrite else "*")
    if version_id is None:
        client.delete_object.assert_not_awaited()
        assert any("manual cleanup required" in note for note in raised.value.__notes__)
    else:
        client.delete_object.assert_awaited_once_with(
            Bucket="bucket", Key="key", VersionId=version_id
        )


@pytest.mark.parametrize("overwrite", [False, True])
@pytest.mark.parametrize("version_id", [None, "owned-version"])
async def test_cancel_after_multipart_commit_rolls_back_only_owned_version(
    monkeypatch: pytest.MonkeyPatch,
    overwrite: bool,
    version_id: str | None,
) -> None:
    client = _BlockingCompleteResponseClient(version_id=version_id)

    @asynccontextmanager
    async def client_context() -> AsyncIterator[_BlockingCompleteResponseClient]:
        yield client

    fs = S3FS(session=MagicMock(), bucket="bucket")
    monkeypatch.setattr(fs, "_client", client_context)
    write = asyncio.create_task(
        fs.write_stream(
            PathRef.from_posix("/key"),
            _source(),
            total_size=7,
            overwrite=overwrite,
        )
    )
    await client.committed.wait()

    write.cancel()
    client.release_response.set()
    with pytest.raises(asyncio.CancelledError) as raised:
        await write

    assert client.complete_args.get("IfNoneMatch") == (None if overwrite else "*")
    client.abort_multipart_upload.assert_not_awaited()
    if version_id is None:
        client.delete_object.assert_not_awaited()
        assert any("manual cleanup required" in note for note in raised.value.__notes__)
    else:
        client.delete_object.assert_awaited_once_with(
            Bucket="bucket", Key="key", VersionId=version_id
        )


@pytest.mark.parametrize("multipart", [False, True])
async def test_cancellation_stays_primary_when_publication_response_fails(
    monkeypatch: pytest.MonkeyPatch,
    multipart: bool,
) -> None:
    client: _FailingBlockingPutClient | _FailingBlockingCompleteClient
    if multipart:
        client = _FailingBlockingCompleteClient(version_id=None)
        source = _source()
        total_size = 7
    else:
        client = _FailingBlockingPutClient(version_id=None)

        async def empty_source() -> AsyncIterator[bytes]:
            if False:
                yield b""

        source = empty_source()
        total_size = 0

    @asynccontextmanager
    async def client_context() -> AsyncIterator[Any]:
        yield client

    fs = S3FS(session=MagicMock(), bucket="bucket")
    monkeypatch.setattr(fs, "_client", client_context)
    write = asyncio.create_task(
        fs.write_stream(PathRef.from_posix("/key"), source, total_size=total_size)
    )
    await client.committed.wait()

    write.cancel()
    client.release_response.set()
    with pytest.raises(asyncio.CancelledError) as raised:
        await write

    assert any("publication also failed" in note for note in raised.value.__notes__)
    if multipart:
        client.abort_multipart_upload.assert_awaited_once_with(
            Bucket="bucket", Key="key", UploadId="upload-1"
        )


async def test_cancelled_publication_reports_failed_exact_version_rollback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _FailingRollbackPutClient()

    async def empty_source() -> AsyncIterator[bytes]:
        if False:
            yield b""

    @asynccontextmanager
    async def client_context() -> AsyncIterator[_FailingRollbackPutClient]:
        yield client

    fs = S3FS(session=MagicMock(), bucket="bucket")
    monkeypatch.setattr(fs, "_client", client_context)
    write = asyncio.create_task(
        fs.write_stream(PathRef.from_posix("/key"), empty_source(), total_size=0)
    )
    await client.committed.wait()

    write.cancel()
    client.release_response.set()
    with pytest.raises(asyncio.CancelledError) as raised:
        await write

    assert any("exact-version rollback failed" in note for note in raised.value.__notes__)


async def test_repeated_cancellation_drains_exact_version_rollback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _BlockingRollbackPutClient()

    async def empty_source() -> AsyncIterator[bytes]:
        if False:
            yield b""

    @asynccontextmanager
    async def client_context() -> AsyncIterator[_BlockingRollbackPutClient]:
        yield client

    fs = S3FS(session=MagicMock(), bucket="bucket")
    monkeypatch.setattr(fs, "_client", client_context)
    write = asyncio.create_task(
        fs.write_stream(PathRef.from_posix("/key"), empty_source(), total_size=0)
    )
    await client.committed.wait()
    write.cancel()
    client.release_response.set()
    await client.delete_started.wait()

    write.cancel()
    await asyncio.sleep(0)
    assert not write.done()
    client.release_delete.set()

    with pytest.raises(asyncio.CancelledError):
        await write
