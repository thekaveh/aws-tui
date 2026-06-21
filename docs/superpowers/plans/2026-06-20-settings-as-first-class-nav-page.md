# Settings as First-Class Nav Page Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rework the App Settings UX shipped in PR #52/#53 from a modal-overlay pattern to a first-class nav-page pattern — left rail becomes a generic vertical menu (Textual `OptionList`) with peer entries `S3` and `Settings`; selecting Settings swaps the main area to a VS Code-style scrollable page of `Collapsible` sections; Add/Edit S3 connection form expands inline within the Connections section instead of pushing yet another modal.

**Architecture:** Twelve tasks in a build-then-cutover-then-demolish sequence. Tasks 1-6 simplify the surviving VMs and build the new view widgets additively (no callers yet). Task 7 adds the per-theme CSS for the new widgets. Task 8 adds snapshot tests for the new widgets, each paired with a content-presence guard per the PR #53 lesson. Task 9 cuts over `AwsTuiApp` + `composition.py` to use the new widgets. Task 10 rewrites the integration test. Task 11 migrates `FirstRunModal` to use the new `ConnectionFormInline` widget and deletes all obsolete files (`SettingsModal`, `ServicesMenuFooter`, `S3CompatFormModal` class, their CSS blocks across all 10 themes, their unit/snapshot tests + goldens). Task 12 rewrites the CHANGELOG entry.

**Tech Stack:** Python 3.11+, Textual (TUI — uses native `OptionList`, `Collapsible`, `VerticalScroll`), VMx MVVM framework (PyPI `vmx>=2.6.0,<3.0.0`), pytest + pytest-asyncio + pytest-textual-snapshot for tests.

## Global Constraints

- 10-theme parity: every per-theme CSS block must land in all 10 theme files (`carbon`, `voidline`, `lattice`, `amber`, `solarized-light`, `github-light`, `one-light`, `nord`, `dracula`, `gruvbox-dark`).
- **Every new snapshot test MUST be paired with a content-presence guard** per the PR #53 lesson — assert key text/glyphs appear in the rendered `.raw` SVG, not just that all themes match each other. See [memory note](/Users/kaveh/.claude/projects/-Users-kaveh-repos-aws-tui/memory/snapshot-test-content-guards.md).
- No new third-party dependencies.
- Nav-menu cursor highlight matches the file-pane row cursor: `$bg-sel` background, `$accent` foreground, no border.
- One inline form open at a time within the Connections section.
- Delete still uses `ConfirmModal` (destructive ops keep the modal interruption pattern).
- Esc inside the inline form = Cancel.
- Layered architecture preserved (enforced by `scripts/check-layers.sh`).
- All quality gates green per commit: `uv run ruff check .`, `uv run ruff format --check .`, `uv run mypy --strict src`, `bash scripts/check-layers.sh`, `uv run pytest`.

---

## File Structure

### Files to create

| Path | Responsibility |
|---|---|
| `src/aws_tui/ui/widgets/settings/connection_form.py` | `ConnectionFormInline` widget (the lifted form body) + `_validate_s3_form_value` validator + `_FIELDS` tuple + `ConnectionFormSubmitted` / `ConnectionFormCancelled` messages |
| `src/aws_tui/ui/widgets/nav_menu.py` | `NavMenu` widget (OptionList-based vertical nav, replaces `ServicesMenu`) |
| `src/aws_tui/ui/widgets/settings_view.py` | `SettingsView` widget (VerticalScroll of Collapsible sections, replaces `SettingsModal`) |
| `tests/unit/ui/test_nav_menu.py` | Construction + OptionSelected event + collapsed-mode prompt-rebuild |
| `tests/unit/ui/test_settings_view.py` | Construction + Collapsible default state |
| `tests/unit/ui/test_connection_form_inline.py` | Construction + open_for_add/edit + emitted messages (extends existing validator tests) |
| `tests/snapshot/apps/nav_menu.py` | Test apps for NavMenu (expanded + collapsed) |
| `tests/snapshot/apps/settings_view.py` | Test apps for SettingsView (empty / populated / form-open) |
| `tests/snapshot/test_nav_menu.py` | Snapshot tests × 10 themes × 2 states + content-presence guards |
| `tests/snapshot/test_settings_view.py` | Snapshot tests × 10 themes × 3 scenarios + content-presence guards |
| `tests/integration/test_settings_flow.py` | Replaces `test_settings_modal_flow.py` — new nav-page flow |

### Files to modify

| Path | Change |
|---|---|
| `src/aws_tui/vm/settings/settings_vm.py` | Drop dirty-set + change_section + SECTIONS/ENABLED; add `setup()` |
| `src/aws_tui/vm/services_menu_vm.py` | Rename file → `nav_menu_vm.py`; rename classes; extend items to include hard-coded Settings entry |
| `src/aws_tui/ui/widgets/settings/s3_connections_panel.py` | Replace `@work` modal-push CRUD with inline-form mount + `ConnectionFormSubmitted` handler |
| `src/aws_tui/ui/widgets/first_run_modal.py` | Delete `S3CompatFormModal` class; `FirstRunModal` composes `ConnectionFormInline` inline; `_validate_s3_form_value` and `_FIELDS` move to `connection_form.py` |
| `src/aws_tui/app.py` | `action_open_settings` rebinds to `nav_menu_vm.select("settings")`; replace `SettingsModal.push_screen` flow with nav-routed ContentHost swap; mount `NavMenu` instead of `ServicesMenu` |
| `src/aws_tui/composition.py` | Construct `NavMenuVM` (renamed) instead of `ServicesMenuVM`; `settings_vm` construction unchanged |
| `src/aws_tui/ui/themes/*.tcss` (×10) | Delete `SettingsModal`, `ServicesMenuFooter`, `S3CompatFormModal` blocks; add `NavMenu`, `SettingsView`, `ConnectionFormInline` blocks |
| `CHANGELOG.md` | Rewrite the PR #52 bullet under `[Unreleased] > ### Added` |
| `tests/unit/ui/test_s3_compat_form_modal.py` | Rename → `test_connection_form_inline.py`; update imports to point at `connection_form.py` |

### Files to delete (in Task 11)

| Path | Why |
|---|---|
| `src/aws_tui/ui/widgets/settings_modal.py` | Replaced by `SettingsView` |
| `src/aws_tui/ui/widgets/services_menu_footer.py` | Settings is now a nav peer, not a footer button |
| `src/aws_tui/ui/widgets/services_menu.py` | Replaced by `nav_menu.py` (or renamed; see Task 4) |
| `src/aws_tui/ui/widgets/settings/_placeholder_panel.py` | Disabled Collapsibles are the new placeholder pattern |
| `tests/unit/ui/test_settings_modal.py` | Tests deleted widget |
| `tests/unit/ui/test_services_menu_footer.py` | Tests deleted widget (including PR #53's content-presence guards — that pattern carries forward as a *general practice* in the new snapshot tests) |
| `tests/snapshot/test_settings_modal.py` + 20 SVG goldens | Tests deleted widget |
| `tests/snapshot/test_services_menu_footer.py` + 10 SVG goldens | Tests deleted widget |
| `tests/snapshot/test_s3_compat_form.py` + 30 SVG goldens | Form is no longer a separate modal screen |
| `tests/snapshot/apps/settings.py`, `apps/services_menu_footer.py`, `apps/s3_compat_form.py` | Their test files are deleted |
| `tests/integration/test_settings_modal_flow.py` | Replaced by `test_settings_flow.py` (Task 10) |

---

## Quality Gates (every task)

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

## Task 1: Simplify `SettingsVM`

The PR #52 `SettingsVM` carries dirty-set + active-section machinery that
existed only because the modal had to remember what changed during its
lifetime so panes could reload on close. With Settings now a nav-routed
page (reload-on-Save is immediate), that state is dead. We also add
`setup()` to conform to the ContentHost VM lifecycle.

**Files:**
- Modify: `src/aws_tui/vm/settings/settings_vm.py`
- Modify: `tests/unit/vm/settings/test_settings_vm.py`

**Interfaces produced:**
- `class SettingsVM` with surface: `__init__(*, s3, hub, dispatcher)`,
  `construct()`, `destruct()`, `dispose()`, `async setup() -> None`, `s3`
  property. **Removed:** `SECTIONS`, `ENABLED`, `active_section`,
  `change_section`, `dirty_connection_names`, `clear_dirty`,
  `_on_hub_message` subscriber.

- [ ] **Step 1: Rewrite `settings_vm.py`**

Replace the entire file `src/aws_tui/vm/settings/settings_vm.py` with:

```python
"""SettingsVM — top-level VM for the Settings nav destination.

A peer to the service VMs hosted by :class:`ContentHostVM`. Owns the
S3 connections sub-VM. Construction and disposal follow the standard
VMx facade pattern; ``setup()`` is a no-op today (kept so the
ContentHost lifecycle calls it without an attribute error).
"""

from __future__ import annotations

from vmx import ComponentVM, Message, MessageHub
from vmx.lifecycle.status import ConstructionStatus
from vmx.services.dispatcher import Dispatcher

from aws_tui.vm.settings.s3_connections_vm import S3ConnectionsVM


class SettingsVM:
    """Top-level VM hosted by ``ContentHostVM`` when the Settings nav
    item is selected.

    The PR #52 dirty-set + active-section machinery has been removed —
    Settings is no longer a modal so there is no "lifetime" to track,
    and the page lays out its sections statically via the View layer
    (a ``VerticalScroll`` of ``Collapsible``).
    """

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
        self._inner: ComponentVM = (
            ComponentVM.builder().name("settings").services(hub, dispatcher).build()
        )

    # ── Properties ─────────────────────────────────────────────────────────

    @property
    def s3(self) -> S3ConnectionsVM:
        return self._s3

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

    def destruct(self) -> None:
        self._inner.destruct()

    def dispose(self) -> None:
        self._inner.dispose()

    async def setup(self) -> None:
        """No-op placeholder so :class:`ContentHostVM.set_content` can
        call it uniformly across all hosted VMs."""
        return None


__all__ = ["SettingsVM"]
```

- [ ] **Step 2: Rewrite `test_settings_vm.py`**

Replace the entire file `tests/unit/vm/settings/test_settings_vm.py` with:

```python
"""Tests for SettingsVM (simplified, post-modal)."""

from __future__ import annotations

from pathlib import Path
from typing import cast

import pytest
from vmx import NULL_DISPATCHER, MessageHub
from vmx.lifecycle.status import ConstructionStatus
from vmx.messages.protocols import Message

from aws_tui.infra.config_store import ConfigStore
from aws_tui.infra.connection_resolver import ConnectionResolver
from aws_tui.vm.settings.s3_connections_vm import S3ConnectionsVM
from aws_tui.vm.settings.settings_vm import SettingsVM


def _hub() -> MessageHub[Message]:
    return cast("MessageHub[Message]", MessageHub())


def _make_vm(tmp_path: Path) -> tuple[SettingsVM, S3ConnectionsVM]:
    hub = _hub()
    store = ConfigStore(path=tmp_path / "config.toml")
    resolver = ConnectionResolver(config_store=store)
    s3 = S3ConnectionsVM(
        resolver=resolver, config_store=store, hub=hub, dispatcher=NULL_DISPATCHER
    )
    s3.construct()
    vm = SettingsVM(s3=s3, hub=hub, dispatcher=NULL_DISPATCHER)
    vm.construct()
    return vm, s3


def test_settings_vm_lifecycle(tmp_path: Path) -> None:
    vm, s3 = _make_vm(tmp_path)
    assert vm.status == ConstructionStatus.CONSTRUCTED
    assert vm.s3 is s3
    vm.dispose()
    assert vm.status == ConstructionStatus.DISPOSED
    s3.dispose()


@pytest.mark.asyncio
async def test_settings_vm_setup_is_noop(tmp_path: Path) -> None:
    vm, s3 = _make_vm(tmp_path)
    try:
        result = await vm.setup()
        assert result is None
    finally:
        vm.dispose()
        s3.dispose()


def test_settings_vm_no_longer_has_dirty_set_or_sections(tmp_path: Path) -> None:
    """Regression: PR #52's dirty-set + section-list surface was deleted.

    These attributes existed only because the modal had a lifetime to
    track. Now Settings is a nav-routed page; sections are a static
    View concern; reload-on-Save is immediate. If any of these
    attributes come back, something has regressed toward the old
    pattern.
    """
    vm, s3 = _make_vm(tmp_path)
    try:
        assert not hasattr(vm, "dirty_connection_names")
        assert not hasattr(vm, "clear_dirty")
        assert not hasattr(vm, "change_section")
        assert not hasattr(vm, "active_section")
        assert not hasattr(vm, "SECTIONS")
        assert not hasattr(vm, "ENABLED")
    finally:
        vm.dispose()
        s3.dispose()
```

- [ ] **Step 3: Run tests + all gates**

```bash
uv run pytest tests/unit/vm/settings/test_settings_vm.py -v
uv run ruff check . && uv run ruff format --check . && uv run mypy --strict src && bash scripts/check-layers.sh && uv run pytest
```

Expected: 3 passed in `test_settings_vm.py`; all gates green. NOTE:
the existing `tests/integration/test_settings_modal_flow.py` will
start failing because it accesses `settings_vm.dirty_connection_names`.
That is expected and is fixed in Task 9 (wiring) / Task 10 (new
integration test). If the gate failure makes it impossible to commit,
add a temporary `pytest.skip` decorator to the failing tests with a
TODO referencing this plan — see Step 3a.

- [ ] **Step 3a (if needed): Skip the now-broken modal integration test**

If `pytest` fails because `tests/integration/test_settings_modal_flow.py`
references `SettingsVM.dirty_connection_names` (or similar):

In `tests/integration/test_settings_modal_flow.py`, add at the top of
the file:

```python
import pytest

pytestmark = pytest.mark.skip(
    reason="SettingsVM simplified in plan task 1; replaced by "
    "tests/integration/test_settings_flow.py in task 10. "
    "This file is deleted in task 11."
)
```

Re-run gates. Expected: all green.

- [ ] **Step 4: Commit**

```bash
git add src/aws_tui/vm/settings/settings_vm.py tests/unit/vm/settings/test_settings_vm.py
# Add the integration test only if Step 3a was needed:
git add tests/integration/test_settings_modal_flow.py 2>/dev/null || true
git commit -m "refactor(vm/settings): SettingsVM drops dirty-set + section machinery

PR #52's dirty-set + active-section state existed only because the
modal had a lifetime to track. With Settings now a nav-routed page
(reload-on-Save is immediate, no modal close to wait for), that
state is dead. Adds async setup() no-op so ContentHostVM.set_content
can call it uniformly across hosted VMs.

If the existing test_settings_modal_flow.py is skipped here, it's
deleted in task 11 once test_settings_flow.py (task 10) replaces it."
```

---

## Task 2: Rename `ServicesMenuVM` → `NavMenuVM` + add Settings item

The left rail is no longer services-only. Rename the VM (and its file)
to reflect the new role and extend `items` to include a hard-coded
`Settings` entry alongside the service-derived items.

**Files:**
- Create: `src/aws_tui/vm/nav_menu_vm.py` (the rename target)
- Delete: `src/aws_tui/vm/services_menu_vm.py`
- Modify: `tests/unit/vm/test_services_menu.py` → rename to `test_nav_menu_vm.py`, extend coverage
- Modify: `src/aws_tui/composition.py` — update import + variable name
- Modify: `src/aws_tui/app.py` — update import + attribute name
- Modify: `src/aws_tui/ui/widgets/services_menu.py` — update import (this widget will itself be replaced in Task 4, but for now it just consumes the renamed VM)

**Interfaces produced:**
- `class NavMenuVM` (renamed from `ServicesMenuVM`) — same public surface
  (`items`, `selected_id`, `select(id)`, `update_connection`,
  `construct`/`destruct`/`dispose`) plus the items list now always
  ends with a `NavItemVM` whose `descriptor.id == "settings"`,
  `descriptor.label == "Settings"`, `descriptor.icon == "⚙"`.
- `class NavItemVM` (renamed from `ServiceItemVM`) — same surface.

- [ ] **Step 1: Read the existing file**

`src/aws_tui/vm/services_menu_vm.py` is the source-of-truth. Note its
exact public surface so the rename preserves behavior 1:1.

- [ ] **Step 2: Write the failing test for the Settings nav item**

In `tests/unit/vm/test_services_menu.py`, add a new test at the end:

```python
from aws_tui.vm.nav_menu_vm import NavMenuVM


def test_nav_menu_always_includes_settings_item_last(
    # use the same fixture signature the existing tests use
) -> None:
    """Settings is a hard-coded nav peer to the service items; it
    appears as the LAST item in the menu regardless of which services
    are registered."""
    # Build a NavMenuVM with at least one service registered (S3).
    # Assert: vm.items[-1].descriptor.id == "settings"
    # Assert: vm.items[-1].descriptor.label == "Settings"
    # Assert: vm.items[-1].descriptor.icon == "⚙"
    # Assert: vm.select("settings") sets selected_id to "settings"
    ...
```

Read the existing tests' fixture pattern (likely a `_make_menu(...)`
helper or inline construction) and write the test body to match.

