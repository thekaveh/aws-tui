"""Failed-image artifacts must not copy the report's environment section."""

from __future__ import annotations

from pathlib import Path

import pytest
from pytest_textual_snapshot import SvgSnapshotDiff
from textual.app import App

from tests.snapshot.conftest import pytest_sessionfinish as export_artifacts


@pytest.mark.parametrize(
    ("expected", "snapshot_exists"),
    [(None, False), ("None<style>placeholder</style>", False), ("<svg>expected</svg>", True)],
)
def test_snapshot_artifacts_export_only_images_and_test_identity(
    expected: str | None,
    snapshot_exists: bool,
    monkeypatch: pytest.MonkeyPatch,
    request: pytest.FixtureRequest,
    tmp_path: Path,
) -> None:
    diff = SvgSnapshotDiff(
        snapshot=expected,
        actual="<svg>actual</svg>",
        test_name="test_example[voidline]",
        path=Path("tests/snapshot/test_example.py"),
        line_number=12,
        app=App(),
        environment={"SECRET": "<svg>ENVIRONMENT_SENTINEL</svg>"},
        docstring="",
        app_path=None,
        snapshot_exists=snapshot_exists,
    )
    monkeypatch.setattr(request.config, "_textual_snapshots", [diff], raising=False)
    output = tmp_path / "artifacts"
    monkeypatch.setenv("AWS_TUI_SNAPSHOT_ARTIFACT_DIR", str(output))
    export_artifacts(request.session, 1)
    assert (output / "001-actual.svg").read_text() == diff.actual
    if snapshot_exists:
        assert (output / "001-expected.svg").read_text() == expected
    else:
        assert not (output / "001-expected.svg").exists()
    assert (output / "001-test.txt").read_text() == (
        "tests/snapshot/test_example.py:12\ntest_example[voidline]\n"
    )
    assert all("ENVIRONMENT_SENTINEL" not in path.read_text() for path in output.iterdir())


def test_snapshot_artifacts_need_opt_in_and_a_failed_comparison(
    monkeypatch: pytest.MonkeyPatch,
    request: pytest.FixtureRequest,
    tmp_path: Path,
) -> None:
    monkeypatch.delenv("AWS_TUI_SNAPSHOT_ARTIFACT_DIR", raising=False)
    export_artifacts(request.session, 0)
    output = tmp_path / "artifacts"
    monkeypatch.setenv("AWS_TUI_SNAPSHOT_ARTIFACT_DIR", str(output))
    monkeypatch.setattr(request.config, "_textual_snapshots", [], raising=False)
    export_artifacts(request.session, 0)
    assert not output.exists()
