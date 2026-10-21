"""Loaded Iceberg metadata is explained without inventing missing evidence."""

from __future__ import annotations

import importlib
import importlib.util
from dataclasses import replace
from datetime import UTC, datetime

import pytest

from aws_tui.domain.data_catalog import TableRef
from aws_tui.domain.iceberg import (
    IcebergCoverage,
    IcebergDataFile,
    IcebergHistoryEntry,
    IcebergPartition,
    IcebergReference,
    IcebergSnapshot,
)

NOW = datetime(2026, 10, 21, 12, tzinfo=UTC)
REF = TableRef("AwsDataCatalog", "db", "ice", "fixture", "eu-west-1")


@pytest.fixture
def interpretation():
    assert importlib.util.find_spec("aws_tui.domain.iceberg_interpretation") is not None, (
        "The loaded-metadata interpretation layer is missing"
    )
    return importlib.import_module("aws_tui.domain.iceberg_interpretation")


def snapshot(identity=10, parent=None, summary=()):
    return IcebergSnapshot(NOW, identity, parent, "append", "s3://fixture/manifest", summary)


def metrics(detail):
    return {m.key: m for m in detail.metrics}


@pytest.mark.parametrize("value", [None, "", "oops", "NaN", "1.5", "-1", "9" * 10000])
def test_missing_or_malformed_metric_is_unknown_and_preserves_raw(interpretation, value):
    summary = () if value is None else (("total-records", value),)
    detail = interpretation.explain_snapshot(snapshot(summary=summary))
    metric = metrics(detail)["total-records"]
    assert metric.value is None
    assert metric.raw_values == (() if value is None else (value,))
    assert metric.unit == "records"
    assert detail.committed_at == NOW
    assert detail.parent_id is None
    assert detail.operation == "append"


def test_zero_is_known_and_arbitrary_property_remains_raw(interpretation):
    summary = (("total-records", "0"), ("private.metric", "12"))
    detail = interpretation.explain_snapshot(snapshot(summary=summary))
    known, custom = metrics(detail)["total-records"], metrics(detail)["private.metric"]
    assert known.value == 0
    assert custom.raw_values == ("12",)
    assert custom.value is None
    assert custom.unit is None
    assert "unknown" in custom.explanation.lower()


@pytest.mark.parametrize("key", ["source-snapshot-id", "wap.id", "partition-summaries-included"])
def test_numeric_looking_identifiers_and_flags_are_not_quantity_deltas(interpretation, key):
    left = snapshot(summary=((key, "10"),))
    right = snapshot(20, 10, ((key, "12"),))
    row = metrics(interpretation.compare_snapshots(REF, left, right))[key]
    assert row.left.raw_values == ("10",)
    assert row.right.raw_values == ("12",)
    assert row.delta is None
    assert row.unit is None


def test_duplicate_key_is_ambiguous_not_last_writer_wins(interpretation):
    detail = interpretation.explain_snapshot(
        snapshot(summary=(("total-records", "3"), ("total-records", "8")))
    )
    metric = metrics(detail)["total-records"]
    assert metric.raw_values == ("3", "8")
    assert metric.value is None


def test_comparison_deltas_are_right_minus_left_with_grounded_units(interpretation):
    left = snapshot(summary=(("total-records", "12"), ("total-files-size", "200")))
    right = snapshot(20, 10, (("total-records", "8"), ("total-files-size", "220")))
    compared = interpretation.compare_snapshots(REF, left, right, snapshots=(left, right))
    rows = metrics(compared)
    assert compared.table_ref == REF
    assert (rows["total-records"].delta, rows["total-records"].unit) == (-4, "records")
    assert (rows["total-files-size"].delta, rows["total-files-size"].unit) == (20, "bytes")
    assert rows["total-data-files"].delta is None
    assert compared.left.snapshot_id == 10
    assert compared.right.snapshot_id == 20
    assert compared.ancestry.relationship == "left_ancestor"
    assert compared.ancestry.path == (10, 20)


