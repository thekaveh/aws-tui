# 1. aws-tui M1 (Infrastructure) Implementation Plan

> **For agentic workers:** Compact-plan format. Each task lists files + acceptance criteria + key contract details. Implementers MUST follow TDD (write failing test → impl → green → commit) within each task using the design spec at `docs/superpowers/specs/2026-06-13-aws-tui-design.md` for behavioral detail. Use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Land the entire `src/aws_tui/infra/` layer: `ConfigStore`, `ConnectionResolver` (auto-discovery), `AwsSession` (SSO cache probe), `ThemeStore`, `KeymapStore`, `LogSink`. All async-friendly, all strict-mypy clean, all unit-tested.

**Architecture:** Six independent boundary-layer modules. No cross-imports between them; they all stand alone and get composed at app start by `RootVM` (M3). Each module has one clear responsibility, a small interface, and exhaustive unit tests against fake `tmp_path` filesystems / `monkeypatch`-stubbed env.

**Tech Stack:** Python 3.11+ stdlib (`tomllib`, `pathlib`, `logging`), `tomli-w` for writes, `boto3`/`botocore` (SSO cache probe), `keyring` (macOS Keychain), `aioboto3` (async sessions). No integration tier in M1 — moto/MinIO arrive in M2 with the domain providers.

---

## 1.1. Task 0: Carry-overs from M0 reviewer

**Files:**
- Modify: `.pre-commit-config.yaml`
- Modify: `pyproject.toml`

- [ ] **Switch pre-commit mypy to `repo: local`** so it runs `uv run mypy` (resolving deps from the venv) instead of `mirrors-mypy` with a hardcoded `additional_dependencies` list that drifts:

```yaml
  - repo: local
    hooks:
      - id: mypy
        name: mypy
        entry: uv run mypy
        language: system
        types_or: [python, pyi]
        require_serial: true
        files: ^src/
```

Drop the `repos: - repo: https://github.com/pre-commit/mirrors-mypy` block.

- [ ] **Wire `flake8-tidy-imports` per-folder layer rules.** In `pyproject.toml`, add at the bottom of the `[tool.ruff.lint]` section:

```toml
[tool.ruff.lint.flake8-tidy-imports.banned-api]
# placeholder; per-folder rules go in per-file-ignores below as the
# code that would violate them lands.

# Per-folder import bans (enforced via per-file-ignores + custom checks
# in M1+). Until ruff supports per-folder banned-modules natively,
# we layer these in via `tool.ruff.lint.per-file-ignores` and CI greps.
```

Also add a `scripts/check-layers.sh` that greps for the banned imports per folder and fails non-zero on violations; wire into ci.yml's `lint-type` job.

`scripts/check-layers.sh`:
```bash
#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."

fail=0
banned() {
  local folder=$1; shift
  local label=$1; shift
  for pat in "$@"; do
    matches=$(grep -RnE "^\s*(from|import)\s+${pat}(\\b|\\.)" "src/aws_tui/${folder}" 2>/dev/null || true)
    if [ -n "$matches" ]; then
      echo "::error::layer rule violation in ${folder}: must not import ${pat}"
      echo "$matches"
      fail=1
    fi
  done
}

banned vm        "ViewModel"     "textual" "boto3" "aioboto3" "botocore" "aws_tui\\.ui" "aws_tui\\.services"
banned domain    "Domain"        "textual" "aws_tui\\.vm" "aws_tui\\.ui" "aws_tui\\.services"
banned infra     "Infrastructure" "textual" "aws_tui\\.vm" "aws_tui\\.ui" "aws_tui\\.services" "aws_tui\\.domain"
banned ui        "View"          "boto3" "aioboto3" "botocore" "aws_tui\\.infra\\.aws_session" "aws_tui\\.infra\\.connection_resolver"
banned services  "Services"      "textual"

if [ "$fail" -ne 0 ]; then
  exit 1
fi
echo "layer rules clean"
```

