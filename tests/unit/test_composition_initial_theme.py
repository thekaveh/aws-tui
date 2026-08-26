"""``build_app_context`` honours ``[defaults].theme`` from ``config.toml``.

Regression guard: the composition root used to hard-code
``initial_theme="carbon"`` and never consulted ``ConfigStore.load()``,
so a user's configured theme was silently ignored on every launch.
"""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from aws_tui.composition import build_app_context
from aws_tui.infra import log_sink as log_sink_module
from aws_tui.infra.keymap_store import KeymapStore
from aws_tui.vm.chrome.focus_coordinator_vm import FocusCoordinatorVM
from aws_tui.vm.table_clipboard_vm import TableClipboardVM


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
        ctx.close_unstarted()


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
        ctx.close_unstarted()


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
        ctx.close_unstarted()


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
        ctx.close_unstarted()


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
        ctx.close_unstarted()


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
        ctx.close_unstarted()


def test_malformed_keybinding_logs_and_falls_back_atomically(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg = tmp_path / "config"
    cache = tmp_path / "cache"
    _write_config(
        cfg,
        '[keybindings]\n"pane.delete" = "x,y"\n',
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
        assert warnings[-1][0] == "composition.keymap_overlay.invalid"
        assert warnings[-1][1]["error_type"] == "InvalidKeybinding"
    finally:
        ctx.close_unstarted()


def test_initial_theme_falls_back_to_carbon_on_broken_config(tmp_path: Path) -> None:
    cfg = tmp_path / "config"
    cache = tmp_path / "cache"
    _write_config(cfg, "this is = not valid toml [[[")
    cache.mkdir()
    ctx = build_app_context(config_dir=cfg, cache_dir=cache)
    try:
        assert ctx.initial_theme == "carbon"
    finally:
        ctx.close_unstarted()


def test_build_context_rolls_back_logging_and_constructed_vms(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    logger = logging.getLogger("aws_tui")
    baseline_handlers = tuple(logger.handlers)
    baseline_capture_count = log_sink_module._stdlib_capture_count
    disposed: list[str] = []
    original_focus_dispose = FocusCoordinatorVM.dispose
    original_clipboard_dispose = TableClipboardVM.dispose

    def dispose_focus(vm: FocusCoordinatorVM) -> None:
        disposed.append("focus")
        original_focus_dispose(vm)

    def dispose_clipboard(vm: TableClipboardVM) -> None:
        disposed.append("clipboard")
        original_clipboard_dispose(vm)

    def fail_clipboard_construct(_vm: TableClipboardVM) -> None:
        raise RuntimeError("clipboard construction failed")

    monkeypatch.setattr(FocusCoordinatorVM, "dispose", dispose_focus)
    monkeypatch.setattr(TableClipboardVM, "dispose", dispose_clipboard)
    monkeypatch.setattr(TableClipboardVM, "construct", fail_clipboard_construct)

    with pytest.raises(RuntimeError, match="clipboard construction failed"):
        build_app_context(
            config_dir=tmp_path / "config",
            cache_dir=tmp_path / "cache",
        )

    assert disposed == ["clipboard", "focus"]
    assert tuple(logger.handlers) == baseline_handlers
    assert log_sink_module._stdlib_capture_count == baseline_capture_count
