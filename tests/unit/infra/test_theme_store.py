"""Unit tests for ThemeStore."""

from __future__ import annotations

import os
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

    def test_outside_symlink_is_rejected_consistently(self, tmp_path: Path) -> None:
        user_themes = tmp_path / "themes"
        user_themes.mkdir()
        outside = tmp_path / "outside.tcss"
        outside.write_text("/* outside */", encoding="utf-8")
        (user_themes / "escaped.tcss").symlink_to(outside)
        store = ThemeStore(user_themes_dir=user_themes)

        assert "escaped" not in store.list_themes()
        assert store.exists("escaped") is False
        with pytest.raises(ThemeNotFound):
            store.load("escaped")

    def test_dangling_symlink_is_rejected_consistently(self, tmp_path: Path) -> None:
        user_themes = tmp_path / "themes"
        user_themes.mkdir()
        (user_themes / "dangling.tcss").symlink_to(user_themes / "missing.tcss")
        store = ThemeStore(user_themes_dir=user_themes)

        assert "dangling" not in store.list_themes()
        assert store.exists("dangling") is False
        with pytest.raises(ThemeNotFound):
            store.load("dangling")

    def test_tcss_directory_is_rejected_consistently(self, tmp_path: Path) -> None:
        user_themes = tmp_path / "themes"
        user_themes.mkdir()
        (user_themes / "directory.tcss").mkdir()
        store = ThemeStore(user_themes_dir=user_themes)

        assert "directory" not in store.list_themes()
        assert store.exists("directory") is False
        with pytest.raises(ThemeNotFound):
            store.load("directory")

    def test_regular_file_is_available_consistently(self, tmp_path: Path) -> None:
        user_themes = tmp_path / "themes"
        user_themes.mkdir()
        (user_themes / "midnight.tcss").write_text("/* midnight */", encoding="utf-8")
        store = ThemeStore(user_themes_dir=user_themes)

        assert store.list_themes() == [*ThemeStore.BUILTIN_NAMES, "midnight"]
        assert store.exists("midnight") is True
        assert store.load("midnight") == "/* midnight */"

    def test_in_root_symlink_is_rejected_consistently(self, tmp_path: Path) -> None:
        user_themes = tmp_path / "themes"
        user_themes.mkdir()
        target = user_themes / "shared"
        target.write_text("/* shared */", encoding="utf-8")
        (user_themes / "linked.tcss").symlink_to(target)
        store = ThemeStore(user_themes_dir=user_themes)

        assert "linked" not in store.list_themes()
        assert store.exists("linked") is False
        with pytest.raises(ThemeNotFound):
            store.load("linked")

    def test_invalid_utf8_theme_is_rejected_consistently(self, tmp_path: Path) -> None:
        user_themes = tmp_path / "themes"
        user_themes.mkdir()
        (user_themes / "binary.tcss").write_bytes(b"\xff\xfe")
        store = ThemeStore(user_themes_dir=user_themes)

        assert "binary" not in store.list_themes()
        assert store.exists("binary") is False
        with pytest.raises(ThemeNotFound):
            store.load("binary")


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

    @pytest.mark.parametrize(
        "name",
        ["../escape", "folder/name", "folder\\name", "drive:stream", "nul\0name"],
    )
    def test_invalid_cross_platform_name_is_rejected(self, tmp_path: Path, name: str) -> None:
        store = ThemeStore(user_themes_dir=tmp_path / "themes")

        assert store.exists(name) is False
        with pytest.raises(ThemeNotFound):
            store.load(name)


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

    def test_builtin_base_operational_css_and_overlay_are_ordered(
        self,
        tmp_path: Path,
    ) -> None:
        overlay = tmp_path / "theme.tcss"
        overlay.write_text("/* user overlay */", encoding="utf-8")
        store = ThemeStore(
            user_themes_dir=tmp_path / "themes",
            user_overlay=overlay,
        )

        content = store.load("carbon")

        assert (
            content.index("$bg:")
            < content.index("ServiceTabStrip > .service-tab")
            < content.index("user overlay")
        )

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

    def test_user_theme_does_not_append_builtin_operational_css(
        self,
        tmp_path: Path,
    ) -> None:
        user_themes = tmp_path / "themes"
        user_themes.mkdir()
        (user_themes / "carbon.tcss").write_text("/* replacement */", encoding="utf-8")
        store = ThemeStore(user_themes_dir=user_themes)

        assert "ServiceTabStrip > .service-tab" not in store.load("carbon")

    def test_builtin_load_bypasses_replacement_and_overlay(self, tmp_path: Path) -> None:
        user_themes = tmp_path / "themes"
        user_themes.mkdir()
        (user_themes / "carbon.tcss").write_text("/* replacement */", encoding="utf-8")
        overlay = tmp_path / "theme.tcss"
        overlay.write_text("/* user-overlay-marker */", encoding="utf-8")
        store = ThemeStore(user_themes_dir=user_themes, user_overlay=overlay)

        content = store.load_builtin("carbon")

        assert "replacement" not in content
        assert "user-overlay-marker" not in content
        assert "ServiceTabStrip > .service-tab" in content

    @pytest.mark.parametrize("inside_root", [False, True])
    def test_overlay_symlink_is_rejected(self, tmp_path: Path, inside_root: bool) -> None:
        target_root = tmp_path if inside_root else tmp_path.parent
        target = target_root / f"overlay-target-{tmp_path.name}.tcss"
        target.write_text("/* linked overlay */", encoding="utf-8")
        overlay = tmp_path / "theme.tcss"
        overlay.symlink_to(target)
        store = ThemeStore(user_themes_dir=tmp_path / "themes", user_overlay=overlay)

        try:
            with pytest.raises(ThemeNotFound):
                store.load("carbon")
        finally:
            if not inside_root:
                target.unlink()

    def test_dangling_overlay_symlink_is_rejected(self, tmp_path: Path) -> None:
        overlay = tmp_path / "theme.tcss"
        overlay.symlink_to(tmp_path / "missing.tcss")
        store = ThemeStore(user_themes_dir=tmp_path / "themes", user_overlay=overlay)

        with pytest.raises(ThemeNotFound):
            store.load("carbon")

    def test_overlay_directory_is_rejected(self, tmp_path: Path) -> None:
        overlay = tmp_path / "theme.tcss"
        overlay.mkdir()
        store = ThemeStore(user_themes_dir=tmp_path / "themes", user_overlay=overlay)

        with pytest.raises(ThemeNotFound):
            store.load("carbon")

    def test_symlinked_config_directory_still_loads_themes(self, tmp_path: Path) -> None:
        """A symlinked config home is the ordinary dotfiles layout, not an attack.

        The symlink guard protects the theme *file*; applying it to the parent
        directory as well made every theme — built-ins included — fail with
        "unsafe user overlay" whenever ``~/.config/aws-tui`` was a symlink.
        """
        real = tmp_path / "real"
        real.mkdir()
        link = tmp_path / "link"
        link.symlink_to(real, target_is_directory=True)
        (real / "theme.tcss").write_text("Screen { background: #123456; }", encoding="utf-8")
        store = ThemeStore(user_themes_dir=tmp_path / "themes", user_overlay=link / "theme.tcss")

        assert "#123456" in store.load("carbon")

    def test_invalid_utf8_overlay_is_rejected(self, tmp_path: Path) -> None:
        overlay = tmp_path / "theme.tcss"
        overlay.write_bytes(b"\xff\xfe")
        store = ThemeStore(user_themes_dir=tmp_path / "themes", user_overlay=overlay)

        with pytest.raises(ThemeNotFound):
            store.load("carbon")

    def test_theme_is_not_reopened_after_validation(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        user_themes = tmp_path / "themes"
        user_themes.mkdir()
        theme = user_themes / "race.tcss"
        theme.write_text("/* original */", encoding="utf-8")
        outside = tmp_path / "outside.tcss"
        outside.write_text("/* outside */", encoding="utf-8")
        store = ThemeStore(user_themes_dir=user_themes)
        original_read_text = Path.read_text

        def replace_before_reopen(path: Path, *args: object, **kwargs: object) -> str:
            if path == theme.resolve():
                theme.unlink()
                theme.symlink_to(outside)
            return original_read_text(path, *args, **kwargs)

        monkeypatch.setattr(Path, "read_text", replace_before_reopen)

        assert store.load("race") == "/* original */"

    def test_regular_theme_read_uses_validated_descriptor(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        if os.open not in os.supports_dir_fd:
            pytest.skip("dir_fd is not available on this platform")
        user_themes = tmp_path / "themes"
        user_themes.mkdir()
        (user_themes / "direct.tcss").write_text("/* direct */", encoding="utf-8")
        store = ThemeStore(user_themes_dir=user_themes)
        opened: list[tuple[object, int | None]] = []
        original_open = os.open

        def recording_open(
            path: os.PathLike[str] | str,
            flags: int,
            mode: int = 0o777,
            *,
            dir_fd: int | None = None,
        ) -> int:
            opened.append((path, dir_fd))
            return original_open(path, flags, mode, dir_fd=dir_fd)

        monkeypatch.setattr(os, "open", recording_open)

        assert store.load("direct") == "/* direct */"
        assert any(path == "direct.tcss" and dir_fd is not None for path, dir_fd in opened)

    def test_symlink_replacement_during_descriptor_open_is_rejected(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        if os.open not in os.supports_dir_fd:
            pytest.skip("dir_fd is not available on this platform")
        user_themes = tmp_path / "themes"
        user_themes.mkdir()
        theme = user_themes / "race.tcss"
        theme.write_text("/* original */", encoding="utf-8")
        outside = tmp_path / "outside.tcss"
        outside.write_text("/* outside */", encoding="utf-8")
        store = ThemeStore(user_themes_dir=user_themes)
        original_open = os.open
        replaced = False

        def replace_during_open(
            path: os.PathLike[str] | str,
            flags: int,
            mode: int = 0o777,
            *,
            dir_fd: int | None = None,
        ) -> int:
            nonlocal replaced
            if path == "race.tcss" and dir_fd is not None and not replaced:
                theme.unlink()
                theme.symlink_to(outside)
                replaced = True
            return original_open(path, flags, mode, dir_fd=dir_fd)

        monkeypatch.setattr(os, "open", replace_during_open)

        with pytest.raises(ThemeNotFound):
            store.load("race")
        assert replaced is True