Make executable, run locally to confirm passes, wire into CI.

- [ ] **Acceptance:** `uv run pre-commit run --all-files` and `./scripts/check-layers.sh` both clean. Commit message: `chore(m1-0): pre-commit mypy via uv + layer-rule grep script`.

---

## 1.2. Task 1: `infra/log_sink.py`

**Files:**
- Create: `src/aws_tui/infra/log_sink.py`
- Create: `tests/unit/infra/test_log_sink.py`

**Contract:**

```python
from pathlib import Path

class LogSink:
    """JSON-lines log writer with rotation. ~/.cache/aws-tui/log/aws-tui.log, 5MB × 5 files."""

    def __init__(self, *, base_dir: Path | None = None, max_bytes: int = 5_242_880, backup_count: int = 5) -> None: ...
    def info(self, event: str, **fields: object) -> None: ...
    def warning(self, event: str, **fields: object) -> None: ...
    def error(self, event: str, **fields: object) -> None: ...
    def debug(self, event: str, **fields: object) -> None: ...
    def flush(self) -> None: ...
    def close(self) -> None: ...
```

Each call writes one JSON line with fields: `ts` (ISO8601 with TZ), `level`, `event`, plus all kwargs. Uses Python's `logging.handlers.RotatingFileHandler` under the hood. `base_dir` default: `Path.home() / ".cache" / "aws-tui" / "log"`. Dir created if missing.

**Acceptance:**
- Logs to a tmp_path-rooted dir; assert valid JSON per line.
- After exceeding `max_bytes`, rotation happens; old file becomes `.log.1`.
- `close()` flushes the handler.
- Strict mypy clean.

---

## 1.3. Task 2: `infra/config_store.py`

**Files:**
- Create: `src/aws_tui/infra/config_store.py`
- Create: `tests/unit/infra/test_config_store.py`

**Contract:**

```python
from pathlib import Path
from typing import Any
from dataclasses import dataclass, field

@dataclass(frozen=True, slots=True)
class ConnectionEntry:
    name: str
    kind: str                              # "aws" | "s3-compatible"
    profile: str | None = None             # only for kind="aws"
    region: str | None = None
    endpoint_url: str | None = None        # only for kind="s3-compatible"
    credentials: str | None = None         # spec: keychain:* | env:* | aws-profile:* | static
    access_key_id: str | None = None       # only when credentials="static"
    secret_access_key: str | None = None   # only when credentials="static"
    force_path_style: bool = False
    verify_tls: bool = True

@dataclass(frozen=True, slots=True)
class Defaults:
    connection: str | None = None
    theme: str = "carbon"

@dataclass(frozen=True, slots=True)
class Keybindings:
    bindings: dict[str, str | list[str]] = field(default_factory=dict)

@dataclass(frozen=True, slots=True)
class Config:
    connections: dict[str, ConnectionEntry]
    defaults: Defaults
    keybindings: Keybindings

class ConfigStore:
    def __init__(self, *, path: Path | None = None) -> None: ...
    def load(self) -> Config: ...
    def save(self, config: Config) -> None: ...                   # atomic: tempfile + rename
    def add_connection(self, entry: ConnectionEntry) -> None: ...
    def remove_connection(self, name: str) -> None: ...
    def set_default_connection(self, name: str) -> None: ...
```

Path default: `Path.home() / ".config" / "aws-tui" / "config.toml"`. Uses stdlib `tomllib` (read) + `tomli_w` (write). Validates that `kind` is one of the two literals; raises `ConfigError` on schema violation with a clear message.

**Acceptance:**
- Roundtrip: write → read → equal.
- Missing file returns `Config(connections={}, defaults=Defaults(), keybindings=Keybindings())`.
- Invalid `kind` raises `ConfigError`.
- Atomic write: simulated mid-write crash leaves the original file intact (test by patching `os.rename` to raise).
- Strict mypy clean.

