"""Pure, directional comparison of two Glue table definitions.

Type normalization is deliberately lexical: remove outer ASCII whitespace and
whitespace adjacent to type punctuation outside quoted spans. Preserve case,
quoting, quoted content, and whitespace between other tokens. Malformed quoting
or unbalanced delimiters leave the original text untouched. This is neither a
type-equivalence parser nor a schema compatibility assessment.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import Enum
from typing import Literal, TypeAlias

from aws_tui.domain.data_catalog import Column, TableDetail, TableFormat, TableRef

Side: TypeAlias = Literal["left", "right"]
Scalar: TypeAlias = str | bool | TableFormat | None
Section: TypeAlias = Literal["columns", "partition_keys", "storage", "parameters"]
_ASCII_WHITESPACE = " \t\n\r\v\f"
_PUNCTUATION = "<>(),:"
_SECTIONS: tuple[Section, ...] = ("columns", "partition_keys", "storage", "parameters")
_NORMALIZATION_POLICY = (
    "ASCII whitespace outside quotes is removed at outer edges and beside <>(),:; "
    "case, quoted content and other token spacing are preserved; malformed types stay literal."
)


class ChangeKind(Enum):
    ADDED = "added"
    REMOVED = "removed"
    TYPE_CHANGED = "type_changed"
    COMMENT_CHANGED = "comment_changed"
    REORDERED = "reordered"
    VALUE_CHANGED = "value_changed"
    UNCHANGED = "unchanged"
    UNAVAILABLE = "unavailable"


class AbsentValue(Enum):
    """An item absent from one side, distinct from a present missing scalar."""

    ABSENT = "absent"


ABSENT = AbsentValue.ABSENT


@dataclass(frozen=True, slots=True)
class TableSnapshot:
    ref: TableRef
    detail: TableDetail
    fetched_at: datetime

    def __post_init__(self) -> None:
        if self.ref != self.detail.summary.ref:
            raise ValueError("snapshot identity must match its table detail")
        if self.fetched_at.utcoffset() is None:
            raise ValueError("snapshot fetch timestamp must be timezone-aware")
        object.__setattr__(self, "fetched_at", self.fetched_at.astimezone(UTC))


@dataclass(frozen=True, slots=True)
class ColumnValue:
    column: Column
    position: int


@dataclass(frozen=True, slots=True)
class ParameterPresence:
    present: bool


ComparisonValue: TypeAlias = ColumnValue | ParameterPresence | Scalar | AbsentValue


@dataclass(frozen=True, slots=True)
class ComparisonRow:
    section: Section
    key: str
    changes: tuple[ChangeKind, ...]
    left: ComparisonValue
    right: ComparisonValue


@dataclass(frozen=True, slots=True)
class TableComparison:
    rows: tuple[ComparisonRow, ...]

    def visible_rows(self, differences_only: bool) -> tuple[ComparisonRow, ...]:
        """Unknown/redacted data remains visible even when known equals are hidden."""
        if not differences_only:
            return self.rows
        return tuple(row for row in self.rows if row.changes != (ChangeKind.UNCHANGED,))


def normalize_type(type_name: str) -> str:
    """Conservatively normalize punctuation spacing, keeping raw malformed input."""
    tokens: list[str] = []
    delimiters: list[str] = []
    index = 0
    while index < len(type_name):
        character = type_name[index]
        start = index
        if character in "\"'`":
            end = _quoted_end(type_name, index)
            if end is None:
                return type_name
            tokens.append(type_name[index:end])
            index = end
            continue
        if character in _ASCII_WHITESPACE:
            while index < len(type_name) and type_name[index] in _ASCII_WHITESPACE:
                index += 1
            tokens.append(type_name[start:index])
            continue
        if character in "<(":
            delimiters.append(character)
        elif character in ">)":
            expected = "<" if character == ">" else "("
            if not delimiters or delimiters.pop() != expected:
                return type_name
        elif character == "\\":
            # Escapes outside quoted spans are not a supported type spelling.
            return type_name
        tokens.append(character)
        index += 1
    if delimiters:
        return type_name
    return "".join(
        token
        for index, token in enumerate(tokens)
        if not (
            token[0] in _ASCII_WHITESPACE
            and (
                index == 0
                or index == len(tokens) - 1
                or tokens[index - 1] in _PUNCTUATION
                or tokens[index + 1] in _PUNCTUATION
            )
        )
    )


def _quoted_end(text: str, start: int) -> int | None:
    quote = text[start]
    index = start + 1
    while index < len(text):
        if text[index] == "\\":
            index += 2
        elif text[index] == quote:
            if index + 1 < len(text) and text[index + 1] == quote:
                index += 2
            else:
                return index + 1
        else:
            index += 1
    return None


def compare_tables(left: TableDetail, right: TableDetail) -> TableComparison:
    """Compare definitions, matching exact column names and parameter keys only."""
    rows = [
        *_compare_columns("columns", left.columns, right.columns),
        *_compare_columns("partition_keys", left.partition_keys, right.partition_keys),
    ]
    for key, left_value, right_value in (
        ("location", left.storage.location, right.storage.location),
        ("input_format", left.storage.input_format, right.storage.input_format),
        ("output_format", left.storage.output_format, right.storage.output_format),
        ("serde", left.storage.serde, right.storage.serde),
        ("compressed", left.storage.compressed, right.storage.compressed),
        ("table_type", left.summary.table_type, right.summary.table_type),
        ("table_format", left.table_format, right.table_format),
    ):
        equal = type(left_value) is type(right_value) and left_value == right_value
        rows.append(
            ComparisonRow(
                "storage",
                key,
                (ChangeKind.UNCHANGED if equal else ChangeKind.VALUE_CHANGED,),
                left_value,
                right_value,
            )
        )
    left_keys = {item[0] for item in left.parameters}
    right_keys = {item[0] for item in right.parameters}
    for key in sorted(left_keys | right_keys):
        change = (
            ChangeKind.ADDED
            if key not in left_keys
            else ChangeKind.REMOVED
            if key not in right_keys
            else ChangeKind.UNAVAILABLE
        )
        rows.append(
            ComparisonRow(
                "parameters",
                key,
                (change,),
                ParameterPresence(key in left_keys),
                ParameterPresence(key in right_keys),
            )
        )
    return TableComparison(tuple(rows))


def _compare_columns(
    section: Literal["columns", "partition_keys"],
    left: tuple[Column, ...],
    right: tuple[Column, ...],
) -> tuple[ComparisonRow, ...]:
    left_names = {column.name for column in left}
    right_names = {column.name for column in right}
    if len(left_names) != len(left) or len(right_names) != len(right):
        # A name map would lose duplicate metadata or invent a correspondence.
        return tuple(
            ComparisonRow(
                section,
                f"{side}:{position}:{column.name}",
                (ChangeKind.UNAVAILABLE,),
                ColumnValue(column, position) if side == "left" else ABSENT,
                ColumnValue(column, position) if side == "right" else ABSENT,
            )
            for side, columns in (("left", left), ("right", right))
            for position, column in enumerate(columns, 1)
        )
    left_rank = {
        name: rank for rank, name in enumerate(c.name for c in left if c.name in right_names)
    }
    right_rank = {
        name: rank for rank, name in enumerate(c.name for c in right if c.name in left_names)
    }
    right_values = {
        column.name: ColumnValue(column, position) for position, column in enumerate(right, 1)
    }
    rows: list[ComparisonRow] = []
    for position, column in enumerate(left, 1):
        left_value = ColumnValue(column, position)
        right_value = right_values.get(column.name)
        if right_value is None:
            rows.append(
                ComparisonRow(section, column.name, (ChangeKind.REMOVED,), left_value, ABSENT)
            )
            continue
        changes: list[ChangeKind] = []
        if normalize_type(column.type_name) != normalize_type(right_value.column.type_name):
            changes.append(ChangeKind.TYPE_CHANGED)
        if column.comment != right_value.column.comment:
            changes.append(ChangeKind.COMMENT_CHANGED)
        if left_rank[column.name] != right_rank[column.name]:
            changes.append(ChangeKind.REORDERED)
        rows.append(
            ComparisonRow(
                section,
                column.name,
                tuple(changes) or (ChangeKind.UNCHANGED,),
                left_value,
                right_value,
            )
        )
    rows.extend(
        ComparisonRow(section, name, (ChangeKind.ADDED,), ABSENT, value)
        for name, value in right_values.items()
        if name not in left_names
    )
    return tuple(rows)


def format_value(value: ComparisonValue) -> str:
    """Produce literal, unambiguous text without exposing parameter values."""
    if isinstance(value, ColumnValue):
        column = value.column
        return (
            f"position={value.position}; name={format_value(column.name)}; "
            f"type={format_value(column.type_name)}; comment={format_value(column.comment)}"
        )
    if isinstance(value, ParameterPresence):
        return "<unavailable: redacted>" if value.present else "<absent>"
    if value is ABSENT:
        return "<absent>"
    if value is None:
        return "<missing>"
    if isinstance(value, TableFormat):
        return f"TableFormat.{value.name}"
    if isinstance(value, bool):
        return "true" if value else "false"
    return json.dumps(value, ensure_ascii=True)


def export_comparison(
    left: TableSnapshot, right: TableSnapshot, comparison: TableComparison
) -> str:
    """Export all rows, independent of screen state, width, focus or filtering."""
    lines = ["Glue table comparison", "Direction: Left -> Right"]
    for label, snapshot in (("Left", left), ("Right", right)):
        ref = snapshot.ref
        lines.extend(
            (
                f"{label}:",
                f"  catalog={format_value(ref.catalog_name)}",
                f"  database={format_value(ref.database_name)}",
                f"  table={format_value(ref.table_name)}",
                f"  connection={format_value(ref.connection_name)}",
                f"  region={format_value(ref.region)}",
                f"  fetched_at={snapshot.fetched_at.isoformat()}",
            )
        )
    lines.extend(
        (f"Normalization: {_NORMALIZATION_POLICY}", "Parameter values: unavailable (redacted)")
    )
    for section in _SECTIONS:
        lines.append(f"[{section}]")
        for row in comparison.rows:
            if row.section == section:
                lines.extend(
                    (
                        f"{format_value(row.key)}: {', '.join(change.value for change in row.changes)}",
                        f"  Left: {format_value(row.left)}",
                        f"  Right: {format_value(row.right)}",
                    )
                )
    return "\n".join(lines) + "\n"


__all__ = [
    "ABSENT",
    "AbsentValue",
    "ChangeKind",
    "ColumnValue",
    "ComparisonRow",
    "ComparisonValue",
    "ParameterPresence",
    "Scalar",
    "Side",
    "TableComparison",
    "TableSnapshot",
    "compare_tables",
    "export_comparison",
    "format_value",
    "normalize_type",
]
