"""Deferred file operations must be available through the contextual registry."""

import pytest

from aws_tui.app import AwsTuiApp
from aws_tui.composition import build_app_context
from aws_tui.vm.chrome.action_catalog import ACTION_SPECS


@pytest.mark.parametrize("action", ["pane.move", "pane.new"])
def test_file_mutation_command_is_registered_and_cataloged(tmp_path, action):
    ctx = build_app_context(config_dir=tmp_path / "config", cache_dir=tmp_path / "cache", demo=True)
    try:
        app = AwsTuiApp(ctx)
        assert app._actions.has(action)
        spec = next(spec for spec in ACTION_SPECS if spec.id == action)
        assert spec.service_ids == frozenset({"s3"})
    finally:
        ctx.close_unstarted()