---

## 1.4. Task 3: `infra/keychain.py`

**Files:**
- Create: `src/aws_tui/infra/keychain.py`
- Create: `tests/unit/infra/test_keychain.py`

**Contract:**

```python
class KeychainBackend:
    def get(self, service: str, key: str) -> str | None: ...
    def set(self, service: str, key: str, value: str) -> None: ...
    def delete(self, service: str, key: str) -> None: ...

class Keyring(KeychainBackend):
    """Thin wrapper around the `keyring` library (macOS Keychain on darwin)."""
    ...

class InMemoryKeychain(KeychainBackend):
    """Test fake. Backed by a dict."""
    ...
```

**Acceptance:**
- `InMemoryKeychain` is a real working dict-backed impl (used by tests in later tasks).
- `Keyring` defers to `keyring.get_password` / `set_password` / `delete_password`.
- Strict mypy clean.

---

## 1.5. Task 4: `infra/connection_resolver.py`

**Files:**
- Create: `src/aws_tui/infra/connection_resolver.py`
- Create: `tests/unit/infra/test_connection_resolver.py`

**Depends on:** `ConfigStore`, `Keychain`.

**Contract:**

```python
from dataclasses import dataclass

@dataclass(frozen=True, slots=True)
class Connection:
    name: str
    kind: str                              # "aws" | "s3-compatible"
    region: str
    source: str                            # "config" | "auto-aws-profile"
    profile: str | None = None
    endpoint_url: str | None = None
    access_key_id: str | None = None
    secret_access_key: str | None = None
    force_path_style: bool = False
    verify_tls: bool = True

class ConnectionResolver:
    def __init__(
        self, *, config_store: ConfigStore, keychain: KeychainBackend | None = None,
        aws_config_path: Path | None = None, aws_credentials_path: Path | None = None,
    ) -> None: ...
    def list(self) -> list[Connection]: ...                       # union: explicit + auto-discovered
    def resolve(self, name: str) -> Connection: ...               # raises ConnectionNotFound
    def materialize(self, name: str) -> ConnectionEntry: ...      # promote auto → explicit
```

`list()` merges:
1. Explicit `[connections.*]` entries from `ConfigStore` (with `source="config"`)
2. AWS profiles parsed from `~/.aws/config` + `~/.aws/credentials` (with `source="auto-aws-profile"`)
Explicit wins on name collision.

For `kind="s3-compatible"` entries, dispatch `credentials` field:
- `keychain:<service>` → keychain.get(service, "access_key_id") + .get(service, "secret_access_key")
- `env:PREFIX_*` → os.environ
- `aws-profile:<name>` → read keys from `~/.aws/credentials` directly
- `static` → fields from config (with startup warning emitted via `LogSink`)

**Acceptance:**
- With empty config + 3 AWS profiles in tmp `~/.aws`, `list()` returns 3 connections, all `source="auto-aws-profile"`.
- With an explicit `[connections.aws-dev]` overriding a same-named profile, list returns 1 entry, `source="config"`.
- S3-compat with `credentials="env:R2_*"` resolves keys from monkeypatched env.
- Missing connection → `ConnectionNotFound`.
- Strict mypy clean.

---

## 1.6. Task 5: `infra/aws_session.py`

**Files:**
- Create: `src/aws_tui/infra/aws_session.py`
- Create: `tests/unit/infra/test_aws_session.py`

**Depends on:** `Connection` (from Task 4), `LogSink`.

**Contract:**

