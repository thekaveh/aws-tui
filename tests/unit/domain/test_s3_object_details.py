"""On-demand object details use only HEAD and exact-version tagging reads."""

from __future__ import annotations

import asyncio
from dataclasses import FrozenInstanceError
from datetime import UTC, datetime
from typing import Any

import pytest
from botocore.exceptions import ClientError, CredentialRetrievalError, EndpointConnectionError

from aws_tui.domain.filesystem import (
    AuthRequiredError,
    ConflictError,
    EntryKind,
    NotFoundError,
    PathRef,
    PermissionDeniedError,
    ProviderError,
    ProviderUnreachableError,
)
from aws_tui.domain.s3_fs import S3FS
from aws_tui.domain.s3_object_details import S3ObjectDetails, S3ObjectDetailsProvider
from tests.s3_object_details_support import ReadBarrier, RecordingSession

pytestmark = pytest.mark.unit


@pytest.fixture
def session() -> RecordingSession:
    return RecordingSession()


def _fs(session: RecordingSession, **kwargs: Any) -> S3FS:
    return S3FS(session=session, bucket=kwargs.pop("bucket", None), **kwargs)


def _error(code: str, message: str = "read failed") -> ClientError:
    return ClientError({"Error": {"Code": code, "Message": message}}, "HeadObject")


async def test_full_details_exact_wire_and_version_tagging(session: RecordingSession) -> None:
    modified = datetime(2026, 10, 3, 12, 30)
    metadata = {"note": "[bold]literal[/bold]", "empty": "", "long": "x" * 4096}
    tags = [{"Key": "owner", "Value": "[red]literal[/red]"}, {"Key": "empty", "Value": ""}]
    algorithms = {
        "ChecksumCRC32": "reported-crc32",
        "ChecksumCRC32C": "reported-crc32c",
        "ChecksumCRC64NVME": "reported-crc64",
        "ChecksumSHA1": "reported-sha1",
        "ChecksumSHA256": "reported-sha256",
    }
    session.s3.queue(
        "head_object",
        {
            "ContentType": "text/plain",
            "ContentEncoding": "gzip",
            "ContentLength": 0,
            "LastModified": modified,
            "StorageClass": "GLACIER",
            "ETag": '"opaque-etag"',
            "VersionId": "null",
            "ServerSideEncryption": "aws:kms",
            "SSEKMSKeyId": "arn:aws:kms:region:account:key/key-id",
            "BucketKeyEnabled": False,
            "SSECustomerKey": "secret-material",
            "SSECustomerKeyMD5": "secret-digest",
            "Metadata": metadata,
            **algorithms,
            "ChecksumType": "COMPOSITE",
        },
    )
    session.s3.queue("get_object_tagging", {"TagSet": tags})
    fs = _fs(session, endpoint_url="https://s3.example", force_path_style=True, verify_tls=False)
    details = await fs.read_object_details(PathRef(("bucket", "alpha.txt")))
    assert isinstance(fs, S3ObjectDetailsProvider)
    assert isinstance(details, S3ObjectDetails)
    assert details.bucket == "bucket"
    assert details.key == "alpha.txt"
    assert details.content_type == "text/plain"
    assert details.content_encoding == "gzip"
    assert details.size == 0
    assert details.version_id == "null"
    assert details.modified == modified.replace(tzinfo=UTC)
    assert details.storage_class == "GLACIER"
    assert details.etag == '"opaque-etag"'
    assert dict(details.encryption or ()) == {
        "ServerSideEncryption": "aws:kms",
        "SSEKMSKeyId": "arn:aws:kms:region:account:key/key-id",
        "BucketKeyEnabled": "false",
    }
    assert dict(details.metadata or ()) == metadata
    assert details.tags == (("owner", "[red]literal[/red]"), ("empty", ""))
    assert dict(details.checksums or ()) == algorithms
    assert details.checksum_type == "COMPOSITE"
    assert details.tags_error is None
    assert details.checksums_error is None
    assert session.s3.calls == [
        ("head_object", {"Bucket": "bucket", "Key": "alpha.txt", "ChecksumMode": "ENABLED"}),
        ("get_object_tagging", {"Bucket": "bucket", "Key": "alpha.txt", "VersionId": "null"}),
    ]
    assert session.s3.enter_count == session.s3.exit_count == 1
    assert session.s3.exit_types == [None]
    service, config = session.client_calls[0]
    assert service == "s3"
    assert config["endpoint_url"] == "https://s3.example"
    assert config["verify"] is False
    assert config["config"].s3 == {"addressing_style": "path"}
    assert config["config"].connect_timeout == 10
    assert config["config"].read_timeout == 60
    assert config["config"].retries == {"total_max_attempts": 6, "mode": "adaptive"}
    metadata["note"] = "changed"
    tags[0]["Value"] = "changed"
    assert dict(details.metadata or ())["note"] == "[bold]literal[/bold]"
    assert details.tags is not None
    assert details.tags[0] == ("owner", "[red]literal[/red]")
    with pytest.raises(FrozenInstanceError):
        details.size = 4  # type: ignore[misc]
    assert not hasattr(details, "__dict__")


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"Metadata": {}},
        {
            "ContentLength": 0,
            "BucketKeyEnabled": False,
            "ContentType": "",
            "Metadata": {"empty": ""},
        },
    ],
)
async def test_minimal_head_preserves_missing_zero_false_and_empty(
    session: RecordingSession, payload: dict[str, Any]
) -> None:
    session.s3.queue("head_object", payload)
    session.s3.queue("get_object_tagging", {"TagSet": []})
    details = await _fs(session).read_object_details(PathRef(("bucket", "a")))
    assert details.size == payload.get("ContentLength")
    assert details.content_type == payload.get("ContentType")
    assert details.content_encoding is None
    assert details.modified is None
    assert details.storage_class is None
    assert details.etag is None
    assert details.version_id is None
    assert details.checksums is None
    assert details.checksum_type is None
    assert details.checksums_error is None
    assert details.tags == ()
    assert details.metadata == (
        tuple(payload["Metadata"].items()) if "Metadata" in payload else None
    )
    expected_encryption = (
        (("BucketKeyEnabled", "false"),) if "BucketKeyEnabled" in payload else None
    )
    assert details.encryption == expected_encryption
    assert session.s3.calls[-1] == ("get_object_tagging", {"Bucket": "bucket", "Key": "a"})


