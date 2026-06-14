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
"pane.delete_marked" = "d"
"app.command_palette" = ["Ctrl+K", ":"]
"app.help" = "?"
```

The input router goes through `ui/actions.py` (action registry) →
`ui/bindings.py` (action ↔ key) → VM command. The default map
(declared in `infra/keymap_store.py`) is merged with your overlay;
unknown action ids are rejected so a typo in your config raises a
startup error instead of silently dropping a binding.

## Action IDs

The full list of registered actions is dumped on every launch into
`~/.cache/aws-tui/log/aws-tui.log` (look for the `bindings.resolved`
record). Highlights:

| Action ID | Default key | What it does |
|---|---|---|
| `pane.cursor_up` / `pane.cursor_down` | `k` / `j` | Move cursor |
| `pane.open` | `Enter` | Descend |
| `pane.ascend` | `Backspace` | Go up one level |
| `pane.refresh` | `Ctrl+R` | Re-run `provider.list()` |
| `pane.copy` / `pane.move` / `pane.delete_marked` | `c` / `m` / `d` | Cross-pane ops |
| `pane.toggle_select` | `Space` (multi-select) | Add/remove from selection |
| `pane.set_filter` | `/` | Local pane filter |
| `dualpane.switch_focus` | `Tab` | Move focus between panes |
| `command_palette.open` | `:` / `Ctrl+K` | Open palette |
| `quick_look.open` | `Space` (normal) | Stream first 64 KB |
| `app.quit` | `q` / `Ctrl+C` | Graceful shutdown |
| `theme.switch` | (palette only) | Reload `.tcss` for the chosen theme |

## Layer separation

The View layer never invokes a VM command by attribute access; it
always goes through the action registry. That's how rebinding can be
purely config-driven — no Textual `BINDINGS` are hard-coded except
for `q` and `Ctrl+C` (so the app can always exit even if the keymap
is broken).
