from __future__ import annotations

import asyncio
import traceback
from copy import deepcopy
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from aws_tui.domain.filesystem import (
    NotFoundError,
    PermissionDeniedError,
    ProviderError,
    ValidationError,
)
from aws_tui.infra.crash_dump import CrashDump
from tests.unit.domain.test_athena import _athena_client, _client_error, _query_execution


class Body:
    def __init__(self, data=b'a,a\n"",\n'):
        self.data = data
        self.closed = False
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.block = False

    async def __aenter__(self):
        return object()  # StreamingBody enters its raw response, not itself.

    async def __aexit__(self, *args):
        self.closed = True

    async def read(self, count):
        self.started.set()
        if self.block:
            await self.release.wait()
        chunk, self.data = self.data[:count], self.data[count:]
        return chunk


def fixture():
    raw = _query_execution()
    client, boto, session = _athena_client("get_query_execution", raw)
    body = Body()
    size = len(body.data)
    s3 = SimpleNamespace(
        head_object=AsyncMock(
            return_value={"ContentLength": size, "ETag": '"etag"', "VersionId": "version-1"}
        ),
        get_object=AsyncMock(
            return_value={
                "Body": body,
                "ContentLength": size,
                "ETag": '"etag"',
                "VersionId": "version-1",
            }
        ),
    )
    session._clients["s3"] = s3
    return client, boto, session, s3, body, raw


async def collect(client, detail, **kwargs):
    return b"".join(
        [part async for part in client.iter_result_artifact(detail, max_bytes=100, **kwargs)]
    )


async def test_original_artifact_uses_reviewed_uri_pins_revision_and_closes_stream():
    client, boto, session, s3, body, _raw = fixture()
    detail = await client.get_query_execution("q-123")
    expected = body.data
    assert await collect(client, detail, chunk_bytes=3) == expected
    s3.head_object.assert_awaited_once_with(Bucket="sensitive-results", Key="q-123.csv")
    s3.get_object.assert_awaited_once_with(
        Bucket="sensitive-results", Key="q-123.csv", IfMatch='"etag"', VersionId="version-1"
    )
    assert body.closed
    assert all(
        connection.region == detail.context.region for connection, _service in session.requests
    )
    boto.start_query_execution.assert_not_awaited()


@pytest.mark.parametrize("kind", ["managed", "missing", "txt", "running", "foreign", "changed"])
async def test_unsupported_or_changed_artifact_never_reads_s3(kind):
    client, boto, _session, s3, _body, raw = fixture()
    detail = await client.get_query_execution("q-123")
    if kind == "managed":
        raw["QueryExecution"]["ManagedQueryResultsConfiguration"] = {"Enabled": True}
        detail = await client.get_query_execution("q-123")
        assert detail.managed_query_results_enabled
    elif kind == "missing":
        raw["QueryExecution"]["ResultConfiguration"] = {}
        detail = await client.get_query_execution("q-123")
    elif kind == "txt":
        raw["QueryExecution"]["ResultConfiguration"]["OutputLocation"] = (
            "s3://sensitive-results/q-123.txt"
        )
        detail = await client.get_query_execution("q-123")
    elif kind == "running":
        raw["QueryExecution"]["Status"]["State"] = "RUNNING"
        detail = await client.get_query_execution("q-123")
    elif kind == "foreign":
        detail = replace(
            detail,
            summary=replace(detail.summary, ref=replace(detail.summary.ref, region="us-west-2")),
        )
    else:
        new = deepcopy(raw)
        new["QueryExecution"]["ResultConfiguration"]["OutputLocation"] = (
            "s3://other-results/other.csv"
        )
        boto.get_query_execution.return_value = new
    with pytest.raises(ValidationError):
        await collect(client, detail)
    s3.head_object.assert_not_awaited()
    s3.get_object.assert_not_awaited()
    boto.start_query_execution.assert_not_awaited()


