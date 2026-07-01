# 1. Settings as First-Class Nav Page — Design Spec

**Date:** 2026-06-20
**Status:** Approved (rework of PR #52/#53's sub-project A)
**Branch:** `feat/settings-as-first-class-nav-page`
**Author:** Kaveh + Claude

---

## 1.1. Motivation

PR #52 shipped the App Settings overlay as a `ModalScreen` opened via a gear
button pinned to the bottom of the services rail. PR #53 fixed the gear
rendering blank. End-to-end the result felt wrong in three ways:

1. **The "gear + Settings" footer looked sloppy** — both glyph and label
   crammed into a 16-cell-wide column with the label spilling and getting
   truncated.
2. **The settings UI as a centered modal overlay** is the wrong pattern for
   a TUI whose primary visual language is a side-rail nav + main-area
   content. The user already navigates between sources via the rail;
   settings should be just another destination, not a layered interrupt.
3. **Add/Edit as a separate full-screen ModalScreen** doubled the modal
   layering and forced the user out of the panel they were looking at.

This spec replaces all three with a single coherent pattern: **Settings as
a first-class entry in the left rail, peer to S3.** The rail becomes a
general-purpose vertical nav; selecting an item swaps the main area's
content via the existing `ContentHostVM.set_content(...)` API. The settings
page itself follows the VS Code Settings UI shape — a scrollable page of
collapsible sections. Add/Edit for S3 connections expands inline within the
Connections section, below the row list.

This is **a rework, not an extension** of PR #52: the modal screen, the
gear footer band, and the form-as-screen pattern are deleted. The two VMs
(`SettingsVM`, `S3ConnectionsVM`), the `ConfigStore` extensions
(`update_connection`, `remove_connection`), and the
`ConnectionListChangedMessage` survive unchanged.

## 1.2. Scope

### 1.2.1. In scope

- **Left rail becomes a generic vertical nav** with peer items `S3` and
  `Settings`. Built on Textual's native `OptionList` widget.
  - Selection-highlight CSS matches the file-pane row cursor
    (`$bg-sel` background, `$accent` foreground).
  - Existing hamburger collapse/expand toggle (`m` binding) preserved.
    Collapsed mode shows icon glyphs only (`S3` → service icon; `Settings`
    → `⚙`); expanded mode shows full text labels.
  - Rail header renames `services` → `menu`.
- **Settings becomes a first-class main-area content**, peer to S3's
  `DualPaneVM`. Implemented via the existing `ContentHostVM.set_content(...)`
  API.
- **Settings page = VS Code-style scrollable page of `Collapsible` sections.**
  - First section populated by this sub-project: `S3-Compatible Connections`
    (expanded by default).
  - `Themes` and `Keymap` are visible-but-disabled `Collapsible` placeholders
    with `(coming in v0.8)` suffix and muted styling. Sub-projects B and C
    drop the disabled flag and fill the body.
- **Add/Edit S3 connection form is inline.** A `ConnectionFormInline` widget
  is mounted within the Connections section, below the row list. Hidden by
  default; visible when `+ Add` or `[✎ Edit]` is clicked. Only one form open
  at a time.
- **Save semantics are immediate.** Save persists + reloads any pane bound
  to the affected connection + collapses the form. Cancel collapses the
  form without persisting.
- **Delete still uses `ConfirmModal`** — destructive ops deserve modal
  interruption.
- **Keyboard:** `,` (comma) rebinds from "open SettingsModal" to "select
  Settings in the nav menu".
- **Snapshot tests gain content-presence guards** per the lesson from PR #53.

### 1.2.2. Removed (deleted as part of this rework)

- `ui/widgets/settings_modal.py` (`SettingsModal` `ModalScreen`)
- `ui/widgets/services_menu_footer.py` (`ServicesMenuFooter` gear band)
- `ui/widgets/first_run_modal.py::S3CompatFormModal` class
  - `FirstRunModal` itself stays — the first-run flow lifts the form
    widget out of `first_run_modal.py` and embeds it inline the same way
    the settings page does.
- Per-theme CSS blocks for the three above (`SettingsModal`,
  `ServicesMenuFooter`, `S3CompatFormModal`) across all 10 themes
- Snapshot test files + goldens:
  - `tests/snapshot/apps/settings.py` + `tests/snapshot/test_settings_modal.py`
    + 20 `__snapshots__/test_settings_modal/` goldens
  - `tests/snapshot/apps/services_menu_footer.py` + `tests/snapshot/test_services_menu_footer.py`
    + 10 goldens (including PR #53's content-presence guards — those guards
    survive the migration as a *pattern* in the new tests)
  - `tests/snapshot/apps/s3_compat_form.py` + `tests/snapshot/test_s3_compat_form.py`
    + 30 goldens
- Unit test files for the deleted widgets

### 1.2.3. Surviving from PR #52/#53 (kept as-is)

- `src/aws_tui/vm/settings/settings_vm.py` — `SettingsVM` (small change:
  drop `dirty_connection_names` + `clear_dirty()`)
- `src/aws_tui/vm/settings/s3_connections_vm.py` — unchanged
- `src/aws_tui/vm/messages.py::ConnectionListChangedMessage` — unchanged
- `src/aws_tui/infra/config_store.py::update_connection / remove_connection`
  — unchanged
- `src/aws_tui/ui/widgets/confirm_modal.py` — still used for delete
- The `S3ConnectionsPanel` widget — kept but heavily modified (drops
  modal-push CRUD, replaces with inline-form toggle)

### 1.2.4. Explicitly out of scope

- Themes panel implementation (sub-project B, separate spec)
- Keymap rebinding UI (sub-project C, separate spec; precondition: finish
  the `KeymapStore`/`BindingResolver` wiring noted in deferred-from-m6)
- AWS-kind connection editing (already out of scope per PR #52)
- Connectivity testing on Save (already out of scope per PR #52)
- Renaming connections (still requires delete + add)

## 1.3. Architecture

### 1.3.1. File map (new)

```
src/aws_tui/
  ui/widgets/
    nav_menu.py                          # NavMenu — OptionList-based rail
    settings_view.py                     # SettingsView — scrollable page
    settings/
      connection_form.py                 # ConnectionFormInline widget
```

### 1.3.2. File map (modified)

- `src/aws_tui/vm/services_menu_vm.py` — rename `ServicesMenuVM` →
  `NavMenuVM`. Items list extends to include a `Settings` entry (hard-coded
  alongside the service-derived items). `selected_id` semantics unchanged.
- `src/aws_tui/vm/settings/settings_vm.py` — drop dirty-set machinery;
  add `setup()`/`construct()`/`dispose()` to conform to the ContentHost VM
  lifecycle.
- `src/aws_tui/ui/widgets/settings/s3_connections_panel.py` — replaces the
  three `push_screen_wait(S3CompatFormModal(...))` worker handlers with
  inline-form-show/hide logic + `connection_form.py` mount. Delete still
  pushes `ConfirmModal`.
- `src/aws_tui/ui/widgets/first_run_modal.py` — `S3CompatFormModal` class
  deleted. The `FirstRunModal` still composes the form inline using the
  same `ConnectionFormInline` widget (single source of truth for the form).
- `src/aws_tui/app.py` — `action_open_settings` rebinds to "select Settings
  in the nav menu" (programmatic selection); the comma keybinding entry
  unchanged. Hub subscription to `ConnectionListChangedMessage` (for
  `unreachable_connections` cleanup) unchanged.
- `src/aws_tui/composition.py` — `build_app_context` constructs
  `SettingsVM` eagerly at startup (same pattern as the existing overlay
  VMs `transfers_vm`, `confirm_vm`, etc.), so the nav selection can swap
  to it synchronously without an await on construction.
- `src/aws_tui/ui/themes/*.tcss` (×10) — replace
  `SettingsModal`/`ServicesMenuFooter`/`S3CompatFormModal` blocks with
  `NavMenu`/`SettingsView`/`ConnectionFormInline` blocks.

### 1.3.3. Layout

The window's top-level layout is unchanged: rail (left) + main content
(right). The rail is now a `NavMenu` widget hosting an `OptionList`; the
main content is the existing `ContentHost` whose `current` swaps between
`DualPaneVM` (for S3) and `SettingsView` (for Settings).

```
┌─ aws-tui ──────────────────────────────────────────────────────────┐
│ menu     │  [main content swaps based on nav selection]            │
│ ▸ S3     │                                                          │
│ ▸ Settings                                                          │
│          │                                                          │
└──────────┴──────────────────────────────────────────────────────────┘
```

When `S3` is selected, the main area shows the `DualPaneView` (file
manager). When `Settings` is selected, the main area shows:

```
╭─ Settings ──────────────────────────────────────────────────────╮
│                                                                  │
│ ▾ S3-Compatible Connections                                      │
│   ┃ minio-local      http://localhost:9000   us-east-1  [✎][✕]  │
│   ┃ ceph-staging     https://ceph.internal   us-west-2  [✎][✕]  │
│                                                                  │
│   [ + Add s3-compatible connection ]                             │
│                                                                  │
│   (Form area below appears when Add or Edit is clicked)          │
│                                                                  │
│ ▸ Themes (coming in v0.8)                                        │
│ ▸ Keymap (coming in v0.8)                                        │
│                                                                  │
╰──────────────────────────────────────────────────────────────────╯
```

When `+ Add` or `[✎ Edit]` is clicked, the inline form expands below the
button:

```
│   [ + Add s3-compatible connection ]                             │
│                                                                  │
│   ┌─ New s3-compatible connection ───────────────── [✕ Close] ─┐ │
│   │  name             [minio-test            ]                │ │
│   │  endpoint URL     [http://localhost:9000 ]                │ │
│   │  region           [us-east-1             ]                │ │
│   │  access key ID    [AKIATEST              ]                │ │
│   │  secret key       [••••••••              ]                │ │
│   │  ☑ force path style                                       │ │
│   │  ☑ verify TLS                                             │ │
│   │              [ Cancel ]    [ Save ]                       │ │
│   └───────────────────────────────────────────────────────────┘ │
```

In edit mode the form title reads `Edit <name>` and the `name` field is
read-only (locked, same `name_locked=True` semantics as PR #52).

## 1.4. VM layer

### 1.4.1. `NavMenuVM` (renamed from `ServicesMenuVM`)

Public surface essentially unchanged from today:

```python
class NavMenuVM:
    items: tuple[NavItemVM, ...]
    selected_id: str | None
    switch_service_command: RelayCommandOf[str]

    def construct(self) -> None: ...
    def dispose(self) -> None: ...
    def update_connection(self, conn: Connection | None) -> None: ...
```

The `items` list is built as: every registered service item that supports
the current connection PLUS a hard-coded `Settings` item appended at the
end (always present, regardless of connection). `NavItemVM` carries the
service-or-Settings descriptor; the View doesn't need a kind discriminator
because both routes go through the same `ContentHost.set_content` call
upstream.

Selection from the View side is `switch_service_command.execute(item_id)`
(the canonical VMx command pattern, inherited from the legacy
`ServicesMenuVM`). The command sets `selected_id` and publishes a
`PropertyChangedMessage("selected_id")`, which the app-level subscriber
turns into a `ContentHost.set_content(...)` call.

### 1.4.2. `SettingsVM` (kept; simplified)

```python
class SettingsVM:
    s3: S3ConnectionsVM    # composed child

    def construct(self) -> None: ...
    def destruct(self) -> None: ...
    def dispose(self) -> None: ...
    async def setup(self) -> None: ...  # NEW — ContentHost calls this
```

The dirty-set/clear_dirty machinery from PR #52 is **deleted**. There is no
modal lifecycle to track anymore — reload-on-Save happens immediately,
synchronously from the form's Save handler.

### 1.4.3. `S3ConnectionsVM` (unchanged)

Public surface, validation, message publishing, and `entry_from_form`
helper all stay identical to PR #52.

### 1.4.4. ContentHost integration

`ContentHostVM.set_content(vm, *, service_id)` already takes any VM with
`construct/dispose` lifecycle. To plug `SettingsVM` in:

```python
# in AwsTuiApp or its hub subscriber for NavMenuVM selection:
async def _on_nav_selection_changed(self) -> None:
    selected = self._app_ctx.nav_menu_vm.selected_id
    if selected == "settings":
        await self._app_ctx.content_host_vm.set_content(
            self._app_ctx.settings_vm, service_id="settings"
        )
    elif selected is not None:
        # Existing service-resolution path (today's switch_service flow).
        ...
```

`SettingsVM` is constructed eagerly at startup (in `build_app_context`,
just like the other overlay VMs already are) so the nav selection can
swap to it without an await on construction. Disposal follows the existing
ContentHost teardown protocol (`set_content(None)` → dispose).

## 1.5. View layer

### 1.5.1. `NavMenu` widget

```python
class NavMenu(Widget):
    """OptionList-based vertical nav with collapse-to-icons mode."""

    DEFAULT_CSS = """
    NavMenu { width: 16; }
    NavMenu.-collapsed { width: 3; }
    """

    def __init__(self, vm: NavMenuVM, *, hub: MessageHub) -> None: ...

    def compose(self) -> ComposeResult:
        yield Static("menu", id="menu-header")
        yield OptionList(id="menu-options")

    def on_option_list_option_selected(self, event) -> None:
        self._vm.select(event.option_id)

    def _refresh_options(self) -> None:
        """Rebuild the OptionList's options list when items change or
        when the collapsed flag flips (prompts change between full label
        and icon-only)."""
        ...
```

The OptionList's options are constructed as `Option(prompt, id)` where
`prompt` is `"⚙ Settings"` when expanded and `"⚙"` when collapsed. The
selection-highlight CSS targets
`NavMenu > OptionList > .option-list--option-highlighted` and uses
`$bg-sel` background + `$accent` foreground (matching the file-pane
`.entry-row.-selected` style).

### 1.5.2. `SettingsView` widget

```python
class SettingsView(Widget):
    """The main-area widget for the Settings nav destination."""

    DEFAULT_CSS = """
    SettingsView { layout: vertical; }
    SettingsView > VerticalScroll { padding: 1 2; }
    """

    def __init__(self, vm: SettingsVM, *, hub: MessageHub) -> None: ...

    def compose(self) -> ComposeResult:
        yield Static("Settings", id="settings-title")
        with VerticalScroll(id="settings-scroll"):
            with Collapsible(title="S3-Compatible Connections", collapsed=False, id="section-connections"):
                yield S3ConnectionsPanel(vm=self._vm.s3, hub=self._hub)
            with Collapsible(title="Themes (coming in v0.8)", disabled=True, id="section-themes"):
                yield Static("This section is coming in v0.8.")
            with Collapsible(title="Keymap (coming in v0.8)", disabled=True, id="section-keymap"):
                yield Static("This section is coming in v0.8.")
```

`Collapsible` is the Textual native widget. `disabled=True` makes the
header non-interactive; per-theme CSS styles it with muted color.

### 1.5.3. `S3ConnectionsPanel` (modified)

Three changes from PR #52:

1. **Drop the modal-push CRUD handlers.** The `_do_add`/`_do_edit` `@work`
   functions that pushed `S3CompatFormModal` are replaced by handlers that
   toggle the inline form's visibility:
   ```python
   def _on_add(self, event) -> None:
       form = self.query_one("#inline-form", ConnectionFormInline)
       form.open_for_add()
   def _on_edit(self, event) -> None:
       name = event.button.id.removeprefix("edit-")
       defaults = self._defaults_for(name)
       form = self.query_one("#inline-form", ConnectionFormInline)
       form.open_for_edit(name=name, defaults=defaults)
   ```
2. **Mount the inline form below the rows.** `compose()` yields the form
   widget with `display: none` initially; `open_for_*` flips `display` to
   `block` and populates fields.
3. **Delete still pushes ConfirmModal** — `@work` handler unchanged.

### 1.5.4. `ConnectionFormInline` widget (new)

Lifted from `S3CompatFormModal`'s compose body. Same fields, same live
validation (`_validate_s3_form_value` reused). Adds:
- `open_for_add()` — clears fields, name unlocked, title reads "New
  s3-compatible connection".
- `open_for_edit(name, defaults)` — pre-fills fields, name locked, title
  reads "Edit <name>".
- Emits a `ConnectionFormSubmitted(form: S3CompatForm, mode: Literal["add", "edit"], original_name: str | None)` message on Save; emits
  `ConnectionFormCancelled` on Cancel. `S3ConnectionsPanel` subscribes and
  routes to `vm.add(...)` / `vm.update(...)`.

### 1.5.5. What's deleted

- `ui/widgets/settings_modal.py`
- `ui/widgets/services_menu_footer.py`
- `S3CompatFormModal` class from `ui/widgets/first_run_modal.py`
- `ui/widgets/settings/_placeholder_panel.py` (no longer needed — the
  disabled Collapsibles are the placeholder pattern)

## 1.6. Save semantics (replaces PR #52's reload-on-close)

When the user clicks Save on the inline form:
1. `ConnectionFormInline` validates one last time + emits `ConnectionFormSubmitted`.
2. `S3ConnectionsPanel` handler calls `vm.add(entry)` or `vm.update(name, entry)`.
3. The VM persists via `ConfigStore` and publishes `ConnectionListChangedMessage`.
4. Subscribers react: `AwsTuiApp` drops deleted names from
   `unreachable_connections` (delete path); pane-reload logic in the app
   rebinds any S3-compatible pane whose `(kind, name)` key matches the
   affected name. Same `_rebind_pane_to_local` /
   `_rebind_pane_to_connection` helpers from PR #52 are reused (they're
   independent of the modal flow). Post-ship hardening: AWS profiles and
   S3-compatible connections may share display names, so reload matching
   must keep the `kind == "s3-compatible"` guard rather than matching on
   name alone.
5. `S3ConnectionsPanel.refresh_rows()` rebuilds the row list.
6. The form collapses (`display: none`).
7. No success toast — the form-close + new-row visibility are
   sufficient user feedback. Error toasts surface only on failure
   (duplicate name, persistence error). This is a deliberate
   low-noise UX choice; future iterations may add success toasts if
   the row-update isn't enough for user confidence.

Delete path: confirm via ConfirmModal → call `vm.remove(name)` →
publish `ConnectionListChangedMessage("deleted")` → pane-reload via
the hub subscriber (revert to local). No success toast on the
delete path either — the row disappears, that's confirmation.

## 1.7. Keyboard

- `,` (comma) — selects the Settings nav item (equivalent to clicking it).
  Implementation: `action_open_settings` calls
  `ctx.root_vm.services_menu.switch_service_command.execute("settings")`
  (legacy property name; the underlying type is `NavMenuVM`).
- `m` — toggles the rail collapsed/expanded state (unchanged).
- Inside `SettingsView`:
  - `Tab` / `Shift+Tab` — Textual's default focus traversal between the
    section headers, list items, form inputs, and the Save/Cancel chips.
  - `Enter` on a Collapsible header — expand/collapse the section.
  - `Esc` — when an inline form is open, equivalent to Cancel.

## 1.8. Error handling

| Failure | Surface | Behavior |
|---|---|---|
| TOML write fails | `ConfigStore.save()` | Raises `OSError`; surfaces as toast `"Couldn't save: <reason>"`. Form stays open with values intact. |
| Form validation fails | `ConnectionFormInline` | Save button stays disabled, red border on invalid fields. |
| Duplicate name on add | `S3ConnectionsVM.add` raises `ValueError` | Toast `"Name already exists: <name>"`. Form stays open. |
| Pane reload fails (new endpoint unreachable) | PR #49's existing detection | Pane shows offline-state; no special handling here. |
| Delete of active connection | Same as PR #52 | Pane reverts to local, toast fires. |
| ContentHost.set_content fails for SettingsVM | Should not occur in normal use | Existing ContentHost error path (rendering placeholder) handles it. |

## 1.9. Testing

### 1.9.1. Removed tests (no longer apply)

- `tests/unit/ui/test_settings_modal.py`
- `tests/unit/ui/test_services_menu_footer.py` (including PR #53's content-presence guards)
- `tests/unit/ui/test_s3_compat_form_modal.py`
- `tests/snapshot/test_settings_modal.py` + 20 goldens
- `tests/snapshot/test_services_menu_footer.py` + 10 goldens
- `tests/snapshot/test_s3_compat_form.py` + 30 goldens
- `tests/integration/test_settings_modal_flow.py`
- `tests/snapshot/apps/settings.py`
- `tests/snapshot/apps/services_menu_footer.py`
- `tests/snapshot/apps/s3_compat_form.py`

### 1.9.2. Kept tests (still apply unchanged)

- `tests/unit/infra/test_config_store.py` — `update_connection` +
  `remove_connection` round-trip tests
- `tests/unit/vm/settings/test_s3_connections_vm.py` — CRUD + message publishing
- `tests/unit/vm/test_messages.py` — `ConnectionListChangedMessage` shape

### 1.9.3. Modified tests

- `tests/unit/vm/settings/test_settings_vm.py` — drop the dirty-set tests
  (4 tests removed), keep the construct/dispose + s3 accessor tests.
- `tests/unit/vm/test_services_menu.py` — rename to `test_nav_menu.py`,
  extend coverage to include the hard-coded `Settings` item.

### 1.9.4. Added tests

- `tests/unit/ui/test_nav_menu.py` — construction smoke + OptionSelected
  event handling + collapsed-mode prompt rebuild.
- `tests/unit/ui/test_settings_view.py` — construction smoke + Collapsible
  default state.
- `tests/unit/ui/test_connection_form_inline.py` — construction smoke +
  open_for_add/edit behavior + emitted messages.
- `tests/snapshot/apps/nav_menu.py` — test app for NavMenu (expanded +
  collapsed states).
- `tests/snapshot/test_nav_menu.py` — parametrized snapshot tests ×10
  themes × 2 states (expanded/collapsed) = 20 goldens. **Paired with
  content-presence guards** asserting `"S3"` and `"Settings"` (or `"⚙"`
  in collapsed mode) appear in the rendered SVG.
- `tests/snapshot/apps/settings_view.py` — test app for SettingsView with
  three scenarios: empty Connections section, populated (2 rows), and
  form-open (Add mode).
- `tests/snapshot/test_settings_view.py` — parametrized ×10 themes × 3
  scenarios = 30 goldens. **Paired with content-presence guards:**
  - Empty scenario: assert `"No S3-compatible connections"` in the SVG
  - Populated: assert `"minio-local"` and the chip glyphs (`"✎"`, `"✕"`)
  - Form-open: assert `"endpoint URL"` label + `"Save"` button text
- `tests/integration/test_settings_flow.py` — replaces the old
  `test_settings_modal_flow.py`. Three end-to-end flows:
  1. Open app → press `,` → assert `SettingsView` is the main-area content
     → click Add → form expands inline → fill + Save → assert row appears
     and TOML round-trips.
  2. Seed a connection bound to the left pane → select Settings → edit
     endpoint → Save → assert pane reload happened (toast + pane
     `current_connection_key == ("s3-compatible", name)` still bound to the
     same S3-compatible connection name).
  3. Same seed → Delete → confirm modal → confirm → assert TOML removal
     and pane revert to local.

### 1.9.5. Snapshot count delta

| | Before | After |
|---|---|---|
| Total snapshots (whole project) | 194 | ~170 |
| Settings-related | 60 (overlay+form+footer × 10) | ~50 (nav×10×2 + settings-view×10×3) |

Net **decrease** of ~24 snapshots — fewer surfaces, fewer goldens, but
every new snapshot is paired with a content-presence guard so empty
renderings can't pass.

## 1.10. Global constraints

These apply to every task in the implementation plan:

- **10-theme parity** for all new CSS (`NavMenu`, `SettingsView`,
  `Collapsible` override, `ConnectionFormInline`).
- **Content-presence guards** mandatory on every new snapshot test (per
  PR #53's lesson: pytest-textual-snapshot parity-match alone can pass a
  uniformly-blank rendering across all themes, since identical-to-self
  matches succeed. Pair every new snapshot with a guard test that reads
  the generated SVG and asserts a user-visible glyph/label is actually
  present in the rendered text — see `tests/snapshot/test_nav_menu.py`
  and `tests/snapshot/test_settings_view.py` for the pattern).
- **No new third-party dependencies.** Everything uses existing Textual
  widgets (`OptionList`, `Collapsible`, `VerticalScroll`, `Input`,
  `Button`, `Container`).
- **Layered architecture preserved.** Enforced by `scripts/check-layers.sh`.
- **All quality gates green per commit:** ruff, ruff format, mypy --strict
  src, check-layers, full pytest.
- **Match the file-pane row-selection styling** for the nav-menu cursor
  highlight: `$bg-sel` background, `$accent` foreground, no border.
- **One inline form open at a time** within the Connections section. Save
  + Cancel both collapse it.
- **Esc inside the form = Cancel.**
- **Delete still uses ConfirmModal** — destructive ops keep the modal
  interruption.

## 1.11. Open implementation questions

These are *implementation-level* questions to resolve while building, not
design decisions to revisit:

- Does `OptionList.Option.id` persist when `clear_options()` + `add_options()`
  is called to rebuild for collapse-mode switching? (Almost certainly yes;
  verify before relying on it.)
- Confirm Textual's `Collapsible` widget accepts `disabled=True` on
  construction (the explore agent indicated it likely does, but verify).
- Find every callsite of `S3CompatFormModal` and `SettingsModal` —
  enumerate so the deletion doesn't leave orphan imports.
- Verify the `Collapsible` widget's expanded/collapsed reactive triggers a
  layout recompute that the parent `VerticalScroll` honors.

## 1.12. Migration path

Single PR, single squash-merge. The branch `feat/settings-as-first-class-nav-page`
starts from `e0edfe1` (current main), deletes the PR #52/#53 modal surfaces
in early commits, builds the new pattern in later commits, and the final
commit updates the CHANGELOG `[Unreleased]` block to describe the new
pattern (replacing the PR #52 bullet, NOT augmenting it).

This is destructive of PR #52's surface — by design. The architecture
shipped in #52 was wrong for the TUI's idiom; this rework gets it right.

## 1.13. Post-ship amendments (PR #55 + PR #56)

Recorded after the original PR #54 landed at `a7bd050` and the
follow-up fixes shipped on `main`. Both items below override the
corresponding sub-sections above and are the current authoritative
behavior.

### 1.13.1. SettingsVM is per-mount, not a singleton (PR #56)

§5.1 originally proposed an `AppContext.settings_vm` field
constructed once in `composition.build_app_context`. That was wrong:
`ContentHostVM.set_content` calls `vm.dispose()` on the outgoing VM
and `vm.construct()` on the incoming one, so after the first
`Settings → S3` swap the singleton was in `Disposed` state and the
second `Settings` click raised
`WorkerFailed: StatusTransitionError('Cannot construct from state Disposed.')`.

Current behavior:

- `AppContext.settings_vm` field is **removed**.
- `AwsTuiApp._mount_settings_view` builds a fresh `SettingsVM` per
  mount: `SettingsVM(s3=ctx.s3_connections_vm, hub=ctx.hub, dispatcher=ctx.dispatcher)`.
- `S3ConnectionsVM` stays a singleton on `AppContext` — `SettingsVM.dispose()`
  intentionally does NOT cascade to its `_s3` child (see
  `vm/settings/settings_vm.py:68-69`), so the shared connection
  list/selection state survives across `SettingsVM` rebuilds. Only
  the thin `ComponentVM` wrapper is recreated each mount.
- Regression test: `tests/integration/test_settings_flow.py::test_toggle_settings_s3_settings_does_not_crash`.

This is the standard factory pattern `S3Service.build_vm` already
uses for the per-mount `DualPaneVM`. Any future ContentHost
destination (Themes panel, Keymap panel) must follow the same
factory pattern — never store the hosted VM on `AppContext`.

### 1.13.2. Settings is docked to the bottom of the rail (PR #56)

§3.3 and §5.1 originally proposed a single `OptionList` for the rail
items (services + Settings, with Settings last). User feedback after
PR #54 shipped: that put Settings directly under `S3` with empty
rows below, when Settings was supposed to read as a separate pinned
item at the bottom of the rail (macOS Settings.app / VS Code
activity-bar idiom).

Current `NavMenu` widget compose:

```python
def compose(self) -> ComposeResult:
    yield Static("menu", id="menu-header")
    yield OptionList(id="menu-services")  # services, height: 1fr (top)
    yield OptionList(id="menu-pinned")    # Settings, dock: bottom
```

- `#menu-services` (top): every service item the active connection
  supports, `height: 1fr`.
- `#menu-pinned` (bottom): the synthetic Settings item, `dock: bottom`,
  `height: auto`.
- Per-theme `border-top: solid $rule-dim` on `#menu-pinned` for a
  subtle separator (defined in each `*.tcss`, not in `DEFAULT_CSS`,
  because `$rule-dim` is an aws-tui theme variable not a Textual
  core variable).
- `NavMenuVM` is unchanged — it still owns ONE ordered items list
  with Settings as the last entry. The split is purely a View
  concern; the widget filters items by id (`item.descriptor.id == "settings"`)
  when populating each list.
- Snapshot scaffolding in `tests/snapshot/apps/nav_menu.py` registers
  a fake S3 service AND seeds an active connection so both lists are
  populated; otherwise the rail rendered only Settings and the
  docking layout was indistinguishable from a single-item list.
- Content-presence guard `test_nav_menu_expanded_renders_visible_settings_label`
  asserts `svg.index("S3") < svg.index("Settings")` to catch a
  regression where the `dock: bottom` rule gets dropped.

### 1.13.3. Nav-mount workers are serialized via an exclusive worker group (PR #56 follow-up)

Not originally specified. CI on Windows py3.11 exposed a race that
back-to-back `Settings → S3 → Settings` clicks could trigger:
`_mount_service_view` and `_mount_settings_view` both `await`
`ContentHostVM.set_content`, and the service worker could resume
after the settings worker had replaced `ContentHost.current` with a
`SettingsVM`, then wrap it in `DualPane(self._vm.left, ...)` →
`AttributeError: 'SettingsVM' object has no attribute 'left'`.

Fix: both `run_worker` calls are now scoped to a shared
`group="content-mount"` with `exclusive=True` so Textual cancels any
in-flight worker in the group before starting the new one. Any
future ContentHost mount path must follow the same group.

### 1.13.4. SSO probe + auth-required placeholder at startup (PR #55)

Not originally specified. PR #55 added an offline SSO-token freshness
probe BEFORE `switch_service("s3")` runs at startup, so an expired
AWS SSO token does not hang the launch on a boto3 token-refresh
network call. The probe runs in `on_mount` via
`AwsSession.probe_token(connection)`; on `TokenState.EXPIRED` for an
`aws`-kind connection, `_mount_auth_required_placeholder` is called
instead of `switch_service`. `TokenState.MISSING` is NOT gated
because it conflates "SSO configured but no cache" with "no SSO at
all (static creds)" — the latter is legitimate and must proceed.
