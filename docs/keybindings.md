# Keybindings

> Mirror of spec §4.2. Fully customizable via the `[keybindings]`
> section of `~/.config/aws-tui/config.toml`.

The defaults are macOS-tailored — no F-keys, no `⌘`-modifier
(terminals intercept it). Letter-driven, with the command palette
(`:` or `Ctrl+K`) as the universal escape hatch.

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
| Enter multi-select mode | `v` | Visual-block style |
| Toggle row selection | `Space` (in multi-select) | |
| Extend selection one row | `Shift+↑` / `Shift+↓` | Marks the current row + moves cursor |
| Modifier+click on row | `Shift+Click`, `Cmd+Click`, `Ctrl+Click` | Toggles mark on the clicked row; on macOS terminals reserve `Shift+Click`, so `Cmd+Click` is the reliable path there |
| Select all | `a` (in multi-select) | |
| Clear selection | `Esc` (in multi-select) | |

### 1.3. File operations

| Action | Default | Notes |
|---|---|---|
| Copy across panes | `c` | Streams through `CrossFsCopy`, shows confirm modal |
| Move across panes | `m` | Copy + delete-source after success |
| Delete (with confirm) | `d` | Confirm modal; destructive ops always ask |
| New folder | `n` | |
| Rename in place | `m` | Move-with-one-marked doubles as rename |
| Refresh pane | `r` | |

### 1.4. Overlays

| Action | Default |
|---|---|
| Quick Look | `Space` (normal mode) |
| Fuzzy find | `Ctrl+P` |
| Filter pane | `/` |
| Command palette | `:` or `Ctrl+K` |
| Theme picker (modal) | `t` |
| Cycle to next theme (no modal) | `Shift+T` (`T`) |
| Help overlay | `?` |

### 1.5. Pane chrome

| Action | Default | Notes |
|---|---|---|
| Toggle services rail (collapsed ↔ expanded) | `s` | Also toggles on a mouse click on the rail |
| Swap focused pane source (S3 ↔ local) | `Shift+S` (`S`) | Enables any of `{S3, local} × {S3, local}` dual-pane combos |

### 1.6. Connection / auth

| Action | Default |
|---|---|
| Authenticate (when auth toast active) | `a` |
| Connection switcher | `:` then `connection switch` |

### 1.7. App

| Action | Default |
|---|---|
| Cancel / dismiss modal | `Esc` |
| Quit | `q` or `Ctrl+C` |

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

The mainline navigation, file-ops, overlay, and chrome actions are
fully wired into Textual handlers. **v0.7.x status**: the
`KeymapStore` accepts the `[keybindings]` overlay via its constructor
and validates every action id, but the composition root does not yet
read the overlay from `config.toml` — that wiring is part of the
input-router work deferred from M6 (see
[cookbook.md §3](cookbook.md#3-customize-a-keybinding)). Today the
same effect is achievable by editing
`src/aws_tui/infra/keymap_store.py::DEFAULT_BINDINGS` directly in
a fork. Bind ahead of time in your config and the wiring will pick
them up once it lands.

## 3. Action IDs

| Action ID | Default key | What it does |
|---|---|---|
| `app.quit` | `q` / `ctrl+c` | Graceful shutdown |
| `app.command_palette` | `:` / `ctrl+k` | Open palette |
| `app.help` | `?` | Help overlay |
| `app.themes` | `t` | Open theme picker modal |
| `app.cycle_theme` | `T` (`shift+t`) | Cycle to next theme without opening the modal |
| `app.swap_source` | `S` (`shift+s`) | Swap the focused pane between S3 and local |
| `pane.move_up` / `pane.move_down` | `↑` / `↓` (also `j` / `k`) | Move cursor |
| `pane.descend` | `enter` | Descend into folder / bucket |
| `pane.ascend` | `backspace` / `←` | Parent path |
| `pane.switch_focus` | `tab` | Move focus to the other pane |
| `pane.switch_focus_back` | `shift+tab` | Move focus to the previous pane |
| `pane.quick_look` | `space` (normal mode) | Stream first 64 KB |
| `pane.filter` | `/` | Local pane filter |
| `pane.fuzzy_find` | `ctrl+p` | Fuzzy find paths / buckets |
| `pane.enter_multiselect` | `v` | Enter multi-select mode |
| `pane.toggle_select` | `space` (multi-select) | Add / remove from selection |
| `pane.select_all` | `a` | Select all in pane |
| `pane.copy` | `c` | Copy marked entries to other pane |
| `pane.move` | `m` | Move marked entries to other pane (or rename one) |
| `pane.delete` | `d` | Delete marked entries (confirms) |
| `pane.new` | `n` | New folder / bucket |
| `pane.refresh` | `r` | Re-run `provider.list()` |
| `auth.authenticate` | `a` (when auth toast active) | Shell-out to `aws sso login` |
| `modal.cancel` | `escape` | Cancel / close current overlay |

These are the action IDs `KeymapStore.DEFAULT_BINDINGS` actually
registers. Overlay any of them in your `[keybindings]` table; any other
id raises `UnknownAction` at startup.

`Shift+↑` / `Shift+↓` (extend-selection) and `s` (services rail toggle)
are wired directly in `AwsTuiApp.BINDINGS` rather than the keymap
store, because they're either modifier combinations or static UI
toggles. They are not currently rebindable through `[keybindings]`.

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
