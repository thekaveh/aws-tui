"""Real mounted Iceberg flows show bounded plans before provider admission."""

from __future__ import annotations

from xml.etree import ElementTree

import pytest

from aws_tui.ui.widgets.glue.iceberg_view import GlueIcebergView
from tests.helpers import drain_workers, wait_until
from tests.unit.ui.glue.test_iceberg_view import _build_vm, _GlueIcebergApp


def painted_text(app):
    return " ".join(
        "".join(node.itertext()).replace("\xa0", " ")
        for node in ElementTree.fromstring(app.export_screenshot()).iter()
        if node.tag.endswith("}text") and node.get("clip-path")
    )


@pytest.mark.parametrize(
    ("collection", "request_limit"),
    [
        ("snapshots", 101),
        ("history", 101),
        ("manifests", 501),
        ("files", 1001),
        ("partitions", 501),
        ("refs", 101),
    ],
)
@pytest.mark.parametrize("size", [(80, 24), (120, 40)])
async def test_initial_and_retry_read_have_visible_bounded_plan_before_provider(
    collection, request_limit, size
):
    vm, source = _build_vm()
    await vm.setup()
    app = _GlueIcebergApp(vm)
    observed = []
    original = source._load

    async def checked(view, ref):
        text = painted_text(app)
        observed.append((view, f"LIMIT {request_limit}" in text, "Athena" in text))
        assert "scan bytes or cost" in text
        return await original(view, ref)

    source._load = checked
    try:
        async with app.run_test(size=size) as pilot:
            await pilot.pause()
            assert app.query_one(GlueIcebergView).display
            await pilot.click(f"#glue-iceberg-tab-{collection}")
            await wait_until(lambda: len(source.calls) == 1, what="planned metadata read admitted")
            await drain_workers(app)
            assert observed == [(collection, True, True)]
            assert await vm.catalog.iceberg.retry()
            assert observed == [(collection, True, True)] * 2
    finally:
        await vm.shutdown()
        vm.dispose()


@pytest.mark.parametrize("size", [(80, 24), (120, 40)])
async def test_keyboard_only_snapshot_detail_pin_and_comparison_do_not_query(size):
    from textual.widgets import TextArea

    from aws_tui.vm.file_manager.pane_vm import PaneState

    vm, source = _build_vm()
    await vm.setup()

    class InfoApp(_GlueIcebergApp):
        # Match the real App's modal-first Enter routing. The base fixture
        # deliberately routes every Enter to GluePage (which cannot own a modal).
        def action_activate_enter(self):
            from aws_tui.ui.widgets.glue.iceberg_info import IcebergInfoModal

            if isinstance(self.screen, IcebergInfoModal):
                self.screen.action_commit_focused()
            else:
                super().action_activate_enter()

    app = InfoApp(vm)
    try:
        async with app.run_test(size=size) as pilot:
            # Reach the snapshots tab through actual keyboard traversal.
            for _ in range(35):
                if getattr(app.focused, "id", None) == "glue-iceberg-tab-snapshots":
                    break
                await pilot.press("tab")
            assert app.focused.id == "glue-iceberg-tab-snapshots"
            await pilot.press("enter")
            await wait_until(
                lambda: vm.catalog.iceberg.state is PaneState.IDLE, what="snapshots loaded"
            )
            await drain_workers(app)
            # Select A from the table, then reach Info without a mouse.
            for _ in range(30):
                if getattr(app.focused, "id", None) == "glue-iceberg-table":
                    break
                await pilot.press("tab")
            assert app.focused.id == "glue-iceberg-table"
            await pilot.press("home")
            a = vm.catalog.iceberg.selected_snapshot_id
            assert a is not None
            calls = list(source.calls)
            for _ in range(10):
                if getattr(app.focused, "id", None) == "glue-iceberg-info":
                    break
                await pilot.press("tab")
            assert app.focused.id == "glue-iceberg-info"
            await pilot.press("enter")
            await pilot.pause()
            body = app.screen.query_one("#iceberg-info-body", TextArea)
            assert "Historical snapshot summary" in body.text
            assert "added-records" in body.text
            assert "Raw" in body.text
            assert "Committed" in body.text
            assert "Parent" in body.text
            await pilot.press("down", "enter")  # Pin selected snapshot as A.
            assert vm.catalog.iceberg.comparison_snapshot_id == a
            await pilot.press("escape")
            for _ in range(30):
                if getattr(app.focused, "id", None) == "glue-iceberg-table":
                    break
                await pilot.press("shift+tab")
            assert app.focused.id == "glue-iceberg-table"
            await pilot.press("down")
            b = vm.catalog.iceberg.selected_snapshot_id
            assert b is not None
            assert b != a
            for _ in range(10):
                if getattr(app.focused, "id", None) == "glue-iceberg-info":
                    break
                await pilot.press("tab")
            await pilot.press("enter")
            await pilot.pause()
            await pilot.press("down", "down", "enter")
            body = app.screen.query_one("#iceberg-info-body", TextArea)
            assert f"A: {a}" in body.text
            assert f"B: {b}" in body.text
            assert "B - A" in body.text
            assert "+0 records" in body.text
            assert "Ancestry" in body.text
            assert source.calls == calls
            await pilot.press("tab", "end", "home", "escape")
    finally:
        await vm.shutdown()
        vm.dispose()


