"""Cross-platform config + cache directory resolution.

``platformdirs`` resolves the right native location per OS:

- macOS:    ``~/Library/Application Support/aws-tui`` / ``~/Library/Caches/aws-tui``
- Linux:    ``~/.config/aws-tui`` / ``~/.cache/aws-tui`` (XDG, with ``$XDG_*``
            env vars honoured)
- Windows:  ``%APPDATA%\\aws-tui`` / ``%LOCALAPPDATA%\\aws-tui\\Cache``

To avoid silent data loss for existing macOS/Linux users who have a legacy
``~/.config/aws-tui`` or ``~/.cache/aws-tui`` populated from earlier
releases, we prefer the legacy XDG location when it already exists on disk.
The native ``platformdirs`` location is only used if the legacy directory
is absent. Tests can override both via the keyword arguments on
``config_home`` / ``cache_home``.
"""

from __future__ import annotations

from pathlib import Path

from platformdirs import user_cache_dir, user_config_dir

_APP_NAME = "aws-tui"


def _legacy_xdg_config() -> Path:
    """The pre-platformdirs config location, kept as a fallback so existing
    macOS/Linux users don't suddenly find an empty config directory after
    an upgrade."""
    return Path.home() / ".config" / _APP_NAME


def _legacy_xdg_cache() -> Path:
    return Path.home() / ".cache" / _APP_NAME


def config_home() -> Path:
    """Return the user-config directory for aws-tui.

    Prefers an existing legacy ``~/.config/aws-tui`` (macOS/Linux upgrade
    path), otherwise falls back to the platform-native location resolved
    by ``platformdirs.user_config_dir``."""
    legacy = _legacy_xdg_config()
    if legacy.exists():
        return legacy
    return Path(user_config_dir(_APP_NAME, appauthor=False, roaming=True))


def cache_home() -> Path:
    """Return the user-cache directory for aws-tui (same legacy-first
    rules as :func:`config_home`)."""
    legacy = _legacy_xdg_cache()
    if legacy.exists():
        return legacy
    return Path(user_cache_dir(_APP_NAME, appauthor=False))


__all__ = ["cache_home", "config_home"]
