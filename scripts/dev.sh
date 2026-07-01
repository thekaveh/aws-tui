#!/usr/bin/env bash
# Launch aws-tui with Textual dev tools (live-reload .tcss, console).
#
# In a second terminal, run `./scripts/run-with-uv.sh textual console` to see logs.

set -euo pipefail

cd "$(dirname "$0")/.."

exec ./scripts/run-with-uv.sh textual run --dev src/aws_tui/app.py
