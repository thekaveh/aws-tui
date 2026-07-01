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
    # CRITICAL: form must NOT have closed itself
    assert form.has_class("-open"), (
        "ConnectionFormInline._submit() closed the form — parent can no "
        "longer keep it open on duplicate-name / persistence errors"
    )
