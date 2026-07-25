from __future__ import annotations

from pathlib import Path

import pytest

from tests.snapshot.apps.glue import GluePageApp
from tests.snapshot.conftest import THEMES

WIDE = (150, 44)
COMPACT = (100, 30)


@pytest.mark.parametrize("theme", THEMES)
def test_glue_catalog_populated_snapshot(theme: str, snap_compare) -> None:
    assert snap_compare(GluePageApp(theme=theme), terminal_size=WIDE)


@pytest.mark.parametrize("theme", THEMES)
def test_glue_jobs_populated_snapshot(theme: str, snap_compare) -> None:
    assert snap_compare(GluePageApp(theme=theme, view="jobs"), terminal_size=WIDE)


@pytest.mark.parametrize("theme", THEMES)
def test_glue_crawlers_populated_snapshot(theme: str, snap_compare) -> None:
    assert snap_compare(GluePageApp(theme=theme, view="crawlers"), terminal_size=WIDE)


@pytest.mark.parametrize("theme", THEMES)
def test_glue_catalog_forbidden_snapshot(theme: str, snap_compare) -> None:
    assert snap_compare(
        GluePageApp(theme=theme, fixture="forbidden"),
        terminal_size=WIDE,
    )


@pytest.mark.parametrize("theme", THEMES)
def test_glue_catalog_empty_snapshot(theme: str, snap_compare) -> None:
    assert snap_compare(
        GluePageApp(theme=theme, fixture="empty"),
        terminal_size=WIDE,
    )


@pytest.mark.parametrize(
    ("view", "fixture"),
    [
        ("catalog", "populated"),
        ("jobs", "populated"),
        ("crawlers", "populated"),
        ("catalog", "forbidden"),
        ("catalog", "empty"),
    ],
)
def test_glue_compact_snapshot(view: str, fixture: str, snap_compare) -> None:
    assert snap_compare(
        GluePageApp(theme="carbon", view=view, fixture=fixture),
        terminal_size=COMPACT,
    )


def _snapshot(test_name: str, theme: str) -> str:
    path = Path(__file__).parent / "__snapshots__" / "test_glue" / f"{test_name}[{theme}].raw"
    assert path.is_file(), f"missing snapshot {path.name}; run --snapshot-update"
    return path.read_text()


@pytest.mark.parametrize("theme", THEMES)
def test_glue_snapshot_content_guards(theme: str) -> None:
    catalog = _snapshot("test_glue_catalog_populated_snapshot", theme)
    jobs = _snapshot("test_glue_jobs_populated_snapshot", theme)
    crawlers = _snapshot("test_glue_crawlers_populated_snapshot", theme)
    forbidden = _snapshot("test_glue_catalog_forbidden_snapshot", theme)
    empty = _snapshot("test_glue_catalog_empty_snapshot", theme)

    source = "analytics-prod&#160;·&#160;us-west-2"
    assert source in catalog
    assert "analytics" in catalog
    assert "events" in catalog
    assert "s3://warehouse/analytics/events/" in catalog
    assert "nightly" in jobs
    assert "RUNNING" in jobs
    assert "ready-crawler" in crawlers
    assert "READY" in crawlers
    assert "permission&#160;denied&#160;by&#160;Lake" in forbidden
    assert "events" not in forbidden
    assert "no&#160;databases" in empty
    assert "events" not in empty
