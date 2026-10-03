"""Focused listing goldens and nonblank content guards."""

from pathlib import Path

import pytest

from tests.snapshot.apps.pane_listing_controls import ListingApp


@pytest.mark.parametrize("query", ["alpha", "missing"])
def test_listing_controls(query, snap_compare):
    assert snap_compare(ListingApp(query), terminal_size=(120, 40))


@pytest.mark.parametrize(
    ("query", "count", "row"),
    [("alpha", "1 / 3 matches", "alpha.txt"), ("missing", "0 / 3 matches", "No matches")],
)
def test_listing_content(query, count, row):
    svg = (
        Path(__file__).parent
        / "__snapshots__"
        / "test_pane_listing_controls"
        / f"test_listing_controls[{query}].raw"
    ).read_text()
    svg = svg.replace("&#160;", " ")
    for text in (query, count.replace(" ", "&#160;"), "Clear", row, "Sort:"):
        assert text in svg or text.replace("&#160;", " ") in svg
