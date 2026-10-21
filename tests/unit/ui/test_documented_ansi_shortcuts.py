"""Migration examples must survive the supported ANSI parser boundary."""

from __future__ import annotations

import pytest
from textual._xterm_parser import XTermParser
from textual.events import Key

from aws_tui.infra.keymap_store import KeymapStore
from aws_tui.ui.actions import ActionRegistry
from aws_tui.ui.bindings import BindingResolver
from tests.keybinding_examples_support import documented_migration_key, traditional_ctrl_byte

_MIGRATION_ACTIONS = (
    "pane.object_details",
    "emr.logs.source",
    "emr.logs.follow",
    "app.transfer_history",
)


@pytest.mark.parametrize("action", _MIGRATION_ACTIONS)
def test_documented_migration_byte_reaches_the_configured_action(action):
    key = documented_migration_key(action)
    parser = XTermParser()
    tokens = list(parser.feed(traditional_ctrl_byte(key))) + list(parser.feed(""))
    delivered = [token.key for token in tokens if isinstance(token, Key)]
    assert delivered == [key]
    registry = ActionRegistry()
    registry.register(action, lambda: None)
    registry.register("pane.switch_focus", lambda: None)
    keymap = KeymapStore(overlay={action: key})
    bindings = BindingResolver(keymap=keymap, actions=registry).to_textual_bindings()
    assert [binding.action for binding in bindings if binding.key == delivered[0]] == [
        f"dispatch({action!r})"
    ]
    assert keymap.resolve("pane.switch_focus") == ("tab",)


def test_distinct_migration_examples_can_be_combined_with_the_copy_example():
    overlay = {action: documented_migration_key(action) for action in _MIGRATION_ACTIONS}
    overlay["pane.copy"] = "ctrl+y"
    store = KeymapStore(overlay=overlay)
    assert len(set(overlay.values())) == len(overlay)
    assert store.resolve("pane.switch_focus") == ("tab",)


@pytest.mark.parametrize(
    ("configured", "delivered"),
    [("ctrl+i", "tab"), ("ctrl+h", "backspace"), ("ctrl+m", "enter")],
)
def test_token_acceptance_does_not_claim_traditional_byte_distinction(configured, delivered):
    # The store has no negotiated-protocol input; enhanced protocols can
    # distinguish these keys. Document its boundary instead of banning them.
    assert KeymapStore(overlay={"pane.object_details": configured}).resolve(
        "pane.object_details"
    ) == (configured,)
    parser = XTermParser()
    tokens = list(parser.feed(traditional_ctrl_byte(configured))) + list(parser.feed(""))
    assert [token.key for token in tokens if isinstance(token, Key)] == [delivered]
