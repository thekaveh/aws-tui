from __future__ import annotations

import shutil
import subprocess
import sys
from datetime import date
from pathlib import Path

import pytest

# ``cut-changelog.sh`` is a POSIX bash script (release tooling); this test runs
# it through ``bash`` via subprocess, which is not available/applicable on
# Windows. Skip there, matching the repo's POSIX-only test convention.
pytestmark = pytest.mark.skipif(
    sys.platform == "win32",
    reason="cut-changelog.sh is a POSIX bash script; not applicable on Windows",
)


def test_cut_changelog_preserves_and_shifts_numbered_headings(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    scripts_dir = repo / "scripts"
    scripts_dir.mkdir()
    shutil.copy2("scripts/cut-changelog.sh", scripts_dir / "cut-changelog.sh")
    (repo / "CHANGELOG.md").write_text(
        "\n".join(
            [
                "# 1. Changelog",
                "",
                "## 1.1. [Unreleased]",
                "",
                "### 1.1.1. Added",
                "",
                "- Fresh work.",
                "",
                "## 1.2. [0.8.0] - 2026-06-30",
                "",
                "### 1.2.1. Fixed",
                "",
                "- Previous release.",
                "",
            ]
        ),
        encoding="utf-8",
    )

    subprocess.run(["bash", "scripts/cut-changelog.sh", "0.9.0"], cwd=repo, check=True)

    text = (repo / "CHANGELOG.md").read_text(encoding="utf-8")
    today = date.today().isoformat()
    assert f"## 1.1. [Unreleased]\n\n## 1.2. [0.9.0] - {today}" in text
    assert "### 1.2.1. Added" in text
    assert "## 1.3. [0.8.0] - 2026-06-30" in text
    assert "### 1.3.1. Fixed" in text
