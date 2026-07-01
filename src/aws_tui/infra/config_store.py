"""TOML-backed configuration store.

Reads and writes ``config.toml`` under the platform-specific aws-tui
config directory. Pure data layer: the store knows nothing about
credential resolution or AWS — it just shuttles :class:`Config` objects
in and out of the file system.

Writes are atomic via tempfile + :func:`os.replace`. A mid-write crash
leaves the original file untouched.
"""

from __future__ import annotations

import contextlib
import os
import tempfile
import tomllib
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final

import tomli_w

from aws_tui.infra.paths import config_home

#: The two supported connection kinds.
VALID_KINDS: Final[frozenset[str]] = frozenset({"aws", "s3-compatible"})


class ConfigError(Exception):
    """Raised when the on-disk config violates the schema or an operation
    references an unknown connection."""


@dataclass(frozen=True, slots=True, repr=False)
class ConnectionEntry:
    """A single ``[connections.<name>]`` block from the config file.

    The ``name`` field is the section name (the key in
    :attr:`Config.connections`); it is duplicated here for ergonomic
    iteration.
    """

    name: str
    kind: str
    profile: str | None = None
    region: str | None = None
    endpoint_url: str | None = None
    credentials: str | None = None
    access_key_id: str | None = None
    secret_access_key: str | None = None
    session_token: str | None = None
    force_path_style: bool = False
    verify_tls: bool = True

    def __repr__(self) -> str:
        masked_id = "***" if self.access_key_id else None
        masked_secret = "***" if self.secret_access_key else None
        masked_token = "***" if self.session_token else None
        return (
            f"ConnectionEntry(name={self.name!r}, kind={self.kind!r}, "
            f"profile={self.profile!r}, region={self.region!r}, "
            f"endpoint_url={self.endpoint_url!r}, credentials={self.credentials!r}, "
            f"access_key_id={masked_id!r}, secret_access_key={masked_secret!r}, "
            f"session_token={masked_token!r}, "
            f"force_path_style={self.force_path_style!r}, verify_tls={self.verify_tls!r})"
        )


@dataclass(frozen=True, slots=True)
class Defaults:
    """The ``[defaults]`` section."""

    connection: str | None = None
    theme: str = "carbon"


@dataclass(frozen=True, slots=True)
class Keybindings:
    """The ``[keybindings]`` section.

    Each value is either a single keystroke string (``"c"``) or a list of
    keystrokes (``["c", "y"]``). Resolution semantics are
    :class:`~aws_tui.infra.keymap_store.KeymapStore`'s problem.
    """

    bindings: dict[str, str | list[str]] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class Config:
    """Top-level on-disk configuration."""

    connections: dict[str, ConnectionEntry]
    defaults: Defaults
    keybindings: Keybindings


def _default_path() -> Path:
    return config_home() / "config.toml"


