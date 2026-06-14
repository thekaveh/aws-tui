"""Shared fixtures for E2E journeys."""

from __future__ import annotations

import contextlib
from collections.abc import Iterator
from pathlib import Path

import pytest

from aws_tui.composition import AppContext, build_app_context


@pytest.fixture
def app_context(tmp_path: Path) -> Iterator[AppContext]:
    """Build a fresh ``AppContext`` rooted at tmp dirs (no home pollution)."""
    cfg = tmp_path / "config"
    cache = tmp_path / "cache"
    cfg.mkdir(parents=True, exist_ok=True)
    cache.mkdir(parents=True, exist_ok=True)
    ctx = build_app_context(config_dir=cfg, cache_dir=cache)
    try:
        yield ctx
    finally:
        # Best-effort dispose.
        with contextlib.suppress(Exception):
            ctx.root_vm.dispose()
