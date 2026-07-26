"""Tests for immutable Iceberg metadata values."""

from __future__ import annotations

from dataclasses import FrozenInstanceError, fields, is_dataclass
from datetime import UTC, datetime
from pathlib import Path
from traceback import TracebackException
from typing import get_type_hints

import pytest

from aws_tui.domain.athena_runner import (
    AthenaQueryCancelledError,
    BoundedQueryResult,
)
from aws_tui.domain.data_catalog import TableRef
from aws_tui.domain.filesystem import ValidationError
from aws_tui.domain.iceberg import (
    IcebergDataFile,
    IcebergHistoryEntry,
    IcebergInspector,
    IcebergManifest,
    IcebergMetadataShapeError,
    IcebergPartition,
    IcebergPartitionSpec,
    IcebergReference,
    IcebergSnapshot,
    quote_athena_identifier,
)
from aws_tui.domain.query import (
    QueryContext,
    QueryExecutionDetail,
    QueryExecutionRef,
    QueryExecutionSummary,
    QueryState,
    QueryStatistics,
    ResultColumn,
)
from aws_tui.infra.crash_dump import CrashDump

pytestmark = pytest.mark.unit

NOW = datetime(2026, 7, 26, tzinfo=UTC)
CONTEXT = QueryContext(
    "dev",
    "us-east-1",
    "primary",
    "AwsDataCatalog",
    "analytics",
)
TABLE = TableRef(
    "AwsDataCatalog",
    "analytics",
    "order-events",
    "dev",
    "us-east-1",
)


def _query_result(
    columns: tuple[str, ...],
    rows: tuple[tuple[str | None, ...], ...],
) -> BoundedQueryResult:
    ref = QueryExecutionRef("metadata-query", "dev", "us-east-1", "primary")
    detail = QueryExecutionDetail(
        QueryExecutionSummary(ref, QueryState.SUCCEEDED, NOW, NOW, "DML"),
        None,
        CONTEXT,
        QueryStatistics(None, None, None, None, None, False),
        None,
        "Athena engine version 3",
        None,
    )
    return BoundedQueryResult(
        detail,
        tuple(ResultColumn(name, "varchar", "NULLABLE") for name in columns),
        rows,
    )


class RecordingRunner:
    def __init__(
        self,
        result: BoundedQueryResult | None = None,
        *,
        error: Exception | None = None,
    ) -> None:
        self.result = result or _query_result((), ())
        self.error = error
        self.calls: list[tuple[str, QueryContext, str, int]] = []

    async def run(
        self,
        sql: str,
        context: QueryContext,
        *,
        request_token: str,
        max_rows: int,
    ) -> BoundedQueryResult:
        self.calls.append((sql, context, request_token, max_rows))
        if self.error is not None:
            raise self.error
        return self.result

    @property
    def sql(self) -> str:
        return self.calls[-1][0]


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
    assert list(get_type_hints(IcebergSnapshot).items()) == [
        ("committed_at", datetime),
        ("snapshot_id", int),
        ("parent_id", int | None),
        ("operation", str),
        ("manifest_list", str),
        ("summary", tuple[tuple[str, str], ...]),
    ]
    assert list(get_type_hints(IcebergHistoryEntry).items()) == [
        ("made_current_at", datetime),
        ("snapshot_id", int),
        ("parent_id", int | None),
        ("is_current_ancestor", bool),
    ]
    assert list(get_type_hints(IcebergManifest).items()) == [
        ("path", str),
        ("length", int),
        ("partition_spec_id", int),
        ("added_snapshot_id", int),
        ("added_data_files_count", int),
        ("existing_data_files_count", int),
        ("deleted_data_files_count", int),
        ("partition_summaries", str | None),
    ]
    assert list(get_type_hints(IcebergDataFile).items()) == [
        ("content", int),
        ("file_path", str),
        ("file_format", str),
        ("spec_id", int),
        ("partition", str | None),
        ("record_count", int),
        ("file_size_in_bytes", int),
        ("equality_ids", str | None),
        ("sort_order_id", int | None),
    ]
    assert list(get_type_hints(IcebergPartitionSpec).items()) == [("field_names", tuple[str, ...])]
    assert list(get_type_hints(IcebergPartition).items()) == [
        ("values", tuple[tuple[str, str | None], ...]),
        ("record_count", int),
        ("file_count", int),
        ("total_data_file_size_in_bytes", int),
        ("position_delete_record_count", int | None),
        ("position_delete_file_count", int | None),
        ("equality_delete_record_count", int | None),
        ("equality_delete_file_count", int | None),
        ("last_updated_at", datetime | None),
        ("last_updated_snapshot_id", int | None),
    ]
    assert list(get_type_hints(IcebergReference).items()) == [
        ("name", str),
        ("ref_type", str),
        ("snapshot_id", int),
        ("max_reference_age_in_ms", int | None),
        ("min_snapshots_to_keep", int | None),
        ("max_snapshot_age_in_ms", int | None),
    ]


