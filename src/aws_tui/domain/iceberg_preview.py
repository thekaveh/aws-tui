"""Build the one statement the Iceberg preview pane runs.

This module is the only producer of SQL for the preview. No user-entered text
reaches the DuckDB port, so there is no SQL policy here and none is needed --
see the design spec's non-goals. Keep it that way: if an editor is ever added,
it gets a read-only policy of its own first.
"""

from __future__ import annotations

from typing import Final

ROW_LIMIT_STEPS: Final[tuple[int, ...]] = (100, 1000, 10000)
"""Selectable row limits, ascending.

The ceiling matches Athena's ``_MAX_RESULT_ROWS``
(``src/aws_tui/vm/athena/results_vm.py:55``) so one engine cannot quietly
return far more rows into the same kind of table than the other.
"""

__all__ = ["ROW_LIMIT_STEPS", "iceberg_preview_sql", "next_row_limit"]


def next_row_limit(limit: int) -> int | None:
    """The next larger step, or ``None`` when ``limit`` is already the largest."""
    for step in ROW_LIMIT_STEPS:
        if step > limit:
            return step
    return None


def _quote_literal(value: str) -> str:
    """Single-quote a SQL string literal, doubling embedded quotes."""
    escaped = value.replace("'", "''")
    return f"'{escaped}'"


def iceberg_preview_sql(
    location: str,
    *,
    limit: int,
    snapshot_id: int | None = None,
) -> str:
    """Build the preview scan for one Iceberg table root.

    ``location`` is the table's physical S3 root, not a metadata file: the
    exact ``metadata_location`` pointer is unavailable because
    ``TableDetail.__post_init__`` redacts every table parameter
    (``src/aws_tui/domain/data_catalog.py:145-154``). The caller therefore
    enables version guessing so DuckDB finds the newest metadata itself.
    """
    if not location.strip() or not location.startswith("s3://"):
        raise ValueError("preview requires an s3:// location")
    if isinstance(limit, bool) or not isinstance(limit, int) or limit not in ROW_LIMIT_STEPS:
        raise ValueError(f"row limit must be one of {ROW_LIMIT_STEPS}")
    scan_args = _quote_literal(location)
    if snapshot_id is not None:
        if isinstance(snapshot_id, bool) or not isinstance(snapshot_id, int):
            raise ValueError("snapshot ID must be a non-negative integer")
        if snapshot_id < 0:
            raise ValueError("snapshot ID must be a non-negative integer")
        scan_args = f"{scan_args}, snapshot_from_id := {snapshot_id}"
    return f"SELECT * FROM iceberg_scan({scan_args}) LIMIT {limit}"
