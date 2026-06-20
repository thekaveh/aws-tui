"""Tests for S3CompatFormModal extensions (name_locked + validation helpers)."""

from __future__ import annotations

import pytest

from aws_tui.ui.widgets.first_run_modal import _validate_s3_form_value


def test_name_valid_simple():
    assert _validate_s3_form_value("name", "minio-local") is None


def test_name_invalid_empty():
    assert _validate_s3_form_value("name", "") is not None


def test_name_invalid_chars():
    assert _validate_s3_form_value("name", "has space") is not None
    assert _validate_s3_form_value("name", "with/slash") is not None


def test_name_invalid_too_long():
    assert _validate_s3_form_value("name", "x" * 33) is not None


def test_name_valid_max_length():
    assert _validate_s3_form_value("name", "x" * 32) is None


def test_endpoint_url_valid():
    assert _validate_s3_form_value("endpoint_url", "http://localhost:9000") is None
    assert _validate_s3_form_value("endpoint_url", "https://minio.internal:443/path") is None


def test_endpoint_url_invalid():
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
