"""CloudWatch read contracts: exact identity, bounded work and body-free failures."""

from __future__ import annotations

import asyncio
import importlib
import traceback
from unittest.mock import AsyncMock

import botocore.exceptions
import pytest

from aws_tui.domain.emr_serverless import EMR_BOTO_CONFIG, CloudWatchLogConfiguration
from aws_tui.domain.filesystem import (
    AuthRequiredError,
    NotFoundError,
    PermissionDeniedError,
    ProviderError,
    ProviderUnreachableError,
    ThrottledError,
    ValidationError,
)

ROOT = "/applications/a/jobs/r/"
STREAM = ROOT + "SPARK_DRIVER"
SENTINEL = "arbitrary-log-body-sentinel-261"


@pytest.fixture
def cw():
    return importlib.import_module("aws_tui.domain.emr_cloudwatch_logs")


class LogsStub:
    def __init__(self, *, listings=(), reads=(), exit_error=None):
        self.describe_log_streams = AsyncMock(side_effect=list(listings))
        self.filter_log_events = AsyncMock(side_effect=list(reads))
        self.closed = False
        self.exit_error = exit_error

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        self.closed = True
        if self.exit_error is not None:
            raise self.exit_error


class SessionStub:
    def __init__(self, client):
        self.logs = client
        self.calls = []

    def client(self, service, **kwargs):
        self.calls.append((service, kwargs))
        return self.logs


def event(event_id="e1", message="ERROR café", *, timestamp=1000, stream=STREAM):
    return dict(
        eventId=event_id,
        message=message,
        timestamp=timestamp,
        ingestionTime=timestamp + 1,
        logStreamName=stream,
    )


async def read(cw, stub, **kwargs):
    return await cw.read_cloudwatch_events(
        session=SessionStub(stub),
        region_name="us-west-2",
        log_group_name="/aws/emr-serverless",
        stream_name=STREAM,
        start_time_ms=kwargs.pop("start", 0),
        end_time_ms=kwargs.pop("end", 2000),
        **kwargs,
    )


async def discover(cw, stub, *, prefix=None, enabled=True):
    return await cw.list_cloudwatch_streams(
        session=SessionStub(stub),
        region_name="us-west-2",
        configuration=CloudWatchLogConfiguration(enabled, None, prefix),
        application_id="a",
        job_run_id="r",
    )


async def test_exact_request_shapes_region_config_and_empty_page(cw):
    stub = LogsStub(reads=[{"events": [], "nextToken": "next"}, {"events": [event()]}])
    session = SessionStub(stub)
    result = await cw.read_cloudwatch_events(
        session=session,
        region_name="us-west-2",
        boto_config=EMR_BOTO_CONFIG,
        log_group_name="/custom/emr",
        stream_name=STREAM,
        start_time_ms=0,
        end_time_ms=2000,
    )
    assert session.calls == [("logs", {"region_name": "us-west-2", "config": EMR_BOTO_CONFIG})]
    assert result.events[0].message == "ERROR café"
    assert result.bytes_read == len("ERROR café".encode())
    assert result.end_time_ms == 2000
    first, second = stub.filter_log_events.await_args_list
    assert first.kwargs == dict(
        logGroupName="/custom/emr", logStreamNames=[STREAM], startTime=0, endTime=2000, limit=1000
    )
    assert second.kwargs == {**first.kwargs, "nextToken": "next"}
    assert stub.closed


async def test_discovery_request_and_retry_component_identity(cw):
    names = [
        ROOT + "attempts/2/SPARK_DRIVER",
        ROOT + "SPARK_EXECUTOR/2/stderr",
        STREAM,
        ROOT + "attempts/1/SPARK_DRIVER",
        "/applications/a/jobs/r2/SPARK_DRIVER",
    ]
    stub = LogsStub(listings=[{"logStreams": [{"logStreamName": name} for name in names]}])
    session = SessionStub(stub)
    streams = await cw.list_cloudwatch_streams(
        session=session,
        region_name="us-west-2",
        boto_config=EMR_BOTO_CONFIG,
        configuration=CloudWatchLogConfiguration(True),
        application_id="a",
        job_run_id="r",
    )
    assert session.calls == [("logs", {"region_name": "us-west-2", "config": EMR_BOTO_CONFIG})]
    assert stub.describe_log_streams.await_args.kwargs == dict(
        logGroupName="/aws/emr-serverless",
        logStreamNamePrefix=ROOT,
        orderBy="LogStreamName",
        limit=50,
    )
    assert [(s.name, s.component, s.attempt) for s in streams] == [
        (STREAM, "SPARK_DRIVER", None),
        (names[3], "SPARK_DRIVER", 1),
        (names[0], "SPARK_DRIVER", 2),
        (names[1], "SPARK_EXECUTOR", None),
    ]


