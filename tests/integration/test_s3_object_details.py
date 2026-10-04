"""Real app routes and literal read-only current-object inspection."""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from textual.widgets import DataTable, TextArea

from aws_tui.app import AwsTuiApp
from aws_tui.domain.filesystem import PathRef
from aws_tui.domain.s3_object_details import S3ObjectDetails
from aws_tui.infra.clipboard import InMemoryClipboard
from aws_tui.infra.keymap_store import KeymapStore
from aws_tui.ui.widgets.modal_button import ModalButton
from aws_tui.ui.widgets.nav_menu import NavMenu
from aws_tui.ui.widgets.pane import EntryRow, Pane
from tests.helpers import drain_workers, wait_until
from tests.integration.conftest import AppContextBuilder
from tests.s3_object_details_support import DetailsInMemoryFS


async def stream() -> AsyncIterator[bytes]:
    yield b"payload"


async def make_app(factory: AppContextBuilder, **kwargs):
    fs = DetailsInMemoryFS()
    await fs.mkdir(PathRef(("bucket",)))
    await fs.mkdir(PathRef(("bucket", "prefix")))
    for name in ("bucket/a.txt", "bucket/b.txt", "bucket/prefix/c.txt"):
        await fs.write_stream(PathRef(tuple(name.split("/"))), stream())
    ctx = factory(fs=fs, **kwargs)
    ctx.config_store.path.write_text(
        '[defaults]\nconnection = "test"\n\n[connections.test]\nkind = "s3-compatible"\nendpoint_url = "http://localhost:9000"\ncredentials = "static"\naccess_key_id = "k"\nsecret_access_key = "s"\nregion = "us-east-1"\n'
    )
    return AwsTuiApp(ctx), fs, ctx


async def select_file(app, pilot):
    await drain_workers(app)
    pane = app.query_one("#pane-left", Pane)
    await pane.vm.navigate_to(PathRef(("bucket",)))
    await wait_until(
        lambda: any(row.entry_vm.name == "a.txt" for row in pane.query(EntryRow)),
        what="object rows",
    )
    pane.vm.move_cursor_to(
        next(i for i, row in enumerate(pane.vm.filtered_entries) if row.name == "a.txt")
    )
    app.set_focus(None)
    await pilot.pause()
    return pane


async def opened(app, pilot):
    from aws_tui.ui.widgets.s3_object_details import S3ObjectDetailsModal
    from aws_tui.vm.file_manager.s3_object_details_vm import S3ObjectDetailsState

    await pilot.press("ctrl+o")
    await wait_until(lambda: isinstance(app.screen, S3ObjectDetailsModal), what="details mounted")
    screen = app.screen
    await wait_until(lambda: screen.vm.state is S3ObjectDetailsState.READY, what="details ready")
    return screen


async def test_ctrl_o_inspects_current_file_and_restores_origin(app_context_factory):
    app, fs, _ = await make_app(app_context_factory)
    fs.queue_details(S3ObjectDetails(bucket="bucket", key="a.txt", content_type="text/plain"))
    async with app.run_test(size=(120, 40)) as pilot:
        pane = await select_file(app, pilot)
        before = app.focused
        row = pane.vm.selected_entry
        rendered_row = next(item for item in pane.query(EntryRow) if item.entry_vm is row)
        slot = app._app_ctx.focus_coordinator.focused_slot
        assert fs.details_paths == []
        screen = await opened(app, pilot)
        assert screen.query_one("#s3-details-fields", DataTable).row_count == 13
        assert fs.details_paths == [PathRef(("bucket", "a.txt"))]
        await pilot.press("escape")
        await wait_until(lambda: app.screen is not screen, what="details close")
        assert app.focused is before
        assert pane.vm.selected_entry is row
        assert next(item for item in pane.query(EntryRow) if item.entry_vm is row) is rendered_row
        assert app._app_ctx.focus_coordinator.focused_slot is slot
        assert fs.details_paths == [PathRef(("bucket", "a.txt"))]


async def test_real_navigation_focus_refuses_previously_selected_file(app_context_factory):
    app, fs, ctx = await make_app(app_context_factory)
    async with app.run_test(size=(120, 40)) as pilot:
        await select_file(app, pilot)
        nav = app.query_one(NavMenu)
        nav.focus()
        await pilot.pause()
        assert app.focused is nav
        await pilot.press("ctrl+o")
        await pilot.pause()
        assert len(app.screen_stack) == 1
        assert fs.details_paths == []
        assert "focused S3 object" in ctx.root_vm.chrome.toast_stack.toasts[-1].model.text


