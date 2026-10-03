"""Tests for ConnectionFormInline (formerly S3CompatFormModal validation)."""

from __future__ import annotations

from pathlib import Path

import pytest

from aws_tui.ui.widgets.settings.connection_form import _validate_s3_form_value


def test_name_valid_simple() -> None:
    assert _validate_s3_form_value("name", "minio-local") is None


def test_name_invalid_empty() -> None:
    assert _validate_s3_form_value("name", "") is not None


def test_name_invalid_chars() -> None:
    assert _validate_s3_form_value("name", "has space") is not None
    assert _validate_s3_form_value("name", "with/slash") is not None


def test_name_invalid_too_long() -> None:
    assert _validate_s3_form_value("name", "x" * 33) is not None


def test_name_valid_max_length() -> None:
    assert _validate_s3_form_value("name", "x" * 32) is None


def test_endpoint_url_valid() -> None:
    assert _validate_s3_form_value("endpoint_url", "http://localhost:9000") is None
    assert _validate_s3_form_value("endpoint_url", "https://minio.internal:443/path") is None


def test_endpoint_url_invalid() -> None:
    assert _validate_s3_form_value("endpoint_url", "") is not None
    assert _validate_s3_form_value("endpoint_url", "ftp://wrong") is not None
    assert _validate_s3_form_value("endpoint_url", "no-scheme") is not None
    assert _validate_s3_form_value("endpoint_url", "http://") is not None
    assert _validate_s3_form_value("endpoint_url", "http://localhost:notaport") is not None
    assert _validate_s3_form_value("endpoint_url", "http://localhost:99999") is not None
    assert _validate_s3_form_value("endpoint_url", "https://user:pass@example.com") is not None
    assert (
        _validate_s3_form_value("endpoint_url", "https://example.com?X-Amz-Signature=sig")
        is not None
    )
    assert _validate_s3_form_value("endpoint_url", "https://example.com#SECRETFRAG") is not None


@pytest.mark.parametrize("field", ["region", "access_key_id", "secret_access_key"])
def test_required_field_rejects_empty(field: str) -> None:
    assert _validate_s3_form_value(field, "") is not None
    assert _validate_s3_form_value(field, "   ") is not None


@pytest.mark.parametrize("field", ["region", "access_key_id", "secret_access_key"])
def test_required_field_accepts_nonempty(field: str) -> None:
    assert _validate_s3_form_value(field, "valid") is None


@pytest.mark.parametrize("value", ["", "   ", "SESSION"])
def test_session_token_is_optional(value: str) -> None:
    assert _validate_s3_form_value("session_token", value) is None


def test_construction_smoke() -> None:
    """Sanity-check that the widget instantiates without an app context."""
    from typing import cast

    from vmx import MessageHub
    from vmx.messages.protocols import Message

    from aws_tui.ui.widgets.settings.connection_form import ConnectionFormInline

    hub = cast("MessageHub[Message]", MessageHub())
    widget = ConnectionFormInline(hub=hub)
    assert widget is not None


@pytest.mark.asyncio
async def test_submit_does_not_close_form_so_parent_can_keep_open_on_error(
    tmp_path: Path,
) -> None:
    """Regression: _submit must NOT call close() — the parent panel
    decides whether to close based on whether the persistence step
    succeeded. If the form closes itself on submit and the parent's
    vm.add raises ValueError on a duplicate name, the user sees
    silence: the form disappeared with no error."""
    from typing import cast

    from textual.app import App, ComposeResult
    from textual.widgets import Input
    from vmx import MessageHub
    from vmx.messages.protocols import Message

    from aws_tui.ui.widgets.settings.connection_form import (
        ConnectionFormInline,
        ConnectionFormSubmitted,
    )

    hub = cast("MessageHub[Message]", MessageHub())
    submissions: list[ConnectionFormSubmitted] = []

    class _Host(App[None]):
        def __init__(self, w: ConnectionFormInline) -> None:
            super().__init__()
            self._w = w

        def compose(self) -> ComposeResult:
            yield self._w

        def on_connection_form_submitted(self, event: ConnectionFormSubmitted) -> None:
            submissions.append(event)

    form = ConnectionFormInline(hub=hub)
    app = _Host(form)
    async with app.run_test() as pilot:
        await pilot.pause()
        form.open_for_add()
        await pilot.pause()
        pilot.app.query_one("#form-name", Input).value = "x"
        pilot.app.query_one("#form-endpoint_url", Input).value = "http://localhost:9000"
        pilot.app.query_one("#form-region", Input).value = "us-east-1"
        pilot.app.query_one("#form-access_key_id", Input).value = "K"
        pilot.app.query_one("#form-secret_access_key", Input).value = "S"
        pilot.app.query_one("#form-session_token", Input).value = "TOKEN"
        await pilot.pause()
        form._submit()
        await pilot.pause()

    # Submission fired
    assert len(submissions) == 1
    assert submissions[0].form.session_token == "TOKEN"
    assert submissions[0].control is form
    # CRITICAL: form must NOT have closed itself
    assert form.has_class("-open"), (
        "ConnectionFormInline._submit() closed the form — parent can no "
        "longer keep it open on duplicate-name / persistence errors"
    )