@pytest.mark.parametrize("other", [(), (("total-records", "bad"),)])
def test_comparison_does_not_substitute_zero_for_one_missing_side(interpretation, other):
    left = snapshot(summary=(("total-records", "12"),))
    right = snapshot(20, 10, other)
    compared = interpretation.compare_snapshots(REF, left, right)
    row = metrics(compared)["total-records"]
    assert row.left.value == 12
    assert row.right.value is None
    assert row.delta is None


def test_ancestry_requires_actual_intermediate_links_not_chronological_order(interpretation):
    left, right = snapshot(), snapshot(30, 20)
    gap = interpretation.compare_snapshots(REF, left, right, snapshots=(left, right))
    assert gap.ancestry.relationship == "unresolved"
    assert "20" in gap.ancestry.reason
    middle = IcebergHistoryEntry(NOW, 20, 10, True)
    linked = interpretation.compare_snapshots(
        REF, left, right, snapshots=(left, right), history=(middle,)
    )
    assert linked.ancestry.relationship == "left_ancestor"
    assert linked.ancestry.path == (10, 20, 30)
    reverse = interpretation.compare_snapshots(
        REF, right, left, snapshots=(left, right), history=(middle,)
    )
    assert reverse.ancestry.relationship == "right_ancestor"


@pytest.mark.parametrize("bad_parent", [30, 99])
def test_ancestry_cycles_or_conflicts_are_unresolved(interpretation, bad_parent):
    left, right = snapshot(), snapshot(30, 20)
    middle = snapshot(20, bad_parent)
    history = (IcebergHistoryEntry(NOW, 20, 10, True),) if bad_parent == 99 else ()
    compared = interpretation.compare_snapshots(
        REF, left, right, snapshots=(left, middle, right), history=history
    )
    assert compared.ancestry.relationship == "unresolved"


@pytest.mark.parametrize(
    ("code", "label"),
    [(0, "data"), (1, "position delete"), (2, "equality delete"), (999, "unknown")],
)
def test_content_explanation_keeps_raw_code(interpretation, code, label):
    text = interpretation.content_label(code)
    assert str(code) in text
    assert label in text.lower()


def files():
    return tuple(
        IcebergDataFile(content, f"s3://fixture/{i}", "PARQUET", 0, None, 10, size, None, 0)
        for i, (content, size) in enumerate(((0, 10), (1, 30), (2, 90)))
    )


@pytest.mark.parametrize("status", ["complete", "truncated", "unknown"])
def test_current_file_distribution_keeps_sample_denominator_cap_and_time(interpretation, status):
    coverage = IcebergCoverage(status, 1000, "files", REF)
    report = interpretation.current_observation(REF, "files", files(), coverage, NOW)
    assert report.table_ref == REF
    assert report.collection == "files"
    assert report.sample_size == 3
    assert report.denominator == (3 if status == "complete" else None)
    assert report.row_limit == 1000
    assert report.coverage == status
    assert report.observed_at == NOW
    assert (report.size.minimum, report.size.p50, report.size.p95, report.size.maximum) == (
        10,
        30,
        90,
        90,
    )
    assert report.size.total == 130
    counts = {c.source: c for c in report.counters}
    assert counts["files.content=1"].value == 1
    assert counts["files.content=2"].value == 1


@pytest.mark.parametrize("count", [0, 1, 1000])
def test_empty_and_at_cap_samples_are_not_extrapolated(interpretation, count):
    rows = tuple(replace(files()[0], file_path=f"s3://fixture/{i}") for i in range(count))
    report = interpretation.current_observation(
        REF, "files", rows, IcebergCoverage("truncated", 1000, "files", REF), NOW
    )
    assert report.sample_size == count
    assert report.denominator is None
    assert report.size.total == (count * 10 if count else None)
    assert report.size.minimum == (10 if count else None)


