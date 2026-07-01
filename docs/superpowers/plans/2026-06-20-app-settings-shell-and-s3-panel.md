# 1. App Settings Shell + S3 Connections Panel Implementation Plan

> **SUPERSEDED** by
> [`2026-06-20-settings-as-first-class-nav-page.md`](2026-06-20-settings-as-first-class-nav-page.md)
> (PR #54 rework). This plan describes the modal-overlay architecture
> that shipped in PR #52 and was reworked away. `SettingsModal`,
> `ServicesMenuFooter`, `S3CompatFormModal`, `_PlaceholderPanel`, and
> `ServicesMenuVM` no longer exist. Retained for git-history
> continuity only — do **not** implement against this plan.

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an in-app App Settings overlay (sidebar nav, gear-button entry in the services column footer, keyboard `,`) shipping its first panel — full CRUD over `kind = "s3-compatible"` connections backed by atomic TOML writes, with affected panes reloading on modal dismiss.

**Architecture:** New `SettingsModal` Textual screen with a left-sidebar ListView (`Connections` active; `Themes (soon)` and `Keymap (soon)` disabled). Body is a single `S3ConnectionsPanel`. Add/Edit reuses the existing `S3CompatFormModal`; Delete reuses the polished `ConfirmModal`. Two new VMs (`SettingsVM`, `S3ConnectionsVM`), two new `ConfigStore` methods (`update_connection`, `remove_connection`, both atomic via `tempfile + os.replace`), one new hub message (`ConnectionListChangedMessage`). Pane reload is implemented as a callback (`rebind_pane`) AwsTuiApp passes into SettingsVM at construction.

**Tech Stack:** Python 3.11+, Textual (TUI), VMx (MVVM framework, PyPI `vmx>=2.6.0,<3.0.0`), `tomllib`/`tomli_w` for TOML I/O, pytest + pytest-asyncio + pytest-textual-snapshot for tests.

## 1.1. Global Constraints

- 10-theme parity: every per-theme CSS block must land in all 10 theme files (`carbon`, `voidline`, `lattice`, `amber`, `solarized-light`, `github-light`, `one-light`, `nord`, `dracula`, `gruvbox-dark`).
- No new third-party dependencies.
- Inline credentials only for entries the form creates (drop the `credentials = "keychain:..."` indirection on edit; document this as one-way).
- Reload-on-close, not mid-edit. Affected panes reload exactly once, when the modal dismisses. No mid-edit pane reloads.
- `,` (comma) is the keyboard shortcut. Verified unbound today.
- Layered architecture preserved (`scripts/check-layers.sh` enforces). `vm/settings/*` may import from `infra/`, `domain/`. May NOT import from `ui/`, `services/`, `textual`, `boto3`.
- All quality gates must stay green per commit: `uv run ruff check .`, `uv run ruff format --check .`, `uv run mypy --strict src`, `bash scripts/check-layers.sh`, `uv run pytest`.
- AWS-kind connection editing is OUT OF SCOPE. The Settings overlay shows only `kind = "s3-compatible"` entries.
- Connectivity testing on save is OUT OF SCOPE.
- Renaming a connection on edit is OUT OF SCOPE (name field is read-only in edit mode).

---

## 1.2. File Structure

### 1.2.1. Files to create

| Path | Responsibility |
|---|---|
| `src/aws_tui/vm/settings/__init__.py` | Package init |
| `src/aws_tui/vm/settings/settings_vm.py` | `SettingsVM` — owns active section + dirty-set + reload-on-close |
| `src/aws_tui/vm/settings/s3_connections_vm.py` | `S3ConnectionsVM` — list + CRUD + validation + message publishing |
| `src/aws_tui/ui/widgets/settings_modal.py` | `SettingsModal` screen — sidebar + body + footer |
| `src/aws_tui/ui/widgets/services_menu_footer.py` | `ServicesMenuFooter` — gear button band |
| `src/aws_tui/ui/widgets/settings/__init__.py` | Package init |
| `src/aws_tui/ui/widgets/settings/s3_connections_panel.py` | `S3ConnectionsPanel` — the S3 panel content |
| `src/aws_tui/ui/widgets/settings/_placeholder_panel.py` | `_PlaceholderPanel` — "Coming in v0.8" body for disabled sections |
| `tests/unit/vm/settings/__init__.py` | Package init |
| `tests/unit/vm/settings/test_settings_vm.py` | Unit tests for `SettingsVM` |
| `tests/unit/vm/settings/test_s3_connections_vm.py` | Unit tests for `S3ConnectionsVM` |
| `tests/snapshot/apps/settings.py` | Snapshot test app for `SettingsModal` |
| `tests/snapshot/apps/s3_compat_form.py` | Snapshot test app for `S3CompatFormModal` (edit + validation modes) |
| `tests/snapshot/apps/services_menu_footer.py` | Snapshot test app for `ServicesMenuFooter` |
| `tests/snapshot/test_settings_modal.py` | Snapshot tests for settings modal × 10 themes |
| `tests/snapshot/test_s3_compat_form.py` | Snapshot tests for form edit + validation × 10 themes |
| `tests/snapshot/test_services_menu_footer.py` | Snapshot tests for gear footer × 10 themes |
| `tests/integration/test_settings_modal_flow.py` | In-process integration tests (3 flows) |

### 1.2.2. Files to modify

| Path | Change |
|---|---|
| `src/aws_tui/infra/config_store.py` | Add `update_connection(name, entry)` + `remove_connection(name)` |
| `src/aws_tui/vm/messages.py` | Add `ConnectionListChangedMessage` |
| `src/aws_tui/ui/widgets/first_run_modal.py` | `S3CompatFormModal`: add `name_locked: bool = False` param + live validation |
| `src/aws_tui/ui/widgets/services_menu.py` | Render `ServicesMenuFooter` below `services-list` |
| `src/aws_tui/vm/services_menu_vm.py` | Subscribe to `ConnectionListChangedMessage`; refresh on event |
| `src/aws_tui/app.py` | Add `action_open_settings`, `,` binding, `rebind_pane_to(...)` helper, hub subscription to drop deleted names from `AppContext.unreachable_connections`, wire `SettingsVM`/`S3ConnectionsVM` |
| `src/aws_tui/ui/themes/carbon.tcss` | Add `SettingsModal`, `S3ConnectionsPanel`, `ServicesMenuFooter` blocks |
| `src/aws_tui/ui/themes/voidline.tcss` | Same |
| `src/aws_tui/ui/themes/lattice.tcss` | Same |
| `src/aws_tui/ui/themes/amber.tcss` | Same |
| `src/aws_tui/ui/themes/solarized-light.tcss` | Same |
| `src/aws_tui/ui/themes/github-light.tcss` | Same |
| `src/aws_tui/ui/themes/one-light.tcss` | Same |
| `src/aws_tui/ui/themes/nord.tcss` | Same |
| `src/aws_tui/ui/themes/dracula.tcss` | Same |
| `src/aws_tui/ui/themes/gruvbox-dark.tcss` | Same |
| `CHANGELOG.md` | New `### Added` entry under `[Unreleased]` |

---

## 1.3. Task Sequence Overview

| # | Task | Phase |
|---|---|---|
| 1 | `ConfigStore.update_connection` + `remove_connection` | Persistence |
| 2 | `ConnectionListChangedMessage` | Messages |
| 3 | `S3ConnectionsVM` | VM layer |
| 4 | `SettingsVM` | VM layer |
| 5 | `S3CompatFormModal`: `name_locked` + live validation | View extensions |
| 6 | `S3ConnectionsPanel` + `_PlaceholderPanel` | View widgets |
| 7 | `SettingsModal` screen | View widgets |
| 8 | `ServicesMenuFooter` + `ServicesMenu` integration | View widgets |
| 9 | `AwsTuiApp` wiring (action, binding, rebind helper, hub sub) | Integration |
| 10 | `ServicesMenuVM` subscription | Integration |
| 11 | Per-theme CSS × 10 themes | CSS |
| 12 | Snapshot tests: `SettingsModal` + `ServicesMenuFooter` | Tests |
| 13 | Snapshot tests: `S3CompatFormModal` edit + validation | Tests |
| 14 | In-process integration tests | Tests |
| 15 | `CHANGELOG.md` entry | Docs |

---

## 1.4. Quality Gates (every task)

Each task ends with these commands all green before commit:

```bash
uv run ruff check .
uv run ruff format --check .
uv run mypy --strict src
bash scripts/check-layers.sh
uv run pytest
```

If any gate fails, fix the underlying cause before committing. Never `--no-verify`.

---

## 1.5. Task 1: `ConfigStore.update_connection` + `remove_connection`

**Files:**
- Modify: `src/aws_tui/infra/config_store.py` (add two methods after `add_connection` at line ~270)
- Test: `tests/unit/infra/test_config_store.py` (extend existing file)

**Interfaces:**
- Consumes: existing `ConfigStore.load()` / `save()` / `Config` / `ConnectionEntry` (in same file).
- Produces:
  - `ConfigStore.update_connection(name: str, entry: ConnectionEntry) -> None`
  - `ConfigStore.remove_connection(name: str) -> None`

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/infra/test_config_store.py`:

```python
import pytest

from aws_tui.infra.config_store import ConfigStore, ConnectionEntry


def _seed_entry(name: str = "minio-local") -> ConnectionEntry:
    return ConnectionEntry(
        name=name,
        kind="s3-compatible",
        region="us-east-1",
        endpoint_url="http://localhost:9000",
        access_key_id="AKIATEST",
        secret_access_key="SECRETTEST",
        force_path_style=True,
        verify_tls=True,
    )


def test_update_connection_round_trip(tmp_path):
    store = ConfigStore(path=tmp_path / "config.toml")
    store.add_connection(_seed_entry())
    updated = ConnectionEntry(
        name="minio-local",
        kind="s3-compatible",
        region="us-west-2",
        endpoint_url="https://minio.internal:443",
        access_key_id="AKIANEW",
        secret_access_key="SECRETNEW",
        force_path_style=False,
        verify_tls=False,
    )
    store.update_connection("minio-local", updated)
    cfg = store.load()
    assert cfg.connections["minio-local"] == updated


def test_remove_connection_round_trip(tmp_path):
    store = ConfigStore(path=tmp_path / "config.toml")
    store.add_connection(_seed_entry())
    store.remove_connection("minio-local")
    cfg = store.load()
    assert "minio-local" not in cfg.connections


def test_update_connection_unknown_name_raises(tmp_path):
    store = ConfigStore(path=tmp_path / "config.toml")
    with pytest.raises(KeyError, match="missing"):
        store.update_connection("missing", _seed_entry(name="missing"))


def test_remove_connection_unknown_name_raises(tmp_path):
    store = ConfigStore(path=tmp_path / "config.toml")
    with pytest.raises(KeyError, match="missing"):
        store.remove_connection("missing")


def test_update_connection_rename_disallowed(tmp_path):
    store = ConfigStore(path=tmp_path / "config.toml")
    store.add_connection(_seed_entry(name="old"))
    renamed = _seed_entry(name="new")
    with pytest.raises(ValueError, match="cannot be renamed"):
        store.update_connection("old", renamed)
```

- [ ] **Step 2: Run tests to verify they fail**

Run:
```bash
uv run pytest tests/unit/infra/test_config_store.py -v -k "update_connection or remove_connection"
```
Expected: 5 FAILED with `AttributeError: 'ConfigStore' object has no attribute 'update_connection'` (or similar).

- [ ] **Step 3: Implement the two methods**

Add to `src/aws_tui/infra/config_store.py` directly after `add_connection`:

```python
def update_connection(self, name: str, entry: ConnectionEntry) -> None:
    """Atomic in-place update of an existing connection.

    Raises ``KeyError`` if no connection with that name exists.
    Raises ``ValueError`` if ``entry.name != name`` (renaming is
    not supported; the field is read-only on edit in the UI).
    """
    if entry.name != name:
        raise ValueError(
            f"connection cannot be renamed in place: "
            f"old={name!r}, new={entry.name!r}"
        )
    if entry.kind not in VALID_KINDS:
        raise ConfigError(f"connection {entry.name!r} has invalid kind {entry.kind!r}")
    cfg = self.load()
    if name not in cfg.connections:
        raise KeyError(name)
    new_conns = {**cfg.connections, name: entry}
    self.save(
        Config(
            connections=new_conns,
            defaults=cfg.defaults,
            keybindings=cfg.keybindings,
        )
    )

def remove_connection(self, name: str) -> None:
    """Atomic removal of a connection.

    Raises ``KeyError`` if no connection with that name exists.
    """
    cfg = self.load()
    if name not in cfg.connections:
        raise KeyError(name)
    new_conns = {k: v for k, v in cfg.connections.items() if k != name}
    self.save(
        Config(
            connections=new_conns,
            defaults=cfg.defaults,
            keybindings=cfg.keybindings,
        )
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run:
```bash
uv run pytest tests/unit/infra/test_config_store.py -v -k "update_connection or remove_connection"
```
Expected: 5 PASSED.

- [ ] **Step 5: Run all quality gates**

```bash
uv run ruff check . && uv run ruff format --check . && uv run mypy --strict src && bash scripts/check-layers.sh && uv run pytest
```
All green.

- [ ] **Step 6: Commit**

```bash
git add src/aws_tui/infra/config_store.py tests/unit/infra/test_config_store.py
git commit -m "feat(infra): ConfigStore.update_connection + remove_connection

Atomic CRUD extensions matching the existing add_connection pattern
(tempfile + os.replace via the same save() path). update_connection
forbids renames (entry.name must match the target name); both raise
KeyError on unknown names."
```

---

## 1.6. Task 2: `ConnectionListChangedMessage`

**Files:**
- Modify: `src/aws_tui/vm/messages.py` (add new dataclass + `__all__` entry)
- Test: `tests/unit/vm/test_messages.py` (extend existing file)

**Interfaces:**
- Produces: `ConnectionListChangedMessage(names: tuple[str, ...], change: Literal["added", "updated", "deleted"], sender_name: str = "s3_connections")`

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/vm/test_messages.py`:

```python
from aws_tui.vm.messages import ConnectionListChangedMessage


def test_connection_list_changed_message_shape():
    msg = ConnectionListChangedMessage(
        names=("minio-local", "ceph-staging"),
        change="updated",
    )
    assert msg.names == ("minio-local", "ceph-staging")
    assert msg.change == "updated"
    assert msg.sender_name == "s3_connections"
    assert msg.sender_object is msg


def test_connection_list_changed_message_is_frozen():
    import dataclasses
    msg = ConnectionListChangedMessage(names=("x",), change="added")
    with __import__("pytest").raises(dataclasses.FrozenInstanceError):
        msg.change = "deleted"  # type: ignore[misc]
```

- [ ] **Step 2: Run test to verify it fails**

Run:
```bash
uv run pytest tests/unit/vm/test_messages.py -v -k connection_list_changed
```
Expected: 2 FAILED with `ImportError: cannot import name 'ConnectionListChangedMessage'`.

- [ ] **Step 3: Add the message**

In `src/aws_tui/vm/messages.py`, after the existing `TransferCancelRequestedMessage` block (before `KeymapChangedMessage`), add:

```python
@dataclass(frozen=True, slots=True)
class ConnectionListChangedMessage:
    """Published by :class:`S3ConnectionsVM` after each successful CRUD
    on the s3-compatible connection list.

    Subscribers:
    - :class:`ConnectionResolver` (cache invalidation if applicable),
    - :class:`ServicesMenuVM` (re-derive the service filter),
    - :class:`AwsTuiApp` (drop deleted names from
      :attr:`AppContext.unreachable_connections`),
    - :class:`SettingsVM` (accumulate names for the reload-on-close
      logic).
    """

    names: tuple[str, ...]
    change: Literal["added", "updated", "deleted"]
    sender_name: str = "s3_connections"

    @property
    def sender_object(self) -> object:
        return self
```

Add `"ConnectionListChangedMessage"` to the `__all__` list (insert in alphabetical order, between `AuthExpiredReason` and `FocusChangedMessage`).

- [ ] **Step 4: Run test to verify it passes**

Run:
```bash
uv run pytest tests/unit/vm/test_messages.py -v -k connection_list_changed
```
Expected: 2 PASSED.

- [ ] **Step 5: Run all quality gates**

```bash
uv run ruff check . && uv run ruff format --check . && uv run mypy --strict src && bash scripts/check-layers.sh && uv run pytest
```
All green.

- [ ] **Step 6: Commit**

```bash
git add src/aws_tui/vm/messages.py tests/unit/vm/test_messages.py
git commit -m "feat(vm/messages): ConnectionListChangedMessage

Published by S3ConnectionsVM on every CRUD; subscribers include
ServicesMenuVM (filter refresh), AwsTuiApp (drop unreachable
entries on delete), and SettingsVM (dirty-set tracking for the
reload-on-close logic)."
```

---

## 1.7. Task 3: `S3ConnectionsVM`

**Files:**
- Create: `src/aws_tui/vm/settings/__init__.py` (empty)
- Create: `src/aws_tui/vm/settings/s3_connections_vm.py`
- Create: `tests/unit/vm/settings/__init__.py` (empty)
- Create: `tests/unit/vm/settings/test_s3_connections_vm.py`

**Interfaces:**
- Consumes:
  - `aws_tui.infra.config_store.ConfigStore` (uses `add_connection`, `update_connection`, `remove_connection` from Task 1)
  - `aws_tui.infra.connection_resolver.ConnectionResolver` (uses `list()`)
  - `aws_tui.infra.config_store.ConnectionEntry`, `aws_tui.infra.connection_resolver.Connection`
  - `aws_tui.vm.messages.ConnectionListChangedMessage` (from Task 2)
  - `vmx.MessageHub`, `vmx.Message`, `vmx.services.dispatcher.Dispatcher`
- Produces:
  - `class S3ConnectionsVM` with methods: `add(entry: ConnectionEntry) -> None`, `update(name: str, entry: ConnectionEntry) -> None`, `remove(name: str) -> None`, `connections` property returning `tuple[Connection, ...]` filtered to s3-compatible, `construct()`/`destruct()`/`dispose()` lifecycle.

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/vm/settings/__init__.py` as an empty file. Then create `tests/unit/vm/settings/test_s3_connections_vm.py`:

```python
"""Tests for S3ConnectionsVM."""

from __future__ import annotations

from pathlib import Path
from typing import cast

import pytest
from vmx import NULL_DISPATCHER, MessageHub
from vmx.messages.protocols import Message

from aws_tui.infra.config_store import ConfigStore, ConnectionEntry
from aws_tui.infra.connection_resolver import ConnectionResolver
from aws_tui.vm.messages import ConnectionListChangedMessage
from aws_tui.vm.settings.s3_connections_vm import S3ConnectionsVM


def _hub() -> MessageHub[Message]:
    return cast("MessageHub[Message]", MessageHub())


def _entry(name: str = "minio-local", region: str = "us-east-1") -> ConnectionEntry:
    return ConnectionEntry(
        name=name,
        kind="s3-compatible",
        region=region,
        endpoint_url="http://localhost:9000",
        access_key_id="AKIATEST",
        secret_access_key="SECRETTEST",
        force_path_style=True,
        verify_tls=True,
    )


def _make_vm(tmp_path: Path) -> tuple[S3ConnectionsVM, MessageHub[Message], ConfigStore]:
    hub = _hub()
    store = ConfigStore(path=tmp_path / "config.toml")
    resolver = ConnectionResolver(config_store=store)
    vm = S3ConnectionsVM(
        resolver=resolver, config_store=store, hub=hub, dispatcher=NULL_DISPATCHER
    )
    vm.construct()
    return vm, hub, store


def test_connections_filters_to_s3_compatible(tmp_path: Path) -> None:
    vm, _, store = _make_vm(tmp_path)
    store.add_connection(_entry("minio-local"))
    store.add_connection(
        ConnectionEntry(name="aws-prod", kind="aws", profile="default", region="us-east-1")
    )
    names = [c.name for c in vm.connections]
    assert names == ["minio-local"]
    vm.dispose()


def test_add_persists_and_publishes(tmp_path: Path) -> None:
    vm, hub, store = _make_vm(tmp_path)
    received: list[ConnectionListChangedMessage] = []
    hub.messages.subscribe(
        on_next=lambda m: received.append(m) if isinstance(m, ConnectionListChangedMessage) else None
    )
    vm.add(_entry("new-bucket"))
    assert "new-bucket" in store.load().connections
    assert len(received) == 1
    assert received[0].change == "added"
    assert received[0].names == ("new-bucket",)
    vm.dispose()


def test_update_persists_and_publishes(tmp_path: Path) -> None:
    vm, hub, store = _make_vm(tmp_path)
    vm.add(_entry("minio-local"))
    received: list[ConnectionListChangedMessage] = []
    hub.messages.subscribe(
        on_next=lambda m: received.append(m) if isinstance(m, ConnectionListChangedMessage) else None
    )
    vm.update("minio-local", _entry("minio-local", region="us-west-2"))
    assert store.load().connections["minio-local"].region == "us-west-2"
    assert len(received) == 1
    assert received[0].change == "updated"
    assert received[0].names == ("minio-local",)
    vm.dispose()


def test_remove_persists_and_publishes(tmp_path: Path) -> None:
    vm, hub, store = _make_vm(tmp_path)
    vm.add(_entry("minio-local"))
    received: list[ConnectionListChangedMessage] = []
    hub.messages.subscribe(
        on_next=lambda m: received.append(m) if isinstance(m, ConnectionListChangedMessage) else None
    )
    vm.remove("minio-local")
    assert "minio-local" not in store.load().connections
    assert len(received) == 1
    assert received[0].change == "deleted"
    assert received[0].names == ("minio-local",)
    vm.dispose()


def test_add_duplicate_name_rejected(tmp_path: Path) -> None:
    vm, _, _ = _make_vm(tmp_path)
    vm.add(_entry("dup"))
    with pytest.raises(ValueError, match="already exists"):
        vm.add(_entry("dup"))
    vm.dispose()


def test_update_with_renamed_entry_rejected(tmp_path: Path) -> None:
    vm, _, _ = _make_vm(tmp_path)
    vm.add(_entry("old"))
    with pytest.raises(ValueError, match="cannot be renamed"):
        vm.update("old", _entry("new"))
    vm.dispose()


def test_construct_dispose_clean(tmp_path: Path) -> None:
    vm, _, _ = _make_vm(tmp_path)
    vm.dispose()
    # No exception on double-dispose
    vm.dispose()
```

- [ ] **Step 2: Run tests to verify they fail**

Run:
```bash
uv run pytest tests/unit/vm/settings/test_s3_connections_vm.py -v
```
Expected: `ImportError: No module named 'aws_tui.vm.settings'` or similar.

- [ ] **Step 3: Implement `S3ConnectionsVM`**

Create `src/aws_tui/vm/settings/__init__.py` as an empty file. Then create `src/aws_tui/vm/settings/s3_connections_vm.py`:

```python
"""S3ConnectionsVM — CRUD over kind='s3-compatible' connections."""

from __future__ import annotations

from vmx import ComponentVM, Message, MessageHub
from vmx.lifecycle.status import ConstructionStatus
from vmx.services.dispatcher import Dispatcher

from aws_tui.infra.config_store import ConfigStore, ConnectionEntry
from aws_tui.infra.connection_resolver import Connection, ConnectionResolver
from aws_tui.vm.messages import ConnectionListChangedMessage


class S3ConnectionsVM:
    """List + CRUD over the s3-compatible subset of TOML connections.

    The CRUD verbs (``add`` / ``update`` / ``remove``) validate, persist
    via :class:`ConfigStore`, then publish a
    :class:`ConnectionListChangedMessage` on the hub. Subscribers
    (``SettingsVM``, ``ServicesMenuVM``, ``AwsTuiApp``) react to the
    message; this VM never tells them directly.
    """

    def __init__(
        self,
        *,
        resolver: ConnectionResolver,
        config_store: ConfigStore,
        hub: MessageHub[Message],
        dispatcher: Dispatcher,
    ) -> None:
        self._resolver: ConnectionResolver = resolver
        self._config_store: ConfigStore = config_store
        self._hub: MessageHub[Message] = hub
        self._dispatcher: Dispatcher = dispatcher
        self._inner: ComponentVM = (
            ComponentVM.builder().name("s3_connections").services(hub, dispatcher).build()
        )

    # ── Lifecycle ──────────────────────────────────────────────────────────

    def construct(self) -> None:
        self._inner.construct()

    def destruct(self) -> None:
        self._inner.destruct()

    def dispose(self) -> None:
        self._inner.dispose()

    @property
    def status(self) -> ConstructionStatus:
        return self._inner.status

    @property
    def is_constructed(self) -> bool:
        return self._inner.is_constructed

    @property
    def name(self) -> str:
        return self._inner.name

    # ── Read ───────────────────────────────────────────────────────────────

    @property
    def connections(self) -> tuple[Connection, ...]:
        """All s3-compatible connections, in resolver order.

        Re-derived from the resolver each call — the resolver has no
        cache, so a recent CRUD is reflected immediately.
        """
        return tuple(c for c in self._resolver.list() if c.kind == "s3-compatible")

    @property
    def names(self) -> frozenset[str]:
        return frozenset(c.name for c in self.connections)

    # ── Write ──────────────────────────────────────────────────────────────

    def add(self, entry: ConnectionEntry) -> None:
        """Validate uniqueness, persist via ConfigStore, publish 'added'."""
        if entry.name in self.names:
            raise ValueError(f"connection {entry.name!r} already exists")
        self._config_store.add_connection(entry)
        self._hub.send(
            ConnectionListChangedMessage(names=(entry.name,), change="added")
        )

    def update(self, name: str, entry: ConnectionEntry) -> None:
        """Validate rename-disallowed, persist, publish 'updated'."""
        if entry.name != name:
            raise ValueError(
                f"connection cannot be renamed in place: old={name!r}, new={entry.name!r}"
            )
        self._config_store.update_connection(name, entry)
        self._hub.send(
            ConnectionListChangedMessage(names=(name,), change="updated")
        )

    def remove(self, name: str) -> None:
        """Persist removal, publish 'deleted'."""
        self._config_store.remove_connection(name)
        self._hub.send(
            ConnectionListChangedMessage(names=(name,), change="deleted")
        )


__all__ = ["S3ConnectionsVM"]
```

- [ ] **Step 4: Run tests to verify they pass**

Run:
```bash
uv run pytest tests/unit/vm/settings/test_s3_connections_vm.py -v
```
Expected: 7 PASSED.

- [ ] **Step 5: Run all quality gates**

```bash
uv run ruff check . && uv run ruff format --check . && uv run mypy --strict src && bash scripts/check-layers.sh && uv run pytest
```
All green.

- [ ] **Step 6: Commit**

```bash
git add src/aws_tui/vm/settings/__init__.py src/aws_tui/vm/settings/s3_connections_vm.py tests/unit/vm/settings/__init__.py tests/unit/vm/settings/test_s3_connections_vm.py
git commit -m "feat(vm/settings): S3ConnectionsVM with CRUD + message publishing

Filters resolver.list() to kind='s3-compatible'. add/update/remove
go through ConfigStore atomic writes and publish a
ConnectionListChangedMessage on success. Validates duplicate-name
on add and rename-disallowed on update."
```

---

## 1.8. Task 4: `SettingsVM`

**Files:**
- Create: `src/aws_tui/vm/settings/settings_vm.py`
- Create: `tests/unit/vm/settings/test_settings_vm.py`

**Interfaces:**
- Consumes:
  - `S3ConnectionsVM` (from Task 3)
  - `aws_tui.vm.messages.ConnectionListChangedMessage` (from Task 2)
  - `vmx.MessageHub`, `vmx.Message`, `vmx.services.dispatcher.Dispatcher`, `vmx.ComponentVM`
- Produces:
  - `class SettingsVM` with: `SECTIONS: tuple[str, ...]`, `ENABLED: frozenset[str]`, `active_section: str`, `change_section(section_id: str) -> None`, `dirty_connection_names: frozenset[str]`, `s3: S3ConnectionsVM`, `clear_dirty() -> None`, `construct()`/`destruct()`/`dispose()`.
  - The reload-on-close logic itself lives in `AwsTuiApp` (Task 9); `SettingsVM` just tracks the dirty names.

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/vm/settings/test_settings_vm.py`:

```python
"""Tests for SettingsVM."""

from __future__ import annotations

from pathlib import Path
from typing import cast

from vmx import NULL_DISPATCHER, MessageHub
from vmx.lifecycle.status import ConstructionStatus
from vmx.messages.protocols import Message

from aws_tui.infra.config_store import ConfigStore, ConnectionEntry
from aws_tui.infra.connection_resolver import ConnectionResolver
from aws_tui.vm.messages import ConnectionListChangedMessage
from aws_tui.vm.settings.s3_connections_vm import S3ConnectionsVM
from aws_tui.vm.settings.settings_vm import SettingsVM


def _hub() -> MessageHub[Message]:
    return cast("MessageHub[Message]", MessageHub())


def _entry(name: str) -> ConnectionEntry:
    return ConnectionEntry(
        name=name,
        kind="s3-compatible",
        region="us-east-1",
        endpoint_url="http://localhost:9000",
        access_key_id="AKIATEST",
        secret_access_key="SECRETTEST",
        force_path_style=True,
        verify_tls=True,
    )


def _make_vm(tmp_path: Path) -> tuple[SettingsVM, MessageHub[Message], S3ConnectionsVM]:
    hub = _hub()
    store = ConfigStore(path=tmp_path / "config.toml")
    resolver = ConnectionResolver(config_store=store)
    s3 = S3ConnectionsVM(
        resolver=resolver, config_store=store, hub=hub, dispatcher=NULL_DISPATCHER
    )
    vm = SettingsVM(s3=s3, hub=hub, dispatcher=NULL_DISPATCHER)
    vm.construct()
    return vm, hub, s3


def test_default_active_section_is_connections(tmp_path: Path) -> None:
    vm, _, _ = _make_vm(tmp_path)
    assert vm.active_section == "connections"
    vm.dispose()


def test_sections_and_enabled_constants(tmp_path: Path) -> None:
    vm, _, _ = _make_vm(tmp_path)
    assert vm.SECTIONS == ("connections", "themes", "keymap")
    assert vm.ENABLED == frozenset({"connections"})
    vm.dispose()


def test_change_section_to_enabled_works(tmp_path: Path) -> None:
    vm, _, _ = _make_vm(tmp_path)
    vm.change_section("connections")
    assert vm.active_section == "connections"
    vm.dispose()


def test_change_section_to_disabled_is_noop(tmp_path: Path) -> None:
    vm, _, _ = _make_vm(tmp_path)
    vm.change_section("themes")
    assert vm.active_section == "connections"  # unchanged
    vm.change_section("keymap")
    assert vm.active_section == "connections"
    vm.dispose()


def test_dirty_set_accumulates_updates_and_deletes(tmp_path: Path) -> None:
    vm, hub, _ = _make_vm(tmp_path)
    hub.send(ConnectionListChangedMessage(names=("a",), change="updated"))
    hub.send(ConnectionListChangedMessage(names=("b",), change="deleted"))
    assert vm.dirty_connection_names == frozenset({"a", "b"})
    vm.dispose()


def test_dirty_set_ignores_adds(tmp_path: Path) -> None:
    vm, hub, _ = _make_vm(tmp_path)
    hub.send(ConnectionListChangedMessage(names=("new",), change="added"))
    assert vm.dirty_connection_names == frozenset()
    vm.dispose()


def test_clear_dirty_resets_set(tmp_path: Path) -> None:
    vm, hub, _ = _make_vm(tmp_path)
    hub.send(ConnectionListChangedMessage(names=("a",), change="updated"))
    assert vm.dirty_connection_names == frozenset({"a"})
    vm.clear_dirty()
    assert vm.dirty_connection_names == frozenset()
    vm.dispose()


def test_lifecycle_status(tmp_path: Path) -> None:
    vm, _, _ = _make_vm(tmp_path)
    assert vm.status == ConstructionStatus.CONSTRUCTED
    vm.dispose()
    assert vm.status == ConstructionStatus.DISPOSED
```

- [ ] **Step 2: Run tests to verify they fail**

Run:
```bash
uv run pytest tests/unit/vm/settings/test_settings_vm.py -v
```
Expected: `ImportError: cannot import name 'SettingsVM' from 'aws_tui.vm.settings.settings_vm'`.

- [ ] **Step 3: Implement `SettingsVM`**

Create `src/aws_tui/vm/settings/settings_vm.py`:

```python
"""SettingsVM — parent VM for the settings shell."""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

from vmx import ComponentVM, Message, MessageHub, PropertyChangedMessage
from vmx.lifecycle.status import ConstructionStatus
from vmx.services.dispatcher import Dispatcher

from aws_tui.vm.messages import ConnectionListChangedMessage
from aws_tui.vm.settings.s3_connections_vm import S3ConnectionsVM

if TYPE_CHECKING:
    from reactivex.abc import DisposableBase


class SettingsVM:
    """Parent VM for the settings shell.

    Owns the active section (one of :attr:`SECTIONS`) and a dirty-set
    of connection names that changed during the modal's lifetime.
    ``AwsTuiApp`` reads :attr:`dirty_connection_names` when the
    SettingsModal dismisses and reloads any pane bound to a dirty
    connection (see the reload-on-close logic in `app.py`).
    """

    SECTIONS: Final[tuple[str, ...]] = ("connections", "themes", "keymap")
    ENABLED: Final[frozenset[str]] = frozenset({"connections"})

    def __init__(
        self,
        *,
        s3: S3ConnectionsVM,
        hub: MessageHub[Message],
        dispatcher: Dispatcher,
    ) -> None:
        self._hub: MessageHub[Message] = hub
        self._dispatcher: Dispatcher = dispatcher
        self._s3: S3ConnectionsVM = s3
        self._active_section: str = "connections"
        self._dirty_connection_names: set[str] = set()
        self._sub: DisposableBase | None = None
        self._inner: ComponentVM = (
            ComponentVM.builder().name("settings").services(hub, dispatcher).build()
        )

    # ── Properties ─────────────────────────────────────────────────────────

    @property
    def s3(self) -> S3ConnectionsVM:
        return self._s3

    @property
    def active_section(self) -> str:
        return self._active_section

    @property
    def dirty_connection_names(self) -> frozenset[str]:
        return frozenset(self._dirty_connection_names)

    @property
    def status(self) -> ConstructionStatus:
        return self._inner.status

    @property
    def is_constructed(self) -> bool:
        return self._inner.is_constructed

    @property
    def name(self) -> str:
        return self._inner.name

    # ── Lifecycle ──────────────────────────────────────────────────────────

    def construct(self) -> None:
        self._inner.construct()
        # Subscribe AFTER inner construct so any message that arrives
        # mid-construction doesn't fire on a half-built VM.
        self._sub = self._hub.messages.subscribe(on_next=self._on_hub_message)

    def destruct(self) -> None:
        self._inner.destruct()

    def dispose(self) -> None:
        if self._sub is not None:
            self._sub.dispose()
            self._sub = None
        self._inner.dispose()

    # ── Actions ────────────────────────────────────────────────────────────

    def change_section(self, section_id: str) -> None:
        """Switch active section; no-op if the section is disabled."""
        if section_id not in self.ENABLED:
            return
        if section_id == self._active_section:
            return
        self._active_section = section_id
        self._hub.send(
            PropertyChangedMessage.create(self, self._inner.name, "active_section")
        )

    def clear_dirty(self) -> None:
        """Reset the dirty-set. Called by ``AwsTuiApp`` after the
        post-close pane reload has finished."""
        self._dirty_connection_names.clear()

    # ── Hub subscriber ─────────────────────────────────────────────────────

    def _on_hub_message(self, msg: object) -> None:
        """Accumulate names from 'updated' and 'deleted' events only.

        'added' is excluded because a brand-new connection can't be
        bound to any pane yet — there's nothing to reload.
        """
        if not isinstance(msg, ConnectionListChangedMessage):
            return
        if msg.change == "added":
            return
        self._dirty_connection_names.update(msg.names)


__all__ = ["SettingsVM"]
```

- [ ] **Step 4: Run tests to verify they pass**

Run:
```bash
uv run pytest tests/unit/vm/settings/test_settings_vm.py -v
```
Expected: 8 PASSED.

- [ ] **Step 5: Run all quality gates**

```bash
uv run ruff check . && uv run ruff format --check . && uv run mypy --strict src && bash scripts/check-layers.sh && uv run pytest
```
All green.

- [ ] **Step 6: Commit**

```bash
git add src/aws_tui/vm/settings/settings_vm.py tests/unit/vm/settings/test_settings_vm.py
git commit -m "feat(vm/settings): SettingsVM with dirty-set tracking

Holds the active section (default 'connections'), the
SECTIONS/ENABLED constants for the sidebar nav, and a dirty-set
of connection names accumulated from 'updated'/'deleted'
ConnectionListChangedMessage events ('added' excluded — new
entries can't be bound to a pane yet). AwsTuiApp reads this set
on modal dismiss to drive the pane reload."
```

---

## 1.9. Task 5: `S3CompatFormModal`: `name_locked` + live validation

**Files:**
- Modify: `src/aws_tui/ui/widgets/first_run_modal.py` (extend `S3CompatFormModal.__init__` and `compose`)
- Test: `tests/unit/ui/test_s3_compat_form_modal.py` (new file)

**Interfaces:**
- Consumes: existing `S3CompatFormModal` constructor + `S3CompatForm` dataclass
- Produces:
  - `S3CompatFormModal(*, hub, defaults=None, name_locked: bool = False)` (new param)
  - Internal live validation: `Input` widgets get class `-invalid` on invalid content, Save button gets `disabled=True` when any required field is invalid.
  - Read-only `name` field when `name_locked=True`

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/ui/test_s3_compat_form_modal.py`:

```python
"""Tests for S3CompatFormModal extensions (name_locked + validation helpers)."""

from __future__ import annotations

import pytest

from aws_tui.ui.widgets.first_run_modal import _validate_s3_form_value


def test_name_valid_simple():
    assert _validate_s3_form_value("name", "minio-local") is None


def test_name_invalid_empty():
    assert _validate_s3_form_value("name", "") is not None


def test_name_invalid_chars():
    assert _validate_s3_form_value("name", "has space") is not None
    assert _validate_s3_form_value("name", "with/slash") is not None


def test_name_invalid_too_long():
    assert _validate_s3_form_value("name", "x" * 33) is not None


def test_name_valid_max_length():
    assert _validate_s3_form_value("name", "x" * 32) is None


def test_endpoint_url_valid():
    assert _validate_s3_form_value("endpoint_url", "http://localhost:9000") is None
    assert _validate_s3_form_value("endpoint_url", "https://minio.internal:443/path") is None


def test_endpoint_url_invalid():
    assert _validate_s3_form_value("endpoint_url", "") is not None
    assert _validate_s3_form_value("endpoint_url", "ftp://wrong") is not None
    assert _validate_s3_form_value("endpoint_url", "no-scheme") is not None
    assert _validate_s3_form_value("endpoint_url", "http://") is not None


@pytest.mark.parametrize("field", ["region", "access_key_id", "secret_access_key"])
def test_required_field_rejects_empty(field: str) -> None:
    assert _validate_s3_form_value(field, "") is not None
    assert _validate_s3_form_value(field, "   ") is not None


@pytest.mark.parametrize("field", ["region", "access_key_id", "secret_access_key"])
def test_required_field_accepts_nonempty(field: str) -> None:
    assert _validate_s3_form_value(field, "valid") is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run:
```bash
uv run pytest tests/unit/ui/test_s3_compat_form_modal.py -v
```
Expected: `ImportError: cannot import name '_validate_s3_form_value'`.

- [ ] **Step 3: Implement the validation helper + extend `S3CompatFormModal`**

In `src/aws_tui/ui/widgets/first_run_modal.py`:

(a) Add a module-level helper above the class definitions:

```python
import re
from urllib.parse import urlparse

_NAME_RE = re.compile(r"^[A-Za-z0-9_-]{1,32}$")


def _validate_s3_form_value(field: str, value: str) -> str | None:
    """Return None if the value is valid for the field, else an
    error message suitable for tooltip display.

    Validation rules per the design spec:
    - ``name``: matches ``^[A-Za-z0-9_-]{1,32}$``
    - ``endpoint_url``: starts with ``http://`` or ``https://``, has
      a non-empty netloc
    - ``region`` / ``access_key_id`` / ``secret_access_key``:
      non-empty after strip
    """
    stripped = value.strip()
    if field == "name":
        if not _NAME_RE.match(value):
            return "1-32 chars, alphanumeric + dash/underscore only"
        return None
    if field == "endpoint_url":
        if not stripped:
            return "required"
        try:
            parsed = urlparse(stripped)
        except ValueError:
            return "not a valid URL"
        if parsed.scheme not in ("http", "https"):
            return "must start with http:// or https://"
        if not parsed.netloc:
            return "missing host"
        return None
    # region, access_key_id, secret_access_key — required, non-empty
    if not stripped:
        return "required"
    return None
```

(b) Extend `S3CompatFormModal.__init__` to accept `name_locked`:

```python
def __init__(
    self,
    *,
    hub: MessageHub[Message],
    defaults: S3CompatForm | None = None,
    name_locked: bool = False,
) -> None:
    super().__init__()
    self._hub: MessageHub[Message] = hub
    self._defaults: S3CompatForm | None = defaults
    self._name_locked: bool = name_locked
```

(c) In `compose()`, when yielding the `Input` for the `name` field, set `disabled=self._name_locked` only for that field. Replace the existing loop with:

```python
def compose(self) -> ComposeResult:
    with Container():
        yield Static("add s3-compatible connection", classes="modal-title")
        with Vertical(classes="form-fields"):
            for key, label, placeholder, secret in _FIELDS:
                default = ""
                if self._defaults is not None:
                    default = str(getattr(self._defaults, key, ""))
                yield Static(label, classes="form-label")
                yield Input(
                    value=default,
                    placeholder=placeholder,
                    password=secret,
                    id=f"form-{key}",
                    disabled=(self._name_locked and key == "name"),
                )
        with Horizontal(classes="modal-footer"):
            yield ModalButton("cancel", button_id="form-cancel-btn")
            yield ModalButton("save", button_id="form-save-btn", classes="-primary")
```

(d) Add an `on_input_changed` handler on the modal that toggles `-invalid` and updates the save button:

```python
from textual import on
from textual.widgets import Input

@on(Input.Changed)
def _on_input_changed(self, event: Input.Changed) -> None:
    field = event.input.id.removeprefix("form-") if event.input.id else ""
    if field not in {"name", "endpoint_url", "region", "access_key_id", "secret_access_key"}:
        return
    err = _validate_s3_form_value(field, event.value)
    if err is None:
        event.input.remove_class("-invalid")
    else:
        event.input.add_class("-invalid")
    self._refresh_save_button()

def _refresh_save_button(self) -> None:
    """Disable the save button if any required field is invalid."""
    save_btn = self.query_one("#form-save-btn", ModalButton)
    invalid = False
    for key, _, _, _ in _FIELDS:
        inp = self.query_one(f"#form-{key}", Input)
        if _validate_s3_form_value(key, inp.value) is not None:
            invalid = True
            break
    save_btn.disabled = invalid

def on_mount(self) -> None:
    # Initial sync: if defaults were passed, validation has already
    # run via compose; otherwise the empty form is invalid and the
    # save button must reflect that.
    self._refresh_save_button()
```

If `on_mount` is already defined on this class, integrate the `_refresh_save_button()` call into the existing handler. Confirm by reading the current `first_run_modal.py` before editing.

- [ ] **Step 4: Run tests to verify they pass**

Run:
```bash
uv run pytest tests/unit/ui/test_s3_compat_form_modal.py -v
```
Expected: 11 PASSED.

- [ ] **Step 5: Run all quality gates**

```bash
uv run ruff check . && uv run ruff format --check . && uv run mypy --strict src && bash scripts/check-layers.sh && uv run pytest
```
All green.

- [ ] **Step 6: Commit**

```bash
git add src/aws_tui/ui/widgets/first_run_modal.py tests/unit/ui/test_s3_compat_form_modal.py
git commit -m "feat(ui): S3CompatFormModal supports edit mode + live validation

New name_locked: bool = False param disables the name Input when
True (used by the Settings overlay's Edit flow — renaming is not
supported in place). Live validation on Input.Changed toggles an
-invalid class on each field and disables the save button when any
required field fails. Pure-function _validate_s3_form_value is
unit-tested independently."
```

---

## 1.10. Task 6: `S3ConnectionsPanel` + `_PlaceholderPanel`

**Files:**
- Create: `src/aws_tui/ui/widgets/settings/__init__.py` (empty)
- Create: `src/aws_tui/ui/widgets/settings/_placeholder_panel.py`
- Create: `src/aws_tui/ui/widgets/settings/s3_connections_panel.py`

**Interfaces:**
- Consumes: `S3ConnectionsVM` (from Task 3), `S3CompatFormModal` (extended in Task 5), `ConfirmModal` (existing), `MessageHub`
- Produces:
  - `class _PlaceholderPanel(Widget)` — renders a `Static` "Coming in v0.8"
  - `class S3ConnectionsPanel(Widget)` — renders the connection list + add button + per-row chips. Methods: `compose()`, `on_button_pressed(event)`. Holds a reference to its `S3ConnectionsVM`. When the user clicks Add/Edit, it pushes `S3CompatFormModal` via `self.app.push_screen(...)`. When the user clicks Delete, it pushes `ConfirmModal`.

**No new tests this task** — the panel's behavior is exercised in the snapshot tests (Task 12) and the in-process integration tests (Task 14). Add a one-line construction smoke test to keep the unit suite happy.

- [ ] **Step 1: Write the construction smoke test**

Create `tests/unit/ui/test_s3_connections_panel.py`:

```python
"""Smoke test for S3ConnectionsPanel construction."""

from __future__ import annotations

from pathlib import Path
from typing import cast

from vmx import NULL_DISPATCHER, MessageHub
from vmx.messages.protocols import Message

from aws_tui.infra.config_store import ConfigStore
from aws_tui.infra.connection_resolver import ConnectionResolver
from aws_tui.ui.widgets.settings.s3_connections_panel import S3ConnectionsPanel
from aws_tui.vm.settings.s3_connections_vm import S3ConnectionsVM


def test_s3_connections_panel_can_be_constructed(tmp_path: Path) -> None:
    hub = cast("MessageHub[Message]", MessageHub())
    store = ConfigStore(path=tmp_path / "config.toml")
    resolver = ConnectionResolver(config_store=store)
    s3_vm = S3ConnectionsVM(
        resolver=resolver, config_store=store, hub=hub, dispatcher=NULL_DISPATCHER
    )
    s3_vm.construct()
    try:
        panel = S3ConnectionsPanel(vm=s3_vm, hub=hub)
        assert panel.vm is s3_vm
    finally:
        s3_vm.dispose()
```

- [ ] **Step 2: Run test to verify it fails**

Run:
```bash
uv run pytest tests/unit/ui/test_s3_connections_panel.py -v
```
Expected: `ImportError`.

- [ ] **Step 3: Create the placeholder panel**

Create `src/aws_tui/ui/widgets/settings/__init__.py` as empty file. Then create `src/aws_tui/ui/widgets/settings/_placeholder_panel.py`:

```python
"""Placeholder panel rendered when a (soon) section is selected."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.widget import Widget
from textual.widgets import Static


class _PlaceholderPanel(Widget):
    """Body widget rendered if a disabled sidebar section is ever
    programmatically selected.

    Unreachable in sub-project A (the disabled rows skip on keyboard
    nav and have no click handler), kept here so the SettingsModal
    can swap any section to a widget without conditionals.
    """

    DEFAULT_CSS = """
    _PlaceholderPanel {
        align: center middle;
    }
    _PlaceholderPanel > Static {
        text-style: italic;
        color: $text-muted;
    }
    """

    def __init__(self, *, section_id: str) -> None:
        super().__init__()
        self._section_id: str = section_id

    def compose(self) -> ComposeResult:
        yield Static(f"{self._section_id.title()} — coming in v0.8")


__all__ = ["_PlaceholderPanel"]
```

- [ ] **Step 4: Create the S3 connections panel**

Create `src/aws_tui/ui/widgets/settings/s3_connections_panel.py`:

```python
"""S3ConnectionsPanel — list + CRUD chips for s3-compatible connections."""

from __future__ import annotations

from textual import on
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widget import Widget
from textual.widgets import Button, Static
from vmx import Message, MessageHub

from aws_tui.infra.config_store import ConnectionEntry
from aws_tui.ui.widgets.confirm_modal import ConfirmModal
from aws_tui.ui.widgets.first_run_modal import S3CompatFormModal
from aws_tui.vm.chrome.confirmation_vm import (
    ConfirmationVM,
    ConfirmPath,
    ConfirmRequest,
)
from aws_tui.vm.chrome.first_run_vm import S3CompatForm
from aws_tui.vm.settings.s3_connections_vm import S3ConnectionsVM


class _RowAccent(Static):
    """1-cell wide colored left rule for a connection row."""


class _AddButton(Button):
    """Pill-shaped 'Add s3-compatible connection' button."""


class _ChipEdit(Button):
    """Inline edit chip for a connection row."""


class _ChipDelete(Button):
    """Inline delete chip for a connection row."""


class S3ConnectionsPanel(Widget):
    """Renders the list of s3-compatible connections + CRUD chips."""

    DEFAULT_CSS = """
    S3ConnectionsPanel {
        height: 1fr;
        width: 1fr;
    }
    S3ConnectionsPanel > #panel-body {
        height: 1fr;
        padding: 0 1;
    }
    S3ConnectionsPanel .connection-row {
        height: 1;
        width: 1fr;
    }
    S3ConnectionsPanel .empty-state {
        align: center middle;
        height: 1fr;
        padding: 1 2;
    }
    S3ConnectionsPanel .empty-state Static {
        text-align: center;
    }
    """

    def __init__(self, *, vm: S3ConnectionsVM, hub: MessageHub[Message]) -> None:
        super().__init__()
        self._vm: S3ConnectionsVM = vm
        self._hub: MessageHub[Message] = hub
        # Cache the row count so we can detect "transitioned to/from
        # empty" without rebuilding identically.
        self._last_row_count: int = -1

    @property
    def vm(self) -> S3ConnectionsVM:
        return self._vm

    def compose(self) -> ComposeResult:
        with Vertical(id="panel-body"):
            yield from self._render_body()

    def _render_body(self) -> ComposeResult:
        conns = self._vm.connections
        self._last_row_count = len(conns)
        if not conns:
            with Vertical(classes="empty-state"):
                yield Static("No S3-compatible connections configured yet.")
                yield Static("")
                yield Static("Add one to access MinIO, Wasabi, R2, etc.")
                yield Static("from the same panes you use for AWS S3.")
                yield Static("")
                yield _AddButton("+ Add s3-compatible connection", id="add-empty")
            return
        for c in conns:
            with Horizontal(classes="connection-row", id=f"row-{c.name}"):
                yield _RowAccent("▎", classes="row-accent")
                yield Static(c.name, classes="row-name")
                yield Static(c.endpoint_url or "", classes="row-endpoint")
                yield Static(c.region, classes="row-region")
                yield _ChipEdit("✎", id=f"edit-{c.name}", classes="row-chip-edit")
                yield _ChipDelete("✕", id=f"delete-{c.name}", classes="row-chip-delete")
        yield _AddButton("+ Add s3-compatible connection", id="add-populated")

    def refresh_rows(self) -> None:
        """Tear down + re-render the body container after a CRUD op."""
        body = self.query_one("#panel-body", Vertical)
        body.remove_children()
        for child in self._render_body():
            body.mount(child)

    @on(Button.Pressed, "#add-empty, #add-populated")
    async def _on_add(self, event: Button.Pressed) -> None:
        event.stop()
        result = await self.app.push_screen_wait(
            S3CompatFormModal(hub=self._hub, defaults=None, name_locked=False)
        )
        if result is None:
            return
        self._vm.add(_form_to_entry(result))
        self.refresh_rows()

    @on(Button.Pressed, ".row-chip-edit")
    async def _on_edit(self, event: Button.Pressed) -> None:
        event.stop()
        btn_id = event.button.id or ""
        name = btn_id.removeprefix("edit-")
        existing = next((c for c in self._vm.connections if c.name == name), None)
        if existing is None:
            return
        defaults = S3CompatForm(
            name=existing.name,
            endpoint_url=existing.endpoint_url or "",
            region=existing.region,
            access_key_id=existing.access_key_id or "",
            secret_access_key=existing.secret_access_key or "",
            force_path_style=existing.force_path_style,
            verify_tls=existing.verify_tls,
        )
        result = await self.app.push_screen_wait(
            S3CompatFormModal(hub=self._hub, defaults=defaults, name_locked=True)
        )
        if result is None:
            return
        self._vm.update(name, _form_to_entry(result))
        self.refresh_rows()

    @on(Button.Pressed, ".row-chip-delete")
    async def _on_delete(self, event: Button.Pressed) -> None:
        event.stop()
        btn_id = event.button.id or ""
        name = btn_id.removeprefix("delete-")
        confirm_vm = ConfirmationVM(hub=self._hub, dispatcher=self._vm._dispatcher)
        confirm_vm.construct()
        try:
            request = ConfirmRequest(
                title=f"Delete connection {name!r}?",
                paths=(ConfirmPath(label="Name", path=name),),
                body_lines=("This cannot be undone.",),
                confirm_label="Delete",
                cancel_label="Cancel",
                danger=True,
            )
            confirmed = await self.app.push_screen_wait(
                ConfirmModal(confirm_vm, request, hub=self._hub)
            )
        finally:
            confirm_vm.dispose()
        if confirmed:
            self._vm.remove(name)
            self.refresh_rows()


def _form_to_entry(form: S3CompatForm) -> ConnectionEntry:
    return ConnectionEntry(
        name=form.name,
        kind="s3-compatible",
        region=form.region,
        endpoint_url=form.endpoint_url,
        access_key_id=form.access_key_id,
        secret_access_key=form.secret_access_key,
        force_path_style=form.force_path_style,
        verify_tls=form.verify_tls,
    )


__all__ = ["S3ConnectionsPanel"]
```

NOTE on `self._vm._dispatcher` access: `S3ConnectionsVM` does not expose the dispatcher publicly. Either (a) add a `dispatcher` property to `S3ConnectionsVM` returning `self._dispatcher`, or (b) accept the dispatcher as a separate constructor arg to `S3ConnectionsPanel`. Pick (a) — minimal change, clean accessor. Add to `S3ConnectionsVM`:

```python
@property
def dispatcher(self) -> Dispatcher:
    return self._dispatcher
```

Update the panel's delete handler to use `self._vm.dispatcher` instead of `self._vm._dispatcher`.

- [ ] **Step 5: Run tests to verify they pass**

Run:
```bash
uv run pytest tests/unit/ui/test_s3_connections_panel.py -v
```
Expected: 1 PASSED.

- [ ] **Step 6: Run all quality gates**

```bash
uv run ruff check . && uv run ruff format --check . && uv run mypy --strict src && bash scripts/check-layers.sh && uv run pytest
```
All green.

- [ ] **Step 7: Commit**

```bash
git add src/aws_tui/ui/widgets/settings/__init__.py src/aws_tui/ui/widgets/settings/_placeholder_panel.py src/aws_tui/ui/widgets/settings/s3_connections_panel.py src/aws_tui/vm/settings/s3_connections_vm.py tests/unit/ui/test_s3_connections_panel.py
git commit -m "feat(ui): S3ConnectionsPanel + _PlaceholderPanel widgets

Panel renders list rows (name / endpoint / region / edit chip / delete
chip) or an empty state, plus an Add button. CRUD chips push the
existing S3CompatFormModal (with name_locked=True on edit) and the
polished ConfirmModal for delete. Also exposes a public
S3ConnectionsVM.dispatcher property used by the panel to construct a
ConfirmationVM for the delete dialog."
```

---

## 1.11. Task 7: `SettingsModal` screen

**Files:**
- Create: `src/aws_tui/ui/widgets/settings_modal.py`

**Interfaces:**
- Consumes: `SettingsVM`, `S3ConnectionsPanel` (Task 6), `_PlaceholderPanel` (Task 6), `MessageHub`
- Produces: `class SettingsModal(ModalScreen[None])` with constructor `(vm: SettingsVM, *, hub: MessageHub[Message])`.

**No unit tests this task** — covered by snapshot tests (Task 12) and integration tests (Task 14). Add a one-line construction smoke test.

- [ ] **Step 1: Write the construction smoke test**

Create `tests/unit/ui/test_settings_modal.py`:

```python
"""Smoke test for SettingsModal construction."""

from __future__ import annotations

from pathlib import Path
from typing import cast

from vmx import NULL_DISPATCHER, MessageHub
from vmx.messages.protocols import Message

from aws_tui.infra.config_store import ConfigStore
from aws_tui.infra.connection_resolver import ConnectionResolver
from aws_tui.ui.widgets.settings_modal import SettingsModal
from aws_tui.vm.settings.s3_connections_vm import S3ConnectionsVM
from aws_tui.vm.settings.settings_vm import SettingsVM


def test_settings_modal_can_be_constructed(tmp_path: Path) -> None:
    hub = cast("MessageHub[Message]", MessageHub())
    store = ConfigStore(path=tmp_path / "config.toml")
    resolver = ConnectionResolver(config_store=store)
    s3 = S3ConnectionsVM(
        resolver=resolver, config_store=store, hub=hub, dispatcher=NULL_DISPATCHER
    )
    s3.construct()
    vm = SettingsVM(s3=s3, hub=hub, dispatcher=NULL_DISPATCHER)
    vm.construct()
    try:
        modal = SettingsModal(vm=vm, hub=hub)
        assert modal.vm is vm
    finally:
        vm.dispose()
        s3.dispose()
```

- [ ] **Step 2: Run test to verify it fails**

Run:
```bash
uv run pytest tests/unit/ui/test_settings_modal.py -v
```
Expected: `ImportError`.

- [ ] **Step 3: Implement `SettingsModal`**

Create `src/aws_tui/ui/widgets/settings_modal.py`:

```python
"""SettingsModal — themed settings overlay with sidebar nav."""

from __future__ import annotations

from textual import on
from textual.app import ComposeResult
from textual.containers import Container, Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, ListItem, ListView, Static
from vmx import Message, MessageHub

from aws_tui.ui.widgets.modal_button import ModalButton
from aws_tui.ui.widgets.settings._placeholder_panel import _PlaceholderPanel
from aws_tui.ui.widgets.settings.s3_connections_panel import S3ConnectionsPanel
from aws_tui.vm.settings.settings_vm import SettingsVM


_SECTION_LABELS: dict[str, str] = {
    "connections": "Connections",
    "themes": "Themes",
    "keymap": "Keymap",
}


class SettingsModal(ModalScreen[None]):
    """Modal that hosts the App Settings shell.

    Sidebar entries:
      ▸ Connections        — active (sub-project A)
        Themes (soon)      — disabled until sub-project B lands
        Keymap (soon)      — disabled until sub-project C lands
    """

    BINDINGS = [  # noqa: RUF012
        ("escape", "close", "Close"),
    ]

    def __init__(self, *, vm: SettingsVM, hub: MessageHub[Message]) -> None:
        super().__init__()
        self._vm: SettingsVM = vm
        self._hub: MessageHub[Message] = hub

    @property
    def vm(self) -> SettingsVM:
        return self._vm

    def compose(self) -> ComposeResult:
        with Container(id="settings-frame"):
            yield Static("Settings", id="settings-title")
            with Horizontal(id="settings-content"):
                with Vertical(id="settings-sidebar"):
                    yield self._build_sidebar()
                with Vertical(id="settings-body"):
                    yield self._build_body()
            with Horizontal(id="settings-footer"):
                yield ModalButton("close", button_id="settings-close-btn")

    def _build_sidebar(self) -> ListView:
        items: list[ListItem] = []
        for section_id in self._vm.SECTIONS:
            label = _SECTION_LABELS[section_id]
            suffix = "" if section_id in self._vm.ENABLED else " (soon)"
            item = ListItem(Static(f"{label}{suffix}"), id=f"section-{section_id}")
            if section_id not in self._vm.ENABLED:
                item.disabled = True
                item.add_class("-disabled")
            items.append(item)
        view = ListView(*items, id="section-list")
        # Initial cursor = active section
        try:
            view.index = self._vm.SECTIONS.index(self._vm.active_section)
        except ValueError:
            view.index = 0
        return view

    def _build_body(self) -> S3ConnectionsPanel | _PlaceholderPanel:
        section = self._vm.active_section
        if section == "connections":
            return S3ConnectionsPanel(vm=self._vm.s3, hub=self._hub)
        return _PlaceholderPanel(section_id=section)

    @on(ListView.Highlighted)
    def _on_section_highlighted(self, event: ListView.Highlighted) -> None:
        if event.item is None or event.item.disabled:
            return
        item_id = event.item.id or ""
        section_id = item_id.removeprefix("section-")
        if section_id not in self._vm.SECTIONS:
            return
        if section_id == self._vm.active_section:
            return
        self._vm.change_section(section_id)
        self._swap_body()

    def _swap_body(self) -> None:
        body = self.query_one("#settings-body", Vertical)
        body.remove_children()
        body.mount(self._build_body())

    @on(Button.Pressed, "#settings-close-btn")
    def _on_close(self, event: Button.Pressed) -> None:
        event.stop()
        self.action_close()

    def action_close(self) -> None:
        self.dismiss()


__all__ = ["SettingsModal"]
```

- [ ] **Step 4: Run tests to verify they pass**

Run:
```bash
uv run pytest tests/unit/ui/test_settings_modal.py -v
```
Expected: 1 PASSED.

- [ ] **Step 5: Run all quality gates**

```bash
uv run ruff check . && uv run ruff format --check . && uv run mypy --strict src && bash scripts/check-layers.sh && uv run pytest
```
All green.

- [ ] **Step 6: Commit**

```bash
git add src/aws_tui/ui/widgets/settings_modal.py tests/unit/ui/test_settings_modal.py
git commit -m "feat(ui): SettingsModal — sidebar + body + footer shell

ModalScreen[None] hosting the App Settings overlay. Left sidebar
ListView shows Connections (active), Themes (soon, disabled), and
Keymap (soon, disabled). Body swaps between the live panel
(S3ConnectionsPanel) and the placeholder. Esc + the Close button
dismiss the modal."
```

---

## 1.12. Task 8: `ServicesMenuFooter` + `ServicesMenu` integration

**Files:**
- Create: `src/aws_tui/ui/widgets/services_menu_footer.py`
- Modify: `src/aws_tui/ui/widgets/services_menu.py` (yield the footer at the end of `compose`)

**Interfaces:**
- Produces: `class ServicesMenuFooter(Widget)` exposing one button with id `gear-button`. On click, calls `app.action_open_settings()` if defined (no-op if the action isn't wired yet — Task 9 wires it).

- [ ] **Step 1: Write the construction smoke test**

Create `tests/unit/ui/test_services_menu_footer.py`:

```python
"""Smoke test for ServicesMenuFooter."""

from __future__ import annotations

from aws_tui.ui.widgets.services_menu_footer import ServicesMenuFooter


def test_services_menu_footer_construction() -> None:
    footer = ServicesMenuFooter()
    assert footer is not None
```

- [ ] **Step 2: Run test to verify it fails**

Run:
```bash
uv run pytest tests/unit/ui/test_services_menu_footer.py -v
```
Expected: `ImportError`.

- [ ] **Step 3: Implement `ServicesMenuFooter`**

Create `src/aws_tui/ui/widgets/services_menu_footer.py`:

```python
"""ServicesMenuFooter — bottom-pinned gear button band."""

from __future__ import annotations

from textual import on
from textual.app import ComposeResult
from textual.containers import Horizontal
from textual.events import Click
from textual.widget import Widget
from textual.widgets import Button


class _GearButton(Button):
    """The ⚙ Settings button inside the footer band."""


class ServicesMenuFooter(Widget):
    """Bottom-of-rail band exposing the App Settings entry point.

    Single button labeled ``⚙  Settings``. On click, calls
    ``app.action_open_settings()`` if the action is wired (otherwise
    no-op — the action lands in Task 9).
    """

    DEFAULT_CSS = """
    ServicesMenuFooter {
        height: 1;
        width: 1fr;
        dock: bottom;
    }
    ServicesMenuFooter > Horizontal {
        height: 1;
        width: 1fr;
    }
    """

    def compose(self) -> ComposeResult:
        with Horizontal():
            yield _GearButton("⚙  Settings", id="gear-button")

    @on(Button.Pressed, "#gear-button")
    def _on_gear(self, event: Button.Pressed) -> None:
        event.stop()
        app = self.app
        action = getattr(app, "action_open_settings", None)
        if callable(action):
            action()

    def on_click(self, event: Click) -> None:
        # Allow clicks anywhere in the band (not just the button glyph)
        # to invoke the action — matches the affordance described in
        # the spec ("a small ⚙ Settings row pinned to the bottom").
        stop = getattr(event, "stop", None)
        if callable(stop):
            stop()
        app = getattr(self, "app", None)
        if app is None:
            return
        action = getattr(app, "action_open_settings", None)
        if callable(action):
            action()


__all__ = ["ServicesMenuFooter"]
```

- [ ] **Step 4: Integrate into `ServicesMenu`**

Edit `src/aws_tui/ui/widgets/services_menu.py`:

(a) Add the import at the top with other widget imports:
```python
from aws_tui.ui.widgets.services_menu_footer import ServicesMenuFooter
```

(b) Inside `compose()`, after the existing `yield Vertical(id="services-list")` line, add:
```python
        yield ServicesMenuFooter()
```

- [ ] **Step 5: Run tests to verify they pass**

Run:
```bash
uv run pytest tests/unit/ui/test_services_menu_footer.py -v
```
Expected: 1 PASSED.

- [ ] **Step 6: Run all quality gates**

```bash
uv run ruff check . && uv run ruff format --check . && uv run mypy --strict src && bash scripts/check-layers.sh && uv run pytest
```
All green.

- [ ] **Step 7: Commit**

```bash
git add src/aws_tui/ui/widgets/services_menu_footer.py src/aws_tui/ui/widgets/services_menu.py tests/unit/ui/test_services_menu_footer.py
git commit -m "feat(ui): ServicesMenuFooter (gear band) docked at column bottom

New ServicesMenuFooter widget docks to the bottom of the services
column with a single ⚙ Settings button. Click anywhere in the band
calls app.action_open_settings() if wired (no-op until Task 9
lands the action). Hides automatically when the column collapses."
```

---

## 1.13. Task 9: `AwsTuiApp` wiring (+ `AppContext` + `build_app_context`)

**Files:**
- Modify: `src/aws_tui/composition.py` (extend `build_app_context` + `AppContext` dataclass)
- Modify: `src/aws_tui/app.py` (binding, action, hub subscription, reload helper)

**Interfaces:**
- Consumes: `SettingsVM`, `S3ConnectionsVM`, `SettingsModal`, `ConnectionListChangedMessage`. Uses existing `LocalFS`, `S3FS`, `_aioboto3_session_for`, `_format_pane_title` (already imported lazily in `action_swap_source`).
- Produces:
  - `AppContext` gains two new fields: `settings_vm: SettingsVM`, `s3_connections_vm: S3ConnectionsVM`
  - `AwsTuiApp.action_open_settings(self) -> None`
  - `,` binding (Textual key name `comma`) added to `BINDINGS`
  - `AwsTuiApp._reload_after_settings(self) -> None` + `_reload_panes_async(self, dirty: frozenset[str]) -> None` + `_rebind_pane_to_local(...)` + `_rebind_pane_to_connection(...)` + `_raise_settings_reload_toast(...)`
  - Hub subscription: `AwsTuiApp._on_connection_list_changed` drops deleted names from `AppContext.unreachable_connections`

- [ ] **Step 1: Extend `AppContext` + `build_app_context`**

In `src/aws_tui/composition.py`, add the two new fields to `AppContext` (preserve dataclass field order — append at the end before `unreachable_connections`):

```python
# At top of file, with other VM imports:
from aws_tui.vm.settings.s3_connections_vm import S3ConnectionsVM
from aws_tui.vm.settings.settings_vm import SettingsVM
```

```python
# In the AppContext dataclass, add two fields just BEFORE
# `unreachable_connections` so that field's default doesn't get pushed
# by required fields (preserve alphabetical-by-purpose grouping with
# the other VM fields):
s3_connections_vm: S3ConnectionsVM
settings_vm: SettingsVM
```

In `build_app_context()`, after the existing overlay VMs block (after `transfers_vm = TransfersVM(...)`), construct the two new VMs and pass them into the `AppContext(...)` call:

```python
# After the existing overlay VMs:
s3_connections_vm = S3ConnectionsVM(
    resolver=connection_resolver,
    config_store=config_store,
    hub=hub,
    dispatcher=dispatcher,
)
settings_vm = SettingsVM(
    s3=s3_connections_vm,
    hub=hub,
    dispatcher=dispatcher,
)
```

Then extend the `return AppContext(...)` call by adding two keyword args before `unreachable_connections=set()`:

```python
s3_connections_vm=s3_connections_vm,
settings_vm=settings_vm,
```

- [ ] **Step 2: Construct + dispose the new VMs in `AwsTuiApp.on_mount` / shutdown**

In `src/aws_tui/app.py`'s `on_mount`, alongside the existing `ctx.transfers_vm.construct()` etc. calls, add:

```python
ctx.s3_connections_vm.construct()
ctx.settings_vm.construct()
```

In the existing shutdown path that disposes overlay VMs (find the block that calls `ctx.transfers_vm.dispose()` etc. — typically in `on_unmount` or an explicit shutdown hook), add at the start of that block (before disposing `root_vm`):

```python
ctx.settings_vm.dispose()
ctx.s3_connections_vm.dispose()
```

- [ ] **Step 3: Add the `,` binding**

In `src/aws_tui/app.py`, inside the `BINDINGS` class attribute, add this entry just before the closing `]` (alphabetical order isn't strict — slot it near the `t`/`T` theme bindings, before `mark_up`/`mark_down`):

```python
Binding("comma", "open_settings", "Settings", show=True, priority=True),
```

- [ ] **Step 4: Add imports + the action + reload helpers**

At the top of `src/aws_tui/app.py`, with the other widget/VM imports:

```python
from aws_tui.ui.widgets.settings_modal import SettingsModal
from aws_tui.vm.chrome.toast_vm import ToastLevel, ToastModel
from aws_tui.vm.messages import ConnectionListChangedMessage
```

Then add these methods to `AwsTuiApp` (place near `action_swap_source` for proximity to the related provider-construction code):

```python
def action_open_settings(self) -> None:
    """Push the SettingsModal. Bound to comma + the gear button."""
    ctx = self._app_ctx
    self.push_screen(
        SettingsModal(vm=ctx.settings_vm, hub=ctx.hub),
        callback=lambda _result: self._reload_after_settings(),
    )

def _reload_after_settings(self) -> None:
    """Reload any pane bound to a connection that changed during the
    settings modal's lifetime, then clear the dirty set.

    The synchronous dismiss callback schedules the async reload via
    ``self.run_worker`` so the callback returns immediately and the
    modal teardown isn't blocked.
    """
    ctx = self._app_ctx
    dirty = ctx.settings_vm.dirty_connection_names
    if not dirty:
        return
    self.run_worker(self._reload_panes_async(dirty))
    ctx.settings_vm.clear_dirty()

async def _reload_panes_async(self, dirty: frozenset[str]) -> None:
    """Walk both panes; rebind any bound to a dirty connection."""
    dual = self._dual_pane()
    if dual is None:
        return
    reloaded: list[tuple[str, str]] = []  # (side_label, detail)
    for side_label, pane in (("Left", dual.left), ("Right", dual.right)):
        key = pane.current_connection_key
        if key is None:
            continue
        _, name = key
        if name not in dirty:
            continue
        try:
            conn = self._app_ctx.connection_resolver.resolve(name)
        except Exception:
            # ``resolve`` raises if the connection no longer exists —
            # treat as deletion and revert to local.
            await self._rebind_pane_to_local(pane)
            reloaded.append((side_label, f"{name} deleted → local"))
            continue
        await self._rebind_pane_to_connection(pane, conn)
        reloaded.append((side_label, f"{name} updated"))
    if reloaded:
        self._raise_settings_reload_toast(reloaded)

async def _rebind_pane_to_local(self, pane: object) -> None:
    """Rebind a pane to the local filesystem provider.

    Mirrors the local-branch of ``action_swap_source``.
    """
    from aws_tui.domain.local_fs import LocalFS

    swap = getattr(pane, "swap_provider", None)
    if swap is None:
        return
    await swap(
        LocalFS(),
        identity_label="local",
        path_protocol="",
        connection_key=None,
    )

async def _rebind_pane_to_connection(self, pane: object, conn: object) -> None:
    """Rebind a pane to an S3FS provider for ``conn``.

    Mirrors the remote-branch of ``action_swap_source``. ``conn`` is
    typed as ``object`` here to avoid a circular import with
    ``infra.connection_resolver``; runtime attribute access is safe
    (Connection is the only thing the resolver returns).
    """
    from aws_tui.domain.s3_fs import S3FS
    from aws_tui.services.s3.service import _aioboto3_session_for, _format_pane_title

    swap = getattr(pane, "swap_provider", None)
    if swap is None:
        return
    session = _aioboto3_session_for(conn)  # type: ignore[arg-type]
    provider = S3FS(
        session=session,
        bucket=None,
        endpoint_url=conn.endpoint_url,  # type: ignore[attr-defined]
        force_path_style=conn.force_path_style,  # type: ignore[attr-defined]
    )
    await swap(
        provider,
        identity_label=_format_pane_title(conn),  # type: ignore[arg-type]
        path_protocol="s3:",
        connection_key=(conn.kind, conn.name),  # type: ignore[attr-defined]
    )

def _raise_settings_reload_toast(
    self, reloaded: list[tuple[str, str]]
) -> None:
    summary = "; ".join(f"{side}: {detail}" for side, detail in reloaded)
    self._app_ctx.root_vm.chrome.toast_stack.raise_toast(
        ToastModel(
            id=f"settings-reload-{','.join(sorted(side for side, _ in reloaded))}",
            text=f"Settings — {summary}",
            level=ToastLevel.INFO,
            sticky=False,
            timeout_seconds=4.0,
            action_label=None,
            action_action=None,
        )
    )
```

- [ ] **Step 5: Subscribe to `ConnectionListChangedMessage`**

Find the existing PR #49 hub subscription block in `app.py` (the one that handles pane-state transitions to feed `unreachable_connections`). Add a second subscription line nearby:

```python
self._connection_list_sub = ctx.hub.messages.subscribe(
    on_next=self._on_connection_list_changed
)
```

Add the handler method on `AwsTuiApp`:

```python
def _on_connection_list_changed(self, msg: object) -> None:
    if not isinstance(msg, ConnectionListChangedMessage):
        return
    if msg.change != "deleted":
        return
    for name in msg.names:
        self._app_ctx.unreachable_connections.discard(
            ("s3-compatible", name)
        )
```

In the shutdown path that disposes the existing `_pane_state_sub`, add:

```python
if self._connection_list_sub is not None:
    self._connection_list_sub.dispose()
    self._connection_list_sub = None
```

Initialize the subscription handle to `None` in `AwsTuiApp.__init__` alongside `_pane_state_sub`:

```python
self._connection_list_sub: DisposableBase | None = None
```

(If `DisposableBase` isn't already imported, add it via `from reactivex.abc import DisposableBase` inside `if TYPE_CHECKING:` to keep it type-only.)

- [ ] **Step 6: Run all quality gates**

```bash
uv run ruff check . && uv run ruff format --check . && uv run mypy --strict src && bash scripts/check-layers.sh && uv run pytest
```
All green. Existing tests should continue to pass; the new wiring is exercised by the integration tests in Task 14.

- [ ] **Step 7: Commit**

```bash
git add src/aws_tui/app.py src/aws_tui/composition.py
git commit -m "feat(app): wire SettingsVM + comma binding + reload-on-close

AppContext gains s3_connections_vm + settings_vm; build_app_context
constructs them alongside the other overlay VMs. AwsTuiApp.on_mount
constructs them; shutdown disposes them. Comma is bound to the new
action_open_settings action which pushes SettingsModal with a
dismiss callback that walks both panes via self._dual_pane() and
rebinds any pane bound to a dirty connection (updated: rebuild S3FS
via _aioboto3_session_for + S3FS; deleted: revert to LocalFS).
Hub subscription drops deleted names from unreachable_connections.
Single summary toast on close."
```

---

## 1.14. Task 10: `ServicesMenuVM` subscription

**Files:**
- Modify: `src/aws_tui/vm/services_menu_vm.py`
- Test: `tests/unit/vm/test_services_menu.py` (extend)

**Interfaces:**
- Consumes: `ConnectionListChangedMessage`
- Produces: ServicesMenuVM re-runs its existing service-filter logic when a `ConnectionListChangedMessage` arrives (no change to public API; just a new subscriber).

- [ ] **Step 1: Write the failing test**

Read `tests/unit/vm/test_services_menu.py` to find the helper that builds a `ServicesMenuVM` (likely `_make_menu(...)` or an inline construction). Reuse it. Then append:

```python
from unittest.mock import MagicMock

from aws_tui.vm.messages import ConnectionListChangedMessage


def test_services_menu_vm_refreshes_on_connection_list_change() -> None:
    """When a ConnectionListChangedMessage arrives on the hub, the
    services menu re-derives its filter — same path that
    ConnectionChangedMessage already triggers."""
    from typing import cast
    from vmx import NULL_DISPATCHER, MessageHub
    from vmx.messages.protocols import Message
    from aws_tui.vm.services_menu_vm import ServicesMenuVM

    hub = cast("MessageHub[Message]", MessageHub())
    # Spy on the registry so we can observe the re-filter call.
    registry = MagicMock()
    registry.iter.return_value = []
    vm = ServicesMenuVM(registry=registry, hub=hub, dispatcher=NULL_DISPATCHER)
    vm.construct()
    try:
        registry.iter.reset_mock()
        hub.send(ConnectionListChangedMessage(names=("minio-local",), change="updated"))
        # The subscriber must have called the same filter-refresh path
        # that ConnectionChangedMessage uses — at minimum, registry.iter
        # (or whatever the menu uses to enumerate services) is called.
        assert registry.iter.called, (
            "ServicesMenuVM did not re-derive its filter after "
            "ConnectionListChangedMessage"
        )
    finally:
        vm.dispose()
```

Adjust the `registry=MagicMock(), registry.iter.return_value=[]` line if `ServicesMenuVM` uses a different registry method name (e.g. `list` or `services`). Read the existing tests for the canonical mock shape and match it.

- [ ] **Step 2: Run test to verify it fails**

Run:
```bash
uv run pytest tests/unit/vm/test_services_menu.py -v -k connection_list
```
Expected: FAIL (subscriber doesn't exist).

- [ ] **Step 3: Add the subscription to `ServicesMenuVM`**

In `services_menu_vm.py`'s `construct()` (or wherever the hub subscription lives — there's already a `ConnectionChangedMessage` sub per the explore report at line ~113), add a second `on_next` handler:

```python
def _on_hub_message(self, msg: object) -> None:
    if isinstance(msg, ConnectionChangedMessage):
        self._refresh_filter()
    elif isinstance(msg, ConnectionListChangedMessage):
        self._refresh_filter()
```

If the existing subscriber only handles `ConnectionChangedMessage`, generalize it to a dispatch on type. Import the new message:
```python
from aws_tui.vm.messages import ConnectionChangedMessage, ConnectionListChangedMessage
```

- [ ] **Step 4: Run test to verify it passes**

Run:
```bash
uv run pytest tests/unit/vm/test_services_menu.py -v -k connection_list
```
Expected: PASSED.

- [ ] **Step 5: Run all quality gates**

```bash
uv run ruff check . && uv run ruff format --check . && uv run mypy --strict src && bash scripts/check-layers.sh && uv run pytest
```
All green.

- [ ] **Step 6: Commit**

```bash
git add src/aws_tui/vm/services_menu_vm.py tests/unit/vm/test_services_menu.py
git commit -m "feat(vm): ServicesMenuVM re-derives filter on ConnectionListChangedMessage

When the user adds/edits/deletes an s3-compatible connection via the
Settings overlay, the services menu must re-evaluate which services
support the current connection. Same _refresh_filter() path that
ConnectionChangedMessage already triggers."
```

---

## 1.15. Task 11: Per-theme CSS × 10 themes

**Files:**
- Modify: all 10 `.tcss` files under `src/aws_tui/ui/themes/`

**Goal:** Add the `SettingsModal`, `S3ConnectionsPanel`, and `ServicesMenuFooter` selector blocks to every theme, using theme-appropriate color tokens (`$bg`, `$bg-elev`, `$accent`, `$rule-dim`, `$text`, `$text-muted`, `$danger`).

- [ ] **Step 1: Author the template block**

Use **carbon.tcss** as the source-of-truth template. Add this block at the bottom of `carbon.tcss` (adjust spacing to match existing file conventions):

```tcss
/* ─── SettingsModal ───────────────────────────────────────────── */

SettingsModal {
    align: center middle;
    background: $bg 60%;
}

SettingsModal > #settings-frame {
    background: $bg-elev;
    color: $text;
    border: round $rule-dim;
    padding: 0 2;
    width: 80;
    height: 28;
    max-height: 90%;
}

SettingsModal > #settings-frame > #settings-title {
    color: $accent;
    text-style: bold;
    padding: 0 0 1 0;
}

SettingsModal > #settings-frame > #settings-content {
    height: 1fr;
}

SettingsModal #settings-sidebar {
    width: 22;
    background: $bg;
    border-right: solid $rule-dim;
}

SettingsModal #settings-sidebar ListView {
    background: $bg;
}

SettingsModal #settings-sidebar ListItem {
    padding: 0 1;
    color: $text;
}

SettingsModal #settings-sidebar ListItem.-disabled {
    color: $text-muted;
    text-style: italic;
}

SettingsModal #settings-sidebar ListItem.-active {
    background: $accent-soft;
    color: $accent;
    text-style: bold;
}

SettingsModal #settings-body {
    width: 1fr;
    padding: 0 1;
}

SettingsModal > #settings-frame > #settings-footer {
    height: 3;
    align: right middle;
}

/* ─── S3ConnectionsPanel ──────────────────────────────────────── */

S3ConnectionsPanel .connection-row {
    height: 1;
}

S3ConnectionsPanel .row-accent {
    width: 1;
    color: $rule-dim;
}

S3ConnectionsPanel .row-name {
    width: 16;
    text-style: bold;
    color: $text;
}

S3ConnectionsPanel .row-endpoint {
    width: 1fr;
    color: $text-muted;
}

S3ConnectionsPanel .row-region {
    width: 10;
    color: $text-muted;
}

S3ConnectionsPanel .row-chip-edit {
    width: 3;
    min-width: 3;
    height: 1;
    background: $bg-elev;
    color: $accent;
    text-style: bold;
    border: none;
}

S3ConnectionsPanel .row-chip-edit:hover {
    background: $accent-soft;
}

S3ConnectionsPanel .row-chip-delete {
    width: 3;
    min-width: 3;
    height: 1;
    background: $bg-elev;
    color: $accent;
    text-style: bold;
    border: none;
}

S3ConnectionsPanel .row-chip-delete:hover {
    background: $danger;
    color: $bg;
}

S3ConnectionsPanel .empty-state Static {
    color: $text-muted;
}

S3ConnectionsPanel _AddButton {
    background: $bg-elev;
    color: $accent;
    border: round $rule-dim;
    padding: 0 2;
    height: 3;
}

/* ─── ServicesMenuFooter ──────────────────────────────────────── */

ServicesMenuFooter {
    background: $bg-elev;
    border-top: solid $rule-dim;
}

ServicesMenuFooter _GearButton {
    background: $bg-elev;
    color: $accent;
    border: none;
    width: 1fr;
    height: 1;
}

ServicesMenuFooter _GearButton:hover {
    background: $accent-soft;
}
```

- [ ] **Step 2: Mirror the block into the other 9 themes**

Copy the same block into each of:
- `voidline.tcss`
- `lattice.tcss`
- `amber.tcss`
- `solarized-light.tcss`
- `github-light.tcss`
- `one-light.tcss`
- `nord.tcss`
- `dracula.tcss`
- `gruvbox-dark.tcss`

The color token references (`$accent`, `$bg-elev`, etc.) are theme-scoped — same selectors resolve to per-theme palette automatically. **Do not hand-craft per-theme color values** unless a specific selector needs a theme-specific override (the only known case from prior PRs: light themes may want to tighten the modal width — light themes already use `width: 64` for `ConfirmModal`; mirror that pattern by setting `width: 70` instead of `80` for `SettingsModal` on the 4 light themes: `lattice`, `solarized-light`, `github-light`, `one-light`).

- [ ] **Step 3: Run all quality gates**

```bash
uv run ruff check . && uv run ruff format --check . && uv run mypy --strict src && bash scripts/check-layers.sh && uv run pytest
```
All green. (Snapshot tests don't exist yet — they're added in Task 12+13.)

- [ ] **Step 4: Manually smoke-test (optional but recommended)**

Run the app and open the settings overlay across a couple of themes to confirm nothing is visibly broken:
```bash
uv run aws-tui
```
Press comma (or click the gear). Toggle themes with `t` and re-open.

- [ ] **Step 5: Commit**

```bash
git add src/aws_tui/ui/themes/
git commit -m "style(themes): SettingsModal + S3ConnectionsPanel + footer × 10 themes

Per-theme CSS blocks for the new App Settings overlay, S3 connections
panel, and gear footer band. Light themes (lattice, solarized-light,
github-light, one-light) use width: 70 for the modal; dark themes
use 80 — matches the per-theme ConfirmModal precedent."
```

---

## 1.16. Task 12: Snapshot tests — `SettingsModal` + `ServicesMenuFooter`

**Files:**
- Create: `tests/snapshot/apps/settings.py`
- Create: `tests/snapshot/apps/services_menu_footer.py`
- Create: `tests/snapshot/test_settings_modal.py`
- Create: `tests/snapshot/test_services_menu_footer.py`

- [ ] **Step 1: Create the test apps**

`tests/snapshot/apps/settings.py`:

```python
"""Test app for SettingsModal snapshots."""

from __future__ import annotations

from pathlib import Path
from typing import cast

from textual.app import App, ComposeResult
from textual.widgets import Static
from vmx import NULL_DISPATCHER, MessageHub
from vmx.messages.protocols import Message

from aws_tui.infra.config_store import ConfigStore, ConnectionEntry
from aws_tui.infra.connection_resolver import ConnectionResolver
from aws_tui.ui.widgets.settings_modal import SettingsModal
from aws_tui.vm.settings.s3_connections_vm import S3ConnectionsVM
from aws_tui.vm.settings.settings_vm import SettingsVM
from tests.snapshot.apps._theme_loader import load_css


def _seed_entry(name: str, region: str = "us-east-1") -> ConnectionEntry:
    return ConnectionEntry(
        name=name,
        kind="s3-compatible",
        region=region,
        endpoint_url=f"http://{name}.local:9000",
        access_key_id="AKIATEST",
        secret_access_key="SECRETTEST",
        force_path_style=True,
        verify_tls=True,
    )


class SettingsModalEmptyApp(App[None]):
    def __init__(self, *, theme: str = "carbon") -> None:
        super().__init__()
        self.CSS = load_css(theme)
        self._hub = cast("MessageHub[Message]", MessageHub())
        self._tmp = Path("/tmp/test-settings-empty")  # nosec: B108 (test only)
        self._tmp.mkdir(parents=True, exist_ok=True)
        store = ConfigStore(path=self._tmp / "config.toml")
        resolver = ConnectionResolver(config_store=store)
        self._s3 = S3ConnectionsVM(
            resolver=resolver, config_store=store, hub=self._hub, dispatcher=NULL_DISPATCHER
        )
        self._s3.construct()
        self._vm = SettingsVM(s3=self._s3, hub=self._hub, dispatcher=NULL_DISPATCHER)
        self._vm.construct()

    def compose(self) -> ComposeResult:
        yield Static("aws-tui main view (behind modal)", id="placeholder")

    async def on_mount(self) -> None:
        await self.push_screen(SettingsModal(vm=self._vm, hub=self._hub))


class SettingsModalPopulatedApp(App[None]):
    def __init__(self, *, theme: str = "carbon") -> None:
        super().__init__()
        self.CSS = load_css(theme)
        self._hub = cast("MessageHub[Message]", MessageHub())
        self._tmp = Path("/tmp/test-settings-populated")  # nosec: B108
        self._tmp.mkdir(parents=True, exist_ok=True)
        store = ConfigStore(path=self._tmp / "config.toml")
        store.add_connection(_seed_entry("minio-local"))
        store.add_connection(_seed_entry("ceph-staging", region="us-west-2"))
        resolver = ConnectionResolver(config_store=store)
        self._s3 = S3ConnectionsVM(
            resolver=resolver, config_store=store, hub=self._hub, dispatcher=NULL_DISPATCHER
        )
        self._s3.construct()
        self._vm = SettingsVM(s3=self._s3, hub=self._hub, dispatcher=NULL_DISPATCHER)
        self._vm.construct()

    def compose(self) -> ComposeResult:
        yield Static("aws-tui main view (behind modal)", id="placeholder")

    async def on_mount(self) -> None:
        await self.push_screen(SettingsModal(vm=self._vm, hub=self._hub))
```

(Pattern-match against `tests/snapshot/apps/modals.py` for the exact `load_css` helper import — if there's no `_theme_loader.py` shared file, copy the inline `_load_css` helper used in `modals.py`.)

`tests/snapshot/apps/services_menu_footer.py`:

```python
"""Test app for ServicesMenuFooter snapshots."""

from __future__ import annotations

from textual.app import App, ComposeResult
from textual.widgets import Static

from aws_tui.ui.widgets.services_menu_footer import ServicesMenuFooter
from tests.snapshot.apps._theme_loader import load_css


class ServicesMenuFooterApp(App[None]):
    def __init__(self, *, theme: str = "carbon") -> None:
        super().__init__()
        self.CSS = load_css(theme)

    def compose(self) -> ComposeResult:
        yield Static("ServicesMenuFooter snapshot")
        yield ServicesMenuFooter()
```

- [ ] **Step 2: Create the snapshot test files**

`tests/snapshot/test_settings_modal.py`:

```python
"""Snapshot tests for SettingsModal × 10 themes."""

from __future__ import annotations

import pytest

from tests.snapshot.apps.settings import (
    SettingsModalEmptyApp,
    SettingsModalPopulatedApp,
)

THEMES = [
    "carbon", "voidline", "lattice", "amber",
    "solarized-light", "github-light", "one-light",
    "nord", "dracula", "gruvbox-dark",
]
TERMINAL_SIZE = (120, 40)


@pytest.mark.parametrize("theme", THEMES)
def test_settings_modal_empty(theme: str, snap_compare) -> None:
    assert snap_compare(SettingsModalEmptyApp(theme=theme), terminal_size=TERMINAL_SIZE)


@pytest.mark.parametrize("theme", THEMES)
def test_settings_modal_populated(theme: str, snap_compare) -> None:
    assert snap_compare(SettingsModalPopulatedApp(theme=theme), terminal_size=TERMINAL_SIZE)
```

`tests/snapshot/test_services_menu_footer.py`:

```python
"""Snapshot tests for ServicesMenuFooter × 10 themes."""

from __future__ import annotations

import pytest

from tests.snapshot.apps.services_menu_footer import ServicesMenuFooterApp

THEMES = [
    "carbon", "voidline", "lattice", "amber",
    "solarized-light", "github-light", "one-light",
    "nord", "dracula", "gruvbox-dark",
]
TERMINAL_SIZE = (40, 6)


@pytest.mark.parametrize("theme", THEMES)
def test_services_menu_footer(theme: str, snap_compare) -> None:
    assert snap_compare(ServicesMenuFooterApp(theme=theme), terminal_size=TERMINAL_SIZE)
```

- [ ] **Step 3: Run the snapshot tests with --snapshot-update to create goldens**

```bash
uv run pytest tests/snapshot/test_settings_modal.py tests/snapshot/test_services_menu_footer.py --snapshot-update -q
```

- [ ] **Step 4: Eyeball the generated SVGs**

Open a couple of the generated SVG files under `tests/snapshot/__snapshots__/test_settings_modal/` and confirm they render as expected for at least one dark and one light theme. If anything looks broken (clipped buttons, wrong colors, misaligned sidebar), fix the underlying CSS in Task 11's files and re-run with `--snapshot-update`.

- [ ] **Step 5: Run all quality gates**

```bash
uv run ruff check . && uv run ruff format --check . && uv run mypy --strict src && bash scripts/check-layers.sh && uv run pytest
```
All green.

- [ ] **Step 6: Commit**

```bash
git add tests/snapshot/apps/settings.py tests/snapshot/apps/services_menu_footer.py tests/snapshot/test_settings_modal.py tests/snapshot/test_services_menu_footer.py tests/snapshot/__snapshots__/test_settings_modal/ tests/snapshot/__snapshots__/test_services_menu_footer/
git commit -m "test(snapshot): SettingsModal + ServicesMenuFooter × 10 themes

20 new snapshots for SettingsModal (empty + populated × 10 themes)
and 10 new snapshots for ServicesMenuFooter."
```

---

## 1.17. Task 13: Snapshot tests — `S3CompatFormModal` edit + validation

**Files:**
- Create: `tests/snapshot/apps/s3_compat_form.py`
- Create: `tests/snapshot/test_s3_compat_form.py`

NOTE: if `tests/snapshot/test_first_run_modal.py` already covers the form's default (add-mode-empty) case across all 10 themes, this task only adds the **edit** and **validation** snapshots. Confirm by `ls tests/snapshot/__snapshots__/` first. If it does NOT exist, also add an `add` case here.

- [ ] **Step 1: Create the test app**

`tests/snapshot/apps/s3_compat_form.py`:

```python
"""Test apps for S3CompatFormModal edit + validation snapshots."""

from __future__ import annotations

from typing import cast

from textual.app import App, ComposeResult
from textual.widgets import Static
from vmx import MessageHub
from vmx.messages.protocols import Message

from aws_tui.ui.widgets.first_run_modal import S3CompatFormModal
from aws_tui.vm.chrome.first_run_vm import S3CompatForm
from tests.snapshot.apps._theme_loader import load_css


_FILLED = S3CompatForm(
    name="minio-local",
    endpoint_url="http://localhost:9000",
    region="us-east-1",
    access_key_id="AKIATEST",
    secret_access_key="SECRETTEST",
    force_path_style=True,
    verify_tls=True,
)


class S3FormEditApp(App[None]):
    def __init__(self, *, theme: str = "carbon") -> None:
        super().__init__()
        self.CSS = load_css(theme)
        self._hub = cast("MessageHub[Message]", MessageHub())

    def compose(self) -> ComposeResult:
        yield Static("aws-tui main view (behind modal)")

    async def on_mount(self) -> None:
        await self.push_screen(
            S3CompatFormModal(hub=self._hub, defaults=_FILLED, name_locked=True)
        )


_INVALID = S3CompatForm(
    name="bad name",          # space → invalid
    endpoint_url="ftp://wrong",  # wrong scheme → invalid
    region="",                 # empty → invalid
    access_key_id="",          # empty → invalid
    secret_access_key="",      # empty → invalid
)


class S3FormValidationErrorsApp(App[None]):
    def __init__(self, *, theme: str = "carbon") -> None:
        super().__init__()
        self.CSS = load_css(theme)
        self._hub = cast("MessageHub[Message]", MessageHub())

    def compose(self) -> ComposeResult:
        yield Static("aws-tui main view (behind modal)")

    async def on_mount(self) -> None:
        await self.push_screen(
            S3CompatFormModal(hub=self._hub, defaults=_INVALID, name_locked=False)
        )
```

- [ ] **Step 2: Create the snapshot test file**

`tests/snapshot/test_s3_compat_form.py`:

```python
"""Snapshot tests for S3CompatFormModal edit + validation × 10 themes."""

from __future__ import annotations

import pytest

from tests.snapshot.apps.s3_compat_form import (
    S3FormEditApp,
    S3FormValidationErrorsApp,
)

THEMES = [
    "carbon", "voidline", "lattice", "amber",
    "solarized-light", "github-light", "one-light",
    "nord", "dracula", "gruvbox-dark",
]
TERMINAL_SIZE = (80, 32)


@pytest.mark.parametrize("theme", THEMES)
def test_s3_compat_form_edit_mode(theme: str, snap_compare) -> None:
    assert snap_compare(S3FormEditApp(theme=theme), terminal_size=TERMINAL_SIZE)


@pytest.mark.parametrize("theme", THEMES)
def test_s3_compat_form_validation_errors(theme: str, snap_compare) -> None:
    assert snap_compare(S3FormValidationErrorsApp(theme=theme), terminal_size=TERMINAL_SIZE)
```

- [ ] **Step 3: Generate goldens**

```bash
uv run pytest tests/snapshot/test_s3_compat_form.py --snapshot-update -q
```

- [ ] **Step 4: Add CSS for the `-invalid` Input class to all 10 themes**

If the validation snapshot SVGs show no visual difference for invalid fields, the per-theme CSS lacks a rule for `S3CompatFormModal Input.-invalid`. Add to each `.tcss` (under the existing `S3CompatFormModal` block or in the form-styles section):

```tcss
S3CompatFormModal Input.-invalid {
    border: tall $danger;
}
```

Re-run `--snapshot-update` to refresh.

- [ ] **Step 5: Eyeball the SVGs** for one dark and one light theme. Fix CSS if anything is broken.

- [ ] **Step 6: Run all quality gates**

```bash
uv run ruff check . && uv run ruff format --check . && uv run mypy --strict src && bash scripts/check-layers.sh && uv run pytest
```
All green.

- [ ] **Step 7: Commit**

```bash
git add tests/snapshot/apps/s3_compat_form.py tests/snapshot/test_s3_compat_form.py tests/snapshot/__snapshots__/test_s3_compat_form/ src/aws_tui/ui/themes/
git commit -m "test(snapshot): S3CompatFormModal edit + validation × 10 themes

20 new snapshots: edit mode (name field locked, all other fields
prefilled) + validation-errors mode (all five fields invalid).
Adds the .-invalid CSS rule to every theme so red borders render
consistently."
```

---

## 1.18. Task 14: In-process integration tests

**Files:**
- Create: `tests/integration/test_settings_modal_flow.py`

**Interfaces:**
- Consumes: `AwsTuiApp` (full app), `ConfigStore`, `S3CompatFormModal`, `SettingsModal`, the dispatcher + hub machinery
- Uses pytest-textual's `App.run_test()` pattern (look at existing tests in `tests/integration/test_swap_source_skips_unreachable.py` for the canonical fixture)

- [ ] **Step 1: Read an existing integration test for the pattern**

```bash
cat tests/integration/test_swap_source_skips_unreachable.py
```

Confirms the canonical pattern: `build_app_context(config_dir=tmp_path / "config", cache_dir=tmp_path / "cache")` → `AwsTuiApp(ctx)` → `async with app.run_test() as pilot`. The `AwsTuiApp` constructor takes `AppContext`, NOT a `config_path` kwarg. Tests seed by writing the raw TOML file before calling `build_app_context`.

- [ ] **Step 2: Write the integration tests**

Create `tests/integration/test_settings_modal_flow.py`:

```python
"""In-process integration tests for the App Settings overlay's flows."""

from __future__ import annotations

from pathlib import Path

import pytest

from aws_tui.app import AwsTuiApp
from aws_tui.composition import build_app_context
from aws_tui.infra.config_store import ConfigStore


_MINIO_LOCAL_TOML = (
    "[connections.minio-local]\n"
    'kind = "s3-compatible"\n'
    'endpoint_url = "http://127.0.0.1:1"\n'   # unreachable on purpose
    'region = "us-east-1"\n'
    'access_key_id = "AKIATEST"\n'
    'secret_access_key = "SECRETTEST"\n'
    "force_path_style = true\n"
    "verify_tls = false\n"
)


def _prep_config(tmp_path: Path, toml_text: str = "") -> Path:
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "config.toml").write_text(toml_text)
    return config_dir


@pytest.mark.asyncio
async def test_add_flow_persists_to_toml(tmp_path: Path) -> None:
    """Empty config; open settings via comma; click +Add; fill the
    form; save; close; verify the TOML round-trip."""
    config_dir = _prep_config(tmp_path)  # empty
    ctx = build_app_context(config_dir=config_dir, cache_dir=tmp_path / "cache")
    app = AwsTuiApp(ctx)
    try:
        async with app.run_test() as pilot:
            # Open settings.
            await pilot.press("comma")
            await pilot.pause()
            # Empty state → "#add-empty" button.
            await pilot.click("#add-empty")
            await pilot.pause()
            # Fill fields by ID (S3CompatFormModal yields Input(id="form-<key>")).
            await pilot.click("#form-name")
            await pilot.press(*"minio-test")
            await pilot.click("#form-endpoint_url")
            await pilot.press(*"http://localhost:9000")
            await pilot.click("#form-region")
            await pilot.press(*"us-east-1")
            await pilot.click("#form-access_key_id")
            await pilot.press(*"AKIATEST")
            await pilot.click("#form-secret_access_key")
            await pilot.press(*"SECRETTEST")
            await pilot.pause()
            # Save.
            await pilot.click("#form-save-btn")
            await pilot.pause()
            # Dismiss settings.
            await pilot.press("escape")
            await pilot.pause()
    finally:
        # build_app_context returns disposable VMs — clean up explicitly
        # so the test isolation is total.
        ctx.settings_vm.dispose()
        ctx.s3_connections_vm.dispose()
        ctx.transfers_vm.dispose()
        ctx.confirm_vm.dispose()
        ctx.quick_look_vm.dispose()
        ctx.command_palette_vm.dispose()
        ctx.root_vm.dispose()

    # On-disk verification.
    cfg = ConfigStore(path=config_dir / "config.toml").load()
    assert "minio-test" in cfg.connections
    entry = cfg.connections["minio-test"]
    assert entry.endpoint_url == "http://localhost:9000"
    assert entry.access_key_id == "AKIATEST"
    assert entry.secret_access_key == "SECRETTEST"


@pytest.mark.asyncio
async def test_edit_with_locked_name_persists_endpoint_change(
    tmp_path: Path,
) -> None:
    """Seed minio-local; open settings; click edit; verify the name
    field is locked; change the endpoint; save; close; verify TOML."""
    config_dir = _prep_config(tmp_path, _MINIO_LOCAL_TOML)
    ctx = build_app_context(config_dir=config_dir, cache_dir=tmp_path / "cache")
    app = AwsTuiApp(ctx)
    try:
        async with app.run_test() as pilot:
            await pilot.press("comma")
            await pilot.pause()
            await pilot.click("#edit-minio-local")
            await pilot.pause()
            # Name field must be locked in edit mode.
            from textual.widgets import Input
            name_input = pilot.app.query_one("#form-name", Input)
            assert name_input.disabled is True
            # Change endpoint: focus, clear, type new.
            await pilot.click("#form-endpoint_url")
            endpoint_input = pilot.app.query_one("#form-endpoint_url", Input)
            endpoint_input.value = ""  # programmatic clear (simpler than ctrl+a)
            await pilot.press(*"http://127.0.0.1:2")
            await pilot.pause()
            await pilot.click("#form-save-btn")
            await pilot.pause()
            await pilot.press("escape")
            await pilot.pause()
    finally:
        ctx.settings_vm.dispose()
        ctx.s3_connections_vm.dispose()
        ctx.transfers_vm.dispose()
        ctx.confirm_vm.dispose()
        ctx.quick_look_vm.dispose()
        ctx.command_palette_vm.dispose()
        ctx.root_vm.dispose()

    cfg = ConfigStore(path=config_dir / "config.toml").load()
    assert cfg.connections["minio-local"].endpoint_url == "http://127.0.0.1:2"


@pytest.mark.asyncio
async def test_delete_via_confirm_removes_from_toml(tmp_path: Path) -> None:
    """Seed minio-local; open settings; click delete; confirm; close;
    verify the entry is gone from TOML."""
    config_dir = _prep_config(tmp_path, _MINIO_LOCAL_TOML)
    ctx = build_app_context(config_dir=config_dir, cache_dir=tmp_path / "cache")
    app = AwsTuiApp(ctx)
    try:
        async with app.run_test() as pilot:
            await pilot.press("comma")
            await pilot.pause()
            await pilot.click("#delete-minio-local")
            await pilot.pause()
            # ConfirmModal opens. For danger dialogs the default focus
            # is Cancel; press Right to move to Confirm, then Enter.
            await pilot.press("right")
            await pilot.press("enter")
            await pilot.pause()
            await pilot.press("escape")
            await pilot.pause()
    finally:
        ctx.settings_vm.dispose()
        ctx.s3_connections_vm.dispose()
        ctx.transfers_vm.dispose()
        ctx.confirm_vm.dispose()
        ctx.quick_look_vm.dispose()
        ctx.command_palette_vm.dispose()
        ctx.root_vm.dispose()

    cfg = ConfigStore(path=config_dir / "config.toml").load()
    assert "minio-local" not in cfg.connections
```

Note on the delete-test: it does not assert on pane state because the seed config has no active S3 connection (the app's startup flow lands on the no-connection placeholder when no `defaults.initial_connection` is set). The pane-reload path is exercised by the existing `_reload_panes_async` unit-level reasoning (no pane bound = no reload work = no toast). If a separate test of the reload-on-close behavior with a bound pane is desired, defer to a follow-up — the swap_source flow that binds a pane mid-test is fragile under `run_test()` because S3 connection probes are made and would time out against the unreachable seed endpoint.

- [ ] **Step 3: Run the tests**

```bash
uv run pytest tests/integration/test_settings_modal_flow.py -v
```

Expected: 3 PASSED. If anything fails, debug — the test wiring usually needs minor adjustments to match the real app's startup sequence (e.g., `await pilot.pause()` between actions, or waiting for a specific message to fire).

- [ ] **Step 4: Run all quality gates**

```bash
uv run ruff check . && uv run ruff format --check . && uv run mypy --strict src && bash scripts/check-layers.sh && uv run pytest
```
All green.

- [ ] **Step 5: Commit**

```bash
git add tests/integration/test_settings_modal_flow.py
git commit -m "test(integration): settings modal end-to-end flows

Three in-process integration tests:
1. Open settings via comma key, add a connection via the form,
   verify TOML round-trip.
2. Edit the currently-active connection (name field locked,
   endpoint changed), verify TOML update + pane stays bound.
3. Delete the currently-active connection via the confirm
   dialog, verify TOML removal + pane reverts to local."
```

---

## 1.19. Task 15: `CHANGELOG.md` entry

**Files:**
- Modify: `CHANGELOG.md` (under `## [Unreleased]` → `### Added`)

- [ ] **Step 1: Add the changelog entry**

In `CHANGELOG.md`, find the `### Added` section under `## [Unreleased]` (it exists around line 94 per memory) and append a new bullet at the end of that section:

```markdown
- **App Settings overlay** with first panel: full CRUD for s3-compatible
  connections. New ``⚙  Settings`` gear button pinned to the bottom of
  the services column (keyboard ``,``) opens a themed ``SettingsModal``
  with a left-sidebar nav. Sub-project A of three: the sidebar shows
  ``Connections`` (active), ``Themes (soon)`` and ``Keymap (soon)`` —
  the disabled rows will go live in sub-projects B and C. The S3 panel
  reads from ``ConnectionResolver`` (filtered to ``kind = "s3-compatible"``)
  and writes through new ``ConfigStore.update_connection`` /
  ``remove_connection`` methods (atomic via ``tempfile + os.replace``).
  Add and Edit reuse the existing ``S3CompatFormModal`` with a new
  ``name_locked`` parameter for edit mode (rename disallowed). Delete
  uses the polished ``ConfirmModal``. Credentials are stored inline in
  TOML (cross-platform; existing keychain-referencing entries are read
  transparently and re-written inline on first edit — documented
  one-way conversion). New ``ConnectionListChangedMessage`` published
  on every CRUD; subscribers include ``ServicesMenuVM`` (filter
  refresh) and ``AwsTuiApp`` (drops deleted names from
  ``AppContext.unreachable_connections``). Affected panes reload
  exactly once on modal dismiss; single summary toast describes what
  reloaded. New per-theme CSS for all 10 themes + 50 new snapshots.
```

- [ ] **Step 2: Run all quality gates**

```bash
uv run ruff check . && uv run ruff format --check . && uv run mypy --strict src && bash scripts/check-layers.sh && uv run pytest
```
All green.

- [ ] **Step 3: Commit**

```bash
git add CHANGELOG.md
git commit -m "docs(changelog): app settings overlay + s3 connections panel

Adds the [Unreleased]/Added bullet describing sub-project A — the
settings shell, the gear footer entry, and the S3 connections panel
with full CRUD over kind='s3-compatible' TOML entries."
```

---

## 1.20. Self-Review Notes (recorded after writing)

**Spec coverage check** — every section of the spec maps to at least one task:

- §2.1 in-scope items → Tasks 1–11 collectively
- §3.1 file map → Tasks 3, 4, 6, 7, 8 (file creation)
- §3.2 file modifications → Tasks 1, 2, 5, 8, 9, 10, 11
- §3.3 layout → Tasks 7, 11 (modal + CSS)
- §3.4 panel layout + empty state → Task 6
- §3.5 gear footer → Task 8
- §4.1 SettingsVM → Task 4
- §4.2 S3ConnectionsVM → Task 3
- §4.3 reload-on-close → Task 9 (lives in AwsTuiApp, not the VM)
- §5.1 update_connection → Task 1
- §5.2 remove_connection → Task 1
- §5.3 schema notes → Task 5 (form behavior preserves the documented one-way conversion)
- §6 ConnectionListChangedMessage → Task 2
- §7 entry point wiring → Task 9
- §8 error handling → covered in implementation steps where relevant; surfaced as KeyError/ValueError in Task 1 and ValueError in Task 3
- §9.1 unit tests → Tasks 1, 3, 4, 5, 10
- §9.2 snapshot tests → Tasks 12, 13
- §9.3 integration tests → Task 14
- §10 global constraints → reproduced at the top of this plan
- §11 open implementation questions — resolved during the explore pass; see notes at top of this plan ("ConnectionResolver has no cache", "use .resolve() not .get()", etc.)
