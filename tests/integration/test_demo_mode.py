"""Integration test for the demo-mode boot flow.

Boots ``AwsTuiApp(build_app_context(demo=True))`` headlessly and
walks the canonical demo journey: verifies the BrandBanner shows
the demo chip, confirms the 4 demo connections are configured, and
checks that the demo-dev InMemoryFS seed exposes ``etl-input`` at
the file-pane level.
"""

from __future__ import annotations

import asyncio
import contextlib
import inspect
from pathlib import Path

import pytest

from aws_tui.app import AwsTuiApp
from aws_tui.composition import AppContext, build_app_context
from aws_tui.demo.in_memory_athena import InMemoryAthena
from aws_tui.domain.data_catalog import TableFormat, TableRef
from aws_tui.domain.filesystem import ProviderUnreachableError
from aws_tui.services.athena.service import AthenaService
from aws_tui.ui.widgets.athena.history_view import AthenaHistoryView
from aws_tui.ui.widgets.brand_banner import BrandBanner
from aws_tui.ui.widgets.nav_menu import NavMenu
from aws_tui.ui.widgets.nav_row import NavRow
from aws_tui.ui.widgets.pane import Pane
from aws_tui.vm.athena.page_vm import AthenaPageVM
from aws_tui.vm.chrome.focus_coordinator_vm import FocusSlot
from aws_tui.vm.file_manager.pane_vm import PaneState
from aws_tui.vm.glue.page_vm import GluePageVM

pytestmark = pytest.mark.asyncio


async def _wait_for_service_setup(
    ctx: AppContext,
    pilot: object,
) -> None:
    app = pilot.app  # type: ignore[attr-defined]
    await app.workers.wait_for_complete(list(app.workers._workers))  # type: ignore[attr-defined]
    setup_task = ctx.root_vm.content_host._setup_task  # type: ignore[attr-defined]
    if setup_task is not None and not setup_task.done():
        await setup_task
    await pilot.pause()  # type: ignore[attr-defined]


async def _open_service(
    ctx: AppContext,
    pilot: object,
    service_id: str,
) -> None:
    app = pilot.app  # type: ignore[attr-defined]
    await app.workers.wait_for_complete(list(app.workers._workers))  # type: ignore[attr-defined]
    await pilot.pause()  # type: ignore[attr-defined]
    ctx.root_vm.services_menu.switch_service_command.execute(service_id)
    await _wait_for_service_setup(ctx, pilot)


def _athena_client(ctx: AppContext, profile: str) -> InMemoryAthena:
    service = ctx.registry.get("athena")
    assert isinstance(service, AthenaService)
    factory = service._client_factory
    assert factory is not None
    client = factory(ctx.connection_resolver.resolve(profile))
    assert isinstance(client, InMemoryAthena)
    return client


async def _invoke(app: AwsTuiApp, action_id: str) -> None:
    result = app.action_dispatch(action_id)
    if inspect.isawaitable(result):
        await result


async def test_demo_mode_boots_with_four_demo_connections(tmp_path) -> None:
    """End-to-end: demo=True wires DemoConnectionResolver +
    InMemoryFS factories so the app boots without touching real
    AWS or local config."""
    ctx = build_app_context(config_dir=tmp_path, cache_dir=tmp_path, demo=True)
    # Sanity: the AppContext flag itself.
    assert ctx.demo is True
    # The connection resolver is the demo one.
    conns = ctx.connection_resolver.list()
    names = {c.name for c in conns}
    assert {"demo-dev", "demo-prod", "demo-shared", "demo-minio"}.issubset(names)
    # Boot the Textual app and verify the BrandBanner subtitle.
    app = AwsTuiApp(context=ctx)
    try:
        async with app.run_test() as pilot:
            # Drain async workers then let the event loop settle.
            await app.workers.wait_for_complete(list(app.workers._workers))  # type: ignore[attr-defined]
            await pilot.pause()
            await pilot.pause()

            banner = app.query_one(BrandBanner)
            assert "DEMO MODE" in banner.border_subtitle
    finally:
        with contextlib.suppress(Exception):
            ctx.root_vm.dispose()