@pytest.mark.parametrize("kind", ["too-large", "no-etag", "truncated", "changed-etag"])
async def test_artifact_limits_or_corruption_never_report_complete(kind):
    client, _boto, _session, s3, body, _raw = fixture()
    detail = await client.get_query_execution("q-123")
    if kind == "too-large":
        s3.head_object.return_value["ContentLength"] = 101
    elif kind == "no-etag":
        s3.head_object.return_value.pop("ETag")
    elif kind == "truncated":
        body.data = b"a"
    else:
        s3.get_object.return_value["ETag"] = '"other"'
    with pytest.raises(ValidationError):
        await collect(client, detail)
    if s3.get_object.await_count:
        assert body.closed
    else:
        s3.get_object.assert_not_awaited()


@pytest.mark.parametrize(
    ("code", "guidance"), [("AccessDenied", "S3"), ("KMS.AccessDeniedException", "encryption")]
)
async def test_artifact_denials_have_typed_safe_service_guidance(code, guidance):
    client, _boto, _session, s3, _body, _raw = fixture()
    detail = await client.get_query_execution("q-123")
    s3.get_object.side_effect = _client_error(
        code, "PRIVATE_OBJECT_SQL_TOKEN", operation="GetObject"
    )
    with pytest.raises(PermissionDeniedError) as error:
        await collect(client, detail)
    assert guidance in str(error.value)
    assert "PRIVATE" not in str(error.value)


async def test_missing_artifact_is_explicitly_unavailable_or_expired():
    client, _boto, _session, s3, _body, _raw = fixture()
    detail = await client.get_query_execution("q-123")
    s3.head_object.side_effect = _client_error("NoSuchKey", "PRIVATE", operation="HeadObject")
    with pytest.raises(NotFoundError, match="unavailable or expired"):
        await collect(client, detail)


async def test_cancelled_artifact_read_closes_stream_without_starting_query():
    client, boto, _session, _s3, body, _raw = fixture()
    detail = await client.get_query_execution("q-123")
    body.block = True
    task = asyncio.create_task(collect(client, detail))
    try:
        await asyncio.wait_for(body.started.wait(), 1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert body.closed
        boto.start_query_execution.assert_not_awaited()
    finally:
        body.release.set()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.parametrize(
    ("max_bytes", "chunk_bytes"), [(True, 3), (-1, 3), (100, 0), (100, 65537), (100, True)]
)
async def test_invalid_artifact_limits_refused_before_provider_reads(max_bytes, chunk_bytes):
    client, boto, _session, s3, _body, _raw = fixture()
    detail = await client.get_query_execution("q-123")
    boto.get_query_execution.reset_mock()
    with pytest.raises(ValidationError):
        _ = [
            part
            async for part in client.iter_result_artifact(
                detail, max_bytes=max_bytes, chunk_bytes=chunk_bytes
            )
        ]
    boto.get_query_execution.assert_not_awaited()
    s3.head_object.assert_not_awaited()


async def test_artifact_error_traceback_and_crash_dump_do_not_expose_provider_data(tmp_path):
    client, _boto, _session, s3, _body, _raw = fixture()
    detail = await client.get_query_execution("q-123")
    secret = "PRIVATE_ARTIFACT_SQL_VALUE_TOKEN"
    s3.get_object.side_effect = RuntimeError(secret)
    with pytest.raises(ProviderError, match="S3 result export failed") as raised:
        await collect(client, detail)
    trace = "".join(traceback.format_exception(raised.value))
    crash_path = CrashDump(base_dir=tmp_path / "crash").write(exc=raised.value)
    assert secret not in trace
    assert secret not in crash_path.read_text()
    assert "sensitive-results" not in trace
    assert "sensitive-results" not in crash_path.read_text()


async def test_original_artifact_preserves_literal_percent_and_leading_key_separator():
    client, _boto, _session, s3, _body, raw = fixture()
    raw["QueryExecution"]["ResultConfiguration"]["OutputLocation"] = (
        "s3://sensitive-results//literal%2Fkey.csv"
    )
    detail = await client.get_query_execution("q-123")
    await collect(client, detail)
    assert s3.head_object.call_args.kwargs["Key"] == "/literal%2Fkey.csv"