@pytest.mark.parametrize("collection", ["files", "partitions", "refs"])
async def test_current_and_reference_info_keep_raw_values_without_another_read(collection):
    from dataclasses import replace

    from textual.widgets import TextArea

    from aws_tui.ui.widgets.glue.iceberg_info import IcebergInfoModal
    from aws_tui.vm.file_manager.pane_vm import PaneState

    vm, source = _build_vm()
    source.files = (replace(source.files[0], content=999),)
    await vm.setup()
    app = _GlueIcebergApp(vm)
    try:
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.click("#glue-iceberg-tab-snapshots")
            await wait_until(
                lambda: vm.catalog.iceberg.state is PaneState.IDLE, what="snapshot loaded"
            )
            await drain_workers(app)
            vm.catalog.iceberg.select_snapshot(41)
            assert vm.catalog.iceberg.pin_comparison_snapshot()
            await pilot.click(f"#glue-iceberg-tab-{collection}")
            await wait_until(
                lambda: vm.catalog.iceberg.state is PaneState.IDLE, what="metadata loaded"
            )
            await drain_workers(app)
            calls = list(source.calls)
            await pilot.click("#glue-iceberg-info")
            await pilot.pause()
            assert isinstance(app.screen, IcebergInfoModal)
            text = app.screen.query_one(TextArea).text
            if collection == "refs":
                assert "Raw:" in text
                assert "reference" in text.lower()
            else:
                assert "CURRENT observations" in text
                assert "Historical snapshot A: 41" in text
                assert "Sample: 1" in text
                observed = vm.catalog.iceberg.observed_at_for(collection)
                assert f"Observed: {observed.isoformat()}" in text
                if collection == "files":
                    assert "999 · unknown" in text
            assert source.calls == calls
            # A background table/context transition must retire the captured dialog.
            from aws_tui.domain.data_catalog import TableFormat
            from tests.unit.vm.glue.test_iceberg_vm import OTHER_REF

            await vm.catalog.iceberg.bind_table(OTHER_REF, table_format=TableFormat.ICEBERG)
            await pilot.pause()
            assert not isinstance(app.screen, IcebergInfoModal)
    finally:
        await vm.shutdown()
        vm.dispose()


async def test_hidden_mounted_metadata_pane_cannot_admit_a_read():
    vm, source = _build_vm()
    await vm.setup()
    app = _GlueIcebergApp(vm)
    try:
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause()
            view = app.query_one(GlueIcebergView)
            view.parent.display = False
            assert not await vm.catalog.iceberg.select_view("snapshots")
            assert source.calls == []
    finally:
        await vm.shutdown()
        vm.dispose()


