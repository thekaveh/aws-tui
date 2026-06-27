from __future__ import annotations

import pytest

from aws_tui.domain.emr_logs import S3LogLocation, build_run_prefix, parse_log_uri


def test_parse_log_uri_extracts_bucket_and_prefix() -> None:
    """``s3MonitoringConfiguration.logUri`` is a string like
    ``s3://my-bucket/emr-logs/`` (with or without trailing slash).
    We split it into bucket + key prefix; the prefix has any
    trailing slash stripped so callers can join with confidence."""
    loc = parse_log_uri("s3://my-bucket/emr-logs/")
    assert loc == S3LogLocation(bucket="my-bucket", prefix="emr-logs")
    loc = parse_log_uri("s3://my-bucket/emr-logs")
    assert loc == S3LogLocation(bucket="my-bucket", prefix="emr-logs")


def test_parse_log_uri_bucket_only_has_empty_prefix() -> None:
    loc = parse_log_uri("s3://my-bucket/")
    assert loc == S3LogLocation(bucket="my-bucket", prefix="")


def test_parse_log_uri_rejects_non_s3_scheme() -> None:
    with pytest.raises(ValueError, match="not an s3:// URI"):
        parse_log_uri("https://my-bucket/path")


@pytest.mark.parametrize(
    ("loc", "expected"),
    [
        (S3LogLocation(bucket="b", prefix=""), "applications/00abc/jobs/r-001"),
        (S3LogLocation(bucket="b", prefix="logs"), "logs/applications/00abc/jobs/r-001"),
        (S3LogLocation(bucket="b", prefix="a/b"), "a/b/applications/00abc/jobs/r-001"),
    ],
)
def test_build_run_prefix(loc: S3LogLocation, expected: str) -> None:
    assert build_run_prefix(loc, "00abc", "r-001") == expected
