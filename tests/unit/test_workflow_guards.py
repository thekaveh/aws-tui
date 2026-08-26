from __future__ import annotations

import tomllib
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


def _executable_lines(run: str) -> list[str]:
    return [
        line.strip()
        for line in run.splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]


def _assert_step_has_command(
    workflow: dict[str, Any],
    job: str,
    name: str,
    command_prefix: str,
    *needles: str,
) -> None:
    run = _step(workflow, job, name)["run"]
    assert any(
        line.startswith(command_prefix) and all(needle in line for needle in needles)
        for line in _executable_lines(run)
    ), f"missing executable {command_prefix!r} line in {job!r} / {name!r}"


def test_every_workflow_job_has_a_bounded_timeout() -> None:
    for workflow_path in (
        ".github/workflows/ci.yml",
        ".github/workflows/pages.yml",
        ".github/workflows/release.yml",
    ):
        workflow = _workflow(workflow_path)
        for job_name, job in workflow["jobs"].items():
            timeout = job.get("timeout-minutes")
            assert isinstance(timeout, int), f"{workflow_path}:{job_name} has no timeout"
            assert 1 <= timeout <= 60, f"{workflow_path}:{job_name} timeout is unbounded"


def test_ci_dependency_audit_keeps_locked_hashes() -> None:
    _assert_hashed_audit_pair(".github/workflows/ci.yml", "security")
    assert _matrix_values(".github/workflows/ci.yml", "security", "python") == [
        "3.11",
        "3.12",
        "3.13",
    ]


def test_ci_pytest_tiers_stay_wired() -> None:
    workflow = _workflow(".github/workflows/ci.yml")
    assert workflow["jobs"]["unit"]["timeout-minutes"] == 30
    assert workflow["jobs"]["integration"]["timeout-minutes"] == 20
    assert workflow["jobs"]["coverage"]["timeout-minutes"] == 30

    _assert_step_has_command(
        workflow,
        "unit",
        "pytest (unit + in-process integration)",
        "uv run",
        "--python ${{ matrix.python }}",
        "pytest",
        "tests/unit",
        "tests/integration",
    )
    _assert_step_has_command(
        workflow,
        "integration",
        "pytest (integration tier)",
        "uv run",
        "pytest",
        "tests/integration",
        "-m integration",
    )
    _assert_step_has_command(
        workflow,
        "coverage",
        "pytest coverage (unit + in-process integration)",
        "uv run",
        "--python 3.12",
        "pytest",
        "tests/unit",
        "tests/integration",
        "--cov=aws_tui",
        "--cov-report=xml",
    )
    _assert_step_has_command(
        workflow,
        "snapshot",
        "pytest (snapshot tier)",
        "uv run",
        "pytest",
        "tests/snapshot",
    )
    _assert_step_has_command(
        workflow,
        "e2e",
        "pytest (e2e tier)",
        "uv run",
        "pytest",
        "tests/e2e",
    )


def test_integration_marker_is_reserved_for_minio_tier() -> None:
    marked_files = {
        path.relative_to(REPO_ROOT).as_posix()
        for path in (REPO_ROOT / "tests/integration").rglob("*.py")
        if "pytest.mark.integration" in path.read_text(encoding="utf-8")
    }

    assert marked_files == {
        "tests/integration/test_cross_fs_minio.py",
        "tests/integration/test_s3_fs_minio.py",
    }


def test_release_dependency_audit_keeps_locked_hashes() -> None:
    workflow = _workflow(".github/workflows/release.yml")
    _assert_hashed_audit_pair(".github/workflows/release.yml", "verify")
    export_run = _step(workflow, "verify", "export locked requirements")["run"]
    audit_run = _step(workflow, "verify", "pip-audit (locked dependencies)")["run"]
    _assert_supported_python_loop(export_run)
    _assert_supported_python_loop(audit_run)
    assert "requirements-audit-$py.txt" in export_run
    assert "requirements-audit-$py.txt" in audit_run


def test_release_version_and_changelog_are_guarded() -> None:
    workflow = _workflow(".github/workflows/release.yml")
    run = _step(workflow, "verify", "tag-version match")["run"]

    assert "scripts.release_version stage-testpypi" in run
    assert "scripts.release_version check-changelog" in run


