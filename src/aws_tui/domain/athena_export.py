"""Bounded, positional serialization of Athena result rows.

Normalized CSV represents NULL as ``\\N`` and doubles every backslash in
non-null data. Header names are literal. JSON retains ordered column metadata
and positional row arrays without interpreting string values as scalars.
"""

from __future__ import annotations

import json
import math
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from typing import Literal

from aws_tui.domain.query import ResultColumn


class ExportLimitError(ValueError):
    """An independent export budget was exhausted."""

    def __init__(self) -> None:
        super().__init__("Athena export limit exceeded")


class ExportDataError(ValueError):
    """The result shape or encoder operation is invalid."""

    def __init__(self) -> None:
        super().__init__("Athena export data is invalid")


@dataclass(frozen=True, slots=True)
class ExportLimits:
    """Named export budgets; request/time bounds are enforced by providers."""

    max_bytes: int = 256 * 1024 * 1024
    max_rows: int = 1_000_000
    max_requests: int = 10_000
    timeout_seconds: float = 300.0
    chunk_bytes: int = 64 * 1024

    def __post_init__(self) -> None:
        for value, minimum in (
            (self.max_bytes, 1),
            (self.max_rows, 0),
            (self.max_requests, 1),
            (self.chunk_bytes, 1),
        ):
            if type(value) is not int or value < minimum:
                raise ValueError("Invalid export limits")
        if type(self.timeout_seconds) not in (int, float) or self.timeout_seconds <= 0:
            raise ValueError("Invalid export limits")
        try:
            finite = math.isfinite(self.timeout_seconds)
        except OverflowError:
            raise ValueError("Invalid export limits") from None
        if not finite:
            raise ValueError("Invalid export limits")


_DEFAULT_LIMITS = ExportLimits()


