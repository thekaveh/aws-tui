"""Shared constants for snapshot tests.

Each snapshot test parametrizes itself across every built-in theme
via ``@pytest.mark.parametrize("theme", THEMES)`` and pins the terminal
to ``TERMINAL_SIZE``. The M5 plan keeps this tier on Python 3.12 /
Ubuntu only (rendering-tolerance reasons).
"""

from __future__ import annotations

import pytest

from aws_tui.infra.theme_store import ThemeStore

#: Derived, never hand-copied. A literal list silently drops a new built-in
#: theme out of snapshot coverage: the parametrization shrinks, every remaining
#: case still passes, and nothing reports the gap.
THEMES = ThemeStore.BUILTIN_NAMES

#: Standard terminal size for every snapshot fixture.
TERMINAL_SIZE = (120, 40)


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