async def test_demo_mode_renders_s3_pane_with_demo_files(tmp_path) -> None:
    """The demo-dev connection's InMemoryFS seed exposes ``etl-input``
    in the S3 file pane.

    With the SSO-probe bypass in place (``ctx.demo`` short-circuits
    ``probe_token``), the natural boot chain reaches ``demo-dev``
    (first ``kind="aws"`` connection) and mounts its seeded InMemoryFS
    automatically — no explicit private-method pokes required.
    """
    from aws_tui.ui.widgets.pane import EntryRow

    ctx = build_app_context(config_dir=tmp_path, cache_dir=tmp_path, demo=True)
    app = AwsTuiApp(context=ctx)
    try:
        async with app.run_test() as pilot:
            # Drain boot chain workers and let the event loop settle.
            await app.workers.wait_for_complete(list(app.workers._workers))  # type: ignore[attr-defined]
            await pilot.pause()

            # Drain workers so DualPane.on_mount → VM setup() → InMemoryFS.list
            # completes and the EntryRow widgets are populated.
            for _ in range(5):
                await app.workers.wait_for_complete(list(app.workers._workers))  # type: ignore[attr-defined]
                await pilot.pause()

            # File rows are EntryRow(Widget) — not Static — so query
            # the concrete class. str(rich.text.Text) yields plain text
            # which includes the name column padded to column width.
            all_text = " ".join(str(w.render()) for w in app.query(EntryRow))
            assert "etl-input" in all_text, (
                f"expected 'etl-input' bucket in the rendered EntryRow tree; got "
                f"first 200 chars: {all_text[:200]!r}"
            )
    finally:
        with contextlib.suppress(Exception):
            ctx.root_vm.dispose()


async def test_demo_mode_shift_s_uses_seeded_connection_factory(tmp_path) -> None:
    """Shift+S must keep demo panes on seeded in-memory providers.

    The boot path starts on ``demo-dev``. Cycling once moves the focused
    pane to ``demo-prod``; if that path bypasses ``S3Service``'s demo
    factory it will try to construct a real boto-backed S3FS instead of
    rendering the seeded ``data-lake`` objects.
    """
    from aws_tui.ui.widgets.pane import EntryRow

    ctx = build_app_context(config_dir=tmp_path, cache_dir=tmp_path, demo=True)
    app = AwsTuiApp(context=ctx)
    try:
        async with app.run_test() as pilot:
            await app.workers.wait_for_complete(list(app.workers._workers))  # type: ignore[attr-defined]
            for _ in range(5):
                await app.workers.wait_for_complete(list(app.workers._workers))  # type: ignore[attr-defined]
                await pilot.pause()

            await pilot.press("S")  # Shift+S
            for _ in range(5):
                await app.workers.wait_for_complete(list(app.workers._workers))  # type: ignore[attr-defined]
                await pilot.pause()

            dual = ctx.root_vm.content_host.current
            focused = dual.focused_pane
            all_text = " ".join(str(w.render()) for w in app.query(EntryRow))

            assert focused.identity_label == "aws s3 · demo-prod · us-east-1"
            assert "data-lake" in all_text
            assert "etl-output" in all_text
    finally:
        with contextlib.suppress(Exception):
            ctx.root_vm.dispose()


async def test_demo_mode_launch_selects_menu_not_s3_panes(tmp_path) -> None:
    """Demo launch starts with the nav/menu pane visually selected.

    The active service is still S3, but neither S3 pane should show the
    focused border until the user explicitly tabs/enters into the service.
    """
    ctx = build_app_context(config_dir=tmp_path, cache_dir=tmp_path, demo=True)
    app = AwsTuiApp(context=ctx)
    try:
        async with app.run_test() as pilot:
            await app.workers.wait_for_complete(list(app.workers._workers))  # type: ignore[attr-defined]
            await pilot.pause()
            for _ in range(5):
                await app.workers.wait_for_complete(list(app.workers._workers))  # type: ignore[attr-defined]
                await pilot.pause()

            selected_nav = [row for row in app.query(NavRow) if "-selected" in row.classes]
            focused_panes = [pane for pane in app.query(Pane) if "-focused" in pane.classes]

            assert ctx.root_vm.services_menu.selected_id == "s3"
            assert len(list(app.query(Pane))) == 2
            assert ctx.focus_coordinator.focused_slot is FocusSlot.NAV_MENU
            assert isinstance(app.focused, NavMenu)
            assert "-rail-active" in app.screen.classes
            assert [row.descriptor_id for row in selected_nav] == ["s3"]
            assert focused_panes == []
    finally:
        with contextlib.suppress(Exception):
            ctx.root_vm.dispose()


