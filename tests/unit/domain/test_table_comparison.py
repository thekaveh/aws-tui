"""Behavioral contract for read-only Glue definition comparison."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta, timezone
from typing import cast

import pytest

from aws_tui.domain.data_catalog import (
    Column,
    StorageDescriptor,
    TableDetail,
    TableFormat,
    TableRef,
    TableSummary,
)
from aws_tui.domain.table_comparison import (
    ABSENT,
    ChangeKind,
    ColumnValue,
    ParameterPresence,
    TableSnapshot,
    compare_tables,
    export_comparison,
    format_value,
    normalize_type,
)


def detail(
    columns: tuple[Column, ...] = (),
    *,
    partitions: tuple[Column, ...] = (),
    parameters: tuple[tuple[str, str], ...] = (),
) -> TableDetail:
    return TableDetail(
        summary=TableSummary(
            TableRef("AwsDataCatalog", "analytics", "events", "development", "us-east-1"),
            None,
            None,
            "EXTERNAL_TABLE",
            None,
            None,
        ),
        columns=columns,
        partition_keys=partitions,
        storage=StorageDescriptor("s3://lake/events/", "Input", "Output", "Serde", False, 0),
        classification=None,
        table_format=TableFormat.HIVE,
        parameters=parameters,
    )


def col(name: str, type_name: str = "int", comment: str | None = None) -> Column:
    return Column(name, type_name, comment, False)


def test_column_changes_are_independent_and_keep_direction_positions_and_order() -> None:
    left = detail((col("removed"), col("a", "int", None), col("b")))
    right = detail((col("b"), col("a", "bigint", ""), col("added")))
    rows = compare_tables(left, right).rows[:4]
    assert [row.key for row in rows] == ["removed", "a", "b", "added"]
    assert [row.changes for row in rows] == [
        (ChangeKind.REMOVED,),
        (ChangeKind.TYPE_CHANGED, ChangeKind.COMMENT_CHANGED, ChangeKind.REORDERED),
        (ChangeKind.REORDERED,),
        (ChangeKind.ADDED,),
    ]
    assert rows[0].right is ABSENT
    assert rows[3].left is ABSENT
    assert rows[1].left == ColumnValue(left.columns[1], 2)
    assert rows[1].right == ColumnValue(right.columns[1], 2)
    assert rows[2].right == ColumnValue(right.columns[0], 1)


def test_pure_reorder_is_observable_without_type_or_comment_changes() -> None:
    rows = compare_tables(detail((col("a"), col("b"))), detail((col("b"), col("a")))).rows
    assert [row.changes for row in rows[:2]] == [(ChangeKind.REORDERED,)] * 2


def test_inserting_a_column_does_not_reorder_common_columns() -> None:
    rows = compare_tables(
        detail((col("a"), col("b"))), detail((col("new"), col("a"), col("b")))
    ).rows[:3]
    assert [row.key for row in rows] == ["a", "b", "new"]
    assert [row.changes for row in rows] == [
        (ChangeKind.UNCHANGED,),
        (ChangeKind.UNCHANGED,),
        (ChangeKind.ADDED,),
    ]


def test_partition_changes_are_separate_from_same_named_ordinary_columns() -> None:
    left = detail((col("a"),), partitions=(col("a"), col("b"), col("gone")))
    right = detail((col("a"),), partitions=(col("b"), col("a"), col("new")))
    rows = compare_tables(left, right).rows[:5]
    assert rows[0].section == "columns"
    assert rows[0].changes == (ChangeKind.UNCHANGED,)
    assert [row.section for row in rows[1:]] == ["partition_keys"] * 4
    assert [row.changes for row in rows[1:]] == [
        (ChangeKind.REORDERED,),
        (ChangeKind.REORDERED,),
        (ChangeKind.REMOVED,),
        (ChangeKind.ADDED,),
    ]


@pytest.mark.parametrize("section", ["columns", "partition_keys"])
def test_duplicate_section_preserves_each_positional_value_as_unavailable(section: str) -> None:
    left = replace(detail(), **{section: (col("dup"), col("dup", "string"))})
    right = replace(detail(), **{section: (col("dup", "bigint"),)})
    rows = [row for row in compare_tables(left, right).rows if row.section == section]
    assert len(rows) == 3
    assert [row.changes for row in rows] == [(ChangeKind.UNAVAILABLE,)] * 3
    assert [row.left for row in rows] == [
        ColumnValue(col("dup"), 1),
        ColumnValue(col("dup", "string"), 2),
        ABSENT,
    ]
    assert [row.right for row in rows] == [ABSENT, ABSENT, ColumnValue(col("dup", "bigint"), 1)]
    assert len({row.key for row in rows}) == 3


def test_rename_and_case_changes_are_add_remove_without_guessing() -> None:
    rows = compare_tables(detail((col("old"), col("ID"))), detail((col("new"), col("id")))).rows
    assert [row.changes for row in rows[:4]] == [
        (ChangeKind.REMOVED,),
        (ChangeKind.REMOVED,),
        (ChangeKind.ADDED,),
        (ChangeKind.ADDED,),
    ]


@pytest.mark.parametrize(
    ("source", "normalized"),
    [
        ("array < struct < id : int > >", "array<struct<id:int>>"),
        (" \tmap < string , decimal ( 10 , 2 ) >\r\n", "map<string,decimal(10,2)>"),
        ("struct<`a b`:array<int>>", "struct<`a b`:array<int>>"),
        ("struct < 'a , : b' : int >", "struct<'a , : b':int>"),
        ('struct < "a , : b" : int >', 'struct<"a , : b":int>'),
        ("struct < `a`` , : b` : int >", "struct<`a`` , : b`:int>"),
        (r"struct < 'a\' , : b' : int >", r"struct<'a\' , : b':int>"),
        ('struct < "a"" , : b" : int >', 'struct<"a"" , : b":int>'),
        (r"struct < `a\` , : b` : int >", r"struct<`a\` , : b`:int>"),
        ("timestamp  with  time zone", "timestamp  with  time zone"),
        ("array<struct<id:int>", "array<struct<id:int>"),
        (" array<int) ", " array<int) "),
        ("struct < `unclosed : int >", "struct < `unclosed : int >"),
        ("array<int>>", "array<int>>"),
        ("<array(int>)", "<array(int>)"),
        ("struct<a:int\\>", "struct<a:int\\>"),
    ],
)
def test_nested_normalization_preserves_tokens_and_rejects_malformed_structure(
    source: str, normalized: str
) -> None:
    assert normalize_type(source) == normalized


@pytest.mark.parametrize(
    ("left", "right"),
    [
        ("struct<ID:int>", "struct<id:int>"),
        ("ARRAY<int>", "array<int>"),
        ("struct<`a b`:int>", "struct<`a  b`:int>"),
        ("struct<`a`:int>", "struct<a:int>"),
        ('struct<"a":int>', "struct<'a':int>"),
        ("int", "integer"),
    ],
)
def test_type_case_quoting_and_aliases_are_not_normalized(left: str, right: str) -> None:
    assert normalize_type(left) != normalize_type(right)


def test_type_spacing_equality_preserves_raw_metadata() -> None:
    left = detail((col("nested", "array < struct < id : int > >"),))
    right = detail((col("nested", "array<struct<id:int>>"),))
    row = compare_tables(left, right).rows[0]
    assert row.changes == (ChangeKind.UNCHANGED,)
    assert row.left == ColumnValue(left.columns[0], 1)
    assert row.right == ColumnValue(right.columns[0], 1)


@pytest.mark.parametrize(
    "field",
    [
        "location",
        "input_format",
        "output_format",
        "serde",
        "compressed",
        "table_type",
        "table_format",
    ],
)
def test_all_seven_storage_fields_distinguish_literal_missing_from_empty(field: str) -> None:
    left = detail()
    right = detail()
    if field == "compressed":
        # Intentionally malformed boundary fixtures; provider contract stays bool.
        left = replace(left, storage=replace(left.storage, compressed=cast(bool, None)))
        right = replace(right, storage=replace(right.storage, compressed=cast(bool, "")))
    elif field == "table_format":
        # Intentionally malformed boundary fixtures; provider contract stays TableFormat.
        left = replace(left, table_format=cast(TableFormat, None))
        right = replace(right, table_format=cast(TableFormat, ""))
    elif field == "table_type":
        left = replace(left, summary=replace(left.summary, table_type=None))
        right = replace(right, summary=replace(right.summary, table_type=""))
    else:
        left = replace(left, storage=replace(left.storage, **{field: None}))
        right = replace(right, storage=replace(right.storage, **{field: ""}))
    rows = compare_tables(left, right).rows
    assert [row.key for row in rows] == [
        "location",
        "input_format",
        "output_format",
        "serde",
        "compressed",
        "table_type",
        "table_format",
    ]
    changed = [row for row in rows if row.changes == (ChangeKind.VALUE_CHANGED,)]
    assert len(changed) == 1
    assert changed[0].key == field
    assert changed[0].left is None
    assert changed[0].right == ""
    assert format_value(changed[0].left) == "<missing>"
    assert format_value(changed[0].right) == '""'


def test_false_is_a_boolean_value_not_missing_or_empty() -> None:
    left = detail()
    right = replace(left, storage=replace(left.storage, compressed=True))
    row = compare_tables(left, right).rows[4]
    assert row.left is False
    assert row.right is True
    assert row.changes == (ChangeKind.VALUE_CHANGED,)
    assert format_value(row.left) == "false"
    assert format_value(row.right) == "true"


@pytest.mark.parametrize("table_format", list(TableFormat))
def test_table_format_is_preserved_and_distinguished_from_its_string(
    table_format: TableFormat,
) -> None:
    left = replace(detail(), table_format=table_format)
    # StrEnum compares equal to str in Python, but metadata types remain distinct.
    right = replace(left, table_format=cast(TableFormat, table_format.value))
    row = compare_tables(left, right).rows[-1]
    assert row.left is table_format
    assert type(row.right) is str
    assert row.changes == (ChangeKind.VALUE_CHANGED,)
    assert compare_tables(left, left).rows[-1].changes == (ChangeKind.UNCHANGED,)
    assert format_value(table_format) == f"TableFormat.{table_format.name}"


def test_redacted_parameters_compare_only_sorted_presence_and_never_equal() -> None:
    left = detail(parameters=(("z", "SECRET-LEFT"), ("both", "[REDACTED]")))
    right = detail(parameters=(("a", "SECRET-RIGHT"), ("both", "[REDACTED]")))
    comparison = compare_tables(left, right)
    rows = comparison.rows[-3:]
    assert [row.key for row in rows] == ["a", "both", "z"]
    assert [row.changes for row in rows] == [
        (ChangeKind.ADDED,),
        (ChangeKind.UNAVAILABLE,),
        (ChangeKind.REMOVED,),
    ]
    assert rows[1].left == rows[1].right == ParameterPresence(True)
    assert rows[0].left == ParameterPresence(False)
    assert rows[2].right == ParameterPresence(False)
    assert format_value(rows[1].left) == "<unavailable: redacted>"
    assert format_value(rows[0].left) == "<absent>"
    assert comparison.visible_rows(True) == rows
    assert comparison.visible_rows(False) == comparison.rows


def test_differences_filter_keeps_duplicate_ambiguity_and_drops_known_unchanged() -> None:
    comparison = compare_tables(detail((col("x"), col("x"))), detail((col("x"),)))
    assert len(comparison.visible_rows(True)) == 3
    assert all(row.changes == (ChangeKind.UNAVAILABLE,) for row in comparison.visible_rows(True))
    assert compare_tables(detail(), detail()).visible_rows(True) == ()


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, "<missing>"),
        (ABSENT, "<absent>"),
        ("", '""'),
        ("<missing>", '"<missing>"'),
        ("<absent>", '"<absent>"'),
        ('line\n\t"\\', '"line\\n\\t\\"\\\\"'),
    ],
)
def test_value_formatting_distinguishes_markers_and_escapes_controls(value, expected: str) -> None:
    assert format_value(value) == expected


def test_column_value_format_includes_raw_type_comment_and_position() -> None:
    text = format_value(ColumnValue(col("id", "array < int >", "line\ncomment"), 2))
    assert 'name="id"' in text
    assert 'type="array < int >"' in text
    assert 'comment="line\\ncomment"' in text
    assert "position=2" in text


def test_snapshot_rejects_foreign_identity_and_naive_timestamp() -> None:
    table = detail()
    with pytest.raises(ValueError, match="identity"):
        TableSnapshot(replace(table.summary.ref, table_name="other"), table, datetime.now(UTC))
    with pytest.raises(ValueError, match="aware"):
        TableSnapshot(table.summary.ref, table, datetime(2026, 10, 6))


def test_snapshot_normalizes_aware_offset_to_utc() -> None:
    table = detail()
    snapshot = TableSnapshot(
        table.summary.ref, table, datetime(2026, 10, 6, 8, tzinfo=timezone(timedelta(hours=-4)))
    )
    assert snapshot.fetched_at == datetime(2026, 10, 6, 12, tzinfo=UTC)
    assert snapshot.fetched_at.tzinfo is UTC


def test_export_is_deterministic_labeled_complete_and_secret_free() -> None:
    left = detail((col("old"),), parameters=(("token", "LEFT-SECRET"),))
    right_ref = TableRef("AwsDataCatalog", "warehouse", "renamed", "production", "eu-west-1")
    right = replace(
        detail((col("new"),), parameters=(("token", "RIGHT-SECRET"),)),
        summary=replace(left.summary, ref=right_ref),
    )
    snapshots = (
        TableSnapshot(left.summary.ref, left, datetime(2026, 10, 6, 12, tzinfo=UTC)),
        TableSnapshot(right.summary.ref, right, datetime(2026, 10, 6, 13, tzinfo=UTC)),
    )
    comparison = compare_tables(left, right)
    exported = export_comparison(*snapshots, comparison)
    comparison.visible_rows(True)
    assert export_comparison(*snapshots, comparison) == exported
    for label, snapshot in zip(("Left", "Right"), snapshots, strict=True):
        assert f"{label}:" in exported
        for value in (
            snapshot.ref.catalog_name,
            snapshot.ref.database_name,
            snapshot.ref.table_name,
            snapshot.ref.connection_name,
            snapshot.ref.region,
        ):
            assert f'"{value}"' in exported
        assert snapshot.fetched_at.isoformat() in exported
    assert "Direction: Left -> Right" in exported
    assert "Normalization:" in exported
    assert "Parameter values: unavailable (redacted)" in exported
    assert "[storage]" in exported
    assert "[parameters]" in exported
    assert exported.index('"old"') < exported.index('"new"') < exported.index('"location"')
    assert "removed" in exported
    assert "added" in exported
    assert "unavailable" in exported
    assert "SECRET" not in exported
    assert all(word not in exported.lower() for word in ("compatible", "breaking", "migration"))


def test_unexpected_unredacted_parameter_values_are_not_inspected_or_exported() -> None:
    class UnreadableValue(str):
        def __eq__(self, _other: object) -> bool:
            raise AssertionError("parameter value equality is forbidden")

        def __str__(self) -> str:
            raise AssertionError("parameter value formatting is forbidden")

    left = detail()
    right = detail()
    # Deliberately bypass upstream redaction to verify the comparator boundary.
    object.__setattr__(left, "parameters", (("token", UnreadableValue("LEFT-SECRET")),))
    object.__setattr__(right, "parameters", (("token", UnreadableValue("RIGHT-SECRET")),))
    comparison = compare_tables(left, right)
    row = comparison.rows[-1]
    assert row.changes == (ChangeKind.UNAVAILABLE,)
    assert row.left == ParameterPresence(True)
    assert row.right == ParameterPresence(True)
    left_snapshot = TableSnapshot(left.summary.ref, left, datetime(2026, 10, 6, tzinfo=UTC))
    right_snapshot = TableSnapshot(right.summary.ref, right, datetime(2026, 10, 6, tzinfo=UTC))
    text = export_comparison(left_snapshot, right_snapshot, comparison)
    assert "SECRET" not in text
    assert "<unavailable: redacted>" in text


@pytest.mark.parametrize("field", ["location", "input_format", "output_format", "serde"])
def test_storage_strings_remain_exact_including_whitespace(field: str) -> None:
    left = detail()
    right = replace(
        left, storage=replace(left.storage, **{field: " " + getattr(left.storage, field)})
    )
    row = next(row for row in compare_tables(left, right).rows if row.key == field)
    assert row.changes == (ChangeKind.VALUE_CHANGED,)
    assert row.right == " " + getattr(left.storage, field)


@pytest.mark.parametrize("storage_fields", [{}, {"Compressed": "not-a-bool"}])
async def test_provider_default_false_provenance_is_preserved(
    storage_fields: dict[str, object],
) -> None:
    from aws_tui.domain.glue import GlueClient
    from aws_tui.infra.aws_session import AwsSession
    from aws_tui.infra.connection_resolver import Connection
    from tests.unit.domain._fake_aws_client import FakeAwsClient, FakeAwsSession

    glue = FakeAwsClient()
    glue.get_table.return_value = {"Table": {"Name": "events", "StorageDescriptor": storage_fields}}
    client = GlueClient(
        aws_session=cast(AwsSession, FakeAwsSession({"glue": glue})),
        connection=Connection("development", "aws", "us-east-1", "config"),
    )
    ref = detail().summary.ref
    fetched = await client.get_table(ref)
    row = next(row for row in compare_tables(fetched, fetched).rows if row.key == "compressed")
    assert fetched.storage.compressed is False
    assert row.left is False
    assert row.right is False
    assert row.changes == (ChangeKind.UNCHANGED,)
    assert format_value(row.left) == "false"
    glue.get_table.assert_awaited_once_with(DatabaseName="analytics", Name="events")
