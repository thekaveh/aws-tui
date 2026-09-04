"""Release-version guards used by the GitHub release workflow."""

from __future__ import annotations

import argparse
import re
from pathlib import Path


def stage_testpypi_version(path: Path, base_version: str, publish_version: str) -> None:
    old = f'__version__ = "{base_version}"'
    new = f'__version__ = "{publish_version}"'
    text = path.read_text(encoding="utf-8")
    if text.count(old) != 1:
        raise ValueError(f"expected exactly one version assignment for {base_version}")
    path.write_text(text.replace(old, new), encoding="utf-8")
    if path.read_text(encoding="utf-8").count(new) != 1:
        raise ValueError(f"failed to stage TestPyPI version {publish_version}")


def require_dated_changelog_release(path: Path, version: str) -> None:
    text = path.read_text(encoding="utf-8")
    heading = re.compile(
        rf"^##\s+(?:\d+(?:\.\d+)*\.\s+)?\[{re.escape(version)}\]\s+-\s+\d{{4}}-\d{{2}}-\d{{2}}\s*$",
        re.MULTILINE,
    )
    if heading.search(text) is None:
        raise ValueError(f"CHANGELOG.md has no dated release heading for {version}")


def main() -> None:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    stage = subparsers.add_parser("stage-testpypi")
    stage.add_argument("base_version")
    stage.add_argument("publish_version")
    check = subparsers.add_parser("check-changelog")
    check.add_argument("version")
    args = parser.parse_args()

    if args.command == "stage-testpypi":
        stage_testpypi_version(
            Path("src/aws_tui/version.py"), args.base_version, args.publish_version
        )
    else:
        require_dated_changelog_release(Path("CHANGELOG.md"), args.version)


if __name__ == "__main__":
    main()
