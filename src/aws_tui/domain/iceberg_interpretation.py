"""Explain loaded Iceberg v2 metadata without fetching or reconstructing data."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal

from aws_tui.domain.data_catalog import TableRef
from aws_tui.domain.iceberg import (
    IcebergCoverage,
    IcebergDataFile,
    IcebergHistoryEntry,
    IcebergPartition,
    IcebergReference,
    IcebergSnapshot,
)

# Iceberg 1.4.2 SnapshotSummary.java. Numeric-looking IDs and arbitrary
# properties deliberately have no inferred quantity or unit.
_METRICS = {
    "added-data-files": ("files", "Data files added by this snapshot"),
    "deleted-data-files": ("files", "Data files removed by this snapshot"),
    "total-data-files": ("files", "Live data files in this snapshot"),
    "added-delete-files": ("files", "Delete files added by this snapshot"),
    "removed-delete-files": ("files", "Delete files removed by this snapshot"),
    "total-delete-files": ("files", "Live delete files in this snapshot"),
    "added-equality-delete-files": ("files", "Equality-delete files added"),
    "removed-equality-delete-files": ("files", "Equality-delete files removed"),
    "added-position-delete-files": ("files", "Position-delete files added"),
    "removed-position-delete-files": ("files", "Position-delete files removed"),
    "added-records": ("records", "Records in added data files, before applying deletes"),
    "deleted-records": ("records", "Records in removed data files"),
    "total-records": ("records", "Live data-file records before applying delete files"),
    "added-files-size": ("bytes", "Size of added data and delete files"),
    "removed-files-size": ("bytes", "Size of removed data and delete files"),
    "total-files-size": ("bytes", "Size of live data and delete files"),
    "added-position-deletes": ("deletes", "Position-delete records added"),
    "removed-position-deletes": ("deletes", "Position-delete records removed"),
    "total-position-deletes": ("deletes", "Live position-delete records"),
    "added-equality-deletes": ("deletes", "Equality-delete records added"),
    "removed-equality-deletes": ("deletes", "Equality-delete records removed"),
    "total-equality-deletes": ("deletes", "Live equality-delete records"),
    "deleted-duplicate-files": ("files", "Duplicate files removed by this snapshot"),
    "changed-partition-count": ("partitions", "Partitions changed by this snapshot"),
}
_CORE_METRICS = (
    "total-data-files",
    "total-delete-files",
    "total-records",
    "total-files-size",
    "total-position-deletes",
    "total-equality-deletes",
)
_COUNTER_TEXT = re.compile(r"[0-9]{1,19}\Z")


def _counter(value: object) -> int | None:
    if type(value) is int:
        return value if 0 <= value <= 2**63 - 1 else None
    if type(value) is str and _COUNTER_TEXT.fullmatch(value.strip()):
        number = int(value.strip())
        return number if number <= 2**63 - 1 else None
    return None


@dataclass(frozen=True, slots=True)
class SummaryMetric:
    key: str
    raw_values: tuple[str, ...] = field(repr=False)
    value: int | None
    unit: str | None
    explanation: str


@dataclass(frozen=True, slots=True)
class SnapshotExplanation:
    snapshot_id: int | None
    committed_at: datetime | None
    parent_id: int | None
    operation: str | None
    metrics: tuple[SummaryMetric, ...] = field(repr=False)


def _metric(key: str, values: tuple[str, ...]) -> SummaryMetric:
    unit, description = _METRICS.get(key, (None, "Unknown summary key; unit not defined"))
    number = _counter(values[0]) if len(values) == 1 and unit is not None else None
    if len(values) > 1:
        description += "; unknown because duplicate values are ambiguous"
    elif number is None and unit is not None:
        description += "; unknown because the value is missing or invalid"
    return SummaryMetric(key, values, number, unit, description)


def explain_snapshot(snapshot: IcebergSnapshot) -> SnapshotExplanation:
    raw: dict[str, list[str]] = {}
    for key, value in snapshot.summary:
        raw.setdefault(key, []).append(value)
    keys = dict.fromkeys((*_CORE_METRICS, *raw))
    return SnapshotExplanation(
        _counter(snapshot.snapshot_id),
        snapshot.committed_at if type(snapshot.committed_at) is datetime else None,
        _counter(snapshot.parent_id),
        snapshot.operation
        if snapshot.operation in {"append", "delete", "overwrite", "replace"}
        else None,
        tuple(_metric(key, tuple(raw.get(key, ()))) for key in keys),
    )


@dataclass(frozen=True, slots=True)
class MetricDelta:
    key: str
    left: SummaryMetric = field(repr=False)
    right: SummaryMetric = field(repr=False)
    delta: int | None
    unit: str | None


@dataclass(frozen=True, slots=True)
class SnapshotAncestry:
    relationship: Literal["same", "left_ancestor", "right_ancestor", "unresolved"]
    path: tuple[int, ...]
    reason: str


@dataclass(frozen=True, slots=True)
class SnapshotComparison:
    table_ref: TableRef = field(repr=False)
    left: SnapshotExplanation = field(repr=False)
    right: SnapshotExplanation = field(repr=False)
    metrics: tuple[MetricDelta, ...] = field(repr=False)
    ancestry: SnapshotAncestry


def _ancestry(
    left: int,
    right: int,
    snapshots: tuple[IcebergSnapshot, ...],
    history: tuple[IcebergHistoryEntry, ...],
) -> SnapshotAncestry:
    parents: dict[int, set[int | None]] = {}
    records: tuple[IcebergSnapshot | IcebergHistoryEntry, ...] = (*snapshots, *history)
    for row in records:
        parents.setdefault(row.snapshot_id, set()).add(row.parent_id)

    def walk(start: int) -> tuple[list[int], str, bool]:
        path: list[int] = []
        current = start
        while current not in path:
            path.append(current)
            options = parents.get(current, set())
            if not options:
                return path, f"Unresolved: no loaded parent record for snapshot {current}", False
            if len(options) != 1:
                return path, f"Unresolved: conflicting parents for snapshot {current}", True
            parent = next(iter(options))
            if parent is None:
                return path, f"Unresolved: no parent link beyond snapshot {current}", False
            current = parent
        return path, f"Unresolved: parent cycle at snapshot {current}", True

    if left == right:
        return SnapshotAncestry("same", (left,), "The same snapshot ID was selected twice")
    from_right, right_reason, right_invalid = walk(right)
    from_left, left_reason, left_invalid = walk(left)
    if right_invalid or left_invalid:
        return SnapshotAncestry("unresolved", (), right_reason if right_invalid else left_reason)
    if left in from_right:
        path = tuple(reversed(from_right[: from_right.index(left) + 1]))
        return SnapshotAncestry("left_ancestor", path, "Supported by loaded parent/history links")
    if right in from_left:
        path = tuple(reversed(from_left[: from_left.index(right) + 1]))
        return SnapshotAncestry("right_ancestor", path, "Supported by loaded parent/history links")
    return SnapshotAncestry("unresolved", (), right_reason)


def compare_snapshots(
    table_ref: TableRef,
    left: IcebergSnapshot,
    right: IcebergSnapshot,
    *,
    snapshots: tuple[IcebergSnapshot, ...] = (),
    history: tuple[IcebergHistoryEntry, ...] = (),
) -> SnapshotComparison:
    a, b = explain_snapshot(left), explain_snapshot(right)
    a_metrics, b_metrics = ({m.key: m for m in side.metrics} for side in (a, b))
    rows: list[MetricDelta] = []
    for key in dict.fromkeys((*a_metrics, *b_metrics)):
        x, y = a_metrics.get(key, _metric(key, ())), b_metrics.get(key, _metric(key, ()))
        delta = y.value - x.value if x.value is not None and y.value is not None else None
        rows.append(MetricDelta(key, x, y, delta, x.unit))
    ancestry = _ancestry(left.snapshot_id, right.snapshot_id, (*snapshots, left, right), history)
    return SnapshotComparison(table_ref, a, b, tuple(rows), ancestry)


def content_label(code: int) -> str:
    labels = {0: "data file", 1: "position delete", 2: "equality delete"}
    return f"{code} · {labels.get(code, 'unknown') if type(code) is int else 'unknown'}"


@dataclass(frozen=True, slots=True)
class ObservedDistribution:
    known_rows: int
    total: int | None
    minimum: int | None
    p50: int | None
    p95: int | None
    maximum: int | None


def _distribution(values: tuple[int | None, ...]) -> ObservedDistribution:
    known = sorted(v for v in values if v is not None)
    count = len(known)
    if not count:
        return ObservedDistribution(0, None, None, None, None, None)
    return ObservedDistribution(
        count,
        sum(known),
        known[0],
        known[(count + 1) // 2 - 1],
        known[(95 * count + 99) // 100 - 1],
        known[-1],
    )


@dataclass(frozen=True, slots=True)
class ObservationCounter:
    source: str
    value: int | None
    known_rows: int
    sample_size: int
    unit: str


@dataclass(frozen=True, slots=True)
class ObservationSummary:
    table_ref: TableRef = field(repr=False)
    collection: Literal["files", "partitions"]
    sample_size: int
    denominator: int | None
    row_limit: int | None
    coverage: str
    observed_at: datetime | None
    size: ObservedDistribution
    file_count_distribution: ObservedDistribution | None
    counters: tuple[ObservationCounter, ...]


def current_observation(
    table_ref: TableRef,
    collection: Literal["files", "partitions"],
    rows: tuple[IcebergDataFile, ...] | tuple[IcebergPartition, ...],
    coverage: IcebergCoverage | None,
    observed_at: datetime | None,
) -> ObservationSummary:
    if coverage is not None and (
        coverage.table_ref != table_ref or coverage.collection != collection
    ):
        raise ValueError("Observation coverage belongs to another table or collection")
    counters: list[ObservationCounter] = []
    sizes: list[int | None] = []
    file_counts: ObservedDistribution | None = None
    if collection == "files":
        files = tuple(row for row in rows if isinstance(row, IcebergDataFile))
        if len(files) != len(rows):
            raise ValueError("Observation rows do not match the collection")
        sizes = [_counter(row.file_size_in_bytes) for row in files]
        for code in (0, 1, 2, None):
            selected = tuple(
                r
                for r in files
                if (r.content == code if code is not None else r.content not in {0, 1, 2})
            )
            counters.append(
                ObservationCounter(
                    f"files.content={code if code is not None else 'unknown'}",
                    len(selected),
                    len(files),
                    len(files),
                    "files",
                )
            )
            values = [_counter(r.record_count) for r in selected]
            known = [v for v in values if v is not None]
            counters.append(
                ObservationCounter(
                    f"files.record_count[content={code if code is not None else 'unknown'}]",
                    sum(known) if known else None,
                    len(known),
                    len(selected),
                    "records",
                )
            )
    elif collection == "partitions":
        parts = tuple(row for row in rows if isinstance(row, IcebergPartition))
        if len(parts) != len(rows):
            raise ValueError("Observation rows do not match the collection")
        sizes = [_counter(row.total_data_file_size_in_bytes) for row in parts]
        file_counts = _distribution(tuple(_counter(row.file_count) for row in parts))
        for name, unit in (
            ("file_count", "files"),
            ("record_count", "records"),
            ("position_delete_record_count", "records"),
            ("position_delete_file_count", "files"),
            ("equality_delete_record_count", "records"),
            ("equality_delete_file_count", "files"),
        ):
            values = [_counter(getattr(row, name)) for row in parts]
            known = [v for v in values if v is not None]
            counters.append(
                ObservationCounter(
                    f"partitions.{name}",
                    sum(known) if known else None,
                    len(known),
                    len(parts),
                    unit,
                )
            )
    else:
        raise ValueError("Only current files and partitions have observation summaries")
    status = coverage.status if coverage is not None else "unknown"
    return ObservationSummary(
        table_ref,
        collection,
        len(rows),
        len(rows) if status == "complete" else None,
        coverage.row_limit if coverage is not None else None,
        status,
        observed_at,
        _distribution(tuple(sizes)),
        file_counts,
        tuple(counters),
    )


@dataclass(frozen=True, slots=True)
class ReferenceExplanation:
    field: str
    raw_value: int | None
    unit: str
    explanation: str


def _duration(milliseconds: int) -> str:
    remaining = milliseconds
    parts: list[str] = []
    for size, unit in (
        (86_400_000, "day"),
        (3_600_000, "hour"),
        (60_000, "minute"),
        (1000, "second"),
        (1, "millisecond"),
    ):
        count, remaining = divmod(remaining, size)
        if count:
            parts.append(f"{count} {unit}{'s' if count != 1 else ''}")
    return ", ".join(parts) or "0 milliseconds"


def explain_reference(reference: IcebergReference) -> tuple[ReferenceExplanation, ...]:
    rows: list[ReferenceExplanation] = []
    for name, description in (
        ("max_reference_age_in_ms", "Maximum reference age during snapshot expiration"),
        ("min_snapshots_to_keep", "Minimum snapshots retained for this branch"),
        (
            "max_snapshot_age_in_ms",
            "Branch snapshot age limit, combined with its minimum retained count",
        ),
    ):
        raw = getattr(reference, name)
        value = _counter(raw)
        unit = "snapshots" if name == "min_snapshots_to_keep" else "milliseconds"
        if reference.ref_type == "TAG" and name != "max_reference_age_in_ms":
            text = "Not applicable to tag references; this retention setting is for branches"
        elif name == "max_reference_age_in_ms" and reference.name == "main":
            text = "The main reference does not expire; this raw age is not an expiry deadline"
        elif value is None or value == 0:
            text = f"{description}: unknown; table defaults were not loaded"
        elif unit == "milliseconds":
            text = f"{description}: {_duration(value)}; not a scheduled deletion time"
        else:
            text = f"{description}: {value} snapshots; evaluated together with snapshot age"
        rows.append(ReferenceExplanation(name, raw, unit, text))
    return tuple(rows)
