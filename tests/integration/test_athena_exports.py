import asyncio
import contextlib
import csv
import io
import json

import pytest
from textual.widgets import Input, Select, Static, Switch

from aws_tui.app import AwsTuiApp
from aws_tui.composition import build_app_context
from aws_tui.demo.in_memory_fs import InMemoryFS
from aws_tui.domain.query import ResultColumn, ResultPage
from aws_tui.infra.aws_session import TokenProbeResult, TokenState
from aws_tui.infra.keymap_store import KeymapStore
from aws_tui.ui.widgets.athena.export_modal import AthenaExportModal
from aws_tui.ui.widgets.athena.page import AthenaPage
from aws_tui.vm.chrome.focus_coordinator_vm import FocusSlot
from tests.helpers import focus_and_settle, wait_until
from tests.integration.test_athena_page import _mounted_athena_app
from tests.integration.test_glue_page import open_service
from tests.unit.vm.athena.test_page_vm import PageClient


async def open_export(app, page, pilot):
    await page.results.load("history-primary", from_history=True)
    await page.select_view("results")
    await pilot.pause()
    await focus_and_settle(app.query_one("#athena-export-results"))
    await pilot.press("enter")
    await wait_until(lambda: bool(app.screen.query("#athena-export-path")), what="export form")
    await pilot.pause()
    return app.screen


@pytest.mark.parametrize("format", ["csv", "json"])
async def test_loaded_results_export_requires_review_and_preserves_projection(tmp_path, format):
    async with _mounted_athena_app(tmp_path) as (app, _ctx, page, client, pilot):

        async def data(_execution_id, *, start_token=None):
            return ResultPage(
                (ResultColumn("value", "varchar", "NULLABLE"),),
                (("one",), ("two",), ("one again",)),
                None,
            )

        client.get_results_page = data
        modal = await open_export(app, page, pilot)
        page.results.set_filter("one")
        path = tmp_path / f"result.{format}"
        modal.query_one("#athena-export-path", Input).value = str(path)
        modal.query_one("#athena-export-format", Select).value = format
        await focus_and_settle(modal.query_one("#athena-export-review"))
        await pilot.press("enter")
        await wait_until(lambda: page.exports.review is not None, what="export review")
        assert not path.exists()
        assert "Loaded view" in str(modal.query_one("#athena-export-summary", Static).render())
        expected = page.results.visible_rows
        assert expected
        page.results.set_filter("two")  # The reviewed projection is immutable.
        await focus_and_settle(modal.query_one("#athena-export-write"))
        await pilot.press("enter")
        await wait_until(lambda: page.exports.receipt is not None, what="published export")
        if format == "json":
            assert json.loads(path.read_text())["rows"] == [list(row) for row in expected]
        else:
            rows = list(csv.reader(io.StringIO(path.read_text())))
            assert rows[0] == [column.name for column in page.results.columns]
            assert len(rows) == len(expected) + 1
        assert client.start_calls == []
        await pilot.press("escape")


async def test_cancelled_export_review_creates_no_file(tmp_path):
    async with _mounted_athena_app(tmp_path) as (app, _ctx, page, client, pilot):
        modal = await open_export(app, page, pilot)
        path = tmp_path / "cancelled.csv"
        modal.query_one("#athena-export-path", Input).value = str(path)
        await focus_and_settle(modal.query_one("#athena-export-review"))
        await pilot.press("enter")
        await wait_until(lambda: page.exports.review is not None, what="export review")
        await pilot.press("escape")
        assert not path.exists()
        assert client.start_calls == []


async def review_export(modal, page, pilot, path):
    modal.query_one("#athena-export-path", Input).value = str(path)
    await focus_and_settle(modal.query_one("#athena-export-review"))
    await pilot.press("enter")
    await wait_until(
        lambda: not page.exports.busy and page.exports.review is not None,
        what="ready export review",
    )
    await pilot.pause()


@pytest.mark.parametrize("format", ["csv", "json", "original"])
async def test_complete_export_ignores_projection_and_uses_original_execution(tmp_path, format):
    async with _mounted_athena_app(tmp_path) as (app, _ctx, page, client, pilot):
        columns = (ResultColumn("duplicate", "varchar", "NULLABLE"),) * 2
        calls = []

        async def data(execution_id, *, start_token=None):
            calls.append((execution_id, start_token))
            return ResultPage(
                columns,
                ((None, ""), ("\\N", "one")) if start_token is None else (("two", "three"),),
                "second" if start_token is None else None,
            )

        artifacts = []

        async def original(detail, *, max_bytes, chunk_bytes):
            artifacts.append(detail)
            yield b"original AWS bytes\n"

        client.get_results_page = data
        client.iter_result_artifact = original
        modal = await open_export(app, page, pilot)
        page.results.set_filter("one")
        assert len(page.results.visible_rows) == 1
        calls.clear()
        modal.query_one("#athena-export-scope", Select).value = "complete"
        modal.query_one("#athena-export-format", Select).value = format
        path = tmp_path / "complete.data"
        await review_export(modal, page, pilot, path)
        summary = str(modal.query_one("#athena-export-summary", Static).render())
        assert "Unknown" in summary
        assert "history-primary" in summary
        await focus_and_settle(modal.query_one("#athena-export-write"))
        await pilot.press("enter")
        await wait_until(lambda: page.exports.receipt is not None, what="complete export")
        if format == "original":
            assert path.read_bytes() == b"original AWS bytes\n"
            assert artifacts[0].summary.ref.execution_id == "history-primary"
            assert calls == []
        else:
            assert calls == [("history-primary", None), ("history-primary", "second")]
            assert page.exports.receipt.rows == 3
            if format == "json":
                value = json.loads(path.read_text())
                assert [column["name"] for column in value["columns"]] == ["duplicate", "duplicate"]
                assert value["rows"] == [[None, ""], ["\\N", "one"], ["two", "three"]]
        assert client.start_calls == []
        await pilot.press("escape")