@pytest.mark.parametrize("prefix", ["literal", "literal/", "SPARK_DRIVER/applications/a/jobs/r/"])
async def test_custom_prefix_uses_only_discovered_run_relative_identity(cw, prefix):
    good = prefix + ROOT + "attempts/2/UNKNOWN/worker"
    names = [
        good,
        prefix + ROOT + "attempts/0/SPARK_DRIVER",
        prefix + ROOT + "attempts/no/SPARK_DRIVER",
        prefix + ROOT + "SPARK_DRIVER" + ROOT + "SPARK_DRIVER",
        prefix + "/applications/b/jobs/r/SPARK_DRIVER",
        prefix + "SPARK_DRIVER",
    ]
    stub = LogsStub(listings=[{"logStreams": [{"logStreamName": name} for name in names]}])
    streams = await discover(cw, stub, prefix=prefix)
    assert [s.name for s in streams] == [good]
    assert streams[0].component == "UNKNOWN"
    assert stub.describe_log_streams.await_args.kwargs["logStreamNamePrefix"] == prefix


async def test_prefix_ending_slash_accepts_root_without_duplicate_slash(cw):
    name = "custom/" + ROOT.lstrip("/") + "SPARK_DRIVER"
    stub = LogsStub(listings=[{"logStreams": [{"logStreamName": name}]}])
    assert [s.name for s in await discover(cw, stub, prefix="custom/")] == [name]


@pytest.mark.parametrize("enabled", [False, None])
async def test_not_enabled_never_calls_aws(cw, enabled):
    stub = LogsStub()
    with pytest.raises(ValidationError):
        await discover(cw, stub, enabled=enabled)
    stub.describe_log_streams.assert_not_awaited()


@pytest.mark.parametrize("operation", ["read", "discover"])
@pytest.mark.parametrize("tokens", [["same", "same"], ["a", "b", "a"]])
async def test_token_cycles_fail_without_extra_call(cw, operation, tokens):
    responses = [{"nextToken": token} for token in tokens]
    stub = LogsStub(reads=responses, listings=responses)
    with pytest.raises(ProviderError, match="continuation token"):
        await (read(cw, stub) if operation == "read" else discover(cw, stub))
    method = stub.filter_log_events if operation == "read" else stub.describe_log_streams
    assert method.await_count == len(tokens)
    assert stub.closed


@pytest.mark.parametrize("operation", ["read", "discover"])
@pytest.mark.parametrize("terminal", [True, False])
async def test_production_page_cap_exact_boundary(cw, operation, terminal):
    pages = [{"nextToken": str(index)} for index in range(100)]
    if terminal:
        pages[-1] = {}
    stub = LogsStub(reads=pages, listings=pages)
    if terminal:
        await (read(cw, stub) if operation == "read" else discover(cw, stub))
    else:
        with pytest.raises(ProviderError, match="page limit"):
            await (read(cw, stub) if operation == "read" else discover(cw, stub))
    method = stub.filter_log_events if operation == "read" else stub.describe_log_streams
    assert method.await_count == 100


@pytest.mark.parametrize("count", [200, 201])
async def test_discovery_counts_duplicate_and_unrelated_records(cw, count):
    stub = LogsStub(listings=[{"logStreams": [{"logStreamName": "/unrelated"}] * count}])
    if count == 200:
        assert await discover(cw, stub) == ()
    else:
        with pytest.raises(ProviderError, match="stream limit"):
            await discover(cw, stub)


@pytest.mark.parametrize("count", [10_000, 10_001])
async def test_returned_event_cap_counts_duplicates(cw, count):
    stub = LogsStub(reads=[{"events": [event(message="")] * count}])
    if count == 10_000:
        assert len((await read(cw, stub)).events) == 1
    else:
        with pytest.raises(ProviderError, match="read limit"):
            await read(cw, stub)


@pytest.mark.parametrize("extra", [0, 1])
async def test_utf8_read_budget_exact_boundary(cw, extra):
    messages = ["é" * (512 * 1024)] * 8 + (["x"] if extra else [])
    stub = LogsStub(reads=[{"events": [event(str(i), text) for i, text in enumerate(messages)]}])
    if extra:
        with pytest.raises(ProviderError, match="read limit"):
            await read(cw, stub)
    else:
        assert (await read(cw, stub)).bytes_read == 8 * 1024 * 1024


