"""Pinned preview ranges over isolated moto HTTP and recording body adapters."""

from __future__ import annotations

import asyncio

import aioboto3
import pytest
from botocore.exceptions import ClientError

from aws_tui.domain.filesystem import PathRef, PreviewSourceChangedError
from aws_tui.domain.preview_limits import PreviewBudget, PreviewLimitExceeded, PreviewRequestKind
from aws_tui.domain.s3_fs import S3FS
from tests.unit.domain.conftest import moto_server, s3_endpoint  # noqa: F401

# These local moto HTTP tests run with the default local test selection.


def session():
    return aioboto3.Session(
        region_name="us-east-1", aws_access_key_id="testing", aws_secret_access_key="testing"
    )


@pytest.mark.parametrize("versioning", ["Enabled", "Suspended", None])
async def test_moto_pinned_ranges_reject_latest_drift(s3_endpoint, versioning):  # noqa: F811
    aws = session()
    async with aws.client("s3", endpoint_url=s3_endpoint) as client:
        await client.create_bucket(Bucket="preview-test")
        if versioning:
            await client.put_bucket_versioning(
                Bucket="preview-test", VersioningConfiguration={"Status": versioning}
            )
        first = await client.put_object(Bucket="preview-test", Key="sample", Body=b"a,b\n1,2\n")
        head = await client.head_object(Bucket="preview-test", Key="sample")
        first_version, first_etag = first.get("VersionId"), head["ETag"]
        fs = S3FS(
            session=aws, bucket="preview-test", endpoint_url=s3_endpoint, force_path_style=True
        )
        calls, wires = [], []

        def parameters(params, model, **kwargs):
            calls.append((model.name, dict(params)))

        def wire(request, **kwargs):
            wires.append((request.method, request.url))

        # Inherited session handlers see the dedicated client's real sends.
        aws.events.register("before-parameter-build.s3", parameters)
        aws.events.register("before-send.s3", wire)
        budget = PreviewBudget.start()
        preview = await fs.open_preview(PathRef(("sample",)), budget=budget)
        try:
            assert await preview.read_range(0, 3) == b"a,b"
            await client.put_object(Bucket="preview-test", Key="sample", Body=b"different")
            if versioning == "Enabled":
                assert await preview.read_range(4, 3) == b"1,2"
            else:
                with pytest.raises(PreviewSourceChangedError):
                    await preview.read_range(4, 3)
            with pytest.raises(PreviewSourceChangedError):
                await preview.validate()
        finally:
            await preview.aclose()
            await preview.aclose()
        ranges = [params for name, params in calls if name == "GetObject"]
        assert all(call["IfMatch"] == first_etag for call in ranges)
        assert all(call["Range"].startswith("bytes=") for call in ranges)
        if first_version is not None:
            assert all(call["VersionId"] == first_version for call in ranges)
        else:
            assert all("VersionId" not in call for call in ranges)
        assert sum(name == "HeadObject" for name, _ in calls) == 2
        assert budget.requests == len(wires) == 2 + len(ranges)
        assert budget.bytes_requested == sum(3 for _ in ranges)
        assert all(name in {"HeadObject", "GetObject"} for name, _ in calls)
        assert fs._config.retries["total_max_attempts"] == 6


class Events:
    def __init__(self):
        self.handlers = {}

    def register(self, event, handler, **kwargs):
        self.handlers[event] = handler

    def unregister(self, event, handler=None, **kwargs):
        self.handlers.pop(event)

    def send(self, operation):
        for event, handler in self.handlers.items():
            if event in ("before-send.s3", f"before-send.s3.{operation}"):
                handler(event_name=f"before-send.s3.{operation}")


class Body:
    def __init__(self, data=b"abc", *, suspend=False):
        self.data, self.suspend, self.closed = data, suspend, False
        self.started = asyncio.Event()
        self.read_lengths = []
        self.position = 0

    async def read(self, length):
        self.read_lengths.append(length)
        self.started.set()
        if self.suspend:
            await asyncio.Event().wait()
        result = self.data[self.position : self.position + length]
        self.position += len(result)
        return result

    def close(self):
        self.closed = True


