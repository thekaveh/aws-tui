"""Synthetic, offline fixtures for the read-only diagnostic collector."""

from __future__ import annotations

import hashlib
import json
import os
import socket
import stat
import subprocess
from dataclasses import FrozenInstanceError, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import Mock

import aioboto3
import botocore.session
import keyring
import pytest

from aws_tui.infra import doctor
from aws_tui.infra.aws_session import AwsSession, TokenLoadError
from aws_tui.infra.doctor import (
    DoctorCheck,
    DoctorPaths,
    DoctorReport,
    collect_local_diagnostics,
    doctor_paths,
)


@pytest.fixture
def paths(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir(mode=0o700)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    monkeypatch.setenv("HOME", str(home))
    for key in tuple(os.environ):
        if key.startswith(("AWS_", "SYNTH_")):
            monkeypatch.delenv(key)
    result = DoctorPaths(
        home / "config.toml",
        home / "cache",
        home / "aws-config",
        home / "aws-credentials",
        home / "sso",
    )
    result.sso_cache_dir.mkdir()
    return result


def _config(paths, body):
    paths.config_file.write_text(body, encoding="utf-8")


def _aws(paths, body):
    paths.aws_config_file.write_text(body, encoding="utf-8")


def _static_profile(paths, profile="dev"):
    paths.aws_credentials_file.write_text(
        f"[{profile}]\naws_access_key_id = synthetic-access\naws_secret_access_key = synthetic-secret\naws_session_token = synthetic-session\n",
        encoding="utf-8",
    )


def _sso(paths, modern=True, expires=None, payload=None):
    key = "synthetic-session" if modern else "https://example.invalid/start"
    _aws(
        paths,
        "[profile dev]\n"
        + (
            f"sso_session = {key}\n[sso-session {key}]\nsso_start_url = https://example.invalid/start\n"
            if modern
            else f"sso_start_url = {key}\n"
        ),
    )
    cache = paths.sso_cache_dir / (hashlib.sha1(key.encode()).hexdigest() + ".json")
    content = (
        payload
        if payload is not None
        else {
            "accessToken": "synthetic-access-token",
            "refreshToken": "synthetic-refresh-token",
            "clientSecret": "synthetic-client-secret",
            "expiresAt": (expires or datetime.now(UTC) + timedelta(hours=1)).isoformat(),
        }
    )
    cache.write_text(json.dumps(content), encoding="utf-8")
    return cache


def _auth(report):
    return [check for check in report.checks if check.name == "auth"]


def _snapshot(root):
    return {
        str(path.relative_to(root)): (
            stat.S_IMODE(path.stat().st_mode),
            path.read_bytes() if path.is_file() else None,
        )
        for path in [root, *sorted(root.rglob("*"))]
    }


def test_report_schema_exit_and_immutable_context():
    context = {"path": "/tmp/config.toml"}
    check = DoctorCheck("config", "ok", context, "No action needed.")
    report = DoctorReport((check,))
    context["path"] = "changed"
    assert check.context["path"] == "/tmp/config.toml"
    with pytest.raises(TypeError):
        check.context["path"] = "changed"
    with pytest.raises(FrozenInstanceError):
        report.checks = ()
    payload = json.loads(report.render_json())
    assert payload["schema_version"] == 1
    assert set(payload["checks"][0]) >= {"name", "result", "context", "next_step"}
    assert report.exit_code == 0
    assert "config" in report.render_text()
    assert "No action needed." in report.render_text()
    assert (
        replace(
            report,
            checks=(
                *report.checks,
                DoctorCheck("config", "invalid_config", {}, "Repair config.", True),
            ),
        ).exit_code
        == 1
    )


def test_runtime_defaults_honor_overrides_without_creation(paths, monkeypatch):
    monkeypatch.setattr(doctor, "config_home", lambda: paths.config_file.parent / "missing-config")
    monkeypatch.setattr(doctor, "cache_home", lambda: paths.cache_dir)
    monkeypatch.setenv("SYNTH_ROOT", str(paths.config_file.parent))
    monkeypatch.setenv("AWS_CONFIG_FILE", "$SYNTH_ROOT/aws-config")
    monkeypatch.setenv("AWS_SHARED_CREDENTIALS_FILE", "~/aws-credentials")
    before = _snapshot(paths.config_file.parent)
    actual = doctor_paths()
    assert actual.aws_config_file == paths.aws_config_file
    assert actual.aws_credentials_file == paths.aws_credentials_file
    assert actual.sso_cache_dir == Path.home() / ".aws/sso/cache"
    assert _snapshot(paths.config_file.parent) == before
    monkeypatch.setenv("AWS_CONFIG_FILE", "")
    assert doctor_paths().aws_config_file == Path()


@pytest.mark.parametrize("usable", [False, True])
def test_missing_config_with_and_without_usable_auto_profile(paths, usable):
    if usable:
        _static_profile(paths)
    report = collect_local_diagnostics(paths)
    assert any(c.result == "missing_config" and not c.actionable for c in report.checks)
    assert report.exit_code == (0 if usable else 1)
    assert any(
        c.name == "sources" and c.result == ("ok" if usable else "missing_credentials")
        for c in report.checks
    )
    assert report.checks[-1].result == "skipped"
    assert not report.checks[-1].actionable


@pytest.mark.parametrize(
    "raw",
    [
        b"not = [valid",
        b"\xff",
        b'[connections.bad]\nkind="aws"\nprofile=42',
        b'[connections.bad]\nkind="s3-compatible"\nendpoint_url="http://example.invalid"\ncredentials="static"',
    ],
)
def test_invalid_config_is_actionable(paths, raw):
    paths.config_file.write_bytes(raw)
    report = collect_local_diagnostics(paths)
    assert any(c.result == "invalid_config" and c.actionable for c in report.checks)
    assert report.exit_code == 1


@pytest.mark.parametrize(
    ("overlay", "result"),
    [
        ('"pane.copy" = "ctrl k"', "invalid_keybinding"),
        ('"pane.copy" = "q"', "keybinding_collision"),
        ('"unknown.action" = "x"', "unknown_action"),
    ],
)
def test_keymap_validation(paths, overlay, result):
    _config(paths, "[keybindings]\n" + overlay)
    assert any(c.result == result and c.actionable for c in collect_local_diagnostics(paths).checks)


@pytest.mark.parametrize("provider", ["env:SYNTH_", "aws-profile:dev"])
def test_s3_missing_env_and_shared_keys(paths, provider):
    _config(
        paths,
        f'[connections.local]\nkind="s3-compatible"\nendpoint_url="http://example.invalid"\ncredentials="{provider}"\n',
    )
    assert _auth(collect_local_diagnostics(paths))[0].result == "missing_credentials"


@pytest.mark.parametrize("provider", ["static", "env:SYNTH_", "aws-profile:dev"])
def test_s3_local_credential_presence(paths, monkeypatch, provider):
    _config(
        paths,
        f'[connections.local]\nkind="s3-compatible"\nendpoint_url="http://example.invalid"\ncredentials="{provider}"\naccess_key_id="synthetic-access"\nsecret_access_key="synthetic-secret"\nsession_token="synthetic-session"\n',
    )
    monkeypatch.setenv("SYNTH_ACCESS_KEY_ID", "synthetic-access")
    monkeypatch.setenv("SYNTH_SECRET_ACCESS_KEY", "synthetic-secret")
    _static_profile(paths)
    assert all(c.result == "ok" for c in _auth(collect_local_diagnostics(paths)))


def test_static_aws_profile_proves_presence_only(paths):
    _static_profile(paths)
    check = _auth(collect_local_diagnostics(paths))[0]
    assert check.result == "ok"
    assert "permission" in check.next_step.lower()


@pytest.mark.parametrize("modern", [False, True])
def test_valid_modern_and_legacy_sso_reuses_token_probe(paths, modern, monkeypatch):
    _sso(paths, modern)
    original = AwsSession.probe_token
    probe = Mock(side_effect=original, autospec=None)
    monkeypatch.setattr(AwsSession, "probe_token", lambda self, conn: probe(self, conn))
    assert _auth(collect_local_diagnostics(paths))[0].result == "ok"
    assert probe.call_count == 1


@pytest.mark.parametrize(
    "state",
    [
        "expired",
        "missing",
        "unreadable",
        "missing-token",
        "blank-token",
        "bad-expiry",
        "missing-expiry",
        "array",
        "string",
        "null",
        "encoding",
    ],
)
def test_sso_cache_failures(paths, state):
    cache = _sso(
        paths, expires=datetime.now(UTC) - timedelta(hours=1) if state == "expired" else None
    )
    expected = "unreadable_sso"
    if state == "missing":
        cache.unlink()
        expected = "missing_credentials"
    elif state == "expired":
        expected = "expired_sso"
    elif state == "unreadable":
        cache.write_text("not-json synthetic-secret", encoding="utf-8")
    elif state == "encoding":
        cache.write_bytes(b"\xffsynthetic-secret")
    elif state in {"array", "string", "null"}:
        cache.write_text(
            {"array": "[]", "string": '"synthetic-secret"', "null": "null"}[state], encoding="utf-8"
        )
    else:
        payload = json.loads(cache.read_text())
        if state == "missing-token":
            payload.pop("accessToken")
            expected = "missing_credentials"
        elif state == "blank-token":
            payload["accessToken"] = " "
            expected = "missing_credentials"
        elif state == "missing-expiry":
            payload.pop("expiresAt")
        else:
            payload["expiresAt"] = "invalid"
        cache.write_text(json.dumps(payload), encoding="utf-8")
    check = _auth(collect_local_diagnostics(paths))[0]
    assert check.result == expected
    assert check.actionable


def test_token_load_error_is_contained(paths, monkeypatch):
    _sso(paths)
    monkeypatch.setattr(
        AwsSession,
        "probe_token",
        Mock(
            side_effect=TokenLoadError(
                "Authorization: Bearer raw-secret SELECT * FROM secret_table"
            )
        ),
    )
    report = collect_local_diagnostics(paths)
    assert _auth(report)[0].result == "unreadable_sso"
    assert "raw-secret" not in report.render_text() + report.render_json()


@pytest.mark.parametrize("which", ["aws_config_file", "aws_credentials_file"])
@pytest.mark.parametrize("raw", [b"not-ini", b"[dev]\na=1\na=2", b"\xff"])
def test_invalid_aws_discovery_is_not_healthy_or_missing(paths, which, raw):
    getattr(paths, which).write_bytes(raw)
    report = collect_local_diagnostics(paths)
    assert any(
        c.name == "discovery" and c.result == "invalid_config" and c.actionable
        for c in report.checks
    )


@pytest.mark.parametrize("which", ["config_file", "aws_config_file", "aws_credentials_file"])
def test_inaccessible_input_is_not_missing(paths, which, monkeypatch):
    target = getattr(paths, which)
    target.write_text("synthetic", encoding="utf-8")
    original = Path.open
    monkeypatch.setattr(
        Path,
        "open",
        lambda self, *args, **kwargs: (
            (_ for _ in ()).throw(PermissionError("synthetic-secret"))
            if self == target
            else original(self, *args, **kwargs)
        ),
    )
    report = collect_local_diagnostics(paths)
    assert any(c.result == "invalid_config" and c.actionable for c in report.checks)


@pytest.mark.parametrize(
    "body",
    [
        "credential_process = /never/run\n",
        "role_arn = arn:aws:iam::123456789012:role/private\ncredential_source = Ec2InstanceMetadata\n",
        "web_identity_token_file = /never/read\nrole_arn = private\n",
    ],
)
def test_dynamic_providers_unverified_offline(paths, body):
    _aws(paths, "[profile dev]\n" + body)
    check = _auth(collect_local_diagnostics(paths))[0]
    assert check.result == "unverified"
    assert not check.actionable


def test_non_sso_probe_connected_does_not_prove_credentials(paths):
    _aws(paths, "[profile dev]\nregion = us-east-1\n")
    assert _auth(collect_local_diagnostics(paths))[0].result == "missing_credentials"


def test_explicit_profile_does_not_inherit_global_env(paths, monkeypatch):
    _config(paths, '[connections.explicit]\nkind="aws"\nprofile="absent"\n')
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "synthetic-access")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "synthetic-secret")
    assert _auth(collect_local_diagnostics(paths))[0].result == "missing_credentials"