class ResultEncoder:
    """Serialize one result, consuming each operation iterator before the next.

    Each iterator must be exhausted. A partial/failed operation invalidates the
    encoder; callers must discard their staged prefix after any failure.
    """

    _columns: tuple[ResultColumn, ...]
    _format: Literal["csv", "json"]
    _limits: ExportLimits
    _state: str
    _rows_written: int
    _bytes_written: int
    _text_chunk_size: int

    def __init__(
        self,
        columns: tuple[ResultColumn, ...],
        *,
        format: Literal["csv", "json"],
        limits: ExportLimits = _DEFAULT_LIMITS,
    ) -> None:
        if (
            type(columns) is not tuple
            or not all(
                type(column) is ResultColumn
                and type(column.name) is str
                and type(column.type_name) is str
                and type(column.nullable) is str
                for column in columns
            )
            or type(format) is not str
            or format not in ("csv", "json")
            or type(limits) is not ExportLimits
        ):
            raise ExportDataError()
        self._columns = columns
        self._format = format
        self._limits = limits
        self._state = "new"
        self._rows_written = 0
        self._bytes_written = 0
        # Escaping/UTF-8 encoding operates on bounded pieces even for huge cells.
        self._text_chunk_size = min(16_384, max(1, limits.chunk_bytes // 4))

    @property
    def rows_written(self) -> int:
        return self._rows_written

    @property
    def bytes_written(self) -> int:
        return self._bytes_written

    def start(self) -> Iterator[bytes]:
        self._require_state("new")
        fragments = (
            self._csv_record(tuple(column.name for column in self._columns), header=True)
            if self._format == "csv"
            else self._json_start()
        )
        self._state = "streaming"
        return self._stream(fragments, next_state="ready")

    def write_row(self, row: tuple[str | None, ...]) -> Iterator[bytes]:
        self._require_state("ready")
        if (
            type(row) is not tuple
            or len(row) != len(self._columns)
            or not all(value is None or type(value) is str for value in row)
        ):
            self._state = "failed"
            raise ExportDataError()
        if self.rows_written >= self._limits.max_rows:
            self._state = "failed"
            raise ExportLimitError()
        fragments = self._csv_record(row) if self._format == "csv" else self._json_row(row)
        self._state = "streaming"
        return self._stream(fragments, next_state="ready", completes_row=True)

    def finish(self) -> Iterator[bytes]:
        self._require_state("ready")
        self._state = "streaming"
        return self._stream(
            iter(("]}",)) if self._format == "json" else iter(()),
            next_state="finished",
        )

    def _require_state(self, state: str) -> None:
        if self._state != state:
            raise ExportDataError()

    def _stream(
        self,
        fragments: Iterable[str],
        *,
        next_state: str,
        completes_row: bool = False,
    ) -> Iterator[bytes]:
        complete = False
        buffer = bytearray()
        try:
            for fragment in fragments:
                for start in range(0, len(fragment), self._text_chunk_size):
                    try:
                        encoded = fragment[start : start + self._text_chunk_size].encode("utf-8")
                    except UnicodeEncodeError:
                        raise ExportDataError() from None
                    offset = 0
                    while offset < len(encoded):
                        size = min(
                            len(encoded) - offset,
                            self._limits.chunk_bytes - len(buffer),
                        )
                        if self.bytes_written + len(buffer) + size > self._limits.max_bytes:
                            raise ExportLimitError()
                        buffer.extend(encoded[offset : offset + size])
                        offset += size
                        if len(buffer) == self._limits.chunk_bytes:
                            chunk = bytes(buffer)
                            buffer.clear()
                            self._bytes_written += len(chunk)
                            yield chunk
            if buffer:
                chunk = bytes(buffer)
                buffer.clear()
                self._bytes_written += len(chunk)
                yield chunk
            if completes_row:
                self._rows_written += 1
            self._state = next_state
            complete = True
        finally:
            if not complete:
                self._state = "failed"

    def _text_parts(self, value: str) -> Iterator[str]:
        for start in range(0, len(value), self._text_chunk_size):
            yield value[start : start + self._text_chunk_size]

    def _csv_record(self, row: tuple[str | None, ...], *, header: bool = False) -> Iterator[str]:
        for index, value in enumerate(row):
            if index:
                yield ","
            if value is None:
                yield "\\N"
                continue
            quoted = (len(row) == 1 and not value) or any(char in value for char in ',"\r\n')
            if quoted:
                yield '"'
            for part in self._text_parts(value):
                if not header:
                    part = part.replace("\\", "\\\\")
                yield part.replace('"', '""')
            if quoted:
                yield '"'
        yield "\n"

    def _json_string(self, value: str) -> Iterator[str]:
        yield '"'
        for part in self._text_parts(value):
            yield json.dumps(part, ensure_ascii=False)[1:-1]
        yield '"'

    def _json_start(self) -> Iterator[str]:
        yield '{"columns":['
        for index, column in enumerate(self._columns):
            if index:
                yield ","
            yield '{"name":'
            yield from self._json_string(column.name)
            yield ',"type":'
            yield from self._json_string(column.type_name)
            yield ',"nullable":'
            yield from self._json_string(column.nullable)
            yield "}"
        yield '],"rows":['

    def _json_row(self, row: tuple[str | None, ...]) -> Iterator[str]:
        if self.rows_written:
            yield ","
        yield "["
        for index, value in enumerate(row):
            if index:
                yield ","
            if value is None:
                yield "null"
            else:
                yield from self._json_string(value)
        yield "]"


def encode_result_rows(
    columns: tuple[ResultColumn, ...],
    rows: Iterable[tuple[str | None, ...]],
    *,
    format: Literal["csv", "json"],
    limits: ExportLimits = _DEFAULT_LIMITS,
) -> Iterator[bytes]:
    """Encode a lazily consumed result with the same independent budgets."""
    encoder = ResultEncoder(columns, format=format, limits=limits)
    yield from encoder.start()
    for row in rows:
        yield from encoder.write_row(row)
    yield from encoder.finish()
