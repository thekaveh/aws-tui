from __future__ import annotations

from pathlib import Path

import pytest

from aws_tui.vm.glue.page_vm import GlueView
from tests.snapshot.apps.glue import GluePageApp
from tests.snapshot.conftest import THEMES

WIDE = (150, 44)
COMPACT = (100, 30)
NARROW = (80, 24)


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


@pytest.mark.parametrize("theme", THEMES)
def test_glue_iceberg_snapshots_snapshot(theme: str, snap_compare) -> None:
    assert snap_compare(
        GluePageApp(theme=theme, fixture="iceberg"),
        terminal_size=WIDE,
    )


@pytest.mark.parametrize(
    "iceberg_view",
    ["history", "manifests", "files", "partitions", "refs"],
)
def test_glue_iceberg_metadata_snapshot(iceberg_view: str, snap_compare) -> None:
    assert snap_compare(
        GluePageApp(
            theme="carbon",
            fixture="iceberg",
            iceberg_view=iceberg_view,
        ),
        terminal_size=WIDE,
    )


def test_glue_iceberg_narrow_snapshot(snap_compare) -> None:
    assert snap_compare(
        GluePageApp(theme="carbon", fixture="iceberg"),
        terminal_size=NARROW,
    )


@pytest.mark.parametrize("view", ["catalog", "jobs", "crawlers"])
def test_glue_service_narrow_snapshot(view: GlueView, snap_compare) -> None:
    assert snap_compare(
        GluePageApp(theme="carbon", view=view),
        terminal_size=NARROW,
    )


def test_glue_open_context_picker_snapshot(snap_compare) -> None:
    assert snap_compare(
        GluePageApp(
            theme="carbon",
            view="jobs",
            open_picker=True,
        ),
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


def _snapshot(test_name: str, theme: str | None = None) -> str:
    suffix = f"[{theme}]" if theme is not None else ""
    path = Path(__file__).parent / "__snapshots__" / "test_glue" / f"{test_name}{suffix}.raw"
    assert path.is_file(), f"missing snapshot {path.name}; run --snapshot-update"
    return path.read_text()


@pytest.mark.parametrize("theme", THEMES)
def test_glue_snapshot_content_guards(theme: str) -> None:
    catalog = _snapshot("test_glue_catalog_populated_snapshot", theme)
    jobs = _snapshot("test_glue_jobs_populated_snapshot", theme)
    crawlers = _snapshot("test_glue_crawlers_populated_snapshot", theme)
    forbidden = _snapshot("test_glue_catalog_forbidden_snapshot", theme)
    empty = _snapshot("test_glue_catalog_empty_snapshot", theme)

    source = "analytics-prod·us-west-2"
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

    snapshots = _snapshot("test_glue_iceberg_snapshots_snapshot", theme)
    assert "Snaps" in snapshots
    assert "Snapshot" in snapshots
    assert "43" in snapshots
    assert "append" in snapshots


def test_glue_iceberg_metadata_content_guards() -> None:
    history = _snapshot("test_glue_iceberg_metadata_snapshot[history]")
    manifests = _snapshot("test_glue_iceberg_metadata_snapshot[manifests]")
    files = _snapshot("test_glue_iceberg_metadata_snapshot[files]")
    partitions = _snapshot("test_glue_iceberg_metadata_snapshot[partitions]")
    refs = _snapshot("test_glue_iceberg_metadata_snapshot[refs]")

    assert "Ancestor" in history
    assert "manifest-0.avro" in manifests
    assert "a.parquet" in files
    assert "day=2026-07-28" in partitions
    assert "BRANCH" in refs
    assert "86400000" in refs

    narrow = _snapshot("test_glue_iceberg_narrow_snapshot")
    for label in ("Snaps", "Hist", "Mnfst", "Files", "Parts", "Refs"):
        assert label in narrow


def test_glue_narrow_catalog_keeps_table_type_on_its_resource_row() -> None:
    catalog = _snapshot("test_glue_service_narrow_snapshot[catalog]")
    sessions_row = next(line for line in catalog.splitlines() if ">sessions" in line)
    assert "EXTERNAL_TA" in sessions_row