def test_profileless_aws_env_presence_and_dynamic_fallback(paths, monkeypatch):
    _config(paths, '[connections.default]\nkind="aws"\n')
    assert _auth(collect_local_diagnostics(paths))[0].result == "unverified"
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "synthetic-access")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "synthetic-secret")
    assert _auth(collect_local_diagnostics(paths))[0].result == "ok"


@pytest.mark.parametrize("cycle", [False, True])
def test_role_source_sso_and_cycle(paths, cycle):
    _sso(paths)
    with paths.aws_config_file.open("a") as file:
        file.write(
            "[profile role]\nrole_arn = private\nsource_profile = "
            + ("role" if cycle else "dev")
            + "\n"
        )
    checks = _auth(collect_local_diagnostics(paths))
    assert checks[-1].result == ("invalid_config" if cycle else "unverified")
    assert checks[-1].actionable == cycle


def test_keychain_source_unverified_and_never_called(paths, monkeypatch):
    _config(
        paths,
        '[connections.local]\nkind="s3-compatible"\nendpoint_url="http://example.invalid"\ncredentials="keychain:private"\n',
    )
    forbidden = Mock(side_effect=AssertionError("keychain invoked"))
    monkeypatch.setattr(keyring, "get_password", forbidden)
    monkeypatch.setattr(keyring, "get_keyring", forbidden)
    assert _auth(collect_local_diagnostics(paths))[0].result == "unverified"
    forbidden.assert_not_called()