async def test_glue_demo_profiles_have_disjoint_catalogs(tmp_path: Path) -> None:
    ctx = build_app_context(
        config_dir=tmp_path / "config",
        cache_dir=tmp_path / "cache",
        demo=True,
    )
    app = AwsTuiApp(ctx)
    try:
        # This test asserts rendered catalog data, so use the same
        # operational viewport as the adjacent Athena profile test. At
        # Textual's 80x24 default the global banner and command deck leave
        # the service content host only one row tall.
        async with app.run_test(size=(120, 40)) as pilot:
            await _open_service(ctx, pilot, "glue")
            assert "dev_events" in app.export_screenshot()

            await app.action_swap_source()
            await _wait_for_service_setup(ctx, pilot)

            rendered = app.export_screenshot()
            assert "prod_sales" in rendered
            assert "dev_events" not in rendered
    finally:
        with contextlib.suppress(Exception):
            ctx.root_vm.dispose()


async def test_athena_demo_profiles_have_disjoint_context_and_history(
    tmp_path: Path,
) -> None:
    ctx = build_app_context(
        config_dir=tmp_path / "config",
        cache_dir=tmp_path / "cache",
        demo=True,
    )
    app = AwsTuiApp(ctx)
    try:
        async with app.run_test(size=(120, 40)) as pilot:
            await _open_service(ctx, pilot, "athena")
            page = ctx.root_vm.content_host.current
            assert isinstance(page, AthenaPageVM)
            await page.select_view("history")
            await pilot.pause()
            app.query_one(AthenaHistoryView)._refresh()  # type: ignore[attr-defined]
            await app.workers.wait_for_complete(list(app.workers._workers))  # type: ignore[attr-defined]
            rendered = app.export_screenshot()
            assert "dev-analytics" in rendered
            assert "q-dev-succeeded" in rendered

            await app.action_swap_source()
            await _wait_for_service_setup(ctx, pilot)
            page = ctx.root_vm.content_host.current
            assert isinstance(page, AthenaPageVM)
            await page.select_view("history")
            await pilot.pause()
            app.query_one(AthenaHistoryView)._refresh()  # type: ignore[attr-defined]
            await app.workers.wait_for_complete(list(app.workers._workers))  # type: ignore[attr-defined]

            rendered = app.export_screenshot()
            assert "prod-reporting" in rendered
            assert "q-prod-succeeded" in rendered
            assert "dev-analytics" not in rendered
            assert "q-dev-succeeded" not in rendered
    finally:
        with contextlib.suppress(Exception):
            ctx.root_vm.dispose()


async def test_athena_profile_switch_mounts_empty_rows_before_new_load(
    tmp_path: Path,
) -> None:
    ctx = build_app_context(
        config_dir=tmp_path / "config",
        cache_dir=tmp_path / "cache",
        demo=True,
    )
    service = ctx.registry.get("athena")
    assert isinstance(service, AthenaService)
    factory = service._client_factory
    assert factory is not None
    prod = factory(ctx.connection_resolver.resolve("demo-prod"))
    original_list_workgroups = prod.list_workgroups_page
    started = asyncio.Event()
    release = asyncio.Event()

    async def blocked_list_workgroups(*, start_token: str | None = None):
        started.set()
        await release.wait()
        return await original_list_workgroups(start_token=start_token)

    prod.list_workgroups_page = blocked_list_workgroups  # type: ignore[method-assign]
    app = AwsTuiApp(ctx)
    try:
        async with app.run_test(size=(120, 40)) as pilot:
            await _open_service(ctx, pilot, "athena")
            old_page = ctx.root_vm.content_host.current
            assert isinstance(old_page, AthenaPageVM)
            assert {row.name for row in old_page.workgroups} == {
                "dev-analytics",
                "dev-empty",
            }

            switching = asyncio.create_task(app.action_swap_source())
            await started.wait()
            new_page = ctx.root_vm.content_host.current

            assert isinstance(new_page, AthenaPageVM)
            assert new_page is not old_page
            assert new_page.workgroups == ()
            assert new_page.history.items == ()
            async with asyncio.timeout(5.0):
                while "dev-analytics" in app.export_screenshot():
                    await pilot.pause(0.01)

            release.set()
            await switching
            await _wait_for_service_setup(ctx, pilot)
            assert {row.name for row in new_page.workgroups} == {"prod-reporting"}
    finally:
        release.set()
        with contextlib.suppress(Exception):
            ctx.root_vm.dispose()


