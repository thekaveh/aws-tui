"""Snapshot tests for JobRunLogsPane across 10 themes.

Every parity snapshot is paired with a content-presence guard so a
uniformly-blank render across all themes can't pass (per PR #53 /
#63 lesson)."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.snapshot.apps.emr_logs import (
    JobRunLogsEmptyTargetApp,
    JobRunLogsErrorApp,
    JobRunLogsIdleApp,
    JobRunLogsLoadingApp,
    JobRunLogsNoLogConfigApp,
    JobRunLogsReadyApp,
)
from tests.snapshot.conftest import THEMES

TERMINAL_SIZE = (120, 40)


# ── Snapshot fixtures ─────────────────────────────────────────────────────


@pytest.mark.parametrize("theme", THEMES)
def test_job_run_logs_empty_target_snapshot(theme: str, snap_compare) -> None:
    assert snap_compare(JobRunLogsEmptyTargetApp(theme=theme), terminal_size=TERMINAL_SIZE)


@pytest.mark.parametrize("theme", THEMES)
def test_job_run_logs_idle_snapshot(theme: str, snap_compare) -> None:
    assert snap_compare(JobRunLogsIdleApp(theme=theme), terminal_size=TERMINAL_SIZE)


@pytest.mark.parametrize("theme", THEMES)
def test_job_run_logs_loading_snapshot(theme: str, snap_compare) -> None:
    assert snap_compare(JobRunLogsLoadingApp(theme=theme), terminal_size=TERMINAL_SIZE)


@pytest.mark.parametrize("theme", THEMES)
def test_job_run_logs_ready_snapshot(theme: str, snap_compare) -> None:
    assert snap_compare(JobRunLogsReadyApp(theme=theme), terminal_size=TERMINAL_SIZE)


@pytest.mark.parametrize("theme", THEMES)
def test_job_run_logs_no_log_config_snapshot(theme: str, snap_compare) -> None:
    assert snap_compare(JobRunLogsNoLogConfigApp(theme=theme), terminal_size=TERMINAL_SIZE)


@pytest.mark.parametrize("theme", THEMES)
def test_job_run_logs_error_snapshot(theme: str, snap_compare) -> None:
    assert snap_compare(JobRunLogsErrorApp(theme=theme), terminal_size=TERMINAL_SIZE)


# ── Content-presence guards ───────────────────────────────────────────────


@pytest.mark.parametrize("theme", THEMES)
def test_job_run_logs_empty_target_renders_placeholder(theme: str) -> None:
    """Content-presence guard for ``test_job_run_logs_empty_target_snapshot``.

    The EMPTY_TARGET state has no run selected. Assert the placeholder
    text survives the render."""
    p = (
        Path(__file__).parent
        / "__snapshots__"
        / "test_emr_logs"
        / f"test_job_run_logs_empty_target_snapshot[{theme}].raw"
    )
    assert p.is_file(), f"expected snapshot {p.name} on disk; run --snapshot-update first"
    svg = p.read_text()
    assert len(svg) > 500, f"snapshot for theme {theme!r} appears to be empty/blank"
    # Textual SVG output encodes spaces as &#160; (non-breaking space).
    assert "no&#160;run&#160;selected" in svg, (
        f"empty-target placeholder missing for theme {theme!r}"
    )


@pytest.mark.parametrize("theme", THEMES)
def test_job_run_logs_idle_renders_placeholder(theme: str) -> None:
    """Content-presence guard for ``test_job_run_logs_idle_snapshot``.

    The IDLE state has a target set but not loaded. Assert the placeholder
    text survives the render."""
    p = (
        Path(__file__).parent
        / "__snapshots__"
        / "test_emr_logs"
        / f"test_job_run_logs_idle_snapshot[{theme}].raw"
    )
    assert p.is_file(), f"expected snapshot {p.name} on disk; run --snapshot-update first"
    svg = p.read_text()
    assert len(svg) > 500, f"snapshot for theme {theme!r} appears to be empty/blank"
    assert "press&#160;Enter&#160;to&#160;load&#160;logs" in svg, (
        f"idle placeholder missing for theme {theme!r}"
    )


@pytest.mark.parametrize("theme", THEMES)
def test_job_run_logs_loading_renders_progress(theme: str) -> None:
    """Content-presence guard for ``test_job_run_logs_loading_snapshot``.

    The LOADING state shows progress data. Assert the progress status line
    survives the render."""
    p = (
        Path(__file__).parent
        / "__snapshots__"
        / "test_emr_logs"
        / f"test_job_run_logs_loading_snapshot[{theme}].raw"
    )
    assert p.is_file(), f"expected snapshot {p.name} on disk; run --snapshot-update first"
    svg = p.read_text()
    assert len(svg) > 500, f"snapshot for theme {theme!r} appears to be empty/blank"
    # The loading status line should include bytes read and lines scanned
    assert "loading" in svg, f"loading status missing for theme {theme!r}"
    assert "bytes" in svg, f"bytes indicator missing for theme {theme!r}"
    assert "scanned" in svg, f"scanned indicator missing for theme {theme!r}"


@pytest.mark.parametrize("theme", THEMES)
def test_job_run_logs_ready_renders_lines(theme: str) -> None:
    """Content-presence guard for ``test_job_run_logs_ready_snapshot``.

    The READY state renders log lines. Assert seeded content survives
    the render."""
    p = (
        Path(__file__).parent
        / "__snapshots__"
        / "test_emr_logs"
        / f"test_job_run_logs_ready_snapshot[{theme}].raw"
    )
    assert p.is_file(), f"expected snapshot {p.name} on disk; run --snapshot-update first"
    svg = p.read_text()
    assert len(svg) > 500, f"snapshot for theme {theme!r} appears to be empty/blank"
    # Seeded log lines should appear in the render
    assert "ERROR" in svg, f"ERROR line missing for theme {theme!r}"
    assert "NullPointerException" in svg, f"exception line missing for theme {theme!r}"


@pytest.mark.parametrize("theme", THEMES)
def test_job_run_logs_no_log_config_renders_placeholder(theme: str) -> None:
    """Content-presence guard for ``test_job_run_logs_no_log_config_snapshot``.

    The NO_LOG_CONFIG state indicates no log monitoring is configured.
    Assert the placeholder text survives the render."""
    p = (
        Path(__file__).parent
        / "__snapshots__"
        / "test_emr_logs"
        / f"test_job_run_logs_no_log_config_snapshot[{theme}].raw"
    )
    assert p.is_file(), f"expected snapshot {p.name} on disk; run --snapshot-update first"
    svg = p.read_text()
    assert len(svg) > 500, f"snapshot for theme {theme!r} appears to be empty/blank"
    assert "no&#160;log&#160;monitoring&#160;configured" in svg, (
        f"no-log-config placeholder missing for theme {theme!r}"
    )


@pytest.mark.parametrize("theme", THEMES)
def test_job_run_logs_error_renders_error_text(theme: str) -> None:
    """Content-presence guard for ``test_job_run_logs_error_snapshot``.

    The ERROR state renders error text. Assert the seeded error message
    survives the render."""
    p = (
        Path(__file__).parent
        / "__snapshots__"
        / "test_emr_logs"
        / f"test_job_run_logs_error_snapshot[{theme}].raw"
    )
    assert p.is_file(), f"expected snapshot {p.name} on disk; run --snapshot-update first"
    svg = p.read_text()
    assert len(svg) > 500, f"snapshot for theme {theme!r} appears to be empty/blank"
    # The seeded error text should appear
    assert "ResourceNotFoundException" in svg, f"error type missing for theme {theme!r}"
    assert "bucket&#160;not&#160;found" in svg, f"error detail missing for theme {theme!r}"
