# BindingResolver Keystone — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Route `AwsTuiApp` key bindings through the built-but-unwired `BindingResolver` + `KeymapStore` + `ActionRegistry` so `config.toml [keybindings]` overlays take effect, with default behavior byte-identical to today's hard-coded `BINDINGS`.

**Architecture:** `ActionRegistry` holds every handled action id → handler. `BindingResolver.to_textual_bindings()` materializes Textual `Binding`s only for registered actions, tagging `show`/`priority` from module-level sets and dispatching through a single `action_dispatch(id)`. `AwsTuiApp` installs those bindings as `self._bindings = BindingsMap(resolver.to_textual_bindings())` after registering handlers, and drops its `BINDINGS` ClassVar. `KeymapStore.DEFAULT_BINDINGS` is reconciled to reproduce today's keys.

**Tech Stack:** Python 3.12+, Textual 8.2.7 (`textual.binding.BindingsMap(Iterable[Binding])`, `Binding` fields `key/action/description/show/priority`), pytest + pytest-asyncio, Textual `App.run_test` pilot.

**Spec:** `docs/superpowers/specs/2026-07-21-binding-resolver-keystone-design.md` (the §5 table of action id → keys/handler/show/priority is the single source of truth for every exact value below).

## Global Constraints

- **Byte-identical default behavior.** With no overlay, every key that works today invokes the same handler with the same `priority`; footer `show` matches the spec table. One documented deviation: `:` no longer shows a duplicate "Help" chip (still opens help).
- **Handlerless actions stay unbound.** The resolver emits a binding only when `ActionRegistry.has(action_id)`. Deferred actions (`app.command_palette`, `pane.quick_look`, `pane.filter`, `pane.fuzzy_find`, `pane.enter_multiselect`, `pane.toggle_select`, `pane.select_all`, `pane.move`, `pane.new`, `auth.authenticate`) remain in `DEFAULT_BINDINGS` but produce no runtime binding.
- **Dispatch form.** Emitted bindings use `action = f"dispatch({action_id!r})"`; the App exposes exactly one `action_dispatch`.
- **Priority rule.** Every handled action is `priority=True` except `app.quit`.
- Existing `tests/unit/**` and `tests/integration/**` stay green (test edits below are spec-mandated behavior changes, not weakenings).
- Run `uv run pytest <target>` and `uv run ruff check` / `uv run mypy` per repo config.

## File Structure

- Modify `src/aws_tui/infra/keymap_store.py` — reconcile `DEFAULT_BINDINGS` (Task 1).
- Modify `src/aws_tui/ui/bindings.py` — skip guard, `_VISIBLE_ACTIONS`/`_PRIORITY_ACTIONS`, dispatch emission (Task 2).
- Modify `src/aws_tui/app.py` — register handlers, `action_dispatch`, install `BindingsMap`, drop `BINDINGS` (Task 3).
- Tests: `tests/unit/infra/test_keymap_store.py`, `tests/unit/ui/test_bindings.py`, `tests/integration/test_keybinding_wiring.py` (new).

---

### Task 1: Reconcile `KeymapStore.DEFAULT_BINDINGS`

**Files:**
- Modify: `src/aws_tui/infra/keymap_store.py` (`DEFAULT_BINDINGS` dict, ~line 28)
- Test: `tests/unit/infra/test_keymap_store.py`

**Interfaces:**
- Produces: the default keymap consumed by Task 2/3. Handled-action keys must match the spec §5 table exactly.

- [ ] **Step 1: Write failing tests for the reconciled defaults**

Add to `tests/unit/infra/test_keymap_store.py`:

```python
def test_default_bindings_reproduce_runtime_keys() -> None:
    km = KeymapStore()
    d = km.all()
    assert d["app.help"] == ("?", ":")
    assert d["app.command_palette"] == ("ctrl+k",)   # ":" moved to help
    assert d["pane.ascend"] == ("backspace",)         # "left" split out
    assert d["pane.modal_left"] == ("left",)
    assert d["pane.modal_right"] == ("right",)
    assert d["app.open_settings"] == (",",)
    assert d["pane.mark_up"] == ("shift+up",)
    assert d["pane.mark_down"] == ("shift+down",)
```

