"""Exercise the real release cut and section extractor on the current notes."""

import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest


@pytest.mark.skipif(sys.platform == "win32", reason="Release cut tooling requires Bash")
def test_current_notes_survive_the_actual_release_cut_and_extraction(tmp_path: Path) -> None:
    root = Path(__file__).parents[2]
    source = (root / "CHANGELOG.md").read_text(encoding="utf-8")
    original_body = source.split("## [Unreleased]\n", 1)[1].split("\n## [", 1)[0]
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    (repo / "scripts").mkdir()
    shutil.copy2(root / "scripts/cut-changelog.sh", repo / "scripts/cut-changelog.sh")
    (repo / "CHANGELOG.md").write_text(source, encoding="utf-8")
    subprocess.run(["bash", "scripts/cut-changelog.sh", "0.9.0"], cwd=repo, check=True)

    # Execute only the reviewed, read-only awk extractor, never the workflow.
    workflow = (root / ".github/workflows/release.yml").read_text(encoding="utf-8")
    extractor = re.search(r'BODY="\$\((awk[\s\S]*?CHANGELOG\.md)\)"', workflow)
    assert extractor is not None
    body = subprocess.check_output(
        ["bash", "-c", "VERSION=0.9.0\n" + extractor[1]], cwd=repo, text=True
    )
    assert body.strip() == original_body.strip()
    for capability in (
        "CloudWatch",
        "SQL drafts",
        "result-cell",
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
    assert "## [0.8.0]" not in body
    assert "Deferred: dynamic commands (`switch connection/theme <name>`)" not in body
    assert "Zebra striping is now actually visible in all ten themes" not in body
