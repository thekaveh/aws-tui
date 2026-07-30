from __future__ import annotations

from vmx.lifecycle.status import ConstructionStatus

from aws_tui.composition import build_app_context
from aws_tui.vm.table_clipboard_vm import TableClipboardVM


def test_build_app_context_constructs_app_lifetime_table_clipboard(tmp_path) -> None:
    ctx = build_app_context(
        config_dir=tmp_path / "config",
        cache_dir=tmp_path / "cache",
    )
    try:
        assert isinstance(ctx.table_clipboard_vm, TableClipboardVM)
        assert ctx.table_clipboard_vm.inner.status is ConstructionStatus.CONSTRUCTED
    finally:
        ctx.table_clipboard_vm.dispose()
        ctx.focus_coordinator.dispose()
        ctx.root_vm.dispose()