- [ ] **Step 2: Run to confirm failure**

Run: `uv run pytest tests/unit/infra/test_keymap_store.py::test_default_bindings_reproduce_runtime_keys -v`
Expected: FAIL (KeyError / wrong tuples).

- [ ] **Step 3: Edit `DEFAULT_BINDINGS`**

Apply exactly these changes to the dict (leave all other entries, including the deferred ones, unchanged):
- `"app.help": ("?",)` → `"app.help": ("?", ":")`
- `"app.command_palette": (":", "ctrl+k")` → `"app.command_palette": ("ctrl+k",)`
- `"pane.ascend": ("backspace", "left")` → `"pane.ascend": ("backspace",)`
- add `"pane.modal_left": ("left",)`
- add `"pane.modal_right": ("right",)`
- add `"app.open_settings": (",",)`
- add `"pane.mark_up": ("shift+up",)`
- add `"pane.mark_down": ("shift+down",)`

Add a comment on `app.command_palette` / `app.help`: `":" is aliased to help until the command palette is wired; then move ":" back to app.command_palette.`

- [ ] **Step 4: Fix any existing keymap tests that asserted the old defaults**

Search `tests/unit/infra/test_keymap_store.py` for assertions on `app.help`, `app.command_palette`, `pane.ascend`; update them to the new tuples. These are spec-mandated changes.

- [ ] **Step 5: Run keymap tests**

Run: `uv run pytest tests/unit/infra/test_keymap_store.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/aws_tui/infra/keymap_store.py tests/unit/infra/test_keymap_store.py
git commit -m "feat(keymap): reconcile DEFAULT_BINDINGS to reproduce runtime keys"
```

---

### Task 2: Resolver — skip-unregistered, dispatch action, priority/show metadata

**Files:**
- Modify: `src/aws_tui/ui/bindings.py`
- Test: `tests/unit/ui/test_bindings.py`

**Interfaces:**
- Consumes: `KeymapStore.all()` (Task 1), `ActionRegistry.has(action_id)` / `register`.
- Produces: `to_textual_bindings()` emitting `Binding(key, action=f"dispatch({id!r})", description, show, priority)` only for registered ids. `resolve_action_id`/`keys_for` unchanged.

- [ ] **Step 1: Rewrite the resolver unit tests for the new contract**

