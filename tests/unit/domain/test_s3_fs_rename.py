from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from botocore.exceptions import ClientError

from aws_tui.domain.filesystem import (
    ConflictError,
    EntryKind,
    FileEntry,
    NotFoundError,
    PathRef,
    ProviderError,
)
from aws_tui.domain.s3_fs import S3FS

pytestmark = pytest.mark.unit


class _CopyClient:
    def __init__(self) -> None:
        self.destination_size = 5 * 1024**3 + 1
        self.destination_etag = '"multipart-etag"'
        self.destination_version_id: str | None = None
        self.copy_object: Any = AsyncMock(
            side_effect=lambda **_kwargs: self.copy_response('"copy-etag"', nested=True)
        )
        self.create_multipart_upload = AsyncMock(return_value={"UploadId": "copy-1"})
        self.upload_part_copy = AsyncMock(
            side_effect=lambda **kwargs: {
                "CopyPartResult": {"ETag": f'"etag-{kwargs["PartNumber"]}"'}
            }
        )
        self.complete_multipart_upload: Any = AsyncMock(
            side_effect=lambda **_kwargs: self.copy_response('"multipart-etag"', nested=False)
        )
        self.abort_multipart_upload = AsyncMock(return_value={})
        self.delete_object = AsyncMock(return_value={})
        self.head_object = AsyncMock(
            side_effect=lambda **kwargs: {
                "ContentLength": 5 * 1024**3 + 1,
                "ContentType": "application/octet-stream",
                "Metadata": {"origin": "test"},
                **(
                    {
                        "ETag": self.destination_etag,
                        "ContentLength": self.destination_size,
                        **(
                            {"VersionId": self.destination_version_id}
                            if self.destination_version_id is not None
                            else {}
                        ),
                    }
                    if kwargs["Key"] == "destination"
                    else {}
                ),
            }
        )
        self.get_object_tagging = AsyncMock(
            return_value={"TagSet": [{"Key": "purpose", "Value": "rename"}]}
        )

    def copy_response(self, etag: str, *, nested: bool) -> dict[str, object]:
        response: dict[str, object] = (
            {"CopyObjectResult": {"ETag": etag}} if nested else {"ETag": etag}
        )
        if self.destination_version_id is not None:
            response["VersionId"] = self.destination_version_id
        return response


class _BlockingCommittedCopyClient(_CopyClient):
    def __init__(self) -> None:
        super().__init__()
        del self.copy_object
        self.copy_committed = asyncio.Event()
        self.release_response = asyncio.Event()

    async def copy_object(self, **_kwargs: object) -> dict[str, object]:
        self.copy_committed.set()
        await self.release_response.wait()
        return self.copy_response('"copy-etag"', nested=True)


class _BlockingCommittedMultipartCopyClient(_CopyClient):
    def __init__(self) -> None:
        super().__init__()
        del self.complete_multipart_upload
        self.copy_committed = asyncio.Event()
        self.release_response = asyncio.Event()

    async def complete_multipart_upload(self, **_kwargs: object) -> dict[str, object]:
        self.copy_committed.set()
        await self.release_response.wait()
        return self.copy_response('"multipart-etag"', nested=False)


def _entry(size: int) -> FileEntry:
    return FileEntry(
        name="source",
        kind=EntryKind.FILE,
        size=size,
        modified=datetime.now(UTC),
        etag="source-etag",
    )


async def _rename_with_size(
    monkeypatch: pytest.MonkeyPatch,
    client: _CopyClient,
    size: int,
    *,
    revision: str = "source-etag",
) -> None:
    fs = S3FS(session=MagicMock(), bucket="bucket")
    client.destination_size = size
    client.destination_etag = '"copy-etag"' if size <= 5 * 1024**3 else '"multipart-etag"'

    async def stat(path: PathRef) -> FileEntry:
        if path.name == "source":
            entry = _entry(size)
            return FileEntry(
                name=entry.name,
                kind=entry.kind,
                size=entry.size,
                modified=entry.modified,
                etag=revision,
            )
        raise NotFoundError(path.as_posix())

    @asynccontextmanager
    async def client_context() -> AsyncIterator[_CopyClient]:
        yield client

    monkeypatch.setattr(fs, "stat", stat)
    monkeypatch.setattr(fs, "_client", client_context)
    await fs.rename(PathRef.from_posix("/source"), PathRef.from_posix("/destination"))


async def test_rename_at_copy_object_limit_uses_atomic_copy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _CopyClient()
    await _rename_with_size(monkeypatch, client, 5 * 1024**3)

    client.copy_object.assert_awaited_once()
    client.create_multipart_upload.assert_not_awaited()
    client.delete_object.assert_awaited_once_with(
        Bucket="bucket", Key="source", IfMatch='"source-etag"'
    )


