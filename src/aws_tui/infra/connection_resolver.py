"""Connection discovery + credential dispatch.

The resolver is the only place that knows how to merge explicit
``[connections.*]`` entries with auto-discovered AWS profiles (from
``~/.aws/{config,credentials}``) and how to dereference the
``credentials`` field of an ``s3-compatible`` entry against the four
supported sources (keychain, env, aws-profile, static).

The resolver returns :class:`Connection` instances — a flat, ready-to-use
view that downstream layers (``AwsSession``, the ConnectionPicker VM) can
consume without thinking about config files or secret stores.
"""

from __future__ import annotations

import builtins
import configparser
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from aws_tui.infra.config_store import ConfigStore, ConnectionEntry
from aws_tui.infra.keychain import KeychainBackend

SOURCE_CONFIG: Final[str] = "config"
SOURCE_AUTO: Final[str] = "auto-aws-profile"

_DEFAULT_REGION: Final[str] = "us-east-1"


class ConnectionNotFound(Exception):
    """Raised when ``resolve`` or ``materialize`` cannot find a connection."""


@dataclass(frozen=True, slots=True)
class Connection:
    """Fully-resolved connection ready for an :class:`AwsSession`."""

    name: str
    kind: str
    region: str
    source: str
    profile: str | None = None
    endpoint_url: str | None = None
    access_key_id: str | None = None
    secret_access_key: str | None = None
    force_path_style: bool = False
    verify_tls: bool = True


def _default_aws_config_path() -> Path:
    return Path.home() / ".aws" / "config"


def _default_aws_credentials_path() -> Path:
    return Path.home() / ".aws" / "credentials"


