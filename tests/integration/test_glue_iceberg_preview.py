"""End-to-end journeys for the Glue Iceberg Peek pane.

Each test drives the real ``AwsTuiApp`` in demo mode, with an
:class:`~aws_tui.infra.duckdb.InMemoryDuckDb` injected through
``build_app_context(duckdb_port=...)``. Nothing here spawns a real DuckDB
engine or touches S3 -- the fake records every query and replays a canned
result, exactly as ``tests/unit/ui/glue/test_iceberg_view.py`` already does at
the widget level. This tier instead proves the whole wiring from composition
through the Textual pilot: opening Glue, landing on the seeded
``dev_events_iceberg`` table, and driving the Peek tab and its shared
``#glue-iceberg-more`` / ``#glue-iceberg-retry`` controls.

Waits use ``tests.helpers.wait_until`` against an observable condition, never
a count of ``pilot.pause()`` calls -- a pause yields one scheduler cycle
without advancing the clock, so a fixed loop of pauses can retire before the
worker it is meant to wait for even starts.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Callable
from pathlib import Path

import pytest
from textual.widgets import Button, DataTable, Static

from aws_tui.app import AwsTuiApp
from aws_tui.composition import AppContext, build_app_context
from aws_tui.infra.duckdb import DuckDbOutcome, InMemoryDuckDb
from aws_tui.ui.widgets.glue.iceberg_view import GlueIcebergView
from aws_tui.vm.file_manager.pane_vm import PaneState
from aws_tui.vm.glue.page_vm import GluePageVM
from tests.helpers import drain_workers, wait_until

SERVICE_SETUP_TIMEOUT_SECONDS = 30


async def _wait_for_service_setup(
    ctx: AppContext,
    app: AwsTuiApp,
    pilot: object,
) -> None:
    await drain_workers(app, timeout=SERVICE_SETUP_TIMEOUT_SECONDS)
    setup_task = ctx.root_vm.content_host._setup_task  # type: ignore[attr-defined]
    if setup_task is not None and not setup_task.done():
        await setup_task
    await pilot.pause()  # type: ignore[attr-defined]


async def _open_iceberg_preview(
    ctx: AppContext,
    app: AwsTuiApp,
    pilot: object,
) -> GluePageVM:
    """Switch to Glue and land on the seeded ``dev_events_iceberg`` table.

    ``dev_analytics`` is demo-dev's only database, and Glue auto-selects its
    first table (the plain Hive ``dev_events``) on setup -- see
    ``GluePageVM._setup_catalog``. Selecting the Iceberg sibling explicitly is
    what gives ``preview.available`` an ``s3://`` location and a profile.
    """
    await _wait_for_service_setup(ctx, app, pilot)
    ctx.root_vm.services_menu.switch_service_command.execute("glue")
    await _wait_for_service_setup(ctx, app, pilot)

    vm = ctx.root_vm.content_host.current
    assert isinstance(vm, GluePageVM)
    # Let Glue's own auto-selection of the first table finish COMPLETELY before
    # selecting the Iceberg sibling. `select_table` sets `_selected_table_name`
    # early and then checks `_is_catalog_operation_current` twice more before it
    # loads the detail and calls `bind_table`, so a selection that is superseded
    # midway still leaves the name set -- and `preview.available`, which depends
    # on the bind, never arrives. Waiting on the detail rather than the name is
    # what makes the precondition real.
    await wait_until(
        lambda: vm.catalog.table_detail is not None,
        what="Glue's initial table auto-selection to finish loading",
    )
    await vm.select_table("dev_events_iceberg")
    await wait_until(
        lambda: (
            (detail := vm.catalog.table_detail) is not None
            and detail.summary.ref.table_name == "dev_events_iceberg"
        ),
        what="the Iceberg sibling's own detail to load",
    )
    await wait_until(
        lambda: vm.catalog.iceberg.preview.available,
        what="the preview became available for the seeded Iceberg table",
    )
    tab = app.query_one("#glue-iceberg-tab-preview")
    # The VM notification queues a widget projection, which in turn queues
    # layout. Pilot.click captures coordinates before yielding to either one.
    await wait_until(
        lambda: (
            tab.display
            and tab.region.width > 0
            and tab.region.height > 0
            and app.get_widget_at(tab.region.x, tab.region.y)[0] is tab
        ),
        what="the Peek tab to be rendered and receive a click at its origin",
    )
    return vm


@pytest.mark.asyncio
async def test_preview_setup_waits_for_the_tab_to_be_rendered(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    original_refresh = GlueIcebergView._refresh
    projection_held = asyncio.Event()
    availability_observed = asyncio.Event()
    release_projection = False
    original_wait = wait_until

    async def observe_availability(predicate: Callable[[], bool], *, what: str) -> None:
        await original_wait(predicate, what=what)
        if what == "the preview became available for the seeded Iceberg table":
            availability_observed.set()

    # Observe the old helper's final milestone so the pending-task assertion
    # cannot pass merely because setup has not reached preview availability.
    monkeypatch.setattr(f"{__name__}.wait_until", observe_availability)

    def defer_preview_projection(view: GlueIcebergView) -> None:
        if view._vm.preview.available and not release_projection:
            projection_held.set()
            return
        original_refresh(view)

    monkeypatch.setattr(GlueIcebergView, "_refresh", defer_preview_projection)
    port = InMemoryDuckDb(columns=("event_id",), rows=(("8821",),))
    ctx = build_app_context(
        config_dir=tmp_path / "config",
        cache_dir=tmp_path / "cache",
        demo=True,
        duckdb_port=port,
    )
    app = AwsTuiApp(ctx)
    try:
        async with app.run_test(size=(120, 40)) as pilot:
            opening = asyncio.create_task(_open_iceberg_preview(ctx, app, pilot))
            try:
                await wait_until(
                    projection_held.is_set,
                    what="available preview to reach its deferred widget projection",
                )
                await wait_until(
                    availability_observed.is_set,
                    what="setup to observe preview availability before checking its tab",
                )
                tab = app.query_one("#glue-iceberg-tab-preview")
                assert tab.region.width == 0
                assert not opening.done(), "setup returned before the Peek tab was rendered"

                release_projection = True
                original_refresh(app.query_one(GlueIcebergView))
                vm = await opening
                assert await pilot.click("#glue-iceberg-tab-preview")
                await wait_until(
                    lambda: vm.catalog.iceberg.preview.state is PaneState.IDLE,
                    what="rendered Peek tab click to finish its preview scan",
                )
                assert len(port.queries) == 1
            finally:
                release_projection = True
                if not opening.done():
                    opening.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await opening
    finally:
        with contextlib.suppress(Exception):
            ctx.root_vm.dispose()


@pytest.mark.asyncio
async def test_peek_pane_loads_rows_and_reaches_idle(tmp_path: Path) -> None:
    port = InMemoryDuckDb(
        columns=("event_id", "event_name"),
        rows=(("8821", "click"), ("8822", "view")),
    )
    ctx = build_app_context(
        config_dir=tmp_path / "config",
        cache_dir=tmp_path / "cache",
        demo=True,
        duckdb_port=port,
    )
    app = AwsTuiApp(ctx)
    try:
        async with app.run_test(size=(120, 40)) as pilot:
            vm = await _open_iceberg_preview(ctx, app, pilot)

            assert await pilot.click("#glue-iceberg-tab-preview")
            await wait_until(
                lambda: vm.catalog.iceberg.preview.state is PaneState.IDLE,
                what="the preview pane reached IDLE",
            )
            table = app.query_one("#glue-iceberg-table", DataTable)
            footer = app.query_one("#glue-iceberg-footer", Static)
            await wait_until(
                lambda: table.row_count == 2 and str(footer.render()) == "2 rows · limit 100",
                what="the completed preview to render its two rows and footer",
            )
            assert table.row_count == 2

            assert len(port.queries) == 1
            sql, profile, region = port.queries[0]
            assert sql == (
                "SELECT * FROM iceberg_scan("
                "'s3://demo-dev/dev_analytics/dev_events_iceberg/') LIMIT 100"
            )
            assert profile == "demo-dev"
            assert region == "us-east-1"

            assert str(footer.render()) == "2 rows · limit 100"
    finally:
        with contextlib.suppress(Exception):
            ctx.root_vm.dispose()


@pytest.mark.asyncio
async def test_peek_more_button_issues_a_second_query_at_the_next_limit(
    tmp_path: Path,
) -> None:
    # A full page at the current limit: the pane's own honesty contract --
    # `has_more` is only true when a real next step exists, unlike the six
    # sibling panes that widen an already-capped local window with no new
    # query at all.
    port = InMemoryDuckDb(
        columns=("event_id",),
        rows=tuple((str(i),) for i in range(100)),
    )
    ctx = build_app_context(
        config_dir=tmp_path / "config",
        cache_dir=tmp_path / "cache",
        demo=True,
        duckdb_port=port,
    )
    app = AwsTuiApp(ctx)
    try:
        async with app.run_test(size=(120, 40)) as pilot:
            vm = await _open_iceberg_preview(ctx, app, pilot)

            assert await pilot.click("#glue-iceberg-tab-preview")
            await wait_until(
                lambda: vm.catalog.iceberg.preview.state is PaneState.IDLE,
                what="the first preview scan finished",
            )
            await wait_until(
                lambda: (
                    len(port.queries) == 1
                    and not app.query_one("#glue-iceberg-more", Button).disabled
                ),
                what="first preview scan to enable the load-more button",
            )
            assert len(port.queries) == 1
            assert vm.catalog.iceberg.preview.limit == 100

            more = app.query_one("#glue-iceberg-more", Button)
            assert not more.disabled

            await pilot.click("#glue-iceberg-more")
            # This is the assertion that makes load-more honest: a genuinely
            # second call reached the fake, not a wider read of rows already
            # in hand.
            await wait_until(
                lambda: len(port.queries) == 2,
                what="load-more issued a second DuckDB query",
            )
            await wait_until(
                lambda: vm.catalog.iceberg.preview.state is PaneState.IDLE,
                what="the second preview scan finished",
            )
            await wait_until(
                lambda: (
                    vm.catalog.iceberg.preview.limit == 1000
                    and app.query_one("#glue-iceberg-more", Button).disabled
                ),
                what="expanded preview to exhaust rows and disable load more",
            )

            assert vm.catalog.iceberg.preview.limit == 1000
            assert port.queries[0][0].endswith("LIMIT 100")
            assert port.queries[1][0].endswith("LIMIT 1000")
            # The canned double never grows past 100 rows, so the pane is
            # honestly out of pages after the second scan.
            assert app.query_one("#glue-iceberg-more", Button).disabled
    finally:
        with contextlib.suppress(Exception):
            ctx.root_vm.dispose()


@pytest.mark.asyncio
async def test_peek_pane_shows_the_install_line_when_the_engine_is_missing(
    tmp_path: Path,
) -> None:
    port = InMemoryDuckDb(outcome=DuckDbOutcome.ENGINE_MISSING)
    ctx = build_app_context(
        config_dir=tmp_path / "config",
        cache_dir=tmp_path / "cache",
        demo=True,
        duckdb_port=port,
    )
    app = AwsTuiApp(ctx)
    try:
        async with app.run_test(size=(120, 40)) as pilot:
            vm = await _open_iceberg_preview(ctx, app, pilot)

            assert await pilot.click("#glue-iceberg-tab-preview")
            await wait_until(
                lambda: vm.catalog.iceberg.preview.state is PaneState.ERROR,
                what="the preview pane reported the missing engine",
            )
            await wait_until(
                lambda: (
                    "pip install aws-tui[duckdb]"
                    in str(app.query_one("#glue-iceberg-status", Static).render())
                    and app.query_one("#glue-iceberg-retry", Button).display
                    and not app.query_one("#glue-iceberg-retry", Button).disabled
                ),
                what="missing preview engine to render its install hint and retry button",
            )

            assert vm.catalog.iceberg.preview.error_text == (
                "local preview needs DuckDB: pip install aws-tui[duckdb]"
            )
            status = app.query_one("#glue-iceberg-status", Static)
            assert "pip install aws-tui[duckdb]" in str(status.render())
            retry = app.query_one("#glue-iceberg-retry", Button)
            assert retry.display
            assert not retry.disabled
    finally:
        with contextlib.suppress(Exception):
            ctx.root_vm.dispose()