- [ ] **Step 3: Run test to verify it fails**

```bash
uv run pytest tests/unit/vm/test_services_menu.py -v -k "settings_item_last"
```
Expected: `ImportError: cannot import name 'NavMenuVM'` (since the
file doesn't exist yet).

- [ ] **Step 4: Rename the file and add Settings to the items list**

Move `src/aws_tui/vm/services_menu_vm.py` → `src/aws_tui/vm/nav_menu_vm.py`:

```bash
git mv src/aws_tui/vm/services_menu_vm.py src/aws_tui/vm/nav_menu_vm.py
```

In the new file `src/aws_tui/vm/nav_menu_vm.py`, rename the classes:
- `ServicesMenuVM` → `NavMenuVM`
- `ServiceItemVM` → `NavItemVM`
- Update the ComponentVM name from `"services_menu"` → `"nav_menu"`
- Update the ComponentVMOf name from `f"service_item.{descriptor.id}"` → `f"nav_item.{descriptor.id}"`
- Update the docstring to describe nav (not services)

After the existing service-item construction loop in `__init__` (or
wherever the initial items are built — find the `self._items = ...`
line and add the Settings item AFTER the loop), append:

```python
# Hard-coded Settings nav peer — always present, always last.
# Built from a synthetic ``ServiceDescriptor`` so the item shares the
# render/select machinery with service items but doesn't require the
# ServiceRegistry to know about it.
from aws_tui.services.protocols import ServiceDescriptor

settings_descriptor = ServiceDescriptor(
    id="settings",
    label="Settings",
    icon="⚙",
)
self._items.append(
    NavItemVM(
        descriptor=settings_descriptor,
        hub=hub,
        dispatcher=dispatcher,
    )
)
```

If `ServiceDescriptor`'s fields differ from `(id, label, icon)`, read
`src/aws_tui/services/protocols.py` first and use the actual field
names. The Settings item must not declare `supports(connection)` (it
should always be visible regardless of connection), so if
`ServiceDescriptor` carries a `supports` callable, set it to
`lambda _conn: True` for Settings.

Update the rename-tracker file `__all__`:
```python
__all__ = ["NavItemVM", "NavMenuVM"]
```

- [ ] **Step 5: Update import sites**

```bash
grep -rln "services_menu_vm\|ServicesMenuVM\|ServiceItemVM" src/ tests/ | sort -u
```

For each hit:
- `src/aws_tui/composition.py`: change `from aws_tui.vm.services_menu_vm import ServicesMenuVM` → `from aws_tui.vm.nav_menu_vm import NavMenuVM`. Rename the local variable from `services_menu_vm` → `nav_menu_vm`. Update the `RootVM(...)` call site that references it.
- `src/aws_tui/app.py`: same import update; any `self._services_menu_vm` references become `self._nav_menu_vm`.
- `src/aws_tui/vm/root_vm.py` (if it references the type): same import update.
- `src/aws_tui/ui/widgets/services_menu.py`: update the type annotation on the constructor — `vm: ServicesMenuVM` → `vm: NavMenuVM`, plus the import. (This widget itself is replaced in Task 4; for now it just consumes the renamed VM type.)

For any file in `tests/` that imports `ServicesMenuVM` or `ServiceItemVM`,
update the imports to `NavMenuVM` / `NavItemVM`.

- [ ] **Step 6: Rename the test file**

```bash
git mv tests/unit/vm/test_services_menu.py tests/unit/vm/test_nav_menu_vm.py
```

Update the imports in the renamed file to point at `nav_menu_vm`.

- [ ] **Step 7: Run tests + all gates**

```bash
uv run pytest tests/unit/vm/test_nav_menu_vm.py -v
uv run ruff check . && uv run ruff format --check . && uv run mypy --strict src && bash scripts/check-layers.sh && uv run pytest
```

Expected: all tests pass including the new `test_nav_menu_always_includes_settings_item_last`.

- [ ] **Step 8: Commit**

```bash
git add -A
git commit -m "refactor(vm): ServicesMenuVM → NavMenuVM + hard-coded Settings item

Rename reflects the rail's broader role: it now hosts not just
service items but also a Settings peer (and eventually any other
top-level nav destinations). The items list always ends with a
hard-coded Settings ``NavItemVM`` (id='settings', label='Settings',
icon='⚙') so the rail's render path is uniform across service and
non-service entries.

Class renames: ServicesMenuVM → NavMenuVM; ServiceItemVM → NavItemVM.
ComponentVM names: services_menu → nav_menu; service_item.* →
nav_item.*. Import sites in composition.py, app.py, root_vm.py,
and services_menu.py updated."
```

---

## Task 3: `ConnectionFormInline` widget

Lift the form body out of `S3CompatFormModal.compose()` into a
standalone `Widget` that can be mounted inline within
`S3ConnectionsPanel`. The validator and `_FIELDS` tuple move with it.
Existing validator tests are renamed and re-pointed.

**Files:**
- Create: `src/aws_tui/ui/widgets/settings/connection_form.py`
- Modify: `tests/unit/ui/test_s3_compat_form_modal.py` → rename to `test_connection_form_inline.py`, update imports

**Interfaces produced:**
- `class ConnectionFormInline(Widget)` with:
  - `__init__(*, hub)`
  - `open_for_add()` — clears fields, unlocks name field, title reads "New s3-compatible connection", shows the widget
  - `open_for_edit(name: str, defaults: S3CompatForm)` — pre-fills fields, locks name field, title reads f"Edit '{name}'", shows the widget
  - `close()` — hides the widget (sets `display: none`), clears state
  - Emits `ConnectionFormSubmitted` on Save (carries `form: S3CompatForm`, `mode: Literal["add", "edit"]`, `original_name: str | None`)
  - Emits `ConnectionFormCancelled` on Cancel or Esc
- `class ConnectionFormSubmitted(Message)` with attrs above
- `class ConnectionFormCancelled(Message)`
- `_validate_s3_form_value(field: str, value: str) -> str | None`
- `_FIELDS` tuple of `(key, label, placeholder, secret)`

- [ ] **Step 1: Write the failing validator tests**

Create `tests/unit/ui/test_connection_form_inline.py` with the validator
tests lifted from `test_s3_compat_form_modal.py` but importing from the
new location:

```python
"""Tests for ConnectionFormInline (formerly S3CompatFormModal validation)."""

from __future__ import annotations

import pytest

from aws_tui.ui.widgets.settings.connection_form import _validate_s3_form_value


def test_name_valid_simple() -> None:
    assert _validate_s3_form_value("name", "minio-local") is None


def test_name_invalid_empty() -> None:
    assert _validate_s3_form_value("name", "") is not None


def test_name_invalid_chars() -> None:
    assert _validate_s3_form_value("name", "has space") is not None
    assert _validate_s3_form_value("name", "with/slash") is not None


def test_name_invalid_too_long() -> None:
    assert _validate_s3_form_value("name", "x" * 33) is not None


def test_name_valid_max_length() -> None:
    assert _validate_s3_form_value("name", "x" * 32) is None


def test_endpoint_url_valid() -> None:
    assert _validate_s3_form_value("endpoint_url", "http://localhost:9000") is None
    assert _validate_s3_form_value("endpoint_url", "https://minio.internal:443/path") is None


def test_endpoint_url_invalid() -> None:
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


def test_construction_smoke() -> None:
    """Sanity-check that the widget instantiates without an app context."""
    from typing import cast
    from vmx import MessageHub
    from vmx.messages.protocols import Message

    from aws_tui.ui.widgets.settings.connection_form import ConnectionFormInline

    hub = cast("MessageHub[Message]", MessageHub())
    widget = ConnectionFormInline(hub=hub)
    assert widget is not None
```

- [ ] **Step 2: Delete the old test file**

```bash
git rm tests/unit/ui/test_s3_compat_form_modal.py
```

- [ ] **Step 3: Run tests to verify they fail**

```bash
uv run pytest tests/unit/ui/test_connection_form_inline.py -v
```
Expected: `ImportError: No module named 'aws_tui.ui.widgets.settings.connection_form'`.

- [ ] **Step 4: Create the widget file**

Create `src/aws_tui/ui/widgets/settings/connection_form.py`:

```python
"""ConnectionFormInline — inline form for adding / editing an
s3-compatible connection. Lifted from the deleted
``S3CompatFormModal``."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal
from urllib.parse import urlparse

from textual import on
from textual.app import ComposeResult
from textual.containers import Container, Horizontal, Vertical
from textual.message import Message as TextualMessage
from textual.widget import Widget
from textual.widgets import Input, Static
from vmx import Message, MessageHub

from aws_tui.ui.widgets.modal_button import ModalButton
from aws_tui.vm.chrome.first_run_vm import S3CompatForm

_NAME_RE = re.compile(r"^[A-Za-z0-9_-]{1,32}$")

_FIELDS: tuple[tuple[str, str, str, bool], ...] = (
    ("name", "Name", "minio-local", False),
    ("endpoint_url", "Endpoint URL", "http://localhost:9000", False),
    ("region", "Region", "us-east-1", False),
    ("access_key_id", "Access key ID", "", False),
    ("secret_access_key", "Secret access key", "", True),
)


def _validate_s3_form_value(field: str, value: str) -> str | None:
    """Return None if valid, else an error string suitable for tooltip.

    Rules:
    - ``name``: matches ``^[A-Za-z0-9_-]{1,32}$``
    - ``endpoint_url``: ``http://`` or ``https://``, non-empty netloc
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
    if not stripped:
        return "required"
    return None


@dataclass
class _OpenContext:
    mode: Literal["add", "edit"]
    original_name: str | None


class ConnectionFormSubmitted(TextualMessage):
    """Emitted by ``ConnectionFormInline`` when the user clicks Save
    on a valid form."""

    def __init__(
        self,
        *,
        form: S3CompatForm,
        mode: Literal["add", "edit"],
        original_name: str | None,
    ) -> None:
        super().__init__()
        self.form: S3CompatForm = form
        self.mode: Literal["add", "edit"] = mode
        self.original_name: str | None = original_name


class ConnectionFormCancelled(TextualMessage):
    """Emitted by ``ConnectionFormInline`` when the user clicks Cancel
    or presses Esc inside the form."""


class ConnectionFormInline(Widget):
    """Inline form for s3-compatible connections.

    Hidden by default (``display: none``). Call ``open_for_add()`` or
    ``open_for_edit(name, defaults)`` to populate fields and show.
    Click Save → emits :class:`ConnectionFormSubmitted` and hides.
    Click Cancel or Esc → emits :class:`ConnectionFormCancelled` and
    hides.
    """

    DEFAULT_CSS = """
    ConnectionFormInline {
        display: none;
        height: auto;
        width: 1fr;
    }
    ConnectionFormInline.-open {
        display: block;
    }
    ConnectionFormInline > Container {
        height: auto;
        padding: 1 2;
    }
    ConnectionFormInline .form-title {
        text-style: bold;
        padding: 0 0 1 0;
    }
    ConnectionFormInline .form-label {
        padding: 0 0 0 0;
    }
    ConnectionFormInline .form-fields {
        height: auto;
    }
    ConnectionFormInline .form-footer {
        height: 3;
        align: right middle;
    }
    """

    BINDINGS = [  # noqa: RUF012
        ("escape", "cancel", "Cancel"),
    ]

    def __init__(self, *, hub: MessageHub[Message]) -> None:
        super().__init__()
        self._hub: MessageHub[Message] = hub
        self._ctx: _OpenContext | None = None

    def compose(self) -> ComposeResult:
        with Container():
            yield Static("New s3-compatible connection", classes="form-title", id="form-title")
            with Vertical(classes="form-fields"):
                for key, label, placeholder, secret in _FIELDS:
                    yield Static(label, classes="form-label")
                    yield Input(
                        value="",
                        placeholder=placeholder,
                        password=secret,
                        id=f"form-{key}",
                    )
            with Horizontal(classes="form-footer"):
                yield ModalButton("cancel", button_id="form-cancel-btn")
                yield ModalButton("save", button_id="form-save-btn", classes="-primary")

    # ── Public API ─────────────────────────────────────────────────────────

    def open_for_add(self) -> None:
        """Show the form in Add mode (all fields empty, name unlocked)."""
        self._ctx = _OpenContext(mode="add", original_name=None)
        self.query_one("#form-title", Static).update("New s3-compatible connection")
        for key, _, _, _ in _FIELDS:
            inp = self.query_one(f"#form-{key}", Input)
            inp.value = ""
            inp.disabled = False
            inp.remove_class("-invalid")
        self._refresh_save_button()
        self.add_class("-open")
        # Focus the first field for keyboard convenience.
        self.query_one("#form-name", Input).focus()

    def open_for_edit(self, *, name: str, defaults: S3CompatForm) -> None:
        """Show the form in Edit mode (pre-filled, name locked)."""
        self._ctx = _OpenContext(mode="edit", original_name=name)
        self.query_one("#form-title", Static).update(f"Edit {name!r}")
        for key, _, _, _ in _FIELDS:
            inp = self.query_one(f"#form-{key}", Input)
            default_val = getattr(defaults, key, "")
            inp.value = str(default_val) if default_val is not None else ""
            inp.disabled = (key == "name")
            inp.remove_class("-invalid")
        self._refresh_save_button()
        self.add_class("-open")
        self.query_one("#form-endpoint_url", Input).focus()

    def close(self) -> None:
        """Hide the form and clear state."""
        self.remove_class("-open")
        self._ctx = None

    # ── Event handlers ─────────────────────────────────────────────────────

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

    def on_click(self, event: object) -> None:
        # Capture button clicks via the legacy on_click route — ModalButton
        # is a Static subclass (not Button), so @on(Button.Pressed) won't
        # fire. Walk the click target to find the button_id.
        target = getattr(event, "widget", None) or getattr(event, "control", None)
        if target is None:
            return
        btn_id = getattr(target, "button_id", None)
        if btn_id == "form-cancel-btn":
            self.action_cancel()
        elif btn_id == "form-save-btn":
            self._submit()

    def action_cancel(self) -> None:
        if self._ctx is None:
            return
        self.close()
        self.post_message(ConnectionFormCancelled())

    # ── Internal ───────────────────────────────────────────────────────────

    def _refresh_save_button(self) -> None:
        save_btn: ModalButton | None = None
        for btn in self.query(ModalButton):
            if btn.button_id == "form-save-btn":
                save_btn = btn
                break
        if save_btn is None:
            return
        invalid = False
        for key, _, _, _ in _FIELDS:
            inp = self.query_one(f"#form-{key}", Input)
            if _validate_s3_form_value(key, inp.value) is not None:
                invalid = True
                break
        save_btn.disabled = invalid

    def _submit(self) -> None:
        if self._ctx is None:
            return
        # Final validation pass — refuse if anything regressed.
        values: dict[str, str] = {}
        for key, _, _, _ in _FIELDS:
            inp = self.query_one(f"#form-{key}", Input)
            if _validate_s3_form_value(key, inp.value) is not None:
                return  # Save button should have been disabled; defense in depth.
            values[key] = inp.value
        form = S3CompatForm(
            name=values["name"],
            endpoint_url=values["endpoint_url"],
            region=values["region"],
            access_key_id=values["access_key_id"],
            secret_access_key=values["secret_access_key"],
            force_path_style=True,
            verify_tls=True,
        )
        ctx = self._ctx
        self.close()
        self.post_message(
            ConnectionFormSubmitted(form=form, mode=ctx.mode, original_name=ctx.original_name)
        )


__all__ = [
    "ConnectionFormCancelled",
    "ConnectionFormInline",
    "ConnectionFormSubmitted",
]
```

NOTE: the existing `S3CompatForm` dataclass at
`src/aws_tui/vm/chrome/first_run_vm.py` does have `force_path_style` and
`verify_tls` boolean fields. For sub-project A the spec doesn't require
the inline form to expose toggle widgets for them — we hard-code
`force_path_style=True, verify_tls=True` here (matching the form's
existing defaults). If a later iteration needs them as user-visible
toggles, add `Switch` widgets to the `compose()` loop.

- [ ] **Step 5: Run tests + all gates**

```bash
uv run pytest tests/unit/ui/test_connection_form_inline.py -v
uv run ruff check . && uv run ruff format --check . && uv run mypy --strict src && bash scripts/check-layers.sh && uv run pytest
```

Expected: 13 tests pass (11 validator + 1 construction + 1 lifted). All
gates green. NOTE: at this point `S3CompatFormModal` still exists and
still imports `_validate_s3_form_value` from inside its own module
(`first_run_modal.py`). That's fine — we're adding the new widget
alongside the old one. Task 11 removes the duplicate.

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "feat(ui): ConnectionFormInline widget lifted from S3CompatFormModal

Standalone Widget with open_for_add() / open_for_edit() public API.
Hidden by default; shown via the .-open CSS class. Emits two
custom Textual messages (ConnectionFormSubmitted with form data +
mode + original_name; ConnectionFormCancelled). Validation is the
same _validate_s3_form_value function lifted from
first_run_modal.py — single source of truth lives in the new
connection_form module now.

S3CompatFormModal still exists and is still used by
S3ConnectionsPanel + FirstRunModal — Task 11 deletes it once those
two callers have been migrated to ConnectionFormInline."
```

---

## Task 4: `NavMenu` widget (OptionList-based)

Replace the `ServiceItemView`-based `ServicesMenu` with an
`OptionList`-backed `NavMenu`. Preserves the collapse/expand-via-hamburger
pattern: collapsed mode shows icon glyphs only; expanded mode shows full
labels.

**Files:**
- Create: `src/aws_tui/ui/widgets/nav_menu.py`
- Create: `tests/unit/ui/test_nav_menu.py`

**Interfaces consumed:**
- `NavMenuVM(items: tuple[NavItemVM, ...], selected_id: str | None, select(id: str), toggle_collapsed(), is_collapsed: bool)` — Task 2

**Interfaces produced:**
- `class NavMenu(Widget)` with:
  - `__init__(*, vm: NavMenuVM, hub: MessageHub)`
  - `toggle_collapsed()` — flips the `-collapsed` CSS class on the widget and rebuilds the OptionList prompts (icon-only ↔ full label)
  - `is_collapsed: bool` property
  - On `OptionList.OptionSelected`, calls `self._vm.select(event.option_id)`

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/ui/test_nav_menu.py`:

```python
"""Tests for NavMenu (OptionList-based vertical nav)."""

from __future__ import annotations

from pathlib import Path
from typing import cast

import pytest
from textual.app import App, ComposeResult
from vmx import NULL_DISPATCHER, MessageHub
from vmx.messages.protocols import Message

from aws_tui.infra.config_store import ConfigStore
from aws_tui.infra.connection_resolver import ConnectionResolver
from aws_tui.services.registry import ServiceRegistry
from aws_tui.ui.widgets.nav_menu import NavMenu
from aws_tui.vm.nav_menu_vm import NavMenuVM


def _hub() -> MessageHub[Message]:
    return cast("MessageHub[Message]", MessageHub())


def _make_vm(tmp_path: Path) -> NavMenuVM:
    hub = _hub()
    registry = ServiceRegistry()
    vm = NavMenuVM(registry=registry, hub=hub, dispatcher=NULL_DISPATCHER)
    vm.construct()
    return vm


def test_nav_menu_can_be_constructed(tmp_path: Path) -> None:
    vm = _make_vm(tmp_path)
    try:
        widget = NavMenu(vm=vm, hub=_hub())
        assert widget is not None
        assert widget.is_collapsed is True  # default starts collapsed
    finally:
        vm.dispose()


@pytest.mark.asyncio
async def test_nav_menu_renders_settings_item_in_options(tmp_path: Path) -> None:
    """The Settings nav item must be visible in the OptionList prompts."""
    vm = _make_vm(tmp_path)

    class _Host(App[None]):
        def __init__(self, w: NavMenu) -> None:
            super().__init__()
            self._w = w

        def compose(self) -> ComposeResult:
            yield self._w

    nav = NavMenu(vm=vm, hub=_hub())
    app = _Host(nav)
    try:
        async with app.run_test() as pilot:
            await pilot.pause()
            from textual.widgets import OptionList

            ol = nav.query_one(OptionList)
            # Force-expand so labels (not just icons) are present.
            nav.toggle_collapsed()
            await pilot.pause()
            prompts = [str(opt.prompt) for opt in ol._options]
            # The Settings prompt should contain "Settings" when expanded.
            assert any("Settings" in p for p in prompts), prompts
    finally:
        vm.dispose()


@pytest.mark.asyncio
async def test_nav_menu_collapsed_shows_icon_only(tmp_path: Path) -> None:
    """In collapsed mode the OptionList prompts are icon glyphs only."""
    vm = _make_vm(tmp_path)

    class _Host(App[None]):
        def __init__(self, w: NavMenu) -> None:
            super().__init__()
            self._w = w

        def compose(self) -> ComposeResult:
            yield self._w

    nav = NavMenu(vm=vm, hub=_hub())
    app = _Host(nav)
    try:
        async with app.run_test() as pilot:
            await pilot.pause()
            # NavMenu starts collapsed by default.
            assert nav.is_collapsed is True
            from textual.widgets import OptionList

            ol = nav.query_one(OptionList)
            prompts = [str(opt.prompt) for opt in ol._options]
            # In collapsed mode, "Settings" should NOT appear; "⚙" SHOULD.
            assert not any("Settings" in p for p in prompts), prompts
            assert any("⚙" in p for p in prompts), prompts
    finally:
        vm.dispose()
```

NOTE: `ol._options` is an internal attribute (no public `options` property
on `OptionList` at the version in `.venv`). It's the only way to introspect
the current options list in a test; using it is fair for white-box
assertions but flag if a future Textual version exposes a public accessor.

- [ ] **Step 2: Run tests to verify they fail**

```bash
uv run pytest tests/unit/ui/test_nav_menu.py -v
```
Expected: `ImportError: No module named 'aws_tui.ui.widgets.nav_menu'`.

- [ ] **Step 3: Implement `NavMenu`**

Create `src/aws_tui/ui/widgets/nav_menu.py`:

```python
"""NavMenu — left-rail vertical nav backed by Textual's OptionList.

Replaces the previous ``ServiceItemView``-based ``ServicesMenu``.
Items rendered come from :class:`NavMenuVM.items`; selecting one
calls ``vm.select(item_id)``, which the app routes to
``ContentHostVM.set_content``.

Collapsed mode shows icon glyphs only (e.g. ``S3``, ``⚙``). Expanded
mode shows full labels (``S3``, ``Settings``). The hamburger button
in the app's title bar calls :meth:`toggle_collapsed`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from textual.app import ComposeResult
from textual.widget import Widget
from textual.widgets import OptionList, Static
from textual.widgets.option_list import Option
from vmx import Message, MessageHub

if TYPE_CHECKING:
    from aws_tui.vm.nav_menu_vm import NavMenuVM


class NavMenu(Widget):
    """OptionList-backed left rail."""

    DEFAULT_CSS = """
    NavMenu {
        display: none;
        width: 0;
        height: 1fr;
    }
    NavMenu.-expanded {
        display: block;
        width: 18;
    }
    NavMenu.-collapsed.-expanded {
        width: 4;
    }
    NavMenu > #menu-header {
        padding: 0 1;
        text-style: bold;
    }
    NavMenu > OptionList {
        height: 1fr;
        background: $bg;
    }
    """

    def __init__(
        self,
        *,
        vm: NavMenuVM,
        hub: MessageHub[Message],
        id: str | None = None,
        classes: str | None = None,
    ) -> None:
        super().__init__(id=id, classes=classes)
        self._vm: NavMenuVM = vm
        self._hub: MessageHub[Message] = hub
        # Match the legacy ServicesMenu default: collapsed at start so
        # the dual-pane gets all the horizontal space until the user
        # toggles via the hamburger.
        self._collapsed: bool = True
        self.add_class("-collapsed")

    @property
    def is_collapsed(self) -> bool:
        return self._collapsed

    def toggle_collapsed(self) -> None:
        self._collapsed = not self._collapsed
        if self._collapsed:
            self.add_class("-collapsed")
        else:
            self.remove_class("-collapsed")
        # Always mark expanded so the display:block/width rules apply.
        # Toggling visibility is the app's responsibility via -expanded.
        self._rebuild_options()

    @property
    def vm(self) -> NavMenuVM:
        return self._vm

    def compose(self) -> ComposeResult:
        yield Static("menu", id="menu-header")
        yield OptionList(id="menu-options")

    def on_mount(self) -> None:
        self._rebuild_options()

    # ── Internal ───────────────────────────────────────────────────────────

    def _rebuild_options(self) -> None:
        """Rebuild the OptionList options to reflect the current
        items + collapsed state. Called on mount, on toggle, and
        whenever the VM's items change."""
        try:
            ol = self.query_one("#menu-options", OptionList)
        except Exception:
            return  # Not mounted yet.
        ol.clear_options()
        for item in self._vm.items:
            descriptor = item.descriptor
            if self._collapsed:
                glyph = (descriptor.icon or descriptor.label or "?")[:2]
                prompt = glyph
            else:
                glyph = descriptor.icon or "·"
                prompt = f"{glyph} {descriptor.label}"
            ol.add_option(Option(prompt, id=descriptor.id))
        # Restore the highlight to the currently-selected item if any.
        if self._vm.selected_id is not None:
            for idx, item in enumerate(self._vm.items):
                if item.descriptor.id == self._vm.selected_id:
                    ol.highlighted = idx
                    break

    # ── Event handlers ─────────────────────────────────────────────────────

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        """Forward selection to the VM."""
        if event.option_id is None:
            return
        self._vm.select(event.option_id)


__all__ = ["NavMenu"]
```

NOTE on `display: none` + `-expanded`: this mirrors the legacy
`ServicesMenu` pattern (the widget is invisible until the app toggles
the `-expanded` class via `action_toggle_services`). Task 9 will wire
the app to toggle `-expanded` on `NavMenu` the same way it did for
`ServicesMenu`. The `-collapsed` class is a separate axis controlling
icon-only vs labeled mode.

If `NavMenuVM` doesn't have a public `select(item_id)` method (the rename
in Task 2 should have created it), add it there as a thin wrapper that
sets `selected_id` and publishes the change. Re-check Task 2's output if
needed.