```python
from datetime import datetime, timezone
from enum import StrEnum

class TokenState(StrEnum):
    CONNECTED = "connected"
    EXPIRED = "expired"
    MISSING = "missing"

@dataclass(frozen=True, slots=True)
class TokenProbeResult:
    state: TokenState
    expires_at: datetime | None = None

class AwsSession:
    """boto3 + aioboto3 session factory. Owns aioboto3 client lifecycle.

    Does NOT initiate sign-in; that's a shell-out (`aws sso login --profile <name>`)
    orchestrated by the caller. AwsSession only OBSERVES token state via the cache.
    """

    def __init__(self, *, sso_cache_dir: Path | None = None) -> None: ...
    def probe_token(self, connection: Connection) -> TokenProbeResult: ...
    async def client(self, connection: Connection, service: str) -> AsyncContextManager[Any]: ...
    async def aclose_all_clients(self) -> None: ...

class AuthExpiredError(Exception): ...
class TokenLoadError(Exception): ...
```

`probe_token`:
- For `kind="s3-compatible"`: always returns `CONNECTED` if credentials resolve, else `MISSING`. (No SSO involved.)
- For `kind="aws"`: locate the SSO cache JSON via `botocore.tokens.SSOTokenLoader` (or replicate its filename derivation: SHA1 of `sso_session` or `sso_start_url`). Read `expiresAt`. Compare to `now(UTC)` with 60-second skew. Return appropriate `TokenState`.
- Default `sso_cache_dir = Path.home() / ".aws" / "sso" / "cache"`.

`client(connection, service)`:
- Returns an `aioboto3.Session().client(...)` async context manager.
- For `kind="aws"`: `Session(profile_name=connection.profile, region_name=connection.region)`.
- For `kind="s3-compatible"`: `Session(aws_access_key_id=..., aws_secret_access_key=..., region_name=...)` with `endpoint_url=connection.endpoint_url` and `Config(s3={"addressing_style": "path" if connection.force_path_style else "auto"}, retries={"max_attempts": 6, "mode": "adaptive"}, connect_timeout=10, read_timeout=60, user_agent_extra=f"aws-tui/{__version__}")`.
- Registers each opened client so `aclose_all_clients()` can await `__aexit__` on all of them on app shutdown.

