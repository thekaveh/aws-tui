"""Infra integration sanity test.

Composes all six infra components against tmp dirs to verify:

1. There are no circular imports between modules.
2. The full happy-path (load config, list connections, resolve both
   kinds, probe their tokens, look up a theme + keybinding) works.

No network calls. No real keychain access — InMemoryKeychain throughout.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path


def _sha1(s: str) -> str:
    return hashlib.sha1(s.encode("utf-8")).hexdigest()


def test_all_m1_components_compose(tmp_path: Path) -> None:
    # Import at function scope so a single circular import surfaces here.
    from aws_tui.infra.aws_session import AwsSession, TokenState
    from aws_tui.infra.config_store import (
        Config,
        ConfigStore,
        ConnectionEntry,
        Defaults,
        Keybindings,
    )
    from aws_tui.infra.connection_resolver import ConnectionResolver
    from aws_tui.infra.keychain import InMemoryKeychain
    from aws_tui.infra.keymap_store import KeymapStore
    from aws_tui.infra.log_sink import LogSink
    from aws_tui.infra.theme_store import ThemeStore

    # 1. LogSink at the cache root.
    log_dir = tmp_path / "log"
    sink = LogSink(base_dir=log_dir)
    sink.info("test.startup")

    # 2. ConfigStore against a tmp config path. Write two connections.
    config_path = tmp_path / "config" / "config.toml"
    store = ConfigStore(path=config_path)
    aws_entry = ConnectionEntry(
        name="kaveh-dev",
        kind="aws",
        profile="kaveh-dev",
        region="us-east-1",
    )
    s3_entry = ConnectionEntry(
        name="minio-local",
        kind="s3-compatible",
        endpoint_url="http://localhost:9000",
        region="us-east-1",
        credentials="keychain:minio-local",
        force_path_style=True,
    )
    store.save(
        Config(
            connections={aws_entry.name: aws_entry, s3_entry.name: s3_entry},
            defaults=Defaults(connection="kaveh-dev"),
            keybindings=Keybindings(),
        )
    )
    sink.info("test.config_saved", path=str(config_path))

    # 3. Keychain holds the s3-compat keys.
    keychain = InMemoryKeychain()
    keychain.set("minio-local", "access_key_id", "AKIA-MINIO")
    keychain.set("minio-local", "secret_access_key", "shh")

    # 4. AWS config + credentials for auto-discovery and SSO probe.
    aws_dir = tmp_path / ".aws"
    aws_config = aws_dir / "config"
    aws_credentials = aws_dir / "credentials"
    aws_dir.mkdir(parents=True)
    aws_config.write_text(
        "[profile kaveh-dev]\n"
        "region = us-east-1\n"
        "sso_session = company-sso\n"
        "[sso-session company-sso]\n"
        "sso_start_url = https://example.awsapps.com/start\n",
        encoding="utf-8",
    )
    aws_credentials.write_text("", encoding="utf-8")

    # 5. ConnectionResolver should see both explicit entries.
    resolver = ConnectionResolver(
        config_store=store,
        keychain=keychain,
        aws_config_path=aws_config,
        aws_credentials_path=aws_credentials,
    )
    names = {c.name for c in resolver.list()}
    assert {"kaveh-dev", "minio-local"}.issubset(names)
    sink.info("test.connections_listed", count=len(names))

    aws_conn = resolver.resolve("kaveh-dev")
    s3_conn = resolver.resolve("minio-local")
    assert aws_conn.source == "config"
    assert aws_conn.kind == "aws"
    assert s3_conn.access_key_id == "AKIA-MINIO"

    # 6. AwsSession probes both. Drop a valid SSO cache entry first.
    sso_cache = tmp_path / "sso-cache"
    sso_cache.mkdir(parents=True)
    expires = datetime.now(UTC) + timedelta(hours=1)
    (sso_cache / f"{_sha1('company-sso')}.json").write_text(
        json.dumps(
            {
                "accessToken": "fake",
                "expiresAt": expires.strftime("%Y-%m-%dT%H:%M:%SZ"),
            }
        ),
        encoding="utf-8",
    )
    session = AwsSession(sso_cache_dir=sso_cache, aws_config_path=aws_config)
    aws_probe = session.probe_token(aws_conn)
    s3_probe = session.probe_token(s3_conn)
    assert aws_probe.state is TokenState.CONNECTED
    assert s3_probe.state is TokenState.CONNECTED
    sink.info("test.probes_done", aws=aws_probe.state, s3=s3_probe.state)

    # 7. ThemeStore + KeymapStore both work standalone alongside the rest.
    theme_store = ThemeStore(
        user_themes_dir=tmp_path / "themes",
        user_overlay=tmp_path / "overlay.tcss",
    )
    assert "carbon" in theme_store.list_themes()
    assert isinstance(theme_store.load("carbon"), str)

    keymap = KeymapStore(overlay={"app.quit": "ctrl+d"})
    assert keymap.resolve("app.quit") == ("ctrl+d",)
    assert keymap.resolve("pane.copy") == ("c",)

    sink.flush()
    sink.close()
    # Log file actually got written.
    assert (log_dir / "aws-tui.log").is_file()


def test_aws_session_handles_missing_sso_cache_for_aws_conn(tmp_path: Path) -> None:
    from aws_tui.infra.aws_session import AwsSession, TokenState
    from aws_tui.infra.connection_resolver import Connection

    aws_cfg = tmp_path / ".aws" / "config"
    aws_cfg.parent.mkdir(parents=True)
    aws_cfg.write_text("[profile dev]\nsso_session = company-sso\n", encoding="utf-8")
    session = AwsSession(sso_cache_dir=tmp_path / "cache", aws_config_path=aws_cfg)
    conn = Connection(
        name="dev",
        kind="aws",
        region="us-east-1",
        source="auto-aws-profile",
        profile="dev",
    )
    result = session.probe_token(conn)
    assert result.state is TokenState.MISSING