def test_iceberg_public_contract_exports_exact_record_set() -> None:
    from aws_tui.domain import iceberg

    assert iceberg.__all__ == [
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


def test_quote_athena_identifier_doubles_hostile_quotes() -> None:
    assert quote_athena_identifier('catalog"name') == '"catalog""name"'


@pytest.mark.asyncio
async def test_inspector_quotes_every_hostile_table_identifier_component() -> None:
    columns = (
        "committed_at",
        "snapshot_id",
        "parent_id",
        "operation",
        "manifest_list",
        "summary",
    )
    runner = RecordingRunner(_query_result(columns, ()))
    context = QueryContext(
        'dev"profile',
        "us-east-1",
        "primary",
        'catalog"name',
        'database"name',
    )
    table = TableRef(
        'catalog"name',
        'database"name',
        'table"name',
        'dev"profile',
        "us-east-1",
    )

    await IcebergInspector(runner=runner, context=context).list_snapshots(table)

    assert 'FROM "catalog""name"."database""name"."table""name$snapshots"' in runner.sql


@pytest.mark.asyncio
async def test_inspector_quotes_identifiers_and_maps_snapshots() -> None:
    runner = RecordingRunner(
        _query_result(
            (
                "committed_at",
                "snapshot_id",
                "parent_id",
                "operation",
                "manifest_list",
                "summary",
            ),
            (
                (
                    "2026-07-26 12:30:00+00:00",
                    "42",
                    "41",
                    "append",
                    "s3://lake/metadata/snap-42.avro",
                    "{added-records=100, owner=analytics}",
                ),
            ),
        )
    )
    inspector = IcebergInspector(runner=runner, context=CONTEXT)

    rows = await inspector.list_snapshots(TABLE)

    assert runner.sql == (
        "SELECT committed_at, snapshot_id, parent_id, operation, manifest_list, summary "
        'FROM "AwsDataCatalog"."analytics"."order-events$snapshots" '
        "ORDER BY committed_at DESC LIMIT 100"
    )
    assert rows == (
        IcebergSnapshot(
            committed_at=datetime(2026, 7, 26, 12, 30, tzinfo=UTC),
            snapshot_id=42,
            parent_id=41,
            operation="append",
            manifest_list="s3://lake/metadata/snap-42.avro",
            summary=(("added-records", "100"), ("owner", "analytics")),
        ),
    )
    assert runner.calls[0][3] == 100
    assert "order-events" not in runner.calls[0][2]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("method", "suffix", "projection", "order_by", "limit"),
    [
        (
            "list_history",
            "history",
            "made_current_at, snapshot_id, parent_id, is_current_ancestor",
            "made_current_at DESC",
            100,
        ),
        (
            "list_manifests",
            "manifests",
            (
                "path, length, partition_spec_id, added_snapshot_id, "
                "added_data_files_count, existing_data_files_count, "
                "deleted_data_files_count, partition_summaries"
            ),
            "added_snapshot_id DESC, path",
            500,
        ),
        (
            "list_files",
            "files",
            (
                "content, file_path, file_format, spec_id, partition, record_count, "
                "file_size_in_bytes, equality_ids, sort_order_id"
            ),
            "file_path",
            1000,
        ),
        (
            "list_refs",
            "refs",
            (
                "name, type, snapshot_id, max_reference_age_in_ms, "
                "min_snapshots_to_keep, max_snapshot_age_in_ms"
            ),
            "name",
            100,
        ),
    ],
)
async def test_inspector_uses_exact_projection_order_and_hard_limit(
    method: str,
    suffix: str,
    projection: str,
    order_by: str,
    limit: int,
) -> None:
    runner = RecordingRunner(
        _query_result(
            tuple(column.strip() for column in projection.split(",")),
            (),
        )
    )
    inspector = IcebergInspector(runner=runner, context=CONTEXT)

    assert await getattr(inspector, method)(TABLE) == ()

    assert runner.sql == (
        f"SELECT {projection} "
        f'FROM "AwsDataCatalog"."analytics"."order-events${suffix}" '
        f"ORDER BY {order_by} LIMIT {limit}"
    )
    assert runner.calls[0][3] == limit


