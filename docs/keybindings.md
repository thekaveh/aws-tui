# Keybindings

> Mirror of spec §4.2. Fully customizable via the `[keybindings]`
> section of `~/.config/aws-tui/config.toml` *once the input-router
> wiring lands* — see the v0.7.x status note at the end of §1 and
> the **Deferred / v0.8 roadmap** block in `CHANGELOG.md`.

The defaults are macOS-tailored — no F-keys, no `⌘`-modifier
(terminals intercept it). Letter-driven, with the command palette
(`:` or `Ctrl+K`) as the universal escape hatch.

> **v0.7.x wiring status:** rows below tagged `(deferred)` are
> declared in `KeymapStore.DEFAULT_BINDINGS` but the matching
> `action_*` handler has not yet been added to `AwsTuiApp`. They
> remain valid action IDs (your `[keybindings]` overlay can rebind
> them today; the binding takes effect once the deferred wiring
> ships). See the **Deferred / v0.8 roadmap** block in `CHANGELOG.md`
> for the full list.

## 1. Default bindings

### 1.1. Navigation

| Action | Default | Notes |
|---|---|---|
| Cursor up / down | `↑ ↓` or `j k` | vi-style alternatives are first-class |
| Descend into directory / bucket | `Enter` | |
| Ascend one level | `Backspace` or `←` | |
| Switch pane focus | `Tab` / `Shift+Tab` | |
| Page up / down | `PageUp` / `PageDown` | |
| Top / bottom | `g` / `G` | |
| Toggle hidden files (LocalFS) | `.` | |

### 1.2. Selection

| Action | Default | Notes |
|---|---|---|
| Enter multi-select mode | `pane.enter_multiselect` action — *(deferred)* | Spec'd on `v`; handler not wired in v0.7.x |
| Toggle row selection | `pane.toggle_select` action — *(deferred)* | Spec'd on `Space` (in multi-select); not wired |
| Extend selection one row | `Shift+↑` / `Shift+↓` | Marks the row the cursor is leaving + moves cursor |
| Modifier+click on row | `Shift+Click`, `Cmd+Click`, `Ctrl+Click` | Toggles mark on the clicked row; on macOS terminals reserve `Shift+Click`, so `Cmd+Click` is the reliable path there |
| Select all | `pane.select_all` action — *(deferred)* | Spec'd on `a` (in multi-select); not wired |
| Clear selection | `Esc` (in multi-select) | Modal-style cancel; clears mark set |

### 1.3. File operations

| Action | Default | Notes |
|---|---|---|
| Copy across panes | `c` | Streams through `CrossFsCopy`, shows confirm modal |
| Move across panes | `pane.move` action — *(deferred)* | `m` is currently bound to the nav-menu toggle (§1.5); the move handler is not yet wired in `AwsTuiApp` |
| Delete (with confirm) | `d` | Confirm modal; destructive ops always ask |
| New folder | `pane.new` action — *(deferred)* | No handler wired in v0.7.x |
| Rename in place | `pane.move` action — *(deferred)* | Bundled into the move handler; not wired |
| Refresh pane | `r` | |

### 1.4. Overlays

| Action | Default | Notes |
|---|---|---|
| Quick Look | `pane.quick_look` action — *(deferred)* | Spec'd on `Space`; preview handler not wired in v0.7.x |
| Fuzzy find | `pane.fuzzy_find` action — *(deferred)* | Spec'd on `Ctrl+P`; not wired |
| Filter pane | `pane.filter` action — *(deferred)* | Spec'd on `/`; not wired |
| Command palette | `app.command_palette` action — *(deferred)* | Spec'd on `:` / `Ctrl+K`; in v0.7.x `:` opens the help overlay (placeholder) and `Ctrl+K` is unbound |
| Theme picker (modal) | `t` | |
| Cycle to next theme (no modal) | `Shift+T` (`T`) | |
| Help overlay | `?` | |

### 1.5. Pane chrome

