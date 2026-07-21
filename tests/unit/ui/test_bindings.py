"""Unit tests for :mod:`aws_tui.ui.bindings`."""

from __future__ import annotations

from aws_tui.infra.keymap_store import KeymapStore
from aws_tui.ui.actions import ActionRegistry
from aws_tui.ui.bindings import BindingResolver


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


def test_first_key_visible_secondary_hidden() -> None:
    # Byte-identical to the live BINDINGS: move_up is a visible action, so its
    # first key shows and the vi-alias is hidden; a non-visible action's keys
    # stay hidden entirely.
    actions = _registry("app.quit", "pane.move_up", "pane.switch_focus_back")
    resolver = BindingResolver(keymap=KeymapStore(), actions=actions)
    by_key = {b.key: b for b in resolver.to_textual_bindings()}
    assert by_key["q"].show is True  # visible action, first key
    assert by_key["ctrl+c"].show is False  # secondary key of a visible action
    assert by_key["up"].show is True  # move_up is visible (matches live app)
    assert by_key["k"].show is False  # secondary (vi alias) hidden
    assert by_key["shift+tab"].show is False  # switch_focus_back not visible


def test_overlay_keymap_reflects_in_bindings() -> None:
    keymap = KeymapStore(overlay={"app.quit": "Q"})
    resolver = BindingResolver(keymap=keymap, actions=_registry("app.quit"))
    quit_bindings = [b for b in resolver.to_textual_bindings() if b.key == "Q"]
    # Single key now since overlay replaces wholesale.
    assert len(quit_bindings) == 1
    assert quit_bindings[0].action == "dispatch('app.quit')"


def test_resolve_action_id_roundtrip() -> None:
    resolver = BindingResolver(keymap=KeymapStore(), actions=ActionRegistry())
    assert resolver.resolve_action_id("q") == "app.quit"
    assert resolver.resolve_action_id(":") == "app.help"  # ":" now aliases help
    assert resolver.resolve_action_id("nope-no-such-key") is None


def test_keys_for_returns_tuple() -> None:
    resolver = BindingResolver(keymap=KeymapStore(), actions=ActionRegistry())
    assert resolver.keys_for("app.quit") == ("q", "ctrl+c")
    assert resolver.keys_for("does.not.exist") == ()
