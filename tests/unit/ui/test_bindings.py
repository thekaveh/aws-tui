"""Unit tests for :mod:`aws_tui.ui.bindings`."""

from __future__ import annotations

from aws_tui.infra.keymap_store import KeymapStore
from aws_tui.ui.actions import ActionRegistry
from aws_tui.ui.bindings import BindingResolver


def test_to_textual_bindings_covers_every_keymap_entry() -> None:
    keymap = KeymapStore()
    actions = ActionRegistry()
    resolver = BindingResolver(keymap=keymap, actions=actions)

    bindings = resolver.to_textual_bindings()

    # Expect at least one Binding per action; multi-key actions emit more.
    total_keys = sum(len(keys) for keys in keymap.all().values())
    assert len(bindings) == total_keys


def test_to_textual_bindings_replaces_dots_with_underscores() -> None:
    keymap = KeymapStore()
    actions = ActionRegistry()
    resolver = BindingResolver(keymap=keymap, actions=actions)

    bindings = resolver.to_textual_bindings()
    quit_binding = next(b for b in bindings if b.key == "q")
    assert quit_binding.action == "app_quit"


def test_to_textual_bindings_makes_secondary_keys_hidden() -> None:
    keymap = KeymapStore()
    actions = ActionRegistry()
    resolver = BindingResolver(keymap=keymap, actions=actions)

    bindings = resolver.to_textual_bindings()
    # `app.quit` has ("q", "ctrl+c"); the second should be show=False.
    q_binding = next(b for b in bindings if b.key == "q")
    ctrl_c_binding = next(b for b in bindings if b.key == "ctrl+c")
    assert q_binding.show is True
    assert ctrl_c_binding.show is False


def test_to_textual_bindings_hides_cursor_chips() -> None:
    """Cursor moves are routed via the binding layer but never appear in the
    Textual footer chip — the bottom hint legend already shows them."""
    keymap = KeymapStore()
    actions = ActionRegistry()
    resolver = BindingResolver(keymap=keymap, actions=actions)
    bindings = resolver.to_textual_bindings()
    move_up_bindings = [b for b in bindings if b.action == "pane_move_up"]
    assert move_up_bindings  # exists
    assert all(b.show is False for b in move_up_bindings)


def test_resolve_action_id_roundtrip() -> None:
    keymap = KeymapStore()
    actions = ActionRegistry()
    resolver = BindingResolver(keymap=keymap, actions=actions)

    assert resolver.resolve_action_id("q") == "app.quit"
    assert resolver.resolve_action_id(":") == "app.command_palette"
    assert resolver.resolve_action_id("nope-no-such-key") is None


def test_keys_for_returns_tuple() -> None:
    keymap = KeymapStore()
    actions = ActionRegistry()
    resolver = BindingResolver(keymap=keymap, actions=actions)

    assert resolver.keys_for("app.quit") == ("q", "ctrl+c")
    assert resolver.keys_for("does.not.exist") == ()


def test_overlay_keymap_reflects_in_bindings() -> None:
    keymap = KeymapStore(overlay={"app.quit": "Q"})
    actions = ActionRegistry()
    resolver = BindingResolver(keymap=keymap, actions=actions)

    bindings = resolver.to_textual_bindings()
    quit_bindings = [b for b in bindings if b.action == "app_quit"]
    # Single key now since overlay replaces wholesale.
    assert len(quit_bindings) == 1
    assert quit_bindings[0].key == "Q"
