"""Unit tests for ConnectionResolver."""

from __future__ import annotations

from pathlib import Path

import pytest

from aws_tui.infra.config_store import ConfigStore, ConnectionEntry
from aws_tui.infra.connection_resolver import (
    Connection,
    ConnectionNotFound,
    ConnectionResolver,
)
from aws_tui.infra.keychain import InMemoryKeychain


def _write_aws_files(
    tmp_path: Path,
    *,
    config_body: str | None = None,
    credentials_body: str | None = None,
) -> tuple[Path, Path]:
    aws_dir = tmp_path / ".aws"
    aws_dir.mkdir(parents=True, exist_ok=True)
    aws_config = aws_dir / "config"
    aws_credentials = aws_dir / "credentials"
    if config_body is not None:
        aws_config.write_text(config_body, encoding="utf-8")
    if credentials_body is not None:
        aws_credentials.write_text(credentials_body, encoding="utf-8")
    return aws_config, aws_credentials


@pytest.fixture
def store(tmp_path: Path) -> ConfigStore:
    return ConfigStore(path=tmp_path / "config.toml")


class TestList:
    def test_empty_config_no_aws_files_returns_empty_list(
        self, tmp_path: Path, store: ConfigStore
    ) -> None:
        resolver = ConnectionResolver(
            config_store=store,
            aws_config_path=tmp_path / "no-config",
            aws_credentials_path=tmp_path / "no-creds",
        )
        assert resolver.list() == []

    def test_auto_discovers_three_aws_profiles(self, tmp_path: Path, store: ConfigStore) -> None:
        cfg, creds = _write_aws_files(
            tmp_path,
            config_body=(
                "[default]\nregion = us-east-1\n"
                "[profile dev]\nregion = us-west-2\n"
                "[profile prod]\nregion = eu-west-1\n"
            ),
            credentials_body="",
        )
        resolver = ConnectionResolver(
            config_store=store,
            aws_config_path=cfg,
            aws_credentials_path=creds,
        )
        connections = resolver.list()
        names = {c.name for c in connections}
        assert names == {"default", "dev", "prod"}
        assert all(c.source == "auto-aws-profile" for c in connections)
        assert all(c.kind == "aws" for c in connections)
        regions = {c.name: c.region for c in connections}
        assert regions == {
            "default": "us-east-1",
            "dev": "us-west-2",
            "prod": "eu-west-1",
        }

    def test_auto_discovers_from_credentials_only(self, tmp_path: Path, store: ConfigStore) -> None:
        cfg, creds = _write_aws_files(
            tmp_path,
            credentials_body=(
                "[only-here]\naws_access_key_id = AKIA\naws_secret_access_key = secret\n"
            ),
        )
        resolver = ConnectionResolver(
            config_store=store,
            aws_config_path=cfg,
            aws_credentials_path=creds,
        )
        names = {c.name for c in resolver.list()}
        assert names == {"only-here"}

    def test_explicit_config_wins_on_name_collision(
        self, tmp_path: Path, store: ConfigStore
    ) -> None:
        cfg, creds = _write_aws_files(
            tmp_path,
            config_body="[profile dev]\nregion = us-east-1\n",
        )
        store.add_connection(
            ConnectionEntry(
                name="dev",
                kind="aws",
                profile="dev",
                region="eu-central-1",
            )
        )
        resolver = ConnectionResolver(
            config_store=store,
            aws_config_path=cfg,
            aws_credentials_path=creds,
        )
        listed = resolver.list()
        assert len(listed) == 1
        only = listed[0]
        assert only.name == "dev"
        assert only.source == "config"
        assert only.region == "eu-central-1"

    def test_default_region_falls_back_when_profile_has_none(
        self, tmp_path: Path, store: ConfigStore
    ) -> None:
        cfg, creds = _write_aws_files(tmp_path, config_body="[profile noregion]\noutput = json\n")
        resolver = ConnectionResolver(
            config_store=store,
            aws_config_path=cfg,
            aws_credentials_path=creds,
        )
        listed = resolver.list()
        assert len(listed) == 1
        # Falls back to "us-east-1" (boto3 default) when nothing is set.
        assert listed[0].region == "us-east-1"


