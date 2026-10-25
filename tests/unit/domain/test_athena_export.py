from __future__ import annotations

import csv
import io
import json
import tracemalloc
from dataclasses import FrozenInstanceError
from typing import Any

import pytest

from aws_tui.domain.athena_export import (
    ExportDataError,
    ExportLimitError,
    ExportLimits,
    ResultEncoder,
    encode_result_rows,
)
from aws_tui.domain.query import ResultColumn

COLUMNS = (
    ResultColumn("value", "varchar", "NULLABLE"),
    ResultColumn("value", "varchar", "UNKNOWN"),
)
FORMATS = ("csv", "json")


def encoded(columns: Any, rows: Any, *, format: Any, **limits: Any) -> bytes:
    return b"".join(encode_result_rows(columns, rows, format=format, limits=ExportLimits(**limits)))


def test_csv_round_trips_positional_values_and_literal_headers() -> None:
    columns = (
        ResultColumn(r"\N", "varchar", "NULLABLE"),
        ResultColumn(r"\N", "varchar", "NULLABLE"),
        ResultColumn('unicode 雪,"\n', "varchar", "UNKNOWN"),
    )
    rows = ((None, "", r"\N"), (r"a\b\\c", 'x,"y"\nz\r', "雪🙂"))
    payload = encoded(columns, rows, format="csv", chunk_bytes=3)
    assert list(csv.reader(io.StringIO(payload.decode("utf-8"), newline=""))) == [
        [column.name for column in columns],
        [r"\N", "", r"\\N"],
        [r"a\\b\\\\c", 'x,"y"\nz\r', "雪🙂"],
    ]
    assert payload.endswith(b"\n")
    assert b"\r\n" not in payload


def test_json_preserves_metadata_duplicates_nulls_and_strings() -> None:
    columns = (COLUMNS[0], ResultColumn("value", "bigint", "NOT_NULL"))
    rows = ((None, ""), ("雪🙂", '123\\"\n'))
    payload = encoded(columns, rows, format="json", chunk_bytes=1)
    assert json.loads(payload) == {
        "columns": [
            {"name": "value", "type": "varchar", "nullable": "NULLABLE"},
            {"name": "value", "type": "bigint", "nullable": "NOT_NULL"},
        ],
        "rows": [[None, ""], ["雪🙂", '123\\"\n']],
    }
    assert "雪🙂".encode() in payload


@pytest.mark.parametrize("format", FORMATS)
def test_empty_data_zero_columns_and_empty_single_field(format: Any) -> None:
    payload = encoded((), ((), ()), format=format)
    if format == "csv":
        assert list(csv.reader(io.StringIO(payload.decode()))) == [[], [], []]
        assert encoded((ResultColumn("", "", ""),), (("",),), format=format) == (b'""\n""\n')
    else:
        assert json.loads(payload) == {"columns": [], "rows": [[], []]}
    empty = encoded(COLUMNS, (), format=format)
    assert empty
    if format == "json":
        assert json.loads(empty)["rows"] == []


@pytest.mark.parametrize("format", FORMATS)
def test_counts_utf8_bytes_rows_and_caps_each_chunk(format: Any) -> None:
    encoder = ResultEncoder(COLUMNS, format=format, limits=ExportLimits(chunk_bytes=7))
    chunks = list(encoder.start())
    assert encoder.rows_written == 0
    chunks.extend(encoder.write_row(("雪🙂", None)))
    assert encoder.rows_written == 1
    chunks.extend(encoder.write_row(("", "second")))
    chunks.extend(encoder.finish())
    assert all(0 < len(chunk) <= 7 for chunk in chunks)
    assert encoder.bytes_written == len(b"".join(chunks))
    assert encoder.rows_written == 2
    assert encoder.bytes_written > len(b"".join(chunks).decode())


@pytest.mark.parametrize("format", FORMATS)
def test_byte_limit_includes_all_framing_and_multibyte_content(format: Any) -> None:
    rows = (("雪🙂", None),)
    expected = encoded(COLUMNS, rows, format=format)
    assert encoded(COLUMNS, rows, format=format, max_bytes=len(expected)) == expected
    for cap in (1, len(expected) - 5, len(expected) - 1):
        encoder = ResultEncoder(
            COLUMNS, format=format, limits=ExportLimits(max_bytes=cap, chunk_bytes=3)
        )

        def consume(current: ResultEncoder) -> None:
            list(current.start())
            list(current.write_row(rows[0]))
            list(current.finish())

        with pytest.raises(ExportLimitError, match=r"^Athena export limit exceeded$"):
            consume(encoder)
        assert encoder.bytes_written <= cap


@pytest.mark.parametrize("format", FORMATS)
def test_row_limit_allows_empty_data_and_rejects_next_row(format: Any) -> None:
    assert encoded(COLUMNS, (), format=format, max_rows=0)
    encoder = ResultEncoder(COLUMNS, format=format, limits=ExportLimits(max_rows=1))
    list(encoder.start())
    list(encoder.write_row((None, "")))
    with pytest.raises(ExportLimitError, match=r"^Athena export limit exceeded$"):
        list(encoder.write_row(("secret", "secret")))
    assert encoder.rows_written == 1


@pytest.mark.parametrize(
    "columns",
    [
        [],
        (object(),),
        (ResultColumn(7, "varchar", "UNKNOWN"),),
        (ResultColumn("secret", None, "UNKNOWN"),),
        (ResultColumn("secret", "varchar", False),),
    ],
)
def test_rejects_malformed_columns_with_value_free_errors(columns: Any) -> None:
    with pytest.raises(ExportDataError, match=r"^Athena export data is invalid$"):
        list(encode_result_rows(columns, (), format="csv"))


