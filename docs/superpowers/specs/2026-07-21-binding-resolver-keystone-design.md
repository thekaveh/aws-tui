# BindingResolver Keystone — Design Spec

**Date:** 2026-07-21
**Status:** Approved (design brief) — pending implementation
**Scope:** Keystone increment only. Quick Look, command palette, and the
other deferred `*_requested` wirings are explicitly out of scope.

## Goal

Route `AwsTuiApp`'s key bindings through the already-built
`BindingResolver` + `KeymapStore` + `ActionRegistry` at runtime, so that
`config.toml [keybindings]` overlays take effect — **with default-keymap
behavior byte-identical to today's hard-coded `BINDINGS`** (same keys,
same handlers, same `priority`, same footer visibility, modulo one
documented dedup).

## Background / current state (verified in code)

- `src/aws_tui/ui/bindings.py` — `BindingResolver` is fully built and
  unit-tested: `to_textual_bindings()`, `resolve_action_id()`,
  `keys_for()`. **Constructed at `app.py:254` but never used at runtime.**
- `src/aws_tui/ui/actions.py` — `ActionRegistry` (`register`/`invoke`/
  `has`/`known_actions`). **Only `app.quit` is registered today.**
- `src/aws_tui/infra/keymap_store.py` — `KeymapStore.DEFAULT_BINDINGS`
  (action id → key tuple) + overlay merge (overlay replaces wholesale,
  unknown action rejected).
- `src/aws_tui/app.py:217` — `BINDINGS: ClassVar[list[BindingType]]` is
  hard-coded (~21 bindings), with `priority=True` on all nav keys. The
  comment at `app.py:213` documents that the overlay is parsed but not
  routed.

### The drift (the crux)

`KeymapStore.DEFAULT_BINDINGS` and the live `BINDINGS` disagree. Wiring
the resolver naively would regress behavior:

| Key | Live `BINDINGS` today | `KeymapStore` default | Resolution |
|---|---|---|---|
| `:` | `help` | `app.command_palette` | keep `:`→help; move `:` to `app.help`, drop from (deferred) `app.command_palette` |
| `left` | `modal_left_or_ascend` | folded into `pane.ascend` | split: `pane.ascend`=(backspace,), new `pane.modal_left`=(left,) |
| `comma`, `shift+up`, `shift+down` | `open_settings`, `mark_up`, `mark_down` | **absent** | add `app.open_settings`, `pane.mark_up`, `pane.mark_down` |
| `space`,`/`,`v`,`a`,`m`,`n`,`ctrl+p`,`ctrl+k` | unbound | quick_look/filter/fuzzy_find/multiselect/select/move/new/palette | **handlerless → resolver skips them** (stay unbound, as today) |

## Design

### 1. `ActionRegistry` is the set of actually-handled actions

At composition, register each live action id → its existing `action_*`
handler (bound method). The registry becomes the authority on "what the
app can do."

| action id | handler method | keys | show | priority |
|---|---|---|---|---|
| `app.quit` | `action_quit` | `q`, `ctrl+c` | q only | — |
| `pane.switch_focus` | `action_switch_focus` | `tab` | ✓ | ✓ |
| `pane.switch_focus_back` | `action_switch_focus_reverse` | `shift+tab` | — | ✓ |
| `pane.move_up` | `action_move_up` | `up`, `k` | ✓ | ✓ |
| `pane.move_down` | `action_move_down` | `down`, `j` | ✓ | ✓ |
| `pane.descend` | `action_descend` | `enter` | ✓ | ✓ |
| `pane.ascend` | `action_ascend` | `backspace` | ✓ | ✓ |
| `pane.modal_left` | `action_modal_left_or_ascend` | `left` | — | ✓ |
| `pane.modal_right` | `action_modal_right` | `right` | — | ✓ |
| `pane.refresh` | `action_refresh` | `r` | ✓ | ✓ |
| `app.help` | `action_help` | `?`, `:` | ✓ | ✓ |
| `app.themes` | `action_themes` | `t` | ✓ | ✓ |
| `app.cycle_theme` | `action_cycle_theme` | `T` | ✓ | ✓ |
| `app.open_settings` | `action_open_settings` | `,` | ✓ | ✓ |
| `pane.copy` | `action_copy` | `c` | ✓ | ✓ |
| `pane.delete` | `action_delete` | `d` | ✓ | ✓ |
| `app.swap_source` | `action_swap_source` | `S` | ✓ | ✓ |
| `pane.mark_up` | `action_mark_up` | `shift+up` | — | ✓ |
| `pane.mark_down` | `action_mark_down` | `shift+down` | — | ✓ |

**Priority rule:** every handled action is `priority=True` **except**
`app.quit`. (Matches today: only `q`/`ctrl+c` lack priority.)

### 2. Resolver materializes only handled actions

`to_textual_bindings()` gains a guard: emit a `Binding` for `action_id`
**only when `ActionRegistry.has(action_id)`**. Deferred/handlerless
actions (`app.command_palette`, `pane.quick_look`, `pane.filter`,
`pane.fuzzy_find`, `pane.enter_multiselect`, `pane.toggle_select`,
`pane.select_all`, `pane.move`, `pane.new`, `auth.authenticate`) stay in
`DEFAULT_BINDINGS` as documented-but-unbound — no handlerless keys reach
the runtime. `modal.cancel`, `emr.clone`, `emr.logs.filter` are
widget/modal-scoped and untouched by this app-level keystone.

