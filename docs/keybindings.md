# Keybindings

> Mirror of spec §4.2. Fully customizable via the `[keybindings]`
> section of `~/.config/aws-tui/config.toml`.

The defaults are macOS-tailored — no F-keys, no `⌘`-modifier
(terminals intercept it). Letter-driven, with the command palette
(`:` or `Ctrl+K`) as the universal escape hatch.

## Default bindings

### Navigation

| Action | Default | Notes |
|---|---|---|
| Cursor up / down | `↑ ↓` or `j k` | vi-style alternatives are first-class |
| Descend into directory / bucket | `Enter` | |
| Ascend one level | `Backspace` or `←` | |
| Switch pane focus | `Tab` / `Shift+Tab` | |
| Page up / down | `PageUp` / `PageDown` | |
| Top / bottom | `g` / `G` | |
| Toggle hidden files (LocalFS) | `.` | |

### Selection

| Action | Default | Notes |
|---|---|---|
| Enter multi-select mode | `v` | Visual-block style |
| Toggle row selection | `Space` (in multi-select) | |
| Select all | `a` (in multi-select) | |
| Clear selection | `Esc` (in multi-select) | |

### File operations

| Action | Default | Notes |
|---|---|---|
| Copy across panes | `c` | Streams through `CrossFsCopy` |
| Move across panes | `m` | Copy + delete-source after success |
| Delete (with confirm) | `d` | Confirm modal; destructive ops always ask |
| New folder | `n` | |
| Rename in place | `r` | |
| Refresh pane | `Ctrl+R` | |

### Overlays

| Action | Default |
|---|---|
| Quick Look | `Space` (normal mode) |
| Fuzzy find | `Ctrl+P` |
| Filter pane | `/` |
| Command palette | `:` or `Ctrl+K` |
| Transfers tray | `t` |
| Help overlay | `?` |

### Connection / auth

| Action | Default |
|---|---|
| Authenticate (when auth toast active) | `a` |
| Connection switcher | `:` then `connection switch` |

### App

| Action | Default |
|---|---|
| Cancel / dismiss modal | `Esc` |
| Quit | `q` or `Ctrl+C` |

## Customizing

A binding can be a single keystroke or a list of fallback keystrokes:

```toml
[keybindings]
"pane.copy" = "c"
"pane.delete" = "d"
"app.command_palette" = ["ctrl+k", ":"]
"app.help" = "?"
```

The default map is declared in `infra/keymap_store.py` and merged with
your overlay; unknown action ids are rejected so a typo in your config
raises a startup error instead of silently dropping a binding.

Note: the keyboard ↔ action ↔ VM-command input router is not yet
fully wired in v0.7 (only `q` / `Ctrl+C` are routed via Textual's
hardcoded `BINDINGS`). The `[keybindings]` overlay is parsed and
validated against `KeymapStore.DEFAULT_BINDINGS`, but most actions
do not yet have a Textual-side handler. Customizing the overlay
today gates how future rebinds will land.

## Action IDs

| Action ID | Default key | What it does |
|---|---|---|
| `app.quit` | `q` / `ctrl+c` | Graceful shutdown |
| `app.command_palette` | `:` / `ctrl+k` | Open palette |
| `app.help` | `?` | Help overlay |
| `app.transfers_tray` | `t` | Toggle transfers tray |
| `pane.move_up` / `pane.move_down` | `↑` (`up`) `j` / `↓` (`down`) `k` | Move cursor |
| `pane.descend` | `enter` | Descend into folder / bucket |
| `pane.ascend` | `backspace` / `←` (`left`) | Parent path |
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

## Layer separation

The View layer never invokes a VM command by attribute access; it
always goes through the action registry. That's how rebinding can be
purely config-driven — no Textual `BINDINGS` are hard-coded except
for `q` and `Ctrl+C` (so the app can always exit even if the keymap
is broken).
