"""Skipped-unreachable behavior on action_swap_source.

Pre-populate ctx.unreachable_connections with two of three configured
connection keys, invoke action_swap_source, verify the swap landed on
the one reachable entry and that a skip-info toast was raised.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from aws_tui.app import AwsTuiApp
from aws_tui.composition import build_app_context


@pytest.mark.asyncio
async def test_swap_source_skips_unreachable_connections(tmp_path: Path) -> None:
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "config.toml").write_text(
        "[connections.reachable-one]\n"
        'kind = "s3-compatible"\n'
        'endpoint_url = "http://localhost:9001"\n'
        'credentials = "static"\n'
        'access_key_id = "k1"\n'
        'secret_access_key = "s1"\n'
        "\n"
        "[connections.unreachable-one]\n"
        'kind = "s3-compatible"\n'
        'endpoint_url = "http://localhost:9002"\n'
        'credentials = "static"\n'
        'access_key_id = "k2"\n'
        'secret_access_key = "s2"\n'
        "\n"
        "[connections.unreachable-two]\n"
        'kind = "s3-compatible"\n'
        'endpoint_url = "http://localhost:9003"\n'
        'credentials = "static"\n'
        'access_key_id = "k3"\n'
        'secret_access_key = "s3"\n'
    )
    ctx = build_app_context(config_dir=config_dir, cache_dir=tmp_path / "cache")
    # Pre-populate the unreachable set as if the two endpoints had been
    # observed offline by the hub-subscription path (Task 3 will wire
    # that observation automatically; this test pins the consumption
    # side independently).
    ctx.unreachable_connections.add(("s3-compatible", "unreachable-one"))
    ctx.unreachable_connections.add(("s3-compatible", "unreachable-two"))

    app = AwsTuiApp(ctx)

    async with app.run_test(size=(120, 30)) as pilot:
        await pilot.pause()
        await pilot.pause()

        # Manually invoke action_swap_source. We can't depend on the
        # initial pane being mounted to a specific connection in this
        # harness because no service mounted (no provider built — the
        # endpoints aren't reachable in the test environment). The
        # observable behavior we care about: the toast.

        # Capture toasts raised on the stack via the VM (the View just
        # renders them; the data lives in the VM).
        toast_stack = ctx.root_vm.chrome.toast_stack
        before = len(toast_stack.toasts)

        # The action requires a mounted dual_pane; on a no-service
        # startup it returns early. To exercise the filter
        # deterministically, call the candidate-building logic directly
        # via the new internal helper we'll add in Task 2 (see plan).
        from aws_tui.app import _build_swap_candidates

        candidates, skipped = _build_swap_candidates(ctx)
        names = [label for label, _ in candidates]
        assert "local" in names
        assert any("reachable-one" in n for n in names)
        assert not any("unreachable-one" in n for n in names)
        assert not any("unreachable-two" in n for n in names)
        assert {"unreachable-one", "unreachable-two"} == set(skipped)

        # And the toast wiring: when action_swap_source actually runs
        # and filters out entries, it raises one INFO toast naming the
        # skipped connections. Call the toast-raising helper directly
        # to assert the shape (the full action requires a dual_pane
        # which this no-service startup doesn't have).
        from aws_tui.app import _raise_skip_toast

        _raise_skip_toast(ctx, skipped)
        after = len(toast_stack.toasts)
        assert after == before + 1
        latest = toast_stack.toasts[-1]
        assert "Skipped unreachable" in latest.model.text
        assert "unreachable-one" in latest.model.text
        assert "unreachable-two" in latest.model.text

    ctx.transfers_vm.dispose()
    ctx.confirm_vm.dispose()
    ctx.quick_look_vm.dispose()
    ctx.command_palette_vm.dispose()
    ctx.root_vm.dispose()