@pytest.mark.parametrize(
    "failure",
    [
        _error("AccessDenied"),
        _error("SlowDown"),
        _error("ServiceUnavailable"),
        EndpointConnectionError(endpoint_url="https://example.invalid"),
    ],
)
async def test_checksum_denied_keeps_readable_head(
    session: RecordingSession, failure: Exception
) -> None:
    session.s3.queue("head_object", failure, {"ContentLength": 7, "ETag": '"value"'})
    session.s3.queue("get_object_tagging", {"TagSet": [{"Key": "tag", "Value": "value"}]})
    details = await _fs(session).read_object_details(PathRef(("bucket", "a")))
    assert details.size == 7
    assert details.etag == '"value"'
    assert details.checksums is None
    assert details.checksums_error
    assert details.tags == (("tag", "value"),)
    assert details.tags_error is None
    assert session.s3.calls == [
        ("head_object", {"Bucket": "bucket", "Key": "a", "ChecksumMode": "ENABLED"}),
        ("head_object", {"Bucket": "bucket", "Key": "a"}),
        ("get_object_tagging", {"Bucket": "bucket", "Key": "a"}),
    ]
    assert session.s3.exit_count == 1


@pytest.mark.parametrize(
    "failure",
    [
        _error("AccessDenied"),
        _error("ExpiredToken"),
        EndpointConnectionError(endpoint_url="https://example.invalid"),
        RuntimeError("unknown tag failure"),
    ],
)
async def test_tagging_denied_keeps_other_details(
    session: RecordingSession, failure: Exception
) -> None:
    session.s3.queue(
        "head_object", {"ContentLength": 8, "Metadata": {"m": "v"}, "ChecksumSHA256": "reported"}
    )
    session.s3.queue("get_object_tagging", failure)
    details = await _fs(session).read_object_details(PathRef(("bucket", "a")))
    assert details.size == 8
    assert details.metadata == (("m", "v"),)
    assert details.checksums == (("ChecksumSHA256", "reported"),)
    assert details.tags is None
    assert details.tags_error
    assert details.checksums_error is None
    assert session.s3.exit_count == 1


@pytest.mark.parametrize(
    "failure", [_error("NotImplemented"), _error("InvalidRequest", "ChecksumMode is not supported")]
)
async def test_unsupported_checksum_mode_falls_back_once(
    session: RecordingSession, failure: Exception
) -> None:
    session.s3.queue("head_object", failure, {"ContentLength": 8})
    session.s3.queue("get_object_tagging", {"TagSet": []})
    details = await _fs(session).read_object_details(PathRef(("bucket", "a")))
    assert details.size == 8
    assert details.checksums_error
    assert [op for op, _ in session.s3.calls] == [
        "head_object",
        "head_object",
        "get_object_tagging",
    ]
    assert "ChecksumMode" not in session.s3.calls[1][1]


@pytest.mark.parametrize(
    ("bucket", "path"), [(None, PathRef()), (None, PathRef(("bucket",))), ("bucket", PathRef())]
)
async def test_roots_and_bucket_only_paths_issue_no_requests(
    session: RecordingSession, bucket: str | None, path: PathRef
) -> None:
    with pytest.raises(ProviderError):
        await _fs(session, bucket=bucket, prefix="prefix").read_object_details(path)
    assert session.client_calls == []
    assert session.s3.calls == []


