"""Sync the generated wiki tree to ``aws-tui.wiki.git`` (pushes ``master``)."""

from __future__ import annotations

import argparse
import contextlib
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from urllib.parse import urlsplit

DEFAULT_REMOTE = "git@github.com:thekaveh/aws-tui.wiki.git"
# Published by GitHub at https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/githubs-ssh-key-fingerprints
GITHUB_ED25519_KNOWN_HOST = (
    "github.com ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIOMqqnkVzrm0SdG6UOoqKLsabgH5C9okWi0dh2l9GKJl\n"
)

_DEFAULT_IDENT = {
    "GIT_AUTHOR_NAME": "aws-tui docs bot",
    "GIT_AUTHOR_EMAIL": "docs-bot@users.noreply.github.com",
    "GIT_COMMITTER_NAME": "aws-tui docs bot",
    "GIT_COMMITTER_EMAIL": "docs-bot@users.noreply.github.com",
}


def authenticated_remote(
    remote: str,
    key_path: str | Path,
    known_hosts_path: str | Path,
) -> str:
    del remote
    return " ".join(
        (
            "ssh",
            "-i",
            shlex.quote(str(key_path)),
            "-o",
            "IdentitiesOnly=yes",
            "-o",
            "StrictHostKeyChecking=yes",
            "-o",
            f"UserKnownHostsFile={shlex.quote(str(known_hosts_path))}",
            "-o",
            "GlobalKnownHostsFile=/dev/null",
        )
    )


def _write_github_known_hosts(path: Path) -> None:
    path.write_text(GITHUB_ED25519_KNOWN_HOST, encoding="utf-8")
    with contextlib.suppress(OSError, NotImplementedError):
        path.chmod(0o600)


def _ssh_host(remote: str) -> str | None:
    if "://" in remote:
        parsed = urlsplit(remote)
        return parsed.hostname if parsed.scheme in {"ssh", "git+ssh"} else None
    match = re.match(r"(?:[^@/:]+@)?(\[[^]]+\]|[^/:]+):", remote)
    if match is None:
        return None
    return match.group(1).strip("[]").casefold()


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
    if staged.returncode != 1:
        raise subprocess.CalledProcessError(
            staged.returncode,
            ["git", "diff", "--cached", "--quiet"],
        )
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
    known_hosts_path: str | Path | None = None,
) -> None:
    src = Path(src)
    if not push:
        # --check: validate we can init a repo and sync into it (no network).
        with tempfile.TemporaryDirectory() as tmp:
            subprocess.run(["git", "init", "-q", "-b", "master", tmp], check=True)
            sync_wiki(src, tmp)
        return
    with tempfile.TemporaryDirectory() as tmp:
        workspace = Path(tmp)
        repo_dir = workspace / "wiki"
        ssh_host = _ssh_host(remote)
        if ssh_host is None:
            raise ValueError("WIKI_REMOTE must be an SSH remote in push mode")
        if known_hosts_path is None:
            if ssh_host != "github.com":
                raise ValueError("WIKI_KNOWN_HOSTS is required for non-GitHub SSH remotes")
            trusted_hosts = workspace / "github_known_hosts"
            _write_github_known_hosts(trusted_hosts)
        else:
            trusted_hosts = Path(known_hosts_path)
        env = _env_with_ident()
        env["GIT_SSH_COMMAND"] = authenticated_remote(
            remote,
            key_path,
            trusted_hosts,
        )
        subprocess.run(
            ["git", "clone", "--depth", "1", remote, repo_dir],
            check=True,
            env=env,
        )
        sync_wiki(src, repo_dir)
        _commit_if_changed(repo_dir)
        subprocess.run(
            ["git", "push", remote, "master"],
            cwd=repo_dir,
            check=True,
            env=env,
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="push_wiki")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--check", action="store_true")
    mode.add_argument("--push", action="store_true")
    args = parser.parse_args(argv)
    repo_root = Path.cwd()
    remote = os.environ.get("WIKI_REMOTE", DEFAULT_REMOTE)
    key_path = os.environ.get("WIKI_DEPLOY_KEY", "")
    known_hosts_path = os.environ.get("WIKI_KNOWN_HOSTS")
    push_wiki(
        repo_root / "generated" / "wiki",
        remote,
        key_path,
        push=args.push,
        known_hosts_path=known_hosts_path,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
