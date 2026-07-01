"""Unit tests for ConfigStore — TOML round-trip + atomic writes."""

from __future__ import annotations

from pathlib import Path

import pytest

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
    leftovers = [
        p for p in config_path.parent.iterdir() if p.name != config_path.name and p.is_file()
    ]
    assert leftovers == []


def test_save_creates_parent_directory(tmp_path: Path) -> None:
    nested = tmp_path / "nested" / "deep" / "config.toml"
    store = ConfigStore(path=nested)
    store.save(Config(connections={}, defaults=Defaults(), keybindings=Keybindings()))
    assert nested.is_file()


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


def test_save_chmods_parent_dir_to_0o700(tmp_path: Path) -> None:
    """Regression: ConfigStore.save() must chmod the config parent dir to 0o700
    (defense-in-depth — the config.toml file itself is 0o600 via mkstemp, but
    the parent dir would otherwise inherit umask 0o755 and leak the dir
    listing). If a future change silently drops the chmod call, this catches it.

    Skipped on Windows / filesystems that do not preserve permission bits."""
    import stat
    import sys

    if sys.platform.startswith("win"):
        pytest.skip("POSIX permission bits not enforced on Windows")
    config_path = tmp_path / "nested" / "aws-tui" / "config.toml"
    store = ConfigStore(path=config_path)
    store.add_connection(
        ConnectionEntry(
            name="test",
            kind="s3-compatible",
            region="us-east-1",
            endpoint_url="http://localhost:9000",
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
