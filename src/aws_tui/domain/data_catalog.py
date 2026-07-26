"""Immutable shared vocabulary for the AWS Glue data catalog."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum


class TableFormat(StrEnum):
    ICEBERG = "iceberg"
    HIVE = "hive"
    HUDI = "hudi"
    DELTA = "delta"
    OTHER = "other"


def detect_table_format(
    parameters: Mapping[str, str],
    input_format: str | None,
    table_type: str | None,
) -> TableFormat:
    """Classify a physical Glue table from its normalized catalog metadata."""
    marker_keys = frozenset(
        {
            "table_type",
            "tabletype",
            "classification",
            "provider",
            "spark.sql.sources.provider",
        }
    )
    markers = {
        value.strip().casefold()
        for key, value in parameters.items()
        if isinstance(key, str) and isinstance(value, str) and key.strip().casefold() in marker_keys
    }
    normalized_type = table_type.strip().casefold() if isinstance(table_type, str) else None
    if normalized_type:
        markers.add(normalized_type)

    if "iceberg" in markers:
        return TableFormat.ICEBERG
    if "hudi" in markers:
        return TableFormat.HUDI
    if "delta" in markers:
        return TableFormat.DELTA

    if normalized_type == "virtual_view":
        return TableFormat.OTHER
    normalized_input = input_format.strip() if isinstance(input_format, str) else ""
    if normalized_type in {"external_table", "managed_table"} or normalized_input:
        return TableFormat.HIVE
    return TableFormat.OTHER


@dataclass(frozen=True, slots=True)
class CatalogRef:
    catalog_name: str
    connection_name: str
    region: str


@dataclass(frozen=True, slots=True)
class DatabaseRef:
    catalog_name: str
    database_name: str
    connection_name: str
    region: str

    @property
    def catalog(self) -> CatalogRef:
        return CatalogRef(self.catalog_name, self.connection_name, self.region)


@dataclass(frozen=True, slots=True)
class TableRef:
    catalog_name: str
    database_name: str
    table_name: str
    connection_name: str
    region: str

    @property
    def database(self) -> DatabaseRef:
        return DatabaseRef(
            self.catalog_name,
            self.database_name,
            self.connection_name,
            self.region,
        )

    @property
    def catalog(self) -> CatalogRef:
        return self.database.catalog


@dataclass(frozen=True, slots=True)
class Column:
    name: str
    type_name: str
    comment: str | None
    partition_key: bool


@dataclass(frozen=True, slots=True)
class StorageDescriptor:
    location: str | None
    input_format: str | None
    output_format: str | None
    serde: str | None
    compressed: bool
    bucket_count: int


@dataclass(frozen=True, slots=True)
class DatabaseSummary:
    ref: DatabaseRef
    description: str | None
    location_uri: str | None
    created_at: datetime | None


@dataclass(frozen=True, slots=True)
class TableSummary:
    ref: TableRef
    description: str | None
    owner: str | None
    table_type: str | None
    created_at: datetime | None
    updated_at: datetime | None


@dataclass(frozen=True, slots=True)
class TableDetail:
    summary: TableSummary
    columns: tuple[Column, ...]
    partition_keys: tuple[Column, ...]
    storage: StorageDescriptor
    classification: str | None = field(repr=False)
    table_format: TableFormat
    parameters: tuple[tuple[str, str], ...] = field(repr=False)

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "parameters",
            tuple(
                sorted(
                    ((key, "[REDACTED]") for key, _ in self.parameters), key=lambda item: item[0]
                )
            ),
        )


@dataclass(frozen=True, slots=True)
class PartitionSummary:
    values: tuple[str, ...]
    created_at: datetime | None
    last_accessed_at: datetime | None
    storage_location: str | None


@dataclass(frozen=True, slots=True)
class ColumnStatistics:
    column_name: str
    type_name: str
    analyzed_at: datetime | None
    values: tuple[tuple[str, str], ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "values", tuple(sorted(self.values, key=lambda item: item[0])))


__all__ = [
    "CatalogRef",
    "Column",
    "ColumnStatistics",
    "DatabaseRef",
    "DatabaseSummary",
    "PartitionSummary",
    "StorageDescriptor",
    "TableDetail",
    "TableFormat",
    "TableRef",
    "TableSummary",
    "detect_table_format",
]