@pytest.mark.parametrize(
    ("profile", "database", "table", "snapshot_ids", "ref_name"),
    [
        ("demo-dev", "dev_analytics", "dev_events_iceberg", (4202, 4201), "dev-main"),
        ("demo-prod", "prod_warehouse", "prod_sales_iceberg", (7702, 7701), "prod-main"),
        ("demo-shared", "shared_lake", "shared_metrics_iceberg", (9902, 9901), "shared-main"),
    ],
)
async def test_demo_profiles_seed_disjoint_complete_iceberg_metadata(
    tmp_path: Path,
    profile: str,
    database: str,
    table: str,
    snapshot_ids: tuple[int, int],
    ref_name: str,
) -> None:
    ctx = build_app_context(
        config_dir=tmp_path / "config",
        cache_dir=tmp_path / "cache",
        demo=True,
    )
    app = AwsTuiApp(ctx)
    try:
        async with app.run_test(size=(120, 40)) as pilot:
            await _open_service(ctx, pilot, "glue")
            while ctx.root_vm.active_connection is None or (
                ctx.root_vm.active_connection.name != profile
            ):
                await app.action_swap_source()
                await _wait_for_service_setup(ctx, pilot)

            page = ctx.root_vm.content_host.current
            assert isinstance(page, GluePageVM)
            ref = TableRef(
                "AwsDataCatalog",
                database,
                table,
                profile,
                ctx.root_vm.active_connection.region,
            )
            await page.open_table(ref)
            assert page.catalog.table_detail is not None
            assert page.catalog.table_detail.table_format is TableFormat.ICEBERG

            iceberg = page.catalog.iceberg
            await iceberg.select_view("snapshots")
            assert tuple(row.snapshot_id for row in iceberg.snapshots) == snapshot_ids
            await iceberg.select_view("history")
            assert {row.snapshot_id for row in iceberg.history} == set(snapshot_ids)
            await iceberg.select_view("manifests")
            assert all(profile in row.path for row in iceberg.manifests)
            await iceberg.select_view("files")
            assert all(profile in row.file_path for row in iceberg.files)
            await iceberg.select_view("partitions")
            assert iceberg.partitions
            await iceberg.select_view("refs")
            assert [row.name for row in iceberg.refs] == [ref_name]

            foreign_markers = {
                "demo-dev": ("7701", "9901", "prod-main", "shared-main"),
                "demo-prod": ("4201", "9901", "dev-main", "shared-main"),
                "demo-shared": ("4201", "7701", "dev-main", "prod-main"),
            }[profile]
            rendered = app.export_screenshot()
            assert all(marker not in rendered for marker in foreign_markers)
    finally:
        with contextlib.suppress(Exception):
            ctx.root_vm.dispose()