async def test_real_configured_connection_choice_refuses_previous_object(app_context_factory):
    from aws_tui.ui.widgets.first_run import ConnectionChoice, FirstRunConnectionList

    app, fs, ctx = await make_app(app_context_factory)
    async with app.run_test(size=(120, 40)) as pilot:
        pane = await select_file(app, pilot)
        previous_entry = pane.vm.selected_entry
        previous_row = next(row for row in pane.query(EntryRow) if row.entry_vm is previous_entry)
        snapshot = ctx.connection_resolver.discover()
        assert snapshot.invalid_sources == ()
        assert len(snapshot.connections) == 1
        connection = snapshot.connections[0]
        assert (connection.name, connection.source, connection.kind) == (
            "test",
            "config",
            "s3-compatible",
        )
        assert connection.endpoint_url == "http://localhost:9000"
        await app.query_one(NavMenu).show_first_run_connections(snapshot)
        choice = app.query_one(ConnectionChoice)
        assert isinstance(choice.parent, FirstRunConnectionList)
        assert choice.connection_name == connection.name
        assert choice._source == connection.source
        choice.focus()
        await pilot.pause()
        assert app.focused is choice
        assert choice.has_focus
        assert choice.is_attached
        assert pane.vm.selected_entry is previous_entry
        assert previous_entry.name == "a.txt"
        assert previous_row.is_attached
        assert fs.details_paths == []
        await pilot.press("ctrl+o")
        await pilot.pause()
        assert app.focused is choice
        assert len(app.screen_stack) == 1
        assert pane.vm.selected_entry is previous_entry
        assert fs.details_paths == []
        toast = ctx.root_vm.chrome.toast_stack.toasts[-1]
        assert toast.model.id == "s3-details-unavailable"
        assert "Select a focused S3 object" in toast.model.text


@pytest.mark.parametrize(
    "value", ['[bold]雪[/bold] "quote"\n' + "x" * 9000, ""], ids=["long-literal", "empty"]
)
async def test_selected_full_literal_value_copies_losslessly(app_context_factory, value):
    clipboard = InMemoryClipboard()
    app, fs, _ = await make_app(app_context_factory, clipboard=clipboard)
    fs.queue_details(S3ObjectDetails(bucket="bucket", key="a.txt", content_type=value))
    async with app.run_test(size=(80, 24)) as pilot:
        await select_file(app, pilot)
        screen = await opened(app, pilot)
        assert screen.query_one("#s3-details-value", TextArea).text == (value or "(empty)")
        await pilot.press("ctrl+c")
        await wait_until(lambda: clipboard.last is not None, what="literal clipboard")
        assert clipboard.last == value
        assert app.screen is screen
        assert app._crash_report is None


@pytest.mark.parametrize("target", ["bucket", "prefix", "parent", "empty", "local"])
async def test_contextual_non_object_refusal_is_visible_and_zero_read(app_context_factory, target):
    from aws_tui.vm.chrome.focus_coordinator_vm import FocusSlot
    from aws_tui.vm.file_manager.dual_pane_vm import FocusedPane

    app, fs, ctx = await make_app(app_context_factory)
    async with app.run_test(size=(120, 40)) as pilot:
        pane = await select_file(app, pilot)
        if target == "bucket":
            await pane.vm.navigate_to(PathRef(()))
            name = "bucket"
        elif target == "prefix":
            name = "prefix"
        elif target == "parent":
            name = ".."
        elif target == "empty":
            await fs.mkdir(PathRef(("empty",)))
            await pane.vm.navigate_to(PathRef(("empty",)))
            pane.vm.set_filter_command.execute("absent")
            name = None
        else:
            app._dual_pane().set_focused(FocusedPane.RIGHT)
            ctx.focus_coordinator.set_focused_slot(FocusSlot.S3_RIGHT)
            name = None
        if name is not None:
            pane.vm.move_cursor_to(
                next(i for i, row in enumerate(pane.vm.filtered_entries) if row.name == name)
            )
        await pilot.pause()
        app.set_focus(None)
        await pilot.press("ctrl+o")
        await pilot.pause()
        assert len(app.screen_stack) == 1
        assert fs.details_paths == []
        assert "focused S3 object" in ctx.root_vm.chrome.toast_stack.toasts[-1].model.text


@pytest.mark.parametrize("route", ["palette", "commands"])
async def test_palette_and_commands_defer_until_dismissal(app_context_factory, route):
    from textual.widgets import Input

    from aws_tui.ui.widgets.command_palette import CommandPalette
    from aws_tui.ui.widgets.hint_legend import HintLegend
    from aws_tui.ui.widgets.s3_object_details import S3ObjectDetailsModal

    app, fs, ctx = await make_app(app_context_factory)
    fs.queue_details(S3ObjectDetails(bucket="bucket", key="a.txt", content_type="text/plain"))
    async with app.run_test(size=(120, 40)) as pilot:
        pane = await select_file(app, pilot)
        original = pane.vm.selected_entry
        if route == "palette":
            await pilot.press("ctrl+k")
        else:
            legend = app.query_one(HintLegend)
            chip = next(
                chip
                for chip in legend.query(".hint-chip")
                if chip.action.action_id == "app.command_palette"
            )
            await pilot.click(chip)
        await wait_until(lambda: isinstance(app.screen, CommandPalette), what="command palette")
        app.screen.query_one(Input).value = "S3 object details"
        await pilot.pause()
        await pilot.press("enter")
        await wait_until(
            lambda: isinstance(app.screen, S3ObjectDetailsModal), what="deferred details"
        )
        await drain_workers(app)
        assert fs.details_paths == [PathRef(("bucket", "a.txt"))]
        assert len(app.screen_stack) == 2
        await pilot.press("escape")
        await pilot.pause()
        assert pane.vm.selected_entry is original
        assert ctx.focus_coordinator.is_modal is False


