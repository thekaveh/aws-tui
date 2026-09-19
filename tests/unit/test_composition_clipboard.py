from __future__ import annotations

from aws_tui.composition import AppContext, build_app_context
from aws_tui.infra.clipboard import ClipboardPort, InMemoryClipboard, NativeClipboard


def _dispose(ctx: AppContext) -> None:
    ctx.table_clipboard_vm.dispose()
    ctx.focus_coordinator.dispose()
    ctx.root_vm.dispose()


def test_build_app_context_wires_a_native_clipboard(tmp_path) -> None:
    ctx = build_app_context(
        config_dir=tmp_path / "config",
        cache_dir=tmp_path / "cache",
    )
    try:
        assert isinstance(ctx.clipboard, NativeClipboard)
        assert isinstance(ctx.clipboard, ClipboardPort)
    finally:
        _dispose(ctx)


def test_direct_app_context_falls_back_to_a_native_clipboard(tmp_path) -> None:
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
        table_clipboard_vm=built.table_clipboard_vm,
    )
    try:
        assert isinstance(fallback.clipboard, NativeClipboard)
    finally:
        _dispose(built)


def test_an_injected_clipboard_is_kept(tmp_path) -> None:
    fake = InMemoryClipboard()
    ctx = build_app_context(
        config_dir=tmp_path / "config",
        cache_dir=tmp_path / "cache",
    )
    try:
        injected = AppContext(
            root_vm=ctx.root_vm,
            registry=ctx.registry,
            config_store=ctx.config_store,
            log_sink=ctx.log_sink,
            keymap_store=ctx.keymap_store,
            theme_store=ctx.theme_store,
            connection_resolver=ctx.connection_resolver,
            aws_session=ctx.aws_session,
            transfers_vm=ctx.transfers_vm,
            confirm_vm=ctx.confirm_vm,
            quick_look_vm=ctx.quick_look_vm,
            command_palette_vm=ctx.command_palette_vm,
            transfer_journal=ctx.transfer_journal,
            hub=ctx.hub,
            dispatcher=ctx.dispatcher,
            initial_theme=ctx.initial_theme,
            s3_connections_vm=ctx.s3_connections_vm,
            focus_coordinator=ctx.focus_coordinator,
            table_clipboard_vm=ctx.table_clipboard_vm,
            clipboard=fake,
        )
        assert injected.clipboard is fake
    finally:
        _dispose(ctx)


def test_clipboard_is_not_a_disposable_of_the_unstarted_context(tmp_path) -> None:
    # It owns no resources, so it must stay out of close_unstarted and out
    # of the app's shutdown event list (tests/unit/test_app_sanity.py).
    ctx = build_app_context(
        config_dir=tmp_path / "config",
        cache_dir=tmp_path / "cache",
    )
    ctx.close_unstarted()
    assert not hasattr(ctx.clipboard, "dispose")
