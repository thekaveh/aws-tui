"""Snapshot host for demo mode — boots the app with demo=True under
one theme so the snapshot tier captures the rail + S3 pane + banner
subtitle chip."""

from __future__ import annotations

import tempfile
from pathlib import Path

from aws_tui.app import AwsTuiApp
from aws_tui.composition import build_app_context


class DemoModeApp(AwsTuiApp):
    def __init__(self, *, theme: str) -> None:
        # snapshot tier reuses the per-theme theme-store: passing
        # the theme via env preserves the existing fixture style.
        tmpdir = Path(tempfile.mkdtemp(prefix="demo-snapshot-"))
        ctx = build_app_context(config_dir=tmpdir, cache_dir=tmpdir, demo=True)
        ctx.initial_theme = theme
        super().__init__(context=ctx)


__all__ = ["DemoModeApp"]
