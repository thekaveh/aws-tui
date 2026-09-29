"""Unit tests for the Iceberg preview statement generator."""

from __future__ import annotations

import pytest

from aws_tui.domain.iceberg_preview import (
    ROW_LIMIT_STEPS,
    iceberg_preview_sql,
    next_row_limit,
)


def test_generates_a_scan_bounded_by_the_limit() -> None:
    sql = iceberg_preview_sql("s3://bucket/warehouse/db/tbl", limit=100)

    assert sql == ("SELECT * FROM iceberg_scan('s3://bucket/warehouse/db/tbl') LIMIT 100")


def test_pins_the_snapshot_when_one_is_selected() -> None:
    sql = iceberg_preview_sql("s3://bkt/t", limit=100, snapshot_id=4201)

    assert sql == ("SELECT * FROM iceberg_scan('s3://bkt/t', snapshot_from_id := 4201) LIMIT 100")


def test_escapes_a_single_quote_in_the_location() -> None:
    sql = iceberg_preview_sql("s3://bkt/it's", limit=100)

    assert "iceberg_scan('s3://bkt/it''s')" in sql


@pytest.mark.parametrize("location", ["", "   ", "https://example.com/t", "/tmp/t"])
def test_rejects_anything_that_is_not_an_s3_uri(location: str) -> None:
    with pytest.raises(ValueError, match="s3:// location"):
        iceberg_preview_sql(location, limit=100)


@pytest.mark.parametrize("limit", [0, -1, 7, 999])
def test_rejects_a_limit_that_is_not_a_declared_step(limit: int) -> None:
    with pytest.raises(ValueError, match="row limit"):
        iceberg_preview_sql("s3://bkt/t", limit=limit)


@pytest.mark.parametrize("limit", [100.0, True, "100"])
def test_rejects_a_limit_that_is_not_an_int(limit: object) -> None:
    """`100.0 == 100`, so a membership test alone renders `LIMIT 100.0`.

    `snapshot_id` already guards bool-before-int; `limit` reaches the SQL text
    the same way and needs the same guard.
    """
    with pytest.raises(ValueError, match="row limit"):
        iceberg_preview_sql("s3://bkt/t", limit=limit)  # type: ignore[arg-type]


@pytest.mark.parametrize("snapshot_id", [-1, True])
def test_rejects_an_invalid_snapshot_id(snapshot_id: object) -> None:
    # `True` is an int subclass and must not pass as a snapshot id, matching
    # select_starter_sql at src/aws_tui/domain/sql_policy.py:561.
    with pytest.raises(ValueError, match="snapshot ID"):
        iceberg_preview_sql("s3://bkt/t", limit=100, snapshot_id=snapshot_id)  # type: ignore[arg-type]


def test_next_row_limit_walks_the_steps_then_stops() -> None:
    assert next_row_limit(100) == 1000
    assert next_row_limit(1000) == 10000
    assert next_row_limit(10000) is None


def test_row_limit_steps_are_ascending_and_capped_at_ten_thousand() -> None:
    assert ROW_LIMIT_STEPS == (100, 1000, 10000)
    assert list(ROW_LIMIT_STEPS) == sorted(ROW_LIMIT_STEPS)


@pytest.mark.parametrize(
    "location",
    [
        "s3://",
        "s3://  ",
        "s3://bucket with spaces/x",
        "s3://../evil/x",
        "s3://192.168.0.1/x",
        "s3://UPPER/x",
        "s3://bucket/x?y=1",
        "s3://bucket/x#frag",
        "s3://sthree-reserved/x",
        "s3://bucket--x-s3/x",
        "https://bucket/x",
    ],
)
def test_rejects_a_location_that_only_looks_like_an_s3_uri(location: str) -> None:
    """A `startswith("s3://")` check accepted all of these.

    The location is quoted into the generated statement, so it must clear the
    project's real validator, not a prefix test.
    """
    with pytest.raises(ValueError, match="valid s3:// location"):
        iceberg_preview_sql(location, limit=ROW_LIMIT_STEPS[0])
