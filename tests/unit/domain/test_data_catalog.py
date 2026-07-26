"""Tests for the immutable shared Glue catalog vocabulary."""

from __future__ import annotations

from dataclasses import FrozenInstanceError, fields, is_dataclass
from datetime import UTC, datetime
from itertools import permutations
from typing import get_type_hints

import pytest

from aws_tui.domain.data_catalog import (
    CatalogRef,
    Column,
    ColumnStatistics,
    DatabaseRef,
    DatabaseSummary,
    PartitionSummary,
    StorageDescriptor,
    TableDetail,
    TableFormat,
    TableRef,
    TableSummary,
    detect_table_format,
)

pytestmark = pytest.mark.unit


def test_table_ref_preserves_security_context() -> None:
    ref = TableRef(
        catalog_name="AwsDataCatalog",
        database_name="analytics",
        table_name="events",
        connection_name="prod-west",
        region="us-west-2",
    )
    assert ref.database == DatabaseRef(
        catalog_name="AwsDataCatalog",
        database_name="analytics",
        connection_name="prod-west",
        region="us-west-2",
    )
    assert ref.catalog == CatalogRef("AwsDataCatalog", "prod-west", "us-west-2")


def test_database_ref_exposes_catalog_context() -> None:
    ref = DatabaseRef("AwsDataCatalog", "analytics", "prod-west", "us-west-2")

    assert ref.catalog == CatalogRef("AwsDataCatalog", "prod-west", "us-west-2")


def test_catalog_models_are_frozen() -> None:
    column = Column("event_id", "string", None, False)

    with pytest.raises(FrozenInstanceError):
        column.type_name = "bigint"  # type: ignore[misc]


@pytest.mark.parametrize(
    "model",
    [
        CatalogRef,
        DatabaseRef,
        TableRef,
        Column,
        StorageDescriptor,
        DatabaseSummary,
        TableSummary,
        TableDetail,
        PartitionSummary,
        ColumnStatistics,
    ],
)
def test_catalog_records_are_frozen_dataclasses_with_slots(model: type[object]) -> None:
    assert is_dataclass(model)
    assert hasattr(model, "__dataclass_params__")
    assert model.__dataclass_params__.frozen is True
    assert hasattr(model, "__slots__")


def test_storage_descriptor_keeps_s3_location() -> None:
    storage = StorageDescriptor(
        location="s3://lake/events/",
        input_format="org.apache.hadoop.hive.ql.io.parquet.MapredParquetInputFormat",
        output_format="org.apache.hadoop.hive.ql.io.parquet.MapredParquetOutputFormat",
        serde="org.apache.hadoop.hive.ql.io.parquet.serde.ParquetHiveSerDe",
        compressed=False,
        bucket_count=0,
    )

    assert storage.location == "s3://lake/events/"
    assert TableFormat.OTHER.value == "other"


def test_table_detail_sorts_and_redacts_parameters() -> None:
    summary = TableSummary(
        TableRef("catalog", "db", "table", "conn", "region"), None, None, None, None, None
    )
    detail = TableDetail(
        summary=summary,
        columns=(),
        partition_keys=(),
        storage=StorageDescriptor(None, None, None, None, False, 0),
        classification="provider-text",
        table_format=TableFormat.OTHER,
        parameters=(("zeta", "PARAMETER_SECRET"), ("alpha", "first")),
    )

    assert detail.parameters == (("alpha", "[REDACTED]"), ("zeta", "[REDACTED]"))
    assert "PARAMETER_SECRET" not in repr(detail)
    assert "provider-text" not in repr(detail)


@pytest.mark.parametrize(
    ("parameters", "input_format", "table_type", "expected"),
    [
        ({"table_type": "ICEBERG"}, None, "EXTERNAL_TABLE", TableFormat.ICEBERG),
        ({"tableType": "IceBeRg"}, None, "external_table", TableFormat.ICEBERG),
        ({"classification": "hudi"}, None, "EXTERNAL_TABLE", TableFormat.HUDI),
        ({"spark.sql.sources.provider": "delta"}, None, "EXTERNAL_TABLE", TableFormat.DELTA),
        (
            {"classification": "parquet"},
            "MapredParquetInputFormat",
            "EXTERNAL_TABLE",
            TableFormat.HIVE,
        ),
        ({}, "view-input-format", "VIRTUAL_VIEW", TableFormat.OTHER),
        ({}, None, "VIRTUAL_VIEW", TableFormat.OTHER),
    ],
)
def test_detect_table_format_uses_case_insensitive_conservative_precedence(
    parameters: dict[str, str],
    input_format: str | None,
    table_type: str | None,
    expected: TableFormat,
) -> None:
    assert detect_table_format(parameters, input_format, table_type) is expected


