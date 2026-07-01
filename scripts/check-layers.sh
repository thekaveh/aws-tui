#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."

python - <<'PY'
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
