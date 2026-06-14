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
