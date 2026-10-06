from __future__ import annotations

import json

import pytest

from aws_tui.vm.athena.result_projection import (
    SortDirection,
    project_row_indices,
    serialize_cell,
    serialize_row,
)


def test_string_sort_null_last_and_loaded_order_reset() -> None:
    rows = (("9007199254740993",), (None,), ("9007199254740992",), ("",), ("10",), ("2",))
    assert project_row_indices(rows, sort_column=0) == (3, 4, 5, 2, 0, 1)
    assert project_row_indices(rows, sort_column=0, sort_direction="descending") == (
        0,
        2,
        5,
        4,
        3,
        1,
    )
    assert project_row_indices(rows) == tuple(range(6))


@pytest.mark.parametrize(
    ("needle", "expected"),
    [("STRASSE", (0,)), ("[x].*", (1,)), ("null", (2, 3)), ("", (0, 1, 2, 3)), ("missing", ())],
)
def test_literal_casefold_filter_across_cells_and_null(
    needle: str, expected: tuple[int, ...]
) -> None:
    rows = (("other", "Straße"), ("[x].*", ""), (None, "other"), ("NULL", "other"))
    assert project_row_indices(rows, filter_text=needle) == expected


@pytest.mark.parametrize("direction", ["ascending", "descending"])
def test_sort_ties_remain_in_loaded_order(direction: SortDirection) -> None:
    rows = (("same", "first"), (None, "first-null"), ("same", "second"), (None, "second-null"))
    assert project_row_indices(rows, sort_column=0, sort_direction=direction) == (0, 2, 1, 3)


def test_duplicate_column_labels_are_sorted_by_ordinal() -> None:
    rows = (("a", "z"), ("z", "a"))
    assert project_row_indices(rows, sort_column=0) == (0, 1)
    assert project_row_indices(rows, sort_column=1) == (1, 0)


def test_projection_preserves_original_bytes_and_values() -> None:
    rows = ((" B\nß ", None), ("a", "[red]literal[/red]"), ("b", ""))
    before = json.dumps(rows, ensure_ascii=False).encode("utf-8")
    assert project_row_indices(rows, filter_text="b", sort_column=0) == (0, 2)
    assert json.dumps(rows, ensure_ascii=False).encode("utf-8") == before
    assert rows == ((" B\nß ", None), ("a", "[red]literal[/red]"), ("b", ""))


def test_serialize_original_null_empty_and_literal_null() -> None:
    assert serialize_cell(None) == "null"
    assert serialize_cell("") == '""'
    assert serialize_row((None, "", "NULL")) == '[null,"","NULL"]'


def test_serialize_multiline_unicode_and_markup_without_transforming_values() -> None:
    row = ("雪\n[bold]é[/bold]", 'quote"\\', None)
    assert serialize_cell(row[0]) == '"雪\\n[bold]é[/bold]"'
    assert serialize_row(row) == '["雪\\n[bold]é[/bold]","quote\\"\\\\",null]'
    assert tuple(json.loads(serialize_row(row))) == row
