# 1. Keybindings

> Mirror of spec §4.2. `[keybindings]` entries in
> `<config-dir>/config.toml` parse and validate today, but runtime
> dispatch still uses `AwsTuiApp.BINDINGS` until the input-router
> wiring lands. See the v0.8.x status note at the end of §1 and the
> **Deferred / v0.9 roadmap** block in the `[0.8.0]` section of
> `CHANGELOG.md`.

The defaults are macOS-tailored — no F-keys, no `⌘`-modifier
(terminals intercept it). Letter-driven, with the command palette
(`:` or `Ctrl+K`) as the universal escape hatch.

> **v0.8.x wiring status:** rows below tagged `(deferred)` are
> declared in `KeymapStore.DEFAULT_BINDINGS` but the matching
> `action_*` handler has not yet been added to `AwsTuiApp`. They
> remain valid action IDs (your `[keybindings]` overlay can rebind
> them ahead of time; the binding takes effect once the deferred wiring
> ships). See the **Deferred / v0.9 roadmap** block in the `[0.8.0]`
> section of `CHANGELOG.md` for the full list.

## 1.1. Default bindings

### 1.1.1. Navigation

| Action | Default | Notes |
|---|---|---|
| Cursor up / down | `↑ ↓` or `k j` | vi-style alternatives are first-class |
| Descend into directory / bucket | `Enter` | |
| Ascend one level | `Backspace` or `←` | |
| Switch pane focus | `Tab` / `Shift+Tab` | |
| Top / bottom | `g` / `G` | |
| Toggle hidden files (LocalFS) | `.` | |

### 1.1.2. Selection

| Action | Default | Notes |
|---|---|---|
| Enter multi-select mode | `pane.enter_multiselect` action — *(deferred)* | Spec'd on `v`; handler not wired in v0.8.x |
| Toggle row selection | `pane.toggle_select` action — *(deferred)* | Spec'd on `Space` (in multi-select); not wired |
| Extend selection one row | `Shift+↑` / `Shift+↓` | Marks the row the cursor is leaving + moves cursor |
| Modifier+click on row | `Shift+Click`, `Cmd+Click`, `Ctrl+Click` | Toggles mark on the clicked row; on macOS terminals reserve `Shift+Click`, so `Cmd+Click` is the reliable path there |
| Select all | `pane.select_all` action — *(deferred)* | Spec'd on `a` (in multi-select); not wired |
| Clear selection | `Esc` (in multi-select) | Modal-style cancel; clears mark set |

### 1.1.3. File operations

