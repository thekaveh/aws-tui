"""Real POSIX lock integrity, including controlled pathname fallback probes."""

import os
from types import SimpleNamespace

import pytest

from aws_tui.domain.navigation_workspace import WorkspaceError
from aws_tui.domain.workspace_store import WorkspaceStore

pytestmark = pytest.mark.skipif(
    os.name == "nt",
    reason="POSIX descriptor/alias fixtures; Windows pathname/reparse branches are simulated",
)


def test_workspace_pathmode_lock_alias_never_modifies_unrelated_target(tmp_path, monkeypatch):
    import aws_tui.domain.workspace_store as module

    config = tmp_path / "config"
    config.mkdir()
    outside = tmp_path / "unrelated-empty-file"
    outside.write_bytes(b"")
    outside.chmod(0o644)
    link = config / ".workspaces.json.lock"
    link.symlink_to(outside)
    # Exercise the portable/no-directory-FD branch used on Windows, while
    # retaining this host's real file lock and filesystem. This is a controlled
    # branch proof, not native Windows execution.
    proxy = SimpleNamespace(
        **{name: getattr(os, name) for name in dir(os) if not name.startswith("__")}
    )
    proxy.name = "nt"
    monkeypatch.setattr(module, "os", proxy)
    store = WorkspaceStore(path=config / "workspaces.json")
    with pytest.raises(WorkspaceError):
        store.list()
    assert outside.read_bytes() == b"", "List followed lock alias and wrote an unrelated file"
    assert outside.stat().st_mode & 0o777 == 0o644, "List changed unrelated lock-target permissions"
    assert link.is_symlink()
    assert not store.path.exists()


def test_workspace_native_lock_hardlink_never_modifies_unrelated_target(tmp_path):
    config = tmp_path / "config"
    config.mkdir()
    outside = tmp_path / "unrelated-empty-file"
    outside.write_bytes(b"")
    outside.chmod(0o644)
    link = config / ".workspaces.json.lock"
    os.link(outside, link)
    assert outside.stat().st_nlink == 2
    store = WorkspaceStore(path=config / "workspaces.json")
    with pytest.raises(WorkspaceError):
        store.list()
    assert outside.read_bytes() == b"", "native List wrote through a hardlinked lock leaf"
    assert outside.stat().st_mode & 0o777 == 0o644
    assert link.stat().st_ino == outside.stat().st_ino
    assert not store.path.exists()


@pytest.mark.parametrize("descriptor", [False, True])
def test_strict_lock_rejects_regular_leaf_replaced_before_existing_open(
    tmp_path, monkeypatch, descriptor
):
    import aws_tui.infra.config_store as module
    from aws_tui.infra.config_store import ConfigError, ConfigStore

    parent = tmp_path / "config"
    parent.mkdir()
    lock = parent / ".workspaces.json.lock"
    lock.write_bytes(b"old lock")
    replacement = tmp_path / "unrelated"
    replacement.write_bytes(b"")
    replacement.chmod(0o644)
    original = lock.stat()
    real_open = module.os.open
    swapped = False

    def replace_before_open(path, flags, *args, **kwargs):
        nonlocal swapped
        if not swapped and str(path) in (str(lock), lock.name) and not flags & os.O_CREAT:
            swapped = True
            lock.rename(parent / "old-owned-lock")
            replacement.replace(lock)
        return real_open(path, flags, *args, **kwargs)

    directory_fd = real_open(parent, os.O_RDONLY | os.O_DIRECTORY) if descriptor else None
    monkeypatch.setattr(module.os, "open", replace_before_open)
    admitted = False
    try:
        try:
            with ConfigStore(path=parent / "workspaces.json").transaction(
                directory_fd=directory_fd, strict_leaf=True
            ):
                admitted = True
        except ConfigError:
            pass
        assert swapped
        assert lock.stat().st_ino != original.st_ino
        assert lock.read_bytes() == b"", "replacement lock inode was modified before rejection"
        assert lock.stat().st_mode & 0o777 == 0o644
        assert not admitted, "a replacement regular leaf was admitted as the original lock"
    finally:
        if directory_fd is not None:
            os.close(directory_fd)