def test_partition_delete_counters_report_known_rows_not_missing_as_zero(interpretation):
    first = IcebergPartition((("day", "one"),), 10, 1, 100, None, None, 0, 0, NOW, 10)
    second = replace(first, values=(("day", "two"),), position_delete_record_count=4)
    report = interpretation.current_observation(
        REF,
        "partitions",
        (first, second),
        IcebergCoverage("complete", 500, "partitions", REF),
        NOW,
    )
    counters = {c.source: c for c in report.counters}
    partial = counters["partitions.position_delete_record_count"]
    assert (partial.value, partial.known_rows, partial.sample_size, partial.unit) == (
        4,
        1,
        2,
        "records",
    )
    assert counters["partitions.position_delete_file_count"].value is None
    assert counters["partitions.equality_delete_file_count"].value == 0
    assert counters["partitions.equality_delete_file_count"].known_rows == 2


def test_partition_file_counts_have_their_own_distribution(interpretation):
    first = IcebergPartition((("day", "one"),), 10, 1, 100, None, None, 0, 0, NOW, 10)
    parts = tuple(
        replace(first, values=(("day", str(index)),), file_count=count)
        for index, count in enumerate((1, 7, 10))
    )
    report = interpretation.current_observation(
        REF, "partitions", parts, IcebergCoverage("complete", 500, "partitions", REF), NOW
    )
    distribution = report.file_count_distribution
    assert distribution.known_rows == 3
    assert distribution.total == 18
    assert (distribution.minimum, distribution.p50, distribution.p95, distribution.maximum) == (
        1,
        7,
        10,
        10,
    )


def test_retention_explains_raw_units_without_guessing_expiry(interpretation):
    ref = IcebergReference("branch", "BRANCH", 10, 86_400_000, 2, None)
    fields = {x.field: x for x in interpretation.explain_reference(ref)}
    age = fields["max_reference_age_in_ms"]
    assert age.raw_value == 86_400_000
    assert age.unit == "milliseconds"
    assert "day" in age.explanation
    assert "1" in age.explanation
    assert "unknown" in fields["max_snapshot_age_in_ms"].explanation.lower()
    main = {x.field: x for x in interpretation.explain_reference(replace(ref, name="main"))}
    assert "does not expire" in main["max_reference_age_in_ms"].explanation.lower()


def test_tag_branch_retention_fields_are_not_applicable_not_missing_defaults(interpretation):
    tag = IcebergReference("release", "TAG", 10, 86_400_000, None, None)
    fields = {x.field: x for x in interpretation.explain_reference(tag)}
    for key in ("min_snapshots_to_keep", "max_snapshot_age_in_ms"):
        assert fields[key].raw_value is None
        assert "not applicable" in fields[key].explanation.lower()
        assert "unknown" not in fields[key].explanation.lower()


@pytest.mark.parametrize("count", [0, 1, 500])
@pytest.mark.parametrize("status", ["complete", "truncated", "unknown"])
def test_partition_samples_at_and_below_limit_preserve_coverage(interpretation, count, status):
    row = IcebergPartition((("day", "one"),), 10, 1, 100, None, None, 0, 0, NOW, 10)
    rows = tuple(replace(row, values=(("day", str(i)),)) for i in range(count))
    report = interpretation.current_observation(
        REF, "partitions", rows, IcebergCoverage(status, 500, "partitions", REF), NOW
    )
    assert report.sample_size == count
    assert report.denominator == (count if status == "complete" else None)
    assert report.row_limit == 500
    assert report.observed_at == NOW
    assert report.size.total == (count * 100 if count else None)
    assert report.file_count_distribution.total == (count if count else None)
    counters = {c.source: c for c in report.counters}
    assert counters["partitions.position_delete_file_count"].value is None
    assert counters["partitions.equality_delete_record_count"].value == (0 if count else None)
    assert counters["partitions.equality_delete_record_count"].known_rows == count
