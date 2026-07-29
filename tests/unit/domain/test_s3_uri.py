from __future__ import annotations

import pytest

from aws_tui.domain.s3_uri import S3Uri, parse_s3_uri


@pytest.mark.parametrize(
    ("uri", "expected"),
    [
        ("s3://abc", S3Uri("abc", "")),
        ("s3://abc/root-result.csv", S3Uri("abc", "/root-result.csv")),
        (
            "s3://reports.prod-2026/results/query.csv",
            S3Uri("reports.prod-2026", "/results/query.csv"),
        ),
        ("S3://valid-bucket/path/file.csv", S3Uri("valid-bucket", "/path/file.csv")),
        (
            "s3://valid-bucket/results/price-%E2%82%AC.csv",
            S3Uri("valid-bucket", "/results/price-%E2%82%AC.csv"),
        ),
    ],
)
def test_parse_s3_uri_accepts_valid_general_purpose_bucket_locations(
    uri: str,
    expected: S3Uri,
) -> None:
    assert parse_s3_uri(uri) == expected


@pytest.mark.parametrize(
    "uri",
    [
        pytest.param("s3://[2001:db8::1]/result.csv", id="ipv6-literal"),
        pytest.param("s3://valid-bucket:443/result.csv", id="port"),
        pytest.param("s3://user@valid-bucket/result.csv", id="userinfo"),
        pytest.param("s3://user:password@valid-bucket/result.csv", id="password"),
        pytest.param("s3://valid-bucket/result\x00.csv", id="raw-control"),
        pytest.param("s3://valid bucket/result.csv", id="raw-whitespace"),
        pytest.param("s3://valid-bucket/result\n.csv", id="raw-newline"),
        pytest.param("s3://valid-bucket/result%00.csv", id="encoded-control"),
        pytest.param("s3://valid-bucket/result%20.csv", id="encoded-whitespace"),
        pytest.param("s3://valid-bucket/result%0A.csv", id="encoded-newline"),
        pytest.param("s3://valid-bucket/result%2.csv", id="bad-percent-escape"),
        pytest.param("s3://valid-bucket/result.csv?token=secret", id="query"),
        pytest.param("s3://valid-bucket/result.csv#secret", id="fragment"),
        pytest.param("s3:///result.csv", id="empty-bucket"),
        pytest.param("https://valid-bucket/result.csv", id="wrong-scheme"),
    ],
)
def test_parse_s3_uri_rejects_hostile_authorities_and_components(uri: str) -> None:
    assert parse_s3_uri(uri) is None


@pytest.mark.parametrize(
    "bucket",
    [
        pytest.param("ab", id="too-short"),
        pytest.param("a" * 64, id="too-long"),
        pytest.param("Invalid-Bucket", id="uppercase"),
        pytest.param("invalid_bucket", id="underscore"),
        pytest.param("-invalid", id="leading-hyphen"),
        pytest.param("invalid-", id="trailing-hyphen"),
        pytest.param(".invalid", id="leading-period"),
        pytest.param("invalid.", id="trailing-period"),
        pytest.param("invalid..bucket", id="adjacent-periods"),
        pytest.param("192.168.5.4", id="ipv4-address"),
        pytest.param("999.999.999.999", id="ipv4-shaped"),
        pytest.param("xn--reserved", id="reserved-xn-prefix"),
        pytest.param("sthree-reserved", id="reserved-sthree-prefix"),
        pytest.param("amzn-s3-demo-reserved", id="reserved-demo-prefix"),
        pytest.param("reserved-s3alias", id="reserved-alias-suffix"),
        pytest.param("reserved--ol-s3", id="reserved-object-lambda-suffix"),
        pytest.param("reserved.mrap", id="reserved-mrap-suffix"),
        pytest.param("reserved--x-s3", id="reserved-directory-suffix"),
        pytest.param("reserved--table-s3", id="reserved-table-suffix"),
    ],
)
def test_parse_s3_uri_rejects_non_general_purpose_bucket_names(bucket: str) -> None:
    assert parse_s3_uri(f"s3://{bucket}/result.csv") is None


def test_s3_uri_repr_redacts_bucket_and_path() -> None:
    location = parse_s3_uri("s3://secret-bucket/secret/path.csv")

    assert location is not None
    rendered = repr(location)
    assert "secret-bucket" not in rendered
    assert "secret/path.csv" not in rendered