class Client:
    def __init__(self):
        self.events = Events()
        self.meta = type("Meta", (), {"events": self.events})()
        self.body = Body()
        self.head = {"ETag": '"etag"', "ContentLength": 3, "VersionId": "null"}
        self.response = {
            "ETag": '"etag"',
            "VersionId": "null",
            "ContentLength": 3,
            "ContentRange": "bytes 0-2/3",
            "ResponseMetadata": {"HTTPStatusCode": 206},
        }
        self.calls = []
        self.extra_sends = 0
        self.error = None
        self.closed = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        self.closed = True

    async def head_object(self, **kwargs):
        self.calls.append(("HeadObject", kwargs))
        self.events.send("HeadObject")
        if self.error:
            raise self.error
        return dict(self.head)

    async def get_object(self, **kwargs):
        self.calls.append(("GetObject", kwargs))
        self.events.send("GetObject")
        for _ in range(self.extra_sends):
            self.events.send("GetObject")
        if self.error:
            raise self.error
        return {**self.response, "Body": self.body}


class Session:
    def __init__(self, client):
        self.value, self.kwargs = client, None

    def client(self, service, **kwargs):
        self.kwargs = kwargs
        return self.value


async def fake_preview():
    client = Client()
    aws = Session(client)
    fs = S3FS(session=aws, bucket="bucket", endpoint_url="http://127.0.0.1", force_path_style=True)
    budget = PreviewBudget.start()
    preview = await fs.open_preview(PathRef(("sample",)), budget=budget)
    return preview, client, aws, fs, budget


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("ETag", '"wrong"'),
        ("VersionId", "wrong"),
        ("ContentRange", "bytes 0-2/9"),
        ("ContentLength", 4),
        ("ResponseMetadata", {"HTTPStatusCode": 200}),
    ],
)
async def test_reject_bad_headers_before_body(field, value):
    preview, client, *_ = await fake_preview()
    client.response[field] = value
    with pytest.raises(PreviewSourceChangedError):
        await preview.read_range(0, 3)
    assert client.body.closed
    assert client.body.read_lengths == []
    await preview.aclose()


async def test_literal_null_identity_and_dedicated_retry_config():
    preview, client, aws, fs, budget = await fake_preview()
    assert await preview.read_range(0, 3) == b"abc"
    await preview.validate()
    assert client.calls[1][1] == {
        "Bucket": "bucket",
        "Key": "sample",
        "Range": "bytes=0-2",
        "IfMatch": '"etag"',
        "VersionId": "null",
    }
    assert client.body.closed
    assert client.body.read_lengths == [3]
    assert aws.kwargs["config"].retries["total_max_attempts"] == 1
    assert fs._config.retries["total_max_attempts"] == 6
    assert aws.kwargs["config"].s3 == fs._config.s3
    assert aws.kwargs["verify"] is True
    assert budget.requests == 3
    await preview.aclose()
    await preview.aclose()
    assert client.closed
    assert client.events.handlers == {}


async def test_redirect_physical_attempts_charge_without_logical_double_charge():
    preview, client, _, _, budget = await fake_preview()
    client.extra_sends = 1
    assert await preview.read_range(0, 3) == b"abc"
    await preview.validate()
    assert budget.requests == 4
    assert budget.bytes_requested == 6
    assert len(client.calls) == 3
    await preview.aclose()


async def test_redirect_rejected_before_send_when_reserved_request_exhausted():
    preview, client, _, _, budget = await fake_preview()
    for _ in range(29):
        budget.charge_request(kind=PreviewRequestKind.RANGE, length=0)
    client.extra_sends = 1
    with pytest.raises(PreviewLimitExceeded, match="request budget"):
        await preview.read_range(0, 3)
    assert budget.requests == 31
    await preview.validate()
    assert budget.requests == 32
    await preview.aclose()


@pytest.mark.parametrize("code", ["PreconditionFailed", "NoSuchVersion", "NoSuchKey", "404"])
async def test_source_errors_reject_preview(code):
    preview, client, *_ = await fake_preview()
    client.error = ClientError({"Error": {"Code": code}}, "GetObject")
    with pytest.raises(PreviewSourceChangedError):
        await preview.read_range(0, 3)
    with pytest.raises(PreviewSourceChangedError):
        await preview.validate()
    await preview.aclose()