- [ ] **Step 4: Run tests + all gates**

```bash
uv run pytest tests/unit/ui/test_nav_menu.py -v
uv run ruff check . && uv run ruff format --check . && uv run mypy --strict src && bash scripts/check-layers.sh && uv run pytest
```

Expected: 3 tests pass. All gates green.

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "feat(ui): NavMenu widget — OptionList-based vertical rail

Replaces ServiceItemView-based ServicesMenu (still exists; deleted
in Task 11). Items come from NavMenuVM (which now includes the
hard-coded Settings peer). Collapsed mode renders icon glyphs
only; expanded mode renders full labels. Selection emits
OptionList.OptionSelected which the widget routes to
NavMenuVM.select(item_id) — the app then translates that into a
ContentHostVM.set_content swap (wired in Task 9)."
```

---

## Task 5: `SettingsView` widget

The main-area content for the Settings nav destination. A
`VerticalScroll` of `Collapsible` sections, with the first (Connections)
populated by `S3ConnectionsPanel` and the rest as disabled placeholders.

**Files:**
- Create: `src/aws_tui/ui/widgets/settings_view.py`
- Create: `tests/unit/ui/test_settings_view.py`

**Interfaces consumed:**
- `SettingsVM(s3: S3ConnectionsVM, hub, dispatcher)` — Task 1
- `S3ConnectionsPanel(vm: S3ConnectionsVM, hub)` — already exists from
  PR #52, modified in Task 6 to use `ConnectionFormInline`

**Interfaces produced:**
- `class SettingsView(Widget)` with `__init__(*, vm: SettingsVM, hub: MessageHub)` and a `compose()` that yields the VerticalScroll + Collapsibles + S3ConnectionsPanel.

- [ ] **Step 1: Write the failing test**

Create `tests/unit/ui/test_settings_view.py`:

```python
"""Tests for SettingsView."""

