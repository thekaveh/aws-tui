# Theming

> Mirror of spec §4.5. Four built-in themes ship; the default is
> configurable; full `.tcss` overrides are supported.

## 1. Built-in themes
| Theme | Vibe | Borders | Accent |
|---|---|---|---|
| `carbon` (default) | Near-monochrome, macOS-quietness | thin | ice-blue (`#6fb8ff`) |
| `voidline` | Neon cyan + magenta on near-black; Tron / Blade Runner | heavy double-line `╔══╗` | cyan + magenta |
| `lattice` | Mint-teal + lavender on deep teal | rounded `╭─╮` | mint (`#4ce0d2`) |
| `amber` | Retro phosphor monitor | thick block | amber (`#ffb000`) |

Carbon's discipline: **one accent color**, **three-tier text
hierarchy** (primary / secondary / label), semantic colors reserved
for narrow, meaningful uses (`success` only on auth + transfer status;
`danger` only on "cannot be undone" affordances; `warning` only on
numerics in Quick Look).

## 2. Selecting a theme
In config:

```toml
# ~/.config/aws-tui/config.toml
[defaults]
theme = "voidline"
```

At runtime via the command palette:

```
:                       # opens palette
theme switch ▸ voidline # arrow keys or fuzzy filter
Enter                   # apply
```

The switch lives only for the session unless you also update
`config.toml`. No restart needed — `ThemeChangedMessage` reflows
the active stylesheet on the fly.

## 3. User overrides
### 3.1. Single-token overrides
Drop `~/.config/aws-tui/theme.tcss` to override individual tokens of
the active built-in. The overlay layers on top of the built-in CSS so
you can adjust one or two colors without forking the whole theme:

```tcss
/* ~/.config/aws-tui/theme.tcss */

Screen {
    background: #050810;     /* override `bg` */
}

.modal-title {
    color: #ff3df8;          /* recolor modal titles */
}
```

### 3.2. Full custom themes
Drop a full `.tcss` file under `~/.config/aws-tui/themes/<name>.tcss`
and it's selectable like any built-in (palette: `theme switch ▸
<name>`).

Use one of the built-ins as a starting point — they live in the
package data at `src/aws_tui/ui/themes/<name>.tcss`.

## 4. Palette tokens
The Carbon palette tokens (full spec table in §4.5):

| Token | Hex | Use |
|---|---|---|
| `bg` | `#0d0e10` | Frame background |
| `bg-sel` | `#16252e` | Selected row tint |
| `rule-dim` | `#2a2d33` | Thin dividers |
| `text` | `#e6e8eb` | Primary values |
| `text-muted` | `#8a8e96` | Secondary values |
| `text-dim` | `#5e6470` | Labels |
| `accent` | `#6fb8ff` | Focused/actionable glyphs |
| `accent-soft` | `#cfe4ff` | Selected-row foreground |
| `magenta` | `#c9a0ff` | Command palette `:` glyph |
| `success` | `#5cd693` | SSO ok, transfer up arrow |
| `warning` | `#f0c674` | Auth-pending state |
| `danger` | `#ff6b7a` | Destructive op modal accents |

See spec §4.5 for the matching Voidline / Lattice / Amber tables.

## 5. How the loader works
`infra/theme_store.py` reads the active theme by:

1. Loading the built-in `<name>.tcss` from the package data via
   `importlib.resources`.
2. If `~/.config/aws-tui/themes/<name>.tcss` exists, **replacing**
   the built-in with it (custom theme wins).
3. If `~/.config/aws-tui/theme.tcss` exists, **appending** it to the
   active CSS (overlay always wins).
4. Returning the combined string; `App.stylesheet.add_source` injects
   it as additional rules and `App.stylesheet.update(self)` reflows.

The overlay layering means you can keep the built-in look and adjust
just one or two colors without copying the entire theme.

## 6. Snapshot tests
The four themes are pinned by 40 SVG snapshot tests in
`tests/snapshot/__snapshots__/` (main screen + 6 modals × 4 themes +
pane-state placeholders). Updates: `uv run pytest tests/snapshot
--snapshot-update`. Snapshots are CI-gated only on Python 3.12 /
Ubuntu to avoid tolerance flakes.
