"""Recovery semantics for the unreachable set.

When the user retries (r) a pane that was UNREACHABLE and the retry
succeeds (state transitions to IDLE / EMPTY), the connection is
removed from ctx.unreachable_connections and re-enters the swap-ring.

This test exercises the hook layer directly — the full
provider-retry path is exercised by separate unit/integration tests
on PaneVM. Here we only need to verify the hub-subscription
plumbing flips the set correctly.
"""

from __future__ import annotations

from pathlib import Path
from typing import cast

import pytest
from vmx import Message, MessageHub

from aws_tui.app import AwsTuiApp, _build_swap_candidates
from aws_tui.composition import build_app_context


def _hub(ctx) -> MessageHub[Message]:
    return cast("MessageHub[Message]", ctx.hub)


@pytest.mark.asyncio
async def test_pane_state_transition_marks_and_unmarks_unreachable(
    tmp_path: Path,
) -> None:
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "config.toml").write_text(
        "[connections.target]\n"
        'kind = "s3-compatible"\n'
        'endpoint_url = "http://localhost:9999"\n'
        'credentials = "static"\n'
        'access_key_id = "k"\n'
        'secret_access_key = "s"\n'
    )
    ctx = build_app_context(config_dir=config_dir, cache_dir=tmp_path / "cache")
    app = AwsTuiApp(ctx)

    async with app.run_test(size=(120, 30)) as pilot:
        await pilot.pause()
        await pilot.pause()

        # Drive the marking/unmarking via the same public seam the hub
        # subscriber uses. (The hub subscriber is internal; we exercise
        # it through the small attribution helpers AwsTuiApp exposes
        # for testing.)
        app._mark_connection_unreachable("s3-compatible", "target")
        assert ("s3-compatible", "target") in ctx.unreachable_connections
        candidates, skipped = _build_swap_candidates(ctx)
        assert "target" in skipped
        assert not any("target" in label for label, _ in candidates)

        # Now simulate the recovery: pane transitions FROM UNREACHABLE.
        app._clear_connection_unreachable("s3-compatible", "target")
        assert ("s3-compatible", "target") not in ctx.unreachable_connections
        candidates, skipped = _build_swap_candidates(ctx)
        assert "target" not in skipped
        assert any("target" in label for label, _ in candidates)

    ctx.transfers_vm.dispose()
    ctx.confirm_vm.dispose()
    ctx.quick_look_vm.dispose()
    ctx.command_palette_vm.dispose()
    ctx.root_vm.dispose()
