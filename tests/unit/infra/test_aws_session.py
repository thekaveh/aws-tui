"""Unit tests for AwsSession — SSO cache probe and client lifecycle."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from aws_tui.infra.aws_session import (
    AwsSession,
    TokenProbeResult,
    TokenState,
)
from aws_tui.infra.connection_resolver import Connection


def _sha1(s: str) -> str:
    return hashlib.sha1(s.encode("utf-8")).hexdigest()


def _write_cache_entry(
    cache_dir: Path,
    *,
    cache_key: str,
    expires_at: datetime,
    access_token: str = "fake-access-token",
) -> Path:
    cache_dir.mkdir(parents=True, exist_ok=True)
    path = cache_dir / f"{cache_key}.json"
    path.write_text(
        json.dumps(
            {
                "accessToken": access_token,
                "expiresAt": expires_at.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
            }
        ),
        encoding="utf-8",
    )
    return path


def _write_aws_config_with_sso(
    aws_config_path: Path,
    *,
    profile: str,
    sso_session: str | None = None,
    sso_start_url: str | None = None,
) -> None:
    aws_config_path.parent.mkdir(parents=True, exist_ok=True)
    if sso_session is not None:
        body = (
            f"[profile {profile}]\nsso_session = {sso_session}\n"
            f"[sso-session {sso_session}]\nsso_start_url = https://example.awsapps.com/start\n"
        )
    else:
        body = f"[profile {profile}]\nsso_start_url = {sso_start_url}\n"
    aws_config_path.write_text(body, encoding="utf-8")


def _aws_conn(profile: str = "dev", region: str = "us-east-1") -> Connection:
    return Connection(
        name=profile,
        kind="aws",
        region=region,
        source="config",
        profile=profile,
    )


def _s3_conn(
    *,
    access_key_id: str | None = "AKIA",
    secret_access_key: str | None = "secret",
    session_token: str | None = "session",
    verify_tls: bool = True,
) -> Connection:
    return Connection(
        name="minio",
        kind="s3-compatible",
        region="us-east-1",
        source="config",
        endpoint_url="http://localhost:9000",
        access_key_id=access_key_id,
        secret_access_key=secret_access_key,
        session_token=session_token,
        force_path_style=True,
        verify_tls=verify_tls,
    )


class TestProbeTokenAws:
    def test_valid_token_returns_connected(self, tmp_path: Path) -> None:
        aws_cfg = tmp_path / ".aws" / "config"
        sso_session = "company-sso"
        _write_aws_config_with_sso(aws_cfg, profile="dev", sso_session=sso_session)
        cache_dir = tmp_path / "sso-cache"
        expires = datetime.now(UTC) + timedelta(hours=1)
        _write_cache_entry(cache_dir, cache_key=_sha1(sso_session), expires_at=expires)

        session = AwsSession(sso_cache_dir=cache_dir, aws_config_path=aws_cfg)
        result = session.probe_token(_aws_conn())
        assert result.state is TokenState.CONNECTED
        assert result.expires_at is not None

    def test_expired_token_returns_expired(self, tmp_path: Path) -> None:
        aws_cfg = tmp_path / ".aws" / "config"
        sso_session = "company-sso"
        _write_aws_config_with_sso(aws_cfg, profile="dev", sso_session=sso_session)
        cache_dir = tmp_path / "sso-cache"
        expires = datetime.now(UTC) - timedelta(hours=1)
        _write_cache_entry(cache_dir, cache_key=_sha1(sso_session), expires_at=expires)

        session = AwsSession(sso_cache_dir=cache_dir, aws_config_path=aws_cfg)
        result = session.probe_token(_aws_conn())
        assert result.state is TokenState.EXPIRED

    def test_missing_cache_file_returns_missing(self, tmp_path: Path) -> None:
        aws_cfg = tmp_path / ".aws" / "config"
        _write_aws_config_with_sso(aws_cfg, profile="dev", sso_session="company-sso")
        cache_dir = tmp_path / "sso-cache"
        cache_dir.mkdir(parents=True)
        session = AwsSession(sso_cache_dir=cache_dir, aws_config_path=aws_cfg)
        result = session.probe_token(_aws_conn())
        assert result.state is TokenState.MISSING

    def test_profile_without_sso_returns_missing(self, tmp_path: Path) -> None:
        aws_cfg = tmp_path / ".aws" / "config"
        aws_cfg.parent.mkdir(parents=True)
        aws_cfg.write_text("[profile dev]\nregion = us-east-1\n", encoding="utf-8")
        session = AwsSession(
            sso_cache_dir=tmp_path / "cache",
            aws_config_path=aws_cfg,
        )
        result = session.probe_token(_aws_conn())
        assert result.state is TokenState.MISSING

    def test_start_url_fallback(self, tmp_path: Path) -> None:
        aws_cfg = tmp_path / ".aws" / "config"
        _write_aws_config_with_sso(
            aws_cfg,
            profile="dev",
            sso_session=None,
            sso_start_url="https://example.awsapps.com/start",
        )
        cache_dir = tmp_path / "sso-cache"
        expires = datetime.now(UTC) + timedelta(hours=1)
        _write_cache_entry(
            cache_dir,
            cache_key=_sha1("https://example.awsapps.com/start"),
            expires_at=expires,
        )
        session = AwsSession(sso_cache_dir=cache_dir, aws_config_path=aws_cfg)
        result = session.probe_token(_aws_conn())
        assert result.state is TokenState.CONNECTED

    def test_skew_buffer_treats_about_to_expire_as_expired(self, tmp_path: Path) -> None:
        aws_cfg = tmp_path / ".aws" / "config"
        sso_session = "company-sso"
        _write_aws_config_with_sso(aws_cfg, profile="dev", sso_session=sso_session)
        cache_dir = tmp_path / "sso-cache"
        # 30 seconds in the future, but inside the 60-second skew buffer.
        expires = datetime.now(UTC) + timedelta(seconds=30)
        _write_cache_entry(cache_dir, cache_key=_sha1(sso_session), expires_at=expires)

        session = AwsSession(sso_cache_dir=cache_dir, aws_config_path=aws_cfg)
        result = session.probe_token(_aws_conn())
        assert result.state is TokenState.EXPIRED


class TestProbeTokenS3Compatible:
    def test_present_keys_returns_connected(self, tmp_path: Path) -> None:
        session = AwsSession(sso_cache_dir=tmp_path)
        result = session.probe_token(_s3_conn())
        assert result == TokenProbeResult(state=TokenState.CONNECTED)

    def test_missing_keys_returns_missing(self, tmp_path: Path) -> None:
        session = AwsSession(sso_cache_dir=tmp_path)
        result = session.probe_token(_s3_conn(access_key_id=None, secret_access_key=None))
        assert result.state is TokenState.MISSING

    def test_partial_keys_returns_missing(self, tmp_path: Path) -> None:
        session = AwsSession(sso_cache_dir=tmp_path)
        result = session.probe_token(_s3_conn(secret_access_key=None))
        assert result.state is TokenState.MISSING


class TestClientLifecycle:
    async def test_aclose_all_clients_awaits_each_opened_client(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Two fake client context managers that record __aexit__ calls.
        exits: list[str] = []

        class FakeCM:
            def __init__(self, name: str) -> None:
                self.name = name

            async def __aenter__(self) -> Any:
                return MagicMock(name=f"client-{self.name}")

            async def __aexit__(self, *_args: Any) -> None:
                exits.append(self.name)

        cms = iter([FakeCM("a"), FakeCM("b")])

        class FakeSession:
            def __init__(self, **_kwargs: Any) -> None:
                pass

            def client(self, *_args: Any, **_kwargs: Any) -> Any:
                return next(cms)

        import aioboto3

        monkeypatch.setattr(aioboto3, "Session", FakeSession)

        session = AwsSession(sso_cache_dir=tmp_path)
        conn = _aws_conn()
        cm1 = await session.client(conn, "s3")
        cm2 = await session.client(conn, "ec2")
        # Enter both so they're tracked as "open".
        await cm1.__aenter__()
        await cm2.__aenter__()
        await session.aclose_all_clients()
        assert sorted(exits) == ["a", "b"]

    async def test_client_constructs_aws_session_with_profile(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        captured: dict[str, Any] = {}

        class FakeSession:
            def __init__(self, **kwargs: Any) -> None:
                captured["session_kwargs"] = kwargs

            def client(self, service: str, **kwargs: Any) -> Any:
                captured["service"] = service
                captured["client_kwargs"] = kwargs
                return AsyncMock()

        import aioboto3

        monkeypatch.setattr(aioboto3, "Session", FakeSession)

        session = AwsSession(sso_cache_dir=tmp_path)
        conn = _aws_conn(profile="kaveh-dev", region="us-west-2")
        await session.client(conn, "s3")
        assert captured["session_kwargs"] == {
            "profile_name": "kaveh-dev",
            "region_name": "us-west-2",
        }
        assert captured["service"] == "s3"

    async def test_client_constructs_s3_compat_session_with_static_keys(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        captured: dict[str, Any] = {}

        class FakeSession:
            def __init__(self, **kwargs: Any) -> None:
                captured["session_kwargs"] = kwargs

            def client(self, service: str, **kwargs: Any) -> Any:
                captured["service"] = service
                captured["client_kwargs"] = kwargs
                return AsyncMock()

        import aioboto3

        monkeypatch.setattr(aioboto3, "Session", FakeSession)

        session = AwsSession(sso_cache_dir=tmp_path)
        conn = _s3_conn(verify_tls=False)
        await session.client(conn, "s3")
        sess_kwargs = captured["session_kwargs"]
        assert sess_kwargs["aws_access_key_id"] == "AKIA"
        assert sess_kwargs["aws_secret_access_key"] == "secret"
        assert sess_kwargs["aws_session_token"] == "session"
        assert sess_kwargs["region_name"] == "us-east-1"
        client_kwargs = captured["client_kwargs"]
        assert client_kwargs["endpoint_url"] == "http://localhost:9000"
        assert client_kwargs["verify"] is False
        # Force-path-style propagates through botocore Config.
        assert "config" in client_kwargs
