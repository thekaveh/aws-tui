#!/usr/bin/env bash
# Rename `[Unreleased]` to `[<version>] - <today>` in CHANGELOG.md
# and prepend a fresh empty `[Unreleased]` block. Changelog headings
# are never section-numbered: they are version names, and numbering
# them would renumber every prior release on each cut.
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
# block followed by the new version header.
TMP="$(mktemp)"
# Without this the temp file is abandoned on every `raise SystemExit`
# path in the Python helper below.
trap 'rm -f "$TMP"' EXIT
python3 - "$VERSION" "$TODAY" "$CHANGELOG" > "$TMP" <<'PY'
from __future__ import annotations

import re
import sys
from pathlib import Path

version, today, changelog_path = sys.argv[1:]
lines = Path(changelog_path).read_text(encoding="utf-8").splitlines()
release_header = re.compile(r"^(##) (?:(\d+(?:\.\d+)*)\. )?\[([^\]]+)\](.*)$")

unreleased_idx: int | None = None
for idx, line in enumerate(lines):
    match = release_header.match(line)
    if match and match.group(3) == "Unreleased":
        if match.group(2):
            raise SystemExit(
                "changelog release headings must not be numbered; "
                "renumbering them would shift every prior release on each cut"
            )
        unreleased_idx = idx
        break

if unreleased_idx is None:
    raise SystemExit("missing Unreleased header")

out = lines[:unreleased_idx]
out.extend(["## [Unreleased]", "", f"## [{version}] - {today}"])
out.extend(lines[unreleased_idx + 1 :])

# Move the compare window forward with the release. Preserve the exact previous
# Unreleased base (a tag or commit) for the new version's comparison link.
unreleased_ref_idx: int | None = None
unreleased_url: str | None = None
for idx, line in enumerate(out):
    if line.startswith("[Unreleased]: "):
        unreleased_ref_idx = idx
        unreleased_url = line.removeprefix("[Unreleased]: ")
        break
if unreleased_ref_idx is None or unreleased_url is None:
    raise SystemExit("missing [Unreleased] comparison reference")
marker = "/compare/"
if marker not in unreleased_url or not unreleased_url.endswith("...HEAD"):
    raise SystemExit("[Unreleased] reference must be a compare URL ending in ...HEAD")
compare_root, comparison = unreleased_url.split(marker, maxsplit=1)
previous_base = comparison.removesuffix("...HEAD")
if not previous_base:
    raise SystemExit("[Unreleased] comparison reference has no base")
tag = f"v{version}"
out[unreleased_ref_idx] = f"[Unreleased]: {compare_root}{marker}{tag}...HEAD"
out.insert(
    unreleased_ref_idx + 1,
    f"[{version}]: {compare_root}{marker}{previous_base}...{tag}",
)
print("\n".join(out))
PY

# mktemp creates 0600; a bare `mv` would carry that onto the changelog and
# silently make a tracked file owner-only.
chmod 644 "$TMP"
mv "$TMP" "$CHANGELOG"

echo "cut [$VERSION] - $TODAY in $CHANGELOG"
echo "next: bump src/aws_tui/version.py, update README status line, open PR."
