"""Theme storage and discovery.

Active theme content is the concatenation of four layers (later wins):

1. The built-in ``<name>.tcss`` shipped with the package under
   ``src/aws_tui/ui/themes/``. The built-in set is defined by
   :attr:`ThemeStore.BUILTIN_NAMES`.
2. The built-in operational pane hierarchy, appended in the same source so
   it can use the built-in theme tokens.
3. A user-defined ``~/.config/aws-tui/themes/<name>.tcss`` that
   completely replaces the built-in if present.
4. A user overlay ``~/.config/aws-tui/theme.tcss`` appended on top of
   whichever theme is active.
"""

from __future__ import annotations

from importlib import resources
from pathlib import Path
from typing import ClassVar


class ThemeNotFound(Exception):
    """Raised when ``load`` is asked for a theme name that doesn't exist."""


def _default_user_themes_dir() -> Path:
    from aws_tui.infra.paths import config_home

    return config_home() / "themes"


def _default_user_overlay() -> Path:
    from aws_tui.infra.paths import config_home

    return config_home() / "theme.tcss"


class ThemeStore:
    """Layered theme loader for Textual ``.tcss`` content."""

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
            if name not in seen and self._resolve_user_theme(name) is not None:
                ordered.append(name)
                seen.add(name)
        return ordered

    def exists(self, name: str) -> bool:
        """Return True if ``name`` resolves to a known built-in or user theme."""
        if name in self.BUILTIN_NAMES:
            return True
        return self._resolve_user_theme(name) is not None

    def load(self, name: str) -> str:
        """Return the concatenated ``.tcss`` content for ``name``.

        Raises :class:`ThemeNotFound` if neither a built-in nor a user
        theme with that name exists.
        """
        user_path = self._resolve_user_theme(name)
        if user_path is not None:
            try:
                base = user_path.read_text(encoding="utf-8")
            except (OSError, UnicodeError) as exc:
                raise ThemeNotFound(name) from exc
        elif name in self.BUILTIN_NAMES:
            base = self._read_builtin(name)
            if base and not base.endswith("\n"):
                base += "\n"
            base += self._read_builtin("operational-panes")
        else:
            raise ThemeNotFound(name)

        if self._user_overlay.is_file():
            overlay_text = self._user_overlay.read_text(encoding="utf-8")
            if base and not base.endswith("\n"):
                base += "\n"
            return base + overlay_text
        return base

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _user_theme_path(self, name: str) -> Path:
        return self._user_themes_dir / f"{name}.tcss"

    def _resolve_user_theme(self, name: str) -> Path | None:
        """Return a readable, regular user-theme path contained by its root."""
        try:
            themes_root = self._user_themes_dir.resolve(strict=True)
            resolved = self._user_theme_path(name).resolve(strict=True)
            resolved.relative_to(themes_root)
            if not resolved.is_file():
                return None
            with resolved.open("r", encoding="utf-8"):
                pass
        except (OSError, ValueError):
            return None
        return resolved

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


__all__ = ["ThemeNotFound", "ThemeStore"]
