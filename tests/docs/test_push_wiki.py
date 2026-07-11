import subprocess
from pathlib import Path

from scripts.docs.push_wiki import DEFAULT_REMOTE, authenticated_remote, sync_wiki


def test_default_remote_targets_wiki_git():
    assert DEFAULT_REMOTE == "git@github.com:thekaveh/aws-tui.wiki.git"


def test_authenticated_remote_uses_key_path():
    cmd = authenticated_remote(DEFAULT_REMOTE, "/home/runner/.ssh/wiki_key")
    assert "ssh" in cmd
    assert "/home/runner/.ssh/wiki_key" in cmd
    assert "IdentitiesOnly=yes" in cmd


def test_authenticated_remote_quotes_key_path_with_space():
    cmd = authenticated_remote(DEFAULT_REMOTE, "/tmp/my key/wiki_key")
    assert "'/tmp/my key/wiki_key'" in cmd  # shlex.quote wraps the spaced path


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
