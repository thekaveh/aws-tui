from __future__ import annotations

from itertools import product
from pathlib import Path

import pytest

from tests.snapshot.apps.athena import AthenaFixture, AthenaPageApp
from tests.snapshot.conftest import THEMES

WIDE = (150, 44)
COMPACT = (100, 30)
NARROW = (80, 24)
FIXTURES: tuple[AthenaFixture, ...] = (
    "empty-query",
    "running",
    "success-results",
    "failure-detail",
    "history",
    "saved",
    "forbidden",
    "missing-result-config",
    "focused-rebound-tabs",
)


@pytest.mark.parametrize(
    ("fixture", "theme"),
    [
        pytest.param(fixture, theme, id=f"{fixture}-{theme}")
        for fixture, theme in product(FIXTURES, THEMES)
    ],
)
def test_athena_all_theme_snapshot(
    fixture: AthenaFixture,
    theme: str,
    snap_compare,
) -> None:
    assert snap_compare(
        AthenaPageApp(theme=theme, fixture=fixture),
        terminal_size=WIDE,
    )


@pytest.mark.parametrize(
    ("fixture", "theme"),
    [
        pytest.param(fixture, theme, id=f"{fixture}-{theme}")
        for fixture, theme in product(FIXTURES, ("carbon", "github-light"))
    ],
)
def test_athena_compact_snapshot(
    fixture: AthenaFixture,
    theme: str,
    snap_compare,
) -> None:
    assert snap_compare(
        AthenaPageApp(theme=theme, fixture=fixture),
        terminal_size=COMPACT,
    )


def test_athena_query_narrow_snapshot(snap_compare) -> None:
    assert snap_compare(
        AthenaPageApp(theme="carbon", fixture="empty-query"),
        terminal_size=NARROW,
    )


def test_athena_open_context_picker_snapshot(snap_compare) -> None:
    app = AthenaPageApp(
        theme="carbon",
        fixture="empty-query",
        show_legend=True,
    )
    assert snap_compare(
        app,
        terminal_size=WIDE,
        run_before=app.open_catalog_picker_with_geometry_check,
    )


def test_athena_open_context_picker_narrow_snapshot(snap_compare) -> None:
    app = AthenaPageApp(
        theme="carbon",
        fixture="empty-query",
        show_legend=True,
    )
    assert snap_compare(
        app,
        terminal_size=NARROW,
        run_before=app.open_catalog_picker_with_geometry_check,
    )


def _snapshot(fixture: AthenaFixture, theme: str) -> str:
    path = (
        Path(__file__).parent
        / "__snapshots__"
        / "test_athena"
        / f"test_athena_all_theme_snapshot[{fixture}-{theme}].raw"
    )
    assert path.is_file(), f"missing snapshot {path.name}; run --snapshot-update"
    return path.read_text()


def _compact_snapshot(fixture: AthenaFixture, theme: str) -> str:
    path = (
        Path(__file__).parent
        / "__snapshots__"
        / "test_athena"
        / f"test_athena_compact_snapshot[{fixture}-{theme}].raw"
    )
    assert path.is_file(), f"missing snapshot {path.name}; run --snapshot-update"
    return path.read_text()


def _named_snapshot(test_name: str) -> str:
    path = Path(__file__).parent / "__snapshots__" / "test_athena" / f"{test_name}.raw"
    assert path.is_file(), f"missing snapshot {path.name}; run --snapshot-update"
    return path.read_text()


def test_athena_picker_and_legend_snapshot_content_guards() -> None:
    for test_name in (
        "test_athena_open_context_picker_snapshot",
        "test_athena_open_context_picker_narrow_snapshot",
    ):
        opened = _named_snapshot(test_name)
        assert "Catalog" in opened
        assert "AwsDataCatalog" in opened
        assert "Commands" in opened

    narrow = _named_snapshot("test_athena_open_context_picker_narrow_snapshot")
    assert "[:]" in narrow
    assert "more" in narrow
    assert "[q]" in narrow
    assert "quit" in narrow


@pytest.mark.parametrize("theme", THEMES)
def test_athena_snapshot_content_guards(theme: str) -> None:
    empty = _snapshot("empty-query", theme)
    running = _snapshot("running", theme)
    results = _snapshot("success-results", theme)
    failure = _snapshot("failure-detail", theme)
    history = _snapshot("history", theme)
    saved = _snapshot("saved", theme)
    forbidden = _snapshot("forbidden", theme)
    missing = _snapshot("missing-result-config", theme)
    rebound = _snapshot("focused-rebound-tabs", theme)

    source = "analytics-prod·us-west-2"
    assert source in empty
    assert "&#160;AWS&#160;context&#160;" in empty
    assert "&#160;Views&#160;" not in empty
    assert "Enter&#160;a&#160;read-only&#160;query" in empty
    assert "q-20260726-running" in running
    assert "RUNNING" in running
    assert results.count("event[id]") >= 2
    assert "&#160;&quot;&quot;" in results
    assert "[literal][/bold]" in results
    assert "NULL" in results
    assert "TABLE_NOT_FOUND" in failure
    assert "Error&#160;category" in failure
    assert "history-primary" in history
    assert "Athena&#160;engine&#160;version&#160;3" in history
    assert "Event&#160;count" in saved
    assert "SELECT&#160;count(*)&#160;FROM&#160;events" in saved
    assert "Athena&#160;access&#160;is&#160;forbidden" in forbidden
    assert "result&#160;configuration&#160;is&#160;required" in missing
    assert "7&#160;query" in rebound
    assert "8&#160;history" in rebound
    assert "9&#160;results" in rebound
    assert "0&#160;saved" in rebound
    assert "1&#160;query" not in rebound


@pytest.mark.parametrize("theme", ["carbon", "github-light"])
def test_athena_compact_snapshot_content_guards(theme: str) -> None:
    empty = _compact_snapshot("empty-query", theme)
    results = _compact_snapshot("success-results", theme)
    history = _compact_snapshot("history", theme)
    saved = _compact_snapshot("saved", theme)
    rebound = _compact_snapshot("focused-rebound-tabs", theme)

    assert "analytics-prod·" in empty
    assert "us-west-2" in empty
    assert "read-only&#160;query" in empty
    assert results.count("event[id]") >= 2
    assert "&#160;&quot;&quot;" in results
    assert "history-primary" in history
    assert "Event&#160;count" in saved
    assert "7&#160;query" in rebound
    assert "1&#160;query" not in rebound