def test_serializers_whitelist_redact_and_strip_controls(monkeypatch):
    spy = Mock(wraps=doctor.redact_text)
    monkeypatch.setattr(doctor, "redact_text", spy)
    check = DoctorCheck(
        "config",
        "invalid_config",
        {
            "path": "\x1b[31m/tmp/Authorization: Bearer auth-secret",
            "endpoint": "https://user:password@host.invalid/path?signature=signed-secret#fragment-secret",
            "source": "SELECT * FROM private_table",
            "access_token": "access-token-secret",
            "raw": "raw-secret",
        },
        "Repair secret_access_key=next-secret\x07",
        True,
    )
    output = DoctorReport((check,)).render_json() + DoctorReport((check,)).render_text()
    for secret in (
        "auth-secret",
        "password",
        "signed-secret",
        "fragment-secret",
        "private_table",
        "access-token-secret",
        "raw-secret",
        "next-secret",
        "\x1b",
        "\x07",
    ):
        assert secret not in output
    assert spy.call_count > 0
    assert "invalid_config" in output


def test_collector_has_no_side_effects_or_sensitive_output(paths, monkeypatch):
    _sso(paths)
    _static_profile(paths)
    _config(
        paths,
        '[connections."SELECT * FROM hidden_sql"]\nkind="s3-compatible"\nendpoint_url="https://endpoint-user:endpoint-password@example.invalid/path?signature=presigned-secret#fragment-secret"\ncredentials="static"\naccess_key_id="static-access-secret"\nsecret_access_key="static-secret-secret"\nsession_token="static-session-secret"\n[connections.keychain]\nkind="s3-compatible"\nendpoint_url="http://example.invalid"\ncredentials="keychain:keychain-secret"\n',
    )
    paths.cache_dir.mkdir()
    (paths.cache_dir / "log").mkdir()
    (paths.cache_dir / "crash").mkdir()
    (paths.cache_dir / "log" / "old.log").write_text(
        "SELECT * FROM log_sql Authorization: Bearer log-secret"
    )
    (paths.cache_dir / "crash" / "old.json").write_text("crash-secret")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "env-secret")
    monkeypatch.setenv("AWS_SESSION_TOKEN", "env-session-secret")
    monkeypatch.setenv("UNRELATED_SQL", "SELECT * FROM env_sql")
    before = _snapshot(paths.config_file.parent)
    forbidden = Mock(side_effect=AssertionError("forbidden offline action"))
    for owner, name in [
        (socket, "socket"),
        (keyring, "get_password"),
        (keyring, "get_keyring"),
        (subprocess, "Popen"),
        (aioboto3, "Session"),
        (botocore.session, "Session"),
        (Path, "write_text"),
        (Path, "write_bytes"),
        (Path, "chmod"),
        (Path, "mkdir"),
        (Path, "unlink"),
        (os, "chmod"),
        (os, "replace"),
    ]:
        monkeypatch.setattr(owner, name, forbidden)
    original_open = Path.open

    def read_only_open(self, mode="r", *args, **kwargs):
        assert not any(flag in mode for flag in "wax+")
        return original_open(self, mode, *args, **kwargs)

    monkeypatch.setattr(Path, "open", read_only_open)
    report = collect_local_diagnostics(paths)
    output = report.render_json() + report.render_text()
    for secret in (
        "static-access-secret",
        "static-secret-secret",
        "static-session-secret",
        "env-secret",
        "env-session-secret",
        "keychain-secret",
        "synthetic-access-token",
        "synthetic-refresh-token",
        "synthetic-client-secret",
        "endpoint-user",
        "endpoint-password",
        "presigned-secret",
        "fragment-secret",
        "hidden_sql",
        "log_sql",
        "log-secret",
        "crash-secret",
        "env_sql",
        "synthetic-access",
        "synthetic-secret",
        "synthetic-session",
    ):
        assert secret not in output
    assert _snapshot(paths.config_file.parent) == before
    assert collect_local_diagnostics(paths).render_json() == report.render_json()
    forbidden.assert_not_called()