async def test_rename_above_copy_object_limit_uses_multipart_copy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _CopyClient()
    await _rename_with_size(monkeypatch, client, 5 * 1024**3 + 1)

    client.copy_object.assert_not_awaited()
    client.create_multipart_upload.assert_awaited_once_with(
        Bucket="bucket",
        Key="destination",
        ContentType="application/octet-stream",
        Metadata={"origin": "test"},
        Tagging="purpose=rename",
    )
    assert client.upload_part_copy.await_count > 1
    assert all(
        call.kwargs["CopySourceIfMatch"] == '"source-etag"'
        for call in client.upload_part_copy.await_args_list
    )
    client.complete_multipart_upload.assert_awaited_once()
    assert client.complete_multipart_upload.await_args is not None
    assert client.complete_multipart_upload.await_args.kwargs["IfNoneMatch"] == "*"
    client.abort_multipart_upload.assert_not_awaited()
    client.delete_object.assert_awaited_once_with(
        Bucket="bucket", Key="source", IfMatch='"source-etag"'
    )


async def test_rename_maps_concurrent_source_change_to_conflict(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _CopyClient()
    client.copy_object.side_effect = ClientError(
        {"Error": {"Code": "PreconditionFailed", "Message": "changed"}},
        "CopyObject",
    )

    with pytest.raises(ConflictError):
        await _rename_with_size(monkeypatch, client, 5 * 1024**3)

    client.delete_object.assert_not_awaited()


async def test_rename_copy_and_delete_are_conditioned_on_observed_etag(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _CopyClient()
    await _rename_with_size(monkeypatch, client, 5 * 1024**3)

    client.copy_object.assert_awaited_once_with(
        Bucket="bucket",
        Key="destination",
        CopySource={"Bucket": "bucket", "Key": "source"},
        CopySourceIfMatch='"source-etag"',
        IfNoneMatch="*",
    )
    client.delete_object.assert_awaited_once_with(
        Bucket="bucket", Key="source", IfMatch='"source-etag"'
    )


@pytest.mark.parametrize("size", [5 * 1024**3, 5 * 1024**3 + 1])
async def test_rename_removes_owned_version_when_source_delete_conflicts(
    monkeypatch: pytest.MonkeyPatch,
    size: int,
) -> None:
    client = _CopyClient()
    client.destination_version_id = "owned-version"
    client.delete_object.side_effect = [
        ClientError(
            {"Error": {"Code": "PreconditionFailed", "Message": "changed"}},
            "DeleteObject",
        ),
        {},
    ]

    with pytest.raises(ConflictError):
        await _rename_with_size(monkeypatch, client, size)

    source_delete, cleanup = [call.kwargs for call in client.delete_object.await_args_list]
    assert source_delete == {
        "Bucket": "bucket",
        "Key": "source",
        "IfMatch": '"source-etag"',
    }
    assert cleanup["Bucket"] == "bucket"
    assert cleanup["Key"] == "destination"
    assert cleanup["VersionId"] == "owned-version"
    assert "IfMatch" not in cleanup


async def test_rename_removes_owned_version_for_non_client_source_delete_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _CopyClient()
    client.destination_version_id = "owned-version"
    client.delete_object.side_effect = [RuntimeError("connection closed"), {}]

    with pytest.raises(RuntimeError, match="connection closed"):
        await _rename_with_size(monkeypatch, client, 5 * 1024**3)

    cleanup = client.delete_object.await_args_list[-1].kwargs
    assert cleanup["Bucket"] == "bucket"
    assert cleanup["Key"] == "destination"
    assert cleanup["VersionId"] == "owned-version"
    assert "IfMatch" not in cleanup


@pytest.mark.parametrize("size", [5 * 1024**3, 5 * 1024**3 + 1])
@pytest.mark.parametrize("response_version_id", [None, "", "   ", "null", "NULL", " NuLl "])
async def test_non_owned_version_id_source_failure_does_not_remove_same_etag_replacement(
    monkeypatch: pytest.MonkeyPatch,
    size: int,
    response_version_id: str | None,
) -> None:
    client = _CopyClient()
    client.destination_version_id = response_version_id
    destination_heads = 0
    replacement_installed = False

    async def head_object(**kwargs: object) -> dict[str, object]:
        nonlocal destination_heads
        if kwargs["Key"] == "source":
            return {
                "ContentLength": size,
                "ContentType": "application/octet-stream",
                "Metadata": {"origin": "test"},
            }
        destination_heads += 1
        return {"ETag": client.destination_etag, "ContentLength": size}

    async def delete_object(**kwargs: object) -> dict[str, object]:
        nonlocal replacement_installed
        if kwargs["Key"] == "source":
            replacement_installed = True
            raise RuntimeError("source delete failed after replacement")
        pytest.fail("an unversioned destination must never be deleted during rollback")

    client.head_object.side_effect = head_object
    client.delete_object.side_effect = delete_object

    with pytest.raises(ProviderError, match=r"manual cleanup required.*VersionId"):
        await _rename_with_size(monkeypatch, client, size)

    assert replacement_installed
    assert destination_heads == 0
    assert client.delete_object.await_count == 1


@pytest.mark.parametrize(
    ("size", "client_type"),
    [
        (5 * 1024**3, _BlockingCommittedCopyClient),
        (5 * 1024**3 + 1, _BlockingCommittedMultipartCopyClient),
    ],
)
@pytest.mark.parametrize("response_version_id", [None, "", "   ", "null", "NULL", " NuLl "])
async def test_non_owned_version_id_cancelled_copy_does_not_remove_same_etag_replacement(
    monkeypatch: pytest.MonkeyPatch,
    size: int,
    client_type: type[_BlockingCommittedCopyClient] | type[_BlockingCommittedMultipartCopyClient],
    response_version_id: str | None,
) -> None:
    client = client_type()
    client.destination_version_id = response_version_id
    destination_heads = 0
    replacement_installed = False

    async def head_object(**kwargs: object) -> dict[str, object]:
        nonlocal destination_heads
        if kwargs["Key"] == "source":
            return {
                "ContentLength": size,
                "ContentType": "application/octet-stream",
                "Metadata": {"origin": "test"},
            }
        destination_heads += 1
        return {"ETag": client.destination_etag, "ContentLength": size}

    client.head_object.side_effect = head_object
    rename = asyncio.create_task(_rename_with_size(monkeypatch, client, size))
    await client.copy_committed.wait()

    rename.cancel()
    replacement_installed = True
    client.release_response.set()
    with pytest.raises(asyncio.CancelledError) as exc_info:
        await rename

    assert any("manual cleanup required" in note for note in exc_info.value.__notes__)
    assert replacement_installed
    assert destination_heads == 0
    client.delete_object.assert_not_awaited()


@pytest.mark.parametrize(
    ("size", "client_type"),
    [
        (5 * 1024**3, _BlockingCommittedCopyClient),
        (5 * 1024**3 + 1, _BlockingCommittedMultipartCopyClient),
    ],
)
async def test_cancelled_copy_removes_only_response_owned_version(
    monkeypatch: pytest.MonkeyPatch,
    size: int,
    client_type: type[_BlockingCommittedCopyClient] | type[_BlockingCommittedMultipartCopyClient],
) -> None:
    client = client_type()
    client.destination_version_id = "owned-version"
    rename = asyncio.create_task(_rename_with_size(monkeypatch, client, size))
    await client.copy_committed.wait()

    rename.cancel()
    client.release_response.set()
    with pytest.raises(asyncio.CancelledError):
        await rename

    client.delete_object.assert_awaited_once_with(
        Bucket="bucket", Key="destination", VersionId="owned-version"
    )


@pytest.mark.parametrize("multipart", [False, True])
async def test_rename_requires_manual_cleanup_when_response_omits_etag(
    monkeypatch: pytest.MonkeyPatch,
    multipart: bool,
) -> None:
    client = _CopyClient()
    size = 5 * 1024**3 + 1 if multipart else 5 * 1024**3
    if multipart:
        client.complete_multipart_upload.side_effect = None
        client.complete_multipart_upload.return_value = {}
    else:
        client.copy_object.side_effect = None
        client.copy_object.return_value = {}
    with pytest.raises(ProviderError, match=r"manual cleanup required.*copy response ETag"):
        await _rename_with_size(monkeypatch, client, size)

    assert all(call.kwargs["Key"] != "destination" for call in client.head_object.await_args_list)
    client.delete_object.assert_not_awaited()


@pytest.mark.parametrize(
    ("size", "version_id"),
    [
        (5 * 1024**3, None),
        (5 * 1024**3, "owned-version"),
        (5 * 1024**3 + 1, None),
        (5 * 1024**3 + 1, "owned-version"),
    ],
)
async def test_cancel_then_source_delete_error_preserves_cancellation(
    monkeypatch: pytest.MonkeyPatch,
    size: int,
    version_id: str | None,
) -> None:
    client = _CopyClient()
    client.destination_version_id = version_id
    delete_started = asyncio.Event()
    release_delete = asyncio.Event()

    async def delete_object(**kwargs: object) -> dict[str, object]:
        if kwargs["Key"] == "source":
            delete_started.set()
            await release_delete.wait()
            raise RuntimeError("source delete failed after cancellation")
        if version_id is None:
            pytest.fail("an unversioned destination must not be deleted during rollback")
        assert kwargs["VersionId"] == version_id
        return {}

    client.delete_object.side_effect = delete_object
    rename = asyncio.create_task(_rename_with_size(monkeypatch, client, size))
    await delete_started.wait()

    rename.cancel()
    release_delete.set()
    with pytest.raises(asyncio.CancelledError) as exc_info:
        await rename

    assert isinstance(exc_info.value.__cause__, RuntimeError)
    assert any(
        "source delete failed after cancellation" in note for note in exc_info.value.__notes__
    )
    if version_id is None:
        assert any("manual cleanup required" in note for note in exc_info.value.__notes__)
        assert client.delete_object.await_count == 1
    else:
        cleanup = client.delete_object.await_args_list[-1].kwargs
        assert cleanup == {"Bucket": "bucket", "Key": "destination", "VersionId": version_id}


async def test_delete_fails_closed_when_revision_has_version_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _CopyClient()
    client.head_object.side_effect = None
    client.head_object.return_value = {
        "ETag": '"same-content"',
        "VersionId": "version-7",
        "ContentLength": 4,
        "LastModified": datetime(2026, 8, 3, tzinfo=UTC),
    }
    fs = S3FS(session=MagicMock(), bucket="bucket")

    @asynccontextmanager
    async def client_context() -> AsyncIterator[_CopyClient]:
        yield client

    monkeypatch.setattr(fs, "_client", client_context)
    observed = await fs.stat(PathRef.from_posix("/source"))
    with pytest.raises(ProviderError, match="versioned S3 object"):
        await fs.delete(PathRef.from_posix("/source"), expected_etag=observed.etag)

    client.delete_object.assert_not_awaited()


async def test_delete_fallback_conditions_on_etag_size_and_modified_time(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _CopyClient()
    client.head_object.side_effect = None
    modified = datetime(2026, 8, 3, tzinfo=UTC)
    client.head_object.return_value = {
        "ETag": '"same-content"',
        "ContentLength": 4,
        "LastModified": modified,
    }
    fs = S3FS(session=MagicMock(), bucket="data--usw2-az1--x-s3")

    @asynccontextmanager
    async def client_context() -> AsyncIterator[_CopyClient]:
        yield client

    monkeypatch.setattr(fs, "_client", client_context)
    observed = await fs.stat(PathRef.from_posix("/source"))
    await fs.delete(PathRef.from_posix("/source"), expected_etag=observed.etag)

    client.delete_object.assert_awaited_once_with(
        Bucket="data--usw2-az1--x-s3",
        Key="source",
        IfMatch='"same-content"',
        IfMatchLastModifiedTime=modified,
        IfMatchSize=4,
    )


async def test_general_bucket_fallback_uses_supported_etag_condition(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _CopyClient()
    client.head_object.side_effect = None
    client.head_object.return_value = {
        "ETag": '"same-content"',
        "ContentLength": 4,
        "LastModified": datetime(2026, 8, 3, tzinfo=UTC),
    }
    fs = S3FS(session=MagicMock(), bucket="bucket")

    @asynccontextmanager
    async def client_context() -> AsyncIterator[_CopyClient]:
        yield client

    monkeypatch.setattr(fs, "_client", client_context)
    observed = await fs.stat(PathRef.from_posix("/source"))
    await fs.delete(PathRef.from_posix("/source"), expected_etag=observed.etag)

    client.delete_object.assert_awaited_once_with(
        Bucket="bucket", Key="source", IfMatch='"same-content"'
    )


@pytest.mark.parametrize("size", [5 * 1024**3, 5 * 1024**3 + 1])
async def test_rename_fails_closed_for_versioned_source(
    monkeypatch: pytest.MonkeyPatch,
    size: int,
) -> None:
    client = _CopyClient()
    revision = (
        's3:v1:{"etag":"source-etag","modified":null,'
        f'"size":{size},"version_id":"source-version"}}'
    )

    with pytest.raises(ProviderError, match="versioned S3 object"):
        await _rename_with_size(
            monkeypatch,
            client,
            size,
            revision=revision,
        )

    client.copy_object.assert_not_awaited()
    client.delete_object.assert_not_awaited()


async def test_cancellation_during_successful_source_delete_keeps_destination(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _CopyClient()
    delete_started = asyncio.Event()
    release_delete = asyncio.Event()

    async def delete_object(**kwargs: object) -> dict[str, object]:
        assert kwargs["Key"] == "source"
        delete_started.set()
        await release_delete.wait()
        return {}

    client.delete_object.side_effect = delete_object
    rename = asyncio.create_task(_rename_with_size(monkeypatch, client, 5 * 1024**3))
    await delete_started.wait()

    rename.cancel()
    release_delete.set()
    with pytest.raises(asyncio.CancelledError):
        await rename

    assert client.delete_object.await_count == 1
