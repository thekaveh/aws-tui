from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]


def _workflow(path: str) -> dict[str, Any]:
    return yaml.safe_load((REPO_ROOT / path).read_text(encoding="utf-8"))


def _step(workflow: dict[str, Any], job: str, name: str) -> dict[str, Any]:
    for step in workflow["jobs"][job]["steps"]:
        if step.get("name") == name:
            return step
    raise AssertionError(f"missing workflow step {job!r} / {name!r}")


def _assert_hashed_audit_pair(workflow_path: str, job: str) -> None:
    workflow = _workflow(workflow_path)
    export_run = _step(workflow, job, "export locked requirements")["run"]
    audit_run = _step(workflow, job, "pip-audit (locked dependencies)")["run"]

    assert "--no-emit-project" in export_run
    assert "--no-hashes" not in export_run
    assert "--python" in export_run
    assert "--require-hashes" in audit_run
    assert "--disable-pip" in audit_run
    assert "--python" in audit_run


def _matrix_values(workflow_path: str, job: str, key: str) -> list[str]:
    workflow = _workflow(workflow_path)
    return list(workflow["jobs"][job]["strategy"]["matrix"][key])


def _assert_supported_python_loop(run: str) -> None:
    assert "for py in 3.11 3.12 3.13; do" in run


def test_ci_dependency_audit_keeps_locked_hashes() -> None:
    _assert_hashed_audit_pair(".github/workflows/ci.yml", "security")
    assert _matrix_values(".github/workflows/ci.yml", "security", "python") == [
        "3.11",
        "3.12",
        "3.13",
    ]


def test_release_dependency_audit_keeps_locked_hashes() -> None:
    workflow = _workflow(".github/workflows/release.yml")
    _assert_hashed_audit_pair(".github/workflows/release.yml", "verify")
    _assert_supported_python_loop(
        _step(workflow, "verify", "pytest supported Python matrix")["run"]
    )
    export_run = _step(workflow, "verify", "export locked requirements")["run"]
    audit_run = _step(workflow, "verify", "pip-audit (locked dependencies)")["run"]
    _assert_supported_python_loop(export_run)
    _assert_supported_python_loop(audit_run)
    assert "requirements-audit-$py.txt" in export_run
    assert "requirements-audit-$py.txt" in audit_run


def test_release_smoke_install_covers_supported_python_versions() -> None:
    assert _matrix_values(".github/workflows/release.yml", "smoke-install", "python") == [
        "3.11",
        "3.12",
        "3.13",
    ]


def test_release_creation_does_not_depend_on_runner_gh_cli() -> None:
    release = (REPO_ROOT / ".github/workflows/release.yml").read_text(encoding="utf-8")

    assert "gh release create" not in release
    assert "api.github.com/repos" in release
