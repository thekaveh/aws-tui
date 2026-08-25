"""Unit tests for KeymapStore."""

from __future__ import annotations

import string
from itertools import combinations

import pytest
from textual.binding import BindingsMap

from aws_tui.infra.keymap_store import KeymapStore, UnknownAction, textual_key_name

_APPROVED_ALIAS_PAIRS = (
    ("pane.quick_look", "pane.toggle_select"),
    ("auth.authenticate", "pane.select_all"),
    ("emr.clone", "pane.copy"),
    ("athena.cancel", "modal.cancel"),
    ("athena.query", "glue.catalog"),
    ("athena.history", "glue.jobs"),
    ("athena.results", "glue.crawlers"),
)


class TestDefaults:
    def test_resolve_quit_default(self) -> None:
        store = KeymapStore()
        assert store.resolve("app.quit") == ("q", "ctrl+c")

    def test_resolve_command_palette_default(self) -> None:
        store = KeymapStore()
        assert store.resolve("app.command_palette") == (":", "ctrl+k")

    def test_emr_next_application_has_dedicated_binding(self) -> None:
        store = KeymapStore()
        assert store.resolve("app.swap_source") == ("S",)
        assert store.resolve("emr.next_application") == ("A",)

    def test_glue_views_have_dedicated_number_bindings(self) -> None:
        store = KeymapStore()
        assert store.resolve("glue.catalog") == ("1",)
        assert store.resolve("glue.jobs") == ("2",)
        assert store.resolve("glue.crawlers") == ("3",)
        assert store.resolve("glue.time_travel_in_athena") == ("V",)

    def test_glue_query_in_athena_has_a_dedicated_binding(self) -> None:
        store = KeymapStore()
        assert store.resolve("glue.query_in_athena") == ("Q",)

    def test_athena_controls_have_dedicated_bindings(self) -> None:
        store = KeymapStore()
        assert store.resolve("athena.query") == ("1",)
        assert store.resolve("athena.history") == ("2",)
        assert store.resolve("athena.results") == ("3",)
        assert store.resolve("athena.saved") == ("4",)
        assert store.resolve("athena.execute") == ("ctrl+enter",)
        assert store.resolve("athena.cancel") == ("escape",)

    def test_vi_navigation_defaults_match_live_app_bindings(self) -> None:
        store = KeymapStore()
        assert store.resolve("pane.move_up") == ("up", "k")
        assert store.resolve("pane.move_down") == ("down", "j")

    def test_default_bindings_reproduce_runtime_keys(self) -> None:
        d = KeymapStore().all()
        assert d["app.help"] == ("?",)
        assert d["app.command_palette"] == (":", "ctrl+k")  # ":" back on the palette
        assert d["pane.ascend"] == ("backspace",)  # "left" split out
        assert d["pane.modal_left"] == ("left",)
        assert d["pane.modal_right"] == ("right",)
        assert d["app.open_settings"] == (",",)
        assert d["pane.mark_up"] == ("shift+up",)
        assert d["pane.mark_down"] == ("shift+down",)

    def test_unknown_action_raises(self) -> None:
        store = KeymapStore()
        with pytest.raises(UnknownAction):
            store.resolve("nope.notreal")

    def test_all_returns_all_defaults(self) -> None:
        store = KeymapStore()
        all_bindings = store.all()
        # Spot-check a handful of known actions from spec §4.2.
        assert "app.quit" in all_bindings
        assert "pane.copy" in all_bindings
        assert "modal.cancel" in all_bindings
        # And the full set should match DEFAULT_BINDINGS exactly when
        # there's no overlay.
        assert set(all_bindings) == set(KeymapStore.DEFAULT_BINDINGS)


class TestOverlay:
    def test_overlay_single_key_replaces_defaults(self) -> None:
        store = KeymapStore(overlay={"app.quit": "ctrl+d"})
        assert store.resolve("app.quit") == ("ctrl+d",)

    def test_overlay_list_keys_replaces_defaults(self) -> None:
        store = KeymapStore(overlay={"pane.copy": ["c", "ctrl+y"]})
        assert store.resolve("pane.copy") == ("c", "ctrl+y")

    def test_approved_alias_allowlist_matches_all_default_collision_pairs(self) -> None:
        actions_by_key: dict[str, set[str]] = {}
        for action, keys in KeymapStore.DEFAULT_BINDINGS.items():
            for key in keys:
                actions_by_key.setdefault(textual_key_name(key), set()).add(action)
        default_pairs = frozenset(
            pair for actions in actions_by_key.values() for pair in combinations(sorted(actions), 2)
        )

        assert frozenset(_APPROVED_ALIAS_PAIRS) == default_pairs
        assert default_pairs == KeymapStore.APPROVED_ALIAS_PAIRS

    @pytest.mark.parametrize(("left", "right"), _APPROVED_ALIAS_PAIRS)
    def test_overlay_can_remap_approved_alias_pair_to_another_key(
        self,
        left: str,
        right: str,
    ) -> None:
        store = KeymapStore(overlay={left: "7", right: "7"})

        assert store.resolve(left) == ("7",)
        assert store.resolve(right) == ("7",)

    def test_overlay_rejects_textual_equivalent_key_names(self) -> None:
        with pytest.raises(
            ValueError,
            match=r"'colon'.*'app\.command_palette'.*'pane\.copy'",
        ):
            KeymapStore(overlay={"pane.copy": "colon"})

    def test_overlay_does_not_add_unknown_actions(self) -> None:
        # The overlay can override existing actions but adding wholly
        # new ones is rejected, since they wouldn't be bound to any
        # command anywhere in the app.
        with pytest.raises(UnknownAction):
            KeymapStore(overlay={"bogus.action": "x"})

    def test_unrelated_defaults_untouched_by_overlay(self) -> None:
        store = KeymapStore(overlay={"app.quit": "ctrl+d"})
        assert store.resolve("pane.copy") == ("c",)
        assert store.resolve("app.command_palette") == (":", "ctrl+k")

    def test_all_includes_overlay_overrides(self) -> None:
        store = KeymapStore(overlay={"app.quit": "ctrl+d"})
        all_bindings = store.all()
        assert all_bindings["app.quit"] == ("ctrl+d",)
        # Other actions still have their defaults.
        assert all_bindings["pane.copy"] == ("c",)


@pytest.mark.parametrize("key", string.punctuation)
def test_single_ascii_punctuation_matches_public_textual_normalization(key: str) -> None:
    if key == ",":
        assert textual_key_name(key) == "comma"
        assert "comma" in BindingsMap([("comma", "noop")]).key_to_bindings
        return

    runtime_key = next(iter(BindingsMap([(key, "noop")]).key_to_bindings))
    assert textual_key_name(key) == runtime_key