@pytest.mark.asyncio
async def test_public_caption_errors_pending_controls_and_edit_name_lock() -> None:
    from typing import cast

    from textual.app import App, ComposeResult
    from textual.widgets import Input
    from vmx import Message, MessageHub

    from aws_tui.ui.widgets.modal_button import ModalButton
    from aws_tui.ui.widgets.settings.connection_form import ConnectionFormInline
    from aws_tui.vm.settings.s3_compat_form import S3CompatForm

    hub = cast("MessageHub[Message]", MessageHub())
    form = ConnectionFormInline(hub=hub, submit_label="Save and open")

    class Host(App[None]):
        def compose(self) -> ComposeResult:
            yield form

    async with Host().run_test() as pilot:
        form.open_for_add()
        await pilot.pause()
        assert form.has_errors
        save = next(b for b in form.query(ModalButton) if b.button_id == "form-save-btn")
        assert str(save.render()) == "Save and open"
        defaults = S3CompatForm(
            name="local",
            endpoint_url="http://localhost:9000",
            region="us-east-1",
            access_key_id="K",
            secret_access_key="S",
            force_path_style=True,
            verify_tls=True,
        )
        form.open_for_edit(name="local", defaults=defaults)
        await pilot.pause()
        assert not form.has_errors
        save.focus()
        await pilot.press("enter")
        assert all(i.disabled for i in form.query(Input))
        assert all(b.disabled for b in form.query(ModalButton))
        form.clear_submitting()
        assert form.query_one("#form-name", Input).disabled
        assert not form.query_one("#form-region", Input).disabled
        assert not save.disabled
        save.focus()
        await pilot.press("enter")
        form.mark_name_invalid()
        assert form.query_one("#form-name", Input).disabled
        assert form.query_one("#form-name", Input).has_class("-invalid")
        assert not form.query_one("#form-region", Input).disabled
        assert not save.disabled
        save.focus()
        await pilot.press("enter")
        form.close()
        assert not form.query_one("#form-name", Input).disabled
        assert not form.query_one("#form-region", Input).disabled


@pytest.mark.asyncio
async def test_settings_default_caption_remains_save() -> None:
    from typing import cast

    from textual.app import App, ComposeResult
    from vmx import Message, MessageHub

    from aws_tui.ui.widgets.modal_button import ModalButton
    from aws_tui.ui.widgets.settings.connection_form import ConnectionFormInline

    form = ConnectionFormInline(hub=cast("MessageHub[Message]", MessageHub()))

    class Host(App[None]):
        def compose(self) -> ComposeResult:
            yield form

    async with Host().run_test() as pilot:
        await pilot.pause()
        assert (
            str(next(b for b in form.query(ModalButton) if b.button_id == "form-save-btn").render())
            == "save"
        )


def test_submitted_legacy_constructor_preserves_model_without_control() -> None:
    from aws_tui.ui.widgets.settings.connection_form import ConnectionFormSubmitted
    from aws_tui.vm.settings.s3_compat_form import S3CompatForm

    model = S3CompatForm(
        name="local",
        endpoint_url="http://localhost:9000",
        region="us-east-1",
        access_key_id="KEY",
        secret_access_key="SECRET",
    )
    event = ConnectionFormSubmitted(form=model, mode="add", original_name=None)
    assert event.control is None
    assert event.form is model
    assert event.mode == "add"
    assert event.original_name is None