async def test_demo_iceberg_time_travel_is_explicit_and_profile_local(
    tmp_path: Path,
) -> None:
    ctx = build_app_context(
        config_dir=tmp_path / "config",
        cache_dir=tmp_path / "cache",
        demo=True,
    )
    app = AwsTuiApp(ctx)
    try:
        async with app.run_test(size=(120, 40)) as pilot:
            await _open_service(ctx, pilot, "glue")
            glue = ctx.root_vm.content_host.current
            assert isinstance(glue, GluePageVM)
            ref = TableRef(
                "AwsDataCatalog",
                "dev_analytics",
                "dev_events_iceberg",
                "demo-dev",
                "us-east-1",
            )
            await glue.open_table(ref)
            await glue.catalog.iceberg.select_view("snapshots")
            assert glue.catalog.iceberg.select_snapshot(4201)
            client = _athena_client(ctx, "demo-dev")
            client.calls.clear()

            await _invoke(app, "glue.time_travel_in_athena")
            await _wait_for_service_setup(ctx, pilot)

            athena = ctx.root_vm.content_host.current
            assert isinstance(athena, AthenaPageVM)
            expected_sql = (
                'SELECT * FROM "AwsDataCatalog"."dev_analytics"."dev_events_iceberg" '
                "FOR VERSION AS OF 4201 LIMIT 100"
            )
            assert athena.query.sql == expected_sql
            assert athena.query.execution_ref is None
            assert athena.results.execution_id is None
            assert not any(call.method == "start_query" for call in client.calls)

            await _invoke(app, "athena.open_in_glue")
            await _wait_for_service_setup(ctx, pilot)
            reopened = ctx.root_vm.content_host.current
            assert isinstance(reopened, GluePageVM)
            assert reopened.catalog.selected_table_name == "dev_events_iceberg"
    finally:
        with contextlib.suppress(Exception):
            ctx.root_vm.dispose()


async def test_demo_source_switch_clears_old_iceberg_metadata_and_restores_scoped_selection(
    tmp_path: Path,
) -> None:
    ctx = build_app_context(
        config_dir=tmp_path / "config",
        cache_dir=tmp_path / "cache",
        demo=True,
    )
    app = AwsTuiApp(ctx)
    try:
        async with app.run_test(size=(120, 40)) as pilot:
            await _open_service(ctx, pilot, "glue")
            dev = ctx.root_vm.content_host.current
            assert isinstance(dev, GluePageVM)
            dev_ref = TableRef(
                "AwsDataCatalog",
                "dev_analytics",
                "dev_events_iceberg",
                "demo-dev",
                "us-east-1",
            )
            await dev.open_table(dev_ref)
            await dev.catalog.iceberg.select_view("snapshots")
            assert dev.catalog.iceberg.select_snapshot(4201)
            assert {row.snapshot_id for row in dev.catalog.iceberg.snapshots} == {4201, 4202}

            await app.action_swap_source()
            await _wait_for_service_setup(ctx, pilot)
            prod = ctx.root_vm.content_host.current
            assert isinstance(prod, GluePageVM)
            assert prod is not dev
            assert prod.catalog.iceberg.snapshots == ()
            assert "4201" not in app.export_screenshot()
            prod_ref = TableRef(
                "AwsDataCatalog",
                "prod_warehouse",
                "prod_sales_iceberg",
                "demo-prod",
                "us-east-1",
            )
            await prod.open_table(prod_ref)
            await prod.catalog.iceberg.select_view("snapshots")
            assert {row.snapshot_id for row in prod.catalog.iceberg.snapshots} == {7701, 7702}
            assert all(
                row.snapshot_id not in {4201, 4202} for row in prod.catalog.iceberg.snapshots
            )

            await app.action_swap_source()
            await _wait_for_service_setup(ctx, pilot)
            await app.action_swap_source()
            await _wait_for_service_setup(ctx, pilot)
            restored = ctx.root_vm.content_host.current
            assert isinstance(restored, GluePageVM)
            assert ctx.root_vm.active_connection is not None
            assert ctx.root_vm.active_connection.name == "demo-dev"
            assert restored.catalog.selected_database_name == "dev_analytics"
            assert restored.catalog.selected_table_name == "dev_events_iceberg"
            assert restored.catalog.iceberg.snapshots == ()
    finally:
        with contextlib.suppress(Exception):
            ctx.root_vm.dispose()