### 3. Single dispatch entry point

Replace `_textual_action_name` (`pane.copy` → `pane_copy`, which would
need ~20 `action_*` forwarders) with a **parameterized dispatch action**.
Each emitted `Binding` uses `action = f"dispatch({action_id!r})"`. The App
gains ONE method:

```python
def action_dispatch(self, action_id: str) -> None | Awaitable[None]:
    return self._actions.invoke(action_id)
```

`ActionRegistry.invoke` already returns `None | Awaitable[None]`; Textual
awaits awaitable action returns, so async handlers keep working.

### 4. Priority / show metadata

`to_textual_bindings()` sets, per binding:
- `show = (index == 0) and (action_id in _VISIBLE_ACTIONS)`
- `priority = action_id in _PRIORITY_ACTIONS`

`_VISIBLE_ACTIONS` and `_PRIORITY_ACTIONS` are module-level frozensets in
`bindings.py` derived from the table above (`_PRIORITY_ACTIONS` = all
handled ids minus `app.quit`).

### 5. Align `KeymapStore.DEFAULT_BINDINGS` to reproduce today's behavior

Edits (handled actions only; deferred entries kept as documented):
- `app.help`: `("?",)` → `("?", ":")`  *(`:` aliased to help until the
  command palette is wired; then `:` moves back to `app.command_palette`)*
- `app.command_palette`: `(":", "ctrl+k")` → `("ctrl+k",)` *(drop `:`; still handlerless → unbound)*
- `pane.ascend`: `("backspace", "left")` → `("backspace",)`
- add `pane.modal_left`: `("left",)`
- add `pane.modal_right`: `("right",)`
- add `app.open_settings`: `(",",)`
- add `pane.mark_up`: `("shift+up",)`
- add `pane.mark_down`: `("shift+down",)`

### 6. Install resolver bindings at runtime

`AwsTuiApp.BINDINGS` ClassVar becomes `[]` (or is removed). After the
resolver is built in `__init__`, install its bindings via Textual's
binding API (`self._bindings` / `bind()`), before first render. A test
asserts Tab/arrow nav still fire (priority preserved).

### 7. Overlays go live

`AwsTuiApp` already loads the `[keybindings]` overlay into `KeymapStore`.
Once the resolver drives `BINDINGS`, an overlay that remaps a handled
action (e.g. `pane.copy = "y"`) takes effect with no further work.

## Backward-compatibility guarantee

Every key that works today still works and invokes the same handler;
`priority` preserved for all nav keys. **One documented deviation:** today
both `?` and `:` show a "Help" footer chip; under the single-visible-key
model only `?` shows the chip (both keys still open help). This removes a
duplicate chip — accepted, not a regression.

## Testing (TDD)

1. **Default fidelity** — build the resolver on a default `KeymapStore` +
   fully-registered `ActionRegistry`; assert `to_textual_bindings()`
   yields exactly the expected `(key, action_id, show, priority)` set from
   the table (the single source of truth for "byte-identical").
2. **Handlerless skip** — assert deferred actions
   (`pane.quick_look`, `app.command_palette`, …) emit **no** binding.
3. **Dispatch routing** — `action_dispatch("pane.copy")` invokes the
   registered `pane.copy` handler exactly once; unknown id raises
   `UnknownAction`.
4. **Overlay remap** — with overlay `{"pane.copy": "y"}`, the materialized
   bindings bind `y`→`pane.copy` and drop `c`.
5. **Nav-not-regressed** — a pilot/integration test: Tab switches panes,
   arrows move the cursor (priority bindings still win over Screen focus).
6. **`KeymapStore` defaults** — unit-assert the edited `DEFAULT_BINDINGS`
   for the handled actions matches the table.

Existing `bindings.py`/`keymap_store.py`/`actions.py` unit suites must
stay green (adjust the resolver tests for the new skip/priority/dispatch
behavior).

## Out of scope

Quick Look (`space` + `preview_requested` subscriber), command palette
(`:`/`ctrl+k` + `CommandPaletteVM` opening), `pane.enter_multiselect`,
`pane.filter`/`fuzzy_find`, and the crash-modal / `*_requested` orphan
wirings — each a later gitflow increment. This keystone only makes the
**currently-handled** actions overlay-driven.

## Files touched

- `src/aws_tui/ui/bindings.py` — skip-unregistered guard; `_VISIBLE_ACTIONS`
  / `_PRIORITY_ACTIONS`; dispatch-action emission.
- `src/aws_tui/ui/actions.py` — unchanged (surface already sufficient).
- `src/aws_tui/infra/keymap_store.py` — `DEFAULT_BINDINGS` reconciliation.
- `src/aws_tui/app.py` — register handlers; `action_dispatch`; install
  resolver bindings; drop the hard-coded `BINDINGS`.
- `tests/unit/ui/test_bindings.py`, `tests/unit/infra/test_keymap_store.py`,
  `tests/unit/ui/test_actions.py` — extend per the tests above.
- `tests/integration/` — nav-not-regressed pilot test.
