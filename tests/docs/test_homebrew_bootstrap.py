"""Execute the documented generation recipe with controlled tool boundaries."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

_GUIDE = Path(__file__).parents[2] / "docs/homebrew-bootstrap.md"


def generation_recipe() -> str:
    blocks = re.findall(r"```bash\n(.*?)\n```", _GUIDE.read_text(), re.S)
    return next(block for block in blocks if "poet" in block and "Formula/aws-tui.rb" in block)


_CONTROLLED_TOOL = r"""
import json, os, pathlib, shutil, sys
name = pathlib.Path(sys.argv[0]).name
args = sys.argv[1:]
mode = os.environ["RECIPE_MODE"]
with open(os.environ["RECIPE_CALLS"], "a") as stream:
    stream.write(json.dumps([name, *args]) + "\n")
if name == "python3.12":
    assert args[:2] == ["-m", "venv"]
    folder = pathlib.Path(args[2]) / "bin"
    folder.mkdir(parents=True)
    for tool in ["python", "poet"]:
        shutil.copy2(sys.argv[0], folder / tool)
elif name == "python":
    assert args[:2] == ["-m", "pip"]
    if args[2] == "install" and mode == "install-fail":
        sys.exit(8)
elif name == "poet":
    assert args == ["-f", "aws-tui"]
    if mode == "generator-fail":
        print("partial formula")
        sys.exit(9)
    if mode != "empty":
        print("class AwsTui <" if mode == "invalid" else "class AwsTui < Formula\nend")
elif name == "ruby":
    assert args[0] == "-c"
    assert pathlib.Path(args[1]).stat().st_size > 0
    actual_ruby = os.environ.get("RECIPE_RUBY")
    if actual_ruby:
        os.execv(actual_ruby, [actual_ruby, *args])
    if mode == "invalid":
        sys.exit(1)
elif name == "shasum":
    print("0" * 64, "fixture.tar.gz")
elif name not in ["git", "curl", "brew", "pipgrip"]:
    sys.exit(127)
"""


@pytest.mark.parametrize("existing", [False, True])
@pytest.mark.parametrize("mode", ["generator-fail", "empty", "invalid", "install-fail", "success"])
def test_recipe_preserves_formula_until_generation_and_validation_succeed(tmp_path, existing, mode):
    bash = shutil.which("bash")
    if bash is None:
        pytest.skip("documented Bash procedure needs a local Bash interpreter")
    home = tmp_path / "home with spaces"
    tap = home / "repos" / "homebrew-aws-tui"
    formula = tap / "Formula" / "aws-tui.rb"
    formula.parent.mkdir(parents=True)
    original = b"# existing valid formula\nclass AwsTui < Formula\nend\n"
    if existing:
        formula.write_bytes(original)
    tools = tmp_path / "tools"
    tools.mkdir()
    for name in [
        "python3.12",
        "python",
        "poet",
        "ruby",
        "git",
        "curl",
        "brew",
        "pipgrip",
        "shasum",
    ]:
        path = tools / name
        path.write_text(f"#!{sys.executable}\n" + _CONTROLLED_TOOL)
        path.chmod(0o755)
    calls = tmp_path / "calls.jsonl"
    scratch = tmp_path / "scratch with spaces"
    scratch.mkdir()
    unrelated_cache = scratch / "unrelated-cache"
    unrelated_cache.write_bytes(b"preserve external tool state")
    env = dict(
        os.environ,
        HOME=str(home),
        TMPDIR=str(scratch),
        RECIPE_MODE=mode,
        RECIPE_CALLS=str(calls),
        RECIPE_RUBY=shutil.which("ruby") or "",
    )
    env["PATH"] = str(tools) + os.pathsep + os.defpath
    result = subprocess.run(
        [bash, "-c", generation_recipe()],
        cwd=tap,
        env=env,
        capture_output=True,
        text=True,
        timeout=20,
    )
    if mode == "success":
        assert result.returncode == 0, result.stderr
        assert formula.read_text() == "class AwsTui < Formula\nend\n"
        invoked = [json.loads(line) for line in calls.read_text().splitlines()]
        assert ["poet", "-f", "aws-tui"] in invoked
        install = next(call for call in invoked if call[:4] == ["python", "-m", "pip", "install"])
        assert "aws-tui==0.9.0" in install
        assert "homebrew-pypi-poet==0.10.0" in install
        assert "setuptools==80.9.0" in install
        assert ["python", "-m", "pip", "check"] in invoked
        assert any(call[:2] == ["ruby", "-c"] for call in invoked)
    else:
        assert result.returncode != 0, result.stdout
        if existing:
            assert formula.read_bytes() == original
        else:
            assert not formula.exists()
    assert list(formula.parent.iterdir()) == ([formula] if formula.exists() else [])
    assert list(scratch.glob("aws-tui-homebrew.*")) == []
    assert unrelated_cache.read_bytes() == b"preserve external tool state"


def test_recipe_remains_future_release_preparation_with_explicit_gate():
    guide = _GUIDE.read_text()
    assert "poet-py3.12" not in guide
    assert "pipx install homebrew-pypi-poet" not in guide
    assert "#267" in guide
    assert "gated" in guide.lower()
    assert "runtime" in guide.lower()
    assert "sdist" in guide.lower()