async def test_palette_revalidates_captured_origin(app_context_factory):
    from textual.widgets import Input

    app, fs, _ = await make_app(app_context_factory)
    async with app.run_test(size=(120, 40)) as pilot:
        pane = await select_file(app, pilot)
        await pilot.press("ctrl+k")
        await pilot.pause()
        pane.vm.move_cursor_to(
            next(i for i, row in enumerate(pane.vm.filtered_entries) if row.name == "b.txt")
        )
        app.screen.query_one(Input).value = "S3 object details"
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()
        assert len(app.screen_stack) == 1
        assert fs.details_paths == []


async def test_custom_remap_and_modal_containment_prevent_stacking(app_context_factory):
    from aws_tui.ui.widgets.s3_object_details import S3ObjectDetailsModal

    app, fs, ctx = await make_app(app_context_factory)
    ctx.config_store.path.write_text(
        ctx.config_store.path.read_text() + '\n[keybindings]\n"pane.object_details" = ["ctrl+i"]\n'
    )
    ctx.keymap_store = KeymapStore(overlay={"pane.object_details": "ctrl+i"})
    app = AwsTuiApp(ctx)
    fs.queue_details(S3ObjectDetails(bucket="bucket", key="a.txt", content_type="text/plain"))
    async with app.run_test(size=(120, 40)) as pilot:
        pane = await select_file(app, pilot)
        row = pane.vm.selected_entry
        await pilot.press("ctrl+o")
        await pilot.pause()
        assert len(app.screen_stack) == 1
        await pilot.press("ctrl+i")
        await wait_until(
            lambda: isinstance(app.screen, S3ObjectDetailsModal), what="remapped details"
        )
        await drain_workers(app)
        app.action_object_details()
        await pilot.press("ctrl+i", "c", "d", "p", "r", "space", "ctrl+k")
        await pilot.pause()
        assert len(app.screen_stack) == 2
        assert pane.vm.selected_entry is row
        assert len(fs.details_paths) == 1
        for _ in range(4):
            await pilot.press("tab")
            if isinstance(app.focused, ModalButton) and app.focused.button_id == "close":
                break
        assert isinstance(app.focused, ModalButton)
        assert app.focused.button_id == "close"
        await pilot.press("enter")
        await pilot.pause()
        assert len(app.screen_stack) == 1


@pytest.mark.parametrize("away_back", [False, True])
async def test_late_selection_reply_never_overwrites_current_object(app_context_factory, away_back):
    from aws_tui.ui.widgets.s3_object_details import S3ObjectDetailsModal
    from aws_tui.vm.file_manager.s3_object_details_vm import S3ObjectDetailsState
    from tests.s3_object_details_support import DetailsReadBarrier

    stale = DetailsReadBarrier(
        S3ObjectDetails(bucket="bucket", key="a.txt", content_type="STALE"),
        cancellation_resistant=True,
    )
    app, fs, _ = await make_app(app_context_factory)
    fs.queue_details(stale, S3ObjectDetails(bucket="bucket", key="b.txt", content_type="CURRENT"))
    async with app.run_test(size=(120, 40)) as pilot:
        pane = await select_file(app, pilot)
        await pilot.press("ctrl+o")
        await wait_until(stale.entered.is_set, what="held details read")
        screen = app.screen
        assert isinstance(screen, S3ObjectDetailsModal)
        pane.vm.move_cursor_to(
            next(i for i, row in enumerate(pane.vm.filtered_entries) if row.name == "b.txt")
        )
        if away_back:
            pane.vm.move_cursor_to(
                next(i for i, row in enumerate(pane.vm.filtered_entries) if row.name == "a.txt")
            )
        await wait_until(
            lambda: screen.vm.state is S3ObjectDetailsState.READY, what="current details response"
        )
        stale.release.set()
        await wait_until(stale.finished.is_set, what="stale reply drained")
        assert screen.query_one(TextArea).text == "CURRENT"
        assert "STALE" not in str(screen.query_one(DataTable).get_row_at(0))
        assert len(fs.details_paths) == 2
        await pilot.press("escape")
        await pilot.pause()
        assert screen.vm.state is S3ObjectDetailsState.CLOSED
        assert screen._subscription is None


