"""Named probes with real SDK providers and synthetic service/HTTP boundaries."""

from __future__ import annotations

import hashlib
import json
import os
import socket
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import botocore.session
import pytest
from botocore.exceptions import (
    ClientError,
    ConnectionClosedError,
    EndpointConnectionError,
    ReadTimeoutError,
)
from botocore.tokens import SSOTokenProvider
from botocore.utils import JSONFileCache

from aws_tui.infra.doctor import DoctorPaths, DoctorReport
from aws_tui.infra.doctor_probe import probe_source

SECRET = "secret-SQL-SELECT password FROM credentials"


@pytest.fixture
def paths(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    for key in tuple(os.environ):
        if key.startswith("AWS_") or key in {"BOTO_CONFIG", "BOTO_PATH"}:
            monkeypatch.delenv(key)
    monkeypatch.setenv("AWS_EC2_METADATA_DISABLED", "true")
    monkeypatch.setenv("BOTO_CONFIG", str(tmp_path / "absent-boto-config"))
    result = DoctorPaths(
        tmp_path / "config.toml",
        tmp_path / "cache",
        tmp_path / "aws-config",
        tmp_path / "aws-credentials",
        tmp_path / "sso",
    )
    result.sso_cache_dir.mkdir()

    def forbidden(*args, **kwargs):
        pytest.fail("probe crossed an unconfigured external boundary")

    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    monkeypatch.setattr(SSOTokenProvider, "_attempt_create_token", forbidden)
    monkeypatch.setattr(JSONFileCache, "__setitem__", forbidden)
    return result


def aws(paths, body="[profile chosen]\n"):
    paths.aws_config_file.write_text(body, encoding="utf-8")


def static(paths, profile="chosen"):
    paths.aws_credentials_file.write_text(
        f"[{profile}]\naws_access_key_id = synthetic-access\n"
        "aws_secret_access_key = synthetic-secret\naws_session_token = synthetic-session\n",
        encoding="utf-8",
    )


def s3(paths, credentials="static", extras=""):
    keys = (
        (
            'access_key_id = "synthetic-access"\nsecret_access_key = "synthetic-secret"\n'
            'session_token = "synthetic-session"\n'
        )
        if credentials == "static"
        else ""
    )
    paths.config_file.write_text(
        '[connections.chosen]\nkind = "s3-compatible"\n'
        'endpoint_url = "https://example.invalid:9000"\n'
        f'credentials = "{credentials}"\n{keys}{extras}',
        encoding="utf-8",
    )


class FakeClient:
    exceptions = SimpleNamespace(
        UnauthorizedException=type("UnauthorizedException", (Exception,), {})
    )

    def __init__(self, factory, session, service, kwargs):
        self.factory, self.session, self.service, self.kwargs = factory, session, service, kwargs
        self.closed = False

    def close(self):
        self.closed = True

    def perform(self, operation):
        self.factory.calls.append((self.service, operation))
        if operation == "get_caller_identity":
            credentials = self.session.get_credentials()
            assert credentials is not None
            self.factory.frozen = credentials.get_frozen_credentials()
        condition = self.factory.condition
        if condition == "denied":
            raise ClientError({"Error": {"Code": "AccessDenied", "Message": SECRET}}, operation)
        if condition == "unreachable":
            raise EndpointConnectionError(endpoint_url=SECRET)
        if condition == "closed":
            raise ConnectionClosedError(endpoint_url=SECRET)
        if condition == "timeout":
            raise ReadTimeoutError(endpoint_url=SECRET)
        if condition == "unexpected":
            raise ValueError(SECRET)
        return {"Account": SECRET, "Arn": SECRET, "Buckets": [{"Name": SECRET}]}

    def get_caller_identity(self):
        return self.perform("get_caller_identity")

    def list_buckets(self):
        return self.perform("list_buckets")

    def get_role_credentials(self, **kwargs):
        self.factory.auth_requests.append(kwargs)
        self.perform("get_role_credentials")
        return {
            "roleCredentials": {
                "accessKeyId": "role-access",
                "secretAccessKey": "role-secret",
                "sessionToken": "role-session",
                "expiration": int((datetime.now(UTC) + timedelta(hours=1)).timestamp() * 1000),
            }
        }

    def assume_role(self, **kwargs):
        self.factory.auth_requests.append(kwargs)
        self.perform("assume_role")
        return {
            "Credentials": {
                "AccessKeyId": "assumed-access",
                "SecretAccessKey": "assumed-secret",
                "SessionToken": "assumed-session",
                "Expiration": datetime.now(UTC) + timedelta(hours=1),
            }
        }

    def assume_role_with_web_identity(self, **kwargs):
        self.factory.auth_requests.append(kwargs)
        self.perform("assume_role_with_web_identity")
        return {
            "Credentials": {
                "AccessKeyId": "web-access",
                "SecretAccessKey": "web-secret",
                "SessionToken": "web-session",
                "Expiration": datetime.now(UTC) + timedelta(hours=1),
            }
        }


@pytest.fixture
def clients(paths, monkeypatch):
    factory = SimpleNamespace(
        condition="success", clients=[], calls=[], auth_requests=[], frozen=None
    )

    def create(session, service_name, **kwargs):
        assert service_name != "sso-oidc"
        assert session.get_config_variable("config_file") == str(paths.aws_config_file)
        assert session.get_config_variable("credentials_file") == str(paths.aws_credentials_file)
        config = kwargs["config"]
        assert config.connect_timeout == config.read_timeout == 5
        assert config.retries == {"total_max_attempts": 1, "mode": "standard"}
        client = FakeClient(factory, session, service_name, kwargs)
        factory.clients.append(client)
        return client

    monkeypatch.setattr(botocore.session.Session, "create_client", create)
    return factory


def inventory(root):
    def contents(path):
        try:
            return path.read_bytes() if path.is_file() else None
        except PermissionError:
            return "unreadable"

    return {
        str(path.relative_to(root)): (path.stat().st_mode, contents(path))
        for path in root.rglob("*")
    }


@pytest.mark.parametrize("kind", ["aws", "s3"])
@pytest.mark.parametrize(
    ("condition", "expected"),
    [
        ("success", "ok"),
        ("denied", "denied"),
        ("unreachable", "unreachable"),
        ("closed", "unreachable"),
        ("timeout", "timed_out"),
        ("unexpected", "unverified"),
    ],
)
def test_probe_classifies_and_closes(paths, clients, kind, condition, expected):
    if kind == "aws":
        aws(paths)
        static(paths)
    else:
        s3(paths)
    before = inventory(paths.config_file.parent)
    clients.condition = condition
    check = probe_source("chosen", paths)
    assert check.result == expected
    assert clients.calls == (
        [("sts", "get_caller_identity")] if kind == "aws" else [("s3", "list_buckets")]
    )
    assert clients.clients
    assert all(client.closed for client in clients.clients)
    assert inventory(paths.config_file.parent) == before
    report = DoctorReport((check,))
    assert SECRET not in report.render_json() + report.render_text()
    assert "chosen" not in report.render_json() + report.render_text()
    assert check.context == {
        "source": "1",
        "kind": "aws" if kind == "aws" else "s3-compatible",
        "origin": "auto-aws-profile" if kind == "aws" else "config",
    }


def test_unknown_does_not_fallback_or_access_keychain(paths, clients, monkeypatch):
    aws(paths)
    static(paths)
    monkeypatch.setattr("aws_tui.infra.keychain.Keyring.get", lambda *a: pytest.fail("keychain"))
    check = probe_source(SECRET, paths)
    assert check.result == "unknown_source"
    assert check.actionable
    assert not clients.clients
    assert SECRET not in DoctorReport((check,)).render_json()


def test_s3_preserves_options_and_only_selected_keychain(paths, clients, monkeypatch):
    s3(paths, "keychain:chosen-service", "verify_tls = false\nforce_path_style = true\n")
    with paths.config_file.open("a") as file:
        file.write(
            '[connections.other]\nkind = "s3-compatible"\nendpoint_url = "https://other.invalid"\ncredentials = "keychain:other-service"\n'
        )
    reads = []

    def get(self, service, key):
        reads.append((service, key))
        return "synthetic-secret"

    monkeypatch.setattr("aws_tui.infra.keychain.Keyring.get", get)
    assert probe_source("chosen", paths).result == "ok"
    assert {service for service, _ in reads} == {"chosen-service"}
    assert {key for _, key in reads} == {"access_key_id", "secret_access_key", "session_token"}
    kwargs = clients.clients[0].kwargs
    assert kwargs["verify"] is False
    assert kwargs["endpoint_url"] == "https://example.invalid:9000"
    assert kwargs["config"].s3 == {"addressing_style": "path"}


@pytest.mark.parametrize("nested", [False, True])
@pytest.mark.parametrize("mechanism", ["process", "mfa"])
def test_unsafe_credentials_are_skipped_before_service_call(paths, clients, nested, mechanism):
    body = "[profile chosen]\n"
    if nested:
        body += "role_arn = arn:aws:iam::123456789012:role/outer\nsource_profile = inner\n[profile inner]\n"
    if mechanism == "process":
        body += "credential_process = forbidden-synthetic-command\n"
    else:
        body += "role_arn = arn:aws:iam::123456789012:role/inner\nsource_profile = base\nmfa_serial = synthetic-mfa\n[profile base]\n"
        static(paths, "base")
    aws(paths, body)
    check = probe_source("chosen", paths)
    assert check.result == "skipped"
    assert not check.actionable
    assert not clients.clients
    assert mechanism in check.next_step.lower()


@pytest.mark.parametrize("nested", [False, True])
def test_shared_static_keys_precede_unused_process(paths, clients, nested):
    body = "[profile chosen]\n"
    if nested:
        body += "role_arn = arn:aws:iam::123456789012:role/outer\nsource_profile = base\n[profile base]\n"
    body += "credential_process = forbidden-synthetic-command\n"
    aws(paths, body)
    static(paths, "base" if nested else "chosen")
    assert probe_source("chosen", paths).result == "ok"
    assert clients.frozen.access_key == ("assumed-access" if nested else "synthetic-access")
    assert all(client.closed for client in clients.clients)


def sso(paths, modern=True, nested=False):
    profile = "base" if nested else "chosen"
    body = (
        "[profile chosen]\nrole_arn = arn:aws:iam::123456789012:role/outer\nsource_profile = base\n"
        if nested
        else ""
    )
    body += f"[profile {profile}]\nsso_account_id = 123456789012\nsso_role_name = synthetic-role\n"
    key = "synthetic-session" if modern else "https://example.invalid/start"
    if modern:
        body += "sso_session = synthetic-session\n[sso-session synthetic-session]\n"
    body += "sso_start_url = https://example.invalid/start\nsso_region = us-east-1\n"
    aws(paths, body)
    cache = paths.sso_cache_dir / (hashlib.sha1(key.encode()).hexdigest() + ".json")
    payload = {
        "accessToken": "synthetic-access-token",
        "expiresAt": (datetime.now(UTC) + timedelta(minutes=10)).isoformat(),
        "refreshToken": "synthetic-refresh-token",
        "clientId": "synthetic-client-id",
        "clientSecret": "synthetic-client-secret",
        "registrationExpiresAt": (datetime.now(UTC) + timedelta(days=1)).isoformat(),
    }
    cache.write_text(json.dumps(payload), encoding="utf-8")
    cache.chmod(0o600)
    return cache, payload


@pytest.mark.parametrize("modern", [False, True])
@pytest.mark.parametrize("nested", [False, True])
def test_real_sdk_sso_near_refresh_has_no_oidc_save_or_file_changes(paths, clients, modern, nested):
    sso(paths, modern, nested)
    before = inventory(paths.config_file.parent)
    assert probe_source("chosen", paths).result == "ok"
    assert ("sso", "get_role_credentials") in clients.calls
    assert (("sts", "assume_role") in clients.calls) == nested
    assert clients.auth_requests[0]["accessToken"] == "synthetic-access-token"
    assert all(client.closed for client in clients.clients)
    assert inventory(paths.config_file.parent) == before


@pytest.mark.parametrize("modern", [False, True])
@pytest.mark.parametrize(
    ("condition", "expected"),
    [
        ("missing", "missing_credentials"),
        ("expired", "expired_sso"),
        ("corrupt", "unreadable_sso"),
        ("shape", "unreadable_sso"),
        ("access", "missing_credentials"),
        ("permissions", "unreadable_sso"),
    ],
)
def test_sso_prerequisites_preserve_diagnostics(paths, clients, modern, condition, expected):
    cache, payload = sso(paths, modern, nested=True)
    if condition == "missing":
        cache.unlink()
    elif condition == "expired":
        payload["expiresAt"] = (datetime.now(UTC) - timedelta(hours=1)).isoformat()
        cache.write_text(json.dumps(payload))
    elif condition == "corrupt":
        cache.write_bytes(b"\xff")
    elif condition == "shape":
        cache.write_text("[]")
    elif condition == "permissions":
        cache.chmod(0)
    else:
        payload.pop("accessToken")
        cache.write_text(json.dumps(payload))
    before = inventory(paths.config_file.parent)
    check = probe_source("chosen", paths)
    assert check.result == expected
    assert check.actionable
    assert not clients.clients
    assert inventory(paths.config_file.parent) == before


@pytest.mark.parametrize("condition", ["denied", "timeout", "unreachable", "unexpected"])
def test_credential_provider_errors_close_all_clients(paths, clients, condition):
    sso(paths, nested=True)
    clients.condition = condition
    assert probe_source("chosen", paths).result in {
        "denied",
        "timed_out",
        "unreachable",
        "unverified",
    }
    assert clients.clients
    assert all(client.closed for client in clients.clients)


def test_web_identity_is_supported_with_memory_cache(paths, clients):
    token = paths.config_file.parent / "web-token"
    token.write_text("synthetic-web-token")
    aws(
        paths,
        "[profile chosen]\nrole_arn = arn:aws:iam::123456789012:role/web\nweb_identity_token_file = "
        + str(token)
        + "\n",
    )
    before = inventory(paths.config_file.parent)
    assert probe_source("chosen", paths).result == "ok"
    assert ("sts", "assume_role_with_web_identity") in clients.calls
    assert all(client.closed for client in clients.clients)
    assert inventory(paths.config_file.parent) == before


def test_missing_s3_credentials_never_creates_client(paths, clients):
    s3(paths, "env:ABSENT_SYNTH_")
    assert probe_source("chosen", paths).result == "missing_credentials"
    assert not clients.clients


@pytest.mark.parametrize("provider", ["container", "metadata"])
@pytest.mark.parametrize("nested", [False, True])
def test_sdk_credential_source_http_is_bounded_and_closed(
    paths, clients, monkeypatch, provider, nested
):
    from botocore.httpsession import URLLib3Session

    if nested:
        aws(
            paths,
            "[profile chosen]\nrole_arn = arn:aws:iam::123456789012:role/outer\ncredential_source = "
            + ("EcsContainer" if provider == "container" else "Ec2InstanceMetadata")
            + "\n",
        )
    else:
        aws(paths)
    if provider == "container":
        monkeypatch.setenv(
            "AWS_CONTAINER_CREDENTIALS_FULL_URI", "http://localhost:8765/credentials"
        )
    else:
        monkeypatch.setenv("AWS_EC2_METADATA_DISABLED", "false")
        monkeypatch.setenv("AWS_EC2_METADATA_SERVICE_ENDPOINT_MODE", "IPv6")
    requests, closed = [], []

    def send(self, request):
        requests.append((self, request))
        if provider == "metadata":
            assert request.url.startswith("http://[fd00:ec2::254]/")
        pool = self._manager.connection_pool_kw
        timeout = pool["timeout"]
        assert timeout == 5 or timeout.connect_timeout == timeout.read_timeout == 5
        if request.method == "PUT":
            content = b"synthetic-imds-token"
        elif request.url.endswith("/security-credentials/"):
            content = b"synthetic-role"
        else:
            content = json.dumps(
                {
                    "AccessKeyId": "http-access",
                    "SecretAccessKey": "http-secret",
                    "Token": "http-token",
                    "Expiration": (datetime.now(UTC) + timedelta(hours=1)).strftime(
                        "%Y-%m-%dT%H:%M:%SZ"
                    ),
                    "Code": "Success",
                }
            ).encode()
        return SimpleNamespace(status_code=200, content=content, text=content.decode())

    monkeypatch.setattr(URLLib3Session, "send", send)
    monkeypatch.setattr(URLLib3Session, "close", lambda self: closed.append(self))
    before = inventory(paths.config_file.parent)
    assert probe_source("chosen", paths).result == "ok"
    assert len(requests) == (1 if provider == "container" else 3)
    assert all(session in closed for session, _ in requests)
    assert all(client.closed for client in clients.clients)
    assert inventory(paths.config_file.parent) == before


@pytest.mark.parametrize("provider", ["container", "metadata"])
@pytest.mark.parametrize("nested", [False, True])
@pytest.mark.parametrize(
    ("condition", "expected"),
    [("timeout", "timed_out"), ("unreachable", "unreachable"), ("closed", "unreachable")],
)
def test_credential_source_transport_error_is_not_false_missing(
    paths, clients, monkeypatch, provider, nested, condition, expected
):
    from botocore.httpsession import URLLib3Session

    if nested:
        aws(
            paths,
            "[profile chosen]\nrole_arn = arn:aws:iam::123456789012:role/outer\ncredential_source = "
            + ("EcsContainer" if provider == "container" else "Ec2InstanceMetadata")
            + "\n",
        )
    else:
        aws(paths)
    if provider == "container":
        monkeypatch.setenv(
            "AWS_CONTAINER_CREDENTIALS_FULL_URI", "http://localhost:8765/credentials"
        )
    else:
        monkeypatch.setenv("AWS_EC2_METADATA_DISABLED", "false")
    attempts, closed = [], []

    def send(self, request):
        attempts.append(self)
        if condition == "closed":
            raise ConnectionClosedError(endpoint_url=SECRET)
        if condition == "timeout":
            raise ReadTimeoutError(endpoint_url=SECRET)
        raise EndpointConnectionError(endpoint_url=SECRET)

    monkeypatch.setattr(URLLib3Session, "send", send)
    monkeypatch.setattr(URLLib3Session, "close", lambda self: closed.append(self))
    check = probe_source("chosen", paths)
    assert check.result == expected
    assert len(attempts) == 1
    assert all(session in closed for session in attempts)
    assert not clients.clients


def test_main_client_creation_failure_closes_auth_clients(paths, clients, monkeypatch):
    sso(paths)
    create = botocore.session.Session.create_client

    def fail_main(session, service_name, **kwargs):
        if service_name == "sts":
            raise ValueError(SECRET)
        return create(session, service_name, **kwargs)

    monkeypatch.setattr(botocore.session.Session, "create_client", fail_main)
    assert probe_source("chosen", paths).result == "unverified"
    assert clients.calls == [("sso", "get_role_credentials")]
    assert all(client.closed for client in clients.clients)


def test_config_static_keys_do_not_override_process_precedence(paths, clients):
    aws(
        paths,
        "[profile chosen]\ncredential_process = forbidden-synthetic-command\naws_access_key_id = synthetic-access\naws_secret_access_key = synthetic-secret\n",
    )
    assert probe_source("chosen", paths).result == "skipped"
    assert not clients.clients


def test_explicit_profile_does_not_use_unrelated_global_env_keys(paths, clients, monkeypatch):
    aws(paths)
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "unrelated-access")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "unrelated-secret")
    assert probe_source("chosen", paths).result == "missing_credentials"
    assert not clients.clients


def test_unexpected_probe_failure_is_actionable_with_safe_guidance(paths, clients):
    aws(paths)
    static(paths)
    clients.condition = "unexpected"
    check = probe_source("chosen", paths)
    assert check.result == "unverified"
    assert check.actionable
    assert SECRET not in DoctorReport((check,)).render_text()


def test_sdk_environment_credential_source_is_supported(paths, clients, monkeypatch):
    aws(
        paths,
        "[profile chosen]\nrole_arn = arn:aws:iam::123456789012:role/outer\ncredential_source = Environment\n",
    )
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "source-env-access")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "source-env-secret")
    assert probe_source("chosen", paths).result == "ok"
    assert ("sts", "assume_role") in clients.calls
    assert all(client.closed for client in clients.clients)
