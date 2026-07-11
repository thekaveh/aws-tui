"""Sync the generated wiki tree to ``aws-tui.wiki.git`` (pushes ``master``)."""

from __future__ import annotations

import argparse
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

DEFAULT_REMOTE = "git@github.com:thekaveh/aws-tui.wiki.git"

_DEFAULT_IDENT = {
    "GIT_AUTHOR_NAME": "aws-tui docs bot",
    "GIT_AUTHOR_EMAIL": "docs-bot@users.noreply.github.com",
    "GIT_COMMITTER_NAME": "aws-tui docs bot",
    "GIT_COMMITTER_EMAIL": "docs-bot@users.noreply.github.com",
}


def authenticated_remote(remote: str, key_path: str | Path) -> str:
    return f"ssh -i {shlex.quote(str(key_path))} -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new"


def _env_with_ident() -> dict[str, str]:
    env = dict(os.environ)
    for key, value in _DEFAULT_IDENT.items():
        env.setdefault(key, value)
    return env


def sync_wiki(src: str | Path, repo_dir: str | Path) -> None:
    src = Path(src)
    repo_dir = Path(repo_dir)
    for existing in repo_dir.iterdir():
        if existing.name == ".git":
            continue
        if existing.is_dir():
            shutil.rmtree(existing)
        else:
            existing.unlink()
    for item in src.iterdir():
        target = repo_dir / item.name
        if item.is_dir():
            shutil.copytree(item, target)
        else:
            shutil.copy2(item, target)


def _commit_if_changed(repo_dir: str | Path) -> None:
    repo_dir = Path(repo_dir)
    env = _env_with_ident()
    subprocess.run(["git", "add", "-A"], cwd=repo_dir, check=True, env=env)
    staged = subprocess.run(["git", "diff", "--cached", "--quiet"], cwd=repo_dir, env=env)
    if staged.returncode == 0:
        return  # nothing staged — no-op
    subprocess.run(
        ["git", "commit", "-m", "docs: sync generated wiki"],
        cwd=repo_dir,
        check=True,
        env=env,
    )


def push_wiki(
    src: str | Path,
    remote: str,
    key_path: str | Path,
    *,
    push: bool = False,
) -> None:
    src = Path(src)
    if not push:
        # --check: validate we can init a repo and sync into it (no network).
        with tempfile.TemporaryDirectory() as tmp:
            subprocess.run(["git", "init", "-q", "-b", "master", tmp], check=True)
            sync_wiki(src, tmp)
        return
    env = _env_with_ident()
    env["GIT_SSH_COMMAND"] = authenticated_remote(remote, key_path)
    with tempfile.TemporaryDirectory() as tmp:
        subprocess.run(
            ["git", "clone", "--depth", "1", remote, tmp],
            check=True,
            env=env,
        )
        sync_wiki(src, tmp)
        _commit_if_changed(tmp)
        subprocess.run(["git", "push", remote, "master"], cwd=tmp, check=True, env=env)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="push_wiki")
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--push", action="store_true")
    args = parser.parse_args(argv)
    repo_root = Path.cwd()
    remote = os.environ.get("WIKI_REMOTE", DEFAULT_REMOTE)
    key_path = os.environ.get("WIKI_DEPLOY_KEY", "")
    push_wiki(repo_root / "generated" / "wiki", remote, key_path, push=args.push)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
