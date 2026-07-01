#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."

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

if command -v uv >/dev/null 2>&1 && version_ge "$UV_MIN_VERSION" "$(uv --version | awk '{print $2}')"; then
  PYTHON_RUNNER=(uv run python)
elif command -v python3 >/dev/null 2>&1 && python3 - <<'PY' >/dev/null 2>&1
import sys

raise SystemExit(0 if sys.version_info >= (3, 11) else 1)
PY
then
  PYTHON_RUNNER=(python3)
else
  echo "error: uv >= ${UV_MIN_VERSION} or python3 >= 3.11 is required to run layer checks" >&2
  exit 127
fi

"${PYTHON_RUNNER[@]}" - <<'PY'
from __future__ import annotations

import ast
import sys
from pathlib import Path

ROOT = Path("src/aws_tui")

Rule = tuple[str, str, tuple[str, ...]]

RULES: tuple[Rule, ...] = (
    (
        "vm",
        "ViewModel",
        (
            "textual",
            "boto3",
            "aioboto3",
            "botocore",
            "aws_tui.ui",
            "aws_tui.services",
        ),
    ),
    ("domain", "Domain", ("textual", "aws_tui.vm", "aws_tui.ui", "aws_tui.services")),
    (
        "infra",
        "Infrastructure",
        (
            "textual",
            "aws_tui.vm",
            "aws_tui.ui",
            "aws_tui.services",
            "aws_tui.domain",
        ),
    ),
    (
        "ui",
        "View",
        (
            "boto3",
            "aioboto3",
            "botocore",
            "aws_tui.infra.aws_session",
            "aws_tui.infra.connection_resolver",
        ),
    ),
    # services/ is the service-composition boundary: it may import concrete VM
    # classes to build each service's page/viewmodel tree, but it must not reach
    # upward into Textual widgets.
    ("services", "Services", ("textual", "aws_tui.ui")),
    # demo/ is the runtime-mock layer. The composition root may opt into it, but
    # production layers must not import demo fakes directly.
    ("vm", "ViewModel", ("aws_tui.demo",)),
    ("domain", "Domain", ("aws_tui.demo",)),
    ("infra", "Infrastructure", ("aws_tui.demo",)),
    ("ui", "View", ("aws_tui.demo",)),
    ("services", "Services", ("aws_tui.demo",)),
)


def module_for(path: Path) -> str:
    rel = path.with_suffix("").relative_to("src")
    parts = rel.parts
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def resolve_from(module: str, node: ast.ImportFrom) -> str:
    if node.level == 0:
        return node.module or ""
    package_parts = module.split(".")
    if not package_parts:
        return node.module or ""
    if module.rsplit(".", 1)[-1] != "__init__":
        package_parts = package_parts[:-1]
    keep = max(0, len(package_parts) - (node.level - 1))
    base = package_parts[:keep]
    if node.module:
        base.extend(node.module.split("."))
    return ".".join(part for part in base if part)


def is_banned(imported: str, banned: str) -> bool:
    return imported == banned or imported.startswith(f"{banned}.")


failures: list[str] = []
for folder, label, banned_modules in RULES:
    base = ROOT / folder
    if not base.exists():
        continue
    for path in sorted(base.rglob("*.py")):
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(path))
        current_module = module_for(path)
        for node in ast.walk(tree):
            imported_modules: list[str] = []
            if isinstance(node, ast.Import):
                imported_modules.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                imported_modules.append(resolve_from(current_module, node))
            else:
                continue

            for imported in imported_modules:
                for banned in banned_modules:
                    if is_banned(imported, banned):
                        failures.append(
                            f"{path}:{node.lineno}: {label} layer rule violation "
                            f"in {folder}: must not import {banned} (resolved {imported})"
                        )

if failures:
    for failure in failures:
        print(f"::error::{failure}")
    sys.exit(1)

print("layer rules clean")
PY
