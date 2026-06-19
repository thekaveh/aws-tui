"""Cross-platform config + cache dir resolution.

Locks in:
- When a legacy ``~/.config/aws-tui`` (or ``~/.cache/aws-tui``) exists, the
  resolver prefers it so existing macOS/Linux installations don't suddenly
  point at an empty platform-native directory after an upgrade.
- Otherwise the resolver hands back the ``platformdirs`` native path, which
  on Windows lives under ``%APPDATA%`` / ``%LOCALAPPDATA%`` and on macOS
  under ``~/Library/Application Support`` / ``~/Library/Caches``.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from aws_tui.infra import paths


def test_config_home_prefers_legacy_when_present(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    legacy = tmp_path / ".config" / "aws-tui"
    legacy.mkdir(parents=True)

    assert paths.config_home() == legacy


def test_cache_home_prefers_legacy_when_present(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    legacy = tmp_path / ".cache" / "aws-tui"
    legacy.mkdir(parents=True)

    assert paths.cache_home() == legacy


def test_config_home_falls_back_to_platformdirs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No legacy dir → resolve via platformdirs. We don't assert the exact
    path (it varies per OS / per platformdirs version) — only that the
    result is a Path under the temp home, not the legacy path."""
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    native = tmp_path / "platformdirs-config"
    native.mkdir()

    monkeypatch.setattr(
        paths,
        "user_config_dir",
        lambda *a, **kw: str(native),
    )

    resolved = paths.config_home()
    assert resolved == native
    assert "aws-tui" not in str(resolved.relative_to(tmp_path))  # not the legacy slot


def test_cache_home_falls_back_to_platformdirs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    native = tmp_path / "platformdirs-cache"
    native.mkdir()

    monkeypatch.setattr(
        paths,
        "user_cache_dir",
        lambda *a, **kw: str(native),
    )

    assert paths.cache_home() == native
