"""Unit tests for ThemeStore."""

from __future__ import annotations

from pathlib import Path

import pytest

from aws_tui.infra.theme_store import ThemeNotFound, ThemeStore


class TestListThemes:
    def test_lists_at_least_four_builtins(self, tmp_path: Path) -> None:
        store = ThemeStore(user_themes_dir=tmp_path / "themes")
        listed = store.list_themes()
        for name in ThemeStore.BUILTIN_NAMES:
            assert name in listed

    def test_includes_user_themes(self, tmp_path: Path) -> None:
        user_themes = tmp_path / "themes"
        user_themes.mkdir()
        (user_themes / "mytheme.tcss").write_text("/* mine */", encoding="utf-8")
        store = ThemeStore(user_themes_dir=user_themes)
        listed = store.list_themes()
        assert "mytheme" in listed

    def test_no_duplicates_when_user_shadows_builtin(self, tmp_path: Path) -> None:
        user_themes = tmp_path / "themes"
        user_themes.mkdir()
        (user_themes / "carbon.tcss").write_text("/* override */", encoding="utf-8")
        store = ThemeStore(user_themes_dir=user_themes)
        listed = store.list_themes()
        assert listed.count("carbon") == 1


class TestExists:
    def test_existing_builtin(self, tmp_path: Path) -> None:
        store = ThemeStore(user_themes_dir=tmp_path / "themes")
        assert store.exists("carbon") is True

    def test_nonexistent(self, tmp_path: Path) -> None:
        store = ThemeStore(user_themes_dir=tmp_path / "themes")
        assert store.exists("nope") is False

    def test_user_theme(self, tmp_path: Path) -> None:
        user_themes = tmp_path / "themes"
        user_themes.mkdir()
        (user_themes / "custom.tcss").write_text("/* x */", encoding="utf-8")
        store = ThemeStore(user_themes_dir=user_themes)
        assert store.exists("custom") is True


class TestLoad:
    def test_load_builtin_returns_content(self, tmp_path: Path) -> None:
        store = ThemeStore(user_themes_dir=tmp_path / "themes")
        content = store.load("carbon")
        # Empty/placeholder builtin is fine; we just want a string back.
        assert isinstance(content, str)

    def test_load_unknown_raises(self, tmp_path: Path) -> None:
        store = ThemeStore(user_themes_dir=tmp_path / "themes")
        with pytest.raises(ThemeNotFound):
            store.load("nope")

    def test_user_theme_wins_over_builtin(self, tmp_path: Path) -> None:
        user_themes = tmp_path / "themes"
        user_themes.mkdir()
        (user_themes / "carbon.tcss").write_text("/* user override */", encoding="utf-8")
        store = ThemeStore(user_themes_dir=user_themes)
        content = store.load("carbon")
        assert "user override" in content

    def test_overlay_is_appended(self, tmp_path: Path) -> None:
        overlay = tmp_path / "theme.tcss"
        overlay.write_text("/* the overlay */", encoding="utf-8")
        store = ThemeStore(
            user_themes_dir=tmp_path / "themes",
            user_overlay=overlay,
        )
        content = store.load("carbon")
        assert "the overlay" in content

    def test_overlay_applied_to_user_theme(self, tmp_path: Path) -> None:
        user_themes = tmp_path / "themes"
        user_themes.mkdir()
        (user_themes / "mine.tcss").write_text("/* base */", encoding="utf-8")
        overlay = tmp_path / "theme.tcss"
        overlay.write_text("/* on top */", encoding="utf-8")
        store = ThemeStore(user_themes_dir=user_themes, user_overlay=overlay)
        content = store.load("mine")
        assert "base" in content
        assert "on top" in content
        assert content.index("base") < content.index("on top")