@pytest.mark.parametrize("stage", ["ready", "held"])
@pytest.mark.parametrize("replacement", ["source", "pane-source", "page"])
async def test_real_source_or_page_replacement_invalidates_details(
    app_context_factory, stage, replacement
):
    from aws_tui.infra.aws_session import TokenState
    from aws_tui.infra.connection_resolver import Connection
    from aws_tui.ui.widgets.s3_object_details import S3ObjectDetailsModal
    from aws_tui.vm.file_manager.s3_object_details_vm import S3ObjectDetailsState
    from tests.s3_object_details_support import DetailsReadBarrier

    outcome = S3ObjectDetails(bucket="bucket", key="a.txt", content_type="OLD SOURCE")
    barrier = DetailsReadBarrier(outcome)
    app, fs, ctx = await make_app(app_context_factory)
    fs.queue_details(barrier if stage == "held" else outcome)
    async with app.run_test(size=(120, 40)) as pilot:
        old = await select_file(app, pilot)
        await pilot.press("ctrl+o")
        await wait_until(
            lambda: isinstance(app.screen, S3ObjectDetailsModal), what="details mounted"
        )
        screen = app.screen
        if stage == "held":
            await wait_until(barrier.entered.is_set, what="held source read")
        else:
            await wait_until(
                lambda: screen.vm.state is S3ObjectDetailsState.READY, what="old source ready"
            )
        if replacement == "source":
            await ctx.root_vm.switch_connection_and_service(
                Connection(
                    name="replacement",
                    kind="s3-compatible",
                    endpoint_url="http://localhost:9001",
                    source="config",
                    access_key_id="k",
                    secret_access_key="s",
                    region="us-east-1",
                ),
                TokenState.CONNECTED,
                "s3",
            )
        elif replacement == "pane-source":
            await app.action_swap_source()
        else:
            app.action_open_settings()
            await pilot.pause()
        await wait_until(
            lambda: screen.vm.state is S3ObjectDetailsState.CLOSED, what="source invalidation"
        )
        assert screen.vm.fields == ()
        if stage == "held":
            barrier.release.set()
            await wait_until(barrier.finished.is_set, what="old source read drained")
        await wait_until(lambda: app.screen is not screen, what="stale inspector dismissal")
        assert app.focused is None or old not in app.focused.ancestors_with_self
        await wait_until(lambda: screen._subscription is None, what="details subscription released")
        assert fs.details_paths == [PathRef(("bucket", "a.txt"))]


async def test_resistant_source_replacement_drains_without_stale_publication(app_context_factory):
    import asyncio

    from aws_tui.infra.aws_session import TokenState
    from aws_tui.infra.connection_resolver import Connection
    from aws_tui.ui.widgets.help_modal import HelpModal
    from aws_tui.ui.widgets.s3_object_details import S3ObjectDetailsModal
    from aws_tui.vm.file_manager.s3_object_details_vm import S3ObjectDetailsState
    from tests.s3_object_details_support import DetailsReadBarrier

    barrier = DetailsReadBarrier(
        S3ObjectDetails(bucket="bucket", key="a.txt", content_type="STALE SOURCE"),
        cancellation_resistant=True,
    )
    clipboard = InMemoryClipboard()
    app, fs, ctx = await make_app(app_context_factory, clipboard=clipboard)
    fs.queue_details(barrier)
    async with app.run_test(size=(120, 40)) as pilot:
        switch = None
        try:
            old_pane = await select_file(app, pilot)
            old_dual = app._dual_pane()
            old_provider = old_pane.vm.provider
            old_source = old_pane.vm.current_connection_key
            await pilot.press("ctrl+o")
            await wait_until(barrier.entered.is_set, what="resistant source read entered")
            screen = app.screen
            assert isinstance(screen, S3ObjectDetailsModal)
            replacement = Connection(
                name="replacement",
                kind="s3-compatible",
                source="config",
                endpoint_url="http://localhost:9001",
                access_key_id="k",
                secret_access_key="s",
                region="us-east-1",
            )
            switch = asyncio.create_task(
                ctx.root_vm.switch_connection_and_service(replacement, TokenState.CONNECTED, "s3")
            )
            await wait_until(
                lambda: screen.vm.state is S3ObjectDetailsState.CLOSED,
                what="resistant old source invalidated",
            )
            await wait_until(lambda: app.screen is not screen, what="old inspector dismissed")
            await wait_until(barrier.cancelled.is_set, what="resistant read cancellation requested")
            assert not barrier.finished.is_set()
            assert screen.vm.fields == ()
            assert ctx.root_vm.active_connection is replacement
            assert ctx.root_vm.content_host.current is not old_dual
            assert old_pane.vm.provider is old_provider
            assert old_pane.vm.current_connection_key == old_source
            await pilot.press("question_mark")
            await wait_until(lambda: isinstance(app.screen, HelpModal), what="current UI usable")
            await pilot.press("escape")
            await pilot.pause()
            barrier.release.set()
            await wait_until(barrier.finished.is_set, what="resistant old source reply drained")
            await switch
            await drain_workers(app)
            await pilot.pause()
            assert not isinstance(app.screen, S3ObjectDetailsModal)
            assert screen.vm.state is S3ObjectDetailsState.CLOSED
            assert screen.vm.fields == ()
            assert screen._subscription is None
            assert screen._source_subscription is None
            assert clipboard.writes == []
            assert all(
                "STALE SOURCE" not in toast.model.text
                for toast in ctx.root_vm.chrome.toast_stack.toasts
            )
            assert fs.details_paths == [PathRef(("bucket", "a.txt"))]
            assert app._crash_report is None
        finally:
            barrier.release.set()
            if barrier.entered.is_set():
                await wait_until(barrier.finished.is_set, what="held source fixture final drain")
            if switch is not None:
                await switch
            await drain_workers(app)


