"""Unit tests for ConfigStore — TOML round-trip + atomic writes."""

from __future__ import annotations

import contextlib
import os
import subprocess
import sys
import threading
from dataclasses import replace
from pathlib import Path

import pytest

from aws_tui.infra import config_store as config_store_module
from aws_tui.infra.config_store import (
    Config,
    ConfigError,
    ConfigStore,
    ConnectionEntry,
    Defaults,
    Keybindings,
)


@pytest.fixture
def config_path(tmp_path: Path) -> Path:
    return tmp_path / "config.toml"


def test_default_path_uses_platform_config_home(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from aws_tui.infra import config_store

    config_home = tmp_path / "native-config"
    monkeypatch.setattr(config_store, "config_home", lambda: config_home)

    assert ConfigStore().path == config_home / "config.toml"


def test_connection_entry_repr_masks_static_credentials() -> None:
    entry = ConnectionEntry(
        name="minio",
        kind="s3-compatible",
        endpoint_url="https://user:pass@example.com/bucket?X-Amz-Signature=sig",
        credentials="static",
        access_key_id="AKID",
        secret_access_key="SECRET",
        session_token="TOKEN",
    )

    rendered = repr(entry)

    assert "AKID" not in rendered
    assert "SECRET" not in rendered
    assert "TOKEN" not in rendered
    assert "user" not in rendered
    assert "pass" not in rendered
    assert "X-Amz-Signature" not in rendered
    assert "sig" not in rendered
    assert "endpoint_url='example.com/bucket'" in rendered
    assert "access_key_id='***'" in rendered
    assert "secret_access_key='***'" in rendered
    assert "session_token='***'" in rendered


def test_config_repr_uses_masked_connection_entries() -> None:
    config = Config(
        connections={
            "minio": ConnectionEntry(
                name="minio",
                kind="s3-compatible",
                credentials="static",
                access_key_id="AKID",
                secret_access_key="SECRET",
                session_token="TOKEN",
            )
        },
        defaults=Defaults(),
        keybindings=Keybindings(),
    )

    rendered = repr(config)

    assert "AKID" not in rendered
    assert "SECRET" not in rendered
    assert "TOKEN" not in rendered
    assert "access_key_id='***'" in rendered
    assert "secret_access_key='***'" in rendered
    assert "session_token='***'" in rendered


def test_missing_file_returns_empty_config(config_path: Path) -> None:
    store = ConfigStore(path=config_path)
    cfg = store.load()
    assert cfg.connections == {}
    assert cfg.defaults == Defaults()
    assert cfg.keybindings.bindings == {}


def test_round_trip_aws_connection(config_path: Path) -> None:
    store = ConfigStore(path=config_path)
    entry = ConnectionEntry(
        name="kaveh-dev",
        kind="aws",
        profile="kaveh-dev",
        region="us-east-1",
    )
    cfg = Config(
        connections={"kaveh-dev": entry},
        defaults=Defaults(connection="kaveh-dev", theme="voidline"),
        keybindings=Keybindings(bindings={"app.quit": "ctrl+d"}),
    )
    store.save(cfg)
    reloaded = store.load()
    assert reloaded == cfg


def test_round_trip_s3_compatible_connection(config_path: Path) -> None:
    store = ConfigStore(path=config_path)
    entry = ConnectionEntry(
        name="minio-local",
        kind="s3-compatible",
        endpoint_url="http://localhost:9000",
        region="us-east-1",
        credentials="keychain:minio-local",
        force_path_style=True,
        verify_tls=False,
    )
    cfg = Config(
        connections={"minio-local": entry},
        defaults=Defaults(),
        keybindings=Keybindings(),
    )
    store.save(cfg)
    reloaded = store.load()
    assert reloaded == cfg


def test_invalid_kind_raises(config_path: Path) -> None:
    config_path.write_text(
        '[connections.bad]\nkind = "not-a-real-kind"\n',
        encoding="utf-8",
    )
    store = ConfigStore(path=config_path)
    with pytest.raises(ConfigError, match="kind"):
        store.load()


@pytest.mark.parametrize(
    "endpoint",
    [
        "",
        "ftp://minio.local",
        "https://",
        "https://user:secret@minio.local",
        "https://minio.local?token=secret",
        "https://minio.local#fragment",
        "https://minio.local:invalid",
    ],
)
def test_load_rejects_invalid_s3_endpoint_urls(config_path: Path, endpoint: str) -> None:
    config_path.write_text(
        "[connections.minio]\n"
        'kind = "s3-compatible"\n'
        f'endpoint_url = "{endpoint}"\n'
        'credentials = "static"\n'
        'access_key_id = "AKIA"\n'
        'secret_access_key = "SECRET"\n',
        encoding="utf-8",
    )

    with pytest.raises(ConfigError, match="endpoint_url"):
        ConfigStore(path=config_path).load()


@pytest.mark.parametrize(
    "credentials",
    [None, "", "unknown", "keychain:", "env:   ", "aws-profile:"],
)
def test_load_rejects_invalid_s3_credential_specs(
    config_path: Path,
    credentials: str | None,
) -> None:
    credential_line = "" if credentials is None else f'credentials = "{credentials}"\n'
    config_path.write_text(
        "[connections.minio]\n"
        'kind = "s3-compatible"\n'
        'endpoint_url = "https://minio.local"\n'
        f"{credential_line}",
        encoding="utf-8",
    )

    with pytest.raises(ConfigError, match="credentials"):
        ConfigStore(path=config_path).load()


@pytest.mark.parametrize(
    ("access_key_id", "secret_access_key"),
    [(None, "SECRET"), ("AKIA", None), (" ", "SECRET"), ("AKIA", " ")],
)
def test_static_credentials_require_nonblank_key_pair(
    config_path: Path,
    access_key_id: str | None,
    secret_access_key: str | None,
) -> None:
    entry = ConnectionEntry(
        name="minio",
        kind="s3-compatible",
        endpoint_url="https://minio.local",
        credentials="static",
        access_key_id=access_key_id,
        secret_access_key=secret_access_key,
    )
    config = Config(
        connections={entry.name: entry},
        defaults=Defaults(),
        keybindings=Keybindings(),
    )

    with pytest.raises(ConfigError, match="static credentials"):
        ConfigStore(path=config_path).save(config)


@pytest.mark.parametrize("field", ["force_path_style", "verify_tls"])
def test_connection_boolean_fields_reject_string_values(config_path: Path, field: str) -> None:
    config_path.write_text(
        "[connections.minio]\n"
        'kind = "s3-compatible"\n'
        'endpoint_url = "https://minio.local"\n'
        f'{field} = "false"\n',
        encoding="utf-8",
    )
    store = ConfigStore(path=config_path)

    with pytest.raises(ConfigError, match=field):
        store.load()


@pytest.mark.parametrize(
    "field",
    [
        "profile",
        "region",
        "endpoint_url",
        "credentials",
        "access_key_id",
        "secret_access_key",
        "session_token",
    ],
)
def test_connection_optional_string_fields_reject_non_strings(
    config_path: Path, field: str
) -> None:
    endpoint_line = "" if field == "endpoint_url" else 'endpoint_url = "https://minio.local"\n'
    config_path.write_text(
        f'[connections.minio]\nkind = "s3-compatible"\n{endpoint_line}{field} = 123\n',
        encoding="utf-8",
    )
    store = ConfigStore(path=config_path)

    with pytest.raises(ConfigError, match=field):
        store.load()


@pytest.mark.parametrize("field", ["connection", "theme"])
def test_default_string_fields_reject_non_strings(config_path: Path, field: str) -> None:
    config_path.write_text(f"[defaults]\n{field} = 42\n", encoding="utf-8")

    with pytest.raises(ConfigError, match=rf"defaults.*{field}"):
        ConfigStore(path=config_path).load()


def test_add_connection_persists(config_path: Path) -> None:
    store = ConfigStore(path=config_path)
    store.add_connection(ConnectionEntry(name="dev", kind="aws", profile="dev", region="us-west-2"))
    cfg = store.load()
    assert "dev" in cfg.connections
    assert cfg.connections["dev"].profile == "dev"


def test_remove_connection_persists(config_path: Path) -> None:
    store = ConfigStore(path=config_path)
    store.add_connection(ConnectionEntry(name="dev", kind="aws", profile="dev"))
    store.add_connection(ConnectionEntry(name="prod", kind="aws", profile="prod"))
    store.remove_connection("dev")
    cfg = store.load()
    assert "dev" not in cfg.connections
    assert "prod" in cfg.connections


def test_remove_unknown_connection_raises(config_path: Path) -> None:
    store = ConfigStore(path=config_path)
    with pytest.raises(ConfigError, match="unknown"):
        store.remove_connection("nope")


def test_set_default_connection(config_path: Path) -> None:
    store = ConfigStore(path=config_path)
    store.add_connection(ConnectionEntry(name="dev", kind="aws", profile="dev"))
    store.set_default_connection("dev")
    cfg = store.load()
    assert cfg.defaults.connection == "dev"


def test_set_default_unknown_connection_raises(config_path: Path) -> None:
    store = ConfigStore(path=config_path)
    with pytest.raises(ConfigError, match="unknown"):
        store.set_default_connection("nope")


def test_atomic_save_leaves_original_intact_on_replace_failure(
    config_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If os.replace fails mid-write, the original file must be untouched."""
    store = ConfigStore(path=config_path)
    store.add_connection(ConnectionEntry(name="dev", kind="aws", profile="dev"))
    original = config_path.read_bytes()

    import os

    def boom(_src: object, _dst: object) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(os, "replace", boom)

    cfg = store.load()
    new_cfg = Config(
        connections={
            **cfg.connections,
            "prod": ConnectionEntry(name="prod", kind="aws", profile="prod"),
        },
        defaults=cfg.defaults,
        keybindings=cfg.keybindings,
    )
    with pytest.raises(OSError, match="disk full"):
        store.save(new_cfg)

    assert config_path.read_bytes() == original
    # No leftover temp files in the same directory.
    leftovers = list(config_path.parent.glob(".config-*.toml.tmp"))
    assert leftovers == []


def test_save_creates_parent_directory(tmp_path: Path) -> None:
    nested = tmp_path / "nested" / "deep" / "config.toml"
    store = ConfigStore(path=nested)
    store.save(Config(connections={}, defaults=Defaults(), keybindings=Keybindings()))
    assert nested.is_file()


def test_save_fsyncs_file_and_parent_directory(
    config_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[int] = []
    real_fsync = os.fsync

    def recording_fsync(fd: int) -> None:
        calls.append(fd)
        real_fsync(fd)

    monkeypatch.setattr(os, "fsync", recording_fsync)

    ConfigStore(path=config_path).save(
        Config(connections={}, defaults=Defaults(), keybindings=Keybindings())
    )

    expected_calls = 2 if os.name == "posix" else 1
    assert len(calls) >= expected_calls


def test_save_flushes_the_payload_before_fsync(
    config_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """fsync on an unflushed buffered writer syncs nothing.

    `_save_unlocked` writes through a buffered `os.fdopen(..., "wb")`. Without
    the `fh.flush()`, the payload is still in userspace when `os.fsync` runs, so
    the fsync is a no-op and the bytes only reach the kernel at `with`-block
    exit — after the fsync and immediately before `tmp_path.replace()`. A power
    loss in that window leaves config.toml renamed into place but empty, taking
    every connection with it, including static credential material.

    The sibling test above only COUNTS fsync calls, so it cannot see the flush
    disappear. This asserts the file actually has its bytes on disk at the
    moment fsync is called.
    """
    sizes: list[int] = []
    real_fsync = os.fsync

    def recording_fsync(fd: int) -> None:
        try:
            sizes.append(os.fstat(fd).st_size)
        except OSError:  # pragma: no cover - directory fd on some platforms
            sizes.append(-1)
        real_fsync(fd)

    monkeypatch.setattr(os, "fsync", recording_fsync)

    store = ConfigStore(path=config_path)
    store.save(
        Config(
            connections={
                "prod": ConnectionEntry(
                    name="prod",
                    kind="s3-compatible",
                    endpoint_url="https://example.com",
                    region="us-east-1",
                    credentials="static",
                    access_key_id="AKIAEXAMPLE",
                    secret_access_key="SECRETEXAMPLE",
                )
            },
            defaults=Defaults(),
            keybindings=Keybindings(),
        )
    )

    on_disk = config_path.read_bytes()
    assert on_disk, "precondition: the config was written"
    assert any(size >= len(on_disk) for size in sizes), (
        f"fsync ran before the payload was flushed: sizes at fsync were {sizes}, "
        f"final file is {len(on_disk)} bytes"
    )


def test_static_credentials_round_trip(config_path: Path) -> None:
    store = ConfigStore(path=config_path)
    entry = ConnectionEntry(
        name="static-r2",
        kind="s3-compatible",
        endpoint_url="https://r2.example.com",
        region="auto",
        credentials="static",
        access_key_id="AKIA-LOCAL",
        secret_access_key="secret",
        session_token="session",
    )
    cfg = Config(
        connections={entry.name: entry},
        defaults=Defaults(),
        keybindings=Keybindings(),
    )
    store.save(cfg)
    reloaded = store.load()
    assert reloaded.connections["static-r2"] == entry


def test_keybindings_list_round_trip(config_path: Path) -> None:
    store = ConfigStore(path=config_path)
    cfg = Config(
        connections={},
        defaults=Defaults(),
        keybindings=Keybindings(bindings={"pane.copy": ["c", "y"]}),
    )
    store.save(cfg)
    reloaded = store.load()
    assert reloaded.keybindings.bindings == {"pane.copy": ["c", "y"]}


def _seed_entry(name: str = "minio-local") -> ConnectionEntry:
    return ConnectionEntry(
        name=name,
        kind="s3-compatible",
        region="us-east-1",
        endpoint_url="http://localhost:9000",
        credentials="static",
        access_key_id="AKIATEST",
        secret_access_key="SECRETTEST",
        force_path_style=True,
        verify_tls=True,
    )


def test_update_connection_round_trip(tmp_path: Path) -> None:
    store = ConfigStore(path=tmp_path / "config.toml")
    store.add_connection(_seed_entry())
    updated = ConnectionEntry(
        name="minio-local",
        kind="s3-compatible",
        region="us-west-2",
        endpoint_url="https://minio.internal:443",
        credentials="static",
        access_key_id="AKIANEW",
        secret_access_key="SECRETNEW",
        force_path_style=False,
        verify_tls=False,
    )
    store.update_connection("minio-local", updated)
    cfg = store.load()
    assert cfg.connections["minio-local"] == updated


def test_remove_connection_round_trip(tmp_path: Path) -> None:
    store = ConfigStore(path=tmp_path / "config.toml")
    store.add_connection(_seed_entry())
    store.remove_connection("minio-local")
    cfg = store.load()
    assert "minio-local" not in cfg.connections


def test_update_connection_unknown_name_raises(tmp_path: Path) -> None:
    store = ConfigStore(path=tmp_path / "config.toml")
    with pytest.raises(KeyError, match="missing"):
        store.update_connection("missing", _seed_entry(name="missing"))


def test_remove_connection_unknown_name_raises(tmp_path: Path) -> None:
    store = ConfigStore(path=tmp_path / "config.toml")
    with pytest.raises(ConfigError, match="unknown"):
        store.remove_connection("missing")


def test_remove_connection_clears_default_if_it_was_the_default(tmp_path: Path) -> None:
    store = ConfigStore(path=tmp_path / "config.toml")
    store.add_connection(_seed_entry("default-conn"))
    store.set_default_connection("default-conn")
    assert store.load().defaults.connection == "default-conn"
    store.remove_connection("default-conn")
    assert store.load().defaults.connection is None


def test_update_connection_rename_disallowed(tmp_path: Path) -> None:
    store = ConfigStore(path=tmp_path / "config.toml")
    store.add_connection(_seed_entry(name="old"))
    renamed = _seed_entry(name="new")
    with pytest.raises(ValueError, match="cannot be renamed"):
        store.update_connection("old", renamed)


# ── Defense-in-depth: parent dir permission tightening ──────────────────────


def test_save_chmods_parent_dir_to_0o700(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Regression: ConfigStore.save() must chmod the config parent dir to 0o700.

    Defense-in-depth — the config.toml file itself is 0o600 via mkstemp, but the
    parent dir would otherwise inherit umask 0o755 and leak the dir listing.

    The 0o700 has TWO independent producers: ``save`` itself, and
    ``_acquire_os_file_lock``, which runs on every ``transaction()``. This test
    previously claimed that "if a future change silently drops the chmod call,
    this catches it" and it did not — dropping either producer alone left the
    other to satisfy the assertion. The lock's chmod is neutralised below so the
    one in ``save`` is genuinely pinned.

    Skipped on Windows / filesystems that do not preserve permission bits."""
    import stat
    import sys

    if sys.platform.startswith("win"):
        pytest.skip("POSIX permission bits not enforced on Windows")

    real_acquire = config_store_module._acquire_os_file_lock

    def acquire_without_chmod(path: Path, timeout: float):  # type: ignore[no-untyped-def]
        path.parent.mkdir(parents=True, exist_ok=True)
        mode_before = stat.S_IMODE(path.parent.stat().st_mode)
        handle = real_acquire(path, timeout)
        # Undo only the lock helper's hardening, leaving save() as the sole
        # remaining producer of 0o700.
        path.parent.chmod(mode_before)
        return handle

    monkeypatch.setattr(config_store_module, "_acquire_os_file_lock", acquire_without_chmod)

    config_path = tmp_path / "nested" / "aws-tui" / "config.toml"
    store = ConfigStore(path=config_path)
    store.add_connection(
        ConnectionEntry(
            name="test",
            kind="s3-compatible",
            region="us-east-1",
            endpoint_url="http://localhost:9000",
            credentials="static",
            access_key_id="K",
            secret_access_key="S",
            force_path_style=True,
            verify_tls=True,
        )
    )
    parent_mode = stat.S_IMODE(config_path.parent.stat().st_mode)
    assert parent_mode == 0o700, (
        f"config parent dir should be 0o700 (defense-in-depth) but is 0o{parent_mode:o}"
    )


def test_load_chmods_existing_config_and_parent_to_owner_only(tmp_path: Path) -> None:
    """Manually-created config files should be hardened before parsing."""
    import stat
    import sys

    if sys.platform.startswith("win"):
        pytest.skip("POSIX permission bits not enforced on Windows")
    config_dir = tmp_path / "manual"
    config_dir.mkdir()
    config_path = config_dir / "config.toml"
    config_path.write_text(
        "[connections.minio]\n"
        'kind = "s3-compatible"\n'
        'endpoint_url = "http://localhost:9000"\n'
        'region = "us-east-1"\n'
        'credentials = "static"\n'
        'access_key_id = "AKIA"\n'
        'secret_access_key = "SECRET"\n',
        encoding="utf-8",
    )
    config_dir.chmod(0o755)
    config_path.chmod(0o644)

    cfg = ConfigStore(path=config_path).load()

    assert cfg.connections["minio"].credentials == "static"
    assert stat.S_IMODE(config_dir.stat().st_mode) == 0o700
    assert stat.S_IMODE(config_path.stat().st_mode) == 0o600


# ── read_only (demo-mode) tests ────────────────────────────────────────────


def test_read_only_config_store_save_is_noop(tmp_path: Path) -> None:
    """ConfigStore(read_only=True).save() must not write the file."""
    path = tmp_path / "config.toml"
    store = ConfigStore(path=path, read_only=True)
    cfg = Config(
        connections={"demo-dev": ConnectionEntry(name="demo-dev", kind="aws", profile="demo-dev")},
        defaults=Defaults(),
        keybindings=Keybindings(),
    )
    store.save(cfg)
    assert not path.exists(), "read_only store must not write config.toml"


def test_read_only_config_store_add_connection_is_noop(tmp_path: Path) -> None:
    """add_connection on a read_only store must not create the file."""
    path = tmp_path / "config.toml"
    store = ConfigStore(path=path, read_only=True)
    store.add_connection(ConnectionEntry(name="x", kind="aws", profile="x"))
    assert not path.exists()


def test_read_only_config_store_remove_connection_is_noop(tmp_path: Path) -> None:
    """remove_connection on a read_only store must not raise or write."""
    path = tmp_path / "config.toml"
    # Seed a real file via a writable store.
    writable = ConfigStore(path=path)
    writable.add_connection(ConnectionEntry(name="x", kind="aws", profile="x"))
    mtime_before = path.stat().st_mtime

    read_only = ConfigStore(path=path, read_only=True)
    read_only.remove_connection("x")
    assert path.stat().st_mtime == mtime_before, "read_only store must not touch the file"


def test_read_only_config_store_load_still_works(tmp_path: Path) -> None:
    """load() must still function on a read_only store."""
    path = tmp_path / "config.toml"
    writable = ConfigStore(path=path)
    writable.add_connection(ConnectionEntry(name="y", kind="aws", profile="y"))

    read_only = ConfigStore(path=path, read_only=True)
    cfg = read_only.load()
    assert "y" in cfg.connections


def test_read_only_property(tmp_path: Path) -> None:
    """ConfigStore.read_only reflects the constructor flag."""
    assert ConfigStore(path=tmp_path / "c.toml").read_only is False
    assert ConfigStore(path=tmp_path / "c.toml", read_only=True).read_only is True


def test_transaction_lock_times_out_with_actionable_error(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    holder = ConfigStore(path=path, lock_timeout=1.0)
    contender = ConfigStore(path=path, lock_timeout=0.05)
    entered = threading.Event()
    release = threading.Event()

    def hold_lock() -> None:
        with holder.transaction():
            entered.set()
            assert release.wait(timeout=2.0)

    thread = threading.Thread(target=hold_lock)
    thread.start()
    assert entered.wait(timeout=1.0)
    try:
        with (
            pytest.raises(
                ConfigError,
                match=r"timed out.*config transaction lock.*retry",
            ),
            contender.transaction(),
        ):
            pytest.fail("contender unexpectedly acquired the held config lock")
    finally:
        release.set()
        thread.join(timeout=2.0)

    assert not thread.is_alive()


def test_transaction_lock_excludes_an_independent_process(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    store = ConfigStore(path=path)
    script = """
import sys
from dataclasses import replace
from pathlib import Path
from aws_tui.infra.config_store import ConfigError, ConfigStore

try:
    with ConfigStore(path=Path(sys.argv[1]), lock_timeout=0.05).transaction():
        pass
except ConfigError as exc:
    print(exc, file=sys.stderr)
    raise SystemExit(23)
raise SystemExit(0)
"""

    with store.transaction():
        result = subprocess.run(
            [sys.executable, "-c", script, str(path)],
            check=False,
            capture_output=True,
            text=True,
            # This bounds interpreter startup, imports, and shutdown too.
            # The lock's own timeout remains 0.05s in the child; a loaded
            # runner must not lose its successful contention result merely
            # because the process lifecycle took more than two seconds.
            timeout=15.0,
        )

    assert result.returncode == 23
    assert "timed out" in result.stderr
    assert "retry" in result.stderr


@pytest.mark.skipif(not hasattr(os, "fork"), reason="requires POSIX fork")
def test_transaction_lock_is_not_reentrant_in_forked_child(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    store = ConfigStore(path=path, lock_timeout=0.05)
    read_fd, write_fd = os.pipe()

    with store.transaction():
        child_pid = os.fork()
        if child_pid == 0:
            os.close(read_fd)
            try:
                with store.transaction():
                    result = b"acquired"
            except ConfigError:
                result = b"blocked"
            os.write(write_fd, result)
            os.close(write_fd)
            os._exit(0)

        os.close(write_fd)
        result = os.read(read_fd, 32)
        os.close(read_fd)
        _, status = os.waitpid(child_pid, 0)

    assert os.waitstatus_to_exitcode(status) == 0
    assert result == b"blocked"


def test_transaction_lock_reports_os_acquisition_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from aws_tui.infra import config_store

    def deny_lock(_path: Path, _timeout: float) -> object:
        raise PermissionError("lock directory is read-only")

    monkeypatch.setattr(config_store, "_acquire_os_file_lock", deny_lock)

    with (
        pytest.raises(ConfigError, match=r"unable to acquire.*read-only"),
        ConfigStore(path=tmp_path / "config.toml").transaction(),
    ):
        pytest.fail("transaction unexpectedly acquired an unavailable lock")


def test_concurrent_store_instances_preserve_both_mutations(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    first = ConfigStore(path=path)
    second = ConfigStore(path=path)
    first_entered = threading.Event()
    release_first = threading.Event()

    def add_first() -> None:
        with first.transaction():
            first_entered.set()
            assert release_first.wait(timeout=2.0)
            first.add_connection(ConnectionEntry(name="first", kind="aws", profile="first"))

    thread = threading.Thread(target=add_first)
    thread.start()
    assert first_entered.wait(timeout=1.0)

    second_done = threading.Event()

    def add_second() -> None:
        second.add_connection(ConnectionEntry(name="second", kind="aws", profile="second"))
        second_done.set()

    contender = threading.Thread(target=add_second)
    contender.start()
    assert not second_done.wait(timeout=0.05)
    release_first.set()
    thread.join(timeout=2.0)
    contender.join(timeout=2.0)

    assert not thread.is_alive()
    assert not contender.is_alive()
    assert set(first.load().connections) == {"first", "second"}


@pytest.mark.parametrize("operation", ["add", "update", "default", "remove", "theme", "keys"])
def test_sql_drafts_survives_mutations(tmp_path: Path, operation: str) -> None:
    store = ConfigStore(path=tmp_path / "config.toml")
    assert store.load().athena_sql_drafts is False
    store.set_athena_sql_drafts(True)
    store.add_connection(ConnectionEntry(name="a", kind="aws", region="us-west-2"))
    if operation == "add":
        store.add_connection(ConnectionEntry(name="b", kind="aws"))
    elif operation == "update":
        store.update_connection("a", ConnectionEntry(name="a", kind="aws", region="us-east-1"))
    elif operation == "default":
        store.set_default_connection("a")
    elif operation == "remove":
        store.remove_connection("a")
    else:
        with store.transaction():
            config = store.load()
            store.save(
                replace(config, defaults=replace(config.defaults, theme="github-light"))
                if operation == "theme"
                else replace(config, keybindings=Keybindings({"pane.copy": "y"}))
            )
    assert ConfigStore(path=store.path).load().athena_sql_drafts is True
    assert "[athena]" in store.path.read_text()
    store.set_athena_sql_drafts(False)
    assert store.load().athena_sql_drafts is False
    assert "sql_drafts" not in store.path.read_text()


@pytest.mark.parametrize("value", ['"yes"', "1", "[]", "{}"])
def test_sql_drafts_rejects_non_bool(tmp_path: Path, value: str) -> None:
    path = tmp_path / "config.toml"
    path.write_text(f"[athena]\nsql_drafts = {value}\n")
    with pytest.raises(ConfigError) as caught:
        ConfigStore(path=path).load()
    assert str(caught.value) == "[athena].sql_drafts must be a boolean"
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None


@pytest.mark.parametrize("value", ['"yes"', "1", "[]"])
def test_sql_drafts_rejects_non_table(tmp_path: Path, value: str) -> None:
    path = tmp_path / "config.toml"
    path.write_text(f"athena = {value}\n")
    with pytest.raises(ConfigError, match=r"^\[athena\] must be a table$"):
        ConfigStore(path=path).load()


@pytest.mark.parametrize("value", [1, "yes", [], {}])
def test_sql_drafts_setter_rejects_non_bool(tmp_path: Path, value: object) -> None:
    store = ConfigStore(path=tmp_path / "config.toml")
    with pytest.raises(ConfigError) as caught:
        store.set_athena_sql_drafts(value)  # type: ignore[arg-type]
    assert str(caught.value) == "[athena].sql_drafts must be a boolean"
    assert not store.path.exists()


def test_sql_drafts_read_only_setter_does_not_write(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text("[athena]\nsql_drafts = true\n")
    previous = path.read_bytes()
    store = ConfigStore(path=path, read_only=True)
    assert store.read_only is True
    store.set_athena_sql_drafts(False)
    assert store.load().athena_sql_drafts is True
    assert path.read_bytes() == previous
    assert not path.with_name(".config.toml.lock").exists()


@pytest.mark.parametrize("failure", ["fdopen", "fstat", "initial-write", "initial-chmod"])
def test_lock_initialization_failure_closes_descriptor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    import errno
    from unittest.mock import Mock

    real_open = os.open
    real_fdopen = os.fdopen
    real_fstat = os.fstat
    real_chmod = Path.chmod
    descriptors: list[int] = []
    files = []

    def open_lock(*args, **kwargs):
        fd = real_open(*args, **kwargs)
        descriptors.append(fd)
        return fd

    def wrap_lock(fd, *args, **kwargs):
        if failure == "fdopen":
            raise OSError(errno.EIO, "fixture fdopen failure")
        file = real_fdopen(fd, *args, **kwargs)
        files.append(file)
        if failure == "initial-write":
            proxy = Mock(wraps=file)
            proxy.write.side_effect = OSError(errno.ENOSPC, "fixture initial write failure")
            return proxy
        return file

    def inspect_lock(fd):
        if failure == "fstat" and fd in descriptors:
            raise OSError(errno.EIO, "fixture fstat failure")
        return real_fstat(fd)

    def chmod_lock(path, *args, **kwargs):
        if failure == "initial-chmod" and path.name == "config.lock":
            raise KeyboardInterrupt("fixture chmod interrupted")
        return real_chmod(path, *args, **kwargs)

    monkeypatch.setattr(config_store_module.os, "open", open_lock)
    monkeypatch.setattr(config_store_module.os, "fdopen", wrap_lock)
    monkeypatch.setattr(config_store_module.os, "fstat", inspect_lock)
    monkeypatch.setattr(Path, "chmod", chmod_lock)
    try:
        # Retain the traceback to ensure cleanup does not depend on finalization.
        expected = KeyboardInterrupt if failure == "initial-chmod" else OSError
        with pytest.raises(expected, match="fixture") as raised:
            config_store_module._acquire_os_file_lock(tmp_path / "config.lock", 0.1)
        if isinstance(raised.value, OSError):
            assert raised.value.errno in {errno.EIO, errno.ENOSPC}
        assert len(descriptors) == 1
        with pytest.raises(OSError, match=r".") as closed:
            real_fstat(descriptors[0])
        assert closed.value.errno == errno.EBADF
    finally:
        for file in files:
            file.close()
        for fd in descriptors:
            with contextlib.suppress(OSError):
                os.close(fd)


@pytest.mark.parametrize("failure", [OSError, KeyboardInterrupt])
def test_save_wrapper_failure_closes_descriptor_and_preserves_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: type[BaseException]
) -> None:
    import errno

    store = ConfigStore(path=tmp_path / "config.toml")
    config = Config(connections={}, defaults=Defaults(), keybindings=Keybindings({}))
    store.save(config)
    replacement = replace(config, defaults=replace(config.defaults, theme="github-light"))
    original = store.path.read_bytes()
    real_fdopen = os.fdopen
    real_fstat = os.fstat
    descriptors: list[int] = []

    def wrap_file(fd, mode, *args, **kwargs):
        if mode == "wb":
            descriptors.append(fd)
            raise failure("fixture wrapper failure")
        return real_fdopen(fd, mode, *args, **kwargs)

    monkeypatch.setattr(config_store_module.os, "fdopen", wrap_file)
    try:
        with pytest.raises(failure, match="fixture"):
            store.save(replacement)
        assert store.path.read_bytes() == original
        assert not list(tmp_path.glob(".config-*.toml.tmp"))
        assert len(descriptors) == 1
        with pytest.raises(OSError, match=r".") as closed:
            real_fstat(descriptors[0])
        assert closed.value.errno == errno.EBADF
    finally:
        for fd in descriptors:
            with contextlib.suppress(OSError):
                os.close(fd)


@pytest.mark.parametrize("stage", ["lock", "save"])
@pytest.mark.parametrize("already_closed", [False, True])
def test_fdopen_construction_preserves_original_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stage: str, already_closed: bool
) -> None:
    import errno

    store = ConfigStore(path=tmp_path / "config.toml")
    original_config = Config(connections={}, defaults=Defaults(), keybindings=Keybindings({}))
    store.save(original_config)
    original_payload = store.path.read_bytes()
    replacement = replace(original_config, defaults=Defaults(theme="github-light"))
    original_error = MemoryError("fixture stream construction failure")
    real_fdopen, real_fstat, real_close = os.fdopen, os.fstat, os.close
    descriptors = []

    def fail_construction(fd, mode, *args, **kwargs):
        if mode == ("r+b" if stage == "lock" else "wb"):
            descriptors.append(fd)
            # Represent CPython's known post-adoption state, without inducing OOM.
            if already_closed:
                real_close(fd)
            raise original_error
        return real_fdopen(fd, mode, *args, **kwargs)

    monkeypatch.setattr(config_store_module.os, "fdopen", fail_construction)
    with pytest.raises(MemoryError) as caught:
        store.save(replacement)
    assert caught.value is original_error
    assert store.path.read_bytes() == original_payload
    assert not list(tmp_path.glob(".config-*.toml.tmp"))
    assert len(descriptors) == 1
    with pytest.raises(OSError, match=r".") as closed:
        real_fstat(descriptors[0])
    assert closed.value.errno == errno.EBADF
