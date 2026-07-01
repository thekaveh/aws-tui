#!/usr/bin/env bash
set -euo pipefail

UV_MIN_VERSION=${UV_MIN_VERSION:-0.11.19}

version_ge() {
  local minimum="$1"
  local actual="$2"
  local min_major min_minor min_patch actual_major actual_minor actual_patch

  minimum="${minimum%%+*}"
  minimum="${minimum%%-*}"
  actual="${actual%%+*}"
  actual="${actual%%-*}"

  IFS=. read -r min_major min_minor min_patch <<<"${minimum}"
  IFS=. read -r actual_major actual_minor actual_patch <<<"${actual}"

  min_major="${min_major:-0}"
  min_minor="${min_minor:-0}"
  min_patch="${min_patch:-0}"
  actual_major="${actual_major:-0}"
  actual_minor="${actual_minor:-0}"
  actual_patch="${actual_patch:-0}"

  if ((10#${actual_major} != 10#${min_major})); then
    ((10#${actual_major} > 10#${min_major}))
    return
  fi
  if ((10#${actual_minor} != 10#${min_minor})); then
    ((10#${actual_minor} > 10#${min_minor}))
    return
  fi
  ((10#${actual_patch} >= 10#${min_patch}))
}

if ! command -v uv >/dev/null 2>&1; then
  echo "error: uv >= ${UV_MIN_VERSION} is required; found: not installed" >&2
  exit 127
fi

UV_ACTUAL_VERSION="$(uv --version | awk '{print $2}')"
if ! version_ge "$UV_MIN_VERSION" "$UV_ACTUAL_VERSION"; then
  echo "error: uv >= ${UV_MIN_VERSION} is required; found ${UV_ACTUAL_VERSION}" >&2
  exit 127
fi

exec uv run "$@"
