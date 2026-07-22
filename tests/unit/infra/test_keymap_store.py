"""Unit tests for KeymapStore."""

from __future__ import annotations

import pytest

from aws_tui.infra.keymap_store import KeymapStore, UnknownAction


class TestDefaults:
    def test_resolve_quit_default(self) -> None:
        store = KeymapStore()
        assert store.resolve("app.quit") == ("q", "ctrl+c")

    def test_resolve_command_palette_default(self) -> None:
        store = KeymapStore()
        # ":" is aliased to help until the command palette is wired; the
        # palette keeps only ctrl+k for now.
        assert store.resolve("app.command_palette") == ("ctrl+k",)

    def test_vi_navigation_defaults_match_live_app_bindings(self) -> None:
        store = KeymapStore()
        assert store.resolve("pane.move_up") == ("up", "k")
        assert store.resolve("pane.move_down") == ("down", "j")

    def test_default_bindings_reproduce_runtime_keys(self) -> None:
        d = KeymapStore().all()
        assert d["app.help"] == ("?", ":")
        assert d["app.command_palette"] == ("ctrl+k",)  # ":" moved to help
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
        store = KeymapStore(overlay={"pane.copy": ["c", "y"]})
        assert store.resolve("pane.copy") == ("c", "y")

    def test_overlay_does_not_add_unknown_actions(self) -> None:
        # The overlay can override existing actions but adding wholly
        # new ones is rejected, since they wouldn't be bound to any
        # command anywhere in the app.
        with pytest.raises(UnknownAction):
            KeymapStore(overlay={"bogus.action": "x"})

    def test_unrelated_defaults_untouched_by_overlay(self) -> None:
        store = KeymapStore(overlay={"app.quit": "ctrl+d"})
        assert store.resolve("pane.copy") == ("c",)
        assert store.resolve("app.command_palette") == ("ctrl+k",)

    def test_all_includes_overlay_overrides(self) -> None:
        store = KeymapStore(overlay={"app.quit": "ctrl+d"})
        all_bindings = store.all()
        assert all_bindings["app.quit"] == ("ctrl+d",)
        # Other actions still have their defaults.
        assert all_bindings["pane.copy"] == ("c",)