@pytest.mark.asyncio
async def test_inspector_maps_history_manifests_files_and_refs_strictly() -> None:
    cases = (
        (
            "list_history",
            _query_result(
                ("made_current_at", "snapshot_id", "parent_id", "is_current_ancestor"),
                (("2026-07-26 00:00:00.000 UTC", "42", None, "true"),),
            ),
            IcebergHistoryEntry(NOW, 42, None, True),
        ),
        (
            "list_manifests",
            _query_result(
                (
                    "path",
                    "length",
                    "partition_spec_id",
                    "added_snapshot_id",
                    "added_data_files_count",
                    "existing_data_files_count",
                    "deleted_data_files_count",
                    "partition_summaries",
                ),
                (("s3://lake/m.avro", "10", "2", "42", "3", "4", "5", "summary"),),
            ),
            IcebergManifest("s3://lake/m.avro", 10, 2, 42, 3, 4, 5, "summary"),
        ),
        (
            "list_files",
            _query_result(
                (
                    "content",
                    "file_path",
                    "file_format",
                    "spec_id",
                    "partition",
                    "record_count",
                    "file_size_in_bytes",
                    "equality_ids",
                    "sort_order_id",
                ),
                (("0", "s3://lake/f.parquet", "PARQUET", "2", "{day=1}", "7", "8", None, None),),
            ),
            IcebergDataFile(0, "s3://lake/f.parquet", "PARQUET", 2, "{day=1}", 7, 8, None, None),
        ),
        (
            "list_refs",
            _query_result(
                (
                    "name",
                    "type",
                    "snapshot_id",
                    "max_reference_age_in_ms",
                    "min_snapshots_to_keep",
                    "max_snapshot_age_in_ms",
                ),
                (("main", "BRANCH", "42", None, "2", "86400000"),),
            ),
            IcebergReference("main", "BRANCH", 42, None, 2, 86400000),
        ),
    )

    for method, result, expected in cases:
        inspector = IcebergInspector(runner=RecordingRunner(result), context=CONTEXT)
        assert await getattr(inspector, method)(TABLE) == (expected,)


