from pathlib import Path

import pytest
from scripts.release_version import (
    require_dated_changelog_release,
    stage_testpypi_version,
)


def test_stage_testpypi_version_requires_and_replaces_one_assignment(tmp_path: Path) -> None:
    version_file = tmp_path / "version.py"
    version_file.write_text('__version__ = "0.8.0"\n', encoding="utf-8")

    stage_testpypi_version(version_file, "0.8.0", "0.8.0.dev42")

    assert version_file.read_text(encoding="utf-8") == '__version__ = "0.8.0.dev42"\n'


def test_stage_testpypi_version_rejects_missing_assignment(tmp_path: Path) -> None:
    version_file = tmp_path / "version.py"
    version_file.write_text('__version__ = "0.9.0"\n', encoding="utf-8")

    with pytest.raises(ValueError, match="exactly one"):
        stage_testpypi_version(version_file, "0.8.0", "0.8.0.dev42")


def test_release_requires_a_dated_changelog_heading(tmp_path: Path) -> None:
    changelog = tmp_path / "CHANGELOG.md"
    changelog.write_text("## 1.2. [0.9.0] - 2026-08-02\n", encoding="utf-8")

    require_dated_changelog_release(changelog, "0.9.0")

    changelog.write_text("## 1.2. [0.9.0] - Pending\n", encoding="utf-8")
    with pytest.raises(ValueError, match="no dated release heading"):
        require_dated_changelog_release(changelog, "0.9.0")