@pytest.mark.parametrize("format", FORMATS)
@pytest.mark.parametrize(
    "row",
    [
        ["secret", "secret"],
        (),
        ("secret",),
        ("secret", "secret", "secret"),
        (1, None),
        (None, False),
        (None, object()),
    ],
)
def test_rejects_malformed_rows_with_value_free_errors(format: Any, row: Any) -> None:
    with pytest.raises(ExportDataError, match=r"^Athena export data is invalid$"):
        encoded(COLUMNS, (row,), format=format)


@pytest.mark.parametrize("format", FORMATS)
def test_rejects_unencodable_unicode_safely(format: Any) -> None:
    with pytest.raises(ExportDataError, match=r"^Athena export data is invalid$"):
        encoded(COLUMNS, (("private\ud800", ""),), format=format)
    with pytest.raises(ExportDataError, match=r"^Athena export data is invalid$"):
        encoded((ResultColumn("private\ud800", "varchar", "UNKNOWN"),), (), format=format)


def test_rejects_unknown_format_and_invalid_limits_argument() -> None:
    for kwargs in ({"format": "private"}, {"format": "csv", "limits": None}):
        with pytest.raises(ExportDataError, match=r"^Athena export data is invalid$"):
            ResultEncoder(COLUMNS, **kwargs)  # type: ignore[arg-type]


@pytest.mark.parametrize("operation", ["write_row", "finish"])
def test_rejects_operations_before_start(operation: str) -> None:
    encoder = ResultEncoder(COLUMNS, format="csv")
    with pytest.raises(ExportDataError):
        list(getattr(encoder, operation)(*((None, None),) if operation == "write_row" else ()))


def test_rejects_overlapping_iterators_and_operations_after_finish() -> None:
    encoder = ResultEncoder(COLUMNS, format="csv", limits=ExportLimits(chunk_bytes=1))
    active = encoder.start()
    next(active)
    with pytest.raises(ExportDataError):
        list(encoder.write_row((None, None)))
    active.close()  # type: ignore[attr-defined]
    with pytest.raises(ExportDataError):
        list(encoder.finish())
    encoder = ResultEncoder(COLUMNS, format="csv")
    list(encoder.start())
    with pytest.raises(ExportDataError):
        list(encoder.start())
    encoder = ResultEncoder(COLUMNS, format="csv")
    list(encoder.start())
    list(encoder.finish())
    for method in (encoder.start, encoder.finish, lambda: encoder.write_row((None, None))):
        with pytest.raises(ExportDataError):
            list(method())


@pytest.mark.parametrize("name", ["max_bytes", "max_rows", "max_requests", "chunk_bytes"])
@pytest.mark.parametrize("value", [True, 1.5, "secret", None, -1])
def test_limits_reject_nonintegers_and_negative_values(name: str, value: Any) -> None:
    with pytest.raises(ValueError, match=r"^Invalid export limits$"):
        ExportLimits(**{name: value})


@pytest.mark.parametrize("name", ["max_bytes", "max_requests", "chunk_bytes"])
def test_limits_reject_zero_except_row_count(name: str) -> None:
    with pytest.raises(ValueError, match=r"^Invalid export limits$"):
        ExportLimits(**{name: 0})


@pytest.mark.parametrize(
    "value", [True, 0, -1, float("nan"), float("inf"), 10**1000, "private", None]
)
def test_limits_reject_invalid_timeout(value: Any) -> None:
    with pytest.raises(ValueError, match=r"^Invalid export limits$"):
        ExportLimits(timeout_seconds=value)


def test_limits_are_frozen_and_have_named_defaults() -> None:
    limits = ExportLimits()
    assert (
        limits.max_bytes,
        limits.max_rows,
        limits.max_requests,
        limits.timeout_seconds,
        limits.chunk_bytes,
    ) == (
        256 * 1024 * 1024,
        1_000_000,
        10_000,
        300.0,
        64 * 1024,
    )
    assert ExportLimits(timeout_seconds=1).timeout_seconds == 1
    with pytest.raises(FrozenInstanceError):
        limits.max_rows = 2  # type: ignore[misc]


@pytest.mark.parametrize("format", FORMATS)
def test_large_generated_export_retains_bounded_memory(format: Any) -> None:
    count = 20_005
    consumed = 0

    def rows() -> Any:
        nonlocal consumed
        for index in range(count):
            consumed += 1
            yield (str(index), "雪" * 100)

    tracemalloc.start()
    try:
        stream = encode_result_rows(COLUMNS, rows(), format=format)
        next(stream)
        assert consumed == 0
        output_bytes = sum(len(chunk) for chunk in stream)
        _current, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert consumed == count
    assert output_bytes > 6_000_000
    assert peak < 1_000_000


@pytest.mark.parametrize("format", FORMATS)
def test_single_large_escaped_cell_has_bounded_encoding_overhead(format: Any) -> None:
    row = ('雪\\"\n' * 500_000,)
    columns = (ResultColumn("value", "varchar", "NULLABLE"),)
    tracemalloc.start()
    try:
        total = 0
        for chunk in encode_result_rows(columns, (row,), format=format):
            assert len(chunk) <= 64 * 1024
            total += len(chunk)
        _current, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert total > 4_000_000
    assert peak < 1_000_000
