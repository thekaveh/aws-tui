"""Full-app layout regressions, including the banner and navigation rail."""

from __future__ import annotations

import asyncio
from html import unescape
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest
from textual.containers import VerticalScroll
from textual.pilot import Pilot
from textual.widgets import Button, TextArea

from aws_tui.composition import build_app_context
from aws_tui.demo.in_memory_athena import InMemoryAthena
from aws_tui.domain.query import QueryContext, QueryExecutionRef, QueryState
from aws_tui.services.athena.service import AthenaService
from aws_tui.ui.widgets.athena.page import AthenaPage
from aws_tui.ui.widgets.brand_banner import BrandBanner
from aws_tui.ui.widgets.context_picker import ContextPicker
from tests.helpers import drain_workers, focus_and_settle, wait_until
from tests.snapshot.apps.demo_mode import DemoModeApp


def _registered_service_ids() -> tuple[str, ...]:
    with TemporaryDirectory(prefix="compact-service-registry-") as directory:
        context = build_app_context(
            config_dir=Path(directory), cache_dir=Path(directory), demo=True
        )
        try:
            return tuple(service.descriptor.id for service in context.registry.all())
        finally:
            context.close_unstarted()


async def _open_athena(app: DemoModeApp) -> AthenaPage:
    await drain_workers(app)
    app.app_ctx.root_vm.services_menu.switch_service_command.execute("athena")
    await wait_until(lambda: bool(app.query(AthenaPage)), what="Athena page mounted")
    await drain_workers(app)
    page = app.query_one(AthenaPage)
    await wait_until(
        lambda: page.vm.context.database == "dev_events",
        what="demo Athena context loaded",
    )
    editor = app.query_one("#athena-editor", TextArea)
    await wait_until(
        lambda: editor.region.width > 0,
        what="Athena editor received its layout region",
    )
    return page


def _athena_client(app: DemoModeApp) -> InMemoryAthena:
    service = app.app_ctx.registry.get("athena")
    assert isinstance(service, AthenaService)
    assert service._client_factory is not None
    client = service._client_factory(app.app_ctx.connection_resolver.resolve("demo-dev"))
    assert isinstance(client, InMemoryAthena)
    return client


@pytest.mark.asyncio
@pytest.mark.parametrize("size", [(80, 24), (120, 40)])
async def test_full_app_athena_editor_has_usable_height(size: tuple[int, int]) -> None:
    app = DemoModeApp(theme="carbon")
    try:
        async with app.run_test(size=size):
            await _open_athena(app)
            editor = app.query_one("#athena-editor", TextArea)
            await wait_until(
                lambda: editor.region.height >= 3,
                what="full-app Athena editor has at least three rows",
            )
            assert editor.region.height >= 3
    finally:
        app.app_ctx.root_vm.dispose()


@pytest.mark.asyncio
async def test_compact_athena_context_controls_fit_the_viewport() -> None:
    app = DemoModeApp(theme="carbon")
    try:
        async with app.run_test(size=(80, 24)):
            page = await _open_athena(app)
            control_ids = (
                "athena-source-header",
                "athena-workgroup",
                "athena-catalog",
                "athena-database",
            )
            try:
                await wait_until(
                    lambda: all(
                        app.query_one(f"#{control_id}").region.width > 0
                        and app.query_one(f"#{control_id}").region.height > 0
                        and app.query_one(f"#{control_id}").region.right <= app.size.width
                        for control_id in control_ids
                    ),
                    what="Athena context controls laid out within the viewport",
                )
            except AssertionError as error:
                regions = {
                    control_id: app.query_one(f"#{control_id}").region for control_id in control_ids
                }
                raise AssertionError(
                    f"{error}; page={page.region}, classes={page.classes}, controls={regions}"
                ) from error
            for control_id in control_ids:
                region = app.query_one(f"#{control_id}").region
                assert region.width > 0
                assert region.height > 0
                assert region.right <= app.size.width, (control_id, region)
                assert region.bottom <= app.size.height, (control_id, region)
    finally:
        app.app_ctx.root_vm.dispose()