def test_release_pytest_tiers_stay_wired() -> None:
    workflow = _workflow(".github/workflows/release.yml")
    assert workflow["jobs"]["verify"]["timeout-minutes"] == 45

    _assert_step_has_command(
        workflow,
        "verify",
        "pytest supported Python edge versions",
        "for py",
        "for py in 3.11 3.13; do",
    )
    _assert_step_has_command(
        workflow,
        "verify",
        "pytest supported Python edge versions",
        "uv sync",
        'uv sync --locked --python "$py" --group docs',
    )
    matrix_run = _step(workflow, "verify", "pytest supported Python edge versions")["run"]
    assert "uv sync --locked --python 3.12 --group docs" in matrix_run
    assert 'uv run --python "3.12" pytest' not in matrix_run
    _assert_step_has_command(
        workflow,
        "verify",
        "pytest supported Python edge versions",
        "uv run",
        'uv run --python "$py" pytest tests/unit tests/integration -v',
    )
    _assert_step_has_command(
        workflow,
        "verify",
        "pytest coverage (unit + in-process integration)",
        "uv run",
        "--python 3.12",
        "pytest",
        "tests/unit",
        "tests/integration",
        "--cov=aws_tui",
        "--cov-report=xml",
    )
    _assert_step_has_command(
        workflow,
        "verify",
        "pytest (MinIO integration tier)",
        "uv run",
        "--python 3.12",
        "pytest",
        "-m integration",
    )
    _assert_step_has_command(
        workflow,
        "verify",
        "pytest (snapshot tier)",
        "uv run",
        "--python 3.12",
        "pytest",
        "tests/snapshot",
    )
    _assert_step_has_command(
        workflow,
        "verify",
        "pytest (e2e tier)",
        "uv run",
        "--python 3.12",
        "pytest",
        "tests/e2e",
    )


def test_release_smoke_install_covers_supported_python_versions() -> None:
    assert _matrix_values(".github/workflows/release.yml", "smoke-install", "python") == [
        "3.11",
        "3.12",
        "3.13",
    ]


def test_release_runs_behavioral_tests_on_non_linux_platforms_before_publish() -> None:
    workflow = _workflow(".github/workflows/release.yml")

    assert _matrix_values(".github/workflows/release.yml", "platform-tests", "os") == [
        "macos-14",
        "windows-latest",
    ]
    assert workflow["jobs"]["platform-tests"]["needs"] == "verify"
    _assert_step_has_command(
        workflow,
        "platform-tests",
        "pytest platform behavior",
        "uv run",
        "--python 3.12",
        "pytest",
        "tests/unit",
        "tests/integration",
    )
    assert set(workflow["jobs"]["publish-pypi"]["needs"]) == {
        "verify",
        "platform-tests",
        "smoke-install",
        "lowest-supported-dependencies",
    }


def test_release_checks_declared_minimum_s3_dependency_models_before_publish() -> None:
    workflow = _workflow(".github/workflows/release.yml")
    job = workflow["jobs"]["lowest-supported-dependencies"]

    assert job["needs"] == "verify"
    assert job["timeout-minutes"] == 20
    install = _step(
        workflow, "lowest-supported-dependencies", "install declared minimum dependencies"
    )["run"]
    assert "uv pip install --resolution lowest-direct" in install
    assert '--python "$PY" .' in install
    assert "aioboto3==" not in install
    assert "botocore==" not in install
    exercise = _step(
        workflow, "lowest-supported-dependencies", "exercise minimum dependency runtime"
    )["run"]
    assert "tests/unit/infra/test_connection_resolver.py" in exercise
    assert "tests/minimum_runtime/test_dependency_floors.py" in exercise
    assert "tests/unit/infra/test_keychain.py" in exercise
    assert "tests/unit/test_app_sanity.py" in exercise
    assert "tests/unit/vm/test_vmx_smoke.py" in exercise

    model_check = _step(
        workflow,
        "lowest-supported-dependencies",
        "assert required S3 request model members",
    )["run"]
    assert "import aws_tui" in model_check
    for operation, member in (
        ("CopyObject", "CopySourceIfMatch"),
        ("DeleteObject", "IfMatch"),
        ("DeleteObject", "IfMatchLastModifiedTime"),
        ("DeleteObject", "IfMatchSize"),
    ):
        assert operation in model_check
        assert member in model_check


def test_textual_range_matches_the_audited_compatibility_adapter() -> None:
    project = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    requirement = next(
        value for value in project["project"]["dependencies"] if value.startswith("textual")
    )

    assert requirement == "textual==8.2.8"


