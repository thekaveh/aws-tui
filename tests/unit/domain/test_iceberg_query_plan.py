"""The displayed bounded metadata plan is the statement actually executed."""

from __future__ import annotations

import pytest

from aws_tui.domain import iceberg
from aws_tui.domain.filesystem import ValidationError
from tests.unit.domain.test_iceberg import CONTEXT, TABLE, RecordingRunner


@pytest.mark.parametrize(
    ("collection", "row_limit"),
    [
        ("snapshots", 100),
        ("history", 100),
        ("manifests", 500),
        ("files", 1000),
        ("partitions", 500),
        ("refs", 100),
    ],
)
async def test_plan_preserves_identity_and_exact_executed_lookahead(collection, row_limit):
    assert hasattr(iceberg, "metadata_query_plan"), "No pre-query metadata plan exists"
    plan = iceberg.metadata_query_plan(TABLE, collection)
    runner = RecordingRunner(error=ValidationError("controlled provider boundary"))
    inspector = iceberg.IcebergInspector(runner=runner, context=CONTEXT)
    assert runner.calls == []
    assert plan.table_ref == TABLE
    assert plan.collection == collection
    assert plan.row_limit == row_limit
    assert plan.request_limit == row_limit + 1
    assert plan.sql.endswith(f"LIMIT {row_limit + 1}")
    with pytest.raises(ValidationError, match="controlled provider boundary"):
        await getattr(inspector, f"list_{collection}")(TABLE)
    assert runner.sql == plan.sql
    assert runner.calls[0][3] == plan.request_limit


def test_unknown_metadata_collection_cannot_be_interpolated_into_sql():
    assert hasattr(iceberg, "metadata_query_plan"), "No pre-query metadata plan exists"
    with pytest.raises(ValueError, match="Unknown Iceberg metadata collection"):
        iceberg.metadata_query_plan(TABLE, "files; DELETE FROM table")
