"""Shared fixtures for snapshot tests.

We define small App subclasses that each render one view (main screen,
modal, pane placeholder) wired to canned VMs. The same App is parametrized
across the four built-in themes via the ``theme_name`` fixture.

Per the M5 plan, all snapshot tests pin to terminal size ``(120, 40)``;
CI only runs this tier on Python 3.12 / Ubuntu (rendering tolerance).
"""

from __future__ import annotations

import pytest

THEMES = ("carbon", "voidline", "lattice", "amber")

#: Standard terminal size for every snapshot fixture.
TERMINAL_SIZE = (120, 40)


@pytest.fixture(params=THEMES)
def theme_name(request: pytest.FixtureRequest) -> str:
    return request.param  # type: ignore[no-any-return]
