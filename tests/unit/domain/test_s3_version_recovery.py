"""Historical recovery reuses streaming copy and never deletes object versions."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any
from urllib.parse import parse_qsl

import pytest
from botocore.exceptions import EndpointConnectionError

from aws_tui.domain.cross_fs import CrossFsCopy
from aws_tui.domain.filesystem import ConflictError, PathRef, ProviderError
from tests.unit.domain.test_s3_versions import (
    PATH,
    BytesBody,
    ReadVersionSession,
    error,
    fs,
    head,
    selected,
)

pytestmark = pytest.mark.unit
DESTINATION = PathRef(("bucket", "prefix", "recovered"))


class Hold:
    def __init__(self, value: dict[str, Any]) -> None:
        self.value = value
        self.entered = asyncio.Event()
        self.release = asyncio.Event()

    async def read(self) -> dict[str, Any]:
        self.entered.set()
        await self.release.wait()
        return self.value


class RecoverySession(ReadVersionSession):
    async def _read(self, operation: str, kwargs: dict[str, Any]) -> dict[str, Any]:
        self.calls.append((operation, kwargs))
        assert self.reads, f"unexpected operation: {operation}"
        expected, result = self.reads.popleft()
        assert operation == expected
        if isinstance(result, BaseException):
            raise result
        if isinstance(result, Hold):
            return await result.read()
        return result

    def __getattr__(self, operation: str):
        assert operation in {
            "list_objects_v2",
            "put_object",
            "create_multipart_upload",
            "upload_part",
            "complete_multipart_upload",
            "abort_multipart_upload",
            "delete_object",
        }, operation

        async def call(**kwargs: Any):
            return await self._read(operation, kwargs)

        return call


def metadata(**changes: Any) -> dict[str, Any]:
    return head(
        **{
            "ContentType": "text/plain",
            "ContentEncoding": "gzip",
            "ContentLanguage": "en",
            "ContentDisposition": 'attachment; filename="old.txt"',
            "CacheControl": "max-age=42",
            "Expires": datetime(2027, 1, 1, tzinfo=UTC),
            "WebsiteRedirectLocation": "/archive",
            "Metadata": {"owner": "literal [bold]", "empty": ""},
            "ServerSideEncryption": "aws:kms",
            "SSEKMSKeyId": "must-not-copy",
            "StorageClass": "GLACIER",
            "ObjectLockMode": "GOVERNANCE",
            "ObjectLockLegalHoldStatus": "ON",
            "ChecksumSHA256": "old-checksum",
            **changes,
        }
    )


def setup_session(
    *,
    empty: bool = False,
    terminal: Any = None,
    part: Any = None,
    create: Any = None,
    abort: Any = None,
) -> tuple[RecoverySession, BytesBody]:
    size = 0 if empty else 7
    observed = metadata(ContentLength=size)
    body = BytesBody(b"" if empty else b"payload")
    reads: list[tuple[str, Any]] = [
        ("head_object", observed),
        ("get_object_tagging", {"TagSet": [{"Key": "a & b", "Value": "literal +/="}]}),
        ("head_object", head(ContentLength=size)),
        ("head_object", error("404", "HeadObject")),
        ("list_objects_v2", {"KeyCount": 0}),
        ("head_object", head(ContentLength=size)),
        ("get_object", head(ContentLength=size, Body=body)),
    ]
    terminal = {"ETag": '"new"', "VersionId": "recovered-version"} if terminal is None else terminal
    if empty:
        reads.append(("put_object", terminal))
    else:
        reads.append(
            ("create_multipart_upload", {"UploadId": "owned-upload"} if create is None else create)
        )
        reads.append(("upload_part", {"ETag": '"part"'} if part is None else part))
        reads.append(("complete_multipart_upload", terminal))
    if abort is not None:
        reads = [(op, value) for op, value in reads if op != "complete_multipart_upload"]
        if create is not None:
            reads = [(op, value) for op, value in reads if op != "upload_part"]
        reads.append(("abort_multipart_upload", abort))
    return RecoverySession(*reads), body


async def prepare(session: RecoverySession, *, size: int = 7):
    provider = fs(session)
    version = selected(size=size)
    target = await provider.recovery_destination(version, DESTINATION)
    copier = CrossFsCopy(source=provider.version_source(version), destination=target)
    return target, copier


@pytest.mark.parametrize("empty", [False, True])
async def test_recovery_preserves_supported_metadata_at_creation_and_never_deletes(
    empty: bool,
) -> None:
    session, body = setup_session(empty=empty)
    target, copier = await prepare(session, size=0 if empty else 7)
    progress = []
    assert await copier.copy(PATH, DESTINATION, progress=progress.append)
    creation = next(
        kwargs for op, kwargs in session.calls if op in {"put_object", "create_multipart_upload"}
    )
    for name in (
        "ContentType",
        "ContentEncoding",
        "ContentLanguage",
        "ContentDisposition",
        "CacheControl",
        "Expires",
        "WebsiteRedirectLocation",
        "Metadata",
    ):
        assert creation[name] == metadata()[name]
    assert dict(parse_qsl(creation["Tagging"])) == {"a & b": "literal +/="}
    assert not {
        "SSEKMSKeyId",
        "ServerSideEncryption",
        "StorageClass",
        "ObjectLockMode",
        "ObjectLockLegalHoldStatus",
        "ChecksumSHA256",
    }.intersection(creation)
    publication = next(
        kwargs for op, kwargs in session.calls if op in {"put_object", "complete_multipart_upload"}
    )
    assert publication["IfNoneMatch"] == "*"
    assert publication["Key"] == "prefix/recovered"
    assert target.publication.published
    assert not target.publication.uncertain
    assert body.closed
    assert all(op != "delete_object" for op, _ in session.calls)
    source_reads = [
        kwargs
        for op, kwargs in session.calls
        if op in {"head_object", "get_object", "get_object_tagging"}
        and kwargs["Key"] == "prefix/object"
    ]
    assert all(kwargs["VersionId"] == "v1" for kwargs in source_reads)
    if not empty:
        assert progress[-1].bytes_transferred == 7


async def test_recovery_to_original_key_is_refused_before_reads_even_if_current_is_deleted() -> (
    None
):
    session = RecoverySession()
    with pytest.raises(ConflictError, match=r"different|same"):
        await fs(session).recovery_destination(selected(), PATH)
    assert not session.calls


async def test_occupied_recovery_destination_refuses_without_streaming_or_mutation() -> None:
    session = RecoverySession(
        ("head_object", metadata()),
        ("get_object_tagging", {"TagSet": []}),
        ("head_object", head()),
        ("head_object", head()),
        ("list_objects_v2", {"KeyCount": 0}),
    )
    target, copier = await prepare(session)
    with pytest.raises(ConflictError):
        await copier.copy(PATH, DESTINATION)
    assert not target.publication.published
    assert all(
        op in {"head_object", "get_object_tagging", "list_objects_v2"} for op, _ in session.calls
    )


@pytest.mark.parametrize(
    "change",
    [
        {"MissingMeta": 1},
        {"Metadata": None},
        {"Metadata": {"a": 5}},
        {"Expires": object()},
        {"ContentType": 3},
    ],
)
async def test_incomplete_or_malformed_metadata_is_not_silently_lost(
    change: dict[str, Any],
) -> None:
    session = RecoverySession(
        ("head_object", metadata(**change)), ("get_object_tagging", {"TagSet": []})
    )
    with pytest.raises(ProviderError, match=r"metadata|header"):
        await fs(session).recovery_destination(selected(), DESTINATION)
    assert all(op in {"head_object", "get_object_tagging"} for op, _ in session.calls)


@pytest.mark.parametrize(
    "tags",
    [{}, {"TagSet": None}, {"TagSet": [{"Key": "a"}]}, error("AccessDenied", "GetObjectTagging")],
)
async def test_unavailable_or_malformed_tags_refuse_recovery(tags: Any) -> None:
    session = RecoverySession(("head_object", metadata()), ("get_object_tagging", tags))
    with pytest.raises(ProviderError, match=r"tag|Tagging"):
        await fs(session).recovery_destination(selected(), DESTINATION)
    assert all(op in {"head_object", "get_object_tagging"} for op, _ in session.calls)


@pytest.mark.parametrize("empty", [False, True])
async def test_cancellation_during_publication_preserves_destination_and_outcome(
    empty: bool,
) -> None:
    hold = Hold({"ETag": '"new"', "VersionId": "published"})
    session, body = setup_session(empty=empty, terminal=hold)
    target, copier = await prepare(session, size=0 if empty else 7)
    task = asyncio.create_task(copier.copy(PATH, DESTINATION))
    await asyncio.wait_for(hold.entered.wait(), 2)
    task.cancel()
    await asyncio.sleep(0)
    task.cancel()
    hold.release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert target.publication.published
    assert target.publication.version_id == "published"
    assert body.closed
    assert all(op not in {"delete_object", "abort_multipart_upload"} for op, _ in session.calls)


@pytest.mark.parametrize("during_create", [False, True])
async def test_cancellation_before_publication_aborts_owned_upload_without_delete(
    during_create: bool,
) -> None:
    hold = Hold({"UploadId": "owned-upload"} if during_create else {"ETag": '"part"'})
    session, body = setup_session(
        create=hold if during_create else None, part=None if during_create else hold, abort={}
    )
    target, copier = await prepare(session)
    task = asyncio.create_task(copier.copy(PATH, DESTINATION))
    await asyncio.wait_for(hold.entered.wait(), 2)
    task.cancel()
    hold.release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not target.publication.published
    assert not target.publication.uncertain
    assert body.closed
    assert session.calls[-1] == (
        "abort_multipart_upload",
        {"Bucket": "bucket", "Key": "prefix/recovered", "UploadId": "owned-upload"},
    )
    assert all(op != "delete_object" for op, _ in session.calls)


async def test_recovery_writer_cannot_be_redirected_or_used_to_overwrite() -> None:
    session = RecoverySession(("head_object", metadata()), ("get_object_tagging", {"TagSet": []}))
    target = await fs(session).recovery_destination(selected(), DESTINATION)

    async def content():
        yield b"payload"

    for destination, overwrite in [(PATH, False), (DESTINATION, True)]:
        with pytest.raises(ConflictError, match=r"bound|overwrite|different"):
            await target.write_stream(destination, content(), overwrite=overwrite)
    assert all(op in {"head_object", "get_object_tagging"} for op, _ in session.calls)


@pytest.mark.parametrize("empty", [False, True])
async def test_uncertain_publication_failure_is_retained_without_deleting(empty: bool) -> None:
    session, body = setup_session(
        empty=empty, terminal=EndpointConnectionError(endpoint_url="http://fixture.invalid")
    )
    if not empty:
        session.reads.append(("abort_multipart_upload", {}))
    target, copier = await prepare(session, size=0 if empty else 7)
    with pytest.raises(ProviderError, match="endpoint"):
        await copier.copy(PATH, DESTINATION)
    assert target.publication.uncertain
    assert not target.publication.published
    assert body.closed
    assert all(op != "delete_object" for op, _ in session.calls)


async def test_part_and_abort_failure_keep_cleanup_identity_in_request_outcome() -> None:
    session, body = setup_session(
        part=RuntimeError("part failed"), abort=RuntimeError("abort failed")
    )
    target, copier = await prepare(session)
    with pytest.raises(ProviderError, match="manual abort"):
        await copier.copy(PATH, DESTINATION)
    assert target.publication.cleanup_error is not None
    assert "owned-upload" in target.publication.cleanup_error
    assert not target.publication.published
    assert body.closed
    assert all(op != "delete_object" for op, _ in session.calls)


async def test_late_destination_conflict_never_replaces_or_deletes_concurrent_object() -> None:
    session, body = setup_session(terminal=error("PreconditionFailed", "CompleteMultipartUpload"))
    session.reads.append(("abort_multipart_upload", {}))
    target, copier = await prepare(session)
    with pytest.raises(ConflictError):
        await copier.copy(PATH, DESTINATION)
    assert not target.publication.published
    assert body.closed
    completion = next(kwargs for op, kwargs in session.calls if op == "complete_multipart_upload")
    assert completion["IfNoneMatch"] == "*"
    assert all(op != "delete_object" for op, _ in session.calls)


async def test_null_metadata_recheck_refuses_a_same_content_metadata_replacement() -> None:
    before = metadata(VersionId="null")
    after = {**before, "Metadata": {"owner": "replacement"}}
    session = RecoverySession(
        ("head_object", before), ("get_object_tagging", {"TagSet": []}), ("head_object", after)
    )
    with pytest.raises(ConflictError, match="metadata changed"):
        await fs(session).recovery_destination(selected("null"), DESTINATION)
    assert all(kwargs["VersionId"] == "null" for _, kwargs in session.calls)
