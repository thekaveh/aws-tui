from __future__ import annotations

from typing import cast

from aws_tui.composition import AppContext, build_app_context
from aws_tui.demo.in_memory_duckdb import InMemoryDuckDb as DemoDuckDb
from aws_tui.infra.duckdb import DuckDbOutcome, DuckDbPort, InMemoryDuckDb, NativeDuckDb
from aws_tui.services.glue.service import GlueService


def _dispose(ctx: AppContext) -> None:
    ctx.table_clipboard_vm.dispose()
    ctx.focus_coordinator.dispose()
    ctx.root_vm.dispose()


def test_build_app_context_wires_a_native_duckdb_port(tmp_path) -> None:
    ctx = build_app_context(
        config_dir=tmp_path / "config",
        cache_dir=tmp_path / "cache",
    )
    try:
        assert isinstance(ctx.duckdb, NativeDuckDb)
        assert isinstance(ctx.duckdb, DuckDbPort)
    finally:
        _dispose(ctx)


def test_demo_mode_does_not_wire_a_native_duckdb_port(tmp_path) -> None:
    # I4: with the extra installed, a real NativeDuckDb in --demo would run
    # INSTALL httpfs/aws/iceberg (a network fetch from extensions.duckdb.org)
    # and resolve credentials for the demo profile -- the only demo path that
    # would reach the outside world, breaking the no-real-AWS contract
    # test_demo_mode_boots_with_four_demo_connections relies on. Demo gets the
    # demo-layer fake instead, so Peek still shows plausible rows. It must be
    # that fake and not infra's test double, which is test-only by contract.
    ctx = build_app_context(
        config_dir=tmp_path / "config",
        cache_dir=tmp_path / "cache",
        demo=True,
    )
    try:
        assert not isinstance(ctx.duckdb, NativeDuckDb)
        assert isinstance(ctx.duckdb, DemoDuckDb)
        assert isinstance(ctx.duckdb, DuckDbPort)
        result = ctx.duckdb.query("SELECT 1", profile="demo", region="us-east-1")
        assert result.outcome is DuckDbOutcome.OK
        assert result.columns
        assert result.rows
        # A NULL in the demo page, so the pane's dimmed-NULL rendering is
        # visible in demo mode rather than only under test.
        assert any(value is None for row in result.rows for value in row)
    finally:
        _dispose(ctx)


def test_an_explicitly_injected_duckdb_port_wins_over_the_demo_default(tmp_path) -> None:
    # A test harness that injects its own fake wants exactly that fake, demo
    # or not -- only the *default* changes under demo=True.
    fake = InMemoryDuckDb()
    ctx = build_app_context(
        config_dir=tmp_path / "config",
        cache_dir=tmp_path / "cache",
        demo=True,
        duckdb_port=fake,
    )
    try:
        assert ctx.duckdb is fake
    finally:
        _dispose(ctx)


def test_direct_app_context_falls_back_to_a_native_duckdb_port(tmp_path) -> None:
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
        assert isinstance(fallback.duckdb, NativeDuckDb)
    finally:
        _dispose(built)


def test_an_injected_duckdb_port_is_kept(tmp_path) -> None:
    fake = InMemoryDuckDb()
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
            duckdb_port=fake,
        )
        assert injected.duckdb is fake
    finally:
        _dispose(ctx)


def test_an_injected_duckdb_port_is_the_same_instance_the_glue_service_receives(
    tmp_path,
) -> None:
    # build_app_context resolves the port once and threads it to both
    # AppContext and GlueService -- the same "one port, not two" reasoning
    # documented on GluePageVM's duckdb_port parameter. A direct AppContext()
    # override (as in test_an_injected_duckdb_port_is_kept above) cannot prove
    # this: the registry it reuses was already built around a different port.
    fake = InMemoryDuckDb()
    ctx = build_app_context(
        config_dir=tmp_path / "config",
        cache_dir=tmp_path / "cache",
        duckdb_port=fake,
    )
    try:
        assert ctx.duckdb is fake
        glue_service = cast(GlueService, ctx.registry.get("glue"))
        assert glue_service._duckdb_port is fake  # type: ignore[attr-defined]
    finally:
        _dispose(ctx)


def test_duckdb_port_is_not_a_disposable_of_the_unstarted_context(tmp_path) -> None:
    # It owns no resources, so it must stay out of close_unstarted and out
    # of the app's shutdown event list (tests/unit/test_app_sanity.py).
    ctx = build_app_context(
        config_dir=tmp_path / "config",
        cache_dir=tmp_path / "cache",
    )
    ctx.close_unstarted()
    assert not hasattr(ctx.duckdb, "dispose")
