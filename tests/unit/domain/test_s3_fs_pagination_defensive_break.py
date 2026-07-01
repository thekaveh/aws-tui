"""Regression: S3FS must not infinite-loop when an S3-compatible
provider returns ``IsTruncated=True`` WITHOUT a
``NextContinuationToken``.

A well-behaved S3 endpoint always returns the continuation token
when the response is truncated. Some S3-compatible providers (MinIO
historically, Cloudflare R2, Backblaze B2 under specific edge
conditions) have shipped responses with the truncated flag set but
no token. Before the fix, ``S3FS.list()`` and ``S3FS.delete()`` would
re-issue the same request forever (the ``if token is not None`` guard
on the request kwargs skipped the ``ContinuationToken`` field, so
each iteration produced an identical paged call).

After the fix, both pagination loops break defensively the moment the
token-extraction step yields a falsy value, even if ``IsTruncated`` is
``True``. These tests would HANG (and trip the asyncio test timeout)
under the buggy code, so a passing run is the regression signal.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from botocore.exceptions import ClientError

from aws_tui.domain.filesystem import PathRef
from aws_tui.domain.s3_fs import S3FS

pytestmark = pytest.mark.unit


class _StubS3Client:
    """Async stub for the boto3 S3 client surface S3FS uses.

    Returns the same canned ``list_objects_v2`` response on every
    call so the pagination loop has nothing to advance on. The fix's
    job is to stop the loop anyway, despite the misbehaving server.
    """

    def __init__(self, list_response: dict[str, Any]) -> None:
        self._list_response = list_response
        self.list_call_count = 0
        self.head_object = AsyncMock(return_value={})
        self.delete_object = AsyncMock(return_value={})
        self.delete_objects = AsyncMock(return_value={})

    async def list_objects_v2(self, **_kwargs: Any) -> dict[str, Any]:
        self.list_call_count += 1
        return self._list_response


class _StubClientContext:
    """Wraps the stub client so ``async with S3FS._client() as s3``
    yields the same object every list/delete invocation. We patch
    ``S3FS._client`` directly with this factory."""

    def __init__(self, stub: _StubS3Client) -> None:
        self._stub = stub

    @asynccontextmanager
    async def __call__(self) -> Any:
        yield self._stub


class _RecordingSession:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def client(self, service_name: str, **kwargs: Any) -> object:
        self.calls.append({"service_name": service_name, **kwargs})
        return object()


def _patch_s3fs_client(monkeypatch: pytest.MonkeyPatch, stub: _StubS3Client) -> None:
    ctx_factory = _StubClientContext(stub)
    monkeypatch.setattr(S3FS, "_client", lambda self: ctx_factory())


def _build_fs() -> S3FS:
    # Session is bypassed via the patched _client; pass a dummy.
    session = MagicMock()
    return S3FS(session=session, bucket="mybkt", endpoint_url=None, force_path_style=False)


def test_client_passes_verify_tls_to_aioboto3_session() -> None:
    session = _RecordingSession()
    fs = S3FS(
        session=session,  # type: ignore[arg-type]
        bucket=None,
        endpoint_url="https://minio.local",
        force_path_style=True,
        verify_tls=False,
    )

    fs._client()

    assert session.calls == [
        {
            "service_name": "s3",
            "config": fs._config,
            "endpoint_url": "https://minio.local",
            "verify": False,
        }
    ]


async def test_list_pagination_breaks_when_truncated_without_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Misbehaving provider: IsTruncated=True, no NextContinuationToken.

    Pre-fix: S3FS.list() loops forever (each iteration re-sends the
    same request because the token guard keeps ContinuationToken out
    of the kwargs). Post-fix: the loop breaks after the first page.
    """
    stub = _StubS3Client(
        list_response={
            "CommonPrefixes": [],
            "Contents": [
                {"Key": "a.txt", "Size": 1, "LastModified": None, "ETag": '"x"'},
            ],
            # The pathological combination — truncated but no token.
            "IsTruncated": True,
            # NextContinuationToken intentionally absent.
        }
    )
    _patch_s3fs_client(monkeypatch, stub)
    fs = _build_fs()
    # ``asyncio.timeout`` aborts the test if the pre-fix infinite loop
    # ever re-appears — otherwise the test would hang and the suite
    # would stall on this single case.
    async with asyncio.timeout(5):
        entries = await fs.list(PathRef(()))
    # First page returned exactly one file; loop must NOT issue
    # additional requests despite IsTruncated=True.
    assert [e.name for e in entries] == ["a.txt"]
    assert stub.list_call_count == 1, (
        f"expected the pagination loop to break after the first page, "
        f"got {stub.list_call_count} list_objects_v2 calls — the "
        f"misbehaving-provider defensive break is not in place"
    )


async def test_delete_pagination_breaks_when_truncated_without_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Same pathological response on the recursive-delete path.

    Pre-fix: ``S3FS.delete()`` re-issues the same list_objects_v2 +
    delete_objects pair forever. Post-fix: one round-trip and done.
    """
    stub = _StubS3Client(
        list_response={
            "Contents": [
                {"Key": "d/a.txt", "Size": 1},
                {"Key": "d/b.txt", "Size": 1},
            ],
            "IsTruncated": True,
            # NextContinuationToken intentionally absent.
        }
    )
    # Force the directory-delete branch: head_object on the target
    # key must surface as NoSuchKey so the delete falls through to the
    # prefix-enumerate + batch-delete loop (the path that hosts the
    # pagination bug).
    stub.head_object = AsyncMock(
        side_effect=ClientError(
            error_response={"Error": {"Code": "NoSuchKey", "Message": "not found"}},
            operation_name="HeadObject",
        )
    )
    _patch_s3fs_client(monkeypatch, stub)
    fs = _build_fs()
    async with asyncio.timeout(5):
        await fs.delete(PathRef.from_posix("/d"))
    assert stub.list_call_count == 1, (
        f"expected the recursive-delete loop to break after the first "
        f"batch, got {stub.list_call_count} list_objects_v2 calls"
    )
    # The first batch was sent to delete_objects exactly once.
    assert stub.delete_objects.await_count == 1