async def test_close_cancels_held_read_and_clears_unavailable_copy(app_context_factory):
    from aws_tui.vm.file_manager.s3_object_details_vm import S3ObjectDetailsState
    from tests.s3_object_details_support import DetailsReadBarrier

    barrier = DetailsReadBarrier(S3ObjectDetails(bucket="bucket", key="a.txt"))
    clipboard = InMemoryClipboard()
    app, fs, _ = await make_app(app_context_factory, clipboard=clipboard)
    fs.queue_details(barrier)
    async with app.run_test(size=(80, 24)) as pilot:
        await select_file(app, pilot)
        await pilot.press("ctrl+o")
        await wait_until(barrier.entered.is_set, what="held read")
        screen = app.screen
        await pilot.press("ctrl+c")
        await pilot.pause()
        assert clipboard.writes == []
        assert screen.query_one(TextArea).text == ""
        await pilot.press("escape")
        await wait_until(barrier.finished.is_set, what="cancelled details read")
        assert barrier.cancelled.is_set()
        assert screen.vm.state is S3ObjectDetailsState.CLOSED
        await pilot.pause()
        assert screen._subscription is None


def make_recording_app(factory, state="ready", *, clipboard=None):
    from datetime import UTC, datetime

    from botocore.exceptions import ClientError, EndpointConnectionError

    from aws_tui.domain.s3_fs import S3FS
    from tests.s3_object_details_support import RecordingSession

    session = RecordingSession()
    session.s3.queue("list_buckets", {"Buckets": [{"Name": "bucket"}]})
    session.s3.queue(
        "list_objects_v2",
        {
            "Contents": [{"Key": "a.txt", "Size": 42}, {"Key": "b.txt", "Size": 12}],
            "CommonPrefixes": [{"Prefix": "prefix/"}],
        },
    )
    head = {
        "ContentType": '[bold]雪[/bold] "quoted"\n' + "reported-property " * 25,
        "ContentEncoding": "gzip",
        "ContentLength": 42,
        "LastModified": datetime(2026, 10, 3, tzinfo=UTC),
        "StorageClass": "STANDARD",
        "ETag": '"opaque-2"',
        "VersionId": "v-current",
        "ServerSideEncryption": "aws:kms",
        "SSEKMSKeyId": "arn:aws:kms:us-east-1:123:key/example",
        "Metadata": {"note": "[red]literal[/red]\n雪", "empty": ""},
        "ChecksumSHA256": "reported-only==",
        "ChecksumType": "COMPOSITE",
    }
    denied = ClientError(
        {
            "Error": {
                "Code": "AccessDenied",
                "Message": "token=hidden https://user:secret@example.com/?X-Amz-Signature=signature",
            }
        },
        "HeadObject",
    )
    if state == "partial":
        session.s3.queue("head_object", denied, head)
        session.s3.queue("get_object_tagging", denied)
    elif state == "error":
        failure = EndpointConnectionError(
            endpoint_url="https://user:secret@example.com/?X-Amz-Signature=signature&token=hidden"
        )
        session.s3.queue("head_object", failure, failure)
    elif state == "minimal":
        session.s3.queue("head_object", {})
        session.s3.queue("get_object_tagging", {"TagSet": []})
    else:
        session.s3.queue("head_object", head)
        session.s3.queue(
            "get_object_tagging", {"TagSet": [{"Key": "owner", "Value": "[blue]雪[/blue]"}]}
        )
    fs = S3FS(session=session, bucket=None)
    ctx = factory(fs=fs, clipboard=clipboard)
    ctx.config_store.path.write_text(
        '[defaults]\nconnection = "test"\n\n[connections.test]\nkind = "s3-compatible"\nendpoint_url = "http://localhost:9000"\ncredentials = "static"\naccess_key_id = "k"\nsecret_access_key = "s"\nregion = "us-east-1"\n'
    )
    return AwsTuiApp(ctx), session, ctx


