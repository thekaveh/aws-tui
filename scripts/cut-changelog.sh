#!/usr/bin/env bash
# Rename `[Unreleased]` to `[<version>] - <today>` in CHANGELOG.md
# and prepend a fresh empty `[Unreleased]` block. Numbered changelog
# headings from the maintenance policy are preserved and renumbered.
#
# Usage:  scripts/cut-changelog.sh 0.8.0
#
# Idempotent against a partially-cut changelog: bails if the version
# header already exists OR if the `[Unreleased]` header is missing.
# Run from the repo root.

set -euo pipefail

if [[ $# -ne 1 ]]; then
  echo "usage: $0 <version>" >&2
  echo "example: $0 0.8.0" >&2
  exit 64
fi

VERSION="$1"

if [[ ! "$VERSION" =~ ^[0-9]+\.[0-9]+\.[0-9]+(-[A-Za-z0-9.]+)?$ ]]; then
  echo "error: version '$VERSION' is not a valid SemVer (X.Y.Z[-tag])" >&2
  exit 64
fi

CHANGELOG="$(git rev-parse --show-toplevel)/CHANGELOG.md"

if [[ ! -f "$CHANGELOG" ]]; then
  echo "error: $CHANGELOG not found" >&2
  exit 66
fi

if ! grep -Eq '^## ([0-9]+(\.[0-9]+)*\. )?\[Unreleased\]' "$CHANGELOG"; then
  echo "error: CHANGELOG.md is missing the '## [Unreleased]' header — already cut?" >&2
  exit 65
fi

if grep -Eq "^## ([0-9]+(\.[0-9]+)*\. )?\[$VERSION\]" "$CHANGELOG"; then
  echo "error: CHANGELOG.md already has a '## [$VERSION]' header — refusing to overwrite" >&2
  exit 65
fi

TODAY="$(date +%Y-%m-%d)"

# Replace the `[Unreleased]` header with a fresh empty Unreleased
# block followed by the new version header. The Python helper keeps
# NUMBERED_DOCS headings coherent: old `1.1.x` Unreleased subsections
# become `1.2.x`, old `1.2` releases become `1.3`, etc.
TMP="$(mktemp)"
python3 - "$VERSION" "$TODAY" "$CHANGELOG" > "$TMP" <<'PY'
from __future__ import annotations

import re
import sys
from pathlib import Path

version, today, changelog_path = sys.argv[1:]
lines = Path(changelog_path).read_text(encoding="utf-8").splitlines()
release_header = re.compile(r"^(##) (?:(\d+(?:\.\d+)*)\. )?\[([^\]]+)\](.*)$")
numbered_heading = re.compile(r"^(#{2,6}) (\d+(?:\.\d+)*)\. (.*)$")

unreleased_idx: int | None = None
unreleased_nums: list[int] | None = None
for idx, line in enumerate(lines):
    match = release_header.match(line)
    if match and match.group(3) == "Unreleased":
        unreleased_idx = idx
        if match.group(2):
            unreleased_nums = [int(part) for part in match.group(2).split(".")]
        break

if unreleased_idx is None:
    raise SystemExit("missing Unreleased header")

if unreleased_nums is not None and len(unreleased_nums) != 2:
    raise SystemExit("numbered changelog release headings must be depth 2")

def shift_numbering(line: str) -> str:
    if unreleased_nums is None:
        return line
    match = numbered_heading.match(line)
    if not match:
        return line
    nums = [int(part) for part in match.group(2).split(".")]
    top, release_index = unreleased_nums
    if len(nums) >= 2 and nums[0] == top and nums[1] >= release_index:
        nums[1] += 1
        return f"{match.group(1)} {'.'.join(str(part) for part in nums)}. {match.group(3)}"
    return line

out = lines[:unreleased_idx]
if unreleased_nums is None:
    out.extend(["## [Unreleased]", "", f"## [{version}] - {today}"])
else:
    top, release_index = unreleased_nums
    out.extend(
        [
            f"## {top}.{release_index}. [Unreleased]",
            "",
            f"## {top}.{release_index + 1}. [{version}] - {today}",
        ]
    )
out.extend(shift_numbering(line) for line in lines[unreleased_idx + 1 :])
print("\n".join(out))
print()
PY

mv "$TMP" "$CHANGELOG"

echo "cut [$VERSION] - $TODAY in $CHANGELOG"
echo "next: bump src/aws_tui/version.py, update README status line, open PR."
