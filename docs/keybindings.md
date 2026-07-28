# 1. Keybindings

> Mirror of spec §4.2. `[keybindings]` entries in
> `<config-dir>/config.toml` are validated by `KeymapStore` and
> installed at runtime through `BindingResolver`. Only action IDs with
> registered handlers receive live Textual bindings.

The defaults are macOS-tailored — no F-keys, no `⌘`-modifier
(terminals intercept it). Letter-driven, with the command palette
(`:` or `Ctrl+K`) as the universal escape hatch.

> **Wiring status:** rows below tagged `(deferred)` are
> declared in `KeymapStore.DEFAULT_BINDINGS` but the matching
> `action_*` handler has not yet been added to `AwsTuiApp`. They
> remain valid action IDs, but `BindingResolver` leaves them unbound
> until the matching handler ships.

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
| Quick Look | `Space` | Streams the first 64 KB of the selected file |
| Fuzzy find | `pane.fuzzy_find` action — *(deferred)* | Spec'd on `Ctrl+P`; not wired |
| Filter pane | `pane.filter` action — *(deferred)* | Spec'd on `/`; not wired |
| Command palette | `:` / `Ctrl+K` | Opens the fuzzy app-command palette |
| Theme picker (modal) | `t` | |
| Cycle to next theme (no modal) | `Shift+T` (`T`) | |
| Help overlay | `?` | |

### 1.1.5. Pane chrome

| Action | Default | Notes |
|---|---|---|
| Open Settings | `,` (comma) | Opens the in-app Settings nav page directly. Equivalent to arrow-keying down to the ⚙ Settings row in the rail and pressing `Enter`. |
| Switch source | `Shift+S` (`S`) | On S3, cycles the focused pane through `local` and resolver-ordered configured sources. On single-context AWS services such as EMR, Glue, and Athena, rebuilds the current service under the next supported AWS profile. |

> **Nav-menu visibility:** the left rail is always visible at a single
> fixed width and shows TEXT labels (Settings docked at the bottom as
> the ⚙ glyph). The pre-PR-#94 `m`-key collapse/expand toggle was
> dropped because there is no longer a collapsed mode to toggle into;
> `BindingResolver` does not emit `m` because the deferred `pane.move`
> action has no registered handler (§1.3).

### 1.1.6. Connection / auth

| Action | Default | Notes |
|---|---|---|
| Authenticate (when auth toast active) | `auth.authenticate` action — *(deferred)* | Spec'd on `a`; handler not wired in v0.8.x |
| Connection switcher | no shipped command — *(deferred)* | Dynamic `connection switch <name>` palette entries are not registered. |

The command palette opens today with `:` or `Ctrl+K`; only the dynamic
connection-switch entries in the row above remain deferred.

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
| Cycle next application | `Shift+A` | Cycles to the next EMR application without opening the picker. `Shift+S` remains available to switch the EMR page to the next configured AWS profile. |
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

> **Right-pane refresh note:** `r` refreshes the currently focused
> right-side surface: detail focus reloads the selected job-run detail;
> logs focus re-fetches logs from S3.

### 1.1.9. AWS Glue

Glue is a single-context AWS service. It keeps one active connection
and region for the whole page; S3-compatible connections are excluded.

| Action | Default | Notes |
|---|---|---|
| Catalog / Jobs / Crawlers | `1` / `2` / `3` | Selects the corresponding Glue view. |
| Cursor up / down | `↑` `↓` (also `k` / `j`) | Moves the focused resource list or scrolls detail. |
| Cycle focus | `Tab` / `Shift+Tab` | Walks Glue tabs, lists, filters, and detail controls. |
| Refresh active view | `r` | Reloads only the selected Catalog, Jobs, or Crawlers view. |
| Switch AWS source | `Shift+S` | Rebuilds Glue under the next supported AWS profile and region. |
| Open selected table location in S3 | `:` / `Ctrl+K`, then **Open table location in S3** | `glue.open_s3_location` is palette-only and absent from `KeymapStore.DEFAULT_BINDINGS`. It preserves the exact Glue connection name and region; malformed or missing locations do not navigate. |
| Query selected table in Athena | `:` / `Ctrl+K`, then **Query table in Athena** | `glue.query_in_athena` is palette-only. It prefills exact, bounded SQL in Athena and never executes it automatically. |
| Query selected Iceberg snapshot in Athena | `V` or the Iceberg time-travel button | Runs `glue.time_travel_in_athena` only for a visible selected snapshot on the Snapshots tab. It prefills `FOR VERSION AS OF` SQL without executing. |

### 1.1.10. Amazon Athena

Athena is a single-context AWS service; its controls do not appear for
S3-compatible connections.