@pytest.mark.parametrize("changed", [False, True])
async def test_existing_export_requires_explicit_versioned_replacement(tmp_path, changed):
    async with _mounted_athena_app(tmp_path) as (app, _ctx, page, client, pilot):
        modal = await open_export(app, page, pilot)
        path = tmp_path / "existing.csv"
        path.write_bytes(b"prior")
        await review_export(modal, page, pilot, path)
        write = modal.query_one("#athena-export-write")
        assert write.disabled
        assert path.read_bytes() == b"prior"
        toggle = modal.query_one("#athena-export-replace", Switch)
        await focus_and_settle(toggle)
        await pilot.press("space")
        if changed:
            path.write_bytes(b"external changed bytes")
        await focus_and_settle(write)
        await pilot.press("enter")
        await wait_until(
            lambda: not page.exports.busy and modal._review is None, what="export settled"
        )
        if changed:
            assert page.exports.receipt is None
            assert path.read_bytes() == b"external changed bytes"
        else:
            assert page.exports.receipt is not None
            assert path.read_bytes() != b"prior"
        assert client.start_calls == []
        await pilot.press("escape")


@pytest.mark.parametrize("first_attempt", ["success", "changed", "failure"])
async def test_new_review_requires_fresh_replacement_acknowledgment(
    tmp_path, monkeypatch, first_attempt
):
    async with _mounted_athena_app(tmp_path) as (app, _ctx, page, client, pilot):
        modal = await open_export(app, page, pilot)
        first = tmp_path / "first.csv"
        first.write_bytes(b"first prior")
        second = tmp_path / "second.csv" if first_attempt == "success" else first
        if second != first:
            second.write_bytes(b"second prior")
        await review_export(modal, page, pilot, first)
        toggle = modal.query_one("#athena-export-replace", Switch)
        write = modal.query_one("#athena-export-write")
        assert not toggle.value
        assert write.disabled
        await focus_and_settle(toggle)
        await pilot.press("space")
        assert toggle.value
        assert not write.disabled
        if first_attempt == "changed":
            first.write_bytes(b"external changed version")
        with monkeypatch.context() as patch:
            if first_attempt == "failure":

                def fail_begin(*_args, **_kwargs):
                    raise OSError("private destination failure")

                patch.setattr(page.exports._files, "begin", fail_begin)
            await focus_and_settle(write)
            await pilot.press("enter")
            await wait_until(
                lambda: not page.exports.busy and modal._review is None,
                what="first export attempt settled",
            )
        if first_attempt == "success":
            assert page.exports.receipt is not None
            assert first.read_bytes() != b"first prior"
        else:
            assert page.exports.receipt is None
            assert first.read_bytes() == (
                b"external changed version" if first_attempt == "changed" else b"first prior"
            )
        # Consuming the old review must retire its acknowledgment immediately.
        assert not toggle.value
        prior = second.read_bytes()
        await review_export(modal, page, pilot, second)
        assert page.exports.review.destination.path == second
        assert not toggle.value
        assert write.disabled
        await pilot.click("#athena-export-write")
        assert second.read_bytes() == prior
        assert page.exports.receipt is None
        await focus_and_settle(toggle)
        await pilot.press("space")
        assert not write.disabled
        await focus_and_settle(write)
        await pilot.press("enter")
        await wait_until(
            lambda: page.exports.receipt is not None, what="freshly acknowledged export"
        )
        assert second.read_bytes() != prior
        assert client.start_calls == []
        await pilot.press("escape")


async def test_context_change_invalidates_export_review(tmp_path):
    async with _mounted_athena_app(tmp_path) as (app, _ctx, page, client, pilot):
        modal = await open_export(app, page, pilot)
        path = tmp_path / "stale.csv"
        await review_export(modal, page, pilot, path)
        await page.select_workgroup("analysts")
        await wait_until(lambda: app.screen is not modal, what="stale export dismissed")
        assert page.exports.review is None
        assert not path.exists()
        assert client.start_calls == []


