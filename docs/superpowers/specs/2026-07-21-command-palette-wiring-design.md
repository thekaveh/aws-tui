# Command Palette Wiring — Design Spec

**Date:** 2026-07-21
**Status:** Approved direction (user: "start working on #2") — implementing.
**Depends on:** BindingResolver keystone (`app.command_palette` handlerless →
skipped) and its documented note to move `:` back to the palette when wired.

## Goal

Wire the built-but-unreached command palette: `:` (and `Ctrl+K`) open a
fuzzy-filterable `CommandPalette` modal populated with the app-level
commands; selecting one dispatches its action.

## Background (verified)

- `CommandPaletteVM` — `register_entry(entry: PaletteEntry, action:
  PaletteAction)`, `open_command`/`close_command`/`execute_selected_command`,
  `is_open`; already `construct()`-ed (`app.py:400`) / `dispose()`-d on
  `ctx.command_palette_vm`.
- `PaletteEntry(id, label, category, keywords=())`; `PaletteAction =
  Callable[[], None | Awaitable[None]]`.
- `CommandPalette(vm, *, hub)` modal (HubSubscriberMixin).
- Keystone keymap today: `app.help: ("?", ":")`, `app.command_palette:
  ("ctrl+k",)` (handlerless → `ctrl+k` unbound). `ActionRegistry` holds the
  app actions (themes, cycle_theme, swap_source, open_settings, help, quit).

## Design

### 1. `:` remap (per the keystone note)

`KeymapStore.DEFAULT_BINDINGS`:
- `app.help: ("?", ":")` → `app.help: ("?",)`
- `app.command_palette: ("ctrl+k",)` → `app.command_palette: (":", "ctrl+k")`

`bindings.py`: add `app.command_palette` to `_VISIBLE_ACTIONS` so `:` shows a
"Command palette" footer chip (it now has a handler).

### 2. Register handler + populate

In `AwsTuiApp.__init__`'s keystone registration block:
`self._actions.register("app.command_palette", self.action_command_palette)`.

```python
_PALETTE_COMMANDS = (  # (action_id, label) — curated app-level commands
    ("app.themes", "Theme picker"),
    ("app.cycle_theme", "Cycle theme"),
    ("app.swap_source", "Swap pane source"),
    ("app.open_settings", "Settings"),
    ("app.help", "Help"),
    ("app.quit", "Quit"),
)

def _populate_command_palette(self) -> None:
    """Register the curated app commands into the palette (idempotent)."""
    if self._command_palette_populated:
        return
    vm = self._app_ctx.command_palette_vm
    for action_id, label in _PALETTE_COMMANDS:
        vm.register_entry(
            PaletteEntry(id=action_id, label=label, category="app"),
            (lambda aid=action_id: self._actions.invoke(aid)),
        )
    self._command_palette_populated = True

def action_command_palette(self) -> None:
    """Open the fuzzy command palette (bound to ``:`` / ``Ctrl+K``)."""
    self.record_action("app.command_palette")
    self._populate_command_palette()
    self._app_ctx.command_palette_vm.open_command.execute()
    self.push_screen(CommandPalette(self._app_ctx.command_palette_vm, hub=self._app_ctx.hub))
```

`self._command_palette_populated = False` initialized in `__init__`.

### 3. Execute / dismiss

The built `CommandPalette` modal + `execute_selected_command` already run the
selected entry's `PaletteAction` and close. Each action here is
`self._actions.invoke(action_id)` — the same dispatch path the key bindings
use, so selecting "Cycle theme" is identical to pressing `T`.

## Scope

**In:** `:` / `Ctrl+K` open the palette with the 6 curated app commands;
fuzzy filter (existing VM); select → dispatch.

**Out (documented follow-ons):**
- **Dynamic commands** (`switch connection <name>`, `switch theme <name>`) —
  needs enumerating connections/themes + `unregister_entry` on change.
- **Textual's built-in `Ctrl+P` palette** — left coexisting (non-breaking);
  consolidating to one palette is a follow-on.
- Pane-context commands (copy/delete/…) — they need a focused pane; omitted
  from the palette for now.

## Testing (TDD)

1. **Keymap remap** — `KeymapStore` defaults: `app.help == ("?",)`,
   `app.command_palette == (":", "ctrl+k")`.
2. **Fidelity (keystone test update)** — installed bindings: `:` and `ctrl+k`
   → `dispatch('app.command_palette')` (show/priority per the visible set),
   `?` → `dispatch('app.help')`; `ctrl+k` dropped from the handlerless guard.
3. **Population** — after `action_command_palette`, the VM has the 6 entries;
   idempotent (calling twice doesn't duplicate).
4. **Integration** — pressing `:` opens the `CommandPalette` modal; selecting
   an entry invokes its action (spy).

## Files touched

- `src/aws_tui/infra/keymap_store.py` — `:` remap.
- `src/aws_tui/ui/bindings.py` — add `app.command_palette` to `_VISIBLE_ACTIONS`.
- `src/aws_tui/app.py` — `_PALETTE_COMMANDS`, `_populate_command_palette`,
  `action_command_palette`, register handler, import `CommandPalette` +
  `PaletteEntry`, `_command_palette_populated` flag.
- `tests/integration/test_keybinding_wiring.py` — fidelity update.
- `tests/unit/…` + `tests/integration/test_command_palette_wiring.py` — new.