def test_ci_and_release_require_documentation_contracts() -> None:
    ci = _workflow(".github/workflows/ci.yml")
    release = _workflow(".github/workflows/release.yml")

    assert "docs" in ci["jobs"]["gate"]["needs"]
    _assert_step_has_command(
        ci, "docs", "documentation drift and strict build", "make", "docs-check"
    )
    _assert_step_has_command(
        ci,
        "docs",
        "pytest (documentation contracts)",
        "uv run",
        "pytest",
        "tests/docs",
    )
    _assert_step_has_command(
        release,
        "verify",
        "documentation drift and strict build",
        "make",
        "docs-check",
    )
    _assert_step_has_command(
        release,
        "verify",
        "pytest (documentation contracts)",
        "uv run",
        "pytest",
        "tests/docs",
    )


def test_pages_push_is_main_only_and_manual_dispatch_accepts_a_branch() -> None:
    workflow = _workflow(".github/workflows/pages.yml")

    assert workflow[True]["workflow_dispatch"] is None
    manual_or_main = "github.ref == 'refs/heads/main' || github.event_name == 'workflow_dispatch'"
    assert workflow["jobs"]["build"]["if"] == manual_or_main
    assert workflow["jobs"]["wiki"]["if"] == manual_or_main
    assert workflow["env"]["UV_VERSION"] == "0.11.19"
    for job_name in ("build", "wiki"):
        setup_uv = next(
            step
            for step in workflow["jobs"][job_name]["steps"]
            if str(step.get("uses", "")).startswith("astral-sh/setup-uv@")
        )
        assert setup_uv["with"]["version"] == "${{ env.UV_VERSION }}"
    assert "assets/screenshots/aws-tui-running.png" in workflow[True]["push"]["paths"]
    assert workflow["permissions"] == {"contents": "read"}
    assert workflow["jobs"]["deploy"]["permissions"] == {
        "contents": "read",
        "pages": "write",
        "id-token": "write",
    }
    _assert_step_has_command(
        workflow, "build", "documentation drift and strict build", "make", "docs-check"
    )
    _assert_step_has_command(
        workflow, "build", "documentation contract tests", "uv run", "pytest", "tests/docs"
    )


def test_workflows_reject_stale_lockfiles() -> None:
    for workflow_path in (
        ".github/workflows/ci.yml",
        ".github/workflows/pages.yml",
        ".github/workflows/release.yml",
    ):
        text = (REPO_ROOT / workflow_path).read_text(encoding="utf-8")
        assert "--frozen" not in text, workflow_path
        for line in text.splitlines():
            if "uv sync " in line or "uv export " in line:
                assert "--locked" in line, f"{workflow_path}: {line.strip()}"


def test_package_workflows_reject_denied_artifact_members() -> None:
    for workflow_path, job in (
        (".github/workflows/ci.yml", "pkg"),
        (".github/workflows/release.yml", "verify"),
    ):
        workflow = _workflow(workflow_path)
        _assert_step_has_command(
            workflow,
            job,
            "validate artifact contents",
            "uv run",
            "python",
            "-m scripts.check_dist",
            "dist/",
        )


def test_wiki_deploy_key_is_validated_before_write() -> None:
    workflow = _workflow(".github/workflows/pages.yml")
    run = _step(workflow, "wiki", "write wiki deploy key")["run"]

    assert 'if [[ -z "${WIKI_DEPLOY_KEY:-}" ]]' in run
    assert "::error::WIKI_DEPLOY_KEY is not configured" in run


def test_homebrew_checkout_does_not_persist_cross_repo_token() -> None:
    workflow = _workflow(".github/workflows/release.yml")
    checkout = next(
        step
        for step in workflow["jobs"]["bump-homebrew"]["steps"]
        if str(step.get("uses", "")).startswith("actions/checkout@")
    )

    assert checkout["with"]["persist-credentials"] is False


def test_ci_gate_rejects_every_non_success_result() -> None:
    workflow = _workflow(".github/workflows/ci.yml")
    run = _step(workflow, "gate", "require every CI tier")["run"]

    assert '[[ "$result" != "success" ]]' in run


def test_release_creation_does_not_depend_on_runner_gh_cli() -> None:
    release = (REPO_ROOT / ".github/workflows/release.yml").read_text(encoding="utf-8")

    assert "gh release create" not in release
    assert "api.github.com/repos" in release
