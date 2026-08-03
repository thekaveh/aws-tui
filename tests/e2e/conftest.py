"""Shared fixtures for E2E journeys."""

from __future__ import annotations

import contextlib
from collections.abc import Iterator
from pathlib import Path

import pytest

from aws_tui.composition import AppContext, build_app_context
from aws_tui.infra.connection_resolver import Connection
from aws_tui.services.s3.service import S3Service
from tests.unit.domain._in_memory_fs import InMemoryFS

_AWS_CREDENTIAL_ENV_VARS = (
    "AWS_ACCESS_KEY_ID",
    "AWS_SECRET_ACCESS_KEY",
    "AWS_SESSION_TOKEN",
    "AWS_SECURITY_TOKEN",
    "AWS_PROFILE",
    "AWS_DEFAULT_PROFILE",
    "AWS_ROLE_ARN",
    "AWS_WEB_IDENTITY_TOKEN_FILE",
    "AWS_ROLE_SESSION_NAME",
    "AWS_CONTAINER_CREDENTIALS_RELATIVE_URI",
    "AWS_CONTAINER_CREDENTIALS_FULL_URI",
    "AWS_CONTAINER_AUTHORIZATION_TOKEN",
    "AWS_CONTAINER_AUTHORIZATION_TOKEN_FILE",
    "AWS_CREDENTIAL_FILE",
)


@pytest.fixture(autouse=True)
def _isolated_aws_files(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep every E2E journey isolated from host AWS credential providers."""
    for variable in _AWS_CREDENTIAL_ENV_VARS:
        monkeypatch.delenv(variable, raising=False)
    monkeypatch.setenv("AWS_EC2_METADATA_DISABLED", "true")
    aws_config = tmp_path / "aws-config"
    aws_credentials = tmp_path / "aws-credentials"
    boto_config = tmp_path / "boto-config"
    aws_config.write_text("", encoding="utf-8")
    aws_credentials.write_text("", encoding="utf-8")
    boto_config.write_text("", encoding="utf-8")
    monkeypatch.setenv("AWS_CONFIG_FILE", str(aws_config))
    monkeypatch.setenv("AWS_SHARED_CREDENTIALS_FILE", str(aws_credentials))
    monkeypatch.setenv("BOTO_CONFIG", str(boto_config))


@pytest.fixture
def app_context(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[AppContext]:
    """Build a fresh ``AppContext`` rooted at tmp dirs (no home pollution)."""
    cfg = tmp_path / "config"
    cache = tmp_path / "cache"
    cfg.mkdir(parents=True, exist_ok=True)
    cache.mkdir(parents=True, exist_ok=True)
    ctx = build_app_context(config_dir=cfg, cache_dir=cache)
    connection = Connection(
        name="e2e-default",
        kind="aws",
        region="us-east-1",
        source="config",
        profile="e2e-default",
    )
    monkeypatch.setattr(ctx.connection_resolver, "list", lambda: [connection])
    s3_service = ctx.registry.get("s3")
    assert isinstance(s3_service, S3Service)
    monkeypatch.setattr(s3_service, "_s3_fs_factory", lambda _connection: InMemoryFS())
    try:
        yield ctx
    finally:
        # Best-effort dispose.
        with contextlib.suppress(Exception):
            ctx.root_vm.dispose()
