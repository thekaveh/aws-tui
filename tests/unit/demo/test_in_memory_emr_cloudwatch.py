from __future__ import annotations

from unittest.mock import patch

import pytest

from aws_tui.demo.in_memory_emr import InMemoryEmr
from aws_tui.domain.emr_cloudwatch_logs import CloudWatchLogEvent, CloudWatchLogStream
from aws_tui.domain.emr_serverless import CloudWatchLogConfiguration
from aws_tui.domain.filesystem import NotFoundError, ProviderError, ValidationError

GROUP = "/demo/emr"
NAME = "/applications/a/jobs/r/attempts/2/SPARK_DRIVER"
CONFIG = CloudWatchLogConfiguration(True, GROUP)


def seed(fake):
    fake.add_cloudwatch_stream(
        application_id="a",
        job_run_id="r",
        log_group_name=GROUP,
        stream=CloudWatchLogStream(NAME, "SPARK_DRIVER", 2),
        events=(
            CloudWatchLogEvent("1", 1000, 1001, "ERROR secret-body-demo"),
            CloudWatchLogEvent("2", 2000, 2001, "same"),
            CloudWatchLogEvent("3", 2000, 2001, "same"),
            CloudWatchLogEvent("4", 999, 1000, "outside"),
        ),
    )


async def test_demo_discovery_and_inclusive_reads_without_network():
    fake = InMemoryEmr()
    seed(fake)
    with patch("aioboto3.Session", side_effect=AssertionError("network")):
        streams = await fake.list_cloudwatch_streams(
            configuration=CONFIG, application_id="a", job_run_id="r"
        )
        assert [s.name for s in streams] == [NAME]
        result = await fake.read_cloudwatch_events(
            log_group_name=GROUP, stream_name=NAME, start_time_ms=1000, end_time_ms=2000
        )
        assert [e.event_id for e in result.events] == ["1", "2", "3"]
        assert result.bytes_read == len(b"ERROR secret-body-demo") + 8
        assert "secret-body-demo" not in repr(fake.calls)
        fake.append_cloudwatch_event(
            log_group_name=GROUP,
            stream_name=NAME,
            event=CloudWatchLogEvent("5", 2000, 2002, "ERROR new"),
        )
        again = await fake.read_cloudwatch_events(
            log_group_name=GROUP, stream_name=NAME, start_time_ms=1000, end_time_ms=2000
        )
        assert [e.event_id for e in again.events] == ["1", "2", "3", "5"]


async def test_demo_isolation_and_missing_stream_group():
    first, second = InMemoryEmr(), InMemoryEmr()
    seed(first)
    with pytest.raises(NotFoundError):
        await second.list_cloudwatch_streams(
            configuration=CONFIG, application_id="a", job_run_id="r"
        )
    assert (
        await first.list_cloudwatch_streams(
            configuration=CONFIG, application_id="a", job_run_id="other"
        )
        == ()
    )
    with pytest.raises(NotFoundError):
        await first.read_cloudwatch_events(
            log_group_name=GROUP, stream_name="missing", start_time_ms=0, end_time_ms=2000
        )
    with pytest.raises(ValidationError):
        await first.list_cloudwatch_streams(
            configuration=CloudWatchLogConfiguration(False), application_id="a", job_run_id="r"
        )


async def test_demo_preserves_both_monitoring_sources_and_clone_metadata():
    fake = InMemoryEmr()
    fake.add_application(app_id="a", name="app")
    detail = fake.add_job_run_detail(
        application_id="a",
        job_run_id="r",
        cloudwatch_monitoring=CONFIG,
        s3_monitoring_log_uri="s3://bucket/logs",
    )
    assert detail.cloudwatch_monitoring == CONFIG
    assert detail.configuration_overrides["monitoringConfiguration"][
        "cloudWatchLoggingConfiguration"
    ]["enabled"]
    clone = await fake.start_job_run(
        "a",
        execution_role_arn=detail.execution_role_arn,
        entry_point=detail.entry_point,
        entry_point_arguments=(),
        spark_submit_parameters=None,
        client_token="cloudwatch-clone",
        configuration_overrides=detail.configuration_overrides,
    )
    try:
        copied = await fake.get_job_run("a", clone)
        assert copied.cloudwatch_monitoring == CONFIG
        assert copied.s3_monitoring_log_uri == "s3://bucket/logs"
    finally:
        fake.dispose()


