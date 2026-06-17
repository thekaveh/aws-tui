"""Shared constants for snapshot tests.

Each snapshot test parametrizes itself across the four built-in themes
via ``@pytest.mark.parametrize("theme", THEMES)`` and pins the terminal
to ``TERMINAL_SIZE``. The M5 plan keeps this tier on Python 3.12 /
Ubuntu only (rendering-tolerance reasons).
"""

from __future__ import annotations

THEMES = ("carbon", "voidline", "lattice", "amber")

#: Standard terminal size for every snapshot fixture.
TERMINAL_SIZE = (120, 40)