| Action | Default | Notes |
|---|---|---|
| Query / History / Results / Saved | `1` / `2` / `3` / `4` | Selects the matching Athena view. |
| Execute editor SQL | `Ctrl+Enter` | Runs `athena.execute` only after the local read-only SQL validation succeeds. |
| Cancel active query | `Esc` | Runs `athena.cancel`; it can stop only an app-owned active execution. |
| Load more result rows | `l` | Runs `athena.load_more` when the selected result has another page. |
| Switch AWS source | `Shift+S` | Rebuilds Athena under the next supported AWS profile and region. |
| Open result artifact in S3 | `:` / `Ctrl+K`, then **Open Athena result in S3** | `athena.open_result_location` is palette-only and absent from `KeymapStore.DEFAULT_BINDINGS`; it validates the successful execution's exact connection, region, and S3 URI before navigating. |
| Open query table in Glue | `:` / `Ctrl+K`, then **Open query table in Glue** | `athena.open_in_glue` is palette-only and works only when the current read-only SQL resolves to one visible table. |

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
"emr.next_application" = "A"
"glue.catalog" = "1"
"glue.jobs" = "2"
"glue.crawlers" = "3"
"glue.time_travel_in_athena" = "V"
"athena.query" = "1"
"athena.history" = "2"
"athena.results" = "3"
"athena.saved" = "4"
"athena.execute" = "ctrl+enter"
"athena.cancel" = "escape"
"athena.load_more" = "l"
```

The default map is declared in `infra/keymap_store.py`. At composition
time, aws-tui validates the overlay and `BindingResolver` installs keys
only for registered actions. Unknown action IDs are logged and the app
continues with the default keymap so a typo does not crash startup.

The bindings that are wired today include `q`,
`Ctrl+C`, `Tab` / `Shift+Tab`, `↑/↓` (and `j/k`), `Enter`,
`Backspace`, `←`, `→`, `r`, `?`, `:`, `t`, `T`, `,` (comma → Settings),
`c`, `d`, `S` (Shift+S), `A` (Shift+A), Glue `1` / `2` / `3`, Athena
`1` / `2` / `3` / `4`, `V`, `Ctrl+Enter`, `Esc`, `l`,
`Shift+↑`, and `Shift+↓`.

## 1.3. Action IDs

The `wired?` column marks whether `AwsTuiApp` currently registers a
matching `ActionRegistry` handler. `(deferred)` rows are valid action
IDs you can overlay ahead of time; `BindingResolver` leaves them
unbound until a handler ships.

| Action ID | Default key | Wired? | What it does |
|---|---|---|---|
| `app.quit` | `q` / `ctrl+c` | ✓ | Graceful shutdown |
| `app.command_palette` | `:` / `ctrl+k` | ✓ | Open the command palette |
| `app.help` | `?` | ✓ | Help overlay |
| `app.themes` | `t` | ✓ | Open theme picker modal |
| `app.cycle_theme` | `T` (`shift+t`) | ✓ | Cycle to next theme without opening the modal |
| `app.swap_source` | `S` (`shift+s`) | ✓ | Switch the focused S3 pane source, or rebuild the current single-context AWS service under the next profile |
| `pane.move_up` / `pane.move_down` | `↑` / `↓` (also `k` / `j`) | ✓ | Move cursor |
| `pane.descend` | `enter` | ✓ | Descend into folder / bucket |
| `pane.ascend` | `backspace` / `←` | ✓ | Parent path |
| `pane.switch_focus` | `tab` | ✓ | Cycle the active page's focus ring |
| `pane.switch_focus_back` | `shift+tab` | ✓ | Cycle the active page's focus ring in reverse |
| `pane.quick_look` | `space` (normal mode) | ✓ | Stream first 64 KB |
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
| `emr.next_application` | `A` (`shift+a`) | ✓ | Cycle to the next EMR application |
| `emr.clone` | `c` (when EMR page mounted) | ✓ | Open the EMR clone-job-run modal pre-filled from the focused run (PR #83) |
| `emr.logs.filter` | `f` (when EMR logs pane focused) | widget-scoped | Open the EMR logs filter modal |
| `glue.catalog` | `1` | ✓ | Select the Glue Catalog view |
| `glue.jobs` | `2` | ✓ | Select the Glue Jobs view |
| `glue.crawlers` | `3` | ✓ | Select the Glue Crawlers view |
| `glue.open_s3_location` | none (command palette) | ✓ | Open the selected Glue table's S3 location under the exact source connection and region |
| `glue.query_in_athena` | none (command palette) | ✓ | Prefill a bounded query for the selected Glue table in Athena |
| `glue.time_travel_in_athena` | `V` | ✓ | Prefill a bounded Athena query for the selected visible Iceberg snapshot |
| `athena.query` | `1` | ✓ | Select the Athena Query view |
| `athena.history` | `2` | ✓ | Select the Athena History view |
| `athena.results` | `3` | ✓ | Select the Athena Results view |
| `athena.saved` | `4` | ✓ | Select the Athena Saved view |
| `athena.execute` | `ctrl+enter` | ✓ | Submit validated, read-only editor SQL |
| `athena.cancel` | `escape` | ✓ | Stop an app-owned active Athena query |
| `athena.load_more` | `l` | ✓ | Fetch the next available result page |
| `athena.open_result_location` | none (command palette) | ✓ | Open a validated successful Athena result artifact in S3 under its exact source identity |
| `athena.open_in_glue` | none (command palette) | ✓ | Open the one unambiguous visible query table in Glue |
| `modal.cancel` | `escape` | ✓ | Cancel / close current overlay (modal-owned) |

Rows with a default key are registered by
`KeymapStore.DEFAULT_BINDINGS` and may be overlaid in
`[keybindings]`. `glue.open_s3_location`, `glue.query_in_athena`,
`athena.open_result_location`, and `athena.open_in_glue` are palette-only,
registered in `ActionRegistry`, and absent from
`KeymapStore.DEFAULT_BINDINGS`; assigning them a key is not currently
supported.
Any unknown overlay id is logged and causes the app to fall back to
the default keymap.

All live App-level bindings are installed through `BindingResolver`,
including `Shift+↑` / `Shift+↓` for extend-selection. Their
`pane.mark_up` / `pane.mark_down` entries may be rebound through
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

Views route through action IDs and `BindingResolver`; service-neutral
VM messages handle cross-service requests. Keep new keyed actions in
`KeymapStore` and `ActionRegistry` together. Palette-only commands such
as `glue.open_s3_location` belong in `ActionRegistry` and the curated
palette, but do not need a default key. The same rule applies to
`athena.open_result_location`.
