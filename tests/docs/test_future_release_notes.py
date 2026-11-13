"""Exercise the real cut script and workflow extractor across release states."""

import hashlib
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[2]


def _extract_notes(repo: Path, version: str) -> str:
    # Execute only the reviewed, read-only awk extractor, never the workflow.
    workflow = (ROOT / ".github/workflows/release.yml").read_text(encoding="utf-8")
    extractor = re.search(r'BODY="\$\((awk[\s\S]*?CHANGELOG\.md)\)"', workflow)
    assert extractor is not None
    return subprocess.check_output(
        ["bash", "-c", f"VERSION={version}\n" + extractor[1]], cwd=repo, text=True
    )


@pytest.mark.skipif(sys.platform == "win32", reason="Release cut tooling requires Bash")
def test_unreleased_fixture_survives_actual_cut_and_extraction(tmp_path: Path) -> None:
    # This fixture's version and body are independent of the prepared tree.
    version = "8.4.2"
    original_body = "\n### Added\n\n- A fixture capability.\n\n"
    source = (
        "# Changelog\n\n## [Unreleased]\n"
        + original_body
        + "## [8.4.1] - 2026-01-01\n\n- Prior fixture release.\n\n"
        + "[Unreleased]: https://github.com/thekaveh/aws-tui/compare/fixture-base...HEAD\n"
    )
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    (repo / "scripts").mkdir()
    shutil.copy2(ROOT / "scripts/cut-changelog.sh", repo / "scripts/cut-changelog.sh")
    (repo / "CHANGELOG.md").write_text(source, encoding="utf-8")
    subprocess.run(["bash", "scripts/cut-changelog.sh", version], cwd=repo, check=True)

    cut = (repo / "CHANGELOG.md").read_text(encoding="utf-8")
    assert re.search(r"## \[Unreleased\]\n\n## \[8\.4\.2\] - \d{4}-\d{2}-\d{2}", cut)
    assert "[Unreleased]: https://github.com/thekaveh/aws-tui/compare/v8.4.2...HEAD" in cut
    assert "[8.4.2]: https://github.com/thekaveh/aws-tui/compare/fixture-base...v8.4.2" in cut
    assert _extract_notes(repo, version).strip() == original_body.strip()


@pytest.mark.skipif(sys.platform == "win32", reason="Workflow extractor requires Bash")
def test_prepared_release_extraction_keeps_current_notes_and_linked_history() -> None:
    source = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    headings = re.findall(r"^## \[0\.9\.0\] - \d{4}-\d{2}-\d{2}$", source, re.MULTILINE)
    assert len(headings) == 1
    expected = source.split(headings[0] + "\n", 1)[1].split("\n## [", 1)[0]
    body = _extract_notes(ROOT, "0.9.0")
    assert body.strip() == expected.strip()
    for capability in (
        "CloudWatch",
        "SQL drafts",
        "result-cell",
        "complete execution as normalized CSV/JSON",
        "table comparison",
        "--connection",
        "--profile",
        "--region",
        "--service",
        "--location",
        "CSV",
        "JSONL",
        "Parquet",
    ):
        assert capability in body, capability
    assert body.count("**Local Iceberg row preview with DuckDB (Peek).**") == 1
    assert "## [0.8.0]" not in source
    assert "Deferred: dynamic commands (`switch connection/theme <name>`)" not in body
    assert "Zebra striping is now actually visible in all ten themes" not in body
    assert "metadata remains" not in body
    assert "target v0.9.0" not in body
    assert "Full-execution Athena export" not in body
    assert "Full-execution export remains separate scope" not in body
    assert "[0.9.0]: https://github.com/thekaveh/aws-tui/compare/v0.7.0...v0.9.0" in source
    assert "[Unreleased]: https://github.com/thekaveh/aws-tui/compare/v0.9.0...HEAD" in source
    assert "\n[0.8.0]:" not in source

    link = re.search(r"\[unpublished 0\.8 development history\]\(([^)]+)\)", body)
    assert link is not None
    archive = (ROOT / link[1]).read_bytes()
    original = archive.split(b"`````markdown\n", 1)[1].split(b"`````\n", 1)[0]
    assert original.startswith(b"## [0.8.0] - Pending\n")
    original_body = original.removeprefix(b"## [0.8.0] - Pending\n")
    # Content identity from the complete pre-cut section, not a filename guard.
    digest = "90cbc78957a962022ff85577ff1b2d4aa11199992753fe39d822e130036148c2"
    assert hashlib.sha256(original_body).hexdigest() == digest
    assert digest.encode() in archive
    assert b"76c99c370a92f89ffec8a812aae95f85f8964561" in archive
    assert b"11e029d89242a334f8e2c62eee9b505fb63b6164" in archive
    assert "2026-10-22-unreleased-implementation-history.md" in body
