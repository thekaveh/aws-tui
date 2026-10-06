"""Loaded-only result ordering and serialization of original values."""

from __future__ import annotations

import json
from typing import Literal

ResultRow = tuple[str | None, ...]
SortDirection = Literal["ascending", "descending"]


def project_row_indices(
    rows: tuple[ResultRow, ...],
    filter_text: str = "",
    sort_column: int | None = None,
    sort_direction: SortDirection = "ascending",
) -> tuple[int, ...]:
    needle = filter_text.casefold()
    indices = [
        i
        for i, row in enumerate(rows)
        if not needle or any(needle in ("null" if v is None else v.casefold()) for v in row)
    ]
    if sort_column is not None:
        column = sort_column
        values = [i for i in indices if rows[i][column] is not None]
        nulls = [i for i in indices if rows[i][column] is None]

        def non_null_value(index: int) -> str:
            value = rows[index][column]
            assert value is not None
            return value

        values.sort(key=non_null_value, reverse=sort_direction == "descending")
        indices = values + nulls
    return tuple(indices)


def serialize_cell(value: str | None) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def serialize_row(row: ResultRow) -> str:
    return json.dumps(row, ensure_ascii=False, separators=(",", ":"))
