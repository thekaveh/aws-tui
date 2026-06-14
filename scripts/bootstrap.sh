#!/usr/bin/env bash
# Initialize the local dev environment after a fresh clone.
#
#   git clone --recurse-submodules https://github.com/thekaveh/aws-tui.git
#   cd aws-tui
#   ./scripts/bootstrap.sh
#
# Idempotent — safe to re-run.

set -euo pipefail

cd "$(dirname "$0")/.."

echo "==> initializing git submodules (vendor/vmx)"
git submodule update --init --recursive

echo "==> uv sync"
uv sync

echo "==> installing pre-commit hooks"
uv run pre-commit install

echo "==> bootstrap complete; try:  uv run pytest"
