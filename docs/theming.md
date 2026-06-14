# Theming

> Mirror of spec §4.5. Four built-in themes ship; the default is configurable; full `.tcss` overrides are supported.

## Built-in themes

| Theme | Vibe |
|---|---|
| `carbon` (default) | Near-monochrome with one ice-blue accent. macOS-quietness. |
| `voidline` | Neon cyan + magenta on near-black. Tron / Blade Runner. |
| `lattice` | Mint-teal + lavender on deep teal. Rounded corners. |
| `amber` | Retro phosphor monitor. Distinctive; harder on the eyes for long sessions. |

## Selecting a theme

```toml
# ~/.config/aws-tui/config.toml
[defaults]
theme = "voidline"
```

Or at runtime via the command palette: `:` `theme switch ▸ voidline`.

## User overrides

Drop `~/.config/aws-tui/theme.tcss` to override any tokens of the active built-in. Drop full custom themes at `~/.config/aws-tui/themes/<name>.tcss` to make them selectable like any built-in.

The Carbon palette tokens (full table in spec §4.5):

| Token | Hex |
|---|---|
| `bg` | `#0d0e10` |
| `bg-sel` | `#16252e` |
| `text` | `#e6e8eb` |
| `text-dim` | `#5e6470` |
| `accent` | `#6fb8ff` |
| `success` | `#5cd693` |
| `danger` | `#ff6b7a` |

See spec §4.5 for Voidline / Lattice / Amber palettes.
