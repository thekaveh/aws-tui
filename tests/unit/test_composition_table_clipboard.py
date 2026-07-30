from __future__ import annotations

from vmx.lifecycle.status import ConstructionStatus

from aws_tui.composition import AppContext, build_app_context
from aws_tui.vm.table_clipboard_vm import TableClipboardVM


def test_build_app_context_constructs_app_lifetime_table_clipboard(tmp_path) -> None:
    ctx = build_app_context(
        config_dir=tmp_path / "config",
        cache_dir=tmp_path / "cache",
    )
    try:
        assert isinstance(ctx.table_clipboard_vm, TableClipboardVM)
        assert ctx.table_clipboard_vm.status is ConstructionStatus.CONSTRUCTED
    finally:
        ctx.table_clipboard_vm.dispose()
        ctx.focus_coordinator.dispose()
        ctx.root_vm.dispose()


def test_direct_app_context_fallback_constructs_table_clipboard(tmp_path) -> None:
    built = build_app_context(
        config_dir=tmp_path / "config",
        cache_dir=tmp_path / "cache",
    )
    fallback = AppContext(
        root_vm=built.root_vm,
        registry=built.registry,
        config_store=built.config_store,
        log_sink=built.log_sink,
        keymap_store=built.keymap_store,
        theme_store=built.theme_store,
        connection_resolver=built.connection_resolver,
        aws_session=built.aws_session,
        transfers_vm=built.transfers_vm,
        confirm_vm=built.confirm_vm,
        quick_look_vm=built.quick_look_vm,
        command_palette_vm=built.command_palette_vm,
        transfer_journal=built.transfer_journal,
        hub=built.hub,
        dispatcher=built.dispatcher,
        initial_theme=built.initial_theme,
        s3_connections_vm=built.s3_connections_vm,
        focus_coordinator=built.focus_coordinator,
    )
    try:
        assert isinstance(fallback.table_clipboard_vm, TableClipboardVM)
        assert fallback.table_clipboard_vm.status is ConstructionStatus.CONSTRUCTED
    finally:
        fallback.table_clipboard_vm.dispose()
        built.table_clipboard_vm.dispose()
        built.focus_coordinator.dispose()
        built.root_vm.dispose()
