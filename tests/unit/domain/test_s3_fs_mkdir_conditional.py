"""Creating a marker never replaces an object that appeared after review."""

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock

import pytest
from botocore.exceptions import ClientError

from aws_tui.domain.filesystem import ConflictError, PathRef
from aws_tui.domain.s3_fs import S3FS


@pytest.mark.asyncio
@pytest.mark.parametrize("race", [False, True])
async def test_marker_creation_uses_no_replace_condition(monkeypatch, race):
    fs = S3FS(session=MagicMock(), bucket="synthetic")
    marker_bytes = None
    calls = []

    async def put(**kwargs):
        nonlocal marker_bytes
        calls.append(kwargs)
        if race and kwargs.get("IfNoneMatch") == "*":
            raise ClientError({"Error": {"Code": "PreconditionFailed"}}, "PutObject")
        marker_bytes = kwargs["Body"]
        return {}

    client = MagicMock()
    client.put_object = AsyncMock(side_effect=put)

    @asynccontextmanager
    async def context():
        yield client

    monkeypatch.setattr(fs, "_client", context)
    if race:
        with pytest.raises(ConflictError):
            await fs.mkdir(PathRef(("folder",)))
        assert marker_bytes is None
    else:
        await fs.mkdir(PathRef(("folder",)))
        assert marker_bytes == b""
    assert calls == [{"Bucket": "synthetic", "Key": "folder/", "Body": b"", "IfNoneMatch": "*"}]


@pytest.mark.asyncio
async def test_unsupported_marker_condition_is_not_retried_unconditionally(monkeypatch):
    from aws_tui.domain.filesystem import ProviderError

    fs = S3FS(session=MagicMock(), bucket="synthetic")
    client = MagicMock()
    client.put_object = AsyncMock(
        side_effect=ClientError({"Error": {"Code": "NotImplemented"}}, "PutObject")
    )

    @asynccontextmanager
    async def context():
        yield client

    monkeypatch.setattr(fs, "_client", context)
    with pytest.raises(ProviderError):
        await fs.mkdir(PathRef(("folder",)))
    client.put_object.assert_awaited_once_with(
        Bucket="synthetic", Key="folder/", Body=b"", IfNoneMatch="*"
    )


@pytest.mark.asyncio
async def test_bucket_creation_refuses_before_opening_client():
    from aws_tui.domain.filesystem import ProviderError

    session = MagicMock()
    fs = S3FS(session=session, bucket=None)
    with pytest.raises(ProviderError, match="cannot mkdir a bucket"):
        await fs.mkdir(PathRef(("new-bucket",)))
    session.client.assert_not_called()