class ConnectionResolver:
    """Union explicit config + auto-discovered AWS profiles.

    Explicit entries win on name collision; auto entries are tagged
    ``source="auto-aws-profile"`` so the UI can label them ``(auto)``.
    """

    def __init__(
        self,
        *,
        config_store: ConfigStore,
        keychain: KeychainBackend | None = None,
        aws_config_path: Path | None = None,
        aws_credentials_path: Path | None = None,
    ) -> None:
        self._config_store: ConfigStore = config_store
        self._keychain: KeychainBackend | None = keychain
        self._aws_config_path: Path = (
            aws_config_path if aws_config_path is not None else _default_aws_config_path()
        )
        self._aws_credentials_path: Path = (
            aws_credentials_path
            if aws_credentials_path is not None
            else _default_aws_credentials_path()
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def list(self) -> builtins.list[Connection]:
        """Return the union of explicit and auto-discovered connections."""
        explicit = self._explicit_connections()
        explicit_names = {c.name for c in explicit}
        autos = [c for c in self._auto_connections() if c.name not in explicit_names]
        return [*explicit, *autos]

    def resolve(self, name: str) -> Connection:
        """Return the connection named ``name`` or raise :class:`ConnectionNotFound`."""
        for conn in self.list():
            if conn.name == name:
                return conn
        raise ConnectionNotFound(name)

    def materialize(self, name: str) -> ConnectionEntry:
        """Promote an auto-discovered connection to an explicit config entry.

        Idempotent on already-explicit entries (writes the same body back).
        """
        conn = self.resolve(name)
        entry = ConnectionEntry(
            name=conn.name,
            kind=conn.kind,
            profile=conn.profile,
            region=conn.region,
            endpoint_url=conn.endpoint_url,
            force_path_style=conn.force_path_style,
            verify_tls=conn.verify_tls,
        )
        self._config_store.add_connection(entry)
        return entry

    # ------------------------------------------------------------------
    # Explicit (from config.toml)
    # ------------------------------------------------------------------

    def _explicit_connections(self) -> builtins.list[Connection]:
        cfg = self._config_store.load()
        out: builtins.list[Connection] = []
        for entry in cfg.connections.values():
            if entry.kind == "aws":
                out.append(
                    Connection(
                        name=entry.name,
                        kind="aws",
                        region=entry.region
                        or self._profile_region(entry.profile)
                        or _DEFAULT_REGION,
                        source=SOURCE_CONFIG,
                        profile=entry.profile,
                    )
                )
            elif entry.kind == "s3-compatible":
                access_key_id, secret_access_key = self._dispatch_s3_credentials(entry)
                out.append(
                    Connection(
                        name=entry.name,
                        kind="s3-compatible",
                        region=entry.region or _DEFAULT_REGION,
                        source=SOURCE_CONFIG,
                        endpoint_url=entry.endpoint_url,
                        access_key_id=access_key_id,
                        secret_access_key=secret_access_key,
                        force_path_style=entry.force_path_style,
                        verify_tls=entry.verify_tls,
                    )
                )
        return out

    # ------------------------------------------------------------------
    # Auto-discovery (from ~/.aws/config + ~/.aws/credentials)
    # ------------------------------------------------------------------

    def _auto_connections(self) -> builtins.list[Connection]:
        profiles = self._discover_aws_profiles()
        return [
            Connection(
                name=name,
                kind="aws",
                region=region or _DEFAULT_REGION,
                source=SOURCE_AUTO,
                profile=name,
            )
            for name, region in profiles.items()
        ]

    def _discover_aws_profiles(self) -> dict[str, str | None]:
        """Return {profile_name: region_or_None} from AWS config + credentials.

        Honours the AWS CLI convention that profiles in ``~/.aws/config``
        are named ``[profile <name>]`` except for ``[default]``, while
        ``~/.aws/credentials`` uses plain ``[<name>]`` everywhere.
        """
        profiles: dict[str, str | None] = {}

        cfg_parser = configparser.ConfigParser()
        if self._aws_config_path.is_file():
            cfg_parser.read(self._aws_config_path)
            for section in cfg_parser.sections():
                if section == "default":
                    name = "default"
                elif section.startswith("profile "):
                    name = section[len("profile ") :].strip()
                else:
                    # Skip [sso-session ...] and other non-profile sections.
                    continue
                region = cfg_parser.get(section, "region", fallback=None)
                profiles[name] = region

        creds_parser = configparser.ConfigParser()
        if self._aws_credentials_path.is_file():
            creds_parser.read(self._aws_credentials_path)
            for section in creds_parser.sections():
                profiles.setdefault(section, None)

        return profiles

    def _profile_region(self, profile: str | None) -> str | None:
        if profile is None:
            return None
        return self._discover_aws_profiles().get(profile)

    # ------------------------------------------------------------------
    # s3-compatible credential dispatch
    # ------------------------------------------------------------------

    def _dispatch_s3_credentials(self, entry: ConnectionEntry) -> tuple[str | None, str | None]:
        spec = entry.credentials or ""
        if spec.startswith("keychain:"):
            service = spec[len("keychain:") :]
            if self._keychain is None:
                return None, None
            return (
                self._keychain.get(service, "access_key_id"),
                self._keychain.get(service, "secret_access_key"),
            )
        if spec.startswith("env:"):
            prefix = spec[len("env:") :]
            return (
                os.environ.get(f"{prefix}ACCESS_KEY_ID"),
                os.environ.get(f"{prefix}SECRET_ACCESS_KEY"),
            )
        if spec.startswith("aws-profile:"):
            profile = spec[len("aws-profile:") :]
            return self._read_aws_credentials_profile(profile)
        if spec == "static":
            return entry.access_key_id, entry.secret_access_key
        # Unknown / empty spec — let the caller deal with missing keys.
        return None, None

    def _read_aws_credentials_profile(self, profile: str) -> tuple[str | None, str | None]:
        if not self._aws_credentials_path.is_file():
            return None, None
        parser = configparser.ConfigParser()
        parser.read(self._aws_credentials_path)
        if not parser.has_section(profile):
            return None, None
        return (
            parser.get(profile, "aws_access_key_id", fallback=None),
            parser.get(profile, "aws_secret_access_key", fallback=None),
        )


__all__ = [
    "SOURCE_AUTO",
    "SOURCE_CONFIG",
    "Connection",
    "ConnectionNotFound",
    "ConnectionResolver",
]