@pytest.mark.parametrize("state", ["ready", "partial", "error"])
@pytest.mark.parametrize("size", [(120, 40), (80, 24)], ids=["normal", "narrow"])
async def test_recorded_s3_fields_partial_errors_and_rendered_artifacts(
    app_context_factory, state, size, tmp_path, monkeypatch
):
    import json
    from html import unescape

    from aws_tui.ui.widgets.s3_object_details import S3ObjectDetailsModal
    from aws_tui.vm.file_manager.s3_object_details_vm import S3ObjectDetailsState

    clipboard = InMemoryClipboard()
    app, session, _ctx = make_recording_app(app_context_factory, state, clipboard=clipboard)
    async with app.run_test(size=size) as pilot:
        pane = await select_file(app, pilot)
        assert [operation for operation, _ in session.s3.calls] == [
            "list_buckets",
            "list_objects_v2",
        ]
        await pilot.press("ctrl+o")
        await wait_until(
            lambda: isinstance(app.screen, S3ObjectDetailsModal), what="recorded details"
        )
        screen = app.screen
        await wait_until(
            lambda: screen.vm.state in {S3ObjectDetailsState.READY, S3ObjectDetailsState.ERROR},
            what="recorded response",
        )
        table = screen.query_one(DataTable)
        fields = {field.label: field for field in screen.vm.fields}
        if state != "error":
            assert table.row_count == 13
            assert fields["ETag"].value == '"opaque-2"'
            assert fields["Checksum verification"].copy_value is None
            assert fields["Content encoding"].value == "gzip"
            assert fields["Size"].value == "42 bytes"
            assert fields["Version ID"].value == "v-current"
            assert fields["Storage class"].value == "STANDARD"
            assert fields["Last modified"].value == "2026-10-03T00:00:00+00:00"
            assert json.loads(fields["Encryption"].copy_value)["ServerSideEncryption"] == "aws:kms"
            if state == "partial":
                for label in ("Tags", "Checksums"):
                    assert fields[label].value.startswith("Unavailable:")
                    assert fields[label].copy_value is None
                assert "hidden" not in fields["Tags"].value
                assert "secret" not in fields["Checksums"].value
                table.move_cursor(row=9)
                await pilot.pause()
                await pilot.press("ctrl+c")
                await pilot.pause()
                assert clipboard.writes == []
            else:
                assert json.loads(fields["Checksums"].copy_value) == {
                    "ChecksumSHA256": "reported-only=="
                }
                assert screen.query_one(TextArea).text == fields["Content type"].copy_value
                table.move_cursor(row=8)
                await pilot.pause()
                for _ in range(4):
                    await pilot.press("tab")
                    if isinstance(app.focused, ModalButton):
                        break
                assert isinstance(app.focused, ModalButton)
                assert app.focused.button_id == "copy"
                await pilot.press("enter")
                await wait_until(lambda: clipboard.last is not None, what="metadata copy")
                assert json.loads(clipboard.last) == {"note": "[red]literal[/red]\n雪", "empty": ""}
                table.move_cursor(row=0)
                table.focus()
        else:
            assert table.row_count == 1
            assert fields["Error"].copy_value is None
            assert "hidden" not in fields["Error"].value
            assert "secret" not in fields["Error"].value
            assert "[REDACTED]" in fields["Error"].value
        await pilot.pause()
        svg = app.export_screenshot()
        rendered = unescape(svg).replace("\xa0", " ")
        for text in ("Full value", "Copy", "Close"):
            assert text in rendered
        if state == "ready":
            assert "[bold]" in rendered
            assert "[/bold]" in rendered
        monkeypatch.chdir(tmp_path)
        destination = tmp_path / f"{state}-{size[0]}x{size[1]}.svg"
        destination.write_text(svg, encoding="utf-8")
        assert destination.read_text(encoding="utf-8") == svg
        assert not (tmp_path / ".superpowers").exists()
        assert all(
            operation in {"list_buckets", "list_objects_v2", "head_object", "get_object_tagging"}
            for operation, _ in session.s3.calls
        )
        read_operations = [
            operation
            for operation, _ in session.s3.calls
            if operation in {"head_object", "get_object_tagging"}
        ]
        assert (
            read_operations
            == (
                {
                    "ready": ["head_object", "get_object_tagging"],
                    "partial": ["head_object", "head_object", "get_object_tagging"],
                    "error": ["head_object", "head_object"],
                }[state]
            )
        )
        await pilot.press("escape")
        await pilot.pause()
        assert pane.vm.selected_entry.name == "a.txt"
        assert [
            operation
            for operation, _ in session.s3.calls
            if operation in {"head_object", "get_object_tagging"}
        ] == read_operations
        session.s3.queue(
            "list_objects_v2", {"Contents": [{"Key": "a.txt", "Size": 42}], "CommonPrefixes": []}
        )
        await pane.vm.refresh()
        await pilot.pause()
        assert [
            operation
            for operation, _ in session.s3.calls
            if operation in {"head_object", "get_object_tagging"}
        ] == read_operations
        assert app._crash_report is None


async def test_minimal_head_explicit_missing_fields_are_visible_and_not_copyable(
    app_context_factory,
):
    app, _, _ = make_recording_app(app_context_factory, "minimal")
    async with app.run_test(size=(120, 40)) as pilot:
        await select_file(app, pilot)
        screen = await opened(app, pilot)
        table = screen.query_one(DataTable)
        for index, field in enumerate(screen.vm.fields):
            if field.label == "Tags":
                assert field.copy_value == "{}"
            elif field.label == "Checksum verification":
                assert "Not performed" in field.value
            else:
                assert field.value == "Unavailable"
                assert field.copy_value is None
            table.move_cursor(row=index)
            await pilot.pause()
            assert screen.query_one(TextArea).text == field.value