class TestResolveAndMaterialize:
    def test_resolve_missing_raises(self, tmp_path: Path, store: ConfigStore) -> None:
        resolver = ConnectionResolver(
            config_store=store,
            aws_config_path=tmp_path / "missing",
            aws_credentials_path=tmp_path / "missing",
        )
        with pytest.raises(ConnectionNotFound):
            resolver.resolve("nope")

    def test_resolve_explicit_aws(self, tmp_path: Path, store: ConfigStore) -> None:
        store.add_connection(
            ConnectionEntry(
                name="dev",
                kind="aws",
                profile="dev",
                region="us-west-2",
            )
        )
        resolver = ConnectionResolver(
            config_store=store,
            aws_config_path=tmp_path / "missing",
            aws_credentials_path=tmp_path / "missing",
        )
        c = resolver.resolve("dev")
        assert c.kind == "aws"
        assert c.profile == "dev"
        assert c.region == "us-west-2"
        assert c.source == "config"

    def test_materialize_promotes_auto_to_explicit(
        self, tmp_path: Path, store: ConfigStore
    ) -> None:
        cfg, creds = _write_aws_files(
            tmp_path, config_body="[profile auto-dev]\nregion = us-east-2\n"
        )
        resolver = ConnectionResolver(
            config_store=store,
            aws_config_path=cfg,
            aws_credentials_path=creds,
        )
        entry = resolver.materialize("auto-dev")
        assert isinstance(entry, ConnectionEntry)
        assert entry.kind == "aws"
        assert entry.profile == "auto-dev"
        assert entry.region == "us-east-2"
        # Persists into the config store.
        cfg_obj = store.load()
        assert "auto-dev" in cfg_obj.connections

    def test_materialize_missing_raises(self, tmp_path: Path, store: ConfigStore) -> None:
        resolver = ConnectionResolver(
            config_store=store,
            aws_config_path=tmp_path / "missing",
            aws_credentials_path=tmp_path / "missing",
        )
        with pytest.raises(ConnectionNotFound):
            resolver.materialize("nope")


class TestS3CompatibleCredentialDispatch:
    def test_keychain_credentials(self, tmp_path: Path, store: ConfigStore) -> None:
        kc = InMemoryKeychain()
        kc.set("minio-local", "access_key_id", "AKIA-MINIO")
        kc.set("minio-local", "secret_access_key", "shh")
        store.add_connection(
            ConnectionEntry(
                name="minio-local",
                kind="s3-compatible",
                endpoint_url="http://localhost:9000",
                region="us-east-1",
                credentials="keychain:minio-local",
                force_path_style=True,
            )
        )
        resolver = ConnectionResolver(
            config_store=store,
            keychain=kc,
            aws_config_path=tmp_path / "missing",
            aws_credentials_path=tmp_path / "missing",
        )
        c = resolver.resolve("minio-local")
        assert c.access_key_id == "AKIA-MINIO"
        assert c.secret_access_key == "shh"
        assert c.endpoint_url == "http://localhost:9000"
        assert c.force_path_style is True

    def test_env_credentials(
        self, tmp_path: Path, store: ConfigStore, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("R2_ACCESS_KEY_ID", "AKIA-R2")
        monkeypatch.setenv("R2_SECRET_ACCESS_KEY", "supersecret")
        store.add_connection(
            ConnectionEntry(
                name="r2",
                kind="s3-compatible",
                endpoint_url="https://r2.example.com",
                region="auto",
                credentials="env:R2_",
            )
        )
        resolver = ConnectionResolver(
            config_store=store,
            aws_config_path=tmp_path / "missing",
            aws_credentials_path=tmp_path / "missing",
        )
        c = resolver.resolve("r2")
        assert c.access_key_id == "AKIA-R2"
        assert c.secret_access_key == "supersecret"

    def test_aws_profile_credentials(self, tmp_path: Path, store: ConfigStore) -> None:
        cfg, creds = _write_aws_files(
            tmp_path,
            credentials_body=(
                "[shared]\naws_access_key_id = AKIA-SHARED\naws_secret_access_key = sharedsecret\n"
            ),
        )
        store.add_connection(
            ConnectionEntry(
                name="wasabi",
                kind="s3-compatible",
                endpoint_url="https://s3.wasabisys.com",
                region="us-east-1",
                credentials="aws-profile:shared",
            )
        )
        resolver = ConnectionResolver(
            config_store=store,
            aws_config_path=cfg,
            aws_credentials_path=creds,
        )
        c = resolver.resolve("wasabi")
        assert c.access_key_id == "AKIA-SHARED"
        assert c.secret_access_key == "sharedsecret"

    def test_static_credentials(self, tmp_path: Path, store: ConfigStore) -> None:
        store.add_connection(
            ConnectionEntry(
                name="static-r2",
                kind="s3-compatible",
                endpoint_url="https://r2.example.com",
                region="auto",
                credentials="static",
                access_key_id="AKIA-STATIC",
                secret_access_key="staticsecret",
            )
        )
        resolver = ConnectionResolver(
            config_store=store,
            aws_config_path=tmp_path / "missing",
            aws_credentials_path=tmp_path / "missing",
        )
        c = resolver.resolve("static-r2")
        assert c.access_key_id == "AKIA-STATIC"
        assert c.secret_access_key == "staticsecret"

    def test_s3_compat_without_keychain_returns_none_keys(
        self, tmp_path: Path, store: ConfigStore
    ) -> None:
        store.add_connection(
            ConnectionEntry(
                name="needs-keychain",
                kind="s3-compatible",
                endpoint_url="http://localhost:9000",
                region="us-east-1",
                credentials="keychain:absent",
            )
        )
        resolver = ConnectionResolver(
            config_store=store,
            keychain=InMemoryKeychain(),
            aws_config_path=tmp_path / "missing",
            aws_credentials_path=tmp_path / "missing",
        )
        c = resolver.resolve("needs-keychain")
        assert isinstance(c, Connection)
        assert c.access_key_id is None
        assert c.secret_access_key is None
