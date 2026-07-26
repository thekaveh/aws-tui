from __future__ import annotations

from itertools import product
from pathlib import Path

import pytest

from tests.snapshot.apps.athena import AthenaFixture, AthenaPageApp
from tests.snapshot.conftest import THEMES

WIDE = (150, 44)
COMPACT = (100, 30)
FIXTURES: tuple[AthenaFixture, ...] = (
    "empty-query",
    "running",
    "success-results",
    "failure-detail",
    "history",
    "saved",
    "forbidden",
    "missing-result-config",
)


@pytest.fixture(autouse=True)
def _color_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.setenv("TERM", "xterm-256color")


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


def _snapshot(fixture: AthenaFixture, theme: str) -> str:
    path = (
        Path(__file__).parent
        / "__snapshots__"
        / "test_athena"
        / f"test_athena_all_theme_snapshot[{fixture}-{theme}].raw"
    )
    assert path.is_file(), f"missing snapshot {path.name}; run --snapshot-update"
    return path.read_text()


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

    source = "analytics-prod&#160;·&#160;us-west-2"
    assert source in empty
    assert "Enter&#160;a&#160;read-only&#160;query" in empty
    assert "q-20260726-running" in running
    assert "RUNNING" in running
    assert "event[id]" in results
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