@pytest.mark.parametrize("route", ["key", "remap", "palette"])
async def test_export_action_available_through_real_app_routes(tmp_path, route):
    keymap = KeymapStore(overlay={"athena.export_results": "alt+z"}) if route == "remap" else None
    async with _mounted_athena_app(tmp_path, keymap=keymap) as (app, ctx, page, _client, pilot):
        await page.results.load("history-primary", from_history=True)
        await page.select_view("results")
        await pilot.pause()
        await focus_and_settle(app.query_one("#athena-results-table"))
        if route == "palette":
            await pilot.press("colon", *"Export Athena results to a local file")
            await pilot.pause()
            assert [entry.id for entry in ctx.command_palette_vm.filtered_entries] == [
                "athena.export_results"
            ]
            await pilot.press("enter")
        else:
            await pilot.press("alt+z" if route == "remap" else "alt+x")
        await wait_until(
            lambda: isinstance(app.screen, AthenaExportModal), what="export action modal"
        )
        await pilot.press("escape")


@pytest.mark.parametrize("size", [(80, 24), (120, 40)])
async def test_export_controls_fit_small_terminal_and_focus_ring(tmp_path, size):
    async with _mounted_athena_app(tmp_path) as (app, _ctx, page, _client, pilot):
        await pilot.resize_terminal(*size)
        modal = await open_export(app, page, pilot)
        await review_export(modal, page, pilot, tmp_path / "fit.csv")
        page_widget = app.query_one(AthenaPage)
        assert any(
            slot == FocusSlot.ATHENA_EXPORT
            for slot, _widget in page_widget._active_surface_focus_targets()
        )
        for key in ("cancel", "review", "write"):
            button = modal.query_one(f"#athena-export-{key}")
            assert button.region.width > 0
            assert button.region.height == 3
            assert button.region.bottom <= size[1]
            assert button.region.right <= size[0]
        await pilot.press("escape")


@pytest.mark.parametrize("leave", [False, True])
async def test_cancel_or_leave_during_complete_export_never_publishes_partial_output(
    tmp_path, leave
):
    async with _mounted_athena_app(tmp_path) as (app, ctx, page, client, pilot):
        modal = await open_export(app, page, pilot)
        path = tmp_path / "partial.csv"
        modal.query_one("#athena-export-scope", Select).value = "complete"
        await review_export(modal, page, pilot, path)
        entered, closed = asyncio.Event(), asyncio.Event()

        async def blocked(_execution_id, *, start_token=None):
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                closed.set()

        client.get_results_page = blocked
        await focus_and_settle(modal.query_one("#athena-export-write"))
        await pilot.press("enter")
        await asyncio.wait_for(entered.wait(), 3)
        if leave:
            ctx.root_vm.services_menu.switch_service_command.execute("s3")
        else:
            await pilot.press("escape")
        await asyncio.wait_for(closed.wait(), 3)
        await wait_until(lambda: not page.exports.busy, what="export cancellation drained")
        assert not path.exists()
        assert not list(tmp_path.glob(".aws-tui-export-*"))
        assert client.start_calls == []
        if not leave:
            await pilot.press("escape")


async def test_protected_real_connection_allows_read_only_complete_export(tmp_path, monkeypatch):
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "config.toml").write_text(
        '[connections.synthetic]\nkind="aws"\nprofile="synthetic"\nregion="us-east-1"\nprotected=true\n[defaults]\nconnection="synthetic"\n'
    )
    aws_config = tmp_path / "aws-config"
    aws_config.write_text("[profile synthetic]\nregion=us-east-1\n")
    monkeypatch.setenv("AWS_CONFIG_FILE", str(aws_config))
    monkeypatch.setenv("AWS_SHARED_CREDENTIALS_FILE", str(tmp_path / "absent"))
    client = PageClient(connection_name="synthetic", region="us-east-1")
    ctx = build_app_context(config_dir=config_dir, cache_dir=tmp_path / "cache")
    ctx.registry.get("s3")._s3_fs_factory = lambda _connection: InMemoryFS()
    ctx.registry.get("athena")._client_factory = lambda _connection: client
    monkeypatch.setattr(
        ctx.aws_session, "probe_token", lambda _connection: TokenProbeResult(TokenState.CONNECTED)
    )
    app = AwsTuiApp(ctx)
    try:
        async with app.run_test(size=(80, 24)) as pilot:
            await open_service(ctx, pilot, "athena")
            page = ctx.root_vm.content_host.current
            assert ctx.root_vm.active_connection.protected
            modal = await open_export(app, page, pilot)
            modal.query_one("#athena-export-scope", Select).value = "complete"
            path = tmp_path / "protected.json"
            modal.query_one("#athena-export-format", Select).value = "json"
            await review_export(modal, page, pilot, path)
            await focus_and_settle(modal.query_one("#athena-export-write"))
            await pilot.press("enter")
            await wait_until(lambda: page.exports.receipt is not None, what="protected read export")
            assert json.loads(path.read_text())["rows"] == [["1"]]
            assert client.start_calls == []
            await pilot.press("escape")
    finally:
        with contextlib.suppress(Exception):
            await ctx.root_vm.content_host.shutdown()
        with contextlib.suppress(Exception):
            ctx.root_vm.dispose()
        ctx.log_sink.close()