class ConfigStore:
    """Read/write API around the TOML config file.

    When constructed with ``read_only=True`` (demo mode), all write
    operations (:meth:`save`, :meth:`add_connection`,
    :meth:`update_connection`, :meth:`remove_connection`,
    :meth:`set_default_connection`) are silent no-ops.  Reads
    (:meth:`load`) still function normally.
    """

    def __init__(self, *, path: Path | None = None, read_only: bool = False) -> None:
        self._path: Path = path if path is not None else _default_path()
        self._read_only = read_only

    @property
    def path(self) -> Path:
        return self._path

    @property
    def read_only(self) -> bool:
        """True when the store is in demo/read-only mode."""
        return self._read_only

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    def load(self) -> Config:
        """Return the current on-disk config, or an empty one if absent."""
        if not self._path.is_file():
            return Config(
                connections={},
                defaults=Defaults(),
                keybindings=Keybindings(),
            )
        if not self._read_only:
            self._harden_existing_path()
        try:
            with self._path.open("rb") as fh:
                raw = tomllib.load(fh)
        except tomllib.TOMLDecodeError as exc:
            raise ConfigError(f"config.toml is not valid TOML: {exc}") from exc
        return self._parse(raw)

    def _harden_existing_path(self) -> None:
        """Best-effort owner-only permissions for manually-created configs."""
        with contextlib.suppress(OSError, NotImplementedError):
            self._path.parent.chmod(0o700)
        with contextlib.suppress(OSError, NotImplementedError):
            self._path.chmod(0o600)

    @staticmethod
    def _parse(raw: dict[str, Any]) -> Config:
        connections: dict[str, ConnectionEntry] = {}
        raw_conns = raw.get("connections", {})
        if not isinstance(raw_conns, dict):
            raise ConfigError("[connections] must be a table")
        for name, body in raw_conns.items():
            if not isinstance(body, dict):
                raise ConfigError(f"[connections.{name}] must be a table")
            kind = body.get("kind")
            if kind not in VALID_KINDS:
                raise ConfigError(
                    f"[connections.{name}] has invalid kind {kind!r}; "
                    f"expected one of {sorted(VALID_KINDS)}"
                )
            connections[name] = ConnectionEntry(
                name=name,
                kind=kind,
                profile=body.get("profile"),
                region=body.get("region"),
                endpoint_url=body.get("endpoint_url"),
                credentials=body.get("credentials"),
                access_key_id=body.get("access_key_id"),
                secret_access_key=body.get("secret_access_key"),
                session_token=body.get("session_token"),
                force_path_style=_bool_field(
                    body,
                    field="force_path_style",
                    default=False,
                    table=f"connections.{name}",
                ),
                verify_tls=_bool_field(
                    body,
                    field="verify_tls",
                    default=True,
                    table=f"connections.{name}",
                ),
            )

        raw_defaults = raw.get("defaults", {})
        if not isinstance(raw_defaults, dict):
            raise ConfigError("[defaults] must be a table")
        defaults = Defaults(
            connection=raw_defaults.get("connection"),
            theme=str(raw_defaults.get("theme", "carbon")),
        )

        raw_kb = raw.get("keybindings", {})
        if not isinstance(raw_kb, dict):
            raise ConfigError("[keybindings] must be a table")
        kb_bindings: dict[str, str | list[str]] = {}
        for action, keys in raw_kb.items():
            if isinstance(keys, str):
                kb_bindings[action] = keys
            elif isinstance(keys, list) and all(isinstance(k, str) for k in keys):
                kb_bindings[action] = list(keys)
            else:
                raise ConfigError(f"[keybindings].{action} must be a string or list of strings")
        keybindings = Keybindings(bindings=kb_bindings)

        return Config(
            connections=connections,
            defaults=defaults,
            keybindings=keybindings,
        )

    # ------------------------------------------------------------------
    # Write (atomic)
    # ------------------------------------------------------------------

    def save(self, config: Config) -> None:
        """Atomically write the given config to disk.

        Writes to a tempfile in the same directory as the target, then
        :func:`os.replace`-es it into place. If anything goes wrong the
        original file (if any) is left untouched.

        In read-only mode (``self.read_only is True``) this is a silent
        no-op so that demo mode cannot accidentally mutate the user's real
        ``config.toml``.
        """
        if self._read_only:
            return
        for entry in config.connections.values():
            if entry.kind not in VALID_KINDS:
                raise ConfigError(f"connection {entry.name!r} has invalid kind {entry.kind!r}")

        self._path.parent.mkdir(parents=True, exist_ok=True)
        # Defense-in-depth: the config.toml file itself is created with
        # mode 0o600 by tempfile.mkstemp, but the parent directory
        # inherits the user's umask (typically 0o755) which leaks the
        # directory listing to other local users on shared systems.
        # Credentials in the file are protected; tightening the parent
        # to 0o700 keeps the existence of the config private too. The
        # chmod is idempotent and best-effort — a failure (e.g.
        # filesystem doesn't support permission bits, like FAT) is
        # surfaced via logs upstream but is not fatal to the save.
        with contextlib.suppress(OSError, NotImplementedError):
            self._path.parent.chmod(0o700)
        payload = self._serialize(config)

        # Same-directory temp ensures os.replace is a true atomic rename.
        tmp_fd, tmp_name = tempfile.mkstemp(
            prefix=".config-",
            suffix=".toml.tmp",
            dir=str(self._path.parent),
        )
        tmp_path = Path(tmp_name)
        try:
            with os.fdopen(tmp_fd, "wb") as fh:
                tomli_w.dump(payload, fh)
            # Path.replace delegates to os.replace, so test patches on
            # `os.replace` still flow through here.
            tmp_path.replace(self._path)
        except BaseException:
            # Catching BaseException (rather than Exception) is
            # intentional: KeyboardInterrupt and SystemExit must also
            # trigger tempfile cleanup before we re-raise, otherwise a
            # Ctrl-C mid-write leaves a stray ``config.toml.XXXX`` on
            # disk. We always re-raise — this is cleanup-then-propagate,
            # not error swallowing.
            with contextlib.suppress(FileNotFoundError):
                tmp_path.unlink()
            raise

    @staticmethod
    def _serialize(config: Config) -> dict[str, Any]:
        out: dict[str, Any] = {}
        if config.connections:
            conns: dict[str, dict[str, Any]] = {}
            for name, entry in config.connections.items():
                body: dict[str, Any] = {"kind": entry.kind}
                # Only emit non-None / non-default fields to keep the file tidy.
                if entry.profile is not None:
                    body["profile"] = entry.profile
                if entry.region is not None:
                    body["region"] = entry.region
                if entry.endpoint_url is not None:
                    body["endpoint_url"] = entry.endpoint_url
                if entry.credentials is not None:
                    body["credentials"] = entry.credentials
                if entry.access_key_id is not None:
                    body["access_key_id"] = entry.access_key_id
                if entry.secret_access_key is not None:
                    body["secret_access_key"] = entry.secret_access_key
                if entry.session_token is not None:
                    body["session_token"] = entry.session_token
                if entry.force_path_style:
                    body["force_path_style"] = True
                if not entry.verify_tls:
                    body["verify_tls"] = False
                conns[name] = body
            out["connections"] = conns

        defaults_body: dict[str, Any] = {}
        if config.defaults.connection is not None:
            defaults_body["connection"] = config.defaults.connection
        if config.defaults.theme != "carbon":
            defaults_body["theme"] = config.defaults.theme
        # Always emit defaults table for clarity even if empty? We skip it
        # when empty so a round-trip on a brand-new file remains tidy.
        if defaults_body:
            out["defaults"] = defaults_body

        if config.keybindings.bindings:
            out["keybindings"] = dict(config.keybindings.bindings)

        return out

    # ------------------------------------------------------------------
    # Mutators
    # ------------------------------------------------------------------

    def _mutate(self, fn: Callable[[Config], Config]) -> None:
        """Load → transform → save in one atomic-looking helper.

        The four mutators below previously duplicated the same
        ``cfg = self.load(); self.save(Config(...))`` framing. Extracting
        the framing keeps the validation + transformation logic in each
        mutator while pruning the boilerplate.
        """
        self.save(fn(self.load()))

    def add_connection(self, entry: ConnectionEntry) -> None:
        """Insert or overwrite ``entry`` and persist."""
        if entry.kind not in VALID_KINDS:
            raise ConfigError(f"connection {entry.name!r} has invalid kind {entry.kind!r}")

        def _apply(cfg: Config) -> Config:
            return Config(
                connections={**cfg.connections, entry.name: entry},
                defaults=cfg.defaults,
                keybindings=cfg.keybindings,
            )

        self._mutate(_apply)

    def update_connection(self, name: str, entry: ConnectionEntry) -> None:
        """Atomic in-place update of an existing connection.

        Raises ``KeyError`` if no connection with that name exists.
        Raises ``ValueError`` if ``entry.name != name`` (renaming is
        not supported; the field is read-only on edit in the UI).
        """
        if entry.name != name:
            raise ValueError(
                f"connection cannot be renamed in place: old={name!r}, new={entry.name!r}"
            )
        if entry.kind not in VALID_KINDS:
            raise ConfigError(f"connection {entry.name!r} has invalid kind {entry.kind!r}")

        def _apply(cfg: Config) -> Config:
            if name not in cfg.connections:
                raise KeyError(name)
            return Config(
                connections={**cfg.connections, name: entry},
                defaults=cfg.defaults,
                keybindings=cfg.keybindings,
            )

        self._mutate(_apply)

    def remove_connection(self, name: str) -> None:
        """Atomic removal of a connection.

        Raises ``ConfigError`` if no connection with that name exists.
        If the removed connection was the default, ``defaults.connection``
        is cleared to ``None``.
        """

        def _apply(cfg: Config) -> Config:
            if name not in cfg.connections:
                raise ConfigError(f"unknown connection: {name!r}")
            new_conns = {k: v for k, v in cfg.connections.items() if k != name}
            new_defaults = (
                Defaults(connection=None, theme=cfg.defaults.theme)
                if cfg.defaults.connection == name
                else cfg.defaults
            )
            return Config(
                connections=new_conns,
                defaults=new_defaults,
                keybindings=cfg.keybindings,
            )

        self._mutate(_apply)

    def set_default_connection(self, name: str) -> None:
        """Mark ``name`` as the default connection. Must exist."""

        def _apply(cfg: Config) -> Config:
            if name not in cfg.connections:
                raise ConfigError(f"unknown connection: {name!r}")
            return Config(
                connections=cfg.connections,
                defaults=Defaults(connection=name, theme=cfg.defaults.theme),
                keybindings=cfg.keybindings,
            )

        self._mutate(_apply)


def _bool_field(body: dict[str, Any], *, field: str, default: bool, table: str) -> bool:
    value = body.get(field, default)
    if isinstance(value, bool):
        return value
    raise ConfigError(f"[{table}].{field} must be a boolean")


__all__ = [
    "VALID_KINDS",
    "Config",
    "ConfigError",
    "ConfigStore",
    "ConnectionEntry",
    "Defaults",
    "Keybindings",
]
