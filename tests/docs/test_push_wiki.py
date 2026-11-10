import hashlib
import json
import os
import subprocess
from pathlib import Path

import pytest
import scripts.docs.push_wiki as push_wiki_module
from scripts.docs.push_wiki import (
    DEFAULT_REMOTE,
    GITHUB_ED25519_KNOWN_HOST,
    _ssh_host,
    _write_github_known_hosts,
    authenticated_remote,
    sync_wiki,
)


@pytest.mark.parametrize(
    ("argv", "expected_push"),
    [([], False), (["--check"], False), (["--push"], True)],
)
def test_main_selects_exactly_one_wiki_mode(
    argv: list[str],
    expected_push: bool,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[bool] = []
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        push_wiki_module,
        "push_wiki",
        lambda *_args, push, **_kwargs: calls.append(push),
    )

    assert push_wiki_module.main(argv) == 0
    assert calls == [expected_push]


def test_main_rejects_check_and_push_together() -> None:
    with pytest.raises(SystemExit) as exc_info:
        push_wiki_module.main(["--check", "--push"])

    assert exc_info.value.code == 2


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


def test_sync_wiki_bootstrap_preserves_unowned_content_and_git(tmp_path):
    src = tmp_path / "src"
    dst = tmp_path / "dst"
    src.mkdir()
    dst.mkdir()
    (dst / ".git").mkdir()
    (dst / ".git" / "HEAD").write_text("ref: refs/heads/master\n")
    (dst / "Stale.md").write_text("old\n")
    (dst / "img").mkdir()
    (dst / "img" / "aws-tui-poster.png").write_bytes(b"handmade poster")
    (dst / "img" / "nested").mkdir()
    (dst / "img" / "nested" / "asset.bin").write_bytes(b"unowned asset")
    (dst / "Home.md").write_text("previous generated page\n")
    (src / "Home.md").write_text("new\n")
    (src / "diagrams").mkdir()
    (src / "diagrams" / "overview.svg").write_text("generated diagram\n")
    sync_wiki(src, dst)
    assert (dst / "Home.md").read_text() == "new\n"
    assert (dst / "Stale.md").read_text() == "old\n"
    assert (dst / "img" / "aws-tui-poster.png").read_bytes() == b"handmade poster"
    assert (dst / "img" / "nested" / "asset.bin").read_bytes() == b"unowned asset"
    assert (dst / "diagrams" / "overview.svg").read_text() == "generated diagram\n"
    assert (dst / ".git" / "HEAD").is_file()  # .git preserved
    ownership = json.loads((dst / ".aws-tui-wiki-owned.json").read_text())
    assert ownership == {
        "schema": 1,
        "files": {
            "Home.md": hashlib.sha256(b"new\n").hexdigest(),
            "diagrams/overview.svg": hashlib.sha256(b"generated diagram\n").hexdigest(),
        },
    }


def _wiki_trees(tmp_path):
    src, dst = tmp_path / "src", tmp_path / "dst"
    src.mkdir()
    dst.mkdir()
    (src / "Home.md").write_text("new\n")
    (dst / "Home.md").write_text("existing\n")
    return src, dst


def _tree_state(root):
    return {
        path.relative_to(root).as_posix(): (
            ("symlink", str(path.readlink()))
            if path.is_symlink()
            else ("directory",)
            if path.is_dir()
            else ("file", path.read_bytes())
        )
        for path in root.rglob("*")
    }


def _write_ownership(dst, files):
    (dst / ".aws-tui-wiki-owned.json").write_text(json.dumps({"schema": 1, "files": files}))


def test_sync_wiki_removes_only_unchanged_retired_owned_files(tmp_path):
    src, dst = _wiki_trees(tmp_path)
    (src / "nested").mkdir()
    (src / "nested" / "Retired.md").write_text("generated\n")
    sync_wiki(src, dst)
    (src / "nested" / "Retired.md").unlink()
    (dst / "nested" / "Handwritten.md").write_text("keep me\n")
    (dst / "Home.md").write_text("current generated paths remain canonical\n")

    sync_wiki(src, dst)

    assert not (dst / "nested" / "Retired.md").exists()
    assert (dst / "nested" / "Handwritten.md").read_text() == "keep me\n"
    assert (dst / "Home.md").read_text() == "new\n"
    assert set(json.loads((dst / ".aws-tui-wiki-owned.json").read_text())["files"]) == {"Home.md"}


