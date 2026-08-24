"""Per-theme snapshot of demo-mode boot + content-presence guard."""

from __future__ import annotations

import asyncio
import contextlib
import html as html_lib
from functools import partial
from pathlib import Path

import pytest
from textual.widgets import DataTable
from textual.worker import WorkerCancelled

from aws_tui.domain.data_catalog import TableRef
from aws_tui.infra.aws_session import TokenState
from aws_tui.ui.widgets.pane import Pane
from aws_tui.vm.file_manager.pane_vm import PaneState
from aws_tui.vm.glue.iceberg_vm import GlueIcebergVM, IcebergView
from aws_tui.vm.glue.page_vm import GluePageVM
from tests.snapshot.apps.demo_mode import DemoModeApp
from tests.snapshot.conftest import THEMES

# Narrower than conftest.TERMINAL_SIZE (120, 40): the extra 10 rows push
# the boot toast below the captured frame, hiding the "Demo mode active"
# advisory that our content-presence guard checks for.  30 rows keeps
# both the BrandBanner "DEMO MODE" chip and the toast overlay in frame.
TERMINAL_SIZE = (120, 30)
_SERVICE_TAB_LABELS = ("3 crawlers", "1 catalog", "2 jobs")
_DEMO_STARTUP_ADVISORY = "Demo mode active — AWS data resets; local pane is real"


async def _drain_workers(pilot) -> None:  # type: ignore[no-untyped-def]
    """Wait for all async boot workers AND their downstream reactive
    updates to settle before snapping.

    Boot chain has two layers that need to settle before the snapshot:

    Layer 1 — Textual ``run_worker`` workers
        ``_initial_mount_worker`` drives the connection attempt and
        resolves when the LEFT pane reaches a terminal state (IDLE,
        EMPTY, UNREACHABLE, etc.).  ``workers.wait_for_complete``
        catches these.

    Layer 2 — plain asyncio task (``ContentHostVM._setup_task``)
        ``ContentHostVM.set_content()`` dispatches ``DualPaneVM.setup()``
        as a raw ``asyncio.create_task``, NOT a Textual worker.
        ``workers.wait_for_complete`` is blind to this task.
        We await it directly via
        ``app._app_ctx.root_vm.content_host._setup_task`` so we know
        the InMemoryFS listing has finished before we force-refresh
        the chrome.

    Layer 3 — direct chrome refresh
        After setup completes, ``_notify("viewmodel")`` has fired but
        the downstream call_after_refresh → InvokeLater → screen
        callback chain is non-deterministic across the 10 sequential
        test runs (different asyncio scheduling pressure per theme).
        Rather than polling the async queue, we call ``_refresh_chrome``
        directly on every mounted ``Pane`` widget.  This is safe because:
        (a) setup has completed, so ``pane._vm.viewmodel.summary`` already
            holds the settled value ("2 obj · 0 B", "21 obj · 94 B", etc.),
        (b) ``_refresh_chrome`` is a pure synchronous read-and-update —
            it does not re-trigger the same reactive chain — and
        (c) it is exactly what the reactive chain would have called
            once settled; forcing it here just removes the timing
            uncertainty from the snapshot.

    Two ``pilot.pause()`` calls after the force-refresh drain CSS
    transitions and focus-ring animations before the snapshot is taken.
    ``wait_for_scheduled_animations`` at the end catches any stragglers.

    The hard cap of 20 iterations in the worker loop handles nested
    second-order workers.
    """
    # Layer 1: drain all Textual run_worker workers.
    for _ in range(20):
        workers = list(pilot.app.workers._workers)  # type: ignore[attr-defined]
        if not workers:
            break
        with contextlib.suppress(WorkerCancelled):
            await pilot.app.workers.wait_for_complete(workers)
        await pilot.pause()

    # Layer 2: await the ContentHostVM._setup_task directly.
    # This is the plain asyncio task that run_worker misses.
    with contextlib.suppress(Exception):
        setup_task: asyncio.Task[None] | None = pilot.app._app_ctx.root_vm.content_host._setup_task  # type: ignore[attr-defined]
        if setup_task is not None and not setup_task.done():
            await setup_task

    # Layer 3: force-sync every Pane's chrome (header + footer) from
    # the live VM state.  After setup_task is done, the VM is settled;
    # calling _refresh_chrome directly bypasses the non-deterministic
    # call_after_refresh → InvokeLater → screen-callback chain.
    for pane in pilot.app.query(Pane):
        with contextlib.suppress(Exception):
            pane._refresh_chrome()  # type: ignore[attr-defined]

    # Two final ticks drain any pending animations / CSS transitions
    # (focus rings, toast entrance) before the screenshot is taken.
    await pilot.pause()
    await pilot.pause()
    await pilot.wait_for_scheduled_animations()


