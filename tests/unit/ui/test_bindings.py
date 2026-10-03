"""Unit tests for :mod:`aws_tui.ui.bindings`."""

from __future__ import annotations

import pytest

from aws_tui.infra.keymap_store import KeymapStore, textual_key_name
from aws_tui.ui.actions import ActionRegistry
from aws_tui.ui.bindings import BindingResolver, _binding_priority


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


def test_source_and_emr_application_bindings_have_distinct_descriptions() -> None:
    actions = _registry("app.swap_source", "emr.next_application")
    resolver = BindingResolver(keymap=KeymapStore(), actions=actions)
    by_key = {binding.key: binding for binding in resolver.to_textual_bindings()}

    assert by_key["S"].description == "Switch source"
    assert by_key["A"].description == "Next EMR application"


def test_glue_query_handoff_has_a_dedicated_binding_description() -> None:
    resolver = BindingResolver(
        keymap=KeymapStore(),
        actions=_registry("glue.query_in_athena"),
    )
    by_key = {binding.key: binding for binding in resolver.to_textual_bindings()}

    assert by_key["Q"].action == "dispatch('glue.query_in_athena')"
    assert by_key["Q"].description == "Open selected Glue table in Athena"


def test_priority_true_except_quit() -> None:
    actions = _registry(
        "app.quit",
        "pane.switch_focus",
        "pane.modal_left",
        "pane.modal_right",
    )
    resolver = BindingResolver(keymap=KeymapStore(), actions=actions)
    by_key = {b.key: b for b in resolver.to_textual_bindings()}
    assert by_key["q"].priority is False
    assert by_key["tab"].priority is True
    assert by_key["left"].priority is False
    assert by_key["right"].priority is False


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


def test_punctuation_keys_translate_to_textual_names() -> None:
    # ":" -> colon, "," -> comma, "?" -> question_mark (a literal Binding(",")
    # is invalid in Textual, which splits on comma).
    actions = _registry("app.command_palette", "app.help", "app.open_settings")
    resolver = BindingResolver(keymap=KeymapStore(), actions=actions)
    keys = {b.key for b in resolver.to_textual_bindings()}
    assert "colon" in keys  # ":" (app.command_palette) -> colon
    assert ":" not in keys
    assert "question_mark" in keys
    assert "?" not in keys
    assert "comma" in keys
    assert "," not in keys


def test_overlay_keymap_reflects_in_bindings() -> None:
    keymap = KeymapStore(overlay={"app.quit": "X"})
    resolver = BindingResolver(keymap=keymap, actions=_registry("app.quit"))
    quit_bindings = [b for b in resolver.to_textual_bindings() if b.key == "X"]
    # Single key now since overlay replaces wholesale.
    assert len(quit_bindings) == 1
    assert quit_bindings[0].action == "dispatch('app.quit')"


def test_duplicate_overlay_key_is_rejected_before_binding_materialization() -> None:
    with pytest.raises(
        ValueError,
        match=r"'y'.*'glue\.copy_table_ref'.*'pane\.copy'",
    ):
        KeymapStore(overlay={"pane.copy": "y"})


@pytest.mark.parametrize(
    ("configured", "equivalent"),
    [("Space", "space"), ("SPACE", "space"), ("Space", " "), ("Slash", "/")],
)
def test_priority_follows_the_runtime_key_not_the_configured_spelling(
    configured: str, equivalent: str
) -> None:
    """Two spellings of one key must not get opposite priority.

    ``Binding(key=...)`` folds the configured literal via ``textual_key_name``
    while ``_binding_priority`` used to receive the raw token, so ``"Space"``
    and ``"space"`` bound the same runtime key with opposite priority. A
    priority App binding is consulted before the focused widget, so the
    capitalised spelling swallowed the space bar before any editable widget —
    the pane filter, the Athena editor, the settings form — could see it.
    """
    assert textual_key_name(configured) == textual_key_name(equivalent)
    assert _binding_priority("pane.toggle_select", configured) == _binding_priority(
        "pane.toggle_select", equivalent
    )