@pytest.mark.parametrize("descriptor", [False, True])
def test_strict_dangling_alias_refused_without_creating_target(tmp_path, descriptor):
    from aws_tui.infra.config_store import ConfigError, ConfigStore

    parent = tmp_path / "config"
    parent.mkdir()
    target = tmp_path / "must-not-create"
    lock = parent / ".workspaces.json.lock"
    lock.symlink_to(target)
    directory_fd = os.open(parent, os.O_RDONLY | os.O_DIRECTORY) if descriptor else None
    try:
        with (
            pytest.raises(ConfigError),
            ConfigStore(path=parent / "workspaces.json").transaction(
                directory_fd=directory_fd, strict_leaf=True
            ),
        ):
            pytest.fail("dangling alias admitted")
        assert not target.exists()
        assert lock.is_symlink()
    finally:
        if directory_fd is not None:
            os.close(directory_fd)


@pytest.mark.parametrize("descriptor", [False, True])
def test_strict_reparse_leaf_flag_refused_before_io(tmp_path, monkeypatch, descriptor):
    import aws_tui.infra.config_store as module
    from aws_tui.infra.config_store import ConfigError, ConfigStore

    parent = tmp_path / "config"
    parent.mkdir()
    lock = parent / ".workspaces.json.lock"
    lock.write_bytes(b"")
    lock.chmod(0o644)
    real_stat = module.os.stat
    directory_fd = os.open(parent, os.O_RDONLY | os.O_DIRECTORY) if descriptor else None

    def reparse(path, **kwargs):
        result = real_stat(path, **kwargs)
        if str(path) in (str(lock), lock.name):
            values = {name: getattr(result, name) for name in dir(result) if name.startswith("st_")}
            values["st_file_attributes"] = 0x400
            return SimpleNamespace(**values)
        return result

    monkeypatch.setattr(module.os, "stat", reparse)
    try:
        with (
            pytest.raises(ConfigError),
            ConfigStore(path=parent / "workspaces.json").transaction(
                directory_fd=directory_fd, strict_leaf=True
            ),
        ):
            pytest.fail("synthetic reparse leaf admitted")
        assert lock.read_bytes() == b""
        assert real_stat(lock).st_mode & 0o777 == 0o644
    finally:
        if directory_fd is not None:
            os.close(directory_fd)


@pytest.mark.parametrize("strict_outer", [False, True])
@pytest.mark.parametrize("descriptor_outer", [False, True])
def test_normal_and_strict_modes_share_reentrant_lock(tmp_path, strict_outer, descriptor_outer):
    from aws_tui.infra.config_store import ConfigStore

    parent = tmp_path / "config"
    parent.mkdir()
    directory_fd = os.open(parent, os.O_RDONLY | os.O_DIRECTORY)
    store = ConfigStore(path=parent / "workspaces.json", lock_timeout=0.2)
    try:
        with (
            store.transaction(
                directory_fd=directory_fd if descriptor_outer else None, strict_leaf=strict_outer
            ),
            store.transaction(
                directory_fd=None if descriptor_outer else directory_fd,
                strict_leaf=not strict_outer,
            ),
        ):
            assert (parent / ".workspaces.json.lock").read_bytes() == b"\0"
    finally:
        os.close(directory_fd)


@pytest.mark.parametrize("descriptor", [False, True])
def test_strict_concurrent_initial_creator_is_validated_and_accepted(
    tmp_path, monkeypatch, descriptor
):
    import aws_tui.infra.config_store as module
    from aws_tui.infra.config_store import ConfigStore

    parent = tmp_path / "config"
    parent.mkdir()
    lock = parent / ".workspaces.json.lock"
    real_open = module.os.open
    directory_fd = real_open(parent, os.O_RDONLY | os.O_DIRECTORY) if descriptor else None
    raced = False

    def concurrent_create(path, flags, *args, **kwargs):
        nonlocal raced
        if not raced and str(path) in (str(lock), lock.name) and flags & os.O_EXCL:
            raced = True
            fd = real_open(
                path, os.O_RDWR | os.O_CREAT | os.O_EXCL, 0o600, dir_fd=kwargs.get("dir_fd")
            )
            os.close(fd)
            raise FileExistsError("controlled concurrent first creator")
        return real_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(module.os, "open", concurrent_create)
    try:
        with ConfigStore(path=parent / "workspaces.json").transaction(
            directory_fd=directory_fd, strict_leaf=True
        ):
            assert raced
            assert lock.read_bytes() == b"\0"
            assert lock.stat().st_nlink == 1
    finally:
        if directory_fd is not None:
            os.close(directory_fd)
