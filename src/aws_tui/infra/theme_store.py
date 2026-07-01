"""Theme storage and discovery.

Active theme content is the concatenation of three layers (later wins):

1. The built-in ``<name>.tcss`` shipped with the package under
   ``src/aws_tui/ui/themes/``. The built-in set is defined by
   :attr:`ThemeStore.BUILTIN_NAMES`.
2. A user-defined ``~/.config/aws-tui/themes/<name>.tcss`` that
   completely replaces the built-in if present.
3. A user overlay ``~/.config/aws-tui/theme.tcss`` appended on top of
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
        if self._user_themes_dir.is_dir():
            for path in sorted(self._user_themes_dir.glob("*.tcss")):
                name = path.stem
                if name not in seen:
                    ordered.append(name)
                    seen.add(name)
        return ordered

    def exists(self, name: str) -> bool:
        """Return True if ``name`` resolves to a known built-in or user theme."""
        if name in self.BUILTIN_NAMES:
            return True
        return self._user_theme_path(name).is_file()

    def load(self, name: str) -> str:
        """Return the concatenated ``.tcss`` content for ``name``.

        Raises :class:`ThemeNotFound` if neither a built-in nor a user
        theme with that name exists.
        """
        user_path = self._user_theme_path(name)
        if user_path.is_file():
            # Refuse to follow a symlink that points outside the
            # user-themes directory — a malicious symlink at
            # ``~/.config/aws-tui/themes/foo.tcss → /etc/passwd``
            # would otherwise have its contents inlined into the
            # active stylesheet (and surface on screen, since
            # Textual will try to parse it as CSS). Local-only
            # threat model, but a TUI shouldn't open arbitrary
            # paths just because the symlink target is readable.
            try:
                resolved = user_path.resolve(strict=True)
                themes_root = self._user_themes_dir.resolve()
            except OSError as exc:  # pragma: no cover - extremely rare
                raise ThemeNotFound(name) from exc
            if not resolved.is_relative_to(themes_root):
                raise ThemeNotFound(f"{name}: resolves outside {themes_root}")
            base = resolved.read_text(encoding="utf-8")
        elif name in self.BUILTIN_NAMES:
            base = self._read_builtin(name)
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