| Action | Default | Notes |
|---|---|---|
| Copy across panes | `c` | Streams through `CrossFsCopy`, shows confirm modal |
| Move across panes | `pane.move` action — *(deferred)* | The move handler is not yet wired in `AwsTuiApp` — `m` is no longer reserved for the nav-menu toggle (the rail is always visible post-PR-#94 — see §1.5) so `m` is available for the move action when the deferred wiring lands |
| Delete (with confirm) | `d` | Confirm modal; destructive ops always ask |
| New folder | `pane.new` action — *(deferred)* | No handler wired in v0.8.x |
| Rename in place | `pane.move` action — *(deferred)* | Bundled into the move handler; not wired |
| Refresh pane | `r` | |

### 1.1.4. Overlays

| Action | Default | Notes |
|---|---|---|
| Quick Look | `pane.quick_look` action — *(deferred)* | Spec'd on `Space`; preview handler not wired in v0.8.x |
| Fuzzy find | `pane.fuzzy_find` action — *(deferred)* | Spec'd on `Ctrl+P`; not wired |
| Filter pane | `pane.filter` action — *(deferred)* | Spec'd on `/`; not wired |
| Command palette | `app.command_palette` action — *(deferred)* | Spec'd on `:` / `Ctrl+K`; in v0.8.x `:` opens the help overlay (placeholder) and `Ctrl+K` is unbound |
| Theme picker (modal) | `t` | |
| Cycle to next theme (no modal) | `Shift+T` (`T`) | |
| Help overlay | `?` | |

### 1.1.5. Pane chrome

| Action | Default | Notes |
|---|---|---|
| Open Settings | `,` (comma) | Opens the in-app Settings nav page directly. Equivalent to arrow-keying down to the ⚙ Settings row in the rail and pressing `Enter`. |
| Cycle focused pane source | `Shift+S` (`S`) | Steps through `local` → each AWS profile (`aws s3 · {profile} · {region}`) → each `s3-compatible` connection (`s3-compatible · {name} · {endpoint}`) → wrap. The fastest way to jump between AWS accounts or s3-compatible endpoints — one keystroke per source, no command-palette modal. New connections added via the in-app Settings page (or `<config-dir>/config.toml`) join the cycle automatically. Either pane can be on any of the four `{S3-class, local}` combinations independently. |

> **Nav-menu visibility:** the left rail is always visible at a single
> fixed width and shows TEXT labels (Settings docked at the bottom as
> the ⚙ glyph). The pre-PR-#94 `m`-key collapse/expand toggle was
> dropped because there is no longer a collapsed mode to toggle into;
> live `AwsTuiApp.BINDINGS` does not bind `m` in v0.8.x, while the
> keymap default reserves `m` for the deferred `pane.move` action
> (§1.3) when its router wiring lands.

### 1.1.6. Connection / auth

| Action | Default | Notes |
|---|---|---|
| Authenticate (when auth toast active) | `auth.authenticate` action — *(deferred)* | Spec'd on `a`; handler not wired in v0.8.x |
| Connection switcher | `app.command_palette` action — *(deferred)* | Spec'd as `:` then `connection switch <name>`; the palette open binding is deferred |

### 1.1.7. App

| Action | Default | Notes |
|---|---|---|
| Cancel / dismiss modal | `Esc` | Modal-owned; works on every modal that ships in v0.8.x |
| Quit | `q` or `Ctrl+C` | |

### 1.1.8. EMR Serverless

These are wired by `EmrServerlessPage` (added post-tag, PR #76; arrow-
key routing added by PR #78; layout overhaul by PR #80; clone-job-run
modal added by PR #83). The EMR page is mounted in place of the S3
dual-pane when the **EMR** nav row is selected. Bindings are
App-level `priority=True` and short-circuit through
`_emr_active_pane()` before the dual-pane guard fires.

| Action | Default | Notes |
|---|---|---|
| Open application picker | `a` | Opens the applications dropdown above the LEFT pane. |
| Cycle next application | `Shift+S` | Cycles to the next EMR application without opening the picker. On S3 the same app-level action cycles pane sources; on EMR `AwsTuiApp.action_swap_source` short-circuits to the page's next-application behavior and the Commands chip labels it as "switch app". |
| State filter chips | `1` `2` `3` `4` `5` | Multi-select toggles, one chip per state in this key order: `SUCCESS` / `RUNNING` / `PENDING` / `FAILED` / `CANCELLED`. Source of truth: ``_KEY_TO_STATE`` in ``ui/widgets/emr_serverless/job_runs_pane.py``. The transient pre-terminal states `SUBMITTED` / `SCHEDULED` / `QUEUED` / `CANCELLING` are NOT chip-filterable — they always render (they're members of the initial all-on default filter set and have no toggle key). |
| Cursor up / down | `↑` `↓` (also `k` / `j`) | Moves the LEFT-pane row cursor; master-detail follows the cursor (the RIGHT pane re-loads on every cursor move, not only on `Enter`). |
| Select run (explicit) | `Enter` | Re-emits `RunSelected` for the cursor row. |
| Refresh | `r` | Forces an immediate poll on the active pane (apps if LEFT focused on the picker, runs if LEFT focused on the runs list, detail if RIGHT focused). |
| Clone selected job run | `c` | Opens the `JobRunCloneModal` pre-filled from the focused run (name, entry point, IAM, args, spark params). Save fires `EmrServerlessClient.start_job_run`; success / error route through the unified `notifications.success` / `notifications.error` helpers (`Subject = "Job"`). `AwsTuiApp.action_copy` priority binding hijacks `c` to the EMR clone path when EMR is mounted — parallel to the dual-pane priority short-circuits for Tab / arrows. Added in PR #83. |
| Cycle pane focus | `Tab` / `Shift+Tab` | 4-slot cycle: nav rail → runs pane → detail pane → logs pane → nav rail. |
| Backspace | `Backspace` | No-op on EMR (symmetric to `Descend` having an EMR branch). |
| Load logs (on-demand) | `Enter` | Loads logs from S3 into the RIGHT-logs pane (first press in the logs slot after Tab-focusing). |
| Reload logs | `r` | Re-fetches logs from S3 even on cache hit. |
| Open log filter modal | `f` | Edit regex patterns, toggle "Show all" or "Match case"; ``Apply`` re-fetches. |
| Reset log filter | `Shift+F` | Clears the logs filter and returns to the default log view. |
| Scroll log lines up / down | `↑` `↓` (also `k` / `j`) | Navigate the loaded log line view (when RIGHT-logs pane is focused). |

> **Logs pane design note:** the detail pane no longer has its own `r`
> refresh path; the 5-second detail poller keeps it fresh
> automatically. `r` now reloads the logs pane when focused there.

## 1.2. Customizing

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

The default map is declared in `infra/keymap_store.py`. At composition
time, aws-tui validates your overlay by constructing a temporary
`KeymapStore(overlay=...)`; the runtime-visible keymap then stays on
defaults until the input-router handoff lands. Unknown action ids are
logged and the app continues with the default keymap so a typo does not
crash startup.

**v0.8.x status**: the `[keybindings]` table is parsed and validated
through `KeymapStore`, so unknown action IDs are caught and the
Commands strip and future router share the action-id vocabulary.
Runtime dispatch and visible command chips still go through the
hard-coded v0.8.x defaults; user overrides do not change which
keystrokes fire actions until the post-v0.8 input-router handoff
tracked in `CHANGELOG.md`.

The bindings that **are** wired today (in v0.8.x) and routed straight
through `AwsTuiApp.BINDINGS` rather than the keymap store: `q`,
`Ctrl+C`, `Tab` / `Shift+Tab`, `↑/↓` (and `j/k`), `Enter`,
`Backspace`, `←`, `→`, `r`, `?`, `:`, `t`, `T`, `,` (comma → Settings),
`c`, `d`, `S` (Shift+S), `Shift+↑`, `Shift+↓`.

## 1.3. Action IDs

The `wired?` column marks whether `AwsTuiApp` currently has a matching
`action_*` handler. `(deferred)` rows are valid action IDs you can
overlay ahead of time; the binding takes effect once the input-router wiring
lands (see the §1 status note).

| Action ID | Default key | Wired? | What it does |
|---|---|---|---|
| `app.quit` | `q` / `ctrl+c` | ✓ | Graceful shutdown |
| `app.command_palette` | `:` / `ctrl+k` | *(deferred)* | Open palette (today `:` falls back to the help overlay) |
| `app.help` | `?` | ✓ | Help overlay |
| `app.themes` | `t` | ✓ | Open theme picker modal |
| `app.cycle_theme` | `T` (`shift+t`) | ✓ | Cycle to next theme without opening the modal |
| `app.swap_source` | `S` (`shift+s`) | ✓ | Cycle the focused pane: `local` → each AWS profile → each `s3-compatible` connection → wrap |
| `pane.move_up` / `pane.move_down` | `↑` / `↓` (also `k` / `j`) | ✓ | Move cursor |
| `pane.descend` | `enter` | ✓ | Descend into folder / bucket |
| `pane.ascend` | `backspace` / `←` | ✓ | Parent path |
| `pane.switch_focus` | `tab` | ✓ | Cycle the active page's focus ring |
| `pane.switch_focus_back` | `shift+tab` | ✓ | Cycle the active page's focus ring in reverse |
| `pane.quick_look` | `space` (normal mode) | *(deferred)* | Stream first 64 KB |
| `pane.filter` | `/` | *(deferred)* | Local pane filter |
| `pane.fuzzy_find` | `ctrl+p` | *(deferred)* | Fuzzy find paths / buckets |
| `pane.enter_multiselect` | `v` | *(deferred)* | Enter multi-select mode |
| `pane.toggle_select` | `space` (multi-select) | *(deferred)* | Add / remove from selection |
| `pane.select_all` | `a` | *(deferred)* | Select all in pane |
| `pane.copy` | `c` | ✓ | Copy marked entries to other pane |
| `pane.move` | `m` | *(deferred)* | Move marked entries (or rename one) — `m` is no longer reserved for the nav-menu toggle (dropped in PR #94), so the default is available when the wiring lands |
| `pane.delete` | `d` | ✓ | Delete marked entries (confirms) |
| `pane.new` | `n` | *(deferred)* | New folder / bucket |
| `pane.refresh` | `r` | ✓ | Re-run `provider.list()` |
| `auth.authenticate` | `a` (when auth toast active) | *(deferred)* | Shell-out to `aws sso login` |
| `emr.clone` | `c` (when EMR page mounted) | ✓ | Open the EMR clone-job-run modal pre-filled from the focused run (PR #83) |
| `emr.logs.filter` | `f` (when EMR logs pane focused) | widget-scoped | Open the EMR logs filter modal |
| `modal.cancel` | `escape` | ✓ | Cancel / close current overlay (modal-owned) |

These are the action IDs `KeymapStore.DEFAULT_BINDINGS` actually
registers. Overlay any of them in your `[keybindings]` table; any other
id is logged and causes the app to fall back to the default keymap.

`Shift+↑` / `Shift+↓` (extend-selection) are wired directly in
`AwsTuiApp.BINDINGS` rather than the keymap store, because they're
modifier combinations. They are not currently rebindable through
`[keybindings]`.

> **Commands strip layout (PR #83)** — the bottom legend is now ONE
> concatenated row (single `#hint-strip` container), service-specific
> chips first, globals after. The L/R dock split that PR #81
> introduced (with `_hint-strip-service` / `_hint-strip-global` ids)
> was reverted per user feedback "I want their concatenation
> displayed at the bottom". Chips disable dynamically: a chip whose
> action no-ops in the current selection state (e.g. `copy` /
> `delete` when the cursor is on the `..` parent row) renders with
> the `-disabled` class (`text-style: dim`) without losing its slot.

## 1.4. Modal forwarding for Enter / Esc / arrows

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

## 1.5. Layer separation

The target architecture is action-registry dispatch: views should route
through action IDs and `BindingResolver` so rebinding can be purely
config-driven. v0.8.x is not there yet; the live app still has a
hard-coded `AwsTuiApp.BINDINGS` table for the wired keys listed in §2,
with direct forwarding into VM commands. Keep new action IDs registered
in `KeymapStore` / `ActionRegistry` now so the later router handoff is a
mechanical swap rather than a vocabulary migration.
