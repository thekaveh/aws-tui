"""Unit tests for ConnectionResolver."""

from __future__ import annotations

import configparser
import os
from dataclasses import FrozenInstanceError
from pathlib import Path
from typing import IO, Any, cast

import pytest

from aws_tui.infra.config_store import ConfigError, ConfigStore, ConnectionEntry
from aws_tui.infra.connection_resolver import (
    Connection,
    ConnectionNotFound,
    ConnectionResolver,
    _read_ini,
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


def test_connection_repr_masks_endpoint_url_secrets() -> None:
    conn = Connection(
        name="r2-prod",
        kind="s3-compatible",
        region="us-east-1",
        source="config",
        endpoint_url="https://user:pass@example.com/bucket?X-Amz-Signature=sig",
        access_key_id="AKID",
        secret_access_key="SECRET",
        session_token="TOKEN",
    )

    rendered = repr(conn)

    assert "user" not in rendered
    assert "pass" not in rendered
    assert "X-Amz-Signature" not in rendered
    assert "sig" not in rendered
    assert "endpoint_url='example.com/bucket'" in rendered
    assert "AKID" not in rendered
    assert "SECRET" not in rendered
    assert "TOKEN" not in rendered


def test_default_paths_honor_standard_aws_file_overrides(
    tmp_path: Path,
    store: ConfigStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = tmp_path / "shared" / "config"
    credentials = tmp_path / "shared" / "credentials"
    config.parent.mkdir()
    config.write_text("[profile override]\nregion = eu-west-1\n", encoding="utf-8")
    credentials.write_text("", encoding="utf-8")
    monkeypatch.setenv("AWS_CONFIG_FILE", str(config))
    monkeypatch.setenv("AWS_SHARED_CREDENTIALS_FILE", str(credentials))

    resolver = ConnectionResolver(config_store=store)

    assert [(item.name, item.region) for item in resolver.list()] == [("override", "eu-west-1")]


def test_default_paths_expand_environment_variables(
    tmp_path: Path,
    store: ConfigStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config, credentials = _write_aws_files(
        tmp_path,
        config_body="[profile expanded]\nregion = ap-southeast-2\n",
        credentials_body="",
    )
    monkeypatch.setenv("AWS_TEST_ROOT", str(tmp_path))
    monkeypatch.setenv("AWS_CONFIG_FILE", "$AWS_TEST_ROOT/.aws/config")
    monkeypatch.setenv("AWS_SHARED_CREDENTIALS_FILE", "$AWS_TEST_ROOT/.aws/credentials")

    resolver = ConnectionResolver(config_store=store)

    assert resolver._aws_config_path == config
    assert resolver._aws_credentials_path == credentials
    assert [item.name for item in resolver.list()] == ["expanded"]


def test_present_empty_standard_path_overrides_are_not_treated_as_unset(
    store: ConfigStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AWS_CONFIG_FILE", "")
    monkeypatch.setenv("AWS_SHARED_CREDENTIALS_FILE", "")

    resolver = ConnectionResolver(config_store=store)

    assert resolver._aws_config_path == Path()
    assert resolver._aws_credentials_path == Path()


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
        self,
        tmp_path: Path,
        store: ConfigStore,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.delenv("AWS_DEFAULT_REGION", raising=False)
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

    def test_standard_default_region_fills_missing_profile_region(
        self,
        tmp_path: Path,
        store: ConfigStore,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        cfg, creds = _write_aws_files(
            tmp_path,
            config_body="[profile noregion]\noutput = json\n",
        )
        monkeypatch.setenv("AWS_DEFAULT_REGION", "eu-west-3")

        resolver = ConnectionResolver(
            config_store=store,
            aws_config_path=cfg,
            aws_credentials_path=creds,
        )

        assert resolver.list()[0].region == "eu-west-3"

    def test_aws_region_does_not_override_botocore_compatible_fallback(
        self,
        tmp_path: Path,
        store: ConfigStore,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        cfg, creds = _write_aws_files(
            tmp_path,
            config_body="[profile noregion]\noutput = json\n",
        )
        monkeypatch.delenv("AWS_DEFAULT_REGION", raising=False)
        monkeypatch.setenv("AWS_REGION", "ap-south-2")

        resolver = ConnectionResolver(
            config_store=store,
            aws_config_path=cfg,
            aws_credentials_path=creds,
        )

        assert resolver.list()[0].region == "us-east-1"


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

    def test_materialize_preserves_an_explicit_connection_verbatim(
        self, tmp_path: Path, store: ConfigStore
    ) -> None:
        explicit = ConnectionEntry(
            name="minio-local",
            kind="s3-compatible",
            endpoint_url="http://localhost:9000/storage",
            region="us-east-1",
            credentials="keychain:minio-local",
            force_path_style=True,
            verify_tls=False,
        )
        store.add_connection(explicit)
        resolver = ConnectionResolver(
            config_store=store,
            keychain=InMemoryKeychain(),
            aws_config_path=tmp_path / "missing",
            aws_credentials_path=tmp_path / "missing",
        )

        materialized = resolver.materialize("minio-local")

        assert materialized == explicit
        assert store.load().connections["minio-local"] == explicit

    def test_materialize_missing_raises(self, tmp_path: Path, store: ConfigStore) -> None:
        resolver = ConnectionResolver(
            config_store=store,
            aws_config_path=tmp_path / "missing",
            aws_credentials_path=tmp_path / "missing",
        )
        with pytest.raises(ConnectionNotFound):
            resolver.materialize("nope")


class TestS3CompatibleCredentialDispatch:
    def test_keychain_backend_failure_is_not_disguised_as_missing_credentials(
        self, tmp_path: Path, store: ConfigStore
    ) -> None:
        class _FailingKeychain(InMemoryKeychain):
            def get(self, service: str, key: str) -> str | None:
                del service, key
                raise RuntimeError("keychain locked")

        store.add_connection(
            ConnectionEntry(
                name="locked",
                kind="s3-compatible",
                endpoint_url="http://localhost:9000",
                credentials="keychain:aws-tui:locked",
            )
        )
        resolver = ConnectionResolver(
            config_store=store,
            keychain=_FailingKeychain(),
            aws_config_path=tmp_path / "missing",
            aws_credentials_path=tmp_path / "missing",
        )

        with pytest.raises(RuntimeError, match="keychain locked"):
            resolver.resolve("locked")

    def test_keychain_credentials(self, tmp_path: Path, store: ConfigStore) -> None:
        kc = InMemoryKeychain()
        kc.set("minio-local", "access_key_id", "AKIA-MINIO")
        kc.set("minio-local", "secret_access_key", "shh")
        kc.set("minio-local", "session_token", "tok")
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
        assert c.session_token == "tok"
        assert c.endpoint_url == "http://localhost:9000"
        assert c.force_path_style is True

    def test_keychain_blank_session_token_resolves_as_none(
        self, tmp_path: Path, store: ConfigStore
    ) -> None:
        kc = InMemoryKeychain()
        kc.set("minio-local", "access_key_id", "AKIA-MINIO")
        kc.set("minio-local", "secret_access_key", "shh")
        kc.set("minio-local", "session_token", "   ")
        store.add_connection(
            ConnectionEntry(
                name="minio-local",
                kind="s3-compatible",
                endpoint_url="http://localhost:9000",
                region="us-east-1",
                credentials="keychain:minio-local",
            )
        )
        resolver = ConnectionResolver(
            config_store=store,
            keychain=kc,
            aws_config_path=tmp_path / "missing",
            aws_credentials_path=tmp_path / "missing",
        )
        assert resolver.resolve("minio-local").session_token is None

    def test_env_credentials(
        self, tmp_path: Path, store: ConfigStore, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("R2_ACCESS_KEY_ID", "AKIA-R2")
        monkeypatch.setenv("R2_SECRET_ACCESS_KEY", "supersecret")
        monkeypatch.setenv("R2_SESSION_TOKEN", "envsession")
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
        assert c.session_token == "envsession"

    def test_env_blank_session_token_resolves_as_none(
        self, tmp_path: Path, store: ConfigStore, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("R2_ACCESS_KEY_ID", "AKIA-R2")
        monkeypatch.setenv("R2_SECRET_ACCESS_KEY", "supersecret")
        monkeypatch.setenv("R2_SESSION_TOKEN", "")
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
        assert resolver.resolve("r2").session_token is None

    def test_aws_profile_credentials(self, tmp_path: Path, store: ConfigStore) -> None:
        cfg, creds = _write_aws_files(
            tmp_path,
            credentials_body=(
                "[shared]\n"
                "aws_access_key_id = AKIA-SHARED\n"
                "aws_secret_access_key = sharedsecret\n"
                "aws_session_token = sharedsession\n"
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
        assert c.session_token == "sharedsession"

    def test_aws_profile_blank_session_token_resolves_as_none(
        self, tmp_path: Path, store: ConfigStore
    ) -> None:
        cfg, creds = _write_aws_files(
            tmp_path,
            credentials_body=(
                "[shared]\n"
                "aws_access_key_id = AKIA-SHARED\n"
                "aws_secret_access_key = sharedsecret\n"
                "aws_session_token =   \n"
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
        assert resolver.resolve("wasabi").session_token is None

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
                session_token="staticsession",
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
        assert c.session_token == "staticsession"

    def test_static_blank_session_token_resolves_as_none(
        self, tmp_path: Path, store: ConfigStore
    ) -> None:
        store.add_connection(
            ConnectionEntry(
                name="static-r2",
                kind="s3-compatible",
                endpoint_url="https://r2.example.com",
                region="auto",
                credentials="static",
                access_key_id="AKIA-STATIC",
                secret_access_key="staticsecret",
                session_token=" ",
            )
        )
        resolver = ConnectionResolver(
            config_store=store,
            aws_config_path=tmp_path / "missing",
            aws_credentials_path=tmp_path / "missing",
        )
        assert resolver.resolve("static-r2").session_token is None

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


def test_malformed_aws_config_is_tolerated(tmp_path: Path, store: ConfigStore) -> None:
    """A duplicate option in ``~/.aws/config`` must not escape the resolver.

    That file is written by other tools and by hand, so a malformed one is an
    ordinary state. Letting ``configparser`` raise reached every caller;
    ``app.py`` guards the sites it owns so boot survived, but
    ``S3ConnectionsVM.connections`` is read from ``S3ConnectionsPanel.compose()``
    — and a ``compose()`` failure bypasses the mount guard — so opening
    Settings, the one screen that could repair the connections, killed the app.
    """
    aws_config = tmp_path / "config"
    aws_config.write_text(
        "[profile broken]\nregion = us-east-1\nregion = us-west-2\n", encoding="utf-8"
    )
    credentials = tmp_path / "credentials"
    credentials.write_text("", encoding="utf-8")
    resolver = ConnectionResolver(
        config_store=store,
        aws_config_path=aws_config,
        aws_credentials_path=credentials,
    )

    # Must not raise. configparser retains whatever it consumed before the
    # duplicate, so the profile is still discovered with its first region —
    # degrading to partial data rather than losing the file entirely.
    connections = resolver.list()

    assert [connection.name for connection in connections] == ["broken"]
    assert connections[0].region == "us-east-1"


@pytest.mark.parametrize(
    "malformed_tail",
    ["region = us-west-2\n", "[profile explicit]\n", "malformed option line\n"],
    ids=["duplicate-option", "duplicate-section", "invalid-option"],
)
def test_partial_ini_values_preserve_defaults_and_multiline_text(
    tmp_path: Path, malformed_tail: str
) -> None:
    path = tmp_path / "config"
    path.write_text(
        "[DEFAULT]\nregion = us-east-1\nnotes = first\n    continued\n\n"
        "[profile inherited]\noutput = json\n"
        "[profile explicit]\nregion = eu-west-1\n" + malformed_tail,
        encoding="utf-8",
    )
    before = path.read_bytes()
    parser = configparser.RawConfigParser()

    assert not _read_ini(parser, path)

    assert parser.get("profile inherited", "region") == "us-east-1"
    assert parser.get("profile explicit", "region") == "eu-west-1"
    assert parser.defaults()["notes"] == "first\ncontinued"
    assert parser.get("profile inherited", "notes") == "first\ncontinued"
    # Normalizing inherited values must not turn them into explicit overrides.
    parser.set(parser.default_section, "notes", "changed default")
    assert parser.get("profile inherited", "notes") == "changed default"
    assert path.read_bytes() == before


def test_explicit_aws_entry_without_a_region_inherits_the_profile_region(
    tmp_path: Path,
    store: ConfigStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Step 2 of the documented region chain was pinned by nothing.

    ``docs/connections.md`` states region resolution follows the explicit
    connection region, then the selected profile's configured region, then
    ``AWS_DEFAULT_REGION``, then ``us-east-1``. Every existing region test
    exercises the AUTO-DISCOVERED path or supplies an explicit region, so
    neutralising ``or self._profile_region(entry.profile)`` survived the whole
    suite — an explicit ``[connections.*]`` entry with no region silently fell
    through to ``us-east-1`` and every Glue/Athena/EMR/S3 call for that
    connection targeted the wrong region.
    """
    config, credentials = _write_aws_files(
        tmp_path,
        config_body="[profile prod]\nregion = eu-west-1\n",
        credentials_body="",
    )
    monkeypatch.setenv("AWS_CONFIG_FILE", str(config))
    monkeypatch.setenv("AWS_SHARED_CREDENTIALS_FILE", str(credentials))
    monkeypatch.delenv("AWS_DEFAULT_REGION", raising=False)

    store.add_connection(
        ConnectionEntry(name="prod-explicit", kind="aws", profile="prod", region=None)
    )
    resolver = ConnectionResolver(config_store=store)

    regions = {item.name: item.region for item in resolver.list()}
    assert regions["prod-explicit"] == "eu-west-1", (
        "explicit entry did not inherit its profile's region"
    )


def test_explicit_region_still_wins_over_the_profile_region(
    tmp_path: Path,
    store: ConfigStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Positive control for the precedence order: step 1 beats step 2."""
    config, credentials = _write_aws_files(
        tmp_path,
        config_body="[profile prod]\nregion = eu-west-1\n",
        credentials_body="",
    )
    monkeypatch.setenv("AWS_CONFIG_FILE", str(config))
    monkeypatch.setenv("AWS_SHARED_CREDENTIALS_FILE", str(credentials))

    store.add_connection(
        ConnectionEntry(name="pinned", kind="aws", profile="prod", region="ap-south-1")
    )
    resolver = ConnectionResolver(config_store=store)

    regions = {item.name: item.region for item in resolver.list()}
    assert regions["pinned"] == "ap-south-1"


def test_percent_in_a_secret_key_is_read_verbatim(tmp_path: Path, store: ConfigStore) -> None:
    """AWS credentials are read with botocore's semantics, not interpolated.

    ``ConfigParser`` interpolates at ``.get()`` -- after ``_read_ini``'s guard --
    so a ``%`` in a secret key raised ``InterpolationSyntaxError`` and surfaced
    through Settings as "the OS keychain could not be read", while ``%%`` was
    silently halved: aws-tui signed with ``ab%cd`` where the AWS CLI used
    ``ab%%cd``, producing SignatureDoesNotMatch in this app only. botocore
    parses both AWS ini files with ``RawConfigParser``.
    """
    creds = tmp_path / "credentials"
    creds.write_text(
        "[percent]\n"
        "aws_access_key_id = AKIAPERCENT\n"
        "aws_secret_access_key = abc%def/ghi\n"
        "[doubled]\n"
        "aws_access_key_id = AKIADOUBLED\n"
        "aws_secret_access_key = ab%%cd\n",
        encoding="utf-8",
    )
    resolver = ConnectionResolver(
        config_store=store,
        aws_config_path=tmp_path / "missing",
        aws_credentials_path=creds,
    )

    assert resolver._read_aws_credentials_profile("percent") == (
        "AKIAPERCENT",
        "abc%def/ghi",
        None,
    )
    # Verbatim, exactly as the AWS CLI would sign with it.
    assert resolver._read_aws_credentials_profile("doubled")[1] == "ab%%cd"


class TestDiscovery:
    @pytest.mark.parametrize("invalid_source", ["aws-config", "aws-credentials"])
    @pytest.mark.parametrize("explicit_credentials", [False, True])
    def test_aws_stat_failure_reports_source_and_retains_other_connections(
        self,
        tmp_path: Path,
        store: ConfigStore,
        monkeypatch: pytest.MonkeyPatch,
        invalid_source: str,
        explicit_credentials: bool,
    ) -> None:
        config, credentials = _write_aws_files(
            tmp_path,
            config_body="[profile usable]\nregion = eu-west-1\n",
            credentials_body="[shared]\naws_access_key_id = EXAMPLE\naws_secret_access_key = secret\n",
        )
        if explicit_credentials:
            store.add_connection(
                ConnectionEntry(
                    name="local",
                    kind="s3-compatible",
                    endpoint_url="http://localhost:9000",
                    credentials="aws-profile:shared",
                )
            )
        blocked = config if invalid_source == "aws-config" else credentials
        before = (config.read_bytes(), credentials.read_bytes())
        original_stat = Path.stat

        def checked_stat(path: Path, *args: Any, **kwargs: Any) -> os.stat_result:
            if path == blocked:
                raise PermissionError("directory-search-denied")
            return original_stat(path, *args, **kwargs)

        resolver = ConnectionResolver(
            config_store=store, aws_config_path=config, aws_credentials_path=credentials
        )
        with monkeypatch.context() as patch:
            patch.setattr(Path, "stat", checked_stat)

            snapshot = resolver.discover()

            assert snapshot.invalid_sources == (invalid_source,)
            expected = ["local"] if explicit_credentials else []
            expected.append("shared" if invalid_source == "aws-config" else "usable")
            assert [c.name for c in snapshot.connections] == expected
            if explicit_credentials:
                assert snapshot.connections[0].access_key_id == (
                    "EXAMPLE" if invalid_source == "aws-config" else None
                )
            assert "directory-search-denied" not in repr(snapshot)
            with pytest.raises(PermissionError):
                resolver.list()
            with pytest.raises(PermissionError):
                resolver.resolve(expected[-1])

        assert (config.read_bytes(), credentials.read_bytes()) == before
        assert resolver.discover().invalid_sources == ()

    def test_array_app_kind_reports_bad_config_without_hiding_profiles(
        self, tmp_path: Path, store: ConfigStore
    ) -> None:
        body = "[connections.bad]\nkind = []\n"
        store.path.write_text(body, encoding="utf-8")
        config, credentials = _write_aws_files(
            tmp_path, config_body="[profile usable]\nregion = eu-west-1\n"
        )
        before = config.read_bytes()
        resolver = ConnectionResolver(
            config_store=store, aws_config_path=config, aws_credentials_path=credentials
        )

        snapshot = resolver.discover()

        assert snapshot.invalid_sources == ("app-config",)
        assert [(c.name, c.source, c.region) for c in snapshot.connections] == [
            ("usable", "auto-aws-profile", "eu-west-1")
        ]
        assert store.path.read_text(encoding="utf-8") == body
        assert config.read_bytes() == before
        assert not credentials.exists()
        with pytest.raises(TypeError, match="unhashable"):
            resolver.list()
        with pytest.raises(TypeError, match="unhashable"):
            resolver.resolve("usable")

    def test_credential_backend_type_error_is_not_disguised_as_bad_app_config(
        self, tmp_path: Path, store: ConfigStore
    ) -> None:
        class FailingKeychain(InMemoryKeychain):
            def get(self, service: str, key: str) -> str | None:
                raise TypeError("credential-backend-failure")

        store.add_connection(
            ConnectionEntry(
                name="local",
                kind="s3-compatible",
                endpoint_url="http://localhost:9000",
                credentials="keychain:local",
            )
        )
        resolver = ConnectionResolver(
            config_store=store,
            keychain=FailingKeychain(),
            aws_config_path=tmp_path / "missing-config",
            aws_credentials_path=tmp_path / "missing-credentials",
        )

        with pytest.raises(TypeError, match="credential-backend-failure"):
            resolver.discover()

    @pytest.mark.parametrize("invalid_source", ["aws-config", "aws-credentials"])
    def test_existing_aws_file_read_failure_is_reported(
        self,
        tmp_path: Path,
        store: ConfigStore,
        monkeypatch: pytest.MonkeyPatch,
        invalid_source: str,
    ) -> None:
        config, credentials = _write_aws_files(
            tmp_path, config_body="[profile configured]\n", credentials_body="[shared]\n"
        )
        blocked = config if invalid_source == "aws-config" else credentials
        before = (config.read_bytes(), credentials.read_bytes())
        original_open = Path.open

        def checked_open(path: Path, *args: Any, **kwargs: Any) -> IO[Any]:
            if path == blocked:
                raise PermissionError("private-error-text")
            return cast(IO[Any], original_open(path, *args, **kwargs))

        with monkeypatch.context() as patch:
            patch.setattr(Path, "open", checked_open)
            resolver = ConnectionResolver(
                config_store=store, aws_config_path=config, aws_credentials_path=credentials
            )
            snapshot = resolver.discover()
            assert snapshot.invalid_sources == (invalid_source,)
            assert [c.name for c in snapshot.connections] == [
                "shared" if invalid_source == "aws-config" else "configured"
            ]
            assert snapshot.connections == tuple(resolver.list())
            assert "private-error-text" not in repr(snapshot)

        assert (config.read_bytes(), credentials.read_bytes()) == before

    def test_empty_snapshot_leaves_missing_files_absent(
        self, tmp_path: Path, store: ConfigStore
    ) -> None:
        config = tmp_path / ".aws" / "config"
        credentials = tmp_path / ".aws" / "credentials"
        resolver = ConnectionResolver(
            config_store=store, aws_config_path=config, aws_credentials_path=credentials
        )

        snapshot = resolver.discover()

        from aws_tui.infra.connection_resolver import ConnectionDiscovery

        assert isinstance(snapshot, ConnectionDiscovery)
        assert snapshot.connections == ()
        assert snapshot.invalid_sources == ()
        assert not store.path.exists()
        assert not config.parent.exists()

    @pytest.mark.parametrize("body", [b"[broken", b"connections = 1\n", b"\xff"])
    def test_bad_app_config_does_not_hide_usable_profiles(
        self, tmp_path: Path, store: ConfigStore, body: bytes
    ) -> None:
        store.path.write_bytes(body)
        config, credentials = _write_aws_files(
            tmp_path, config_body="[profile added]\nregion = eu-west-1\n"
        )
        before = config.read_bytes()
        resolver = ConnectionResolver(
            config_store=store, aws_config_path=config, aws_credentials_path=credentials
        )

        snapshot = resolver.discover()

        assert snapshot.invalid_sources == ("app-config",)
        assert [(c.name, c.source, c.region) for c in snapshot.connections] == [
            ("added", "auto-aws-profile", "eu-west-1")
        ]
        assert store.path.read_bytes() == body
        assert config.read_bytes() == before
        assert not credentials.exists()
        with pytest.raises((ConfigError, UnicodeDecodeError)):
            resolver.list()
        with pytest.raises((ConfigError, UnicodeDecodeError)):
            resolver.resolve("added")

    def test_app_read_failure_is_safe_and_does_not_hide_profiles(
        self, tmp_path: Path, store: ConfigStore, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        config, credentials = _write_aws_files(tmp_path, config_body="[profile added]\n")
        resolver = ConnectionResolver(
            config_store=store, aws_config_path=config, aws_credentials_path=credentials
        )

        def fail_load():
            raise PermissionError("private-error-text")

        monkeypatch.setattr(store, "load", fail_load)
        snapshot = resolver.discover()

        assert snapshot.invalid_sources == ("app-config",)
        assert [c.name for c in snapshot.connections] == ["added"]
        assert "private-error-text" not in repr(snapshot)
        with pytest.raises(PermissionError):
            resolver.list()

    @pytest.mark.parametrize("invalid_source", ["aws-config", "aws-credentials"])
    def test_malformed_aws_ini_retains_profiles_and_bytes(
        self, tmp_path: Path, store: ConfigStore, invalid_source: str
    ) -> None:
        config, credentials = _write_aws_files(
            tmp_path,
            config_body="[profile configured]\nregion = eu-west-1\n",
            credentials_body="[credential-only]\naws_access_key_id = EXAMPLE\n",
        )
        broken = config if invalid_source == "aws-config" else credentials
        duplicate = "region" if invalid_source == "aws-config" else "aws_access_key_id"
        with broken.open("a", encoding="utf-8") as file:
            file.write(f"{duplicate} = private-error-text\n")
        before = (config.read_bytes(), credentials.read_bytes())
        resolver = ConnectionResolver(
            config_store=store, aws_config_path=config, aws_credentials_path=credentials
        )

        snapshot = resolver.discover()

        assert snapshot.invalid_sources == (invalid_source,)
        assert [(c.name, c.region) for c in snapshot.connections] == [
            ("configured", "eu-west-1"),
            ("credential-only", "us-east-1"),
        ]
        assert snapshot.connections == tuple(resolver.list())
        assert (config.read_bytes(), credentials.read_bytes()) == before
        assert "private-error-text" not in repr(snapshot)

    def test_explicit_name_wins_and_region_is_inherited(
        self, tmp_path: Path, store: ConfigStore
    ) -> None:
        config, credentials = _write_aws_files(
            tmp_path,
            config_body="[profile prod]\nregion = eu-west-1\n[profile dev]\nregion = us-west-2\n",
        )
        store.add_connection(ConnectionEntry(name="prod", kind="aws", profile="prod"))
        resolver = ConnectionResolver(
            config_store=store, aws_config_path=config, aws_credentials_path=credentials
        )

        snapshot = resolver.discover()

        assert snapshot.invalid_sources == ()
        assert [(c.name, c.source, c.region) for c in snapshot.connections] == [
            ("prod", "config", "eu-west-1"),
            ("dev", "auto-aws-profile", "us-west-2"),
        ]
        assert snapshot.connections == tuple(resolver.list())

    def test_external_repair_refreshes_diagnostics_and_connections(
        self, tmp_path: Path, store: ConfigStore
    ) -> None:
        store.path.write_text("[broken", encoding="utf-8")
        config, credentials = _write_aws_files(
            tmp_path,
            config_body="[profile prod]\nregion = eu-west-1\nregion = us-east-1\n",
            credentials_body="[shared]\naws_access_key_id = EXAMPLE\naws_access_key_id = OTHER\n",
        )
        resolver = ConnectionResolver(
            config_store=store, aws_config_path=config, aws_credentials_path=credentials
        )
        original = resolver.discover()
        assert original.invalid_sources == ("app-config", "aws-config", "aws-credentials")

        store.path.write_text('[connections.prod]\nkind = "aws"\nregion = "ap-south-1"\n')
        config.write_text("[profile added]\nregion = eu-west-2\n", encoding="utf-8")
        credentials.unlink()
        repaired = resolver.discover()

        assert repaired.invalid_sources == ()
        assert [(c.name, c.source, c.region) for c in repaired.connections] == [
            ("prod", "config", "ap-south-1"),
            ("added", "auto-aws-profile", "eu-west-2"),
        ]
        assert original.invalid_sources == ("app-config", "aws-config", "aws-credentials")
        assert [c.name for c in original.connections] == ["prod", "shared"]
        assert not credentials.exists()

    def test_snapshot_and_connections_are_immutable(
        self, tmp_path: Path, store: ConfigStore
    ) -> None:
        config, credentials = _write_aws_files(tmp_path, config_body="[profile prod]\n")
        snapshot = ConnectionResolver(
            config_store=store, aws_config_path=config, aws_credentials_path=credentials
        ).discover()

        assert isinstance(snapshot.connections, tuple)
        assert isinstance(snapshot.invalid_sources, tuple)
        with pytest.raises(FrozenInstanceError):
            snapshot.connections = ()  # type: ignore[misc]
        with pytest.raises(FrozenInstanceError):
            snapshot.invalid_sources = ("app-config",)  # type: ignore[misc]
        with pytest.raises(FrozenInstanceError):
            snapshot.connections[0].name = "changed"  # type: ignore[misc]

    @pytest.mark.parametrize(
        "source", ["static", "env:TEST_", "keychain:local", "aws-profile:shared"]
    )
    def test_credentials_match_list_and_files_are_unchanged(
        self, tmp_path: Path, store: ConfigStore, monkeypatch: pytest.MonkeyPatch, source: str
    ) -> None:
        config, credentials = _write_aws_files(
            tmp_path,
            config_body="[profile shared]\nregion = eu-west-1\n",
            credentials_body=(
                "[shared]\naws_access_key_id = EXAMPLE\naws_secret_access_key = ab%%cd\n"
                "aws_session_token = session\n"
            ),
        )
        monkeypatch.setenv("TEST_ACCESS_KEY_ID", "EXAMPLE")
        monkeypatch.setenv("TEST_SECRET_ACCESS_KEY", "ab%%cd")
        monkeypatch.setenv("TEST_SESSION_TOKEN", "session")
        keychain = InMemoryKeychain()
        for key, value in (
            ("access_key_id", "EXAMPLE"),
            ("secret_access_key", "ab%%cd"),
            ("session_token", "session"),
        ):
            keychain.set("local", key, value)
        store.add_connection(
            ConnectionEntry(
                name="local",
                kind="s3-compatible",
                endpoint_url="http://localhost:9000",
                credentials=source,
                access_key_id="EXAMPLE",
                secret_access_key="ab%%cd",
                session_token="session",
            )
        )
        before = (store.path.read_bytes(), config.read_bytes(), credentials.read_bytes())
        resolver = ConnectionResolver(
            config_store=store,
            keychain=keychain,
            aws_config_path=config,
            aws_credentials_path=credentials,
        )

        snapshot = resolver.discover()

        assert snapshot.invalid_sources == ()
        assert snapshot.connections == tuple(resolver.list())
        local = snapshot.connections[0]
        assert (local.access_key_id, local.secret_access_key, local.session_token) == (
            "EXAMPLE",
            "ab%%cd",
            "session",
        )
        assert (store.path.read_bytes(), config.read_bytes(), credentials.read_bytes()) == before

    def test_repeated_aws_reads_report_each_invalid_source_only_once(
        self, tmp_path: Path, store: ConfigStore
    ) -> None:
        config, credentials = _write_aws_files(
            tmp_path,
            config_body="[profile shared]\nregion = eu-west-1\nregion = us-east-1\n",
            credentials_body="[shared]\naws_access_key_id = EXAMPLE\naws_access_key_id = OTHER\n",
        )
        store.add_connection(ConnectionEntry(name="prod", kind="aws", profile="shared"))
        store.add_connection(
            ConnectionEntry(
                name="local",
                kind="s3-compatible",
                endpoint_url="http://localhost:9000",
                credentials="aws-profile:shared",
            )
        )
        resolver = ConnectionResolver(
            config_store=store, aws_config_path=config, aws_credentials_path=credentials
        )

        snapshot = resolver.discover()

        assert snapshot.invalid_sources == ("aws-config", "aws-credentials")
        assert snapshot.connections == tuple(resolver.list())
        assert snapshot.connections[0].region == "eu-west-1"
        assert snapshot.connections[1].access_key_id is None


def test_resolve_selected_reads_only_selected_keychain(tmp_path: Path, store: ConfigStore) -> None:
    for name in ("chosen", "other"):
        store.add_connection(
            ConnectionEntry(
                name=name,
                kind="s3-compatible",
                endpoint_url="https://example.invalid",
                credentials=f"keychain:{name}-service",
            )
        )
    reads: list[tuple[str, str]] = []

    class TrackingKeychain(InMemoryKeychain):
        def get(self, service: str, key: str) -> str | None:
            reads.append((service, key))
            return "synthetic-secret"

    config, credentials = _write_aws_files(tmp_path)
    resolver = ConnectionResolver(
        config_store=store,
        keychain=TrackingKeychain(),
        aws_config_path=config,
        aws_credentials_path=credentials,
    )
    assert resolver.resolve_selected("chosen").name == "chosen"
    assert {service for service, _ in reads} == {"chosen-service"}
    assert {key for _, key in reads} == {"access_key_id", "secret_access_key", "session_token"}
    reads.clear()
    with pytest.raises(ConnectionNotFound):
        resolver.resolve_selected("absent")
    assert not reads
    # Existing eager list/resolve semantics remain intact for their callers.
    assert resolver.resolve("chosen").name == "chosen"
    assert {service for service, _ in reads} == {"chosen-service", "other-service"}


def test_resolve_selected_preserves_explicit_over_auto_precedence(
    tmp_path: Path, store: ConfigStore
) -> None:
    store.add_connection(ConnectionEntry(name="chosen", kind="aws", profile="base"))
    config, credentials = _write_aws_files(
        tmp_path,
        config_body="[profile chosen]\nregion = eu-west-1\n[profile base]\nregion = ap-south-1\n",
    )
    resolver = ConnectionResolver(
        config_store=store,
        aws_config_path=config,
        aws_credentials_path=credentials,
    )
    selected = resolver.resolve_selected("chosen")
    assert selected.profile == "base"
    assert selected.region == "ap-south-1"
    assert resolver.resolve_selected("base").source == "auto-aws-profile"