async def _tab_cycle(app: DemoModeApp, pilot: Pilot[None], *, reverse: bool = False) -> set[str]:
    nav = app.query_one("#nav-menu")
    await focus_and_settle(nav)
    seen: set[str] = set()
    # A bounded full traversal detects a missing target without a time-based
    # failure. The extra turn also detects cycles that never return to the rail.
    for _ in range(len(app.screen.focus_chain) + 1):
        await pilot.press("shift+tab" if reverse else "tab")
        assert app.focused is not None
        if app.focused is nav:
            return seen
        if app.focused.id is not None:
            seen.add(app.focused.id)
    pytest.fail(f"Tab cycle did not return to navigation: {seen}")


@pytest.mark.asyncio
@pytest.mark.parametrize("reverse", [False, True])
async def test_compact_athena_tab_reaches_context_execute_and_status(reverse: bool) -> None:
    app = DemoModeApp(theme="carbon")
    try:
        async with app.run_test(size=(80, 24)) as pilot:
            await _open_athena(app)
            await focus_and_settle(app.query_one("#athena-editor", TextArea))
            await pilot.press(*"SELECT 1")
            await wait_until(
                lambda: not app.query_one("#athena-execute", Button).disabled,
                what="valid SQL enables Athena execution",
            )
            assert app.query_one("#athena-cancel", Button).disabled
            seen = await _tab_cycle(app, pilot, reverse=reverse)
            assert {
                "athena-workgroup",
                "athena-catalog",
                "athena-database",
                "athena-execute",
                "athena-query-status",
            } <= seen
    finally:
        app.app_ctx.root_vm.dispose()


@pytest.mark.asyncio
async def test_athena_resize_preserves_typed_sql_context_and_focus() -> None:
    app = DemoModeApp(theme="carbon")
    client = _athena_client(app)
    client.add_workgroup("resize-workgroup", output_location="s3://athena-results/resize/")
    client.add_catalog("resize-workgroup", "ResizeCatalog")
    client.add_database("resize-workgroup", "ResizeCatalog", "resize_database")
    try:
        async with app.run_test(size=(120, 40)) as pilot:
            page = await _open_athena(app)
            await page.vm.select_workgroup("resize-workgroup")
            await wait_until(
                lambda: page.vm.context.database == "resize_database",
                what="non-default Athena workgroup, catalog and database selected",
            )
            editor = app.query_one("#athena-editor", TextArea)
            await focus_and_settle(editor)
            sql = "SELECT 42 AS compact_resize"
            await pilot.press(*sql)
            await wait_until(lambda: page.vm.query.sql == sql, what="typed SQL reached query model")
            context = page.vm.context
            for size in ((80, 24), (120, 40)):
                await pilot.resize_terminal(*size)
                await wait_until(
                    lambda size=size: app.size == size and editor.region.height >= 3,
                    what=f"Athena editor is visible after resize to {size}",
                )
                assert app.query_one(AthenaPage) is page
                assert app.query_one("#athena-editor", TextArea) is editor
                assert editor.text == sql
                assert page.vm.query.sql == sql
                assert page.vm.context == context
                assert app.focused is editor
                assert app.query_one("#athena-workgroup", ContextPicker).value == context.workgroup
                assert app.query_one("#athena-catalog", ContextPicker).value == context.catalog
                assert app.query_one("#athena-database", ContextPicker).value == context.database
                for reverse in (False, True):
                    assert {
                        "athena-editor",
                        "athena-workgroup",
                        "athena-catalog",
                        "athena-database",
                        "athena-query-status",
                    } <= await _tab_cycle(app, pilot, reverse=reverse)
                await focus_and_settle(editor)
    finally:
        app.app_ctx.root_vm.dispose()


@pytest.mark.asyncio
async def test_open_context_picker_remains_escapable_after_resize() -> None:
    app = DemoModeApp(theme="carbon")
    try:
        async with app.run_test(size=(120, 40)) as pilot:
            page = await _open_athena(app)
            context = page.vm.context
            picker = app.query_one("#athena-catalog", ContextPicker)
            await focus_and_settle(picker)
            await pilot.press("enter")
            await wait_until(lambda: picker.is_open, what="catalog overlay opened before resize")
            await pilot.resize_terminal(80, 24)
            assert picker.is_open
            assert app.focused is not None
            await pilot.press("escape")
            await wait_until(
                lambda: not picker.is_open and app.focused is picker,
                what="resized catalog overlay closes and returns focus",
            )
            assert page.vm.context == context
            await pilot.resize_terminal(120, 40)
            assert app.focused is picker
            assert page.vm.context == context
    finally:
        app.app_ctx.root_vm.dispose()