def test_sync_wiki_refuses_modified_retired_file_before_any_writes(tmp_path):
    src, dst = _wiki_trees(tmp_path)
    (src / "Retired.md").write_text("generated\n")
    sync_wiki(src, dst)
    (src / "Retired.md").unlink()
    (src / "Home.md").write_text("next generation\n")
    (dst / "Retired.md").write_text("maintainer edits\n")
    before = _tree_state(dst)

    with pytest.raises(ValueError, match=r"Retired\.md"):
        sync_wiki(src, dst)

    assert _tree_state(dst) == before


@pytest.mark.parametrize(
    "registry",
    [
        "not JSON",
        "[]",
        "null",
        '{"schema": 2, "files": {}}',
        '{"schema": true, "files": {}}',
        '{"schema": 1}',
        '{"schema": 1, "files": []}',
        '{"schema": 1, "files": {"Old.md": "bad"}}',
        '{"schema": 1, "files": {"Old.md": 0}}',
        '{"schema": 1, "schema": 1, "files": {}}',
        '{"schema": 1, "files": {"Old.md": "' + "A" * 64 + '"}}',
    ],
)
def test_sync_wiki_rejects_invalid_registry_before_writes(tmp_path, registry):
    src, dst = _wiki_trees(tmp_path)
    (dst / ".aws-tui-wiki-owned.json").write_text(registry)
    before = _tree_state(dst)

    with pytest.raises(ValueError, match="ownership"):
        sync_wiki(src, dst)

    assert _tree_state(dst) == before


@pytest.mark.parametrize(
    "path",
    [
        "",
        ".",
        "..",
        "../outside",
        "/outside",
        "a/../b",
        "a//b",
        "./a",
        "a/",
        "a\\b",
        "C:/outside",
        "C:outside",
        ".git/config",
        "nested/.git/config",
        ".GIT/config",
        ".aws-tui-wiki-owned.json",
        "a/./b",
        "a\x00b",
        "a. /b",
    ],
)
def test_sync_wiki_rejects_unsafe_owned_paths_before_writes(tmp_path, path):
    src, dst = _wiki_trees(tmp_path)
    _write_ownership(dst, {path: "0" * 64})
    before = _tree_state(dst)

    with pytest.raises(ValueError, match="ownership"):
        sync_wiki(src, dst)

    assert _tree_state(dst) == before


def test_sync_wiki_rejects_overlapping_owned_file_paths(tmp_path):
    src, dst = _wiki_trees(tmp_path)
    _write_ownership(dst, {"a": "0" * 64, "a/b": "0" * 64})
    before = _tree_state(dst)

    with pytest.raises(ValueError, match="ownership"):
        sync_wiki(src, dst)

    assert _tree_state(dst) == before


@pytest.mark.parametrize("name", [".git", ".aws-tui-wiki-owned.json"])
def test_sync_wiki_refuses_reserved_source_payload(tmp_path, name):
    src, dst = _wiki_trees(tmp_path)
    (src / name).write_text("must not copy\n")
    before = _tree_state(dst)

    with pytest.raises(ValueError, match="ownership path"):
        sync_wiki(src, dst)

    assert _tree_state(dst) == before


@pytest.mark.parametrize("kind", ["file", "directory", "dangling", "root"])
def test_sync_wiki_refuses_source_symlinks_before_writes(tmp_path, kind):
    src, dst = _wiki_trees(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "page.md").write_text("outside bytes\n")
    if kind == "root":
        source = tmp_path / "linked-source"
        source.symlink_to(src, target_is_directory=True)
    else:
        source = src
        target = outside if kind == "directory" else outside / kind
        if kind == "file":
            target.write_text("outside bytes\n")
        (src / "Linked").symlink_to(target, target_is_directory=kind == "directory")
    before = _tree_state(tmp_path)

    with pytest.raises(ValueError, match="symlink"):
        sync_wiki(source, dst)

    assert _tree_state(tmp_path) == before