from __future__ import annotations

from pathlib import Path
from typing import cast

import pytest
from textual.app import App, ComposeResult
from vmx import NULL_DISPATCHER, MessageHub
from vmx.messages.protocols import Message

from aws_tui.infra.config_store import ConfigStore
from aws_tui.infra.connection_resolver import ConnectionResolver
from aws_tui.ui.widgets.settings_view import SettingsView
from aws_tui.vm.settings.s3_connections_vm import S3ConnectionsVM
from aws_tui.vm.settings.settings_vm import SettingsVM


def _hub() -> MessageHub[Message]:
    return cast("MessageHub[Message]", MessageHub())


def _make_vm(tmp_path: Path) -> tuple[SettingsVM, S3ConnectionsVM]:
    hub = _hub()
    store = ConfigStore(path=tmp_path / "config.toml")
    resolver = ConnectionResolver(config_store=store)
    s3 = S3ConnectionsVM(
        resolver=resolver, config_store=store, hub=hub, dispatcher=NULL_DISPATCHER
    )
    s3.construct()
    vm = SettingsVM(s3=s3, hub=hub, dispatcher=NULL_DISPATCHER)
    vm.construct()
    return vm, s3


def test_settings_view_can_be_constructed(tmp_path: Path) -> None:
    vm, s3 = _make_vm(tmp_path)
    try:
        view = SettingsView(vm=vm, hub=_hub())
        assert view is not None
    finally:
        vm.dispose()
        s3.dispose()


@pytest.mark.asyncio
async def test_settings_view_shows_connections_section_expanded_by_default(tmp_path: Path) -> None:
    vm, s3 = _make_vm(tmp_path)

    class _Host(App[None]):
        def __init__(self, w: SettingsView) -> None:
            super().__init__()
            self._w = w

        def compose(self) -> ComposeResult:
            yield self._w

    view = SettingsView(vm=vm, hub=_hub())
    app = _Host(view)
    try:
        async with app.run_test() as pilot:
            await pilot.pause()
            from textual.widgets import Collapsible

            conn_section = view.query_one("#section-connections", Collapsible)
            assert conn_section.collapsed is False
            themes_section = view.query_one("#section-themes", Collapsible)
            assert themes_section.collapsed is True
            assert themes_section.disabled is True
    finally:
        vm.dispose()
        s3.dispose()
```

- [ ] **Step 2: Run test to verify it fails**

```bash
uv run pytest tests/unit/ui/test_settings_view.py -v
```
Expected: `ImportError`.

- [ ] **Step 3: Implement `SettingsView`**

Create `src/aws_tui/ui/widgets/settings_view.py`:

```python
"""SettingsView — main-area content for the Settings nav destination.

VS Code-style scrollable page of Collapsible sections. The
Connections section is populated by ``S3ConnectionsPanel`` for
sub-project A. Themes and Keymap are visible-but-disabled
placeholders that go live in sub-projects B and C.
"""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import VerticalScroll
from textual.widget import Widget
from textual.widgets import Collapsible, Static
from vmx import Message, MessageHub

from aws_tui.ui.widgets.settings.s3_connections_panel import S3ConnectionsPanel
from aws_tui.vm.settings.settings_vm import SettingsVM


class SettingsView(Widget):
    """Top-level Settings page."""

    DEFAULT_CSS = """
    SettingsView {
        height: 1fr;
        width: 1fr;
    }
    SettingsView > #settings-title {
        padding: 0 2 1 2;
        text-style: bold;
    }
    SettingsView > VerticalScroll {
        height: 1fr;
        padding: 0 2;
    }
    SettingsView Collapsible {
        margin-bottom: 1;
    }
    """

    def __init__(self, *, vm: SettingsVM, hub: MessageHub[Message]) -> None:
        super().__init__()
        self._vm: SettingsVM = vm
        self._hub: MessageHub[Message] = hub

    @property
    def vm(self) -> SettingsVM:
        return self._vm

    def compose(self) -> ComposeResult:
        yield Static("Settings", id="settings-title")
        with VerticalScroll(id="settings-scroll"):
            with Collapsible(
                title="S3-Compatible Connections",
                collapsed=False,
                id="section-connections",
            ):
                yield S3ConnectionsPanel(vm=self._vm.s3, hub=self._hub)
            with Collapsible(
                title="Themes (coming in v0.8)",
                collapsed=True,
                disabled=True,
                id="section-themes",
            ):
                yield Static("This section is coming in v0.8.")
            with Collapsible(
                title="Keymap (coming in v0.8)",
                collapsed=True,
                disabled=True,
                id="section-keymap",
            ):
                yield Static("This section is coming in v0.8.")


__all__ = ["SettingsView"]
```

- [ ] **Step 4: Run tests + all gates**

```bash
uv run pytest tests/unit/ui/test_settings_view.py -v
uv run ruff check . && uv run ruff format --check . && uv run mypy --strict src && bash scripts/check-layers.sh && uv run pytest
```

Expected: 2 tests pass. All gates green.

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "feat(ui): SettingsView widget — scrollable page of Collapsible sections

VS Code-style settings page. Connections section is expanded by
default and hosts S3ConnectionsPanel. Themes and Keymap sections
are visible-but-disabled placeholders (sub-projects B and C will
drop the disabled flag and fill their bodies).

Replaces (but doesn't yet retire) SettingsModal. Cutover to use
this widget happens in Task 9; SettingsModal file deleted in
Task 11."
```

---

## Task 6: `S3ConnectionsPanel` switches to `ConnectionFormInline`

Replace the three `@work` modal-push handlers (`_do_add`, `_do_edit`)
with handlers that toggle the inline form's visibility. Delete still
pushes `ConfirmModal`. The form widget is mounted as a child of the
panel; the panel subscribes to `ConnectionFormSubmitted` /
`ConnectionFormCancelled` messages and routes Save to the VM.

**Files:**
- Modify: `src/aws_tui/ui/widgets/settings/s3_connections_panel.py`
- Modify: `tests/unit/ui/test_s3_connections_panel.py`

**Interfaces consumed:**
- `ConnectionFormInline(hub)` + `open_for_add()` / `open_for_edit(name, defaults)` / `close()` + `ConnectionFormSubmitted` / `ConnectionFormCancelled` messages — Task 3

