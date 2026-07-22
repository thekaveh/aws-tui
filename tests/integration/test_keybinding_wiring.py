"""Keystone wiring: the App installs BindingResolver-materialized bindings.

Guards that routing bindings through KeymapStore + BindingResolver +
ActionRegistry keeps default behavior byte-identical to the previous
hard-coded ``BINDINGS`` (same keys, same dispatch target, same priority),
while ``[keybindings]`` overlays now take effect.
"""

from __future__ import annotations

import pytest

from aws_tui.app import AwsTuiApp

# The full set the App must install under the default keymap: our 23
# resolver-materialized bindings (dispatch form, Textual key names) plus
# Textual's built-in ctrl+q (alt-quit) and ctrl+p (command palette) that
# survive super().__init__(). (key, action, show, priority).
_EXPECTED: set[tuple[str, str, bool, bool]] = {
    ("q", "dispatch('app.quit')", True, False),
    ("ctrl+c", "dispatch('app.quit')", False, False),
    ("tab", "dispatch('pane.switch_focus')", True, True),
    ("shift+tab", "dispatch('pane.switch_focus_back')", False, True),
    ("up", "dispatch('pane.move_up')", True, True),
    ("k", "dispatch('pane.move_up')", False, True),
    ("down", "dispatch('pane.move_down')", True, True),
    ("j", "dispatch('pane.move_down')", False, True),
    ("enter", "dispatch('pane.descend')", True, True),
    ("backspace", "dispatch('pane.ascend')", True, True),
    ("left", "dispatch('pane.modal_left')", False, True),
    ("right", "dispatch('pane.modal_right')", False, True),
    ("r", "dispatch('pane.refresh')", True, True),
    ("question_mark", "dispatch('app.help')", True, True),
    ("colon", "dispatch('app.command_palette')", True, True),
    ("ctrl+k", "dispatch('app.command_palette')", False, True),
    ("t", "dispatch('app.themes')", True, True),
    ("T", "dispatch('app.cycle_theme')", True, True),
    ("comma", "dispatch('app.open_settings')", True, True),
    ("c", "dispatch('pane.copy')", True, True),
    ("d", "dispatch('pane.delete')", True, True),
    ("S", "dispatch('app.swap_source')", True, True),
    ("shift+up", "dispatch('pane.mark_up')", False, True),
    ("shift+down", "dispatch('pane.mark_down')", False, True),
    ("space", "dispatch('pane.quick_look')", False, True),
    ("ctrl+q", "quit", False, True),
    ("ctrl+p", "command_palette", False, True),
}


def _installed(app: AwsTuiApp) -> set[tuple[str, str, bool, bool]]:
    out: set[tuple[str, str, bool, bool]] = set()
    for key, binds in app._bindings.key_to_bindings.items():
        for b in binds:
            out.add((key, b.action, b.show, b.priority))
    return out


def test_default_bindings_are_byte_identical(app_context_factory) -> None:  # type: ignore[no-untyped-def]
    app = AwsTuiApp(app_context_factory())
    assert _installed(app) == _EXPECTED


def test_no_handlerless_keys_bound(app_context_factory) -> None:  # type: ignore[no-untyped-def]
    app = AwsTuiApp(app_context_factory())
    keys = set(app._bindings.key_to_bindings)
    # Deferred (handlerless) actions' keys must NOT be bound: filter (slash),
    # enter_multiselect (v), select_all/authenticate (a), move (m), new (n).
    # (`space`->quick_look and `:`/`ctrl+k`->command_palette are now wired.)
    for k in ("slash", "v", "a", "m", "n"):
        assert k not in keys, f"{k} should be unbound (handlerless)"


def test_dispatch_invokes_registered_handler(app_context_factory) -> None:  # type: ignore[no-untyped-def]
    app = AwsTuiApp(app_context_factory())
    calls: list[str] = []
    app._actions.register("pane.copy", lambda: calls.append("copy"))
    app.action_dispatch("pane.copy")
    assert calls == ["copy"]


def test_overlay_remaps_a_handled_action() -> None:
    from aws_tui.infra.keymap_store import KeymapStore
    from aws_tui.ui.actions import ActionRegistry
    from aws_tui.ui.bindings import BindingResolver

    keymap = KeymapStore(overlay={"pane.copy": "y"})
    actions = ActionRegistry()
    actions.register("pane.copy", lambda: None)
    resolver = BindingResolver(keymap=keymap, actions=actions)
    keys = {b.key for b in resolver.to_textual_bindings()}
    assert "y" in keys
    assert "c" not in keys


@pytest.mark.asyncio
async def test_priority_tab_binding_fires_at_runtime(app_context_factory) -> None:  # type: ignore[no-untyped-def]
    # The "Tab does nothing" regression: without priority, Textual's Screen
    # consumes tab for focus traversal before the App binding fires. Pressing
    # tab must reach our dispatch -> switch_focus handler, proving the priority
    # binding is installed AND honored at runtime.
    app = AwsTuiApp(app_context_factory())
    calls: list[str] = []
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        app._actions.register("pane.switch_focus", lambda: calls.append("tab"))
        await pilot.press("tab")
        await pilot.pause()
    assert calls == ["tab"]
