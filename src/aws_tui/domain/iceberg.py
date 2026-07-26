"""Immutable domain values returned by Iceberg metadata-table inspection."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


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


__all__ = [
    "IcebergDataFile",
    "IcebergHistoryEntry",
    "IcebergManifest",
    "IcebergPartition",
    "IcebergPartitionSpec",
    "IcebergReference",
    "IcebergSnapshot",
]