| Action | Default | Notes |
|---|---|---|
| Toggle nav menu (collapsed ↔ expanded) | `m` | The left rail shows services on top and Settings docked at the bottom. Also toggles via the hamburger glyph on the rail's top-left. |
| Cycle focused pane source | `Shift+S` (`S`) | Steps through `local` → each AWS profile (`aws s3 · {profile} · {region}`) → each `s3-compatible` connection (`s3-compatible · {name} · {endpoint}`) → wrap. The fastest way to jump between AWS accounts or s3-compatible endpoints — one keystroke per source, no command-palette modal. New connections added via the in-app Settings page (or `~/.config/aws-tui/config.toml`) join the cycle automatically. Either pane can be on any of the four `{S3-class, local}` combinations independently. |

### 1.6. Connection / auth

| Action | Default | Notes |
|---|---|---|
| Authenticate (when auth toast active) | `auth.authenticate` action — *(deferred)* | Spec'd on `a`; handler not wired in v0.7.x |
| Connection switcher | `app.command_palette` action — *(deferred)* | Spec'd as `:` then `connection switch <name>`; the palette open binding is deferred |

### 1.7. App

| Action | Default | Notes |
|---|---|---|
| Cancel / dismiss modal | `Esc` | Modal-owned; works on every modal that ships in v0.7.x |
| Quit | `q` or `Ctrl+C` | |

### 1.8. EMR Serverless

