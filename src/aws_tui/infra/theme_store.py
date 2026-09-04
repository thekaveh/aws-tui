"""Theme storage and discovery.

Active theme content is the concatenation of four layers (later wins):

1. The built-in ``<name>.tcss`` shipped with the package under
   ``src/aws_tui/ui/themes/``. The built-in set is defined by
   :attr:`ThemeStore.BUILTIN_NAMES`.
2. The built-in operational pane hierarchy, appended in the same source so
   it can use the built-in theme tokens.
3. A user-defined ``<config-dir>/themes/<name>.tcss`` that
   completely replaces the built-in if present.
4. A user overlay ``<config-dir>/theme.tcss`` appended on top of
   whichever theme is active.
"""

from __future__ import annotations

import os
import stat
from importlib import resources
from pathlib import Path
from typing import ClassVar

_HAS_DIR_FD = os.open in os.supports_dir_fd and hasattr(os, "O_NOFOLLOW")


class ThemeNotFound(Exception):
    """Raised when ``load`` is asked for a theme name that doesn't exist."""


class _UnsafeThemeFile(Exception):
    """A configured theme path was present but unsafe or unreadable."""


def _default_user_themes_dir() -> Path:
    from aws_tui.infra.paths import config_home

    return config_home() / "themes"


def _default_user_overlay() -> Path:
    from aws_tui.infra.paths import config_home

    return config_home() / "theme.tcss"


