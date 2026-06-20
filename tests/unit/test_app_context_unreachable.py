"""AppContext.unreachable_connections is a mutable set used by the
swap-source ring to skip connections observed unreachable. Default
empty; mutated at runtime by hub subscribers in AwsTuiApp.
"""

from __future__ import annotations

from aws_tui.composition import build_app_context


def test_app_context_unreachable_connections_defaults_empty(tmp_path) -> None:
    ctx = build_app_context(config_dir=tmp_path / "config", cache_dir=tmp_path / "cache")
    try:
        assert ctx.unreachable_connections == set()
        # Mutable: callers (AwsTuiApp) add/remove entries at runtime.
        ctx.unreachable_connections.add(("s3-compatible", "minio-local"))
        assert ("s3-compatible", "minio-local") in ctx.unreachable_connections
        ctx.unreachable_connections.discard(("s3-compatible", "minio-local"))
        assert ctx.unreachable_connections == set()
    finally:
        # Best-effort teardown — AppContext doesn't own dispose, but
        # we tear down the VMs we know about to avoid leaked subscriptions.
        ctx.transfers_vm.dispose()
        ctx.confirm_vm.dispose()
        ctx.quick_look_vm.dispose()
        ctx.command_palette_vm.dispose()
        ctx.root_vm.dispose()