@pytest.mark.parametrize("collection", ["files", "partitions"])
async def test_empty_current_observation_is_reachable(collection):
    from textual.widgets import TextArea

    from aws_tui.vm.file_manager.pane_vm import PaneState

    vm, source = _build_vm()
    setattr(source, collection, ())
    await vm.setup()
    app = _GlueIcebergApp(vm)
    try:
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.click(f"#glue-iceberg-tab-{collection}")
            await wait_until(
                lambda: vm.catalog.iceberg.observed_at_for(collection) is not None,
                what="empty observation completed",
            )
            assert vm.catalog.iceberg.state is PaneState.EMPTY
            await drain_workers(app)
            assert app.query_one(GlueIcebergView).info_available
            await pilot.click("#glue-iceberg-info")
            text = app.screen.query_one(TextArea).text
            assert "Sample: 0" in text
            assert "Min: unknown" in text
    finally:
        await vm.shutdown()
        vm.dispose()


async def test_raw_file_codes_cover_all_fetched_rows_before_local_pagination():
    from dataclasses import replace

    from textual.widgets import TextArea

    vm, source = _build_vm()
    source.files = tuple(
        replace(source.files[0], file_path=f"s3://bucket/{i:03}", content=999 if i == 59 else 0)
        for i in range(60)
    )
    await vm.setup()
    app = _GlueIcebergApp(vm)
    try:
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.click("#glue-iceberg-tab-files")
            await wait_until(lambda: vm.catalog.iceberg.fetched_count == 60, what="60files fetched")
            await drain_workers(app)
            assert len(vm.catalog.iceberg.items) == 50
            await pilot.click("#glue-iceberg-info")
            text = app.screen.query_one(TextArea).text
            assert "Sample: 60" in text
            assert "999 · unknown" in text
    finally:
        await vm.shutdown()
        vm.dispose()


async def test_info_invalidated_before_compose_cannot_render_or_call_captured_metadata():
    from aws_tui.ui.widgets.glue.iceberg_info import IcebergInfoModal

    vm, source = _build_vm()
    await vm.setup()
    await vm.catalog.iceberg.select_view("snapshots")
    app = _GlueIcebergApp(vm)
    try:
        async with app.run_test(size=(80, 24)) as pilot:
            await wait_until(
                lambda: vm.catalog.iceberg.selected_snapshot_id is not None,
                what="preloaded snapshot selected",
            )
            calls = list(source.calls)
            view = app.query_one(GlueIcebergView)
            view.show_info()
            vm.catalog.iceberg.clear_table()
            await pilot.pause()
            assert not isinstance(app.screen, IcebergInfoModal)
            assert source.calls == calls
    finally:
        await vm.shutdown()
        vm.dispose()


@pytest.mark.parametrize("offset", [5, 100])
async def test_clipped_plan_cannot_admit_a_read(offset):
    from textual.widgets import Static

    vm, source = _build_vm()
    await vm.setup()
    app = _GlueIcebergApp(vm)
    try:
        async with app.run_test(size=(80, 30)) as pilot:
            await pilot.pause()
            app.query_one("#glue-iceberg-footer", Static).styles.offset = (0, offset)
            await pilot.pause()
            assert not await vm.catalog.iceberg.select_view("snapshots")
            assert source.calls == []
    finally:
        await vm.shutdown()
        vm.dispose()


async def test_info_control_label_is_painted_in_full_on_narrow_screen():
    from aws_tui.vm.file_manager.pane_vm import PaneState

    vm, _source = _build_vm()
    await vm.setup()
    app = _GlueIcebergApp(vm)
    try:
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.click("#glue-iceberg-tab-snapshots")
            await wait_until(
                lambda: vm.catalog.iceberg.state is PaneState.IDLE, what="snapshot ready"
            )
            await drain_workers(app)
            await pilot.pause()
            assert "Info" in painted_text(app)
    finally:
        await vm.shutdown()
        vm.dispose()