@pytest.mark.parametrize(
    ("terminal_fails", "helper_fails"),
    [(False, True), (True, False), (True, True)],
    ids=["helper-fails", "terminal-fails", "both-fail"],
)
async def test_copy_helper_failure_keeps_truthful_safe_outcome(
    app_context_factory, terminal_fails, helper_fails, monkeypatch
):
    from aws_tui.vm.chrome.toast_vm import ToastLevel

    value = "private-property-[bold]雪[/bold]"
    clipboard = InMemoryClipboard(
        ok=False, mechanism="fixture-helper" if helper_fails else "none", error_type="OSError"
    )
    app, fs, ctx = await make_app(app_context_factory, clipboard=clipboard)
    fs.queue_details(S3ObjectDetails(bucket="bucket", key="a.txt", content_type=value))
    warnings = []
    original_warning = ctx.log_sink.warning

    def record_warning(*args, **kwargs):
        warnings.append((args, kwargs))
        original_warning(*args, **kwargs)

    monkeypatch.setattr(ctx.log_sink, "warning", record_warning)
    if terminal_fails:

        def terminal_failure(_value):
            raise OSError("terminal refused")

        monkeypatch.setattr(app, "copy_to_clipboard", terminal_failure)
    async with app.run_test(size=(80, 24)) as pilot:
        await select_file(app, pilot)
        screen = await opened(app, pilot)
        await pilot.press("ctrl+c")
        await wait_until(
            lambda: any(
                toast.model.id
                == (
                    "clipboard-failed-S3-object-detail"
                    if helper_fails
                    else "clipboard-unavailable-S3-object-detail"
                )
                for toast in ctx.root_vm.chrome.toast_stack.toasts
            ),
            what="clipboard outcome",
        )
        toast = next(
            toast.model
            for toast in ctx.root_vm.chrome.toast_stack.toasts
            if toast.model.id
            == (
                "clipboard-failed-S3-object-detail"
                if helper_fails
                else "clipboard-unavailable-S3-object-detail"
            )
        )
        assert toast.level is ToastLevel.WARNING
        assert ("terminal refused OSC 52" if terminal_fails else "fixture-helper") in toast.text
        if terminal_fails:
            assert "terminal was sent" not in toast.text
        if helper_fails:
            assert "fixture-helper" in toast.text
        assert value not in toast.text
        assert "copied" not in toast.text.lower()
        assert clipboard.last == value
        assert value not in repr(warnings)
        assert app.screen is screen


async def test_close_before_mount_starts_no_details_read(app_context_factory):
    from aws_tui.vm.file_manager.s3_object_details_vm import S3ObjectDetailsState

    app, fs, _ = await make_app(app_context_factory)
    async with app.run_test(size=(80, 24)) as pilot:
        await select_file(app, pilot)
        app.action_object_details()
        modal = app.screen
        modal.action_close()
        await pilot.pause()
        assert fs.details_paths == []
        assert modal.vm.state is S3ObjectDetailsState.CLOSED


async def test_queued_copy_does_not_publish_stale_field(app_context_factory):
    from tests.s3_object_details_support import DetailsReadBarrier

    clipboard = InMemoryClipboard()
    app, fs, _ = await make_app(app_context_factory, clipboard=clipboard)
    barrier = DetailsReadBarrier(
        S3ObjectDetails(bucket="bucket", key="b.txt", content_type="CURRENT")
    )
    fs.queue_details(S3ObjectDetails(bucket="bucket", key="a.txt", content_type="OLD"), barrier)
    async with app.run_test(size=(80, 24)) as pilot:
        pane = await select_file(app, pilot)
        modal = await opened(app, pilot)
        modal.action_copy()
        pane.vm.move_cursor_to(
            next(i for i, row in enumerate(pane.vm.filtered_entries) if row.name == "b.txt")
        )
        await wait_until(barrier.entered.is_set, what="new selected field read")
        assert modal.query_one(TextArea).text == ""
        await pilot.press("ctrl+c")
        await pilot.pause()
        assert clipboard.writes == []
        await pilot.press("escape")
        await wait_until(barrier.finished.is_set, what="new read cancelled")


