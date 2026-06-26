# Theming

> Mirror of spec §4.5. Ten built-in themes ship (4 dark + 3 light +
> 3 popular community palettes); the default is configurable; full
> `.tcss` overrides are supported.

## 1. Built-in themes
| Theme | Vibe | Borders | Accent |
|---|---|---|---|
| `carbon` (default) | Near-monochrome, macOS-quietness | rounded | ice-blue (`#6fb8ff`) |
| `voidline` | Neon cyan + magenta on near-black; Tron / Blade Runner | heavy double-line `╔══╗` | cyan + magenta |
| `lattice` | Mint-teal + lavender on deep teal | rounded `╭─╮` | mint (`#4ce0d2`) |
| `amber` | Retro phosphor monitor | thick block | amber (`#ffb000`) |
| `solarized-light` | Ethan Schoonover's cream + muted accents (Solarized Light) | rounded `╭─╮` | Solarized blue (`#268bd2`) |
| `github-light` | Primer-style clean white with link-blue accent | rounded `╭─╮` | link blue (`#0969da`) |
| `one-light` | Atom One Light pastels on near-white | rounded `╭─╮` | One blue (`#4078f2`) |
| `nord` | Sven Greb's Arctic Polar Night with Frost accents | rounded `╭─╮` | Frost cyan (`#88c0d0`) |
| `dracula` | Zeno Rocha's purple-and-pink on deep blue | rounded `╭─╮` | purple (`#bd93f9`) |
| `gruvbox-dark` | Pavel Pertsev's warm earth tones (mahogany → mustard) | rounded `╭─╮` | gold (`#fabd2f`) |

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

At runtime via the keyboard:

```
t                       # open the theme picker modal
↑ ↓                     # arrow to the theme you want
Enter                   # apply
```

Or skip the modal entirely and cycle to the next theme:

```
Shift+T                 # cycle: carbon → voidline → lattice → amber →
                        # solarized-light → github-light → one-light →
                        # nord → dracula → gruvbox-dark → carbon ...
```

The switch lives only for the session unless you also update
`config.toml`. No restart needed — `ThemeChangedMessage` reflows
the active stylesheet on the fly.

> The command-palette path (`:` then `theme switch ▸ voidline`) is in
> the design spec but the palette doesn't yet register theme entries
> in v0.7.x — `t` / `Shift+T` are the working shortcuts.

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
| `accent-hot` | `#c9a0ff` | Command palette `:` glyph |
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
The ten themes are pinned by 214 snapshot goldens in
`tests/snapshot/__snapshots__/` across 10 scaffolding apps — main
screen, modals, nav menu, pane states, settings view, theme picker,
toast, transfers, EMR (added post-tag by PR #76's `test_emr/`
subdir), and the EMR clone-job-run modal (added by PR #83's
`test_emr_clone_modal/` subdir — 10 themes × 1 scene) — most
parametrised by theme. Every new widget snapshot is paired with a
content-presence guard test (per the PR #53 lesson). Updates:
`uv run pytest tests/snapshot --snapshot-update`. Snapshots are
CI-gated only on Python 3.12 / Ubuntu to avoid tolerance flakes.