async def test_demo_read_cap_and_id_dedup():
    fake = InMemoryEmr()
    seed(fake)
    repeated = CloudWatchLogEvent("repeated", 1000, 1001, "é" * (512 * 1024))
    for _ in range(9):
        fake.append_cloudwatch_event(log_group_name=GROUP, stream_name=NAME, event=repeated)
    with pytest.raises(ProviderError, match="read limit"):
        await fake.read_cloudwatch_events(
            log_group_name=GROUP, stream_name=NAME, start_time_ms=0, end_time_ms=2000
        )


async def test_demo_orders_events_like_cloudwatch_and_keeps_first_duplicate():
    fake = InMemoryEmr()
    seed(fake)
    fake.append_cloudwatch_event(
        log_group_name=GROUP,
        stream_name=NAME,
        event=CloudWatchLogEvent("late", 1000, 1002, "late arrival"),
    )
    fake.append_cloudwatch_event(
        log_group_name=GROUP,
        stream_name=NAME,
        event=CloudWatchLogEvent("late", 1000, 1002, "duplicate"),
    )
    result = await fake.read_cloudwatch_events(
        log_group_name=GROUP, stream_name=NAME, start_time_ms=1000, end_time_ms=2000
    )
    assert [event.event_id for event in result.events] == ["1", "late", "2", "3"]
    assert result.events[1].message == "late arrival"


@pytest.mark.parametrize("count", [200, 201])
async def test_demo_discovery_cap(count):
    fake = InMemoryEmr()
    for index in range(count):
        name = f"/applications/a/jobs/r/SPARK_EXECUTOR/{index}"
        fake.add_cloudwatch_stream(
            application_id="a",
            job_run_id="r",
            log_group_name=GROUP,
            stream=CloudWatchLogStream(name, "SPARK_EXECUTOR", None),
            events=(),
        )
    if count == 200:
        assert (
            len(
                await fake.list_cloudwatch_streams(
                    configuration=CONFIG, application_id="a", job_run_id="r"
                )
            )
            == 200
        )
    else:
        with pytest.raises(ProviderError, match="stream limit"):
            await fake.list_cloudwatch_streams(
                configuration=CONFIG, application_id="a", job_run_id="r"
            )


async def test_demo_invalid_bodies_have_sanitized_exception_context():
    fake = InMemoryEmr()
    seed(fake)
    fake.append_cloudwatch_event(
        log_group_name=GROUP,
        stream_name=NAME,
        event=CloudWatchLogEvent("invalid", 1000, 1001, "body\ud800"),
    )
    with pytest.raises(ValidationError) as caught:
        await fake.read_cloudwatch_events(
            log_group_name=GROUP, stream_name=NAME, start_time_ms=0, end_time_ms=2000
        )
    assert str(caught.value) == "CloudWatch log response invalid"
    assert caught.value.__context__ is None


@pytest.mark.parametrize("prefix", [None, "literal/", "/applications/foreign/jobs/prefix/"])
async def test_demo_rejects_ambiguous_run_roots_preserves_suffix_words(prefix):
    fake = InMemoryEmr()
    literal = prefix or ""
    selected = "/applications/a/jobs/r/"
    foreign = "/applications/b/jobs/other/"
    valid = [
        literal + selected + "SPARK_DRIVER/jobs/applications/worker/stdout",
        literal + selected + "attempts/2/SPARK_EXECUTOR/applications/jobs/stdout",
    ]
    ambiguous = [
        literal + selected + "SPARK_DRIVER" + foreign + "SPARK_DRIVER",
        literal + selected + "SPARK_DRIVER" + foreign.rstrip("/"),
        literal + selected + "SPARK_DRIVER" + selected + "SPARK_DRIVER",
        literal + foreign + "SPARK_DRIVER" + selected + "SPARK_DRIVER",
    ]
    for name in valid + ambiguous:
        fake.add_cloudwatch_stream(
            application_id="a",
            job_run_id="r",
            log_group_name=GROUP,
            stream=CloudWatchLogStream(name, "SPARK_DRIVER", None),
            events=(),
        )
    streams = await fake.list_cloudwatch_streams(
        configuration=CloudWatchLogConfiguration(True, GROUP, prefix),
        application_id="a",
        job_run_id="r",
    )
    assert [stream.name for stream in streams] == valid
    assert [(stream.component, stream.attempt) for stream in streams] == [
        ("SPARK_DRIVER", None),
        ("SPARK_EXECUTOR", 2),
    ]