class ThemeStore:
    """Layered theme loader for Textual ``.tcss`` content."""

    DEFAULT_NAME: ClassVar[str] = "carbon"
    BUILTIN_NAMES: ClassVar[tuple[str, ...]] = (
        # Original four (dark themes).
        "carbon",
        "voidline",
        "lattice",
        "amber",
        # Three light themes.
        "solarized-light",
        "github-light",
        "one-light",
        # Three additional dark themes.
        "nord",
        "dracula",
        "gruvbox-dark",
    )

    def __init__(
        self,
        *,
        user_themes_dir: Path | None = None,
        user_overlay: Path | None = None,
    ) -> None:
        self._user_themes_dir: Path = (
            user_themes_dir if user_themes_dir is not None else _default_user_themes_dir()
        )
        self._user_overlay: Path = (
            user_overlay if user_overlay is not None else _default_user_overlay()
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def list_themes(self) -> list[str]:
        """Return all available theme names (built-ins + user themes), deduplicated."""
        seen: set[str] = set()
        ordered: list[str] = []
        for name in self.BUILTIN_NAMES:
            if name not in seen:
                ordered.append(name)
                seen.add(name)
        try:
            candidates = (
                sorted(self._user_themes_dir.glob("*.tcss"))
                if self._user_themes_dir.is_dir()
                else ()
            )
        except OSError:
            candidates = ()
        for path in candidates:
            name = path.stem
            if name not in seen and self._read_user_theme(name) is not None:
                ordered.append(name)
                seen.add(name)
        return ordered

    def exists(self, name: str) -> bool:
        """Return True if ``name`` resolves to a known built-in or user theme."""
        if name in self.BUILTIN_NAMES:
            return True
        return self._read_user_theme(name) is not None

    def load(self, name: str) -> str:
        """Return the concatenated ``.tcss`` content for ``name``.

        Raises :class:`ThemeNotFound` if neither a built-in nor a user
        theme with that name exists.
        """
        user_theme = self._read_user_theme(name)
        if user_theme is not None:
            base = user_theme
        elif name in self.BUILTIN_NAMES:
            base = self.load_builtin(name)
        else:
            raise ThemeNotFound(name)

        try:
            overlay_text = _read_regular_file(
                self._user_overlay.parent,
                self._user_overlay.name,
            )
        except _UnsafeThemeFile as exc:
            raise ThemeNotFound(f"{name}: unsafe user overlay") from exc
        if overlay_text is not None:
            if base and not base.endswith("\n"):
                base += "\n"
            return base + overlay_text
        return base

    def load_builtin(self, name: str) -> str:
        """Load packaged built-in CSS without user replacement or overlay.

        This is the known-good startup fallback path. It deliberately composes
        the raw built-in with the shared operational pane layer while bypassing
        every user-controlled theme file.
        """
        if name not in self.BUILTIN_NAMES:
            raise ThemeNotFound(name)
        base = self._read_builtin(name)
        if base and not base.endswith("\n"):
            base += "\n"
        return base + self._read_builtin("operational-panes")

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _read_user_theme(self, name: str) -> str | None:
        """Read one direct regular theme file from its validated descriptor."""
        filename = _theme_filename(name)
        if filename is None:
            return None
        try:
            return _read_regular_file(self._user_themes_dir, filename)
        except _UnsafeThemeFile:
            return None

    @staticmethod
    def _read_builtin(name: str) -> str:
        """Read a packaged built-in ``.tcss`` via importlib.resources."""
        try:
            return (
                resources.files("aws_tui.ui.themes")
                .joinpath(f"{name}.tcss")
                .read_text(encoding="utf-8")
            )
        except (FileNotFoundError, ModuleNotFoundError) as exc:
            raise ThemeNotFound(name) from exc


def _theme_filename(name: str) -> str | None:
    """Return a direct child filename, rejecting traversal on every platform."""
    if not name or name in {".", ".."} or any(char in name for char in ("/", "\\", ":", "\0")):
        return None
    return f"{name}.tcss"


def _read_regular_file(directory: Path, filename: str) -> str | None:
    """Atomically read ``filename`` as a direct regular UTF-8 file.

    The symlink guard applies to the LEAF only: ``filename`` itself must not be
    a symlink, so a theme file cannot resolve outside the directory and inline
    its target into the stylesheet. ``directory`` IS resolved through symlinks —
    it is the caller's config home, and refusing a symlinked config directory
    rejects the ordinary dotfiles layout.

    POSIX opens ``filename`` relative to a ``directory`` descriptor with
    ``O_NOFOLLOW``. Platforms without ``dir_fd`` support use one descriptor plus
    before/opened/after identity checks. Missing files are optional; present but
    unsafe, unreadable, or invalid UTF-8 files raise ``_UnsafeThemeFile``.
    """
    try:
        if _HAS_DIR_FD:
            return _read_regular_file_at(directory, filename)
        return _read_regular_file_portable(directory, filename)
    except FileNotFoundError:
        return None
    except (OSError, UnicodeError, ValueError) as exc:
        raise _UnsafeThemeFile(filename) from exc


def _read_regular_file_at(directory: Path, filename: str) -> str:
    # No ``O_NOFOLLOW`` on the directory: the guard exists to stop the theme
    # *file* being a symlink to somewhere else, and the caller passes the config
    # home itself. Refusing a symlinked config directory rejects the ordinary
    # dotfiles layout and made every theme, built-ins included, fail to load.
    directory_flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
    directory_flags |= getattr(os, "O_DIRECTORY", 0)
    file_flags = (
        os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NONBLOCK", 0) | os.O_NOFOLLOW
    )
    directory_fd = os.open(directory, directory_flags)
    try:
        file_fd = os.open(filename, file_flags, dir_fd=directory_fd)
        try:
            if not stat.S_ISREG(os.fstat(file_fd).st_mode):
                raise _UnsafeThemeFile(filename)
            with os.fdopen(file_fd, "r", encoding="utf-8") as stream:
                file_fd = -1
                return stream.read()
        finally:
            if file_fd >= 0:
                os.close(file_fd)
    finally:
        os.close(directory_fd)


def _read_regular_file_portable(directory: Path, filename: str) -> str:
    """Descriptor-identity fallback for platforms without ``dir_fd``."""
    candidate = directory / filename
    # The directory is resolved through symlinks (see ``_read_regular_file_at``);
    # only the leaf theme file must not be one.
    directory_before = directory.stat()
    candidate_before = candidate.stat(follow_symlinks=False)
    if not stat.S_ISDIR(directory_before.st_mode) or not stat.S_ISREG(candidate_before.st_mode):
        raise _UnsafeThemeFile(filename)

    file_fd = os.open(candidate, os.O_RDONLY | getattr(os, "O_CLOEXEC", 0))
    try:
        opened = os.fstat(file_fd)
        directory_after = directory.stat()
        candidate_after = candidate.stat(follow_symlinks=False)
        identities = (
            _file_identity(directory_before) == _file_identity(directory_after),
            _file_identity(candidate_before) == _file_identity(opened),
            _file_identity(opened) == _file_identity(candidate_after),
        )
        if not stat.S_ISREG(opened.st_mode) or not all(identities):
            raise _UnsafeThemeFile(filename)
        with os.fdopen(file_fd, "r", encoding="utf-8") as stream:
            file_fd = -1
            return stream.read()
    finally:
        if file_fd >= 0:
            os.close(file_fd)


def _file_identity(result: os.stat_result) -> tuple[int, int]:
    return result.st_dev, result.st_ino


__all__ = ["ThemeNotFound", "ThemeStore"]
