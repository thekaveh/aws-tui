"""Tests for immutable Iceberg metadata values."""

from __future__ import annotations

from dataclasses import FrozenInstanceError, fields, is_dataclass
from datetime import UTC, datetime

import pytest

from aws_tui.domain.iceberg import (
    IcebergDataFile,
    IcebergHistoryEntry,
    IcebergManifest,
    IcebergPartition,
    IcebergPartitionSpec,
    IcebergReference,
    IcebergSnapshot,
)

pytestmark = pytest.mark.unit

NOW = datetime(2026, 7, 26, tzinfo=UTC)


def test_iceberg_snapshot_is_frozen_and_keeps_summary() -> None:
    snapshot = IcebergSnapshot(
        snapshot_id=42,
        parent_id=41,
        committed_at=NOW,
        operation="append",
        manifest_list="s3://lake/table/metadata/snap-42.avro",
        summary=(("added-records", "100"),),
    )

    assert snapshot.snapshot_id == 42
    assert snapshot.summary == (("added-records", "100"),)
    assert "s3://lake/table/metadata/snap-42.avro" not in repr(snapshot)
    assert "added-records" not in repr(snapshot)
    with pytest.raises(FrozenInstanceError):
        snapshot.operation = "overwrite"  # type: ignore[misc]


def test_partition_spec_preserves_dynamic_partition_columns() -> None:
    spec = IcebergPartitionSpec(("event_date", "region_bucket"))

    assert spec.field_names == ("event_date", "region_bucket")


def test_iceberg_records_are_frozen_slot_dataclasses_with_exact_fields() -> None:
    records = (
        IcebergSnapshot,
        IcebergHistoryEntry,
        IcebergManifest,
        IcebergDataFile,
        IcebergPartitionSpec,
        IcebergPartition,
        IcebergReference,
    )

    assert all(is_dataclass(record) for record in records)
    assert all(record.__dataclass_params__.frozen for record in records)
    assert all(hasattr(record, "__slots__") for record in records)
    assert [field.name for field in fields(IcebergSnapshot)] == [
        "committed_at",
        "snapshot_id",
        "parent_id",
        "operation",
        "manifest_list",
        "summary",
    ]
    assert [field.name for field in fields(IcebergHistoryEntry)] == [
        "made_current_at",
        "snapshot_id",
        "parent_id",
        "is_current_ancestor",
    ]
    assert [field.name for field in fields(IcebergManifest)] == [
        "path",
        "length",
        "partition_spec_id",
        "added_snapshot_id",
        "added_data_files_count",
        "existing_data_files_count",
        "deleted_data_files_count",
        "partition_summaries",
    ]
    assert [field.name for field in fields(IcebergDataFile)] == [
        "content",
        "file_path",
        "file_format",
        "spec_id",
        "partition",
        "record_count",
        "file_size_in_bytes",
        "equality_ids",
        "sort_order_id",
    ]
    assert [field.name for field in fields(IcebergPartitionSpec)] == ["field_names"]
    assert [field.name for field in fields(IcebergPartition)] == [
        "values",
        "record_count",
        "file_count",
        "total_data_file_size_in_bytes",
        "position_delete_record_count",
        "position_delete_file_count",
        "equality_delete_record_count",
        "equality_delete_file_count",
        "last_updated_at",
        "last_updated_snapshot_id",
    ]
    assert [field.name for field in fields(IcebergReference)] == [
        "name",
        "ref_type",
        "snapshot_id",
        "max_reference_age_in_ms",
        "min_snapshots_to_keep",
        "max_snapshot_age_in_ms",
    ]


def test_iceberg_metadata_paths_and_complex_values_do_not_enter_reprs() -> None:
    manifest = IcebergManifest(
        path="s3://lake/metadata/manifest-secret.avro",
        length=1,
        partition_spec_id=2,
        added_snapshot_id=42,
        added_data_files_count=3,
        existing_data_files_count=4,
        deleted_data_files_count=5,
        partition_summaries="PARTITION_SUMMARY_SECRET",
    )
    data_file = IcebergDataFile(
        content=0,
        file_path="s3://lake/data/file-secret.parquet",
        file_format="PARQUET",
        spec_id=2,
        partition="PARTITION_SECRET",
        record_count=10,
        file_size_in_bytes=100,
        equality_ids="EQUALITY_IDS_SECRET",
        sort_order_id=None,
    )
    partition = IcebergPartition(
        values=(("region", "PARTITION_VALUE_SECRET"),),
        record_count=10,
        file_count=1,
        total_data_file_size_in_bytes=100,
        position_delete_record_count=None,
        position_delete_file_count=None,
        equality_delete_record_count=None,
        equality_delete_file_count=None,
        last_updated_at=NOW,
        last_updated_snapshot_id=42,
    )

    rendered = repr((manifest, data_file, partition))
    for marker in (
        "manifest-secret",
        "PARTITION_SUMMARY_SECRET",
        "file-secret",
        "PARTITION_SECRET",
        "EQUALITY_IDS_SECRET",
        "PARTITION_VALUE_SECRET",
    ):
        assert marker not in rendered