async def _show_demo_iceberg(pilot, view: IcebergView = "snapshots") -> None:  # type: ignore[no-untyped-def]
    await _show_profile_iceberg(
        pilot,
        profile="demo-dev",
        region="us-east-1",
        database="dev_analytics",
        table="dev_events_iceberg",
        view=view,
    )


async def _wait_for_rendered_iceberg(
    pilot,
    vm: GlueIcebergVM,
    view: IcebergView,
) -> None:  # type: ignore[no-untyped-def]
    """Wait until the public VM state has reached the rendered widget tree."""
    table = pilot.app.query_one("#glue-iceberg-table", DataTable)
    tab = pilot.app.query_one(f"#glue-iceberg-tab-{view}")
    for _ in range(100):
        expected_rows = len(vm.items)
        if (
            vm.active_view == view
            and vm.state in {PaneState.IDLE, PaneState.EMPTY}
            and expected_rows > 0
            and table.row_count == expected_rows
            and tab.has_class("-active")
        ):
            return
        await pilot.pause(0.01)
    raise AssertionError(
        f"Iceberg {view!r} did not reactively render "
        f"(state={vm.state}, items={len(vm.items)}, rows={table.row_count})"
    )


async def _dismiss_demo_startup_advisory(pilot) -> None:  # type: ignore[no-untyped-def]
    """Capture Iceberg documentation after the one-shot demo advisory clears."""
    toast_stack = pilot.app.app_ctx.root_vm.chrome.toast_stack
    matching = [toast for toast in toast_stack.toasts if _DEMO_STARTUP_ADVISORY in toast.model.text]
    assert len(matching) == 1, "expected exactly one startup demo advisory"

    toast_stack.dismiss(matching[0].model.id)
    await _drain_workers(pilot)
    assert not any(_DEMO_STARTUP_ADVISORY in toast.model.text for toast in toast_stack.toasts), (
        "startup demo advisory did not dismiss"
    )


async def _show_profile_iceberg(  # type: ignore[no-untyped-def]
    pilot,
    *,
    profile: str,
    region: str,
    database: str,
    table: str,
    view: IcebergView,
) -> None:
    await _drain_workers(pilot)
    app = pilot.app
    ctx = app.app_ctx
    connection = ctx.connection_resolver.resolve(profile)
    await ctx.root_vm.switch_connection_with(
        connection,
        TokenState.CONNECTED,
    )
    ctx.root_vm.services_menu.switch_service_command.execute("glue")
    await _drain_workers(pilot)
    page = ctx.root_vm.content_host.current
    assert isinstance(page, GluePageVM)
    await page.open_table(
        TableRef(
            "AwsDataCatalog",
            database,
            table,
            profile,
            region,
        )
    )
    assert await page.catalog.iceberg.select_view(view)
    await _wait_for_rendered_iceberg(pilot, page.catalog.iceberg, view)
    await _dismiss_demo_startup_advisory(pilot)


def _assert_service_tab_labels(svg: str, path: Path) -> None:
    for label in _SERVICE_TAB_LABELS:
        assert label in svg, f"{label!r} is not visibly rendered in {path.name}"


@pytest.mark.parametrize("theme", THEMES)
def test_demo_mode_snapshot(theme: str, snap_compare) -> None:  # type: ignore[no-untyped-def]
    assert snap_compare(
        DemoModeApp(theme=theme),
        terminal_size=TERMINAL_SIZE,
        run_before=_drain_workers,
    )


@pytest.mark.parametrize("theme", THEMES)
def test_demo_iceberg_snapshot(theme: str, snap_compare) -> None:  # type: ignore[no-untyped-def]
    assert snap_compare(
        DemoModeApp(theme=theme),
        terminal_size=(120, 40),
        run_before=_show_demo_iceberg,
    )


@pytest.mark.parametrize(
    "iceberg_view",
    ["history", "manifests", "files", "partitions", "refs"],
)
def test_demo_iceberg_metadata_snapshot(
    iceberg_view: IcebergView,
    snap_compare,
) -> None:  # type: ignore[no-untyped-def]
    assert snap_compare(
        DemoModeApp(theme="carbon"),
        terminal_size=(120, 40),
        run_before=partial(_show_demo_iceberg, view=iceberg_view),
    )


@pytest.mark.parametrize(
    ("profile", "region", "database", "table", "iceberg_view"),
    [
        (
            "demo-prod",
            "us-east-1",
            "prod_warehouse",
            "prod_sales_iceberg",
            "snapshots",
        ),
        (
            "demo-prod",
            "us-east-1",
            "prod_warehouse",
            "prod_sales_iceberg",
            "files",
        ),
        (
            "demo-shared",
            "us-west-2",
            "shared_lake",
            "shared_metrics_iceberg",
            "refs",
        ),
    ],
)
def test_demo_iceberg_profile_snapshot(
    profile: str,
    region: str,
    database: str,
    table: str,
    iceberg_view: IcebergView,
    snap_compare,
) -> None:  # type: ignore[no-untyped-def]
    assert snap_compare(
        DemoModeApp(theme="carbon"),
        terminal_size=(100, 40),
        run_before=partial(
            _show_profile_iceberg,
            profile=profile,
            region=region,
            database=database,
            table=table,
            view=iceberg_view,
        ),
    )