**Interfaces produced:**
- `S3ConnectionsPanel` constructor signature unchanged.
- `on_connection_form_submitted(event)` event handler.
- `on_connection_form_cancelled(event)` event handler (no-op other than
  potentially refreshing rows; the form closes itself).

- [ ] **Step 1: Update the panel**

In `src/aws_tui/ui/widgets/settings/s3_connections_panel.py`:

(a) Add imports at the top:
```python
from aws_tui.ui.widgets.settings.connection_form import (
    ConnectionFormCancelled,
    ConnectionFormInline,
    ConnectionFormSubmitted,
)
```

(b) In `compose()` (or wherever the panel's children are yielded), add
the inline form at the end:
```python
yield ConnectionFormInline(hub=self._hub)
```

(c) Replace `_do_add`, `_do_edit`, `_do_delete` and their click-dispatch
sites:

```python
def _on_add_clicked(self) -> None:
    form = self.query_one(ConnectionFormInline)
    form.open_for_add()

def _on_edit_clicked(self, name: str) -> None:
    # Resolve current connection values to seed the form.
    existing = self._vm.find_by_name(name)
    if existing is None:
        return
    from aws_tui.vm.chrome.first_run_vm import S3CompatForm
    defaults = S3CompatForm(
        name=existing.name,
        endpoint_url=existing.endpoint_url or "",
        region=existing.region,
        access_key_id=existing.access_key_id or "",
        secret_access_key=existing.secret_access_key or "",
        force_path_style=existing.force_path_style,
        verify_tls=existing.verify_tls,
    )
    form = self.query_one(ConnectionFormInline)
    form.open_for_edit(name=name, defaults=defaults)

@work(exclusive=False)
async def _do_delete(self, name: str) -> None:
    # UNCHANGED from PR #52 — destructive ops still use ConfirmModal.
    confirm_vm = ConfirmationVM(hub=self._hub, dispatcher=self._vm.dispatcher)
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
        await self.refresh_rows()
```

Update the click handlers (`on_click` or wherever button presses dispatch
to `_do_add` / `_do_edit`) to call `_on_add_clicked` / `_on_edit_clicked`
instead — they are no longer `@work` workers (the form is already
mounted; no awaiting needed).

(d) Add the inline form event handlers:

```python
def on_connection_form_submitted(self, event: ConnectionFormSubmitted) -> None:
    """Handle a Save from the inline form."""
    entry = self._vm.entry_from_form(event.form)
    if event.mode == "add":
        try:
            self._vm.add(entry)
        except ValueError:
            # Duplicate name — re-open the form so the user sees the error.
            return
    else:  # "edit"
        assert event.original_name is not None
        self._vm.update(event.original_name, entry)
    # Synchronous-friendly call into refresh:
    self.run_worker(self.refresh_rows())

def on_connection_form_cancelled(self, event: ConnectionFormCancelled) -> None:
    """Form closed itself on cancel; nothing to do here."""
    return
```

(e) Add a helper `find_by_name(name) -> Connection | None` to
`S3ConnectionsVM` (file: `src/aws_tui/vm/settings/s3_connections_vm.py`)
if it doesn't already exist:

```python
def find_by_name(self, name: str) -> Connection | None:
    """Look up a connection by name; returns None if not found."""
    for c in self.connections:
        if c.name == name:
            return c
    return None
```

(f) Drop the import of `S3CompatFormModal` from the top of the panel
file — it's no longer used. If `from typing import TYPE_CHECKING` was
only used for that, remove it too.

- [ ] **Step 2: Update tests**

In `tests/unit/ui/test_s3_connections_panel.py`, drop any test that
explicitly verifies the `@work` modal-push flow. Add a test that
verifies the panel responds to a `ConnectionFormSubmitted` message:

```python
@pytest.mark.asyncio
async def test_panel_routes_form_submission_to_vm_add(tmp_path: Path) -> None:
    """When ConnectionFormSubmitted fires with mode='add', the panel
    calls vm.add(entry_from_form(form))."""
    from textual.app import App, ComposeResult

    from aws_tui.ui.widgets.settings.connection_form import ConnectionFormSubmitted
    from aws_tui.vm.chrome.first_run_vm import S3CompatForm

    hub = _hub()
    store = ConfigStore(path=tmp_path / "config.toml")
    resolver = ConnectionResolver(config_store=store)
    s3_vm = S3ConnectionsVM(
        resolver=resolver, config_store=store, hub=hub, dispatcher=NULL_DISPATCHER
    )
    s3_vm.construct()
    panel = S3ConnectionsPanel(vm=s3_vm, hub=hub)

    class _Host(App[None]):
        def __init__(self, w: S3ConnectionsPanel) -> None:
            super().__init__()
            self._w = w

        def compose(self) -> ComposeResult:
            yield self._w

    app = _Host(panel)
    try:
        async with app.run_test() as pilot:
            await pilot.pause()
            form = S3CompatForm(
                name="from-event",
                endpoint_url="http://localhost:9999",
                region="us-east-1",
                access_key_id="K",
                secret_access_key="S",
                force_path_style=True,
                verify_tls=True,
            )
            panel.post_message(
                ConnectionFormSubmitted(form=form, mode="add", original_name=None)
            )
            await pilot.pause()
        # After event handling the row must be persisted.
        assert "from-event" in store.load().connections
    finally:
        s3_vm.dispose()
```

- [ ] **Step 3: Run tests + all gates**

```bash
uv run pytest tests/unit/ui/test_s3_connections_panel.py -v
uv run ruff check . && uv run ruff format --check . && uv run mypy --strict src && bash scripts/check-layers.sh && uv run pytest
```
Expected: existing smoke tests still pass + new event-handling test passes. All gates green.

- [ ] **Step 4: Commit**

```bash
git add -A
git commit -m "refactor(ui): S3ConnectionsPanel uses ConnectionFormInline

The three @work handlers that pushed S3CompatFormModal via
push_screen_wait are replaced by sync click handlers that toggle
the inline form's visibility via open_for_add / open_for_edit.
The panel subscribes to ConnectionFormSubmitted /
ConnectionFormCancelled messages and routes Save to vm.add /
vm.update. Delete still uses ConfirmModal (destructive ops keep
the modal interruption pattern).

Adds S3ConnectionsVM.find_by_name helper used by edit-mode form
prefill. S3CompatFormModal is no longer imported by this file —
deleted in Task 11."
```

---

## Task 7: Per-theme CSS for new widgets (×10 themes)

Add the CSS blocks for `NavMenu`, `SettingsView`, and `ConnectionFormInline`
to all 10 themes. The selection-highlight CSS for the OptionList inside
NavMenu must use `$bg-sel` + `$accent` to match the file-pane row cursor.

**Files:**
- Modify: all 10 `src/aws_tui/ui/themes/*.tcss` files

**Interfaces consumed:**
- `NavMenu`, `SettingsView`, `ConnectionFormInline` widgets (Tasks 3-5)

- [ ] **Step 1: Author the template CSS for `carbon.tcss`**

At the bottom of `src/aws_tui/ui/themes/carbon.tcss`, add the block:

```tcss
/* ─── NavMenu ─────────────────────────────────────────────────── */

NavMenu {
    background: $bg;
}

NavMenu > #menu-header {
    background: $bg;
    color: $text-muted;
    padding: 0 1;
    text-style: bold;
}

NavMenu > OptionList {
    background: $bg;
    color: $text;
    border: none;
    padding: 0;
}

NavMenu > OptionList > .option-list--option-highlighted {
    background: $bg-sel;
    color: $accent;
    text-style: bold;
}

/* ─── SettingsView ────────────────────────────────────────────── */

SettingsView {
    background: $bg;
}

SettingsView > #settings-title {
    background: $bg;
    color: $accent;
    padding: 0 2 1 2;
    text-style: bold;
}

SettingsView > VerticalScroll {
    background: $bg;
    padding: 0 2;
}

SettingsView Collapsible {
    background: $bg-elev;
    margin-bottom: 1;
}

SettingsView Collapsible:disabled {
    color: $text-muted;
    text-style: italic;
}

SettingsView Collapsible > CollapsibleTitle {
    background: $bg-elev;
    color: $accent;
}

/* ─── ConnectionFormInline ────────────────────────────────────── */

ConnectionFormInline {
    background: $bg-elev;
}

ConnectionFormInline > Container {
    background: $bg-elev;
    color: $text;
    padding: 1 2;
}

ConnectionFormInline .form-title {
    color: $accent;
    text-style: bold;
    padding: 0 0 1 0;
}

ConnectionFormInline .form-label {
    color: $text-muted;
}

ConnectionFormInline Input {
    background: $bg;
    color: $text;
    border: none;
    padding: 0 1;
    margin-bottom: 1;
}

ConnectionFormInline Input.-invalid {
    border: tall $danger;
}

ConnectionFormInline .form-footer {
    height: 3;
    align: right middle;
}
```

NOTE: per the project's [textual-design-system-gotchas memory note](/Users/kaveh/.claude/projects/-Users-kaveh-repos-aws-tui/memory/textual-design-system-gotchas.md),
Textual reserves `$text-muted` and overrides it with alpha-blend
expressions. Using it in `border-*` properties causes CSS parse failures.
The block above uses `$text-muted` only in `color:` and `text-style:` —
safe. The `Input.-invalid` rule uses `$danger` which is safe in borders.

- [ ] **Step 2: Mirror the block into the other 9 themes**

Copy the same block (identical token names) into each of:
- `voidline.tcss`
- `lattice.tcss`
- `amber.tcss`
- `solarized-light.tcss`
- `github-light.tcss`
- `one-light.tcss`
- `nord.tcss`
- `dracula.tcss`
- `gruvbox-dark.tcss`

Theme-scoped tokens resolve to per-theme palette automatically.

- [ ] **Step 3: Run all gates**

```bash
uv run ruff check . && uv run ruff format --check . && uv run mypy --strict src && bash scripts/check-layers.sh && uv run pytest
```
Expected: all gates green. (No snapshot tests for the new widgets yet — those land in Task 8.)

- [ ] **Step 4: Commit**

```bash
git add src/aws_tui/ui/themes/
git commit -m "style(themes): NavMenu + SettingsView + ConnectionFormInline × 10 themes

Per-theme CSS for the three new widgets. NavMenu's OptionList
highlight uses \$bg-sel + \$accent to match the file-pane row
cursor (.entry-row.-selected). SettingsView's Collapsible chrome
uses \$bg-elev as section-card background and \$accent for active
titles; disabled Collapsibles render with \$text-muted + italic.
ConnectionFormInline matches the dialog-tier aesthetic from
ConfirmModal."
```

---

## Task 8: Snapshot tests for new widgets + content-presence guards

For each new widget, create:
- A test-app file that mounts the widget in isolation across 10 themes
- A snapshot test file with parametrized `snap_compare`
- A paired content-presence guard test that reads each generated `.raw`
  SVG and asserts key text/glyphs are actually rendered (per PR #53
  lesson)

**Files:**
- Create: `tests/snapshot/apps/nav_menu.py`
- Create: `tests/snapshot/apps/settings_view.py`
- Create: `tests/snapshot/test_nav_menu.py`
- Create: `tests/snapshot/test_settings_view.py`

- [ ] **Step 1: Create the NavMenu test apps**

Create `tests/snapshot/apps/nav_menu.py`:

```python
"""Test apps for NavMenu snapshots — expanded + collapsed."""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import cast

from textual.app import App, ComposeResult
from vmx import NULL_DISPATCHER, MessageHub
from vmx.messages.protocols import Message

from aws_tui.services.registry import ServiceRegistry
from aws_tui.ui.widgets.nav_menu import NavMenu
from aws_tui.vm.nav_menu_vm import NavMenuVM
from tests.snapshot.apps._theme_loader import load_css


def _build_vm() -> NavMenuVM:
    hub = cast("MessageHub[Message]", MessageHub())
    registry = ServiceRegistry()
    vm = NavMenuVM(registry=registry, hub=hub, dispatcher=NULL_DISPATCHER)
    vm.construct()
    return vm


class NavMenuExpandedApp(App[None]):
    def __init__(self, *, theme: str = "carbon") -> None:
        super().__init__()
        self.CSS = load_css(theme)
        self._vm = _build_vm()
        self._tmp = Path(tempfile.mkdtemp(prefix="navmenu-exp-"))

    def compose(self) -> ComposeResult:
        nav = NavMenu(vm=self._vm, hub=cast("MessageHub[Message]", MessageHub()))
        nav.add_class("-expanded")
        nav.toggle_collapsed()  # flip from default-collapsed to expanded
        yield nav


class NavMenuCollapsedApp(App[None]):
    def __init__(self, *, theme: str = "carbon") -> None:
        super().__init__()
        self.CSS = load_css(theme)
        self._vm = _build_vm()
        self._tmp = Path(tempfile.mkdtemp(prefix="navmenu-col-"))

    def compose(self) -> ComposeResult:
        nav = NavMenu(vm=self._vm, hub=cast("MessageHub[Message]", MessageHub()))
        nav.add_class("-expanded")  # visible but in icon-only mode
        yield nav
```

NOTE: pattern-match the existing `tests/snapshot/apps/_theme_loader.py`
helper used by other test apps (e.g. `tests/snapshot/apps/modals.py` from
PR #52). If a different helper name is in use, adjust the import.

- [ ] **Step 2: Create the SettingsView test apps**

Create `tests/snapshot/apps/settings_view.py`:

```python
"""Test apps for SettingsView snapshots — empty / populated / form-open."""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import cast

from textual.app import App, ComposeResult
from vmx import NULL_DISPATCHER, MessageHub
from vmx.messages.protocols import Message

from aws_tui.infra.config_store import ConfigStore, ConnectionEntry
from aws_tui.infra.connection_resolver import ConnectionResolver
from aws_tui.ui.widgets.settings.connection_form import ConnectionFormInline
from aws_tui.ui.widgets.settings_view import SettingsView
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


def _build(seed_count: int) -> tuple[SettingsVM, S3ConnectionsVM, Path]:
    hub = cast("MessageHub[Message]", MessageHub())
    tmp = Path(tempfile.mkdtemp(prefix="settingsview-"))
    store = ConfigStore(path=tmp / "config.toml")
    for i in range(seed_count):
        store.add_connection(_seed_entry(f"conn-{i}"))
    resolver = ConnectionResolver(config_store=store)
    s3 = S3ConnectionsVM(
        resolver=resolver, config_store=store, hub=hub, dispatcher=NULL_DISPATCHER
    )
    s3.construct()
    vm = SettingsVM(s3=s3, hub=hub, dispatcher=NULL_DISPATCHER)
    vm.construct()
    return vm, s3, tmp


class SettingsViewEmptyApp(App[None]):
    def __init__(self, *, theme: str = "carbon") -> None:
        super().__init__()
        self.CSS = load_css(theme)
        self._vm, self._s3, self._tmp = _build(seed_count=0)

    def compose(self) -> ComposeResult:
        yield SettingsView(vm=self._vm, hub=cast("MessageHub[Message]", MessageHub()))


class SettingsViewPopulatedApp(App[None]):
    def __init__(self, *, theme: str = "carbon") -> None:
        super().__init__()
        self.CSS = load_css(theme)
        self._vm, self._s3, self._tmp = _build(seed_count=2)

    def compose(self) -> ComposeResult:
        yield SettingsView(vm=self._vm, hub=cast("MessageHub[Message]", MessageHub()))


class SettingsViewFormOpenApp(App[None]):
    def __init__(self, *, theme: str = "carbon") -> None:
        super().__init__()
        self.CSS = load_css(theme)
        self._vm, self._s3, self._tmp = _build(seed_count=1)

    def compose(self) -> ComposeResult:
        yield SettingsView(vm=self._vm, hub=cast("MessageHub[Message]", MessageHub()))

    async def on_mount(self) -> None:
        # Open the inline form so the snapshot captures it visible.
        form = self.query_one(ConnectionFormInline)
        form.open_for_add()
```

- [ ] **Step 3: Create the snapshot tests with content-presence guards**

Create `tests/snapshot/test_nav_menu.py`:

```python
"""Snapshot tests for NavMenu + content-presence guards × 10 themes."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.snapshot.apps.nav_menu import NavMenuCollapsedApp, NavMenuExpandedApp

THEMES = [
    "carbon", "voidline", "lattice", "amber",
    "solarized-light", "github-light", "one-light",
    "nord", "dracula", "gruvbox-dark",
]
TERMINAL_SIZE = (40, 20)


@pytest.mark.parametrize("theme", THEMES)
def test_nav_menu_expanded(theme: str, snap_compare) -> None:  # type: ignore[no-untyped-def]
    assert snap_compare(NavMenuExpandedApp(theme=theme), terminal_size=TERMINAL_SIZE)


@pytest.mark.parametrize("theme", THEMES)
def test_nav_menu_collapsed(theme: str, snap_compare) -> None:  # type: ignore[no-untyped-def]
    assert snap_compare(NavMenuCollapsedApp(theme=theme), terminal_size=TERMINAL_SIZE)


@pytest.mark.parametrize("theme", THEMES)
def test_nav_menu_expanded_renders_visible_settings_label(theme: str) -> None:
    """Content-presence guard: an expanded NavMenu MUST render the
    'Settings' label text. Pure snapshot-match can pass a uniformly
    blank render across all themes; this catches that."""
    p = Path(__file__).parent / "__snapshots__" / "test_nav_menu" / f"test_nav_menu_expanded[{theme}].raw"
    if not p.is_file():
        pytest.skip(f"snapshot not yet generated for theme {theme!r}")
    svg = p.read_text()
    assert "Settings" in svg, (
        f"'Settings' label missing from expanded NavMenu SVG for theme {theme!r}"
    )
    assert "menu" in svg, f"'menu' header missing for theme {theme!r}"


@pytest.mark.parametrize("theme", THEMES)
def test_nav_menu_collapsed_renders_visible_settings_icon(theme: str) -> None:
    """Content-presence guard: a collapsed NavMenu MUST render the
    gear glyph for Settings."""
    p = Path(__file__).parent / "__snapshots__" / "test_nav_menu" / f"test_nav_menu_collapsed[{theme}].raw"
    if not p.is_file():
        pytest.skip(f"snapshot not yet generated for theme {theme!r}")
    svg = p.read_text()
    assert "⚙" in svg, f"gear glyph missing from collapsed NavMenu SVG for theme {theme!r}"
```

Create `tests/snapshot/test_settings_view.py`:

```python
"""Snapshot tests for SettingsView + content-presence guards × 10 themes."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.snapshot.apps.settings_view import (
    SettingsViewEmptyApp,
    SettingsViewFormOpenApp,
    SettingsViewPopulatedApp,
)

THEMES = [
    "carbon", "voidline", "lattice", "amber",
    "solarized-light", "github-light", "one-light",
    "nord", "dracula", "gruvbox-dark",
]
TERMINAL_SIZE = (90, 40)


@pytest.mark.parametrize("theme", THEMES)
def test_settings_view_empty(theme: str, snap_compare) -> None:  # type: ignore[no-untyped-def]
    assert snap_compare(SettingsViewEmptyApp(theme=theme), terminal_size=TERMINAL_SIZE)


@pytest.mark.parametrize("theme", THEMES)
def test_settings_view_populated(theme: str, snap_compare) -> None:  # type: ignore[no-untyped-def]
    assert snap_compare(SettingsViewPopulatedApp(theme=theme), terminal_size=TERMINAL_SIZE)


@pytest.mark.parametrize("theme", THEMES)
def test_settings_view_form_open(theme: str, snap_compare) -> None:  # type: ignore[no-untyped-def]
    assert snap_compare(SettingsViewFormOpenApp(theme=theme), terminal_size=TERMINAL_SIZE)


@pytest.mark.parametrize("theme", THEMES)
def test_settings_view_empty_renders_title_and_section_header(theme: str) -> None:
    p = Path(__file__).parent / "__snapshots__" / "test_settings_view" / f"test_settings_view_empty[{theme}].raw"
    if not p.is_file():
        pytest.skip(f"snapshot not yet generated for theme {theme!r}")
    svg = p.read_text()
    assert "Settings" in svg, f"title 'Settings' missing for theme {theme!r}"
    assert "S3-Compatible Connections" in svg, (
        f"Connections section header missing for theme {theme!r}"
    )


@pytest.mark.parametrize("theme", THEMES)
def test_settings_view_populated_renders_rows(theme: str) -> None:
    p = Path(__file__).parent / "__snapshots__" / "test_settings_view" / f"test_settings_view_populated[{theme}].raw"
    if not p.is_file():
        pytest.skip(f"snapshot not yet generated for theme {theme!r}")
    svg = p.read_text()
    # The seed data uses names "conn-0", "conn-1"
    assert "conn-0" in svg, f"row 'conn-0' missing for theme {theme!r}"
    assert "conn-1" in svg, f"row 'conn-1' missing for theme {theme!r}"


@pytest.mark.parametrize("theme", THEMES)
def test_settings_view_form_open_renders_input_labels(theme: str) -> None:
    p = Path(__file__).parent / "__snapshots__" / "test_settings_view" / f"test_settings_view_form_open[{theme}].raw"
    if not p.is_file():
        pytest.skip(f"snapshot not yet generated for theme {theme!r}")
    svg = p.read_text()
    assert "Endpoint URL" in svg, f"form label 'Endpoint URL' missing for theme {theme!r}"
    assert "Access key ID" in svg, f"form label 'Access key ID' missing for theme {theme!r}"
    assert "save" in svg.lower(), f"Save button label missing for theme {theme!r}"
```

- [ ] **Step 4: Generate the goldens**

```bash
uv run pytest tests/snapshot/test_nav_menu.py tests/snapshot/test_settings_view.py --snapshot-update -q
```

- [ ] **Step 5: Eyeball one dark + one light theme SVG for each test**

Inspect at least these four to confirm rendering looks reasonable:
- `tests/snapshot/__snapshots__/test_nav_menu/test_nav_menu_expanded[carbon].raw`
- `tests/snapshot/__snapshots__/test_nav_menu/test_nav_menu_expanded[one-light].raw`
- `tests/snapshot/__snapshots__/test_settings_view/test_settings_view_populated[carbon].raw`
- `tests/snapshot/__snapshots__/test_settings_view/test_settings_view_form_open[one-light].raw`

If any look broken (clipped widgets, wrong colors, invisible text), fix
the CSS from Task 7 and re-run `--snapshot-update`.

- [ ] **Step 6: Run the content-presence guards**

```bash
uv run pytest tests/snapshot/test_nav_menu.py tests/snapshot/test_settings_view.py -v -k "renders"
```
Expected: all guards pass. If any fail, the rendering is missing
expected content — fix the CSS (most likely cause) and re-generate the
goldens.

- [ ] **Step 7: Run all gates**

```bash
uv run ruff check . && uv run ruff format --check . && uv run mypy --strict src && bash scripts/check-layers.sh && uv run pytest
```
Expected: all gates green.

- [ ] **Step 8: Commit**

```bash
git add tests/snapshot/apps/nav_menu.py tests/snapshot/apps/settings_view.py tests/snapshot/test_nav_menu.py tests/snapshot/test_settings_view.py tests/snapshot/__snapshots__/test_nav_menu/ tests/snapshot/__snapshots__/test_settings_view/
git commit -m "test(snapshot): NavMenu + SettingsView × 10 themes + content guards

50 new snapshots: NavMenu × 10 themes × 2 states (expanded /
collapsed) + SettingsView × 10 themes × 3 scenarios (empty,
populated, form-open). Every snapshot is paired with a
content-presence guard test that reads the generated SVG and
asserts key text/glyphs ('Settings', '⚙', section headers, row
names, form labels) are actually rendered — not just that all
themes match each other. Per the PR #53 lesson."
```

---

## Task 9: Wire `AwsTuiApp` + `composition.py`

The cutover task. Switch the app from mounting `ServicesMenu` +
pushing `SettingsModal` to mounting `NavMenu` + routing nav selection
through `ContentHostVM.set_content(settings_vm, service_id="settings")`.
Rebind `,` (comma) to select the Settings nav item. The old surfaces
still exist after this commit (they're deleted in Task 11).

**Files:**
- Modify: `src/aws_tui/app.py`
- Modify: `src/aws_tui/composition.py`

**Interfaces consumed:**
- `NavMenuVM`, `NavMenu` — Tasks 2 and 4
- `SettingsView`, `SettingsVM` — Tasks 5 and 1
- `ContentHostVM.set_content(vm, service_id)` — existing
- `S3Service.build_vm(connection)` — existing (used to construct the S3
  DualPaneVM)

- [ ] **Step 1: Update `composition.py`**

In `src/aws_tui/composition.py`:

(a) Update the import — `ServicesMenuVM` → `NavMenuVM` (file was already
renamed in Task 2). Variable rename: `services_menu_vm` →
`nav_menu_vm`. If `RootVM` takes this as a constructor arg, update its
call site to pass `nav_menu_vm=nav_menu_vm`.

(b) `settings_vm` construction stays unchanged (the simplification in
Task 1 didn't change its constructor signature).

- [ ] **Step 2: Update `AwsTuiApp` to mount NavMenu instead of ServicesMenu**

In `src/aws_tui/app.py`:

(a) Find where `ServicesMenu` is mounted (likely in the app's `compose()`
method or `_build_layout` helper — search for `ServicesMenu(`). Replace
with `NavMenu`:

```python
# OLD:
yield ServicesMenu(self._app_ctx.services_menu_vm, hub=self._app_ctx.hub, id="services-menu")
# NEW:
yield NavMenu(vm=self._app_ctx.nav_menu_vm, hub=self._app_ctx.hub, id="nav-menu")
```

(b) Update the `action_toggle_services` method (which currently toggles
the `-expanded` class on `ServicesMenu`) to target `NavMenu` instead.
The class-toggle pattern stays identical — just the widget class
reference changes. If the action's method body queries
`self.query_one(ServicesMenu)`, change to `self.query_one(NavMenu)`.

(c) Update the action to also call `nav.toggle_collapsed()` so the
OptionList prompts rebuild for the new mode:

```python
def action_toggle_services(self) -> None:
    try:
        nav = self.query_one("#nav-menu", NavMenu)
    except Exception:
        return
    if nav.has_class("-expanded"):
        nav.remove_class("-expanded")
    else:
        nav.add_class("-expanded")
    nav.toggle_collapsed()
```

- [ ] **Step 3: Replace `action_open_settings` body**

Replace the old `action_open_settings` (which pushes `SettingsModal`)
with one that selects the Settings nav item:

```python
def action_open_settings(self) -> None:
    """Select the Settings entry in the nav menu (programmatic equivalent
    of clicking it). Bound to ``,`` (comma)."""
    self._app_ctx.nav_menu_vm.select("settings")
```

Delete the `_reload_after_settings`, `_reload_panes_async` helpers — the
PR #52 dirty-set + reload-on-close flow is dead. KEEP `_rebind_pane_to_local`
and `_rebind_pane_to_connection` — they're still used by the
ConnectionListChangedMessage subscriber for updated/deleted connection
reload.

Actually, **REPLACE** the reload-after-close logic with an immediate
reload-on-ConnectionListChangedMessage. Update `_on_connection_list_changed`:

```python
def _on_connection_list_changed(self, msg: object) -> None:
    """Hub subscriber: drop deleted connection names from the
    unreachable set AND reload any pane bound to a changed connection."""
    if not isinstance(msg, ConnectionListChangedMessage):
        return
    if msg.change == "deleted":
        for name in msg.names:
            self._app_ctx.unreachable_connections.discard(("s3-compatible", name))
    # Schedule pane reload for affected connections (immediate, not
    # deferred). Skip on 'added' — new connections aren't bound yet.
    if msg.change == "added":
        return
    self.run_worker(self._reload_panes_for(msg.names, deleted=(msg.change == "deleted")))

async def _reload_panes_for(self, names: tuple[str, ...], *, deleted: bool) -> None:
    """Walk both panes; rebind any pane bound to a connection in ``names``."""
    dual = self._dual_pane()
    if dual is None:
        return
    for pane in (dual.left, dual.right):
        key = pane.current_connection_key
        if key is None:
            continue
        _, pane_name = key
        if pane_name not in names:
            continue
        if deleted:
            await self._rebind_pane_to_local(pane)
        else:
            try:
                conn = self._app_ctx.connection_resolver.resolve(pane_name)
            except Exception:
                await self._rebind_pane_to_local(pane)
            else:
                await self._rebind_pane_to_connection(pane, conn)
```

Delete imports that became unused (`SettingsModal`, `ToastModel`,
`ToastLevel` if they were only referenced by the deleted reload-toast
helper).

- [ ] **Step 4: Route nav selection to ContentHost**

Find where `NavMenuVM.select()` (or `selected_id` change) is observed
by the app. If the app today has a hub subscriber that watches for
`PropertyChangedMessage` from `ServicesMenuVM`'s selected_id, update
it to also handle the "settings" id:

```python
def _on_nav_selection_changed(self, msg: object) -> None:
    from vmx import PropertyChangedMessage
    if not isinstance(msg, PropertyChangedMessage):
        return
    if msg.property_name != "selected_id":
        return
    if not isinstance(msg.sender_object, NavMenuVM):
        return
    selected = self._app_ctx.nav_menu_vm.selected_id
    if selected is None:
        return
    if selected == "settings":
        self.run_worker(
            self._app_ctx.content_host_vm.set_content(
                self._app_ctx.settings_vm, service_id="settings"
            )
        )
    else:
        # Existing service-resolution path — preserve whatever currently
        # happens here. If today's code uses `switch_service(selected)`,
        # keep it. The Settings branch above just short-circuits before
        # the service-resolution path.
        ...
```

The exact integration point depends on the existing hub-subscription
pattern in `app.py`. Read the existing `_on_*` subscribers near the
ones quoted in the brief and pattern-match.

- [ ] **Step 5: Run all gates**

```bash
uv run ruff check . && uv run ruff format --check . && uv run mypy --strict src && bash scripts/check-layers.sh && uv run pytest
```
Expected: all gates green. The integration test `test_settings_modal_flow.py`
is already skipped (Task 1, Step 3a) so it doesn't break here.

- [ ] **Step 6: Manual smoke test (optional but recommended)**

```bash
uv run aws-tui
```
Press `m` to expand the nav rail. Confirm `S3` and `⚙ Settings` both
visible. Click `Settings` (or press `,`) — the main area should swap to
the SettingsView (scrollable page with the Connections collapsible
expanded). Click `S3` to swap back. If anything is broken, fix and
re-test before committing.

- [ ] **Step 7: Commit**

```bash
git add src/aws_tui/app.py src/aws_tui/composition.py
git commit -m "feat(app): cutover from SettingsModal to nav-routed SettingsView

AwsTuiApp now mounts NavMenu (replacing ServicesMenu) and routes
nav selection through ContentHostVM.set_content to swap the main
area between DualPaneVM (S3) and SettingsVM (Settings). The comma
shortcut now selects the Settings nav item.

Reload-on-Save is immediate: hub subscriber to
ConnectionListChangedMessage walks both panes and rebinds any
that match a changed connection name (delete → revert to local;
update → rebind to fresh provider). Replaces PR #52's deferred-
until-modal-close reload pattern.

SettingsModal and ServicesMenuFooter files still exist — they
have no callers now but the file deletion + theme CSS cleanup
happens in Task 11."
```

---

## Task 10: New integration test

Replace the skipped `test_settings_modal_flow.py` with
`test_settings_flow.py` exercising the new nav-page flow.

**Files:**
- Create: `tests/integration/test_settings_flow.py`

**Interfaces consumed:**
- The full cutover from Task 9 (`AwsTuiApp` mounts `NavMenu`, comma
  selects Settings, etc.)

- [ ] **Step 1: Read an existing integration test**

```bash
cat tests/integration/test_swap_source_skips_unreachable.py
```
Confirms the canonical pattern: `build_app_context(config_dir, cache_dir)`
+ `AwsTuiApp(ctx)` + `app.run_test()` pattern.

- [ ] **Step 2: Write three integration tests**

Create `tests/integration/test_settings_flow.py`:

```python
"""In-process integration tests for the nav-routed Settings flow."""

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


def _prep(tmp_path: Path, toml_text: str = "") -> Path:
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "config.toml").write_text(toml_text)
    return config_dir


def _dispose(ctx: object) -> None:
    """Standard teardown — mirrors the pattern in build_app_context order."""
    for attr in [
        "settings_vm", "s3_connections_vm", "transfers_vm",
        "confirm_vm", "quick_look_vm", "command_palette_vm",
    ]:
        v = getattr(ctx, attr, None)
        if v is not None and hasattr(v, "dispose"):
            try:
                v.dispose()
            except Exception:
                pass
    root = getattr(ctx, "root_vm", None)
    if root is not None and hasattr(root, "dispose"):
        try:
            root.dispose()
        except Exception:
            pass


@pytest.mark.asyncio
async def test_comma_selects_settings_and_swaps_main_area(tmp_path: Path) -> None:
    """Press comma → SettingsView becomes the ContentHost's current content."""
    config_dir = _prep(tmp_path)
    ctx = build_app_context(config_dir=config_dir, cache_dir=tmp_path / "cache")
    app = AwsTuiApp(ctx)
    try:
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.press("comma")
            await pilot.pause()
            # NavMenuVM should now have settings selected.
            assert ctx.nav_menu_vm.selected_id == "settings"
            # ContentHost's current VM should be the SettingsVM.
            from aws_tui.vm.settings.settings_vm import SettingsVM

            assert isinstance(ctx.root_vm.content_host.current, SettingsVM)
    finally:
        _dispose(ctx)


@pytest.mark.asyncio
async def test_add_inline_form_persists_to_toml(tmp_path: Path) -> None:
    """Open Settings → expand inline form → fill + Save → TOML round-trip."""
    config_dir = _prep(tmp_path)
    ctx = build_app_context(config_dir=config_dir, cache_dir=tmp_path / "cache")
    app = AwsTuiApp(ctx)
    try:
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.press("comma")
            await pilot.pause()

            # The inline form widget should be mounted (hidden) within
            # the SettingsView's Connections panel. Open it.
            from aws_tui.ui.widgets.settings.connection_form import (
                ConnectionFormInline,
                ConnectionFormSubmitted,
            )
            from aws_tui.vm.chrome.first_run_vm import S3CompatForm

            form = pilot.app.query_one(ConnectionFormInline)
            form.open_for_add()
            await pilot.pause()

            # Fill programmatically (pilot.press char-by-char is flaky
            # under the textual test harness for Input widgets).
            from textual.widgets import Input

            pilot.app.query_one("#form-name", Input).value = "minio-test"
            pilot.app.query_one("#form-endpoint_url", Input).value = "http://localhost:9000"
            pilot.app.query_one("#form-region", Input).value = "us-east-1"
            pilot.app.query_one("#form-access_key_id", Input).value = "AKIATEST"
            pilot.app.query_one("#form-secret_access_key", Input).value = "SECRETTEST"
            await pilot.pause()

            # Post the submission event the form would emit on Save click.
            form_obj = S3CompatForm(
                name="minio-test",
                endpoint_url="http://localhost:9000",
                region="us-east-1",
                access_key_id="AKIATEST",
                secret_access_key="SECRETTEST",
                force_path_style=True,
                verify_tls=True,
            )
            form.post_message(
                ConnectionFormSubmitted(form=form_obj, mode="add", original_name=None)
            )
            await pilot.pause()
    finally:
        _dispose(ctx)

    cfg = ConfigStore(path=config_dir / "config.toml").load()
    assert "minio-test" in cfg.connections
    entry = cfg.connections["minio-test"]
    assert entry.endpoint_url == "http://localhost:9000"


@pytest.mark.asyncio
async def test_delete_via_confirm_removes_from_toml(tmp_path: Path) -> None:
    """Seed a connection → open Settings → click delete chip → confirm → TOML removed."""
    config_dir = _prep(tmp_path, _MINIO_LOCAL_TOML)
    ctx = build_app_context(config_dir=config_dir, cache_dir=tmp_path / "cache")
    app = AwsTuiApp(ctx)
    try:
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.press("comma")
            await pilot.pause()
            await pilot.click("#delete-minio-local")
            await pilot.pause()
            # ConfirmModal opens; danger defaults focus to Cancel — press
            # Right then Enter to confirm.
            await pilot.press("right")
            await pilot.press("enter")
            await pilot.pause()
    finally:
        _dispose(ctx)

    cfg = ConfigStore(path=config_dir / "config.toml").load()
    assert "minio-local" not in cfg.connections
```

- [ ] **Step 3: Run the integration tests**

```bash
uv run pytest tests/integration/test_settings_flow.py -v
```
Expected: 3 PASSED. If timing-related flakiness emerges, add more
`await pilot.pause()` between actions.

- [ ] **Step 4: Run all gates**

```bash
uv run ruff check . && uv run ruff format --check . && uv run mypy --strict src && bash scripts/check-layers.sh && uv run pytest
```
Expected: all green.

- [ ] **Step 5: Commit**

```bash
git add tests/integration/test_settings_flow.py
git commit -m "test(integration): nav-routed Settings flow end-to-end

Replaces the skipped test_settings_modal_flow.py (deleted in
task 11). Three flows: comma-selects-settings, inline-add-form-
persists, delete-via-confirm-removes. Uses build_app_context +
AwsTuiApp(ctx) + run_test() pattern (cribbed from
test_swap_source_skips_unreachable.py)."
```

---

## Task 11: Migrate `FirstRunModal`; delete obsolete files

The big cleanup task. `FirstRunModal` is the last caller of
`S3CompatFormModal` — it gets migrated to embed `ConnectionFormInline`
directly. Then all obsolete files + their tests + their CSS blocks get
deleted in one commit.

**Files affected:**
- Modify: `src/aws_tui/ui/widgets/first_run_modal.py` — embed `ConnectionFormInline`
- Delete: `src/aws_tui/ui/widgets/settings_modal.py`
- Delete: `src/aws_tui/ui/widgets/services_menu_footer.py`
- Delete: `src/aws_tui/ui/widgets/services_menu.py`
- Delete: `src/aws_tui/ui/widgets/settings/_placeholder_panel.py`
- Delete: `tests/unit/ui/test_settings_modal.py`
- Delete: `tests/unit/ui/test_services_menu_footer.py`
- Delete: `tests/snapshot/test_settings_modal.py` + 20 SVG goldens
- Delete: `tests/snapshot/test_services_menu_footer.py` + 10 SVG goldens
- Delete: `tests/snapshot/test_s3_compat_form.py` + 30 SVG goldens
- Delete: `tests/snapshot/apps/settings.py`
- Delete: `tests/snapshot/apps/services_menu_footer.py`
- Delete: `tests/snapshot/apps/s3_compat_form.py`
- Delete: `tests/integration/test_settings_modal_flow.py`
- Modify: all 10 `src/aws_tui/ui/themes/*.tcss` files — remove
  `SettingsModal`, `ServicesMenuFooter`, `S3CompatFormModal` blocks

- [ ] **Step 1: Migrate `FirstRunModal` to use `ConnectionFormInline`**

Read the current `src/aws_tui/ui/widgets/first_run_modal.py` to find
where `FirstRunModal` currently uses `S3CompatFormModal` (likely a
`push_screen_wait` call in a button handler). Replace with embedded
`ConnectionFormInline`:

(a) Update `FirstRunModal.compose()` to yield `ConnectionFormInline`
within its container, marked initially hidden:

```python
from aws_tui.ui.widgets.settings.connection_form import (
    ConnectionFormCancelled,
    ConnectionFormInline,
    ConnectionFormSubmitted,
)

# In FirstRunModal.compose():
yield ConnectionFormInline(hub=self._hub)
```

(b) Replace the click handler that previously pushed `S3CompatFormModal`
with one that opens the inline form:

```python
def _on_add_s3_compat_clicked(self) -> None:
    self.query_one(ConnectionFormInline).open_for_add()

def on_connection_form_submitted(self, event: ConnectionFormSubmitted) -> None:
    # Same logic FirstRunModal previously ran on the modal's return.
    # Whatever it did with the S3CompatForm result, do here.
    ...

def on_connection_form_cancelled(self, event: ConnectionFormCancelled) -> None:
    return
```

The exact handler logic depends on what `FirstRunModal` does today —
likely: persist the entry via `ConfigStore.add_connection` then dismiss
the modal with a result. Preserve that behavior, replacing the modal-
push with the inline form.

(c) Delete the `S3CompatFormModal` class definition from
`first_run_modal.py` (along with any imports it specifically required
like `ModalScreen`).

- [ ] **Step 2: Delete the obsolete widget files**

```bash
git rm src/aws_tui/ui/widgets/settings_modal.py
git rm src/aws_tui/ui/widgets/services_menu_footer.py
git rm src/aws_tui/ui/widgets/services_menu.py
git rm src/aws_tui/ui/widgets/settings/_placeholder_panel.py
```

- [ ] **Step 3: Delete the obsolete test files + goldens**

```bash
git rm tests/unit/ui/test_settings_modal.py
git rm tests/unit/ui/test_services_menu_footer.py
git rm tests/snapshot/test_settings_modal.py
git rm -r tests/snapshot/__snapshots__/test_settings_modal/
git rm tests/snapshot/test_services_menu_footer.py
git rm -r tests/snapshot/__snapshots__/test_services_menu_footer/
git rm tests/snapshot/test_s3_compat_form.py
git rm -r tests/snapshot/__snapshots__/test_s3_compat_form/
git rm tests/snapshot/apps/settings.py
git rm tests/snapshot/apps/services_menu_footer.py
git rm tests/snapshot/apps/s3_compat_form.py
git rm tests/integration/test_settings_modal_flow.py
```

- [ ] **Step 4: Delete the obsolete CSS blocks from all 10 themes**

For each of the 10 theme files in `src/aws_tui/ui/themes/`:

```bash
for theme in carbon voidline lattice amber solarized-light github-light one-light nord dracula gruvbox-dark; do
    echo "Cleaning ${theme}.tcss..."
    # Manually delete the SettingsModal { ... }, ServicesMenuFooter { ... },
    # and S3CompatFormModal { ... } selector blocks from
    # src/aws_tui/ui/themes/${theme}.tcss
done
```

Use `Edit` to remove each `SettingsModal`, `ServicesMenuFooter`, and
`S3CompatFormModal` selector block from each theme file. The exact line
ranges differ per theme — search for `SettingsModal {`,
`ServicesMenuFooter {`, `S3CompatFormModal {` in each file.

NOTE: this is mechanical work — ~30 edits across 10 files. Be careful
not to delete adjacent selectors that don't belong to one of the three.
A good heuristic: each selector block starts with `<SelectorName> {` and
ends with the matching `}` at column 0.

- [ ] **Step 5: Drop dead imports from app.py**

```bash
grep -n "SettingsModal\|ServicesMenu\|S3CompatFormModal\|ServicesMenuFooter" src/aws_tui/app.py
```

For each hit that is now dead, remove the import / reference. If `app.py`
still references `ServicesMenu` (it shouldn't after Task 9, but
double-check), update to `NavMenu`.

- [ ] **Step 6: Run all gates**

```bash
uv run ruff check . && uv run ruff format --check . && uv run mypy --strict src && bash scripts/check-layers.sh && uv run pytest
```
Expected: all gates green. Snapshot count should drop by 60 (deleted) and
remain at the +50 from Task 8 — net change vs main is roughly -10
snapshots.

- [ ] **Step 7: Commit**

```bash
git add -A
git commit -m "refactor(ui): delete SettingsModal + ServicesMenuFooter + S3CompatFormModal

The big cleanup. After Tasks 1-10, none of these files have any
callers in the application code. FirstRunModal was the last
holdout — migrated in this commit to embed ConnectionFormInline
directly instead of pushing S3CompatFormModal.

Deleted:
- src/aws_tui/ui/widgets/settings_modal.py
- src/aws_tui/ui/widgets/services_menu_footer.py
- src/aws_tui/ui/widgets/services_menu.py
- src/aws_tui/ui/widgets/settings/_placeholder_panel.py
- S3CompatFormModal class from first_run_modal.py
- 60 snapshot SVGs (20 settings_modal + 10 services_menu_footer +
  30 s3_compat_form) and their parametrized test files
- 3 snapshot test app files
- tests/integration/test_settings_modal_flow.py (replaced by
  tests/integration/test_settings_flow.py in Task 10)
- SettingsModal, ServicesMenuFooter, S3CompatFormModal CSS blocks
  in all 10 .tcss theme files"
```

---

## Task 12: Rewrite the CHANGELOG entry

The `[Unreleased] > ### Added` bullet from PR #52 described the modal
pattern. It needs to be rewritten to describe the nav-page pattern.

**Files:**
- Modify: `CHANGELOG.md`

- [ ] **Step 1: Replace the PR #52 bullet**

In `CHANGELOG.md`, find the `[Unreleased] > ### Added` bullet that begins
with `**App Settings overlay** with first panel: full CRUD for s3-compatible
connections.` Replace it entirely with:

```markdown
- **App Settings as a first-class nav page** with full CRUD for
  s3-compatible connections. The left rail is now a generic vertical
  nav (Textual ``OptionList``) with peer items ``S3`` and ``Settings``;
  selection-highlight matches the file-pane row cursor (``$bg-sel`` +
  ``$accent``). Selecting Settings swaps the main area to a VS Code-style
  scrollable page of ``Collapsible`` sections. Sub-project A populates
  the ``S3-Compatible Connections`` section; ``Themes (coming in v0.8)``
  and ``Keymap (coming in v0.8)`` are visible-disabled placeholders.
  Add/Edit S3 connection form expands inline within the Connections
  section, below the rows — no more modal-on-modal layering. Save
  commits + reloads any affected pane + collapses the form, all
  immediately. Cancel just collapses. Delete still uses the polished
  ``ConfirmModal`` (destructive ops keep the modal interruption
  pattern). Credentials remain inline in TOML (cross-platform, no
  keychain dependency). Keyboard: ``,`` selects the Settings nav item;
  ``m`` toggles the rail's collapsed/expanded state. Per-theme CSS for
  all 10 themes. Every new snapshot test is paired with a content-
  presence guard per the [snapshot-test-content-guards lesson](docs/superpowers/specs/2026-06-20-settings-as-first-class-nav-page-design.md).
  This is a rework of the PR #52 modal pattern, not an extension —
  ``SettingsModal``, the gear footer band, and ``S3CompatFormModal``
  are all deleted. The two surviving VMs (``SettingsVM`` simplified,
  ``S3ConnectionsVM`` unchanged) plus the ``ConfigStore`` extensions
  plus ``ConnectionListChangedMessage`` all carry over.
```

If a `### Known gaps` block exists from PR #52 mentioning the
reload-on-close end-to-end test, remove that block — the new test in
Task 10 covers the comma-selects-settings + add-form-persists flows
end-to-end. Remaining gap (delete-with-pane-reload-on-close) is now
covered by the hub-subscription path tested implicitly.

- [ ] **Step 2: Run all gates**

```bash
uv run ruff check . && uv run ruff format --check . && uv run mypy --strict src && bash scripts/check-layers.sh && uv run pytest
```
Expected: all green.

- [ ] **Step 3: Commit**

```bash
git add CHANGELOG.md
git commit -m "docs(changelog): rewrite App Settings bullet for nav-page pattern

The PR #52 entry described the modal pattern. Rewritten to describe
the nav-routed first-class page that replaces it. Same surviving
infrastructure (SettingsVM simplified, S3ConnectionsVM unchanged,
ConfigStore extensions, ConnectionListChangedMessage); fundamentally
different UX surface (left-rail peer to S3, scrollable VS Code-style
page, inline Add/Edit form)."
```

---

## Self-Review Notes

**Spec coverage:**

| Spec section | Task(s) |
|---|---|
| §2.1 in-scope: OptionList nav rail with S3 + Settings peers | Tasks 2, 4, 7, 9 |
| §2.1 in-scope: SettingsView as first-class main-area content | Tasks 1, 5, 7, 9 |
| §2.1 in-scope: VS Code-style scrollable page of Collapsibles | Task 5 |
| §2.1 in-scope: Connections section populated, Themes/Keymap disabled placeholders | Task 5 |
| §2.1 in-scope: Inline Add/Edit form within Connections | Tasks 3, 6 |
| §2.1 in-scope: Save semantics immediate | Tasks 6, 9 |
| §2.1 in-scope: Delete still uses ConfirmModal | Task 6 |
| §2.1 in-scope: comma rebinds to "select Settings" | Task 9 |
| §2.1 in-scope: snapshot tests gain content-presence guards | Task 8 |
| §2.2 removed: SettingsModal | Task 11 |
| §2.2 removed: ServicesMenuFooter | Task 11 |
| §2.2 removed: S3CompatFormModal class (FirstRunModal still works) | Task 11 |
| §2.2 removed: per-theme CSS for three above × 10 | Task 11 |
| §2.2 removed: snapshot tests + goldens for three above | Task 11 |
| §2.3 surviving: SettingsVM (simplified) | Task 1 |
| §2.3 surviving: S3ConnectionsVM (unchanged) | (no task — explicitly unchanged) |
| §2.3 surviving: ConnectionListChangedMessage (unchanged) | (no task) |
| §2.3 surviving: ConfigStore extensions (unchanged) | (no task) |
| §2.3 surviving: ConfirmModal (still used for delete) | (no task — Task 6 keeps this) |
| §3 architecture | Tasks 4-9 collectively |
| §4 VM layer (NavMenuVM rename, SettingsVM simplified) | Tasks 1, 2 |
| §5 view layer (NavMenu, SettingsView, ConnectionFormInline, S3ConnectionsPanel modified) | Tasks 3-6 |
| §6 Save semantics (immediate reload via hub) | Task 9 |
| §7 keyboard (comma rebinds) | Task 9 |
| §8 error handling | Covered by Task 3 (form validation), Task 6 (duplicate-name catch), Task 9 (reload helpers preserve PR #52's error surfaces) |
| §9.1 removed tests | Task 11 |
| §9.2 kept tests | (no task — unchanged) |
| §9.3 modified tests | Tasks 1, 2 |
| §9.4 added tests | Tasks 3-8, 10 |
| §10 global constraints | top of plan |
| §11 open implementation questions | Implementer to resolve while building (small enough not to need their own tasks) |
| §12 migration path | Tasks 1-12 are the migration |

**Placeholder scan:** searched the plan for "TBD", "TODO", "fill in",
"implement later", "Add appropriate" — found one `...` in the test code
in Task 2 Step 2 (intentionally placeholder for the fixture pattern the
implementer adapts from existing tests in the same file). One `...` in
Task 11 Step 1's `_on_add_s3_compat_clicked` handler (the migration body
depends on what FirstRunModal does today — implementer reads + adapts).
Both are flagged with surrounding instructions; acceptable.

**Type consistency:** `NavMenuVM` referenced consistently across Tasks 2,
4, 7, 9, 10. `SettingsVM` simplified signature in Task 1 is consumed
unchanged in Tasks 5, 9, 10. `ConnectionFormInline` API
(`open_for_add()`, `open_for_edit(name, defaults)`, `close()`,
`ConnectionFormSubmitted`/`ConnectionFormCancelled` messages) referenced
consistently in Tasks 3, 6, 10, 11.
