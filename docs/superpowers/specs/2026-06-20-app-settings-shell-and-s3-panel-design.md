# App Settings Shell + S3 Connections Panel — Design Spec

> **SUPERSEDED** by
> [`2026-06-20-settings-as-first-class-nav-page-design.md`](2026-06-20-settings-as-first-class-nav-page-design.md)
> (PR #54 rework, merged at `a7bd050`). This spec describes the
> modal-overlay architecture (`SettingsModal` opened via a gear-band
> footer, `S3CompatFormModal` opened on top of it) that shipped in
> PR #52 and was reworked away. `SettingsModal`, `ServicesMenuFooter`,
> and `S3CompatFormModal` no longer exist in `src/`. Retained for
> git-history continuity only — do **not** implement against this spec.

**Date:** 2026-06-20
**Status:** Superseded — see PR #54 rework spec.
**Branch:** `feat/app-settings-shell-and-s3-panel`
**Author:** Kaveh + Claude

---

## 1. Motivation

Adding or modifying an S3-compatible service (MinIO, Wasabi, Cloudflare R2,
self-hosted Ceph, etc.) today requires hand-editing
`~/.config/aws-tui/config.toml`, restarting the app, and dealing with TOML
syntax mistakes. The friction is high enough that the only s3-compatible
connection most users ever wire up is the one demonstrated in the README.

This sub-project builds the first cut of an **App Settings hub** — a single
themed overlay where every kind of app configuration eventually lives — and
ships its first panel: full CRUD over `kind = "s3-compatible"` TOML entries.

## 2. Scope

### 2.1 In scope (sub-project A)

- **Settings shell:** themed modal with a left-sidebar nav, body container,
  footer. Three sidebar entries from day one: `Connections` (active),
  `Themes` (disabled, marked `(soon)`), `Keymap` (disabled, marked `(soon)`).
- **Entry point:** a `⚙  Settings` gear button pinned to the bottom of the
  services column (the existing collapsible left rail). Keyboard shortcut: `,`.
- **S3 Connections panel:** list of all `kind = "s3-compatible"` TOML entries
  with per-row `[✎ Edit]` / `[✕ Delete]` chips, an `+ Add s3-compatible
  connection` button, and an empty state when none exist.
- **Add/Edit form:** reuse of the existing `S3CompatFormModal` (in
  `ui/widgets/first_run_modal.py`) with an `initial: Connection | None`
  parameter for edit pre-fill. Inline cleartext credentials in TOML
  (no keychain indirection — works cross-platform).
- **Delete confirmation:** reuse of the polished `ConfirmModal` from PR #50.
- **Persistence:** new `ConfigStore.update_connection()` and
  `ConfigStore.remove_connection()` methods, both atomic via `tempfile +
  os.replace` (same pattern as existing `add_connection`).
- **Save semantics:** each CRUD action commits immediately. Affected panes
  reload **on modal dismiss** (not mid-edit) — single summary toast.
- **Per-theme CSS:** ~15–18 new selectors per theme × 10 themes for the
  settings modal, S3 panel rows, gear footer band.
- **Tests:** unit (VMs + ConfigStore extensions), snapshot (×10 themes),
  in-process integration (gear-click flow + pane-reload flow).

### 2.2 Out of scope (deferred to sub-projects B and C)

- **Sub-project B — Themes panel:** refactor of the existing
  `ThemePickerModal` (with its live-preview-on-cursor and Esc-rollback flow
  from PR #47) into a settings section. Sidebar entry shipped today as
  `Themes (soon)`. **Separate spec, separate plan, separate PR.**
- **Sub-project C — Keybindings panel:** new UI for rebinding keys.
  Precondition: finishing the `KeymapStore` / `BindingResolver` wiring noted
  in the M6-deferred list. **Separate spec, separate plan, separate PR.**

### 2.3 Explicitly out of scope (this entire feature)

- **Editing AWS-kind connections** (named SSO profiles, auto-discovered AWS
  profiles). AWS credentials come from `aws sso login` and the app does not
  modify them. The Settings overlay shows only `kind = "s3-compatible"`
  entries; AWS entries remain managed via the AWS CLI and TOML files for
  named-profile defaults.
- **Connectivity testing on save.** The form persists and dismisses; the
  next time a pane binds to the connection, PR #49's unreachable-detection
  trips if the endpoint is bad. Uniform with how every other connection is
  validated today.
- **Renaming a connection on edit.** The name field is read-only in edit
  mode. To rename, delete and re-add. Avoids orphaning any future
  per-connection state (journal entries, keychain references in B/C).

## 3. Architecture

### 3.1 File map (new)

```
src/aws_tui/
  vm/settings/
    __init__.py
    settings_vm.py            # SettingsVM — active section + dirty-set
    s3_connections_vm.py      # S3ConnectionsVM — list + CRUD wiring
  ui/widgets/
    settings_modal.py         # SettingsModal Screen — sidebar + body shell
    services_menu_footer.py   # Gear footer band
    settings/
      __init__.py
      s3_connections_panel.py # The S3 panel content
      _placeholder_panel.py   # "Coming in v0.8" body for disabled sections
```

### 3.2 File map (modified)

- `src/aws_tui/infra/config_store.py` — add `update_connection()`,
  `remove_connection()`
- `src/aws_tui/vm/messages.py` — add `ConnectionListChangedMessage`
- `src/aws_tui/vm/services_menu_vm.py` — subscribe to
  `ConnectionListChangedMessage`, refresh filter
- `src/aws_tui/ui/widgets/first_run_modal.py` — make `S3CompatFormModal`
  accept `initial: Connection | None = None` for edit pre-fill; add `name`
  read-only mode
- `src/aws_tui/app/aws_tui_app.py` — add `action_open_settings`, bind to `,`;
  hub-subscribe to `ConnectionListChangedMessage` to drop deleted names from
  `AppContext.unreachable_connections`
- `src/aws_tui/ui/themes/*.tcss` (×10) — add settings-modal, s3-panel, gear
  footer selector blocks
- `src/aws_tui/infra/connection_resolver.py` — invalidate cache on
  `ConnectionListChangedMessage` if the resolver caches; otherwise no change
  (verify during implementation)

### 3.3 Layout

The settings modal opens centered, ~80 cols × ~28 rows (dark themes; per
ConfirmModal precedent, light themes can tighten):

```
┌─ Settings ──────────────────────────────────────────────────────────┐
│  ┌─ Sections ─────┐  ┌─ S3-Compatible Connections ──────────────┐  │
│  │ ▸ Connections  │  │  [the panel content goes here]            │  │
│  │   Themes (soon)│  │                                            │  │
│  │   Keymap (soon)│  │                                            │  │
│  └────────────────┘  └────────────────────────────────────────────┘  │
│                                                       [ Close ]      │
└──────────────────────────────────────────────────────────────────────┘
```

- **Sidebar (left, width: 22):** ListView with three rows. `Connections` is
  the cursor-active row by default. `Themes (soon)` and `Keymap (soon)` are
  rendered with disabled styling (`color: $text-muted`); keyboard nav (up/down/j/k)
  skips disabled rows. Clicking a disabled row is a no-op.
- **Body (right, fills remaining width):** a container that renders the
  panel widget for the active section. For sub-project A the only reachable
  panel is `S3ConnectionsPanel`.
- **Footer:** single `[ Close ]` button (right-aligned). Esc also dismisses.
  No global "Save" / "Cancel" — each CRUD action commits the TOML write
  immediately when its form / confirm dialog dismisses. The only thing
  deferred to modal-dismiss is the **pane reload** for connections an
  active pane was already bound to (see §4.3).

### 3.4 S3 connections panel layout

```
┌─ S3-Compatible Connections ─────────────────────────────────────┐
│  ┃ minio-local      http://localhost:9000   us-east-1  [✎] [✕] │
│  ┃ ceph-staging     https://ceph.internal   us-west-2  [✎] [✕] │
│                                                                  │
│             [ + Add s3-compatible connection ]                   │
└──────────────────────────────────────────────────────────────────┘
```

Each row:
- Single line, `height: 1`.
- Left accent rule (1 cell wide, `▎`): `$accent` if this is the
  currently-active connection in either pane; `$rule-dim` otherwise.
- Name (`width: 16`, ellipsis on overflow), endpoint (flex), region
  (`width: 10`), `[✎]` Edit chip, `[✕]` Delete chip.
- Chips reuse the flat 1-cell style from PR #50: `color: $accent;
  background: $bg-elev`; `[✕]` hover swaps to `background: $danger; color: $bg`.

Below the rows: a centered `[ + Add s3-compatible connection ]` button.

#### 3.4.1 Empty state

When `S3ConnectionsVM.connections` is empty:

```
┌─ S3-Compatible Connections ─────────────────────────────────────┐
│                                                                  │
│        No S3-compatible connections configured yet.              │
│                                                                  │
│        Add one to access MinIO, Wasabi, R2, etc. from            │
│        the same panes you use for AWS S3.                        │
│                                                                  │
│             [ + Add s3-compatible connection ]                   │
│                                                                  │
└──────────────────────────────────────────────────────────────────┘
```

### 3.5 Gear footer band

A new `ServicesMenuFooter` widget docked at the bottom of the services
column. Single row, `height: 1`, `background: $bg-elev`, `border-top:
$rule-dim`, contains a `⚙  Settings` button styled like a regular service
item but visually demarcated by the border-top and the elevated background.

When the services column is hamburger-collapsed (`display: none`), the
footer hides with it. The keyboard shortcut `,` opens the modal regardless
of column visibility.

## 4. VM layer

### 4.1 `SettingsVM`

```python
class SettingsVM:
    """Parent VM for the settings shell."""

    SECTIONS: Final = ("connections", "themes", "keymap")
    ENABLED: Final = frozenset({"connections"})  # B + C will add to this

    def __init__(self, *, s3: S3ConnectionsVM, hub: MessageHub, dispatcher: Dispatcher) -> None:
        self._hub = hub
        self._dispatcher = dispatcher
        self._s3 = s3
        self._active_section: str = "connections"
        self._dirty_connection_names: set[str] = set()
        self._sub: DisposableBase | None = None
        # ... inner ComponentVM, change_section_command, etc.

    # Subscribed to ConnectionListChangedMessage to accumulate names from
    # 'updated' and 'deleted' events (not 'added' — a brand-new connection
    # can't be bound to any pane yet) into _dirty_connection_names for the
    # reload-on-close logic.
    def _on_hub_message(self, msg: object) -> None: ...

    @property
    def active_section(self) -> str: ...
    @property
    def dirty_connection_names(self) -> frozenset[str]: ...
    @property
    def s3(self) -> S3ConnectionsVM: ...

    def change_section(self, section_id: str) -> None: ...   # no-op if disabled
```

Construct/destruct/dispose forward to the child `S3ConnectionsVM`.

### 4.2 `S3ConnectionsVM`

```python
class S3ConnectionsVM:
    """List + CRUD over kind='s3-compatible' connections."""

    def __init__(
        self,
        *,
        resolver: ConnectionResolver,
        config_store: ConfigStore,
        hub: MessageHub,
        dispatcher: Dispatcher,
    ) -> None: ...

    @property
    def connections(self) -> tuple[Connection, ...]:
        """Filtered to kind == 's3-compatible'; re-derived from the
        resolver on each access (after a CRUD, the resolver's cache —
        if any — is invalidated via the published message)."""

    def add(self, entry: ConnectionEntry) -> None:
        """Validate, persist, publish ConnectionListChangedMessage(..., 'added')."""

    def update(self, name: str, entry: ConnectionEntry) -> None:
        """Validate (name unchanged), persist, publish ('updated')."""

    def remove(self, name: str) -> None:
        """Persist, publish ('deleted')."""

    def can_remove(self, name: str) -> bool:
        """Always True for sub-project A; placeholder for future
        'connection is referenced by a journal entry' guard."""
```

Validation surface (live as user types in the form; final check at the VM):

- `name`: required, unique among existing connections, matches
  `^[A-Za-z0-9_-]{1,32}$` (valid TOML bare key)
- `endpoint_url`: required, starts with `http://` or `https://`, parses
  cleanly with `urllib.parse.urlparse` (scheme + netloc both non-empty)
- `region`: required, non-empty (no AWS-region-format check — third-party
  S3 services pick their own region strings)
- `access_key_id`: required, non-empty
- `secret_access_key`: required, non-empty

Validation errors surface as red borders on the invalid `Input` widgets;
the Save button is disabled until all five fields pass.

### 4.3 Reload-on-close

`SettingsVM._on_hub_message` adds any update/delete name to
`_dirty_connection_names`. When the SettingsModal calls
`SettingsVM.on_dismiss()`:

```python
def on_dismiss(self, *, panes: tuple[PaneVM, PaneVM]) -> None:
    affected: list[tuple[PaneVM, str | None]] = []
    for pane in panes:
        key = pane.current_connection_key
        if key is None:
            continue
        kind, name = key
        if kind != "s3-compatible":
            continue
        if name in self._dirty_connection_names:
            try:
                new_conn = self._resolver.get(name)
                affected.append((pane, name))  # update path
            except KeyError:
                affected.append((pane, None))  # delete path
    for pane, name in affected:
        if name is None:
            pane.swap_provider(LOCAL_CONNECTION)
        else:
            pane.swap_provider(self._resolver.get(name))
    if affected:
        self._publish_reload_toast(affected)
    self._dirty_connection_names.clear()
```

(`ConnectionResolver.get(name)` may need adding if not present; verify
during implementation. `LOCAL_CONNECTION` is the singleton local
connection used today for the default left pane.)

## 5. Persistence (infra)

### 5.1 `ConfigStore.update_connection(name, entry)`

```python
def update_connection(self, name: str, entry: ConnectionEntry) -> None:
    """Atomic update of an existing connection by name.

    Raises ``KeyError`` if no connection with that name exists.
    Raises ``ValueError`` if ``entry.name != name`` (renaming is
    not supported; the field is read-only on edit in the UI).
    """
```

### 5.2 `ConfigStore.remove_connection(name)`

```python
def remove_connection(self, name: str) -> None:
    """Atomic removal of a connection by name.

    Raises ``KeyError`` if no connection with that name exists.
    """
```

Both methods:
- Load current config via the existing `load()` path.
- Mutate the in-memory `Config` dataclass.
- Persist via the existing `save()` path (which uses `tempfile +
  os.replace` for crash-safety).
- No retry, no locking — the TOML file is single-user, no concurrent
  writers expected.

### 5.3 Schema notes

The TOML schema for s3-compatible entries with inline credentials:

```toml
[connections.minio-local]
kind = "s3-compatible"
endpoint_url = "http://localhost:9000"
region = "us-east-1"
access_key_id = "AKIA..."
secret_access_key = "secret..."
force_path_style = true
verify_tls = true
```

No `credentials = "keychain:..."` indirection for entries created by the
form. Existing entries that use the keychain indirection continue to work
at runtime (the connection resolver dispatches transparently). When such
an entry is opened in the edit form:

- The form pre-fills `access_key_id` and `secret_access_key` from the
  *resolved* `Connection` (already materialized by the resolver, so the
  fields are populated regardless of the storage backend).
- On Save, the entry is rewritten with **inline** credentials. The
  `credentials = "keychain:..."` line is dropped from TOML; the entry in
  macOS Keychain Access is left untouched but no longer referenced. This
  is one-way: the form does not have a "store in keychain" option in
  sub-project A.
- This conversion is intentional, not a bug. Users who want to keep
  keychain-backed credentials should edit the TOML by hand or skip the
  form for that entry.

## 6. Messages

### 6.1 `ConnectionListChangedMessage` (new)

```python
@dataclass(frozen=True, slots=True)
class ConnectionListChangedMessage:
    """Published by S3ConnectionsVM after each successful CRUD.

    Subscribers:
    - ConnectionResolver: invalidate any in-memory cache of the
      connection list (re-list on next query).
    - ServicesMenuVM: re-derive the filter of which services support
      the active connection.
    - AwsTuiApp: drop deleted names from AppContext.unreachable_connections.
    - SettingsVM: accumulate names in dirty_connection_names for the
      reload-on-close logic.
    """
    names: tuple[str, ...]
    change: Literal["added", "updated", "deleted"]
    sender_name: str = "s3_connections"

    @property
    def sender_object(self) -> object:
        return self
```

Added to `vm/messages.py` `__all__`.

### 6.2 No reuse of `ConnectionChangedMessage`

`ConnectionChangedMessage` already exists for the per-pane connection-switch
flow (published by `RootVM.switch_connection_with`). It is **not** reused
here — semantics are different (one switches an active binding, the other
announces a config-file change). Subscribers can listen to whichever they
care about.

## 7. Entry point wiring

### 7.1 `AwsTuiApp.action_open_settings`

```python
def action_open_settings(self) -> None:
    """Push the SettingsModal. Bound to `,` (comma)."""
    self.push_screen(SettingsModal(
        vm=self._settings_vm,
        panes=(self._dual_pane_vm.left, self._dual_pane_vm.right),
        # ... etc.
    ))
```

Binding added in the `BINDINGS` class attribute alongside existing actions.

### 7.2 Gear button click

The `ServicesMenuFooter` widget calls `app.action_open_settings()` on
button-click. Same path as the keyboard shortcut.

## 8. Error handling

| Failure | Surface | Behavior |
|---|---|---|
| TOML write fails (disk full, permissions) | `ConfigStore.save()` | Raises `OSError`. Caught by S3ConnectionsVM; row is not added/updated/deleted in-memory; toast: "Couldn't save config: <reason>." Form remains open with values intact. |
| TOML file corrupt at load time | `ConfigStore.load()` | Existing behavior (raises a typed error at startup). Out of scope for this feature. |
| Form validation fails | `S3CompatFormModal` | Save button stays disabled; invalid fields have red borders. No save attempt is made. |
| Duplicate name on add | `S3ConnectionsVM.add()` | Validates before persist; raises `ValueError`; surfaced as red border on name field with inline error text "Name already exists." |
| User edits a connection that another process changed mid-edit | n/a — single-user assumption | The TOML file is single-writer (this app instance). Concurrent edits from another running instance of the app are out of scope. The atomic `os.replace` in `save()` prevents partial writes; if the user has two app instances running, last-writer-wins on the TOML. |
| User deletes the only s3-compatible connection while a pane is bound to it | Reload-on-close | The pane reverts to `local` on modal dismiss. Toast: `"Left pane reverted to local (minio-local deleted)"`. |
| Pane reload fails (new endpoint unreachable) | Existing PR #49 mechanism | Pane shows the offline-state placeholder; user sees the standard "endpoint unreachable" rendering. No special handling in the settings flow. |

## 9. Testing

### 9.1 Unit tests

- `tests/unit/vm/settings/test_settings_vm.py` (new)
  - construct/dispose
  - `change_section` transitions for enabled section
  - `change_section` no-op for disabled section
  - `_on_hub_message` accumulates dirty names from `ConnectionListChangedMessage`
  - `dirty_connection_names` cleared after `on_dismiss`
- `tests/unit/vm/settings/test_s3_connections_vm.py` (new)
  - `connections` property filters to `kind == 's3-compatible'`
  - `add` persists via `ConfigStore.add_connection` + publishes message
  - `update` persists via `ConfigStore.update_connection` + publishes message
  - `remove` persists via `ConfigStore.remove_connection` + publishes message
  - validation: duplicate name rejected on add (raises `ValueError`)
  - validation: rename rejected on update (raises `ValueError`)
- `tests/unit/infra/test_config_store.py` (extend existing)
  - `test_update_connection_round_trip`: write → re-parse → assert equal
  - `test_remove_connection_round_trip`: write → re-parse → not present
  - `test_update_connection_unknown_name`: raises `KeyError`
  - `test_remove_connection_unknown_name`: raises `KeyError`
  - `test_update_connection_rename_disallowed`: raises `ValueError`
- `tests/unit/vm/test_messages.py` (extend)
  - `test_connection_list_changed_message_shape`: dataclass fields + Protocol conformance

### 9.2 Snapshot tests (×10 themes each)

- `tests/snapshot/test_settings_modal.py` (new)
  - `test_settings_modal_empty_connections`
  - `test_settings_modal_populated_connections`
  - `test_settings_modal_disabled_section_not_clickable`
- `tests/snapshot/test_s3_compat_form.py` (new; if `tests/snapshot/test_first_run_modal.py` already covers the add case, only edit + validation snapshots are net-new)
  - `test_s3_form_add_mode_empty`
  - `test_s3_form_edit_mode_prefilled`
  - `test_s3_form_validation_errors_visible`
- `tests/snapshot/test_services_menu_footer.py` (new)
  - `test_services_menu_with_gear_footer_expanded`
  - `test_services_menu_collapsed_hides_footer`

Test apps (`tests/snapshot/apps/`):
- `settings.py` — `SettingsModalApp(theme: str)` with seed S3 entries
- `s3_compat_form.py` — `S3CompatFormApp(theme: str, mode: str, errors: bool)`
- `services_menu_footer.py` — `ServicesMenuFooterApp(theme: str, collapsed: bool)`

### 9.3 In-process integration tests

`tests/integration/test_settings_modal_flow.py` (new):

- **`test_add_flow_persists_to_toml`:** mount AwsTuiApp with empty TOML, click
  gear button, assert modal opens, click "+ Add", fill form (name=minio-test,
  endpoint=http://localhost:9000, region=us-east-1, access_key_id=AKIATEST,
  secret_access_key=SECRETTEST), click Save, assert form dismisses, assert
  panel now shows 1 row, dismiss modal, re-load `ConfigStore` from disk,
  assert the new connection is persisted with all fields.
- **`test_edit_active_connection_reloads_pane_on_close`:** seed TOML with
  `minio-local`, mount app with left pane bound to `minio-local`, open
  settings, click Edit on the row, change endpoint URL, save form, dismiss
  modal, assert toast fires ("Reloaded left pane (minio-local updated)") and
  `pane.current_connection_key == ("s3-compatible", "minio-local")` (still
  bound but new endpoint took effect).
- **`test_delete_active_connection_reverts_pane_to_local`:** seed TOML with
  `minio-local`, mount app with left pane bound to `minio-local`, open
  settings, click Delete, confirm, dismiss modal, assert toast
  ("Left pane reverted to local") and `pane.current_connection_key is None`.

### 9.4 Snapshot count delta

Pre-feature: 144 snapshots (per memory). Post-feature: +30 snapshots
approximately (3 settings-modal cases + 3 form cases + 2 footer cases) × 10
themes = +80. Real delta will be confirmed in the implementation plan.

## 10. Global constraints

These apply to every task in the implementation plan and are not negotiable
without re-opening the design:

- **10-theme parity:** every per-theme CSS block must land in all 10 theme
  files (`carbon`, `voidline`, `lattice`, `amber`, `solarized-light`,
  `github-light`, `one-light`, `nord`, `dracula`, `gruvbox-dark`). Snapshots
  parametrize over all 10.
- **No new third-party dependencies.** Everything here uses existing Textual
  widgets (`ListView`, `Input`, `Button`, `Container`) and existing infra
  (`ConfigStore`, `ConnectionResolver`, `MessageHub`).
- **Inline credentials only** for entries the form creates. Keychain
  indirection for existing entries continues to work via the connection
  resolver but is not surfaced in the form's persistence path.
- **Reload-on-close, not mid-edit.** Affected panes reload exactly once,
  when the modal dismisses. No mid-edit pane reloads.
- **`,` (comma) is the keyboard shortcut.** Verify no existing binding
  conflict during implementation; if conflict, escalate before changing.
- **Layered architecture preserved.** `vm/settings/*` must not import from
  `ui/`; `ui/widgets/settings_modal.py` must not import from `infra/`
  directly (route via VM). Enforced by `scripts/check-layers.sh`.
- **All quality gates must stay green:** ruff, ruff format, mypy --strict
  (`src/`), `scripts/check-layers.sh`, the full pre-commit run, `uv build`
  + `twine check`, `uvx pip-audit --strict`. Each commit in the plan must
  leave all gates green.

## 11. Open implementation questions

These are *implementation-level* questions to resolve while building, not
design decisions to revisit:

- Does `ConnectionResolver` cache its `list()` output? (If so,
  `ConnectionListChangedMessage` subscriber must invalidate the cache.
  If not, no action needed.)
- Does `ConnectionResolver` have a `get(name)` method? If not, add one
  (small, scoped to the resolver).
- Does `tests/snapshot/test_first_run_modal.py` already exist and cover
  the `S3CompatFormModal` add case? If yes, only `edit` + `validation`
  snapshots are net-new.
- Confirm no existing keybinding maps to `,` in the default keymap
  (`infra/keymap_store.py` or equivalent).

## 12. Future sub-projects (not this spec)

| Sub-project | Description | Spec ETA |
|---|---|---|
| **B — Themes panel** | Refactor `ThemePickerModal` (preview-on-cursor + Esc-rollback from PR #47) into a `ThemesPanel` plugged into the SettingsModal sidebar. The standalone modal can stay as a fast-path (existing keyboard shortcut), but the settings flow becomes the canonical entry. | When A merges |
| **C — Keybindings panel** | New `KeybindingsPanel`. Precondition: finish `KeymapStore` / `BindingResolver` wiring listed in the M6-deferred set. Includes a "press a key" capture flow, conflict detection, reset-to-default, and persistence to a keymap section in `config.toml`. | After B; KeymapStore work first |

When A's settings shell is in place, B just registers a panel under the
`Themes` sidebar row and flips it from `(soon)` to active. C does the same
for `Keymap`. No structural changes to the shell expected.
