from dataclasses import replace

from aws_tui.domain.iceberg import IcebergCoverage, IcebergReference
from aws_tui.domain.iceberg_interpretation import (
    compare_snapshots,
    current_observation,
    explain_snapshot,
)
from aws_tui.ui.widgets.glue.iceberg_info import (
    comparison_text,
    observation_text,
    reference_text,
    snapshot_text,
)
from tests.unit.vm.glue.test_iceberg_vm import ICEBERG_REF, NOW, RecordingInspector, _snapshot


def test_detail_preserves_literal_raw_values_and_unknown_metrics():
    row = replace(_snapshot(41), summary=(("added-records", "bad"), ("custom", "[red]")))
    text = snapshot_text(explain_snapshot(row))
    assert "Historical snapshot summary" in text
    assert "Committed" in text
    assert "Parent: 40" in text
    assert "Operation: append" in text
    assert "Raw: 'bad'" in text
    assert "Raw: '[red]'" in text
    assert "unknown records" in text


def test_comparison_keeps_both_raw_values_signed_delta_and_gap():
    a = replace(_snapshot(1), summary=(("added-records", "10"),))
    b = replace(_snapshot(3), summary=(("added-records", "7"),))
    text = comparison_text(compare_snapshots(ICEBERG_REF, a, b))
    assert "A: 1" in text
    assert "B: 3" in text
    assert "-3 records" in text
    assert "B - A" in text
    assert "Raw A: '10'" in text
    assert "Raw B: '7'" in text
    assert "unresolved" in text


def test_current_observation_reports_coverage_distributions_and_delete_units():
    source = RecordingInspector()
    summary = current_observation(
        ICEBERG_REF,
        "partitions",
        source.partitions,
        IcebergCoverage("truncated", 500, "partitions", ICEBERG_REF),
        NOW,
    )
    text = observation_text(summary, pinned_snapshot=41)
    for expected in (
        "CURRENT observations",
        "Historical snapshot A: 41",
        "Sample: 1",
        "Denominator: unknown",
        "cap: 500",
        "truncated",
        "2026",
        "p50",
        "bytes",
        "file_count",
        "position_delete",
        "equality_delete",
        "1/1",
    ):
        assert expected in text


def test_reference_keeps_raw_units_and_tag_semantics():
    row = IcebergReference("tag1", "TAG", 41, 86400000, 2, 1000)
    text = reference_text(row)
    assert "86400000 milliseconds" in text
    assert "1 day" in text
    assert "Not applicable" in text
    assert "Raw: 2 snapshots" in text


async def test_full_identity_is_in_scrollable_body_for_long_table_names():
    from textual.app import App
    from textual.widgets import TextArea

    from aws_tui.ui.widgets.glue.iceberg_info import IcebergInfoModal

    identity = (
        "Connection: "
        + "c" * 100
        + "\\nRegion: us-east-1\\nCatalog: "
        + "a" * 100
        + "\\nDatabase: "
        + "b" * 100
        + "\\nTable: "
        + "t" * 100
        + "-END"
    )
    app = App()
    async with app.run_test(size=(80, 24)) as pilot:
        modal = IcebergInfoModal(
            identity, (("Details", lambda: "Details"), ("Other", lambda: "Other details"))
        )
        app.push_screen(modal)
        await pilot.pause()
        assert identity in modal.query_one(TextArea).text
        await pilot.press("down", "enter")
        assert identity in modal.query_one(TextArea).text
        assert "Other details" in modal.query_one(TextArea).text
        await pilot.press("escape")
