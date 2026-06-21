"""Tests for ConnectionFormInline (formerly S3CompatFormModal validation)."""

from __future__ import annotations

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


@pytest.mark.parametrize("field", ["region", "access_key_id", "secret_access_key"])
def test_required_field_rejects_empty(field: str) -> None:
    assert _validate_s3_form_value(field, "") is not None
    assert _validate_s3_form_value(field, "   ") is not None


@pytest.mark.parametrize("field", ["region", "access_key_id", "secret_access_key"])
def test_required_field_accepts_nonempty(field: str) -> None:
    assert _validate_s3_form_value(field, "valid") is None


def test_construction_smoke() -> None:
    """Sanity-check that the widget instantiates without an app context."""
    from typing import cast

    from vmx import MessageHub
    from vmx.messages.protocols import Message

    from aws_tui.ui.widgets.settings.connection_form import ConnectionFormInline

    hub = cast("MessageHub[Message]", MessageHub())
    widget = ConnectionFormInline(hub=hub)
    assert widget is not None