These are wired by `EmrServerlessPage` (added post-tag, PR #76; arrow-
key routing added by PR #78; layout overhaul by PR #80). The EMR page
is mounted in place of the S3 dual-pane when the ⚡️ EMR nav peer is
selected. Bindings are App-level `priority=True` and short-circuit
through `_emr_active_pane()` before the dual-pane guard fires.

| Action | Default | Notes |
|---|---|---|
| Open application picker | `a` | Opens the applications dropdown above the LEFT pane. The Commands chip on EMR re-labels `app.swap_source` ("switch app") and `Shift+S` is forwarded to the same handler so muscle memory from S3 still opens the picker. |
| State filter chips | `1` `2` `3` `4` `5` | Multi-select toggles for `RUNNING` / `SUCCESS` / `FAILED` / `CANCELLING` + `CANCELLED` / pending family (`PENDING` / `SUBMITTED` / `SCHEDULED` / `QUEUED`). |
| Cursor up / down | `↑` `↓` (also `j` / `k`) | Moves the LEFT-pane row cursor; master-detail follows the cursor (the RIGHT pane re-loads on every cursor move, not only on `Enter`). |
| Select run (explicit) | `Enter` | Re-emits `RunSelected` for the cursor row. |
| Refresh | `r` | Forces an immediate poll on the active pane (apps if LEFT focused on the picker, runs if LEFT focused on the runs list, detail if RIGHT focused). |
| Cycle pane focus | `Tab` / `Shift+Tab` | 2-slot cycle (LEFT ↔ RIGHT); narrower than the S3 3-slot cycle because the EMR page has no separate nav slot. |
| Swap source (re-purposed) | `Shift+S` (`S`) | On EMR this forwards to **open application picker** instead of the S3 connection cycle. |
| Backspace | `Backspace` | No-op on EMR (symmetric to `Descend` having an EMR branch). |

## 2. Customizing

A binding can be a single keystroke or a list of fallback keystrokes:

```toml
[keybindings]
"pane.copy" = "c"
"pane.delete" = "d"
"app.command_palette" = ["ctrl+k", ":"]
"app.help" = "?"
"app.themes" = "t"
"app.cycle_theme" = "T"
"app.swap_source" = "S"
```

The default map is declared in `infra/keymap_store.py` and merged with
your overlay; unknown action ids are rejected so a typo in your config
raises a startup error instead of silently dropping a binding.

**v0.7.x status**: the `KeymapStore` accepts the `[keybindings]`
overlay via its constructor and validates every action id, but the
composition root does not yet read the overlay from `config.toml` —
that wiring is part of the input-router work deferred from M6 (see
[cookbook.md §3](cookbook.md#3-customize-a-keybinding) and the
**Deferred / v0.8 roadmap** block in `CHANGELOG.md`). Today the same
effect is achievable by editing
`src/aws_tui/infra/keymap_store.py::DEFAULT_BINDINGS` directly in a
fork. Bind ahead of time in your config and the wiring will pick them
up once it lands.

The bindings that **are** wired today (in v0.7.x) and routed straight
through `AwsTuiApp.BINDINGS` rather than the keymap store: `q`,
`Ctrl+C`, `Tab` / `Shift+Tab`, `↑/↓` (and `j/k`), `Enter`,
`Backspace`, `←`, `→`, `r`, `?`, `:`, `t`, `T`, `c`, `d`, `m`, `S`
(Shift+S), `Shift+↑`, `Shift+↓`.

## 3. Action IDs

The `wired?` column marks whether `AwsTuiApp` currently has a matching
`action_*` handler. `(deferred)` rows are valid action IDs you can
overlay today; the binding takes effect once the input-router wiring
lands (see the §1 status note).

| Action ID | Default key | Wired? | What it does |
|---|---|---|---|
| `app.quit` | `q` / `ctrl+c` | ✓ | Graceful shutdown |
| `app.command_palette` | `:` / `ctrl+k` | *(deferred)* | Open palette (today `:` falls back to the help overlay) |
| `app.help` | `?` | ✓ | Help overlay |
| `app.themes` | `t` | ✓ | Open theme picker modal |
| `app.cycle_theme` | `T` (`shift+t`) | ✓ | Cycle to next theme without opening the modal |
| `app.swap_source` | `S` (`shift+s`) | ✓ | Cycle the focused pane: `local` → each AWS profile → each `s3-compatible` connection → wrap |
| `pane.move_up` / `pane.move_down` | `↑` / `↓` (also `j` / `k`) | ✓ | Move cursor |
| `pane.descend` | `enter` | ✓ | Descend into folder / bucket |
| `pane.ascend` | `backspace` / `←` | ✓ | Parent path |
| `pane.switch_focus` | `tab` | ✓ | Move focus to the other pane |
| `pane.switch_focus_back` | `shift+tab` | ✓ | Move focus to the previous pane |
| `pane.quick_look` | `space` (normal mode) | *(deferred)* | Stream first 64 KB |
| `pane.filter` | `/` | *(deferred)* | Local pane filter |
| `pane.fuzzy_find` | `ctrl+p` | *(deferred)* | Fuzzy find paths / buckets |
| `pane.enter_multiselect` | `v` | *(deferred)* | Enter multi-select mode |
| `pane.toggle_select` | `space` (multi-select) | *(deferred)* | Add / remove from selection |
| `pane.select_all` | `a` | *(deferred)* | Select all in pane |
| `pane.copy` | `c` | ✓ | Copy marked entries to other pane |
| `pane.move` | `m` | *(deferred)* — `m` is in use by the nav-menu toggle | Move marked entries (or rename one) |
| `pane.delete` | `d` | ✓ | Delete marked entries (confirms) |
| `pane.new` | `n` | *(deferred)* | New folder / bucket |
| `pane.refresh` | `r` | ✓ | Re-run `provider.list()` |
| `auth.authenticate` | `a` (when auth toast active) | *(deferred)* | Shell-out to `aws sso login` |
| `modal.cancel` | `escape` | ✓ | Cancel / close current overlay (modal-owned) |

These are the action IDs `KeymapStore.DEFAULT_BINDINGS` actually
registers. Overlay any of them in your `[keybindings]` table; any other
id raises `UnknownAction` at startup.

`Shift+↑` / `Shift+↓` (extend-selection) and `m` (nav menu
toggle) are wired directly in `AwsTuiApp.BINDINGS` rather than the
keymap store, because they're either modifier combinations or static
UI toggles. They are not currently rebindable through `[keybindings]`.

## 4. Modal forwarding for Enter / Esc / arrows

Textual dispatches App-level `priority=True` bindings *before* modal
screen bindings. Without that, pressing `Enter` inside the theme
picker or confirm modal would fire the dual-pane `descend` action and
never reach the modal's confirm handler.

`AwsTuiApp` works around this via `_forward_to_modal(*action_names)`:
when a modal is on top of the screen stack, `action_descend` /
`action_ascend` / `action_move_up` / `action_move_down` first look for
the corresponding handler on the active screen and forward there. The
result: `Enter` confirms in any modal, `Esc` (or `Backspace`) cancels,
and `↑/↓` navigate the picker even though the app reserves them for
the dual-pane cursor.

## 5. Layer separation

The View layer never invokes a VM command by attribute access; it
always goes through the action registry. That's how rebinding can be
purely config-driven — no Textual `BINDINGS` are hard-coded except
for `q`, `Ctrl+C` (so the app can always exit even if the keymap
is broken), and the modifier-combination actions noted in §3.
