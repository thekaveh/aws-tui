#!/usr/bin/env bash
# Rename `[Unreleased]` to `[<version>] - <today>` in CHANGELOG.md
# and prepend a fresh empty `[Unreleased]` block.
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

if ! grep -q '^## \[Unreleased\]' "$CHANGELOG"; then
  echo "error: CHANGELOG.md is missing the '## [Unreleased]' header — already cut?" >&2
  exit 65
fi

if grep -q "^## \[$VERSION\]" "$CHANGELOG"; then
  echo "error: CHANGELOG.md already has a '## [$VERSION]' header — refusing to overwrite" >&2
  exit 65
fi

TODAY="$(date +%Y-%m-%d)"

# Replace the `## [Unreleased]` header with a fresh empty Unreleased
# block followed by the new version header. Portable awk so this runs
# on macOS BSD awk + Linux gawk without surprises.
TMP="$(mktemp)"
awk -v ver="$VERSION" -v today="$TODAY" '
  /^## \[Unreleased\]/ {
    print "## [Unreleased]";
    print "";
    print "## [" ver "] - " today;
    next;
  }
  { print }
' "$CHANGELOG" > "$TMP"

mv "$TMP" "$CHANGELOG"

echo "cut [$VERSION] - $TODAY in $CHANGELOG"
echo "next: bump src/aws_tui/version.py, update README status line, open PR."