async def test_demo_iceberg_metadata_paginates_and_retries_safely(
    tmp_path: Path,
) -> None:
    ctx = build_app_context(
        config_dir=tmp_path / "config",
        cache_dir=tmp_path / "cache",
        demo=True,
    )
    client = _athena_client(ctx, "demo-dev")
    original_start_query = client.start_query
    failed_once = False

    async def fail_first_manifest(sql, context, *, request_token, output_location=None):
        nonlocal failed_once
        if "$manifests" in sql and not failed_once:
            failed_once = True
            raise ProviderUnreachableError("PRIVATE_DEMO_ENDPOINT")
        return await original_start_query(
            sql,
            context,
            request_token=request_token,
            output_location=output_location,
        )

    client.start_query = fail_first_manifest  # type: ignore[method-assign]
    app = AwsTuiApp(ctx)
    try:
        async with app.run_test(size=(120, 40)) as pilot:
            await _open_service(ctx, pilot, "glue")
            page = ctx.root_vm.content_host.current
            assert isinstance(page, GluePageVM)
            await page.open_table(
                TableRef(
                    "AwsDataCatalog",
                    "dev_analytics",
                    "dev_events_iceberg",
                    "demo-dev",
                    "us-east-1",
                )
            )
            iceberg = page.catalog.iceberg
            iceberg._page_size = 1  # type: ignore[attr-defined]
            await iceberg.select_view("snapshots")
            assert [row.snapshot_id for row in iceberg.snapshots] == [4202]
            assert iceberg.has_more
            await iceberg.load_more()
            assert [row.snapshot_id for row in iceberg.snapshots] == [4202, 4201]

            assert not await iceberg.select_view("manifests")
            assert iceberg.state is PaneState.UNREACHABLE
            assert "PRIVATE_DEMO_ENDPOINT" not in (iceberg.error_text or "")
            assert await iceberg.retry()
            assert iceberg.state is PaneState.IDLE
            assert len(iceberg.manifests) == 1
    finally:
        with contextlib.suppress(Exception):
            ctx.root_vm.dispose()


async def test_demo_source_switch_suppresses_stale_metadata_load(
    tmp_path: Path,
) -> None:
    ctx = build_app_context(
        config_dir=tmp_path / "config",
        cache_dir=tmp_path / "cache",
        demo=True,
    )
    client = _athena_client(ctx, "demo-dev")
    original_start_query = client.start_query
    started = asyncio.Event()
    release = asyncio.Event()

    async def blocked_start_query(sql, context, *, request_token, output_location=None):
        if "$snapshots" in sql:
            started.set()
            await release.wait()
        return await original_start_query(
            sql,
            context,
            request_token=request_token,
            output_location=output_location,
        )

    client.start_query = blocked_start_query  # type: ignore[method-assign]
    app = AwsTuiApp(ctx)
    load_task: asyncio.Task[bool] | None = None
    swap_task: asyncio.Task[None] | None = None
    try:
        async with app.run_test(size=(120, 40)) as pilot:
            await _open_service(ctx, pilot, "glue")
            old_page = ctx.root_vm.content_host.current
            assert isinstance(old_page, GluePageVM)
            await old_page.open_table(
                TableRef(
                    "AwsDataCatalog",
                    "dev_analytics",
                    "dev_events_iceberg",
                    "demo-dev",
                    "us-east-1",
                )
            )
            load_task = asyncio.create_task(old_page.catalog.iceberg.select_view("snapshots"))
            await asyncio.wait_for(started.wait(), timeout=5)

            swap_task = asyncio.create_task(app.action_swap_source())
            await asyncio.sleep(0)
            assert not swap_task.done()
            release.set()
            await asyncio.wait_for(swap_task, timeout=5)
            await _wait_for_service_setup(ctx, pilot)
            new_page = ctx.root_vm.content_host.current
            assert isinstance(new_page, GluePageVM)
            assert new_page is not old_page
            assert old_page.catalog.iceberg.snapshots == ()
            assert new_page.catalog.iceberg.snapshots == ()
            assert "4201" not in app.export_screenshot()
            assert "4202" not in app.export_screenshot()

            assert not await asyncio.wait_for(load_task, timeout=5)
            assert old_page.catalog.iceberg.snapshots == ()
            assert new_page.catalog.iceberg.snapshots == ()
    finally:
        release.set()
        if swap_task is not None and not swap_task.done():
            swap_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await swap_task
        if load_task is not None and not load_task.done():
            load_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await load_task
        with contextlib.suppress(Exception):
            ctx.root_vm.dispose()
