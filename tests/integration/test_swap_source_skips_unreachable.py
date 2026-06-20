"""Skipped-unreachable behavior on action_swap_source.

Pre-populate ctx.unreachable_connections with two of three configured
connection keys, call the candidate-building logic, verify the one
reachable entry survives and the two unreachable ones are excluded.

Note: this test exercises the _build_swap_candidates / _raise_skip_toast
helpers directly rather than going through app.run_test (which would mount
an actual S3 provider against localhost:9001 — an endpoint that isn't
running in the test environment).  Doing so via app.run_test would
auto-trigger Bug-2-fix behaviour and mark reachable-one as UNREACHABLE
too (correct behaviour for real usage, but incorrect for a test that
wants to pin "reachable-one IS reachable").
"""

from __future__ import annotations

from pathlib import Path

import pytest

from aws_tui.app import AwsTuiApp, _build_swap_candidates, _raise_skip_toast
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
    # observed offline by the hub-subscription path.
    ctx.unreachable_connections.add(("s3-compatible", "unreachable-one"))
    ctx.unreachable_connections.add(("s3-compatible", "unreachable-two"))

    # Exercise the filter logic directly — no app.run_test needed.
    candidates, skipped = _build_swap_candidates(ctx)
    names = [label for label, _ in candidates]
    assert "local" in names
    assert any("reachable-one" in n for n in names)
    assert not any("unreachable-one" in n for n in names)
    assert not any("unreachable-two" in n for n in names)
    assert {"unreachable-one", "unreachable-two"} == set(skipped)

    # Toast wiring: build a minimal app just to get the toast stack,
    # then call _raise_skip_toast and verify the shape.
    app = AwsTuiApp(ctx)
    toast_stack = ctx.root_vm.chrome.toast_stack
    before = len(toast_stack.toasts)

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
    del app  # no run_test so nothing to close
