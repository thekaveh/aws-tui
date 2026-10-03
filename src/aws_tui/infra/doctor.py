"""Immutable, privacy-filtered reports and fully offline local diagnostics.

Only local configuration and known credential fields are inspected. No SDK
credential resolver, keychain backend, client, process or directory creation is
needed to collect a report.
"""

from __future__ import annotations

import configparser
import hashlib
import json
import os
import platform
import re
import stat
import sys
import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from types import MappingProxyType
from urllib.parse import urlsplit, urlunsplit

from aws_tui.infra.aws_session import (
    _SKEW_BUFFER,
    AwsSession,
    TokenLoadError,
    TokenState,
    _parse_iso8601,
)
from aws_tui.infra.config_store import Config, ConfigError, ConfigStore
from aws_tui.infra.connection_resolver import (
    Connection,
    ConnectionResolver,
    _default_aws_config_path,
    _default_aws_credentials_path,
)
from aws_tui.infra.keymap_store import (
    InvalidKeybinding,
    KeybindingCollision,
    KeymapStore,
    UnknownAction,
)
from aws_tui.infra.paths import cache_home, config_home
from aws_tui.infra.redaction import redact_mapping, redact_text
from aws_tui.version import __version__

_CONTEXT_FIELDS = frozenset(
    {
        "path",
        "config",
        "cache",
        "log",
        "crash",
        "aws_config",
        "aws_credentials",
        "sso_cache",
        "version",
        "python",
        "platform",
        "source",
        "kind",
        "provider",
        "profile",
        "endpoint",
        "count",
        "origin",
    }
)
_ANSI = re.compile(r"\x1b(?:\[[0-?]*[ -/]*[@-~]|\][^\x07]*(?:\x07|\x1b\\))")
_SQL = re.compile(
    r"\b(?:select\s+.+\s+from|insert\s+into|update\s+.+\s+set|delete\s+from|drop\s+table|alter\s+table|create\s+table)\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class DoctorPaths:
    config_file: Path
    cache_dir: Path
    aws_config_file: Path
    aws_credentials_file: Path
    sso_cache_dir: Path


def doctor_paths() -> DoctorPaths:
    """Resolve current path semantics without creating any directories."""
    return DoctorPaths(
        config_home() / "config.toml",
        cache_home(),
        _default_aws_config_path(),
        _default_aws_credentials_path(),
        Path.home() / ".aws" / "sso" / "cache",
    )


def _display(value: str) -> str:
    # Remove complete terminal sequences before their remaining control bytes.
    value = _ANSI.sub("", value)
    value = "".join(" " if unicodedata.category(char).startswith("C") else char for char in value)
    if _SQL.search(value):
        return "[REDACTED]"
    return redact_text(value)


def _endpoint(value: str) -> str:
    try:
        parts = urlsplit(value)
        host = parts.hostname
        if parts.scheme not in {"http", "https"} or not host:
            return "[REDACTED]"
        if ":" in host:
            host = f"[{host}]"
        port = parts.port
        return urlunsplit((parts.scheme, f"{host}:{port}" if port else host, parts.path, "", ""))
    except ValueError:
        return "[REDACTED]"


@dataclass(frozen=True)
class DoctorCheck:
    name: str
    result: str
    context: Mapping[str, str]
    next_step: str
    actionable: bool = False

    def __post_init__(self) -> None:
        # A frozen dataclass alone does not protect a caller-owned dictionary.
        object.__setattr__(self, "context", MappingProxyType(dict(self.context)))

    def _public(self) -> dict[str, object]:
        projected = {
            key: _display(_endpoint(value) if key == "endpoint" else value)
            for key, value in self.context.items()
            if key in _CONTEXT_FIELDS
        }
        return {
            "name": _display(self.name),
            "result": self.result,
            "context": redact_mapping(projected),
            "next_step": _display(self.next_step),
            "actionable": self.actionable,
        }


@dataclass(frozen=True)
class DoctorReport:
    checks: tuple[DoctorCheck, ...]

    @property
    def exit_code(self) -> int:
        return int(any(check.actionable for check in self.checks))

    def render_json(self) -> str:
        return json.dumps(
            {"schema_version": 1, "checks": [check._public() for check in self.checks]},
            indent=2,
            ensure_ascii=True,
        )

    def render_text(self) -> str:
        lines: list[str] = []
        for check in self.checks:
            public = check._public()
            lines.append(f"{public['name']}: {public['result']}")
            context = public["context"]
            assert isinstance(context, dict)
            lines.extend(f"  {key}: {value}" for key, value in context.items())
            lines.append(f"  Next step: {public['next_step']}")
        return "\n".join(lines)


def _file_state(path: Path) -> str:
    try:
        mode = path.stat().st_mode
    except FileNotFoundError:
        return "missing"
    except OSError:
        return "unreadable"
    return "file" if stat.S_ISREG(mode) else "unreadable"


def _profile_metadata(paths: DoctorPaths) -> tuple[dict[str, dict[str, str]], set[str]]:
    profiles: dict[str, dict[str, str]] = {}
    invalid: set[str] = set()
    for path, source, is_config in (
        (paths.aws_config_file, "aws-config", True),
        (paths.aws_credentials_file, "aws-credentials", False),
    ):
        state = _file_state(path)
        if state == "missing":
            continue
        if state != "file":
            invalid.add(source)
            continue
        parser = configparser.RawConfigParser()
        try:
            with path.open(encoding="utf-8-sig") as file:
                parser.read_file(file)
        except (configparser.Error, OSError, UnicodeError):
            invalid.add(source)
            continue
        for section in parser.sections():
            if is_config:
                if section == "default":
                    name = section
                elif section.startswith("profile "):
                    name = section[len("profile ") :].strip()
                else:
                    continue
            else:
                name = section
            fields = (
                "sso_session",
                "sso_start_url",
                "role_arn",
                "source_profile",
                "credential_process",
                "web_identity_token_file",
                "credential_source",
                "aws_access_key_id",
                "aws_secret_access_key",
            )
            profiles.setdefault(name, {}).update(
                (key, value) for key, value in parser.items(section) if key in fields
            )
    return profiles, invalid


def _has_keys(access: str | None, secret: str | None) -> bool:
    return bool(access and access.strip() and secret and secret.strip())


def _auth_check(result: str, context: Mapping[str, str]) -> DoctorCheck:
    guidance = {
        "ok": "Local credentials are present; permissions remain unverified. Use a named probe to check access.",
        "missing_credentials": "Add credentials in Settings or configure the AWS profile; for SSO, run aws sso login for that profile.",
        "expired_sso": "Run aws sso login for the affected profile, then rerun doctor.",
        "unreadable_sso": "Repair the affected SSO cache or run aws sso login for that profile, then rerun doctor.",
        "invalid_config": "Repair the source_profile chain in the AWS config; remove cycles and reference an existing profile.",
        "unverified": "Offline checks cannot verify this credential provider. Use a named probe to check access.",
    }
    return DoctorCheck(
        "auth", result, context, guidance[result], result not in {"ok", "unverified"}
    )


def _sso_result(connection: Connection, profile: str, key: str, paths: DoctorPaths) -> str:
    cache_file = paths.sso_cache_dir / f"{hashlib.sha1(key.encode('utf-8')).hexdigest()}.json"
    state = _file_state(cache_file)
    if state == "missing":
        return "missing_credentials"
    if state != "file":
        return "unreadable_sso"
    try:
        payload = json.loads(cache_file.read_text(encoding="utf-8"))
        if not isinstance(payload, dict) or not isinstance(payload.get("expiresAt"), str):
            return "unreadable_sso"
        token = payload.get("accessToken")
        if token is None or (isinstance(token, str) and not token.strip()):
            return "missing_credentials"
        if not isinstance(token, str):
            return "unreadable_sso"
        session = AwsSession(
            sso_cache_dir=paths.sso_cache_dir, aws_config_path=paths.aws_config_file
        )
        # The app probe only resolves AWS config metadata. Shared credentials
        # can select a different cache (or supply the only SSO metadata), so
        # never accept the probe's non-SSO CONNECTED shortcut as freshness.
        if session._sso_cache_key_for_profile(profile) == cache_file.stem:
            probe = session.probe_token(replace(connection, profile=profile))
            if probe.state != TokenState.CONNECTED or probe.expires_at is not None:
                return {
                    TokenState.CONNECTED: "ok",
                    TokenState.EXPIRED: "expired_sso",
                    TokenState.MISSING: "missing_credentials",
                }[probe.state]
        # Confined fallback: the same pure parser/skew policy, applied only to
        # the known cache selected by resolved local metadata. No config writes
        # or credential providers are needed to align the two local inputs.
        expires_at = _parse_iso8601(payload["expiresAt"])
        return "expired_sso" if expires_at - _SKEW_BUFFER <= datetime.now(UTC) else "ok"
    except (
        TokenLoadError,
        OSError,
        UnicodeError,
        ValueError,
        TypeError,
        AttributeError,
        OverflowError,
    ):
        return "unreadable_sso"


def _aws_auth_result(
    connection: Connection,
    profiles: dict[str, dict[str, str]],
    invalid: set[str],
    paths: DoctorPaths,
) -> str:
    profile = connection.profile
    if profile is None:
        if _has_keys(os.environ.get("AWS_ACCESS_KEY_ID"), os.environ.get("AWS_SECRET_ACCESS_KEY")):
            return "ok"
        # A profile-less SDK session may use environment, container or metadata
        # providers. Their execution belongs exclusively to an explicit probe.
        profile = (
            os.environ.get("AWS_PROFILE") or os.environ.get("AWS_DEFAULT_PROFILE") or "default"
        )
        if profile not in profiles:
            return "unverified"
    visited: set[str] = set()
    derived_role = False
    while True:
        if profile in visited:
            return "invalid_config"
        visited.add(profile)
        metadata = profiles.get(profile)
        if metadata is None:
            return "unverified" if invalid else "missing_credentials"
        # The SDK tries assume-role before the profile's SSO provider. Parent
        # SSO metadata can be stale and unused when a role selects its source.
        # A nested source with any static key fields uses the SDK's profile
        # provider chain instead, where its SSO metadata can be active.
        has_static_fields = any(
            key in metadata for key in ("aws_access_key_id", "aws_secret_access_key")
        )
        follow_role = bool(metadata.get("role_arn")) and (not derived_role or not has_static_fields)
        derived_role = derived_role or follow_role
        if follow_role:
            source = metadata.get("source_profile")
            if source:
                # The SDK permits a top-level role to source its own static
                # fields; its profile provider chain then supplies the source.
                if source != profile or not has_static_fields:
                    profile = source
                    continue
            else:
                return "unverified"
        key = metadata.get("sso_session") or metadata.get("sso_start_url")
        if key:
            if "aws-config" in invalid:
                return "unverified"
            result = _sso_result(connection, profile, key, paths)
            return "unverified" if derived_role and result == "ok" else result
        if any(
            metadata.get(field)
            for field in ("credential_process", "web_identity_token_file", "credential_source")
        ):
            return "unverified"
        if _has_keys(metadata.get("aws_access_key_id"), metadata.get("aws_secret_access_key")):
            return "unverified" if derived_role else "ok"
        return "unverified" if invalid else "missing_credentials"


def collect_local_diagnostics(paths: DoctorPaths | None = None) -> DoctorReport:
    """Collect deterministic diagnostics using only read-only local inputs."""
    paths = paths if paths is not None else doctor_paths()
    checks = [
        DoctorCheck("version", "ok", {"version": __version__}, "No action needed."),
        DoctorCheck(
            "runtime",
            "ok",
            {"python": platform.python_version(), "platform": sys.platform},
            "No action needed.",
        ),
        DoctorCheck(
            "paths",
            "ok",
            {
                "config": str(paths.config_file),
                "cache": str(paths.cache_dir),
                "log": str(paths.cache_dir / "log" / "aws-tui.log"),
                "crash": str(paths.cache_dir / "crash"),
                "aws_config": str(paths.aws_config_file),
                "aws_credentials": str(paths.aws_credentials_file),
                "sso_cache": str(paths.sso_cache_dir),
            },
            "Paths are shown for reference; doctor does not create them.",
        ),
    ]
    store = ConfigStore(path=paths.config_file, read_only=True)
    config: Config | None = None
    config_state = _file_state(paths.config_file)
    try:
        if config_state == "unreadable":
            raise OSError
        config = store.load()
    except (ConfigError, OSError, UnicodeError, TypeError, ValueError):
        checks.append(
            DoctorCheck(
                "config",
                "invalid_config",
                {"path": str(paths.config_file)},
                "Repair config.toml syntax and connection fields, including required static credential keys, and ensure it is readable.",
                True,
            )
        )
    else:
        checks.append(
            DoctorCheck(
                "config",
                "missing_config" if config_state == "missing" else "ok",
                {"path": str(paths.config_file)},
                "Optional app config is absent; use Settings to add connections."
                if config_state == "missing"
                else "No action needed.",
            )
        )
        keymap_result = "ok"
        try:
            KeymapStore(overlay=config.keybindings.bindings)
        except InvalidKeybinding:
            keymap_result = "invalid_keybinding"
        except KeybindingCollision:
            keymap_result = "keybinding_collision"
        except UnknownAction:
            keymap_result = "unknown_action"
        checks.append(
            DoctorCheck(
                "keymap",
                keymap_result,
                {},
                "No action needed."
                if keymap_result == "ok"
                else "Repair [keybindings] in config.toml: use known action names and distinct, non-empty Textual key tokens.",
                keymap_result != "ok",
            )
        )

    profiles, invalid = _profile_metadata(paths)
    discovery = ConnectionResolver(
        config_store=store,
        aws_config_path=paths.aws_config_file,
        aws_credentials_path=paths.aws_credentials_file,
    ).discover()
    invalid.update(source for source in discovery.invalid_sources if source != "app-config")
    for source in ("aws-config", "aws-credentials"):
        if source in invalid:
            checks.append(
                DoctorCheck(
                    "discovery",
                    "invalid_config",
                    {"source": source},
                    "Repair the AWS INI file syntax and encoding, and ensure the file is readable.",
                    True,
                )
            )
    connections = discovery.connections
    checks.append(
        DoctorCheck(
            "sources",
            "ok" if connections else "missing_credentials",
            {"count": str(len(connections))},
            "Inspect local authentication checks; source discovery does not verify permissions."
            if connections
            else "Add a connection in Settings or add an AWS profile to the shared AWS config/credentials files.",
            not connections,
        )
    )
    for index, connection in enumerate(connections, 1):
        # Use an ordinal rather than arbitrary source/profile/region metadata:
        # those user-controlled strings may themselves contain tokens or SQL.
        context = {"source": str(index), "kind": connection.kind, "origin": connection.source}
        if connection.kind == "s3-compatible":
            entry = config.connections.get(connection.name) if config is not None else None
            spec = entry.credentials if entry is not None else None
            if spec and spec.startswith("keychain:"):
                result = "unverified"
                context["provider"] = "keychain"
            elif spec and spec.startswith("aws-profile:") and "aws-credentials" in invalid:
                result = "unverified"
            else:
                result = (
                    "ok"
                    if _has_keys(connection.access_key_id, connection.secret_access_key)
                    else "missing_credentials"
                )
        else:
            result = _aws_auth_result(connection, profiles, invalid, paths)
        checks.append(_auth_check(result, context))
    checks.append(
        DoctorCheck(
            "offline",
            "unverified",
            {},
            "Offline checks inspect local presence and SSO freshness only. Permissions and dynamic credential providers require a named probe.",
        )
    )
    checks.append(
        DoctorCheck(
            "probe",
            "skipped",
            {},
            "Run aws-tui doctor --probe NAME with the configured connection name or discovered AWS profile name to check access.",
        )
    )
    return DoctorReport(tuple(checks))


__all__ = [
    "DoctorCheck",
    "DoctorPaths",
    "DoctorReport",
    "collect_local_diagnostics",
    "doctor_paths",
]
