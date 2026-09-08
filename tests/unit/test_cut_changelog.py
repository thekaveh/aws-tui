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


def _repo_with_changelog(tmp_path: Path, lines: list[str]) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    (repo / "scripts").mkdir()
    shutil.copy2("scripts/cut-changelog.sh", repo / "scripts" / "cut-changelog.sh")
    (repo / "CHANGELOG.md").write_text("\n".join(lines), encoding="utf-8")
    return repo


def test_cut_changelog_renames_unreleased_and_opens_a_fresh_block(tmp_path: Path) -> None:
    repo = _repo_with_changelog(
        tmp_path,
        [
            "# Changelog",
            "",
            "## [Unreleased]",
            "",
            "### Added",
            "",
            "- Fresh work.",
            "",
            "## [0.8.0] - 2026-06-30",
            "",
            "### Fixed",
            "",
            "- Previous release.",
            "",
            "[Unreleased]: https://github.com/thekaveh/aws-tui/compare/v0.8.0...HEAD",
            "[0.8.0]: https://github.com/thekaveh/aws-tui/compare/v0.7.0...v0.8.0",
        ],
    )

    subprocess.run(["bash", "scripts/cut-changelog.sh", "0.9.0"], cwd=repo, check=True)

    text = (repo / "CHANGELOG.md").read_text(encoding="utf-8")
    today = date.today().isoformat()
    assert f"## [Unreleased]\n\n## [0.9.0] - {today}" in text
    # Prior releases keep their headings verbatim; nothing is renumbered.
    assert "## [0.8.0] - 2026-06-30" in text
    assert "### Added" in text
    assert "### Fixed" in text
    assert "[Unreleased]: https://github.com/thekaveh/aws-tui/compare/v0.9.0...HEAD" in text
    assert "[0.9.0]: https://github.com/thekaveh/aws-tui/compare/v0.8.0...v0.9.0" in text


def test_cut_changelog_refuses_a_numbered_release_heading(tmp_path: Path) -> None:
    """Numbering release headings renumbers every prior release on each cut."""
    repo = _repo_with_changelog(
        tmp_path,
        [
            "# 1. Changelog",
            "",
            "## 1.1. [Unreleased]",
            "",
            "[Unreleased]: https://github.com/thekaveh/aws-tui/compare/v0.8.0...HEAD",
        ],
    )

    result = subprocess.run(
        ["bash", "scripts/cut-changelog.sh", "0.9.0"],
        cwd=repo,
        capture_output=True,
        text=True,
    )

    assert result.returncode != 0
    assert "must not be numbered" in result.stderr
