"""Shared constants for snapshot tests.

Each snapshot test parametrizes itself across every built-in theme
via ``@pytest.mark.parametrize("theme", THEMES)`` and pins the terminal
to ``TERMINAL_SIZE``. The M5 plan keeps this tier on Python 3.12 /
Ubuntu only (rendering-tolerance reasons).
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from aws_tui.infra.theme_store import ThemeStore

#: Derived, never hand-copied. A literal list silently drops a new built-in
#: theme out of snapshot coverage: the parametrization shrinks, every remaining
#: case still passes, and nothing reports the gap.
THEMES = ThemeStore.BUILTIN_NAMES

#: Standard terminal size for every snapshot fixture.
TERMINAL_SIZE = (120, 40)


@pytest.hookimpl(trylast=True)
def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    """Retain failed SVG pairs after the snapshot plugin assembles its report.

    Export only its image fields: the HTML report also embeds every environment
    variable, including unescaped values that may themselves contain SVG tags.
    The opt-in directory keeps local runs free of extra generated files.
    """
    destination = os.environ.get("AWS_TUI_SNAPSHOT_ARTIFACT_DIR")
    diffs = getattr(session.config, "_textual_snapshots", ())
    if not destination or not diffs:
        return
    output = Path(destination)
    output.mkdir(parents=True, exist_ok=True)
    for index, diff in enumerate(diffs, start=1):
        prefix = f"{index:03d}"
        (output / f"{prefix}-test.txt").write_text(
            f"{diff.path}:{diff.line_number}\n{diff.test_name}\n", encoding="utf-8"
        )
        for kind, svg in (("expected", diff.snapshot), ("actual", diff.actual)):
            if svg is not None and (kind == "actual" or diff.snapshot_exists):
                (output / f"{prefix}-{kind}.svg").write_text(svg, encoding="utf-8")


@pytest.fixture
def snapshot_caller_environment() -> None:
    """Extension point for tests that emulate a hostile caller environment."""


@pytest.fixture(autouse=True)
def canonical_snapshot_environment(
    monkeypatch: pytest.MonkeyPatch,
    snapshot_caller_environment: None,
) -> None:
    """Make every snapshot render with the same color-capable terminal."""
    del snapshot_caller_environment
    for name in ("NO_COLOR", "CLICOLOR", "CLICOLOR_FORCE"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("TERM", "xterm-256color")