@pytest.mark.asyncio
async def test_compact_execution_error_detail_can_be_scrolled_with_keyboard() -> None:
    app = DemoModeApp(theme="carbon")
    try:
        async with app.run_test(size=(80, 24)) as pilot:
            page = await _open_athena(app)
            page.vm.query.set_sql("SELECT 12345")
            await page.vm.query.execute()
            assert page.vm.query.error_text == "Athena rejected the request"
            detail = app.query_one("#athena-query-detail", VerticalScroll)
            await wait_until(
                lambda: detail.max_scroll_y > 0,
                what="compact execution detail has scrollable error and workgroup rows",
            )
            await focus_and_settle(detail)
            await pilot.press(*(["down"] * 10))
            assert detail.scroll_y > 0
            rendered = unescape(app.export_screenshot()).replace("\xa0", " ")
            assert "Workgroup output" in rendered
            await pilot.press(*(["up"] * 10))
            await wait_until(
                lambda: detail.scroll_y == 0, what="execution detail scrolled to error"
            )
            rendered = unescape(app.export_screenshot()).replace("\xa0", " ")
            assert "Athena rejected the request" in rendered
    finally:
        app.app_ctx.root_vm.dispose()


@pytest.mark.asyncio
async def test_compact_cancel_remains_reachable_during_submission_and_resize(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = DemoModeApp(theme="carbon")
    started = asyncio.Event()
    release = asyncio.Event()
    try:
        async with app.run_test(size=(120, 40)) as pilot:
            try:
                page = await _open_athena(app)
                client = _athena_client(app)
                original_start = client.start_query

                async def gated_start(
                    sql: str,
                    context: QueryContext,
                    *,
                    request_token: str,
                    output_location: str | None = None,
                ) -> QueryExecutionRef:
                    started.set()
                    await release.wait()
                    return await original_start(
                        sql, context, request_token=request_token, output_location=output_location
                    )

                monkeypatch.setattr(client, "start_query", gated_start)
                editor = app.query_one("#athena-editor", TextArea)
                await focus_and_settle(editor)
                await pilot.press(*"SELECT 1")
                await focus_and_settle(app.query_one("#athena-execute", Button))
                await pilot.press("enter")
                await wait_until(started.is_set, what="demo Athena submission reached the provider")
                await pilot.resize_terminal(80, 24)
                await wait_until(
                    lambda: (
                        editor.region.height >= 3 and not app.query_one("#athena-cancel").disabled
                    ),
                    what="compact editor and Cancel are ready during submission",
                )
                assert page.vm.query.is_submitting
                assert editor.text == "SELECT 1"
                assert app.focused is not None
                seen = await _tab_cycle(app, pilot)
                assert "athena-cancel" in seen
                assert "athena-query-status" in seen
                assert "athena-execute" not in seen
                await focus_and_settle(app.query_one("#athena-cancel", Button))
                await pilot.press("enter")
                await wait_until(
                    lambda: page.vm.query.state is QueryState.CANCELLED,
                    what="keyboard Cancel interrupts the pending submission",
                )
                await pilot.resize_terminal(120, 40)
                assert app.focused is not None
                assert editor.text == "SELECT 1"
                assert page.vm.query.state is QueryState.CANCELLED
            finally:
                release.set()
    finally:
        release.set()
        app.app_ctx.root_vm.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("service_id", [*_registered_service_ids(), "settings"])
async def test_compact_services_have_content_identity_and_escapable_pickers(
    service_id: str,
) -> None:
    app = DemoModeApp(theme="carbon")
    try:
        async with app.run_test(size=(80, 24)) as pilot:
            await drain_workers(app)
            app.app_ctx.root_vm.services_menu.switch_service_command.execute(service_id)
            await wait_until(
                lambda: app.app_ctx.root_vm.content_host.current_id == service_id,
                what=f"{service_id} content model is active",
            )
            await drain_workers(app)
            host = app.query_one("#content-host")
            await wait_until(
                lambda: bool(host.children) and host.children[0].region.height > 0,
                what=f"{service_id} page is mounted with nonzero content",
            )
            assert host.region.height > 0
            assert host.region.width > 0
            assert host.children[0].region.height > 0
            assert host.children[0].region.width > 0
            rendered = unescape(app.export_screenshot()).replace("\xa0", " ")
            label = (
                "Settings"
                if service_id == "settings"
                else app.app_ctx.registry.get(service_id).descriptor.label
            )
            assert label in rendered
            assert "DEMO MODE" in rendered
            if service_id != "settings":
                assert "demo-dev" in rendered
            pickers = [
                picker
                for picker in host.query(ContextPicker)
                if picker.visible and picker.region.height > 0 and not picker.disabled
            ]
            for picker in pickers:
                await focus_and_settle(picker)
                await pilot.press("enter")
                await wait_until(
                    lambda picker=picker: picker.is_open, what=f"{picker.id} overlay opened"
                )
                await pilot.press("escape")
                await wait_until(
                    lambda picker=picker: not picker.is_open and app.focused is picker,
                    what=f"Escape closes {picker.id} and restores trigger focus",
                )
                assert not picker.is_open
                assert app.focused is picker
    finally:
        app.app_ctx.root_vm.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("width", [80, 90, 120])
async def test_compact_context_pagination_controls_fit_and_join_tab_cycle(width: int) -> None:
    app = DemoModeApp(theme="carbon")
    client = _athena_client(app)
    client.page_size = 1
    client.add_database("dev-analytics", "DevDataCatalog", "dev_extra")
    try:
        async with app.run_test(size=(width, 24)) as pilot:
            page = await _open_athena(app)
            more_ids = ("athena-more-workgroups", "athena-more-catalogs", "athena-more-databases")
            await wait_until(
                lambda: all(app.query_one(f"#{control_id}").display for control_id in more_ids),
                what="all three Athena context pagination controls are visible",
            )
            await wait_until(
                lambda: all(
                    app.query_one(f"#{control_id}").region.width > 0
                    and app.screen.region.contains_region(app.query_one(f"#{control_id}").region)
                    for control_id in more_ids
                ),
                what="paginated Athena context row fits the viewport",
            )
            for control_id in (*more_ids, "athena-workgroup", "athena-catalog", "athena-database"):
                region = app.query_one(f"#{control_id}").region
                assert region.width > 0
                assert region.height > 0
                assert app.screen.region.contains_region(region), (control_id, region)
            seen = await _tab_cycle(app, pilot)
            assert set(more_ids) <= seen
            await page.vm.load_more_workgroups()
            await page.vm.load_more_catalogs()
            await page.vm.load_more_databases()
            await wait_until(
                lambda: all(not app.query_one(f"#{control_id}").display for control_id in more_ids),
                what="exhausted context pagination removes its controls",
            )
            if width >= 90:
                await wait_until(
                    lambda: app.query_one("#athena-source-header").region.width == 29,
                    what="source selector regains its roomy width when context controls fit",
                )
            for control_id in (
                "athena-source-header",
                "athena-workgroup",
                "athena-catalog",
                "athena-database",
            ):
                assert app.screen.region.contains_region(app.query_one(f"#{control_id}").region)
    finally:
        app.app_ctx.root_vm.dispose()


@pytest.mark.asyncio
async def test_compact_banner_follows_committed_service_and_source_changes() -> None:
    app = DemoModeApp(theme="carbon")
    try:
        async with app.run_test(size=(80, 24)):
            await _open_athena(app)
            banner = app.query_one(BrandBanner)
            assert "Athena · demo-dev · us-east-1" in str(banner.render())
            await app.action_swap_source()
            await drain_workers(app)
            await wait_until(
                lambda: "Athena · demo-prod · us-east-1" in str(banner.render()),
                what="compact chrome reflects the committed Athena source switch",
            )
            rendered = unescape(app.export_screenshot()).replace("\xa0", " ")
            assert "Athena · demo-prod · us-east-1" in rendered
            assert "demo-dev" not in str(banner.render())
            assert "DEMO MODE" in str(banner.render())
    finally:
        app.app_ctx.root_vm.dispose()
