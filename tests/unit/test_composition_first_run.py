"""Tests for the first-run helpers in :mod:`aws_tui.composition`."""

from __future__ import annotations

from pathlib import Path

from aws_tui.composition import (
    add_s3_compat_connection,
    needs_first_run,
)
from aws_tui.infra.config_store import ConfigStore
from aws_tui.infra.connection_resolver import ConnectionResolver
from aws_tui.vm.chrome.first_run_vm import S3CompatForm


def _resolver_empty(tmp_path: Path, config_store: ConfigStore) -> ConnectionResolver:
    return ConnectionResolver(
        config_store=config_store,
        aws_config_path=tmp_path / "no-aws-config",
        aws_credentials_path=tmp_path / "no-aws-credentials",
    )


def test_needs_first_run_true_with_empty_config_and_aws(tmp_path: Path) -> None:
    config_store = ConfigStore(path=tmp_path / "config.toml")
    resolver = _resolver_empty(tmp_path, config_store)
    assert needs_first_run(config_store=config_store, connection_resolver=resolver) is True


def test_needs_first_run_false_when_config_has_entry(tmp_path: Path) -> None:
    config_store = ConfigStore(path=tmp_path / "config.toml")
    add_s3_compat_connection(
        config_store=config_store,
        form=S3CompatForm(
            name="minio",
            endpoint_url="http://localhost:9000",
            region="us-east-1",
            access_key_id="AKID",
            secret_access_key="SECRET",
        ),
    )
    resolver = _resolver_empty(tmp_path, config_store)
    assert needs_first_run(config_store=config_store, connection_resolver=resolver) is False


def test_needs_first_run_false_when_aws_profile_exists(tmp_path: Path) -> None:
    config_store = ConfigStore(path=tmp_path / "config.toml")
    aws_config = tmp_path / "config"
    aws_config.write_text("[profile kaveh-dev]\nregion=us-east-1\n", encoding="utf-8")
    resolver = ConnectionResolver(
        config_store=config_store,
        aws_config_path=aws_config,
        aws_credentials_path=tmp_path / "no-creds",
    )
    assert needs_first_run(config_store=config_store, connection_resolver=resolver) is False


def test_add_s3_compat_writes_entry(tmp_path: Path) -> None:
    config_store = ConfigStore(path=tmp_path / "config.toml")
    form = S3CompatForm(
        name="minio-local",
        endpoint_url="http://localhost:9000",
        region="us-east-1",
        access_key_id="MINIO_AKID",
        secret_access_key="MINIO_SECRET",
        session_token="MINIO_SESSION",
        force_path_style=True,
        verify_tls=False,
    )
    add_s3_compat_connection(config_store=config_store, form=form)
    cfg = config_store.load()
    assert "minio-local" in cfg.connections
    entry = cfg.connections["minio-local"]
    assert entry.kind == "s3-compatible"
    assert entry.endpoint_url == "http://localhost:9000"
    assert entry.access_key_id == "MINIO_AKID"
    assert entry.session_token == "MINIO_SESSION"
    assert entry.verify_tls is False


def test_add_s3_compat_normalizes_form_values(tmp_path: Path) -> None:
    config_store = ConfigStore(path=tmp_path / "config.toml")
    form = S3CompatForm(
        name=" minio-local ",
        endpoint_url=" http://localhost:9000 ",
        region=" us-east-1 ",
        access_key_id=" MINIO_AKID ",
        secret_access_key=" MINIO_SECRET ",
        session_token="   ",
        force_path_style=True,
        verify_tls=False,
    )
    add_s3_compat_connection(config_store=config_store, form=form)
    entry = config_store.load().connections["minio-local"]
    assert entry.endpoint_url == "http://localhost:9000"
    assert entry.region == "us-east-1"
    assert entry.access_key_id == "MINIO_AKID"
    assert entry.secret_access_key == "MINIO_SECRET"
    assert entry.session_token is None
