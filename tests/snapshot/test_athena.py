from __future__ import annotations

import os
from html import unescape
from itertools import product
from pathlib import Path
from typing import cast

import pytest
from textual.pilot import Pilot
from textual.widgets import TextArea

from aws_tui.ui.widgets.athena.page import AthenaPage
from tests.helpers import drain_workers, seed_athena_sql, wait_until
from tests.snapshot.apps.athena import AthenaFixture, AthenaPageApp
from tests.snapshot.apps.demo_mode import DemoModeApp
from tests.snapshot.conftest import THEMES
from tests.snapshot.test_demo_mode import _dismiss_demo_startup_advisory

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
FULL_APP_CASES = [
    pytest.param(theme, size, id=f"{theme}-{size[0]}x{size[1]}")
    for theme, size in product(("carbon", "github-light"), ((80, 24), (120, 40)))
]


async def _show_full_app_query(pilot: Pilot[None]) -> None:
    app = cast(DemoModeApp, pilot.app)
    await drain_workers(app)
    app.app_ctx.root_vm.services_menu.switch_service_command.execute("athena")
    await wait_until(lambda: bool(app.query(AthenaPage)), what="full-app Athena page mounted")
    await drain_workers(app)
    page = app.query_one(AthenaPage)
    await wait_until(
        lambda: page.vm.context.database == "dev_events",
        what="full-app Athena snapshot context loaded",
    )
    editor = app.query_one("#athena-editor", TextArea)
    await seed_athena_sql(pilot, page.vm.query, editor, "SELECT 42 AS compact_layout")
    await _dismiss_demo_startup_advisory(pilot)
    try:
        await wait_until(
            lambda: editor.region.height >= 3 and "compact_layout" in app.export_screenshot(),
            what="full-app snapshot contains visibly rendered SQL",
        )
    except AssertionError as error:
        # run_before failures happen before the snapshot plugin captures an
        # image. Preserve this demo-only SVG too, so CI can distinguish a
        # layout failure from missing SQL or split SVG text spans.
        try:
            state = (
                f"size={app.size!r}; screen={app.screen.classes!r}; "
                f"service={app.app_ctx.root_vm.content_host.current_id!r}; "
                f"focus={app.focused!r}; editor_attached={editor.is_attached}; "
                f"editor_current={editor in app.query('#athena-editor')}; "
                f"editor_region={editor.region!r}; editor_scroll={editor.scroll_offset!r}; "
                f"editor_text={editor.text!r}; vm_sql={page.vm.query.sql!r}; "
                f"regions={[(type(w).__name__, w.region, w.classes) for w in app.query('BrandBanner, AthenaPage, AthenaQueryView')]!r}"
            )
            error.add_note(state)
            svg = app.export_screenshot()
            destination = os.environ.get("AWS_TUI_SNAPSHOT_ARTIFACT_DIR")
            if destination:
                output = Path(destination)
                output.mkdir(parents=True, exist_ok=True)
                theme = app.app_ctx.initial_theme
                name = f"athena-readiness-{theme}-{app.size.width}x{app.size.height}.svg"
                (output / name).write_text(svg, encoding="utf-8")
            error.add_note(f"rendered_marker={'compact_layout' in svg}")
        except Exception as diagnostic_error:
            error.add_note(f"Could not complete snapshot diagnostics: {diagnostic_error!r}")
        raise
    assert editor.region.height >= 3
    assert editor.text == "SELECT 42 AS compact_layout"


@pytest.mark.parametrize(("theme", "size"), FULL_APP_CASES)
def test_athena_full_app_snapshot(theme: str, size: tuple[int, int], snap_compare) -> None:
    app = DemoModeApp(theme=theme)
    try:
        assert snap_compare(app, terminal_size=size, run_before=_show_full_app_query)
    finally:
        app.app_ctx.root_vm.dispose()


@pytest.mark.parametrize(("theme", "size"), FULL_APP_CASES)
def test_athena_full_app_snapshot_content_guard(theme: str, size: tuple[int, int]) -> None:
    snapshot = _named_snapshot(f"test_athena_full_app_snapshot[{theme}-{size[0]}x{size[1]}]")
    rendered = unescape(snapshot).replace("\xa0", " ")
    assert "query editor" in rendered
    assert "SELECT" in rendered
    assert "compact_layout" in rendered
    assert "Athena" in rendered
    assert "demo-dev" in rendered
    assert "DEMO MODE" in rendered


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


@pytest.mark.parametrize(
    ("fixture", "theme"),
    [
        pytest.param(fixture, theme, id=f"{fixture}-{theme}")
        for fixture, theme in product(FIXTURES, THEMES)
    ],
)
def test_athena_all_theme_narrow_snapshot(
    fixture: AthenaFixture,
    theme: str,
    snap_compare,
) -> None:
    app = AthenaPageApp(theme=theme, fixture=fixture)
    assert snap_compare(
        app,
        terminal_size=NARROW,
        run_before=app.assert_narrow_layout,
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


def _narrow_snapshot(fixture: AthenaFixture, theme: str) -> str:
    path = (
        Path(__file__).parent
        / "__snapshots__"
        / "test_athena"
        / f"test_athena_all_theme_narrow_snapshot[{fixture}-{theme}].raw"
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


def test_athena_query_narrow_snapshot_content_guard() -> None:
    raw = _named_snapshot("test_athena_query_narrow_snapshot")
    assert "AWS&#160;context" not in raw
    assert "query&#160;controls" in raw
    assert "query&#160;editor" in raw
    assert raw.index("query&#160;controls") < raw.index("query&#160;editor")


@pytest.mark.parametrize("theme", THEMES)
def test_athena_narrow_snapshot_content_guards(theme: str) -> None:
    empty = _narrow_snapshot("empty-query", theme)
    running = _narrow_snapshot("running", theme)
    failure = _narrow_snapshot("failure-detail", theme)
    missing = _narrow_snapshot("missing-result-config", theme)

    assert "analytics-prod·us-west-2" in empty
    assert "query&#160;controls" in empty
    assert "query&#160;editor" in empty
    assert empty.index("query&#160;controls") < empty.index("query&#160;editor")
    assert "Enter&#160;a&#160;read-only&#160;query" in empty
    assert "q-20260726-running" in running
    assert "RUNNING" in running
    assert "TABLE_NOT_FOUND" in failure
    assert "result&#160;configuration&#160;is&#160;required" in missing


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
