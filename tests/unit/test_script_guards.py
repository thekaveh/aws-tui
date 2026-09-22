from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

# These tests exercise the repo's POSIX developer/CI shell scripts by invoking
# them through ``/bin/bash`` with a POSIX ``PATH``. They are inapplicable on
# Windows (no ``/bin/bash``; the scripts are POSIX-only dev tooling), matching
# the repo's existing convention of skipping POSIX-only tests on ``win32``.
pytestmark = pytest.mark.skipif(
    sys.platform == "win32",
    reason="POSIX shell scripts invoked via /bin/bash; not applicable on Windows",
)

REPO_ROOT = Path(__file__).resolve().parents[2]
BASE_PATH = "/usr/bin:/bin"


def _write_executable(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")
    path.chmod(path.stat().st_mode | 0o111)


def _run_script(script: str, *args: str, path: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["/bin/bash", str(REPO_ROOT / script), *args],
        cwd=REPO_ROOT,
        env={**os.environ, "PATH": path},
        text=True,
        capture_output=True,
        check=False,
    )


def _fake_uv(bin_dir: Path, *, version: str, body: str = 'printf "%s\\n" "$*"') -> None:
    _write_executable(
        bin_dir / "uv",
        f"""#!/bin/sh
if [ "$1" = "--version" ]; then
  echo "uv {version}"
  exit 0
fi
{body}
""",
    )


def _fake_docker(bin_dir: Path) -> None:
    _write_executable(
        bin_dir / "docker",
        """#!/bin/sh
if [ "$1" = "compose" ]; then
  exit 0
fi
if [ "$1" = "inspect" ]; then
  echo healthy
  exit 0
fi
exit 2
""",
    )


def test_run_with_uv_rejects_missing_uv(tmp_path: Path) -> None:
    result = _run_script("scripts/run-with-uv.sh", "pytest", path=f"{tmp_path}:{BASE_PATH}")

    assert result.returncode == 127
    assert "uv >= 0.11.19 is required; found: not installed" in result.stderr


def test_run_with_uv_rejects_stale_uv(tmp_path: Path) -> None:
    _fake_uv(tmp_path, version="0.5.7")

    result = _run_script("scripts/run-with-uv.sh", "pytest", path=f"{tmp_path}:{BASE_PATH}")

    assert result.returncode == 127
    assert "uv >= 0.11.19 is required; found 0.5.7" in result.stderr


def test_run_with_uv_forwards_arguments_to_current_uv(tmp_path: Path) -> None:
    _fake_uv(tmp_path, version="0.11.19")

    result = _run_script(
        "scripts/run-with-uv.sh",
        "python",
        "--version",
        path=f"{tmp_path}:{BASE_PATH}",
    )

    assert result.returncode == 0
    assert result.stdout.strip() == "run python --version"


def test_bootstrap_rejects_stale_uv_before_sync(tmp_path: Path) -> None:
    _fake_uv(tmp_path, version="0.5.7")

    result = _run_script("scripts/bootstrap.sh", path=f"{tmp_path}:{BASE_PATH}")

    assert result.returncode == 1
    assert "aws-tui requires uv >= 0.11.19; found 0.5.7" in result.stderr
    assert "uv sync --locked --all-groups" not in result.stdout


def test_bootstrap_installs_all_groups_before_pre_commit(tmp_path: Path) -> None:
    calls = tmp_path / "uv-calls.txt"
    _fake_uv(
        tmp_path,
        version="0.11.19",
        body=f"""printf "%s\\n" "$*" >> {calls}
exit 0
""",
    )

    result = _run_script("scripts/bootstrap.sh", path=f"{tmp_path}:{BASE_PATH}")

    assert result.returncode == 0
    assert calls.read_text(encoding="utf-8").splitlines() == [
        "python install 3.11",
        "sync --locked --all-groups",
        "run pre-commit install",
    ]


def _bootstrap_in(checkout: Path, *, path: str) -> subprocess.CompletedProcess[str]:
    """Run a copy of `bootstrap.sh` from inside `checkout`.

    The script resolves its own directory rather than trusting the working
    directory, so the copy has to live at `<checkout>/scripts/`.
    """
    (checkout / "scripts").mkdir(parents=True, exist_ok=True)
    script = checkout / "scripts" / "bootstrap.sh"
    script.write_text(
        (REPO_ROOT / "scripts" / "bootstrap.sh").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    return subprocess.run(
        ["/bin/bash", str(script)],
        cwd=checkout,
        env={**os.environ, "PATH": path},
        text=True,
        capture_output=True,
        check=False,
    )


def test_bootstrap_installs_hooks_from_a_linked_worktree(tmp_path: Path) -> None:
    """A linked worktree is a git checkout, and its `.git` is a FILE.

    `git worktree` and `git submodule` both write `.git` as a regular file
    holding a `gitdir:` pointer instead of creating a directory, so the
    `[ -d .git ]` test bootstrap used to run reported "not a git checkout" and
    silently skipped `pre-commit install` for every contributor developing in
    one. Built as a real worktree of a real repository rather than a
    hand-written pointer, so the fixture cannot drift from the shape git
    actually produces.
    """
    calls = tmp_path / "uv-calls.txt"
    _fake_uv(
        tmp_path,
        version="0.11.19",
        body=f"""printf "%s\\n" "$*" >> {calls}
exit 0
""",
    )
    origin = tmp_path / "origin"
    origin.mkdir()
    git = ["git", "-c", "user.email=t@example.com", "-c", "user.name=t"]
    subprocess.run([*git, "init", "-q", str(origin)], check=True, capture_output=True)
    (origin / "seed.txt").write_text("seed\n", encoding="utf-8")
    subprocess.run([*git, "-C", str(origin), "add", "seed.txt"], check=True, capture_output=True)
    subprocess.run(
        [*git, "-C", str(origin), "commit", "-q", "-m", "seed"],
        check=True,
        capture_output=True,
    )
    checkout = tmp_path / "linked"
    subprocess.run(
        [*git, "-C", str(origin), "worktree", "add", "-q", str(checkout)],
        check=True,
        capture_output=True,
    )
    assert (checkout / ".git").is_file(), "fixture is not the worktree shape this test is about"

    result = _bootstrap_in(checkout, path=f"{tmp_path}:{BASE_PATH}")

    assert result.returncode == 0, result.stderr
    assert "run pre-commit install" in calls.read_text(encoding="utf-8").splitlines()
    assert "skipping pre-commit hooks" not in result.stdout


def test_bootstrap_skips_hooks_without_a_git_checkout(tmp_path: Path) -> None:
    """The case the skip branch exists for: a downloaded tarball or zip.

    `pre-commit install` exits non-zero with no git dir, which under
    `set -euo pipefail` aborted bootstrap after a successful sync. Untested
    before this ticket, in either direction.
    """
    calls = tmp_path / "uv-calls.txt"
    _fake_uv(
        tmp_path,
        version="0.11.19",
        body=f"""printf "%s\\n" "$*" >> {calls}
exit 0
""",
    )

    result = _bootstrap_in(tmp_path / "tarball", path=f"{tmp_path}:{BASE_PATH}")

    assert result.returncode == 0, result.stderr
    assert "skipping pre-commit hooks" in result.stdout
    assert "run pre-commit install" not in calls.read_text(encoding="utf-8").splitlines()


def test_bootstrap_skips_hooks_when_the_git_pointer_dangles(tmp_path: Path) -> None:
    """A `.git` file can exist and still resolve to nothing.

    An orphaned or moved worktree leaves the pointer behind. `[ -e .git ]`
    would accept it and then abort bootstrap on the failing hook install, which
    is why the condition asks `git rev-parse --git-dir` instead of testing for
    the path.
    """
    calls = tmp_path / "uv-calls.txt"
    _fake_uv(
        tmp_path,
        version="0.11.19",
        body=f"""printf "%s\\n" "$*" >> {calls}
exit 0
""",
    )
    checkout = tmp_path / "orphaned"
    checkout.mkdir()
    (checkout / ".git").write_text(
        f"gitdir: {tmp_path / 'gone' / '.git' / 'worktrees' / 'orphaned'}\n",
        encoding="utf-8",
    )

    result = _bootstrap_in(checkout, path=f"{tmp_path}:{BASE_PATH}")

    assert result.returncode == 0, result.stderr
    assert "skipping pre-commit hooks" in result.stdout
    assert "run pre-commit install" not in calls.read_text(encoding="utf-8").splitlines()


def test_dev_tooling_declares_textual_cli_dependency() -> None:
    import tomllib

    project = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    dev_dependencies = project["dependency-groups"]["dev"]

    assert any(requirement.startswith("textual-dev>=") for requirement in dev_dependencies)


def test_dev_script_routes_through_stale_uv_guard(tmp_path: Path) -> None:
    _fake_uv(tmp_path, version="0.5.7")

    result = _run_script("scripts/dev.sh", path=f"{tmp_path}:{BASE_PATH}")

    assert result.returncode == 127
    assert "uv >= 0.11.19 is required; found 0.5.7" in result.stderr
    assert "textual run --dev" not in result.stdout


def test_s3_up_script_routes_seed_through_stale_uv_guard(tmp_path: Path) -> None:
    _fake_docker(tmp_path)
    _fake_uv(tmp_path, version="0.5.7")

    result = _run_script("scripts/test-services/s3/up.sh", path=f"{tmp_path}:{BASE_PATH}")

    assert result.returncode == 127
    assert "==> seeding buckets" in result.stdout
    assert "uv >= 0.11.19 is required; found 0.5.7" in result.stderr
    assert "dev S3 is up" not in result.stdout


def test_check_layers_rejects_missing_usable_runners(tmp_path: Path) -> None:
    _write_executable(
        tmp_path / "python3",
        """#!/bin/sh
exit 1
""",
    )

    result = _run_script("scripts/check-layers.sh", path=f"{tmp_path}:{BASE_PATH}")

    assert result.returncode == 127
    assert "uv >= 0.11.19 or python3 >= 3.11 is required" in result.stderr


def test_check_layers_uses_current_uv_runner(tmp_path: Path) -> None:
    _fake_uv(
        tmp_path,
        version="0.11.19",
        body=f"""if [ "$1" = "run" ] && [ "$2" = "python" ]; then
  shift 2
  exec {sys.executable} "$@"
fi
exit 2
""",
    )

    result = _run_script("scripts/check-layers.sh", path=f"{tmp_path}:{BASE_PATH}")

    assert result.returncode == 0
    assert result.stdout.strip() == "layer rules clean"


def test_check_layers_resolves_relative_imports_from_package_init(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    scripts = repo / "scripts"
    package = repo / "src" / "aws_tui" / "vm"
    scripts.mkdir(parents=True)
    package.mkdir(parents=True)
    (scripts / "check-layers.sh").write_bytes(
        (REPO_ROOT / "scripts" / "check-layers.sh").read_bytes()
    )
    (package / "__init__.py").write_text("from ..ui import Widget\n", encoding="utf-8")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _write_executable(
        bin_dir / "python3",
        f'#!/bin/sh\nexec "{sys.executable}" "$@"\n',
    )

    result = subprocess.run(
        ["/bin/bash", str(scripts / "check-layers.sh")],
        cwd=repo,
        env={**os.environ, "PATH": f"{bin_dir}:{BASE_PATH}"},
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 1
    assert "resolved aws_tui.ui" in result.stdout