async def test_short_body_rejected_and_closed():
    preview, client, *_ = await fake_preview()
    client.body.data = b"ab"
    with pytest.raises(PreviewSourceChangedError):
        await preview.read_range(0, 3)
    assert client.body.closed
    await preview.aclose()


async def test_cancellation_closes_body_and_client():
    preview, client, *_ = await fake_preview()
    client.body.suspend = True
    task = asyncio.create_task(preview.read_range(0, 3))
    await client.body.started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert client.body.closed
    await preview.aclose()
    assert client.closed
    assert not client.events.handlers


async def test_zero_and_invalid_ranges_never_send():
    preview, client, _, _, budget = await fake_preview()
    assert await preview.read_range(3, 0) == b""
    for offset, length in [(-1, 1), (0, -1), (4, 0), (2, 2)]:
        with pytest.raises(ValueError, match="range"):
            await preview.read_range(offset, length)
    assert budget.requests == 1
    assert len(client.calls) == 1
    await preview.aclose()


async def test_fragmented_body_fills_only_admitted_range():
    preview, client, *_ = await fake_preview()
    pieces = [b"a", b"b", b"c"]

    async def fragmented(length):
        client.body.read_lengths.append(length)
        return pieces.pop(0)

    client.body.read = fragmented
    assert await preview.read_range(0, 3) == b"abc"
    assert client.body.read_lengths == [3, 2, 1]
    assert client.body.closed
    await preview.aclose()


async def test_auxiliary_redirect_head_counts_without_range_bytes():
    preview, client, _, _, budget = await fake_preview()
    original = client.get_object

    async def redirected(**kwargs):
        client.events.send("HeadBucket")
        return await original(**kwargs)

    client.get_object = redirected
    assert await preview.read_range(0, 3) == b"abc"
    assert budget.requests == 3
    assert budget.bytes_requested == 3
    await preview.validate()
    assert budget.requests == 4
    await preview.aclose()


async def test_open_deadline_closes_client_and_hooks():
    client = Client()
    now = [0.0]
    budget = PreviewBudget.start(clock=lambda: now[0])
    original = client.head_object

    async def late_head(**kwargs):
        response = await original(**kwargs)
        now[0] = 5.0
        return response

    client.head_object = late_head
    fs = S3FS(session=Session(client), bucket="bucket")
    with pytest.raises(PreviewLimitExceeded, match="timed out"):
        await fs.open_preview(PathRef(("sample",)), budget=budget)
    assert client.closed
    assert not client.events.handlers


async def test_moto_repeated_physical_send_is_accounted(s3_endpoint):  # noqa: F811
    aws = session()
    async with aws.client("s3", endpoint_url=s3_endpoint) as client:
        await client.create_bucket(Bucket="preview-repeated-send")
        await client.put_object(Bucket="preview-repeated-send", Key="sample", Body=b"abc")
    attempts, wires = [], []

    def redirect_once(attempts, **kwargs):
        # Region redirect handlers use this retry event even with one configured
        # attempt. Simulate that resend over actual localhost HTTP.
        if attempts == 1:
            return 0
        return None

    def actual_send(request, event_name, **kwargs):
        wires.append((request.method, event_name))

    def parameters(params, model, **kwargs):
        attempts.append((model.name, dict(params)))

    aws.events.register("needs-retry.s3.GetObject", redirect_once)
    aws.events.register("before-send.s3", actual_send)
    aws.events.register("before-parameter-build.s3", parameters)
    fs = S3FS(session=aws, bucket="preview-repeated-send", endpoint_url=s3_endpoint)
    budget = PreviewBudget.start()
    preview = await fs.open_preview(PathRef(("sample",)), budget=budget)
    try:
        assert await preview.read_range(0, 3) == b"abc"
        await preview.validate()
    finally:
        await preview.aclose()
    assert [method for method, _ in wires] == ["HEAD", "GET", "GET", "HEAD"]
    assert [operation for operation, _ in attempts] == ["HeadObject", "GetObject", "HeadObject"]
    assert budget.requests == 4
    assert budget.bytes_requested == 6
