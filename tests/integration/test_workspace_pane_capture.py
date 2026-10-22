"""Mounted source wiring evidence; workspace manager/Open acceptance is separate."""

from dataclasses import replace

import pytest

from aws_tui.app import AwsTuiApp
from aws_tui.composition import build_app_context
from aws_tui.domain.navigation_workspace import FileLocation, NavigationTarget
from aws_tui.vm.workspace_panes import capture_pane_target


@pytest.mark.asyncio
async def test_app_rebind_keeps_both_pane_regions_independent_of_root(tmp_path):
    context = build_app_context(
        demo=True, config_dir=tmp_path / "config", cache_dir=tmp_path / "cache"
    )
    app = AwsTuiApp(context)
    try:
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            dual = app._dual_pane()
            assert dual is not None
            initial = context.root_vm.active_connection
            assert initial is not None
            assert capture_pane_target(dual.left).region == initial.region
            other = replace(initial, name="other-pane", region="eu-west-1")
            await app._rebind_pane_to_connection(dual.right, other)
            assert capture_pane_target(dual.right) == NavigationTarget(
                "s3", "other-pane", "eu-west-1", location=FileLocation(s3=())
            )
            assert capture_pane_target(dual.left).connection == initial.name
            assert capture_pane_target(dual.left).region == initial.region
            assert context.root_vm.active_connection == initial
            await app._rebind_pane_to_local(dual.right)
            assert capture_pane_target(dual.right).connection is None
            assert dual.right.current_connection_region is None
    finally:
        context.root_vm.dispose()
        context.log_sink.close()