async def test_one_event_byte_cap(cw):
    with pytest.raises(ProviderError, match="read limit"):
        await read(cw, LogsStub(reads=[{"events": [event(message="x" * (1024 * 1024 + 1))]}]))


@pytest.mark.parametrize("size", [512, 513])
async def test_unicode_stream_name_character_boundary(cw, size):
    name = ROOT + "😀" * (size - len(ROOT))
    stub = LogsStub(listings=[{"logStreams": [{"logStreamName": name}]}])
    if size == 512:
        assert (await discover(cw, stub))[0].name == name
    else:
        with pytest.raises(ValidationError):
            await discover(cw, stub)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("eventId", ""),
        ("eventId", "a" * 1025),
        ("message", 4),
        ("message", "\ud800"),
        ("timestamp", True),
        ("timestamp", -1),
        ("timestamp", 2001),
        ("ingestionTime", -1),
        ("logStreamName", "/wrong"),
        ("eventId", None),
    ],
)
async def test_malformed_event_never_echoes_response(cw, field, value):
    raw = event(message=SENTINEL)
    raw[field] = value
    with pytest.raises(ValidationError) as caught:
        await read(cw, LogsStub(reads=[{"events": [raw]}]))
    assert SENTINEL not in str(caught.value)


@pytest.mark.parametrize(
    "response",
    [
        None,
        [],
        {"events": {}},
        {"events": [None]},
        {"events": [{}]},
        {"nextToken": ""},
        {"nextToken": "a" * 8193},
    ],
)
async def test_malformed_read_containers(cw, response):
    with pytest.raises(ValidationError):
        await read(cw, LogsStub(reads=[response]))


async def test_event_id_and_token_metadata_exact_caps(cw):
    snapshot = await read(
        cw, LogsStub(reads=[{"nextToken": "a" * 8192}, {"events": [event("e" * 1024)]}])
    )
    assert len(snapshot.events[0].event_id) == 1024


async def test_inclusive_time_boundaries_and_id_not_text_dedup(cw):
    result = await read(
        cw,
        LogsStub(
            reads=[
                {
                    "events": [
                        event("start", timestamp=1000),
                        event("end", timestamp=2000),
                        event("end", timestamp=2000),
                        event("distinct", timestamp=2000),
                    ]
                }
            ]
        ),
        start=1000,
    )
    assert [e.event_id for e in result.events] == ["start", "end", "distinct"]
    with pytest.raises(ValidationError):
        await read(cw, LogsStub(reads=[{"events": [event(timestamp=999)]}]), start=1000)


@pytest.mark.parametrize(
    ("code", "kind"),
    [
        ("ExpiredTokenException", AuthRequiredError),
        ("AccessDeniedException", PermissionDeniedError),
        ("ThrottlingException", ThrottledError),
        ("ResourceNotFoundException", NotFoundError),
        ("ServiceUnavailableException", ProviderUnreachableError),
        ("InvalidParameterException", ValidationError),
        ("Other", ProviderError),
    ],
)
async def test_sdk_failures_are_typed_without_bodies_or_exception_chains(cw, code, kind):
    error = botocore.exceptions.ClientError(
        {"Error": {"Code": code, "Message": SENTINEL}}, "FilterLogEvents"
    )
    with pytest.raises(kind) as caught:
        await read(cw, LogsStub(reads=[error]))
    safe = caught.value
    assert SENTINEL not in str(safe)
    assert SENTINEL not in repr(safe)
    assert SENTINEL not in "".join(traceback.format_exception(safe))
    assert safe.__cause__ is None
    assert safe.__context__ is None


@pytest.mark.parametrize(
    "error",
    [
        RuntimeError(SENTINEL),
        ProviderError(SENTINEL),
        botocore.exceptions.NoCredentialsError(),
        botocore.exceptions.EndpointConnectionError(endpoint_url=SENTINEL),
    ],
)
async def test_unexpected_and_injected_error_cleanup_are_safe(cw, error):
    error.__cause__ = RuntimeError(SENTINEL)
    with pytest.raises(ProviderError) as caught:
        await read(cw, LogsStub(reads=[{"events": [event(message=SENTINEL)]}], exit_error=error))
    assert SENTINEL not in "".join(traceback.format_exception(caught.value))
    assert caught.value.__context__ is None


async def test_records_hide_event_bodies(cw):
    result = await read(cw, LogsStub(reads=[{"events": [event(message=SENTINEL)]}]))
    assert SENTINEL not in repr(result)
    assert SENTINEL not in repr(result.events[0])


