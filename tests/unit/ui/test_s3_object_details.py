"""Literal inspector field projection and unavailable copy controls."""

import pytest
from rich.text import Text
from textual.widgets import DataTable, TextArea

from aws_tui.domain.s3_object_details import S3ObjectDetails
from aws_tui.infra.keymap_store import KeymapStore
from aws_tui.ui.actions import ActionRegistry
from aws_tui.ui.bindings import BindingResolver
from tests.helpers import wait_until
from tests.integration.conftest import app_context_factory as app_context_factory
from tests.integration.test_s3_object_details import (
    make_app,
    opened,
    select_file,
)


def test_object_details_has_unique_default_key_and_literal_label():
    actions = ActionRegistry()
    actions.register("pane.object_details", lambda: None)
    bindings = BindingResolver(keymap=KeymapStore(), actions=actions).to_textual_bindings()
    assert len(bindings) == 1
    assert bindings[0].key == "ctrl+o"
    assert bindings[0].description == "S3 object details"


@pytest.mark.parametrize("section", ["head", "tags", "checksums"])
async def test_secret_bearing_errors_render_redacted_literal_fields(app_context_factory, section):
    from aws_tui.ui.widgets.s3_object_details import S3ObjectDetailsModal
    from aws_tui.vm.file_manager.s3_object_details_vm import S3ObjectDetailsState

    app, fs, _ = await make_app(app_context_factory)
    secret_error = "token=hidden https://user:secret@example.com/?X-Amz-Signature=signature"
    fs.queue_details(
        RuntimeError(secret_error)
        if section == "head"
        else S3ObjectDetails(bucket="bucket", key="a.txt", **{f"{section}_error": secret_error})
    )
    async with app.run_test(size=(120, 40)) as pilot:
        await select_file(app, pilot)
        await pilot.press("ctrl+o")
        await wait_until(
            lambda: isinstance(app.screen, S3ObjectDetailsModal), what="error inspector"
        )
        modal = app.screen
        await wait_until(
            lambda: modal.vm.state in {S3ObjectDetailsState.READY, S3ObjectDetailsState.ERROR},
            what="safe error projection",
        )
        table = modal.query_one(DataTable)
        label = {"head": "Error", "tags": "Tags", "checksums": "Checksums"}[section]
        row = next(index for index, field in enumerate(modal.vm.fields) if field.label == label)
        table.move_cursor(row=row)
        await pilot.pause()
        assert all(isinstance(cell, Text) for cell in table.get_row_at(row))
        value = modal.query_one(TextArea).text
        for secret in ("hidden", "secret", "signature"):
            assert secret not in value
        assert "[REDACTED]" in value
        assert modal.vm.fields[row].copy_value is None


async def test_literal_table_keeps_full_text_and_viewer_is_read_only(app_context_factory):
    from aws_tui.infra.clipboard import InMemoryClipboard

    value = '[bold]雪[/bold] "quoted"\n' + "x" * 9000
    clipboard = InMemoryClipboard()
    app, fs, _ = await make_app(app_context_factory, clipboard=clipboard)
    fs.queue_details(S3ObjectDetails(bucket="bucket", key="a.txt", content_type=value))
    async with app.run_test(size=(80, 24)) as pilot:
        await select_file(app, pilot)
        modal = await opened(app, pilot)
        cell = modal.query_one(DataTable).get_row_at(0)[1]
        assert isinstance(cell, Text)
        assert cell.plain == value
        assert cell.spans == []
        viewer = modal.query_one(TextArea)
        assert viewer.read_only
        assert viewer.soft_wrap
        viewer.focus()
        await pilot.press("x", "delete", "backspace", "enter")
        await pilot.pause()
        assert app.focused is viewer
        assert viewer.text == value
        assert modal.vm.fields[0].value == value
        assert modal.vm.fields[0].copy_value == value
        assert modal.query_one(DataTable).get_row_at(0)[1].plain == value
        await pilot.press("ctrl+c")
        await wait_until(lambda: clipboard.last is not None, what="viewer copies full property")
        assert viewer.text == value
        assert clipboard.last == value
