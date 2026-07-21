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
    assert "uv sync --frozen" not in result.stdout


def test_bootstrap_installs_pre_commit_python_before_sync(tmp_path: Path) -> None:
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
        "sync --frozen",
        "run pre-commit install",
    ]


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