@pytest.mark.parametrize("which", ["config_file", "aws_config_file", "aws_credentials_file"])
def test_input_directory_is_invalid_not_missing(paths, which):
    getattr(paths, which).mkdir()
    report = collect_local_diagnostics(paths)
    assert any(check.result == "invalid_config" and check.actionable for check in report.checks)


@pytest.mark.parametrize("target_kind", ["config", "aws-config", "aws-credentials", "sso"])
def test_stat_errors_remain_unreadable(paths, monkeypatch, target_kind):
    cache = _sso(paths)
    targets = {
        "config": paths.config_file,
        "aws-config": paths.aws_config_file,
        "aws-credentials": paths.aws_credentials_file,
        "sso": cache,
    }
    target = targets[target_kind]
    original = Path.stat

    def guarded_stat(self, *args, **kwargs):
        if self == target:
            raise PermissionError("secret_access_key=stat-error-secret")
        return original(self, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", guarded_stat)
    report = collect_local_diagnostics(paths)
    expected = "unreadable_sso" if target_kind == "sso" else "invalid_config"
    assert any(check.result == expected and check.actionable for check in report.checks)
    assert "stat-error-secret" not in report.render_json() + report.render_text()


def test_unreadable_sso_cache_is_not_missing(paths, monkeypatch):
    cache = _sso(paths)
    original = Path.open

    def guarded_open(self, *args, **kwargs):
        if self == cache:
            raise PermissionError("Authorization: Bearer cache-error-secret")
        return original(self, *args, **kwargs)

    monkeypatch.setattr(Path, "open", guarded_open)
    report = collect_local_diagnostics(paths)
    assert _auth(report)[0].result == "unreadable_sso"
    assert "cache-error-secret" not in report.render_json() + report.render_text()


def test_malformed_access_token_type_is_unreadable(paths):
    _sso(
        paths,
        payload={
            "accessToken": ["synthetic-secret"],
            "expiresAt": (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
        },
    )
    assert _auth(collect_local_diagnostics(paths))[0].result == "unreadable_sso"


def test_multihop_sso_and_nested_process_provider(paths):
    _sso(paths)
    with paths.aws_config_file.open("a") as file:
        file.write(
            "[profile middle]\nrole_arn = private\nsource_profile = dev\n[profile outer]\nrole_arn = private\nsource_profile = middle\n[profile process]\ncredential_process = /never/run\n[profile process-role]\nrole_arn = private\nsource_profile = process\n"
        )
    checks = _auth(collect_local_diagnostics(paths))
    assert [check.result for check in checks] == [
        "ok",
        "unverified",
        "unverified",
        "unverified",
        "unverified",
    ]


@pytest.mark.parametrize("missing", ["aws_access_key_id", "aws_secret_access_key"])
def test_partial_static_aws_keys_are_missing(paths, missing):
    present = "aws_secret_access_key" if missing == "aws_access_key_id" else "aws_access_key_id"
    paths.aws_credentials_file.write_text(
        f"[dev]\n{present} = synthetic-secret\n", encoding="utf-8"
    )
    assert _auth(collect_local_diagnostics(paths))[0].result == "missing_credentials"


def test_invalid_discovery_does_not_claim_unknown_auth_is_missing(paths):
    _config(paths, '[connections.explicit]\nkind="aws"\nprofile="private"\n')
    paths.aws_credentials_file.write_bytes(b"\xffsynthetic-secret")
    checks = _auth(collect_local_diagnostics(paths))
    assert checks[0].result == "unverified"
    assert not checks[0].actionable


@pytest.mark.parametrize(
    "query",
    ["SELECT\n* FROM hidden_query", "SELECT\t* FROM hidden_query", "SELECT\x00* FROM hidden_query"],
)
def test_control_characters_cannot_hide_sql_from_projection(query):
    report = DoctorReport((DoctorCheck("config", "ok", {"path": query}, "No action needed."),))
    assert "hidden_query" not in report.render_json() + report.render_text()


@pytest.mark.parametrize(
    "location",
    ["metadata", "invalid-config", "invalid-keymap", "invalid-sso", "environment", "logs"],
)
def test_all_privacy_sentinels_in_untrusted_inputs(paths, monkeypatch, location):
    sentinels = (
        "opaque-static-access",
        "opaque-static-secret",
        "opaque-static-session",
        "opaque-env-access",
        "opaque-env-secret",
        "opaque-env-session",
        "opaque-keychain-access",
        "opaque-keychain-secret",
        "opaque-keychain-session",
        "opaque-sso-access",
        "opaque-sso-refresh",
        "opaque-sso-client-secret",
        "opaque-auth-header",
        "opaque-presigned-secret",
        "opaque-sql-table",
    )
    planted = " ".join(sentinels) + " SELECT * FROM opaque-sql-table"
    if location == "metadata":
        _config(
            paths,
            f'[connections.{json.dumps(planted)}]\nkind="aws"\nprofile={json.dumps(planted)}\nregion={json.dumps(planted)}\n',
        )
    elif location == "invalid-config":
        _config(paths, f"[connections.bad]\nkind={json.dumps(planted)}\n")
    elif location == "invalid-keymap":
        _config(paths, f'[keybindings]\n{json.dumps(planted)} = "x"\n')
    elif location == "invalid-sso":
        _sso(paths, payload=planted)
    elif location == "environment":
        monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", planted)
        monkeypatch.setenv("AWS_SESSION_TOKEN", planted)
        monkeypatch.setenv("AWS_DEFAULT_REGION", planted)
        _static_profile(paths)
    else:
        paths.cache_dir.mkdir()
        (paths.cache_dir / "log").mkdir()
        (paths.cache_dir / "crash").mkdir()
        (paths.cache_dir / "log" / "existing.log").write_text(planted, encoding="utf-8")
        (paths.cache_dir / "crash" / "existing.json").write_text(planted, encoding="utf-8")
    report = collect_local_diagnostics(paths)
    for output in (report.render_json(), report.render_text()):
        assert all(sentinel not in output for sentinel in sentinels)


def test_effective_log_and_crash_paths_and_source_origin(paths):
    _static_profile(paths)
    report = collect_local_diagnostics(paths)
    path_check = next(check for check in report.checks if check.name == "paths")
    assert path_check.context["log"] == str(paths.cache_dir / "log" / "aws-tui.log")
    assert path_check.context["crash"] == str(paths.cache_dir / "crash")
    check = _auth(report)[0]
    assert check.context == {"source": "1", "kind": "aws", "origin": "auto-aws-profile"}


@pytest.mark.parametrize("source_state", ["static", "sso", "missing", "expired", "unreadable"])
def test_role_source_prerequisites_never_prove_derived_credentials(paths, source_state):
    if source_state == "static":
        _static_profile(paths)
        _aws(paths, "[profile dev]\n")
    elif source_state != "missing":
        cache = _sso(
            paths,
            expires=datetime.now(UTC) - timedelta(hours=1) if source_state == "expired" else None,
        )
        if source_state == "unreadable":
            cache.write_bytes(b"\xffsynthetic-secret")
    with paths.aws_config_file.open("a") as file:
        file.write("[profile role]\nrole_arn = private\nsource_profile = dev\n")
    check = _auth(collect_local_diagnostics(paths))[-1]
    expected = {
        "static": "unverified",
        "sso": "unverified",
        "missing": "missing_credentials",
        "expired": "expired_sso",
        "unreadable": "unreadable_sso",
    }[source_state]
    assert check.result == expected
    assert check.actionable == (source_state not in {"static", "sso"})


@pytest.mark.parametrize("modern", [False, True])
@pytest.mark.parametrize("cache_state", ["healthy", "expired", "missing", "unreadable", "extreme"])
def test_shared_credentials_sso_uses_resolved_cache_freshness(paths, modern, cache_state):
    cache = _sso(paths, modern=modern)
    paths.aws_credentials_file.write_text(
        paths.aws_config_file.read_text().replace("[profile dev]", "[dev]"), encoding="utf-8"
    )
    paths.aws_config_file.unlink()
    if cache_state == "missing":
        cache.unlink()
    elif cache_state == "unreadable":
        cache.write_bytes(b"\xffsynthetic-secret")
    elif cache_state in {"expired", "extreme"}:
        payload = json.loads(cache.read_text())
        payload["expiresAt"] = (
            "0001-01-01T00:00:00Z"
            if cache_state == "extreme"
            else (datetime.now(UTC) - timedelta(hours=1)).isoformat()
        )
        cache.write_text(json.dumps(payload), encoding="utf-8")
    before = _snapshot(paths.config_file.parent)
    check = _auth(collect_local_diagnostics(paths))[0]
    assert (
        check.result
        == {
            "healthy": "ok",
            "expired": "expired_sso",
            "missing": "missing_credentials",
            "unreadable": "unreadable_sso",
            "extreme": "unreadable_sso",
        }[cache_state]
    )
    assert _snapshot(paths.config_file.parent) == before


def test_shared_credentials_override_cannot_probe_unrelated_sso_cache(paths):
    _sso(paths)
    url = "https://other.example.invalid/start"
    paths.aws_credentials_file.write_text(
        f"[dev]\nsso_session = replacement\nsso_start_url = {url}\n", encoding="utf-8"
    )
    cache = paths.sso_cache_dir / (hashlib.sha1(b"replacement").hexdigest() + ".json")
    cache.write_text(
        json.dumps(
            {
                "accessToken": "synthetic-replacement-token",
                "expiresAt": (datetime.now(UTC) - timedelta(hours=1)).isoformat(),
            }
        ),
        encoding="utf-8",
    )
    check = _auth(collect_local_diagnostics(paths))[0]
    assert check.result == "expired_sso"


def test_extreme_sso_expiration_returns_safe_actionable_report(paths):
    _sso(
        paths,
        payload={"accessToken": "synthetic-extreme-secret", "expiresAt": "0001-01-01T00:00:00Z"},
    )
    report = collect_local_diagnostics(paths)
    check = _auth(report)[0]
    assert check.result == "unreadable_sso"
    assert check.actionable
    assert report.exit_code == 1
    assert "synthetic-extreme-secret" not in report.render_json() + report.render_text()


@pytest.mark.parametrize("modern", [False, True])
@pytest.mark.parametrize("unused_state", ["missing", "expired", "unreadable"])
def test_role_ignores_unused_parent_sso_cache(paths, monkeypatch, modern, unused_state):
    key = "unused-session" if modern else "https://example.invalid/unused-start"
    parent_sso = "sso_session = unused-session\n" if modern else f"sso_start_url = {key}\n"
    _aws(
        paths,
        "[profile role]\nrole_arn = arn:aws:iam::123456789012:role/synthetic\n"
        "source_profile = base\nsso_account_id = 123456789012\nsso_role_name = unused-role\n"
        + parent_sso
        + "[profile base]\n"
        + (
            "[sso-session unused-session]\nsso_start_url = https://example.invalid/unused-start\nsso_region = us-east-1\n"
            if modern
            else ""
        ),
    )
    _static_profile(paths, "base")
    cache = paths.sso_cache_dir / (hashlib.sha1(key.encode()).hexdigest() + ".json")
    if unused_state == "expired":
        cache.write_text(
            json.dumps(
                {
                    "accessToken": "unused-token",
                    "expiresAt": (datetime.now(UTC) - timedelta(hours=1)).isoformat(),
                }
            )
        )
    elif unused_state == "unreadable":
        cache.write_bytes(b"\xffunused-token")
    forbidden = Mock(side_effect=AssertionError("unexpected external action"))
    monkeypatch.setattr(botocore.session.Session, "create_client", forbidden)
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    session = botocore.session.Session(profile="role")
    session.set_config_variable("config_file", str(paths.aws_config_file))
    session.set_config_variable("credentials_file", str(paths.aws_credentials_file))
    session.set_config_variable("metadata_service_timeout", 5)
    session.set_config_variable("metadata_service_num_attempts", 1)
    sdk_resolver = session.get_component("credential_provider")
    try:
        assert session.get_credentials().method == "assume-role"
    finally:
        sdk_resolver.get_provider("container-role")._fetcher._session.close()
        sdk_resolver.get_provider("iam-role")._role_fetcher._session.close()
    # Collector itself must never use the credential resolver/provider.
    monkeypatch.setattr(botocore.session.Session, "get_credentials", forbidden)
    before = _snapshot(paths.config_file.parent)
    report = collect_local_diagnostics(paths)
    assert _auth(report)[0].result == "unverified"
    assert not _auth(report)[0].actionable
    assert report.exit_code == 0
    assert _snapshot(paths.config_file.parent) == before
    forbidden.assert_not_called()


@pytest.mark.parametrize(
    ("source_state", "expected"),
    [
        ("healthy", "unverified"),
        ("expired", "expired_sso"),
        ("missing", "missing_credentials"),
        ("unreadable", "unreadable_sso"),
    ],
)
def test_role_preserves_active_source_sso_when_source_role_fields_are_unused(
    paths, monkeypatch, source_state, expected
):
    cache = _sso(
        paths, expires=datetime.now(UTC) - timedelta(hours=1) if source_state == "expired" else None
    )
    if source_state == "missing":
        cache.unlink()
    elif source_state == "unreadable":
        cache.write_bytes(b"\xffsynthetic-token")
    _aws(
        paths,
        "[profile outer]\nrole_arn = arn:aws:iam::123456789012:role/outer\nsource_profile = base\n[profile base]\nrole_arn = arn:aws:iam::123456789012:role/unused\nsource_profile = unused-missing\nsso_session = synthetic-session\nsso_account_id = 123456789012\nsso_role_name = synthetic-role\n[sso-session synthetic-session]\nsso_start_url = https://example.invalid/start\nsso_region = us-east-1\n",
    )
    _static_profile(paths, "base")
    forbidden = Mock(side_effect=AssertionError("unexpected external action"))
    monkeypatch.setattr(botocore.session.Session, "create_client", forbidden)
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    session = botocore.session.Session(profile="outer")
    session.set_config_variable("config_file", str(paths.aws_config_file))
    session.set_config_variable("credentials_file", str(paths.aws_credentials_file))
    sdk_resolver = session.get_component("credential_provider")
    try:
        credentials = session.get_credentials()
        assert credentials.method == "assume-role"
        # SDK source-profile static fields select the profile provider chain,
        # where SSO precedes shared static keys and unused source role fields.
        assert credentials._refresh_using.__self__._source_credentials.method == "sso"
    finally:
        sdk_resolver.get_provider("container-role")._fetcher._session.close()
        sdk_resolver.get_provider("iam-role")._role_fetcher._session.close()
    monkeypatch.setattr(botocore.session.Session, "get_credentials", forbidden)
    before = _snapshot(paths.config_file.parent)
    check = _auth(collect_local_diagnostics(paths))[0]
    assert check.result == expected
    assert check.actionable == (source_state != "healthy")
    assert _snapshot(paths.config_file.parent) == before
    forbidden.assert_not_called()


@pytest.mark.parametrize("sso_source", [False, True])
def test_role_static_self_source_matches_sdk_without_false_cycle(paths, monkeypatch, sso_source):
    if sso_source:
        _sso(paths, expires=datetime.now(UTC) - timedelta(hours=1))
    _aws(
        paths,
        "[profile dev]\nrole_arn = arn:aws:iam::123456789012:role/synthetic\nsource_profile = dev\n"
        + (
            "sso_session = synthetic-session\nsso_account_id = 123456789012\nsso_role_name = synthetic-role\n[sso-session synthetic-session]\nsso_start_url = https://example.invalid/start\nsso_region = us-east-1\n"
            if sso_source
            else ""
        ),
    )
    _static_profile(paths)
    forbidden = Mock(side_effect=AssertionError("unexpected external action"))
    monkeypatch.setattr(botocore.session.Session, "create_client", forbidden)
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    session = botocore.session.Session(profile="dev")
    session.set_config_variable("config_file", str(paths.aws_config_file))
    session.set_config_variable("credentials_file", str(paths.aws_credentials_file))
    sdk_resolver = session.get_component("credential_provider")
    try:
        credentials = session.get_credentials()
        assert credentials.method == "assume-role"
        assert credentials._refresh_using.__self__._source_credentials.method == (
            "sso" if sso_source else "shared-credentials-file"
        )
    finally:
        sdk_resolver.get_provider("container-role")._fetcher._session.close()
        sdk_resolver.get_provider("iam-role")._role_fetcher._session.close()
    monkeypatch.setattr(botocore.session.Session, "get_credentials", forbidden)
    before = _snapshot(paths.config_file.parent)
    check = _auth(collect_local_diagnostics(paths))[0]
    assert check.result == ("expired_sso" if sso_source else "unverified")
    assert check.actionable == sso_source
    assert _snapshot(paths.config_file.parent) == before
    forbidden.assert_not_called()


@pytest.mark.parametrize("nested", [False, True])
@pytest.mark.parametrize("unused_state", ["healthy", "expired", "missing", "unreadable"])
def test_web_identity_ignores_unused_role_source_and_sso(paths, monkeypatch, nested, unused_state):
    import botocore.credentials

    cache = _sso(
        paths, expires=datetime.now(UTC) - timedelta(hours=1) if unused_state == "expired" else None
    )
    if unused_state == "missing":
        cache.unlink()
    elif unused_state == "unreadable":
        cache.write_bytes(b"\xffunused-token")
    body = (
        "[profile dev]\nrole_arn = arn:aws:iam::123456789012:role/web\nweb_identity_token_file = "
        + str(paths.config_file.parent / "unread-web-token")
        + "\nsource_profile = unused-missing\nsso_session = synthetic-session\nsso_account_id = 123456789012\nsso_role_name = unused-role\n"
    )
    if nested:
        body += "[profile outer]\nrole_arn = arn:aws:iam::123456789012:role/outer\nsource_profile = dev\n"
    body += "[sso-session synthetic-session]\nsso_start_url = https://example.invalid/start\nsso_region = us-east-1\n"
    _aws(paths, body)
    forbidden = Mock(
        side_effect=AssertionError("unexpected external action or unused prerequisite")
    )
    monkeypatch.setattr(botocore.session.Session, "create_client", forbidden)
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    session = botocore.session.Session(profile="outer" if nested else "dev")
    session.set_config_variable("config_file", str(paths.aws_config_file))
    session.set_config_variable("credentials_file", str(paths.aws_credentials_file))
    sdk_resolver = session.get_component("credential_provider")
    try:
        credentials = session.get_credentials()
        if nested:
            assert credentials.method == "assume-role"
            credentials = credentials._refresh_using.__self__._source_credentials
        assert credentials.method == "assume-role-with-web-identity"
    finally:
        sdk_resolver.get_provider("container-role")._fetcher._session.close()
        sdk_resolver.get_provider("iam-role")._role_fetcher._session.close()
    # Inspect SDK selection only above. The collector must not resolve/freeze
    # credentials, open sockets, execute processes or inspect unused SSO data.
    monkeypatch.setattr(botocore.session.Session, "get_credentials", forbidden)
    monkeypatch.setattr(botocore.credentials.CredentialResolver, "load_credentials", forbidden)
    monkeypatch.setattr(socket, "socket", forbidden)
    monkeypatch.setattr(doctor, "_sso_result", forbidden)
    before = _snapshot(paths.config_file.parent)
    report = collect_local_diagnostics(paths)
    assert all(check.result == "unverified" for check in _auth(report))
    assert not any(check.actionable for check in _auth(report))
    assert report.exit_code == 0
    assert _snapshot(paths.config_file.parent) == before
    forbidden.assert_not_called()
