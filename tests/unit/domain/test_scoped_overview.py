from datetime import UTC, datetime

import pytest

from aws_tui.domain.scoped_overview import OverviewEntry, OverviewPage, WatchScope


@pytest.mark.parametrize(
    ("kind", "status", "expected"),
    [
        ("athena", "QUEUED", "active"),
        ("athena", "RUNNING", "active"),
        ("athena", "FAILED", "failed"),
        ("athena", "SUCCEEDED", "other"),
        ("athena", "CANCELLED", "other"),
        ("glue-job", "WAITING", "active"),
        ("glue-job", "STARTING", "active"),
        ("glue-job", "STOPPING", "active"),
        ("glue-job", "TIMEOUT", "failed"),
        ("glue-job", "ERROR", "failed"),
        ("glue-job", "EXPIRED", "failed"),
        ("glue-job", "STOPPED", "other"),
        ("emr-serverless", "SUBMITTED", "active"),
        ("emr-serverless", "CANCELLING", "active"),
        ("emr-serverless", "FAILED", "failed"),
        ("emr-serverless", "SUCCESS", "other"),
        ("transfers", "pending", "active"),
        ("transfers", "running", "active"),
        ("transfers", "failed", "failed"),
        ("transfers", "completed", "other"),
        ("athena", "FUTURE_STATE", "other"),
    ],
)
def test_native_status_is_retained_without_inventing_a_cause(
    kind: str, status: str, expected: str
) -> None:
    scope = WatchScope(kind, "dev", "eu-west-1", "" if kind == "transfers" else "chosen")
    entry = OverviewEntry(scope, "run-1", status)
    assert entry.status == status
    assert entry.group == expected
    assert entry.error_summary is None


def test_running_crawler_does_not_present_last_failure_as_current_failure() -> None:
    scope = WatchScope("glue-crawler", "dev", "eu-west-1", "chosen")
    current = OverviewEntry(scope, "chosen", "RUNNING", last_status="FAILED")
    finished = OverviewEntry(scope, "chosen", "READY", last_status="FAILED")
    assert current.group == "active"
    assert current.status == "RUNNING"
    assert current.last_status == "FAILED"
    assert finished.group == "failed"


def test_projection_retains_labelled_native_time_and_explicit_cap() -> None:
    scope = WatchScope("emr-serverless", "dev", "eu-west-1", "app-1")
    when = datetime(2026, 1, 2, tzinfo=UTC)
    entry = OverviewEntry(scope, "run-1", "FAILED", times=(("created", when), ("updated", None)))
    page = OverviewPage((entry,), cap="First page: at most 25 entries; more available")
    assert page.entries[0].times == (("created", when), ("updated", None))
    assert page.cap is not None
    assert not hasattr(entry, "query")
    assert not hasattr(entry, "response")


@pytest.mark.parametrize(
    ("kind", "name", "region", "resource"),
    [
        ("unknown", "dev", "eu-west-1", "chosen"),
        ("athena", "", "eu-west-1", "chosen"),
        ("athena", "dev", "", "chosen"),
        ("athena", "dev", "eu-west-1", ""),
        ("transfers", "dev", "eu-west-1", "unrequested-resource"),
        ("glue-job", "dev", "eu-west-1", "a\x00b"),
        ("glue-crawler", "dev", "eu-west-1", "x" * 1025),
    ],
)
def test_invalid_or_unbounded_watch_scope_is_refused(
    kind: str, name: str, region: str, resource: str
) -> None:
    with pytest.raises(ValueError, match="Invalid watched scope"):
        WatchScope(kind, name, region, resource)


def test_watch_identity_is_exact_and_hashable() -> None:
    a = WatchScope("athena", "dev", "eu-west-1", "workgroup-a")
    assert a == WatchScope("athena", "dev", "eu-west-1", "workgroup-a")
    assert len({a, WatchScope("athena", "dev", "us-east-1", "workgroup-a")}) == 2


def test_error_projection_is_bounded_redacted_and_has_no_terminal_controls() -> None:
    scope = WatchScope("athena", "dev", "eu-west-1", "chosen")
    entry = OverviewEntry(
        scope,
        "run-1",
        "FAILED",
        error_summary="\x1b[31msecret_access_key=supersecret\n" + "x" * 1000,
    )
    assert entry.error_summary is not None
    assert len(entry.error_summary) <= 500
    assert "supersecret" not in entry.error_summary
    assert not any(ord(char) < 32 or 127 <= ord(char) <= 159 for char in entry.error_summary)
