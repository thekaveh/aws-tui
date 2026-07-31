"""``build_app_context`` honours ``[defaults].theme`` from ``config.toml``.

Regression guard: the composition root used to hard-code
``initial_theme="carbon"`` and never consulted ``ConfigStore.load()``,
so a user's configured theme was silently ignored on every launch.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from aws_tui.composition import build_app_context
from aws_tui.infra.keymap_store import KeymapStore


def _write_config(config_dir: Path, body: str) -> None:
    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / "config.toml").write_text(body, encoding="utf-8")


def test_initial_theme_falls_back_to_carbon_with_no_config(tmp_path: Path) -> None:
    cfg = tmp_path / "config"
    cache = tmp_path / "cache"
    cfg.mkdir()
    cache.mkdir()
    ctx = build_app_context(config_dir=cfg, cache_dir=cache)
    try:
        assert ctx.initial_theme == "carbon"
    finally:
        ctx.root_vm.dispose()


def test_initial_theme_honours_defaults_theme_from_config(tmp_path: Path) -> None:
    cfg = tmp_path / "config"
    cache = tmp_path / "cache"
    _write_config(
        cfg,
        '[defaults]\ntheme = "voidline"\n',
    )
    cache.mkdir()
    ctx = build_app_context(config_dir=cfg, cache_dir=cache)
    try:
        assert ctx.initial_theme == "voidline"
    finally:
        ctx.root_vm.dispose()


def test_keybinding_overlay_becomes_the_runtime_keymap(tmp_path: Path) -> None:
    cfg = tmp_path / "config"
    cache = tmp_path / "cache"
    _write_config(
        cfg,
        '[keybindings]\n"pane.delete" = "x"\n',
    )
    cache.mkdir()
    ctx = build_app_context(config_dir=cfg, cache_dir=cache)
    try:
        assert ctx.keymap_store.resolve("pane.delete") == ("x",)
        assert ctx.root_vm.chrome.hint_legend._keymap is ctx.keymap_store
    finally:
        ctx.root_vm.dispose()


def test_empty_keybinding_overlay_disables_the_runtime_binding(tmp_path: Path) -> None:
    cfg = tmp_path / "config"
    cache = tmp_path / "cache"
    _write_config(
        cfg,
        '[keybindings]\n"pane.delete" = []\n',
    )
    cache.mkdir()
    ctx = build_app_context(config_dir=cfg, cache_dir=cache)
    try:
        assert ctx.keymap_store.resolve("pane.delete") == ()
    finally:
        ctx.root_vm.dispose()


def test_keybinding_collision_logs_clear_error_and_falls_back(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg = tmp_path / "config"
    cache = tmp_path / "cache"
    _write_config(
        cfg,
        '[keybindings]\n"pane.copy" = "y"\n',
    )
    cache.mkdir()
    warnings: list[tuple[str, dict[str, str]]] = []
    monkeypatch.setattr(
        "aws_tui.composition._logger.warning",
        lambda event, *, extra: warnings.append((event, extra)),
    )

    ctx = build_app_context(config_dir=cfg, cache_dir=cache)
    try:
        assert ctx.keymap_store.resolve("pane.copy") == ("c",)
        assert ctx.keymap_store.all() == KeymapStore().all()
        collision_errors = [
            extra["error"]
            for event, extra in warnings
            if event == "composition.keymap_overlay.invalid"
        ]
        assert any(
            "'y'" in error and "'glue.copy_table_ref'" in error and "'pane.copy'" in error
            for error in collision_errors
        )
    finally:
        ctx.root_vm.dispose()


def test_unknown_keybinding_action_logs_and_falls_back_atomically(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg = tmp_path / "config"
    cache = tmp_path / "cache"
    _write_config(
        cfg,
        '[keybindings]\n"pane.delete" = "x"\n"unknown.action" = "z"\n',
    )
    cache.mkdir()
    warnings: list[tuple[str, dict[str, str]]] = []
    monkeypatch.setattr(
        "aws_tui.composition._logger.warning",
        lambda event, *, extra: warnings.append((event, extra)),
    )

    ctx = build_app_context(config_dir=cfg, cache_dir=cache)
    try:
        assert ctx.keymap_store.all() == KeymapStore().all()
        assert warnings[-1][1]["error_type"] == "UnknownAction"
    finally:
        ctx.root_vm.dispose()


def test_initial_theme_falls_back_to_carbon_on_broken_config(tmp_path: Path) -> None:
    cfg = tmp_path / "config"
    cache = tmp_path / "cache"
    _write_config(cfg, "this is = not valid toml [[[")
    cache.mkdir()
    ctx = build_app_context(config_dir=cfg, cache_dir=cache)
    try:
        assert ctx.initial_theme == "carbon"
    finally:
        ctx.root_vm.dispose()
