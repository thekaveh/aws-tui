from __future__ import annotations

import os
from pathlib import Path

import pytest

from tests.snapshot.apps.nav_menu import NavMenuApp

HOSTILE_ENVIRONMENTS = (
    pytest.param(
        {"NO_COLOR": "1", "CLICOLOR": "0", "TERM": "dumb"},
        id="no-color-dumb",
    ),
    pytest.param(
        {"CLICOLOR": "0", "CLICOLOR_FORCE": "0", "TERM": "unknown"},
        id="clicolor-disabled",
    ),
    pytest.param(
        {"NO_COLOR": "", "CLICOLOR_FORCE": "1", "TERM": ""},
        id="conflicting-flags",
    ),
)


@pytest.fixture(params=HOSTILE_ENVIRONMENTS)
def snapshot_caller_environment(
    request: pytest.FixtureRequest,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name in ("NO_COLOR", "CLICOLOR", "CLICOLOR_FORCE", "TERM"):
        monkeypatch.delenv(name, raising=False)
    for name, value in request.param.items():
        monkeypatch.setenv(name, value)


def test_representative_snapshot_normalizes_hostile_environment(
    snapshot_caller_environment: None,
    snap_compare,
) -> None:
    del snapshot_caller_environment
    assert "NO_COLOR" not in os.environ
    assert "CLICOLOR" not in os.environ
    assert "CLICOLOR_FORCE" not in os.environ
    assert os.environ["TERM"] == "xterm-256color"
    assert snap_compare(
        NavMenuApp(theme="carbon"),
        terminal_size=(40, 20),
    )


def test_hostile_environment_snapshots_are_identical() -> None:
    root = Path(__file__).parent / "__snapshots__" / "test_environment"
    snapshots = tuple(
        (root / f"test_representative_snapshot_normalizes_hostile_environment[{case.id}].raw")
        .read_text()
        .replace(case.id or "", "")
        for case in HOSTILE_ENVIRONMENTS
    )
    assert len(set(snapshots)) == 1
