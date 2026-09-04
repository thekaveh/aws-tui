"""Immutable domain values returned by Iceberg metadata-table inspection."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import TypeVar
from uuid import uuid4

import sqlglot
from sqlglot import exp
from sqlglot.errors import SqlglotError

from aws_tui.domain.athena_runner import AthenaQueryRunner, BoundedQueryResult
from aws_tui.domain.data_catalog import TableRef
from aws_tui.domain.filesystem import ValidationError
from aws_tui.domain.query import QueryContext

_PARTITION_METRIC_COLUMNS = (
    "record_count",
    "file_count",
    "total_data_file_size_in_bytes",
    "position_delete_record_count",
    "position_delete_file_count",
    "equality_delete_record_count",
    "equality_delete_file_count",
    "last_updated_at",
    "last_updated_snapshot_id",
)
T = TypeVar("T")


@dataclass(frozen=True, slots=True)
class IcebergSnapshot:
    committed_at: datetime
    snapshot_id: int
    parent_id: int | None
    operation: str
    manifest_list: str = field(repr=False)
    summary: tuple[tuple[str, str], ...] = field(repr=False)


@dataclass(frozen=True, slots=True)
class IcebergHistoryEntry:
    made_current_at: datetime
    snapshot_id: int
    parent_id: int | None
    is_current_ancestor: bool


@dataclass(frozen=True, slots=True)
class IcebergManifest:
    path: str = field(repr=False)
    length: int
    partition_spec_id: int
    added_snapshot_id: int
    added_data_files_count: int
    existing_data_files_count: int
    deleted_data_files_count: int
    partition_summaries: str | None = field(repr=False)


@dataclass(frozen=True, slots=True)
class IcebergDataFile:
    content: int
    file_path: str = field(repr=False)
    file_format: str
    spec_id: int
    partition: str | None = field(repr=False)
    record_count: int
    file_size_in_bytes: int
    equality_ids: str | None = field(repr=False)
    sort_order_id: int | None


@dataclass(frozen=True, slots=True)
class IcebergPartitionSpec:
    field_names: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class IcebergPartition:
    values: tuple[tuple[str, str | None], ...] = field(repr=False)
    record_count: int
    file_count: int
    total_data_file_size_in_bytes: int
    position_delete_record_count: int | None
    position_delete_file_count: int | None
    equality_delete_record_count: int | None
    equality_delete_file_count: int | None
    last_updated_at: datetime | None
    last_updated_snapshot_id: int | None


@dataclass(frozen=True, slots=True)
class IcebergReference:
    name: str
    ref_type: str
    snapshot_id: int
    max_reference_age_in_ms: int | None
    min_snapshots_to_keep: int | None
    max_snapshot_age_in_ms: int | None


class IcebergMetadataShapeError(ValidationError):
    """Athena returned an unexpected Iceberg metadata-table shape."""


def quote_athena_identifier(value: str) -> str:
    """Quote one Athena identifier without treating dots as separators."""
    return '"' + value.replace('"', '""') + '"'


class IcebergInspector:
    """Read bounded Iceberg metadata tables through Athena."""

    def __init__(
        self,
        *,
        runner: AthenaQueryRunner,
        context: QueryContext,
    ) -> None:
        self._runner = runner
        self._context = context

    async def list_snapshots(
        self,
        table_ref: TableRef,
    ) -> tuple[IcebergSnapshot, ...]:
        return await self._inspect(
            table_ref,
            suffix="snapshots",
            projection=("committed_at, snapshot_id, parent_id, operation, manifest_list, summary"),
            order_by="committed_at DESC",
            limit=100,
            mapper=_map_snapshots,
        )

    async def list_history(
        self,
        table_ref: TableRef,
    ) -> tuple[IcebergHistoryEntry, ...]:
        return await self._inspect(
            table_ref,
            suffix="history",
            projection=("made_current_at, snapshot_id, parent_id, is_current_ancestor"),
            order_by="made_current_at DESC",
            limit=100,
            mapper=_map_history,
        )

    async def list_manifests(
        self,
        table_ref: TableRef,
    ) -> tuple[IcebergManifest, ...]:
        return await self._inspect(
            table_ref,
            suffix="manifests",
            projection=(
                "path, length, partition_spec_id, added_snapshot_id, "
                "added_data_files_count, existing_data_files_count, "
                "deleted_data_files_count, partition_summaries"
            ),
            order_by="added_snapshot_id DESC, path",
            limit=500,
            mapper=_map_manifests,
        )

    async def list_files(
        self,
        table_ref: TableRef,
    ) -> tuple[IcebergDataFile, ...]:
        return await self._inspect(
            table_ref,
            suffix="files",
            projection=(
                "content, file_path, file_format, spec_id, partition, record_count, "
                "file_size_in_bytes, equality_ids, sort_order_id"
            ),
            order_by="file_path",
            limit=1000,
            mapper=_map_files,
        )

    async def list_partitions(
        self,
        table_ref: TableRef,
    ) -> tuple[IcebergPartition, ...]:
        result = await self._partition_result(table_ref)
        return _sanitize_mapping(result, _map_partitions)

    async def list_refs(
        self,
        table_ref: TableRef,
    ) -> tuple[IcebergReference, ...]:
        return await self._inspect(
            table_ref,
            suffix="refs",
            projection=(
                "name, type, snapshot_id, max_reference_age_in_ms, "
                "min_snapshots_to_keep, max_snapshot_age_in_ms"
            ),
            order_by="name",
            limit=100,
            mapper=_map_refs,
        )

    async def partition_spec(
        self,
        table_ref: TableRef,
    ) -> IcebergPartitionSpec:
        result = await self._partition_result(table_ref)
        return _sanitize_mapping(result, _map_partition_spec)

    async def _partition_result(
        self,
        table_ref: TableRef,
    ) -> BoundedQueryResult:
        self._validate_table(table_ref)
        sql = f"SELECT * FROM {_metadata_table(table_ref, 'partitions')} LIMIT 500"
        return await self._runner.run(
            sql,
            self._context,
            request_token=_request_token(),
            max_rows=500,
        )

    async def _inspect(
        self,
        table_ref: TableRef,
        *,
        suffix: str,
        projection: str,
        order_by: str,
        limit: int,
        mapper: Callable[[BoundedQueryResult], tuple[T, ...]],
    ) -> tuple[T, ...]:
        self._validate_table(table_ref)
        sql = (
            f"SELECT {projection} FROM {_metadata_table(table_ref, suffix)} "
            f"ORDER BY {order_by} LIMIT {limit}"
        )
        result = await self._runner.run(
            sql,
            self._context,
            request_token=_request_token(),
            max_rows=limit,
        )
        return _sanitize_mapping(result, mapper)

    def _validate_table(self, table_ref: TableRef) -> None:
        if (
            table_ref.connection_name != self._context.connection_name
            or table_ref.region != self._context.region
            or table_ref.catalog_name != self._context.catalog
            or table_ref.database_name != self._context.database
        ):
            raise ValidationError("Iceberg table does not match the active query context")


def _request_token() -> str:
    return f"iceberg-{uuid4().hex}"


def _metadata_table(table_ref: TableRef, suffix: str) -> str:
    return ".".join(
        (
            quote_athena_identifier(table_ref.catalog_name),
            quote_athena_identifier(table_ref.database_name),
            quote_athena_identifier(f"{table_ref.table_name}${suffix}"),
        )
    )


def _sanitize_mapping(
    result: BoundedQueryResult,
    mapper: Callable[[BoundedQueryResult], T],
) -> T:
    try:
        return mapper(result)
    except IcebergMetadataShapeError as exc:
        message = str(exc)
    except (KeyError, TypeError, ValueError):
        message = "invalid Iceberg metadata value"
    raise IcebergMetadataShapeError(message) from None


def _column_indexes(
    result: BoundedQueryResult,
    required: tuple[str, ...],
) -> dict[str, int]:
    names = tuple(column.name for column in result.columns)
    if len(set(names)) != len(names):
        raise IcebergMetadataShapeError("duplicate Iceberg metadata column")
    indexes = {name: index for index, name in enumerate(names)}
    for name in required:
        if name not in indexes:
            raise IcebergMetadataShapeError(f"missing Iceberg metadata column: {name}")
    if any(len(row) != len(names) for row in result.rows):
        raise IcebergMetadataShapeError("Iceberg metadata row width mismatch")
    return indexes


def _value(
    row: tuple[str | None, ...],
    indexes: dict[str, int],
    name: str,
) -> str | None:
    return row[indexes[name]]


def _required_string(value: str | None) -> str:
    if value is None:
        raise ValueError
    return value


def _required_int(value: str | None) -> int:
    return int(_required_string(value))


def _optional_int(value: str | None) -> int | None:
    return None if value is None else int(value)


def _required_bool(value: str | None) -> bool:
    normalized = _required_string(value).strip().casefold()
    if normalized == "true":
        return True
    if normalized == "false":
        return False
    raise ValueError


def _required_datetime(value: str | None) -> datetime:
    return _parse_datetime(_required_string(value))


def _optional_datetime(value: str | None) -> datetime | None:
    return None if value is None else _parse_datetime(value)


def _parse_datetime(value: str) -> datetime:
    normalized = value.strip()
    if normalized.casefold().endswith(" utc"):
        normalized = f"{normalized[:-4]}+00:00"
    elif normalized.endswith(("Z", "z")):
        normalized = f"{normalized[:-1]}+00:00"
    return datetime.fromisoformat(normalized)


def _map_summary(value: str | None) -> tuple[tuple[str, str], ...]:
    raw = _required_string(value).strip()
    if raw == "{}":
        return ()
    if not raw.startswith("{") or not raw.endswith("}"):
        raise ValueError
    pairs: list[tuple[str, str]] = []
    for item in _split_collection(raw[1:-1]):
        key, separator, item_value = item.partition("=")
        if not separator or not key.strip():
            raise ValueError
        pairs.append((key.strip(), item_value.strip()))
    return tuple(pairs)


def _split_collection(value: str) -> tuple[str, ...]:
    items: list[str] = []
    current: list[str] = []
    depth = 0
    quote: str | None = None
    escaped = False
    for character in value:
        if escaped:
            current.append(character)
            escaped = False
            continue
        if character == "\\":
            current.append(character)
            escaped = True
            continue
        if quote is not None:
            current.append(character)
            if character == quote:
                quote = None
            continue
        if character in {"'", '"'}:
            current.append(character)
            quote = character
            continue
        if character in "([{":
            depth += 1
        elif character in ")]}":
            depth -= 1
        if character == "," and depth == 0:
            items.append("".join(current).strip())
            current.clear()
        else:
            current.append(character)
    if quote is not None or depth != 0:
        raise ValueError
    if current:
        items.append("".join(current).strip())
    return tuple(items)


def _map_snapshots(
    result: BoundedQueryResult,
) -> tuple[IcebergSnapshot, ...]:
    required = (
        "committed_at",
        "snapshot_id",
        "parent_id",
        "operation",
        "manifest_list",
        "summary",
    )
    indexes = _column_indexes(result, required)
    return tuple(
        IcebergSnapshot(
            committed_at=_required_datetime(_value(row, indexes, "committed_at")),
            snapshot_id=_required_int(_value(row, indexes, "snapshot_id")),
            parent_id=_optional_int(_value(row, indexes, "parent_id")),
            operation=_required_string(_value(row, indexes, "operation")),
            manifest_list=_required_string(_value(row, indexes, "manifest_list")),
            summary=_map_summary(_value(row, indexes, "summary")),
        )
        for row in result.rows
    )


def _map_history(
    result: BoundedQueryResult,
) -> tuple[IcebergHistoryEntry, ...]:
    required = (
        "made_current_at",
        "snapshot_id",
        "parent_id",
        "is_current_ancestor",
    )
    indexes = _column_indexes(result, required)
    return tuple(
        IcebergHistoryEntry(
            made_current_at=_required_datetime(_value(row, indexes, "made_current_at")),
            snapshot_id=_required_int(_value(row, indexes, "snapshot_id")),
            parent_id=_optional_int(_value(row, indexes, "parent_id")),
            is_current_ancestor=_required_bool(_value(row, indexes, "is_current_ancestor")),
        )
        for row in result.rows
    )


def _map_manifests(
    result: BoundedQueryResult,
) -> tuple[IcebergManifest, ...]:
    required = (
        "path",
        "length",
        "partition_spec_id",
        "added_snapshot_id",
        "added_data_files_count",
        "existing_data_files_count",
        "deleted_data_files_count",
        "partition_summaries",
    )
    indexes = _column_indexes(result, required)
    return tuple(
        IcebergManifest(
            path=_required_string(_value(row, indexes, "path")),
            length=_required_int(_value(row, indexes, "length")),
            partition_spec_id=_required_int(_value(row, indexes, "partition_spec_id")),
            added_snapshot_id=_required_int(_value(row, indexes, "added_snapshot_id")),
            added_data_files_count=_required_int(_value(row, indexes, "added_data_files_count")),
            existing_data_files_count=_required_int(
                _value(row, indexes, "existing_data_files_count")
            ),
            deleted_data_files_count=_required_int(
                _value(row, indexes, "deleted_data_files_count")
            ),
            partition_summaries=_value(row, indexes, "partition_summaries"),
        )
        for row in result.rows
    )


def _map_files(
    result: BoundedQueryResult,
) -> tuple[IcebergDataFile, ...]:
    required = (
        "content",
        "file_path",
        "file_format",
        "spec_id",
        "partition",
        "record_count",
        "file_size_in_bytes",
        "equality_ids",
        "sort_order_id",
    )
    indexes = _column_indexes(result, required)
    return tuple(
        IcebergDataFile(
            content=_required_int(_value(row, indexes, "content")),
            file_path=_required_string(_value(row, indexes, "file_path")),
            file_format=_required_string(_value(row, indexes, "file_format")),
            spec_id=_required_int(_value(row, indexes, "spec_id")),
            partition=_value(row, indexes, "partition"),
            record_count=_required_int(_value(row, indexes, "record_count")),
            file_size_in_bytes=_required_int(_value(row, indexes, "file_size_in_bytes")),
            equality_ids=_value(row, indexes, "equality_ids"),
            sort_order_id=_optional_int(_value(row, indexes, "sort_order_id")),
        )
        for row in result.rows
    )


def _map_partition_spec(result: BoundedQueryResult) -> IcebergPartitionSpec:
    _column_indexes(result, _PARTITION_METRIC_COLUMNS)
    names = tuple(column.name for column in result.columns)
    expected = frozenset((*_PARTITION_METRIC_COLUMNS, "partition", "spec_id"))
    unexpected = tuple(name for name in names if name not in expected)
    if unexpected:
        raise IcebergMetadataShapeError("unexpected Iceberg partition metadata column")
    has_partition = "partition" in names
    has_spec_id = "spec_id" in names
    if has_partition != has_spec_id:
        raise IcebergMetadataShapeError("incomplete Iceberg partition metadata columns")
    if not has_partition:
        return IcebergPartitionSpec(())
    partition_column = result.columns[names.index("partition")]
    return IcebergPartitionSpec(_partition_field_names(partition_column.type_name))


def _map_partitions(
    result: BoundedQueryResult,
) -> tuple[IcebergPartition, ...]:
    indexes = _column_indexes(result, _PARTITION_METRIC_COLUMNS)
    spec = _map_partition_spec(result)
    return tuple(_map_partition(row, indexes, spec) for row in result.rows)


def _map_partition(
    row: tuple[str | None, ...],
    indexes: dict[str, int],
    spec: IcebergPartitionSpec,
) -> IcebergPartition:
    if spec.field_names:
        _required_int(_value(row, indexes, "spec_id"))
    return IcebergPartition(
        values=_partition_values(
            _value(row, indexes, "partition"),
            spec.field_names,
        )
        if spec.field_names
        else (),
        record_count=_required_int(_value(row, indexes, "record_count")),
        file_count=_required_int(_value(row, indexes, "file_count")),
        total_data_file_size_in_bytes=_required_int(
            _value(row, indexes, "total_data_file_size_in_bytes")
        ),
        position_delete_record_count=_optional_int(
            _value(row, indexes, "position_delete_record_count")
        ),
        position_delete_file_count=_optional_int(
            _value(row, indexes, "position_delete_file_count")
        ),
        equality_delete_record_count=_optional_int(
            _value(row, indexes, "equality_delete_record_count")
        ),
        equality_delete_file_count=_optional_int(
            _value(row, indexes, "equality_delete_file_count")
        ),
        last_updated_at=_optional_datetime(_value(row, indexes, "last_updated_at")),
        last_updated_snapshot_id=_optional_int(_value(row, indexes, "last_updated_snapshot_id")),
    )


def _partition_field_names(type_name: str) -> tuple[str, ...]:
    try:
        data_type = sqlglot.parse_one(
            type_name,
            into=exp.DataType,
            read="athena",
            error_message_context=0,
        )
    except (SqlglotError, TypeError, ValueError):
        raise IcebergMetadataShapeError("invalid Iceberg partition struct type") from None
    if not isinstance(data_type, exp.DataType) or data_type.this is not exp.DataType.Type.STRUCT:
        raise IcebergMetadataShapeError("invalid Iceberg partition struct type")
    field_names: list[str] = []
    for definition in data_type.expressions:
        if not isinstance(definition, exp.ColumnDef):
            raise IcebergMetadataShapeError("invalid Iceberg partition struct field")
        identifier = definition.this
        if not isinstance(identifier, exp.Identifier) or not identifier.name:
            raise IcebergMetadataShapeError("invalid Iceberg partition struct field")
        field_names.append(identifier.name)
    if not field_names or len(set(field_names)) != len(field_names):
        raise IcebergMetadataShapeError("invalid Iceberg partition struct fields")
    return tuple(field_names)


def _partition_values(
    value: str | None,
    field_names: tuple[str, ...],
) -> tuple[tuple[str, str | None], ...]:
    raw = _required_string(value).strip()
    if not raw.startswith("{") or not raw.endswith("}"):
        raise ValueError
    items = _split_collection(raw[1:-1])
    if len(items) != len(field_names):
        raise ValueError
    assignments = tuple(_split_assignment(item) for item in items)
    named = tuple(assignment is not None for assignment in assignments)
    if any(named) and not all(named):
        raise ValueError
    values: list[tuple[str, str | None]] = []
    for field_name, item, assignment in zip(field_names, items, assignments, strict=True):
        item_value = item
        if assignment is not None:
            item_name, item_value = assignment
            if _unquote_field_name(item_name) != field_name:
                raise ValueError
        normalized = item_value.strip()
        if not normalized:
            raise ValueError
        values.append(
            (
                field_name,
                None if normalized.casefold() == "null" else normalized,
            )
        )
    return tuple(values)


def _split_assignment(value: str) -> tuple[str, str] | None:
    depth = 0
    quote: str | None = None
    escaped = False
    for index, character in enumerate(value):
        if escaped:
            escaped = False
            continue
        if character == "\\":
            escaped = True
            continue
        if quote is not None:
            if character == quote:
                quote = None
            continue
        if character in {"'", '"'}:
            quote = character
            continue
        if character in "([{":
            depth += 1
            continue
        if character in ")]}":
            depth -= 1
            if depth < 0:
                raise ValueError
            continue
        if character == "=" and depth == 0:
            name = value[:index].strip()
            item_value = value[index + 1 :].strip()
            if not name or not item_value:
                raise ValueError
            return name, item_value
    if quote is not None or depth != 0 or escaped:
        raise ValueError
    return None


def _unquote_field_name(value: str) -> str:
    stripped = value.strip()
    if stripped.startswith('"') and stripped.endswith('"') and len(stripped) >= 2:
        return stripped[1:-1].replace('""', '"')
    return stripped


def _map_refs(
    result: BoundedQueryResult,
) -> tuple[IcebergReference, ...]:
    required = (
        "name",
        "type",
        "snapshot_id",
        "max_reference_age_in_ms",
        "min_snapshots_to_keep",
        "max_snapshot_age_in_ms",
    )
    indexes = _column_indexes(result, required)
    return tuple(
        IcebergReference(
            name=_required_string(_value(row, indexes, "name")),
            ref_type=_required_string(_value(row, indexes, "type")),
            snapshot_id=_required_int(_value(row, indexes, "snapshot_id")),
            max_reference_age_in_ms=_optional_int(_value(row, indexes, "max_reference_age_in_ms")),
            min_snapshots_to_keep=_optional_int(_value(row, indexes, "min_snapshots_to_keep")),
            max_snapshot_age_in_ms=_optional_int(_value(row, indexes, "max_snapshot_age_in_ms")),
        )
        for row in result.rows
    )


__all__ = [
    "IcebergDataFile",
    "IcebergHistoryEntry",
    "IcebergInspector",
    "IcebergManifest",
    "IcebergMetadataShapeError",
    "IcebergPartition",
    "IcebergPartitionSpec",
    "IcebergReference",
    "IcebergSnapshot",
    "quote_athena_identifier",
]