@pytest.mark.parametrize("kind", ["file", "directory", "metadata", "root", "retired"])
def test_sync_wiki_refuses_touched_destination_symlinks_before_writes(tmp_path, kind):
    src, dst = _wiki_trees(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "page.md").write_text("outside bytes\n")
    destination = dst
    if kind == "root":
        destination = tmp_path / "linked-destination"
        destination.symlink_to(dst, target_is_directory=True)
    elif kind == "directory":
        (src / "nested").mkdir()
        (src / "nested" / "page.md").write_text("new nested\n")
        (dst / "nested").symlink_to(outside, target_is_directory=True)
    else:
        name = {"file": "Home.md", "metadata": ".aws-tui-wiki-owned.json", "retired": "Retired.md"}[
            kind
        ]
        (dst / name).unlink(missing_ok=True)
        (dst / name).symlink_to(outside / "page.md")
        if kind == "retired":
            _write_ownership(dst, {"Retired.md": hashlib.sha256(b"outside bytes\n").hexdigest()})
    before = _tree_state(tmp_path)

    with pytest.raises(ValueError, match="symlink"):
        sync_wiki(src, destination)

    assert _tree_state(tmp_path) == before


@pytest.mark.parametrize("kind", ["file", "directory", "metadata", "retired"])
def test_sync_wiki_refuses_destination_type_conflicts_before_writes(tmp_path, kind):
    src, dst = _wiki_trees(tmp_path)
    if kind == "directory":
        (src / "nested").mkdir()
        (src / "nested" / "page.md").write_text("new\n")
        (dst / "nested").write_text("unowned file\n")
    else:
        name = {"file": "Home.md", "metadata": ".aws-tui-wiki-owned.json", "retired": "Retired.md"}[
            kind
        ]
        (dst / name).unlink(missing_ok=True)
        (dst / name).mkdir()
        if kind == "retired":
            _write_ownership(dst, {"Retired.md": "0" * 64})
    before = _tree_state(dst)

    with pytest.raises(ValueError, match="type conflict"):
        sync_wiki(src, dst)

    assert _tree_state(dst) == before


@pytest.mark.parametrize("kind", ["generated", "registry"])
def test_sync_wiki_refuses_destination_hard_links_before_writes(tmp_path, kind):
    src, dst = _wiki_trees(tmp_path)
    if kind == "registry":
        outside_bytes = b'{"schema": 1, "files": {}}\n'
        target = dst / ".aws-tui-wiki-owned.json"
    else:
        outside_bytes = b"external page bytes\n"
        target = dst / "Home.md"
        target.unlink()
    outside = tmp_path / "outside-file"
    outside.write_bytes(outside_bytes)
    target.hardlink_to(outside)
    before = _tree_state(tmp_path)

    with pytest.raises(ValueError, match="hard link"):
        sync_wiki(src, dst)

    assert _tree_state(tmp_path) == before
    assert outside.read_bytes() == outside_bytes


@pytest.mark.parametrize("kind", ["equal", "source_inside", "destination_inside"])
def test_sync_wiki_refuses_overlapping_trees_before_writes(tmp_path, kind):
    src, dst = _wiki_trees(tmp_path)
    if kind == "equal":
        dst = src
    elif kind == "source_inside":
        src = dst / "nested"
        src.mkdir()
    else:
        dst = src / "nested"
        dst.mkdir()
    before = _tree_state(tmp_path)

    with pytest.raises(ValueError, match="overlap"):
        sync_wiki(src, dst)

    assert _tree_state(tmp_path) == before


@pytest.mark.parametrize("empty_directory", [False, True])
def test_sync_wiki_refuses_empty_source_before_writes(tmp_path, empty_directory):
    src, dst = _wiki_trees(tmp_path)
    sync_wiki(src, dst)
    (src / "Home.md").unlink()
    if empty_directory:
        (src / "empty").mkdir()
    (dst / "Manual.md").write_text("keep\n")
    before = _tree_state(dst)

    with pytest.raises(ValueError, match="empty"):
        sync_wiki(src, dst)

    assert _tree_state(dst) == before


def _git(repo: Path, *args: str, env=None):
    return subprocess.run(
        ["git", *args], cwd=repo, env=env, capture_output=True, text=True, check=True
    )


def test_repeated_wiki_sync_is_byte_stable_and_creates_no_commit(tmp_path):
    from scripts.docs.push_wiki import _commit_if_changed

    src, dst = _wiki_trees(tmp_path)
    _git(dst, "init", "-q", "-b", "master")
    sync_wiki(src, dst)
    assert (dst / ".aws-tui-wiki-owned.json").is_file()
    _commit_if_changed(dst)
    before_head = _git(dst, "rev-parse", "HEAD").stdout
    before = _tree_state(dst)

    sync_wiki(src, dst)
    assert _tree_state(dst) == before
    _commit_if_changed(dst)

    assert _git(dst, "rev-parse", "HEAD").stdout == before_head
    assert _git(dst, "status", "--porcelain").stdout == ""


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
