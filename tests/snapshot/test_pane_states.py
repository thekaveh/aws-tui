"""Snapshot tests for the six pane-state placeholders per spec §7.7.

Each state x theme produces one golden. Per the watchout, this is a sizable
test surface — we only ship one state ('empty') x four themes plus a
'auth_required' x four themes set in the canonical suite. Other states are
covered by a single snapshot under Carbon to keep CI fast and goldens
manageable.
"""

from __future__ import annotations

import pytest

from tests.snapshot.apps.pane_states import (
    make_auth_required_app,
    make_empty_app,
    make_error_app,
    make_forbidden_app,
    make_loading_app,
    make_unreachable_app,
)
from tests.snapshot.conftest import TERMINAL_SIZE, THEMES


@pytest.mark.parametrize("theme", THEMES)
def test_pane_empty(theme: str, snap_compare) -> None:
    assert snap_compare(make_empty_app(theme), terminal_size=TERMINAL_SIZE)


@pytest.mark.parametrize("theme", THEMES)
def test_pane_auth_required(theme: str, snap_compare) -> None:
    assert snap_compare(make_auth_required_app(theme), terminal_size=TERMINAL_SIZE)


# The four less-frequent placeholders only snapshot under Carbon to keep
# the golden set lean. The .tcss themes are validated separately by the
# unit-tier ``test_themes.py`` suite.


def test_pane_loading_carbon(snap_compare) -> None:
    assert snap_compare(make_loading_app("carbon"), terminal_size=TERMINAL_SIZE)


def test_pane_forbidden_carbon(snap_compare) -> None:
    assert snap_compare(make_forbidden_app("carbon"), terminal_size=TERMINAL_SIZE)


def test_pane_unreachable_carbon(snap_compare) -> None:
    assert snap_compare(make_unreachable_app("carbon"), terminal_size=TERMINAL_SIZE)


def test_pane_error_carbon(snap_compare) -> None:
    assert snap_compare(make_error_app("carbon"), terminal_size=TERMINAL_SIZE)
