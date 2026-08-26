import os
import subprocess
from pathlib import Path

import pytest
from scripts.docs.push_wiki import (
    DEFAULT_REMOTE,
    GITHUB_ED25519_KNOWN_HOST,
    _ssh_host,
    _write_github_known_hosts,
    authenticated_remote,
    sync_wiki,
)


def test_default_remote_targets_wiki_git():
    assert DEFAULT_REMOTE == "git@github.com:thekaveh/aws-tui.wiki.git"


def test_authenticated_remote_uses_key_path():
    cmd = authenticated_remote(
        DEFAULT_REMOTE,
        "/home/runner/.ssh/wiki_key",
        "/home/runner/.ssh/github_known_hosts",
    )
    assert "ssh" in cmd
    assert "/home/runner/.ssh/wiki_key" in cmd
    assert "IdentitiesOnly=yes" in cmd
    assert "StrictHostKeyChecking=yes" in cmd
    assert "UserKnownHostsFile=/home/runner/.ssh/github_known_hosts" in cmd
    assert "GlobalKnownHostsFile=/dev/null" in cmd
    assert "accept-new" not in cmd


def test_authenticated_remote_quotes_key_path_with_space():
    cmd = authenticated_remote(
        DEFAULT_REMOTE,
        "/tmp/my key/wiki_key",
        "/tmp/my key/known hosts",
    )
    assert "'/tmp/my key/wiki_key'" in cmd  # shlex.quote wraps the spaced path
    assert "UserKnownHostsFile='/tmp/my key/known hosts'" in cmd


def test_bundled_github_host_key_is_written_privately(tmp_path):
    known_hosts = tmp_path / "known_hosts"

    _write_github_known_hosts(known_hosts)

    assert known_hosts.read_text(encoding="utf-8") == GITHUB_ED25519_KNOWN_HOST
    assert GITHUB_ED25519_KNOWN_HOST.startswith("github.com ssh-ed25519 ")
    if os.name == "posix":
        assert known_hosts.stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize(
    ("remote", "host"),
    [
        ("git@github.com:other/wiki.git", "github.com"),
        ("ssh://git@github.com/other/wiki.git", "github.com"),
        ("https://github.com/other/wiki.git", None),
        ("git@example.com:docs/wiki.git", "example.com"),
    ],
)
def test_ssh_host_detection(remote: str, host: str | None) -> None:
    assert _ssh_host(remote) == host


def test_non_github_ssh_remote_requires_an_explicit_trust_file(tmp_path):
    from scripts.docs.push_wiki import push_wiki

    src = tmp_path / "wiki"
    src.mkdir()

    with pytest.raises(ValueError, match="non-GitHub SSH"):
        push_wiki(
            src,
            "git@example.com:docs/wiki.git",
            "/tmp/wiki-key",
            push=True,
        )


def test_push_rejects_non_ssh_remote(tmp_path):
    from scripts.docs.push_wiki import push_wiki

    src = tmp_path / "wiki"
    src.mkdir()

    with pytest.raises(ValueError, match="must be an SSH remote"):
        push_wiki(
            src,
            "https://github.com/other/wiki.git",
            "/tmp/wiki-key",
            push=True,
        )


def test_sync_wiki_preserves_git_and_removes_stale(tmp_path):
    src = tmp_path / "src"
    dst = tmp_path / "dst"
    src.mkdir()
    dst.mkdir()
    (dst / ".git").mkdir()
    (dst / ".git" / "HEAD").write_text("ref: refs/heads/master\n")
    (dst / "Stale.md").write_text("old\n")
    (src / "Home.md").write_text("new\n")
    sync_wiki(src, dst)
    assert (dst / "Home.md").read_text() == "new\n"
    assert not (dst / "Stale.md").exists()  # stale removed
    assert (dst / ".git" / "HEAD").is_file()  # .git preserved


def _git(repo: Path, *args: str, env=None):
    return subprocess.run(
        ["git", *args], cwd=repo, env=env, capture_output=True, text=True, check=True
    )


def test_push_wiki_commits_with_default_identity_when_unset(tmp_path, monkeypatch):
    # Isolate from the developer's global git identity.
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", "/dev/null")
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.delenv("GIT_AUTHOR_NAME", raising=False)
    monkeypatch.delenv("GIT_AUTHOR_EMAIL", raising=False)
    monkeypatch.delenv("GIT_COMMITTER_NAME", raising=False)
    monkeypatch.delenv("GIT_COMMITTER_EMAIL", raising=False)

    from scripts.docs.push_wiki import _commit_if_changed

    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "master")
    (repo / "Home.md").write_text("hello\n")
    _commit_if_changed(repo)  # must not raise "empty ident name not allowed"
    log = subprocess.run(["git", "log", "--oneline"], cwd=repo, capture_output=True, text=True)
    assert log.returncode == 0
    assert log.stdout.strip()  # a commit exists


def test_commit_if_changed_is_noop_when_clean(tmp_path):
    from scripts.docs.push_wiki import _commit_if_changed

    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "master")
    _git(
        repo,
        "-c",
        "user.name=x",
        "-c",
        "user.email=x@y.z",
        "commit",
        "-q",
        "--allow-empty",
        "-m",
        "base",
    )
    before = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True
    ).stdout
    _commit_if_changed(repo)  # nothing staged → no new commit
    after = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True
    ).stdout
    assert before == after


def test_commit_if_changed_raises_when_git_diff_fails(tmp_path, monkeypatch):
    from scripts.docs.push_wiki import _commit_if_changed

    calls = 0

    def run(command, **kwargs):
        nonlocal calls
        del kwargs
        calls += 1
        if command[:3] == ["git", "diff", "--cached"]:
            return subprocess.CompletedProcess(command, 128)
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(subprocess, "run", run)

    with pytest.raises(subprocess.CalledProcessError) as exc_info:
        _commit_if_changed(tmp_path)

    assert exc_info.value.returncode == 128
    assert calls == 2
