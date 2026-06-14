# Keybindings

> Mirror of spec §4.2. Fully customizable via `~/.config/aws-tui/config.toml` `[keybindings]`.

The defaults are macOS-tailored — no F-keys, no `⌘`-modifier (terminals intercept it). Letter-driven, command palette (`:` or `Ctrl+K`) as the universal escape hatch.

| Action | Default key |
|---|---|
| Move cursor | `↑ ↓` or `j k` |
| Descend | `Enter` |
| Ascend | `Backspace` / `←` |
| Switch pane focus | `Tab` / `Shift+Tab` |
| Quick Look | `Space` (normal mode) |
| Enter multi-select | `v` |
| Toggle row selection | `Space` (multi-select mode) |
| Filter pane | `/` |
| Fuzzy find | `Ctrl+P` |
| Command palette | `:` or `Ctrl+K` |
| Copy / Move / Delete / New | `c` / `m` / `d` / `n` |
| Refresh | `r` |
| Transfers tray | `t` |
| Authenticate (toast active) | `a` |
| Help overlay | `?` |
| Cancel / Esc | `Esc` |
| Quit | `q` / `Ctrl+C` |

## Customizing

```toml
[keybindings]
"pane.copy" = "c"
"app.command_palette" = ["Ctrl+K", ":"]
```

The input router goes through `ui/actions.py` (action registry) → `ui/bindings.py` (action ↔ key) → VM command — so users rebind anything without touching code.