Replace the body of `tests/unit/ui/test_bindings.py` behavior expectations (keep the file's imports). A registry must register the actions under test.

```python
def _registry(*ids: str) -> ActionRegistry:
    r = ActionRegistry()
    for i in ids:
        r.register(i, lambda: None)
    return r


def test_only_registered_actions_emit_bindings() -> None:
    keymap = KeymapStore()
    actions = _registry("app.quit")  # nothing else registered
    resolver = BindingResolver(keymap=keymap, actions=actions)
    bindings = resolver.to_textual_bindings()
    # Only app.quit's two keys emit; deferred/handlerless emit nothing.
    assert {b.key for b in bindings} == {"q", "ctrl+c"}


def test_binding_action_uses_dispatch_form() -> None:
    actions = _registry("pane.copy")
    resolver = BindingResolver(keymap=KeymapStore(), actions=actions)
    (copy,) = [b for b in resolver.to_textual_bindings() if b.key == "c"]
    assert copy.action == "dispatch('pane.copy')"


def test_priority_true_except_quit() -> None:
    actions = _registry("app.quit", "pane.switch_focus")
    resolver = BindingResolver(keymap=KeymapStore(), actions=actions)
    by_key = {b.key: b for b in resolver.to_textual_bindings()}
    assert by_key["q"].priority is False
    assert by_key["tab"].priority is True


def test_secondary_key_hidden_and_cursor_hidden() -> None:
    actions = _registry("app.quit", "pane.move_up")
    resolver = BindingResolver(keymap=KeymapStore(), actions=actions)
    by_key = {b.key: b for b in resolver.to_textual_bindings()}
    assert by_key["q"].show is True
    assert by_key["ctrl+c"].show is False    # secondary key
    assert by_key["up"].show is False        # move_up not visible
    assert by_key["k"].show is False


def test_overlay_keymap_reflects_in_bindings() -> None:
    keymap = KeymapStore(overlay={"app.quit": "Q"})
    resolver = BindingResolver(keymap=keymap, actions=_registry("app.quit"))
    quit_bindings = [b for b in resolver.to_textual_bindings() if b.key == "Q"]
    assert len(quit_bindings) == 1
    assert quit_bindings[0].action == "dispatch('app.quit')"


def test_resolve_action_id_roundtrip() -> None:
    resolver = BindingResolver(keymap=KeymapStore(), actions=ActionRegistry())
    assert resolver.resolve_action_id("q") == "app.quit"
    assert resolver.resolve_action_id(":") == "app.help"   # ":" now help
    assert resolver.resolve_action_id("nope") is None
```

(Delete the old `test_to_textual_bindings_covers_every_keymap_entry`, `_replaces_dots_with_underscores`, `_hides_cursor_chips`, `_makes_secondary_keys_hidden`, `test_keys_for_returns_tuple`-keep-if-still-valid.)

- [ ] **Step 2: Run to confirm failure**

Run: `uv run pytest tests/unit/ui/test_bindings.py -v`
Expected: FAIL.

- [ ] **Step 3: Implement the resolver changes**

In `src/aws_tui/ui/bindings.py`:

Add module-level sets (from spec §5 / §4):

```python
_VISIBLE_ACTIONS: frozenset[str] = frozenset({
    "app.quit", "pane.switch_focus", "pane.move_up", "pane.move_down",
    "pane.descend", "pane.ascend", "pane.refresh", "app.help",
    "app.themes", "app.cycle_theme", "app.open_settings", "pane.copy",
    "pane.delete", "app.swap_source",
})

# Every handled action is priority except app.quit; the resolver only needs
# to know the exception because non-priority is the Binding default.
_NON_PRIORITY_ACTIONS: frozenset[str] = frozenset({"app.quit"})
```

Rewrite `to_textual_bindings`:

```python
def to_textual_bindings(self) -> list[Binding]:
    bindings: list[Binding] = []
    for action_id, keys in self._keymap.all().items():
        if not self._actions.has(action_id):
            continue  # handlerless (deferred) action -> stays unbound
        description = _describe(action_id)
        priority = action_id not in _NON_PRIORITY_ACTIONS
        visible = action_id in _VISIBLE_ACTIONS
        for index, key in enumerate(keys):
            bindings.append(
                Binding(
                    key=key,
                    action=f"dispatch({action_id!r})",
                    description=description,
                    show=index == 0 and visible,
                    priority=priority,
                )
            )
    return bindings
```

Delete `_textual_action_name` and `_is_visible_action` (replaced). Keep `resolve_action_id`, `keys_for`, `_describe`, `_ACTION_DESCRIPTIONS`.

- [ ] **Step 4: Run resolver + keymap tests**

Run: `uv run pytest tests/unit/ui/test_bindings.py tests/unit/infra/test_keymap_store.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/aws_tui/ui/bindings.py tests/unit/ui/test_bindings.py
git commit -m "feat(bindings): dispatch-action emission, skip handlerless, priority/show sets"
```

---

### Task 3: Wire the App — register handlers, dispatch, install bindings, drop ClassVar

**Files:**
- Modify: `src/aws_tui/app.py` (`BINDINGS` ~217-247; `__init__` ~249-259; add `action_dispatch`)
- Test: `tests/integration/test_keybinding_wiring.py` (new)

**Interfaces:**
- Consumes: `BindingResolver.to_textual_bindings()` (Task 2), `KeymapStore` defaults (Task 1), `textual.binding.BindingsMap`.
- Produces: a running app whose active bindings come from the resolver.

- [ ] **Step 1: Write the failing fidelity + nav tests**

Create `tests/integration/test_keybinding_wiring.py`:

```python
from __future__ import annotations

import pytest

from aws_tui.app import AwsTuiApp

# (key, action, show, priority) that MUST be installed under the default keymap.
# Mirrors the spec §5 table.
_EXPECTED: set[tuple[str, str, bool, bool]] = {
    ("q", "dispatch('app.quit')", True, False),
    ("ctrl+c", "dispatch('app.quit')", False, False),
    ("tab", "dispatch('pane.switch_focus')", True, True),
    ("shift+tab", "dispatch('pane.switch_focus_back')", False, True),
    ("up", "dispatch('pane.move_up')", False, True),
    ("k", "dispatch('pane.move_up')", False, True),
    ("down", "dispatch('pane.move_down')", False, True),
    ("j", "dispatch('pane.move_down')", False, True),
    ("enter", "dispatch('pane.descend')", True, True),
    ("backspace", "dispatch('pane.ascend')", True, True),
    ("left", "dispatch('pane.modal_left')", False, True),
    ("right", "dispatch('pane.modal_right')", False, True),
    ("r", "dispatch('pane.refresh')", True, True),
    ("?", "dispatch('app.help')", True, True),
    (":", "dispatch('app.help')", False, True),
    ("t", "dispatch('app.themes')", True, True),
    ("T", "dispatch('app.cycle_theme')", True, True),
    (",", "dispatch('app.open_settings')", True, True),
    ("c", "dispatch('pane.copy')", True, True),
    ("d", "dispatch('pane.delete')", True, True),
    ("S", "dispatch('app.swap_source')", True, True),
    ("shift+up", "dispatch('pane.mark_up')", False, True),
    ("shift+down", "dispatch('pane.mark_down')", False, True),
}


def _installed(app: AwsTuiApp) -> set[tuple[str, str, bool, bool]]:
    out: set[tuple[str, str, bool, bool]] = set()
    for key, bindings in app._bindings.key_to_bindings.items():
        for b in bindings:
            out.add((key, b.action, b.show, b.priority))
    return out


def test_default_bindings_are_byte_identical() -> None:
    app = AwsTuiApp()
    assert _installed(app) == _EXPECTED


def test_no_handlerless_keys_bound() -> None:
    app = AwsTuiApp()
    keys = set(app._bindings.key_to_bindings)
    # deferred actions' keys must NOT be bound
    for k in ("space", "/", "v", "a", "m", "n", "ctrl+p", "ctrl+k"):
        assert k not in keys, f"{k} should be unbound (handlerless)"


def test_overlay_remaps_a_handled_action(tmp_path, monkeypatch) -> None:
    # Build an app whose KeymapStore carries an overlay {pane.copy: "y"}.
    from aws_tui.infra.keymap_store import KeymapStore
    from aws_tui.ui.actions import ActionRegistry
    from aws_tui.ui.bindings import BindingResolver
    keymap = KeymapStore(overlay={"pane.copy": "y"})
    actions = ActionRegistry()
    actions.register("pane.copy", lambda: None)
    resolver = BindingResolver(keymap=keymap, actions=actions)
    keys = {b.key for b in resolver.to_textual_bindings()}
    assert "y" in keys and "c" not in keys


@pytest.mark.asyncio
async def test_dispatch_invokes_registered_handler() -> None:
    app = AwsTuiApp()
    calls: list[str] = []
    app._actions.register("pane.copy", lambda: calls.append("copy"))
    app.action_dispatch("pane.copy")
    assert calls == ["copy"]
```

- [ ] **Step 2: Run to confirm failure**

Run: `uv run pytest tests/integration/test_keybinding_wiring.py -v`
Expected: FAIL (bindings still from the ClassVar; no `action_dispatch`).

- [ ] **Step 3: Register every handled action id → handler in `__init__`**

In `AwsTuiApp.__init__`, after the resolver is built (keep the existing `app.quit` → `self._handle_quit` registration) add the rest, mapping each id to its existing handler method (spec §5 table):

```python
self._actions.register("pane.switch_focus", self.action_switch_focus)
self._actions.register("pane.switch_focus_back", self.action_switch_focus_reverse)
self._actions.register("pane.move_up", self.action_move_up)
self._actions.register("pane.move_down", self.action_move_down)
self._actions.register("pane.descend", self.action_descend)
self._actions.register("pane.ascend", self.action_ascend)
self._actions.register("pane.modal_left", self.action_modal_left_or_ascend)
self._actions.register("pane.modal_right", self.action_modal_right)
self._actions.register("pane.refresh", self.action_refresh)
self._actions.register("app.help", self.action_help)
self._actions.register("app.themes", self.action_themes)
self._actions.register("app.cycle_theme", self.action_cycle_theme)
self._actions.register("app.open_settings", self.action_open_settings)
self._actions.register("pane.copy", self.action_copy)
self._actions.register("pane.delete", self.action_delete)
self._actions.register("app.swap_source", self.action_swap_source)
self._actions.register("pane.mark_up", self.action_mark_up)
self._actions.register("pane.mark_down", self.action_mark_down)
```

Then install the resolver's bindings (import `BindingsMap` at top of file):

```python
from textual.binding import BindingsMap  # top-of-file import
...
# After all handlers are registered:
self._bindings = BindingsMap(self._resolver.to_textual_bindings())
```

- [ ] **Step 4: Add the single dispatch action + drop the ClassVar**

Replace the hard-coded `BINDINGS` list (lines ~217-247) with:

```python
# Bindings are installed at runtime from BindingResolver.to_textual_bindings()
# (see __init__), so config.toml [keybindings] overlays take effect. The
# empty ClassVar keeps Textual's binding machinery happy before install.
BINDINGS: ClassVar[list[BindingType]] = []
```

Add the dispatch method (near the other `action_*` methods):

```python
def action_dispatch(self, action_id: str) -> None | Awaitable[None]:
    """Single entry point for resolver-materialized bindings.

    Textual calls this for every ``dispatch('<id>')`` binding; forward to
    the ActionRegistry, which holds the real handler. Returning the
    handler's awaitable (if any) lets Textual await async actions.
    """
    return self._actions.invoke(action_id)
```

Ensure `Awaitable` is imported (`from collections.abc import Awaitable`).

- [ ] **Step 5: Run the new tests + the touched unit suites**

Run: `uv run pytest tests/integration/test_keybinding_wiring.py tests/unit/ui/test_bindings.py tests/unit/infra/test_keymap_store.py tests/unit/ui/test_actions.py -v`
Expected: PASS.

- [ ] **Step 6: Nav-not-regressed pilot check**

Add to `tests/integration/test_keybinding_wiring.py`:

```python
@pytest.mark.asyncio
async def test_tab_and_arrows_still_drive_navigation() -> None:
    app = AwsTuiApp()
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        focused_before = app.focused
        await pilot.press("tab")
        await pilot.pause()
        # Tab is a priority binding -> App handler runs, focus moves.
        assert app.focused is not focused_before
```

Run: `uv run pytest tests/integration/test_keybinding_wiring.py -v`
Expected: PASS. (If Tab does nothing, priority isn't being honored — revisit Step 3 install.)

- [ ] **Step 7: Full regression sweep**

Run: `uv run pytest tests/unit -q` then `uv run pytest tests/integration -q`
Expected: PASS (no behavior regressions from dropping the ClassVar). Also `uv run ruff check src tests` and `uv run mypy src`.

- [ ] **Step 8: Commit**

```bash
git add src/aws_tui/app.py tests/integration/test_keybinding_wiring.py
git commit -m "feat(app): install BindingResolver bindings at runtime; overlays live"
```

---

## Self-Review notes (author)

- **Spec coverage:** Task 1 = §5 keymap edits; Task 2 = §2/§3/§4 resolver; Task 3 = §1/§6/§7 app wiring + backward-compat guarantee test. All spec sections mapped.
- **Behavior-change tests, not weakenings:** the rewritten `test_bindings.py` assertions (dispatch form, skip-unregistered, `:`→help) and the keymap test edits encode the approved spec's intentional changes — flag this to the reviewer so they are not read as weakened tests.
- **Type consistency:** handler methods per spec §5 verified to exist in `app.py` (`action_switch_focus_reverse`, `action_modal_left_or_ascend`, etc.). `app.quit` keeps its existing `self._handle_quit` registration.
- **Implementation risk resolved:** Textual 8.2.7 `BindingsMap(Iterable[Binding])` accepts the resolver's list directly; reassigning `self._bindings` post-`super().__init__()` (before the app runs) is the install point.