@pytest.mark.parametrize(
    "items",
    tuple(
        permutations(
            (
                ("Classification", " delta "),
                ("classification", " HUDI "),
                (" CLASSIFICATION ", " IceBeRg "),
            )
        )
    ),
)
def test_detect_table_format_aggregates_duplicate_casefold_keys_in_any_order(
    items: tuple[tuple[str, str], ...],
) -> None:
    assert detect_table_format(dict(items), None, None) is TableFormat.ICEBERG


@pytest.mark.parametrize(
    "items",
    tuple(
        permutations(
            (
                ("spark.sql.sources.provider", "delta"),
                ("tableType", "hudi"),
            )
        )
    ),
)
def test_detect_table_format_hudi_beats_delta_in_any_order(
    items: tuple[tuple[str, str], ...],
) -> None:
    assert detect_table_format(dict(items), None, None) is TableFormat.HUDI


@pytest.mark.parametrize(
    ("parameters", "input_format", "table_type", "expected"),
    [
        ({" table_type ": " iceberg "}, None, None, TableFormat.ICEBERG),
        ({" TABLETYPE ": " hudi "}, None, None, TableFormat.HUDI),
        ({" Classification ": " delta "}, None, None, TableFormat.DELTA),
        ({" SPARK.SQL.SOURCES.PROVIDER ": " iceberg "}, None, None, TableFormat.ICEBERG),
        ({" provider ": " hudi "}, None, None, TableFormat.HUDI),
        ({"classification": "delta"}, None, " hudi ", TableFormat.HUDI),
        ({"classification": "hudi"}, None, " iceberg ", TableFormat.ICEBERG),
        ({}, " \t ", " VIRTUAL_VIEW ", TableFormat.OTHER),
        ({}, " parquet-input ", " VIRTUAL_VIEW ", TableFormat.OTHER),
        ({}, " \t ", " EXTERNAL_TABLE ", TableFormat.HIVE),
        ({}, " parquet-input ", None, TableFormat.HIVE),
    ],
)
def test_detect_table_format_normalizes_exact_markers_and_physical_fallback(
    parameters: dict[str, str],
    input_format: str | None,
    table_type: str | None,
    expected: TableFormat,
) -> None:
    assert detect_table_format(parameters, input_format, table_type) is expected


@pytest.mark.parametrize(
    ("parameters", "input_format", "table_type"),
    [
        ({"classification": "iceberg-v2"}, None, None),
        ({"provider": "s3://warehouse/iceberg"}, None, None),
        ({"table_type": "not_iceberg"}, None, None),
        ({"location": "s3://warehouse/iceberg"}, None, None),
        ({}, None, "MY_EXTERNAL_TABLE"),
    ],
)
def test_detect_table_format_does_not_match_substrings_or_paths(
    parameters: dict[str, str],
    input_format: str | None,
    table_type: str | None,
) -> None:
    assert detect_table_format(parameters, input_format, table_type) is TableFormat.OTHER


def test_column_statistics_sorts_values_by_key() -> None:
    statistics = ColumnStatistics(
        column_name="event_id",
        type_name="string",
        analyzed_at=datetime(2026, 7, 25, tzinfo=UTC),
        values=(("zeta", "2"), ("alpha", "1")),
    )

    assert statistics.values == (("alpha", "1"), ("zeta", "2"))


def test_catalog_records_preserve_summary_and_partition_fields() -> None:
    now = datetime(2026, 7, 25, tzinfo=UTC)
    database = DatabaseRef("catalog", "analytics", "conn", "us-east-1")
    table = TableRef("catalog", "analytics", "events", "conn", "us-east-1")
    column = Column("event_date", "date", "partition date", True)

    database_summary = DatabaseSummary(database, "analytics data", "s3://lake/", now)
    table_summary = TableSummary(table, "events data", "owner", "EXTERNAL_TABLE", now, now)
    partition = PartitionSummary(("2026-07-25",), now, None, "s3://lake/events/2026-07-25/")

    assert database_summary.created_at == now
    assert table_summary.ref == table
    assert partition.values == ("2026-07-25",)
    assert column.partition_key is True


def test_public_types_are_exported() -> None:
    from aws_tui.domain import data_catalog

    assert data_catalog.__all__ == [
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


def test_record_field_order_matches_catalog_contract() -> None:
    assert [field.name for field in fields(CatalogRef)] == [
        "catalog_name",
        "connection_name",
        "region",
    ]
    assert [field.name for field in fields(TableDetail)] == [
        "summary",
        "columns",
        "partition_keys",
        "storage",
        "classification",
        "table_format",
        "parameters",
    ]
    assert list(get_type_hints(TableDetail).items()) == [
        ("summary", TableSummary),
        ("columns", tuple[Column, ...]),
        ("partition_keys", tuple[Column, ...]),
        ("storage", StorageDescriptor),
        ("classification", str | None),
        ("table_format", TableFormat),
        ("parameters", tuple[tuple[str, str], ...]),
    ]