def test_bare_printable_keys_never_take_priority_over_editable_widgets() -> None:
    for key in ("space", " ", "/", "k", "K", "7", "?"):
        assert not _binding_priority("pane.toggle_select", key), key
    for key in ("ctrl+k", "Ctrl+K", "escape", "f1", "enter", "backspace"):
        assert _binding_priority("pane.toggle_select", key), key


def test_all_selection_ids_emit_individually_and_exit_yields_to_editor() -> None:
    ids = (
        "pane.enter_multiselect",
        "pane.toggle_select",
        "pane.select_all",
        "pane.clear_selection",
        "pane.exit_multiselect",
    )
    resolver = BindingResolver(
        keymap=KeymapStore(), actions=_registry(*ids, "pane.quick_look", "auth.authenticate")
    )
    bindings = {binding.action: binding for binding in resolver.to_textual_bindings()}
    for action_id, key in zip(ids, ("v", "space", "a", "u", "ctrl+v"), strict=True):
        action = (
            f"dispatch({action_id!r}, {key!r})"
            if action_id in {"pane.toggle_select", "pane.select_all"}
            else f"dispatch({action_id!r})"
        )
        assert bindings[action].key == key
    assert bindings["dispatch('pane.clear_selection')"].description == "Clear selection"
    assert bindings["dispatch('pane.exit_multiselect')"].description == "Exit multi-select"
    assert not bindings["dispatch('pane.exit_multiselect')"].priority
    assert (
        bindings["dispatch('pane.toggle_select', 'space')"].key
        == bindings["dispatch('pane.quick_look', 'space')"].key
    )


def test_contextual_bindings_serialize_each_actual_normalized_key() -> None:
    from textual.actions import parse

    keymap = KeymapStore(
        overlay={
            "pane.quick_look": ["Space", "x"],
            "auth.authenticate": ["a", "b"],
            "emr.cancel": "z",
        }
    )
    ids = ("pane.quick_look", "pane.toggle_select", "auth.authenticate", "pane.select_all")
    bindings = BindingResolver(keymap=keymap, actions=_registry(*ids)).to_textual_bindings()
    assert len(bindings) == 6
    assert {(binding.key, parse(binding.action)[1:]) for binding in bindings} == {
        ("space", ("dispatch", ("pane.quick_look", "space"))),
        ("x", ("dispatch", ("pane.quick_look", "x"))),
        ("space", ("dispatch", ("pane.toggle_select", "space"))),
        ("a", ("dispatch", ("auth.authenticate", "a"))),
        ("b", ("dispatch", ("auth.authenticate", "b"))),
        ("a", ("dispatch", ("pane.select_all", "a"))),
    }


@pytest.mark.parametrize(
    "action_id", ["pane.filter", "pane.fuzzy_find", "pane.sort", "pane.clear_filter"]
)
def test_listing_controls_modified_remaps_are_non_priority(action_id: str) -> None:
    assert not _binding_priority(action_id, "ctrl+u")
    if action_id in KeymapStore.DEFAULT_BINDINGS:
        resolver = BindingResolver(
            keymap=KeymapStore(overlay={action_id: "ctrl+u"}), actions=_registry(action_id)
        )
        bindings = resolver.to_textual_bindings()
        assert len(bindings) == 1
        assert bindings[0].key == "ctrl+u"
        assert not bindings[0].priority


def test_emr_cancel_is_printable_non_priority_and_remappable() -> None:
    for overlay, key in ((None, "x"), ({"emr.cancel": "z"}, "z")):
        resolver = BindingResolver(
            keymap=KeymapStore(overlay=overlay), actions=_registry("emr.cancel")
        )
        (binding,) = resolver.to_textual_bindings()
        assert binding.key == key
        assert not binding.priority
        assert binding.description == "Cancel selected EMR job run"