**Acceptance:**
- `probe_token` against a fixture SSO cache JSON with `expiresAt` in the future → `CONNECTED`.
- Same with `expiresAt` in the past → `EXPIRED`.
- Cache file missing → `MISSING`.
- For an s3-compatible connection with all keys present → `CONNECTED`.
- For an s3-compatible connection missing keys → `MISSING`.
- `aclose_all_clients()` awaits every opened client.
- Strict mypy clean.
- **No actual AWS or network calls in tests** — `boto3.Session(...)` is fine to instantiate (it's lazy); client creation is mocked.

---

## 1.7. Task 6: `infra/theme_store.py`

**Files:**
- Create: `src/aws_tui/infra/theme_store.py`
- Create: `tests/unit/infra/test_theme_store.py`

**Contract:**

```python
class ThemeStore:
    """Provides the active `.tcss` content for Textual.

    Layers (in order, later wins):
      1. Built-in: `src/aws_tui/ui/themes/<name>.tcss` (package data)
      2. User-defined: `~/.config/aws-tui/themes/<name>.tcss`
      3. User overlay: `~/.config/aws-tui/theme.tcss` (applied on top of whichever is active)
    """

    BUILTIN_NAMES: ClassVar[tuple[str, ...]] = ("carbon", "voidline", "lattice", "amber")

    def __init__(self, *, user_themes_dir: Path | None = None, user_overlay: Path | None = None) -> None: ...
    def list_themes(self) -> list[str]: ...                            # built-ins + user
    def load(self, name: str) -> str: ...                              # returns concatenated .tcss
    def exists(self, name: str) -> bool: ...
```

Note: M5 ships the actual `.tcss` files. For M1, `ThemeStore.load` should work against EMPTY built-in files (we'll touch them as placeholders) so the API exists.

**Acceptance:**
- `list_themes()` returns at least the 4 built-ins.
- `load("carbon")` returns the (empty) built-in content + overlay if present.
- `load("nonexistent")` raises `ThemeNotFound`.
- User-defined theme in tmp dir is discoverable.
- Strict mypy clean.

Also: create empty placeholder files at `src/aws_tui/ui/themes/{carbon,voidline,lattice,amber}.tcss` (just headers like `/* carbon theme - filled in M5 */`).

---

## 1.8. Task 7: `infra/keymap_store.py`

**Files:**
- Create: `src/aws_tui/infra/keymap_store.py`
- Create: `tests/unit/infra/test_keymap_store.py`

**Contract:**

```python
@dataclass(frozen=True, slots=True)
class KeyBinding:
    action: str          # e.g. "pane.copy"
    keys: tuple[str, ...]  # e.g. ("c",) or ("ctrl+k", ":") for multiple

class KeymapStore:
    """Action ↔ key indirection layer.

    Defaults from spec §4.2. Overlay from `[keybindings]` in config.toml.
    Spec §4.2 keymap is the canonical source.
    """

    DEFAULT_BINDINGS: ClassVar[dict[str, tuple[str, ...]]] = {
        "app.quit": ("q", "ctrl+c"),
        "app.command_palette": (":", "ctrl+k"),
        "app.help": ("?",),
        "pane.move_up": ("up", "j"),
        "pane.move_down": ("down", "k"),
        "pane.descend": ("enter",),
        "pane.ascend": ("backspace", "left"),
        "pane.switch_focus": ("tab",),
        "pane.switch_focus_back": ("shift+tab",),
        "pane.quick_look": ("space",),
        "pane.filter": ("/",),
        "pane.fuzzy_find": ("ctrl+p",),
        "pane.enter_multiselect": ("v",),
        "pane.toggle_select": ("space",),       # multi-select mode
        "pane.select_all": ("a",),
        "pane.copy": ("c",),
        "pane.move": ("m",),
        "pane.delete": ("d",),
        "pane.new": ("n",),
        "pane.refresh": ("r",),
        "app.transfers_tray": ("t",),
        "auth.authenticate": ("a",),            # only active when AuthExpired toast present
        "modal.cancel": ("escape",),
    }

    def __init__(self, *, overlay: dict[str, str | list[str]] | None = None) -> None: ...
    def resolve(self, action: str) -> tuple[str, ...]: ...
    def all(self) -> dict[str, tuple[str, ...]]: ...
```

**Acceptance:**
- `resolve("app.quit")` returns `("q", "ctrl+c")` with no overlay.
- With overlay `{"app.quit": "ctrl+d"}`, `resolve("app.quit")` returns `("ctrl+d",)`.
- With overlay `{"pane.copy": ["c", "y"]}`, `resolve("pane.copy")` returns `("c", "y")`.
- Unknown action raises `UnknownAction`.
- Strict mypy clean.

---

## 1.9. Task 8: Final M1 integration sanity test

**Files:**
- Create: `tests/unit/infra/test_integration.py` (still "unit" because it uses tmp_path; no network)

Sanity test composing the layer: build a `ConfigStore` against tmp config, write 2 connections (1 aws, 1 s3-compat), use `ConnectionResolver` to resolve both, then sanity-construct `AwsSession` against each. No AWS calls — just verify construction + `probe_token` against a fake SSO cache.

**Acceptance:**
- All M1 components import cleanly together (no circular deps).
- Composition works for both connection kinds.
- `uv run pytest tests/unit/infra -v` reports all green.
- `./scripts/check-layers.sh` reports clean.

---

## 1.10. Task 9: Commit, push, verify CI

- [ ] One commit per Task 1-7 (atomic per-component), one for Task 0 (M0 carry-overs), one for Task 8 (integration sanity).
- [ ] Push.
- [ ] Watch CI green.
- [ ] Tag `v0.2.0` (skipping v0.1.0 because per spec §9.7 that's reserved for "M0 only"; v0.2 is "M0+M1" = scaffolding + infra layer ready). Update CHANGELOG.

**Acceptance:** all CI jobs green, README's Quickstart still works, layer-rule grep clean.