@pytest.mark.parametrize(
    ("bucket", "path", "key"),
    [
        ("fixed", PathRef(("a", "b")), "prefix/a/b"),
        (None, PathRef(("fixed", "a", "b")), "prefix/a/b"),
    ],
)
async def test_details_uses_fixed_prefix_and_bucketless_resolution(
    session: RecordingSession, bucket: str | None, path: PathRef, key: str
) -> None:
    session.s3.queue("head_object", {})
    session.s3.queue("get_object_tagging", {"TagSet": []})
    details = await _fs(session, bucket=bucket, prefix="/prefix/").read_object_details(path)
    assert details.bucket == "fixed"
    assert details.key == key
    assert session.s3.calls[0][1] == {"Bucket": "fixed", "Key": key, "ChecksumMode": "ENABLED"}


@pytest.mark.parametrize(
    ("failure", "expected"),
    [
        (_error("ExpiredToken"), AuthRequiredError),
        (
            CredentialRetrievalError(provider="process", error_msg="PRIVATE_STDERR_TOKEN"),
            AuthRequiredError,
        ),
        (_error("NoSuchKey"), NotFoundError),
        (_error("PreconditionFailed"), ConflictError),
        (_error("InvalidRequest", "unrelated problem"), ProviderError),
    ],
)
async def test_auth_and_notfound_do_not_fallback_or_tag(
    session: RecordingSession, failure: Exception, expected: type[Exception]
) -> None:
    session.s3.queue("head_object", failure)
    with pytest.raises(expected) as caught:
        await _fs(session).read_object_details(PathRef(("bucket", "a")))
    assert [op for op, _ in session.s3.calls] == ["head_object"]
    assert session.s3.exit_count == 1
    if isinstance(failure, CredentialRetrievalError):
        assert "PRIVATE_STDERR_TOKEN" not in str(caught.value)


@pytest.mark.parametrize(
    ("failure", "expected"),
    [
        (_error("AccessDenied"), PermissionDeniedError),
        (_error("NoSuchKey"), NotFoundError),
        (EndpointConnectionError(endpoint_url="https://example.invalid"), ProviderUnreachableError),
    ],
)
async def test_ordinary_head_failure_propagates(
    session: RecordingSession, failure: Exception, expected: type[Exception]
) -> None:
    session.s3.queue("head_object", _error("AccessDenied"), failure)
    with pytest.raises(expected):
        await _fs(session).read_object_details(PathRef(("bucket", "a")))
    assert [op for op, _ in session.s3.calls] == ["head_object", "head_object"]
    assert session.s3.exit_count == 1


async def test_unknown_head_failure_propagates(session: RecordingSession) -> None:
    session.s3.queue("head_object", RuntimeError("unclassified error"))
    with pytest.raises(RuntimeError, match="unclassified error"):
        await _fs(session).read_object_details(PathRef(("bucket", "a")))
    assert len(session.s3.calls) == 1
    assert session.s3.exit_count == 1


@pytest.mark.parametrize("operation", ["head_object", "ordinary_head", "get_object_tagging"])
async def test_cancel_during_read_exits_client(session: RecordingSession, operation: str) -> None:
    barrier = ReadBarrier()
    if operation == "ordinary_head":
        session.s3.queue("head_object", _error("AccessDenied"), barrier)
    elif operation == "get_object_tagging":
        session.s3.queue("head_object", {})
        session.s3.queue("get_object_tagging", barrier)
    else:
        session.s3.queue("head_object", barrier)
    task = asyncio.create_task(_fs(session).read_object_details(PathRef(("bucket", "a"))))
    await asyncio.wait_for(barrier.entered.wait(), timeout=1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert session.s3.exit_count == 1
    assert session.s3.exit_types == [asyncio.CancelledError]
    assert len(session.s3.calls) == (1 if operation == "head_object" else 2)


async def test_listing_has_no_per_row_details_calls(session: RecordingSession) -> None:
    session.s3.queue("list_buckets", {"Buckets": [{"Name": "bucket"}]})
    session.s3.queue(
        "list_objects_v2",
        {
            "Contents": [{"Key": f"a-{i}", "Size": i} for i in range(200)],
            "CommonPrefixes": [{"Prefix": "folder/"}],
        },
    )
    fs = _fs(session)
    buckets = await fs.list(PathRef())
    entries = await fs.list(PathRef(("bucket",)))
    assert buckets[0].name == "bucket"
    assert buckets[0].kind is EntryKind.DIRECTORY
    assert len(entries) == 201
    assert entries[0].name == "folder"
    assert session.s3.calls == [
        ("list_buckets", {"MaxBuckets": 1000}),
        ("list_objects_v2", {"Bucket": "bucket", "Prefix": "", "Delimiter": "/"}),
    ]
    assert session.s3.enter_count == session.s3.exit_count == 2