@pytest.mark.parametrize("exit_error", [None, RuntimeError(SENTINEL)])
async def test_cancel_closes_client_and_sanitizes_cleanup(cw, exit_error):
    entered = asyncio.Event()

    async def blocked(**kwargs):
        entered.set()
        await asyncio.Event().wait()

    stub = LogsStub(exit_error=exit_error)
    stub.filter_log_events.side_effect = blocked
    task = asyncio.create_task(read(cw, stub))
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError if exit_error is None else ProviderError) as caught:
        await task
    assert stub.closed
    assert SENTINEL not in "".join(traceback.format_exception(caught.value))
    assert caught.value.__context__ is None


async def test_deadline_closes_client_and_is_unreachable(cw, monkeypatch):
    monkeypatch.setattr(cw, "READ_TIMEOUT_SECONDS", 0.01)

    async def blocked(**kwargs):
        await asyncio.Event().wait()

    stub = LogsStub()
    stub.filter_log_events.side_effect = blocked
    with pytest.raises(ProviderUnreachableError):
        await read(cw, stub)
    assert stub.closed


async def test_facade_forwards_connection_owned_arguments(cw):
    from aws_tui.domain.emr_logs import EmrServerlessLogsClient

    stub = LogsStub(
        listings=[{"logStreams": [{"logStreamName": STREAM}]}], reads=[{"events": [event()]}]
    )
    session = SessionStub(stub)
    facade = EmrServerlessLogsClient(
        session=session, region_name="us-west-2", boto_config=EMR_BOTO_CONFIG
    )
    assert (
        await facade.list_cloudwatch_streams(
            configuration=CloudWatchLogConfiguration(True), application_id="a", job_run_id="r"
        )
    )[0].name == STREAM
    assert (
        await facade.read_cloudwatch_events(
            log_group_name="/custom", stream_name=STREAM, start_time_ms=0, end_time_ms=2000
        )
    ).events[0].event_id == "e1"
    assert session.calls == [("logs", {"region_name": "us-west-2", "config": EMR_BOTO_CONFIG})] * 2


@pytest.mark.parametrize(
    "response",
    [
        None,
        [],
        {"logStreams": {}},
        {"logStreams": [None]},
        {"logStreams": [{}]},
        {"logStreams": [{"logStreamName": "\ud800"}]},
        {"nextToken": []},
    ],
)
async def test_malformed_discovery_is_safe_validation(cw, response):
    with pytest.raises(ValidationError):
        await discover(cw, LogsStub(listings=[response]))


@pytest.mark.parametrize("operation", ["read", "discover"])
async def test_new_provider_error_reaches_diagnostics_without_body(cw, operation, caplog):
    from aws_tui.vm.messages import ServiceOperationFailedMessage

    raw = botocore.exceptions.ClientError(
        {"Error": {"Code": "AccessDeniedException", "Message": SENTINEL}}, "FilterLogEvents"
    )
    stub = LogsStub(reads=[raw], listings=[raw])
    with pytest.raises(PermissionDeniedError) as caught:
        await (read(cw, stub) if operation == "read" else discover(cw, stub))
    diagnostic = ServiceOperationFailedMessage.from_error(
        service="emr-serverless", operation="load_job_logs", error=caught.value
    )
    assert SENTINEL not in repr(diagnostic)
    assert SENTINEL not in repr(caplog.records)


async def test_small_utf8_budget_is_checked_before_retention(cw, monkeypatch):
    monkeypatch.setattr(cw, "MAX_READ_BYTES", 4)
    assert (await read(cw, LogsStub(reads=[{"events": [event(message="éé")]}]))).bytes_read == 4
    with pytest.raises(ProviderError, match="read limit"):
        await read(cw, LogsStub(reads=[{"events": [event(message="ééx")]}]))


@pytest.mark.parametrize(("start", "end"), [(-1, 10), (20, 10), (True, 10)])
async def test_invalid_interval_is_rejected_before_sdk_call(cw, start, end):
    stub = LogsStub()
    with pytest.raises(ValidationError):
        await read(cw, stub, start=start, end=end)
    stub.filter_log_events.assert_not_awaited()


async def test_discovery_deadline_covers_blocked_sdk_and_closes(cw, monkeypatch):
    assert cw.READ_TIMEOUT_SECONDS == 30.0
    monkeypatch.setattr(cw, "READ_TIMEOUT_SECONDS", 0.01)

    async def blocked(**kwargs):
        await asyncio.Event().wait()

    stub = LogsStub()
    stub.describe_log_streams.side_effect = blocked
    with pytest.raises(ProviderUnreachableError):
        await discover(cw, stub)
    assert stub.closed