_PARTITION_COLUMNS = (
    "partition",
    "spec_id",
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


@pytest.mark.asyncio
async def test_partitions_derive_dynamic_spec_and_validate_fixed_metrics() -> None:
    result = _query_result(
        _PARTITION_COLUMNS,
        (
            (
                "{event_date=2026-07-26, region_bucket=7}",
                "3",
                "10",
                "2",
                "1024",
                None,
                "0",
                None,
                "0",
                "2026-07-26T00:00:00Z",
                "42",
            ),
        ),
    )
    result = BoundedQueryResult(
        result.detail,
        (
            ResultColumn(
                "partition",
                "row(event_date date, region_bucket integer)",
                "NULLABLE",
            ),
            *result.columns[1:],
        ),
        result.rows,
    )
    runner = RecordingRunner(result)
    inspector = IcebergInspector(runner=runner, context=CONTEXT)

    spec = await inspector.partition_spec(TABLE)
    rows = await inspector.list_partitions(TABLE)

    assert spec == IcebergPartitionSpec(("event_date", "region_bucket"))
    assert rows == (
        IcebergPartition(
            values=(("event_date", "2026-07-26"), ("region_bucket", "7")),
            record_count=10,
            file_count=2,
            total_data_file_size_in_bytes=1024,
            position_delete_record_count=None,
            position_delete_file_count=0,
            equality_delete_record_count=None,
            equality_delete_file_count=0,
            last_updated_at=NOW,
            last_updated_snapshot_id=42,
        ),
    )
    assert runner.calls[0][0].endswith(
        'FROM "AwsDataCatalog"."analytics"."order-events$partitions" LIMIT 500'
    )
    assert all(call[3] == 500 for call in runner.calls)


@pytest.mark.asyncio
async def test_partitions_accept_official_iceberg_positional_struct_rendering() -> None:
    result = _query_result(
        _PARTITION_COLUMNS,
        (
            (
                "{2026-07-26, 7}",
                "3",
                "10",
                "2",
                "1024",
                None,
                "0",
                None,
                "0",
                "2026-07-26T00:00:00Z",
                "42",
            ),
        ),
    )
    result = BoundedQueryResult(
        result.detail,
        (
            ResultColumn(
                "partition",
                "row(event_date date, region_bucket integer)",
                "NULLABLE",
            ),
            *result.columns[1:],
        ),
        result.rows,
    )

    rows = await IcebergInspector(
        runner=RecordingRunner(result),
        context=CONTEXT,
    ).list_partitions(TABLE)

    assert rows[0].values == (
        ("event_date", "2026-07-26"),
        ("region_bucket", "7"),
    )


@pytest.mark.asyncio
async def test_unpartitioned_table_omits_partition_and_spec_id() -> None:
    columns = tuple(
        column for column in _PARTITION_COLUMNS if column not in {"partition", "spec_id"}
    )
    result = _query_result(
        columns,
        (
            (
                "10",
                "2",
                "1024",
                None,
                "0",
                None,
                "0",
                "2026-07-26T00:00:00Z",
                "42",
            ),
        ),
    )
    inspector = IcebergInspector(runner=RecordingRunner(result), context=CONTEXT)

    assert await inspector.partition_spec(TABLE) == IcebergPartitionSpec(())
    assert await inspector.list_partitions(TABLE) == (
        IcebergPartition(
            values=(),
            record_count=10,
            file_count=2,
            total_data_file_size_in_bytes=1024,
            position_delete_record_count=None,
            position_delete_file_count=0,
            equality_delete_record_count=None,
            equality_delete_file_count=0,
            last_updated_at=NOW,
            last_updated_snapshot_id=42,
        ),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("partition_type", "partition_value"),
    [
        ("varchar", "{event_date=2026-07-26}"),
        ("row(event_date date, region_bucket integer)", "{event_date=2026-07-26}"),
        (
            "row(event_date date, region_bucket integer)",
            "{wrong_name=2026-07-26, region_bucket=7}",
        ),
        ("row(event_date date)", "RESULT_VALUE_SECRET"),
    ],
)
async def test_partitions_fail_closed_on_malformed_struct_metadata(
    partition_type: str,
    partition_value: str,
) -> None:
    result = _query_result(
        _PARTITION_COLUMNS,
        (
            (
                partition_value,
                "3",
                "10",
                "2",
                "1024",
                None,
                "0",
                None,
                "0",
                "2026-07-26T00:00:00Z",
                "42",
            ),
        ),
    )
    result = BoundedQueryResult(
        result.detail,
        (
            ResultColumn("partition", partition_type, "NULLABLE"),
            *result.columns[1:],
        ),
        result.rows,
    )

    with pytest.raises(IcebergMetadataShapeError):
        await IcebergInspector(
            runner=RecordingRunner(result),
            context=CONTEXT,
        ).list_partitions(TABLE)


@pytest.mark.asyncio
async def test_partitions_validate_spec_id_without_exposing_it_as_a_partition_field() -> None:
    result = _query_result(
        _PARTITION_COLUMNS,
        (
            (
                "{event_date=2026-07-26}",
                "not-an-integer",
                "10",
                "2",
                "1024",
                None,
                "0",
                None,
                "0",
                "2026-07-26T00:00:00Z",
                "42",
            ),
        ),
    )
    result = BoundedQueryResult(
        result.detail,
        (
            ResultColumn("partition", "row(event_date date)", "NULLABLE"),
            *result.columns[1:],
        ),
        result.rows,
    )

    with pytest.raises(IcebergMetadataShapeError):
        await IcebergInspector(
            runner=RecordingRunner(result),
            context=CONTEXT,
        ).list_partitions(TABLE)


@pytest.mark.asyncio
async def test_partitions_reject_missing_fixed_metric_column() -> None:
    columns = tuple(column for column in _PARTITION_COLUMNS if column != "last_updated_snapshot_id")
    runner = RecordingRunner(_query_result(columns, (tuple("1" for _ in columns),)))
    inspector = IcebergInspector(runner=runner, context=CONTEXT)

    with pytest.raises(
        IcebergMetadataShapeError,
        match="last_updated_snapshot_id",
    ):
        await inspector.list_partitions(TABLE)


@pytest.mark.asyncio
async def test_inspector_rejects_missing_columns_and_invalid_typed_values() -> None:
    missing = RecordingRunner(
        _query_result(
            ("snapshot_id",),
            (("42",),),
        )
    )
    invalid = RecordingRunner(
        _query_result(
            (
                "committed_at",
                "snapshot_id",
                "parent_id",
                "operation",
                "manifest_list",
                "summary",
            ),
            (("not-a-date", "forty-two", None, "append", "path", "{}"),),
        )
    )

    with pytest.raises(IcebergMetadataShapeError, match="committed_at"):
        await IcebergInspector(runner=missing, context=CONTEXT).list_snapshots(TABLE)
    with pytest.raises(IcebergMetadataShapeError, match="metadata value"):
        await IcebergInspector(runner=invalid, context=CONTEXT).list_snapshots(TABLE)


@pytest.mark.asyncio
async def test_metadata_shape_failure_excludes_result_values_from_crash_surfaces(
    tmp_path: Path,
) -> None:
    marker = "RESULT_ROW_SECRET_7F4C2A9D"
    runner = RecordingRunner(
        _query_result(
            (
                "committed_at",
                "snapshot_id",
                "parent_id",
                "operation",
                "manifest_list",
                "summary",
            ),
            (("not-a-date", marker, None, "append", marker, "{}"),),
        )
    )

    with pytest.raises(IcebergMetadataShapeError) as raised:
        await IcebergInspector(runner=runner, context=CONTEXT).list_snapshots(TABLE)

    production_traceback = raised.value.__traceback__
    while (
        production_traceback is not None
        and "/src/aws_tui/" not in production_traceback.tb_frame.f_code.co_filename
    ):
        production_traceback = production_traceback.tb_next
    error = raised.value.with_traceback(production_traceback)
    captured = "".join(
        TracebackException.from_exception(
            error,
            capture_locals=True,
        ).format()
    )
    crash_path = CrashDump(base_dir=tmp_path / "crash").write(exc=error)
    assert marker not in captured
    assert marker not in crash_path.read_text(encoding="utf-8")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "field",
    ["connection_name", "region", "catalog_name", "database_name"],
)
async def test_inspector_rejects_each_cross_context_table_identity_without_query(
    field: str,
) -> None:
    runner = RecordingRunner()
    inspector = IcebergInspector(runner=runner, context=CONTEXT)
    values = {
        "catalog_name": TABLE.catalog_name,
        "database_name": TABLE.database_name,
        "table_name": TABLE.table_name,
        "connection_name": TABLE.connection_name,
        "region": TABLE.region,
    }
    values[field] = f"other-{field}"
    other = TableRef(**values)

    with pytest.raises(ValidationError, match="active query context"):
        await inspector.list_snapshots(other)

    assert runner.calls == []


@pytest.mark.asyncio
async def test_inspector_propagates_runner_cancellation_without_retry() -> None:
    runner = RecordingRunner(error=AthenaQueryCancelledError("Athena query was cancelled"))
    inspector = IcebergInspector(runner=runner, context=CONTEXT)

    with pytest.raises(AthenaQueryCancelledError):
        await inspector.list_snapshots(TABLE)

    assert len(runner.calls) == 1