@pytest.mark.parametrize("theme", THEMES)
def test_demo_mode_renders_chip_and_seed_data(theme: str) -> None:
    """Content-presence guard. ``DEMO MODE`` text MUST appear in the
    snapshot (BrandBanner subtitle) and at least one seeded demo
    artifact MUST be rendered (proves the demo wiring actually
    landed, not just an empty shell)."""
    p = (
        Path(__file__).parent
        / "__snapshots__"
        / "test_demo_mode"
        / f"test_demo_mode_snapshot[{theme}].raw"
    )
    assert p.is_file(), f"expected snapshot {p.name}; run --snapshot-update first"
    svg = p.read_text()
    # The snapshot SVG uses HTML entity encoding (e.g., &#160; for
    # non-breaking spaces in text nodes). Decode entities and normalise
    # non-breaking spaces so plain substring checks work reliably.
    svg_plain = html_lib.unescape(svg).replace("\xa0", " ")
    # The DEMO affordance appears as either:
    # - "DEMO MODE" in the BrandBanner border_subtitle (when no overlay
    #   covers it), or
    # - "Demo mode active" in the startup advisory toast (which the app
    #   raises in on_mount when demo=True; this toast can overlap the
    #   BrandBanner bottom border in the snapshot depending on z-order).
    # Both prove the user sees a clear "you are in demo mode" signal.
    assert "DEMO MODE" in svg_plain or "Demo mode active" in svg_plain, (
        f"no DEMO affordance visible in {theme}"
    )
    assert "etl-input/" in svg_plain, f"no demo seed artifact rendered in {theme}"
    assert "athena-results/" in svg_plain, f"no Athena result bucket rendered in {theme}"
    assert "Athena" in svg_plain, f"no Athena service row rendered in {theme}"
    assert "4 obj" in svg_plain, f"no settled demo pane summary rendered in {theme}"


@pytest.mark.parametrize("theme", THEMES)
def test_demo_iceberg_snapshot_content(theme: str) -> None:
    path = (
        Path(__file__).parent
        / "__snapshots__"
        / "test_demo_mode"
        / f"test_demo_iceberg_snapshot[{theme}].raw"
    )
    svg = html_lib.unescape(path.read_text()).replace("\xa0", " ")
    assert "dev_events_iceberg" in svg
    assert "4202" in svg
    assert "4201" in svg
    assert "append" in svg
    _assert_service_tab_labels(svg, path)


@pytest.mark.parametrize(
    ("view", "required"),
    [
        ("history", "4201"),
        ("manifests", "s3://demo-dev/dev_analytics/dev_events_iceberg/met"),
        ("files", "s3://demo-dev/dev_analytics/dev_events_iceberg/dat"),
        ("partitions", "event_date=2026-07-24"),
        ("refs", "dev-main"),
    ],
)
def test_demo_iceberg_metadata_snapshot_content(view: str, required: str) -> None:
    root = Path(__file__).parent / "__snapshots__" / "test_demo_mode"
    path = root / f"test_demo_iceberg_metadata_snapshot[{view}].raw"
    svg = html_lib.unescape(path.read_text()).replace("\xa0", " ")

    assert required in svg
    _assert_service_tab_labels(svg, path)


@pytest.mark.parametrize(
    ("profile", "view", "required"),
    [
        ("demo-prod", "snapshots", ("prod_sales_iceberg", "7702", "7701")),
        (
            "demo-prod",
            "files",
            (
                "prod_sales_iceberg",
                "s3://demo-prod/prod_warehouse/prod_sales",
            ),
        ),
        (
            "demo-shared",
            "refs",
            ("shared_metrics_iceberg", "shared-main", "9902"),
        ),
    ],
)
def test_demo_iceberg_profile_snapshot_content(
    profile: str,
    view: str,
    required: tuple[str, ...],
) -> None:
    root = Path(__file__).parent / "__snapshots__" / "test_demo_mode"
    matches = tuple(root.glob(f"test_demo_iceberg_profile_snapshot[[]{profile}-*-{view}].raw"))
    assert len(matches) == 1
    path = matches[0]
    svg = html_lib.unescape(path.read_text()).replace("\xa0", " ")

    assert "dev_events_iceberg" not in svg
    assert "dev-main" not in svg
    assert "4201" not in svg
    assert "4202" not in svg
    for marker in required:
        assert marker in svg
    _assert_service_tab_labels(svg, path)