async def test_clipboard_offload_does_not_wedge_close_or_publish_after_unmount(app_context_factory):
    from tests.s3_object_details_support import HeldDetailsClipboard

    clipboard = HeldDetailsClipboard()
    app, fs, ctx = await make_app(app_context_factory, clipboard=clipboard)
    fs.queue_details(S3ObjectDetails(bucket="bucket", key="a.txt", content_type="held full value"))
    async with app.run_test(size=(80, 24)) as pilot:
        await select_file(app, pilot)
        modal = await opened(app, pilot)
        try:
            await pilot.press("ctrl+c")
            await wait_until(clipboard.entered.is_set, what="clipboard helper entered")
            await pilot.press("escape")
            await wait_until(lambda: app.screen is not modal, what="close while helper held")
            assert not any(
                toast.model.id == "clipboard-S3-object-detail"
                for toast in ctx.root_vm.chrome.toast_stack.toasts
            )
        finally:
            clipboard.release.set()
        await wait_until(clipboard.finished.is_set, what="helper thread teardown")
        await wait_until(lambda: modal._subscription is None, what="copy modal unmounted")
        await drain_workers(app)
        assert clipboard.writes == ["held full value"]
        assert not any(
            toast.model.id == "clipboard-S3-object-detail"
            for toast in ctx.root_vm.chrome.toast_stack.toasts
        )


@pytest.mark.parametrize("target", ["bucket", "prefix"])
async def test_recorded_bucket_and_prefix_listing_rows_refuse_without_metadata(
    app_context_factory, target
):
    app, session, ctx = make_recording_app(app_context_factory)
    async with app.run_test(size=(120, 40)) as pilot:
        await drain_workers(app)
        pane = app.query_one("#pane-left", Pane)
        if target == "prefix":
            await pane.vm.navigate_to(PathRef(("bucket",)))
        await wait_until(
            lambda: any(row.entry_vm.name == target for row in pane.query(EntryRow)),
            what=f"real S3 {target} row",
        )
        pane.vm.move_cursor_to(
            next(i for i, entry in enumerate(pane.vm.filtered_entries) if entry.name == target)
        )
        await pilot.pause()
        app.set_focus(None)
        before = pane.vm.selected_entry
        rendered_row = next(row for row in pane.query(EntryRow) if row.entry_vm is before)
        assert rendered_row.has_class("-selected")
        await pilot.press("ctrl+o")
        await pilot.pause()
        assert len(app.screen_stack) == 1
        assert pane.vm.selected_entry is before
        assert "focused S3 object" in ctx.root_vm.chrome.toast_stack.toasts[-1].model.text
        assert [operation for operation, _ in session.s3.calls] == (
            ["list_buckets", "list_objects_v2"] if target == "prefix" else ["list_buckets"]
        )


async def test_physical_pane_descendant_restores_its_owning_row_and_focus(app_context_factory):
    app, fs, ctx = await make_app(app_context_factory)
    fs.queue_details(S3ObjectDetails(bucket="bucket", key="a.txt", content_type="descendant owner"))
    async with app.run_test(size=(120, 40)) as pilot:
        pane = await select_file(app, pilot)
        pane.vm.set_filter_command.execute("a.txt")
        pane.vm.move_cursor_to(
            next(i for i, entry in enumerate(pane.vm.filtered_entries) if entry.name == "a.txt")
        )
        await pilot.pause()
        entry = pane.vm.selected_entry
        row = next(row for row in pane.query(EntryRow) if row.entry_vm is entry)
        clear = pane.query_one(".pane-clear-filter", ModalButton)
        clear.focus()
        await pilot.pause()
        assert app.focused is clear
        assert ctx.focus_coordinator.focused_slot.value == "s3.left"
        screen = await opened(app, pilot)
        await pilot.press("escape")
        await wait_until(lambda: app.screen is not screen, what="descendant details close")
        await pilot.pause()
        assert app.focused is clear
        assert pane.vm.selected_entry is entry
        assert next(item for item in pane.query(EntryRow) if item.entry_vm is entry) is row
        assert fs.details_paths == [PathRef(("bucket", "a.txt"))]


async def test_skipped_descendant_restoration_cannot_steal_focus_after_another_modal(
    app_context_factory,
):
    from aws_tui.ui.widgets.help_modal import HelpModal

    app, fs, ctx = await make_app(app_context_factory)
    fs.queue_details(S3ObjectDetails(bucket="bucket", key="a.txt", content_type="descendant owner"))
    async with app.run_test(size=(120, 40)) as pilot:
        pane = await select_file(app, pilot)
        pane.vm.set_filter_command.execute("a.txt")
        pane.vm.move_cursor_to(
            next(i for i, entry in enumerate(pane.vm.filtered_entries) if entry.name == "a.txt")
        )
        await pilot.pause()
        clear = pane.query_one(".pane-clear-filter", ModalButton)
        clear.focus()
        await pilot.pause()
        modal = await opened(app, pilot)
        modal.action_close()
        await app.action_help()
        await wait_until(lambda: isinstance(app.screen, HelpModal), what="new modal owns focus")
        await pilot.pause()
        assert app.focused is not clear
        assert ctx.focus_coordinator.is_modal
        assert app._object_details_focus_restoration is None
        await pilot.press("escape")
        await wait_until(lambda: len(app.screen_stack) == 1, what="unrelated modal close")
        await pilot.pause()
        assert app.focused is None
        assert app._object_details_focus_restoration is None
        assert pane.vm.selected_entry.name == "a.txt"
