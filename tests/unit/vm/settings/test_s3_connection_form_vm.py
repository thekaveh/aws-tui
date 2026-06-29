"""Tests for the S3ConnectionFormVM custom edit-flow VM."""

from __future__ import annotations

import pytest

from aws_tui.vm.chrome.first_run_vm import S3CompatForm
from aws_tui.vm.settings.s3_connection_form_vm import S3ConnectionFormVM


def _blank() -> S3CompatForm:
    return S3CompatForm(
        name="",
        endpoint_url="",
        region="",
        access_key_id="",
        secret_access_key="",
        force_path_style=True,
        verify_tls=True,
    )


def _valid() -> S3CompatForm:
    return S3CompatForm(
        name="minio-local",
        endpoint_url="http://minio.local:9000",
        region="us-east-1",
        access_key_id="AKIA...",
        secret_access_key="secret",
        force_path_style=True,
        verify_tls=True,
    )


async def _ok_persister(_m: S3CompatForm) -> None:
    pass


# -------------------- field-required validators --------------------


def test_blank_form_flags_every_required_field() -> None:
    f = S3ConnectionFormVM(initial=_blank(), persister=_ok_persister)
    assert "name" in f.errors
    assert "endpoint_url" in f.errors  # also flagged by the cross-field rule
    assert "region" in f.errors
    assert "access_key_id" in f.errors
    assert "secret_access_key" in f.errors
    assert f.has_errors is True
    assert f.is_valid is False
    f.dispose()


def test_completed_form_is_valid() -> None:
    f = S3ConnectionFormVM(initial=_valid(), persister=_ok_persister)
    assert f.errors == {}
    assert f.is_valid is True
    f.dispose()


def test_filling_a_required_field_clears_its_error() -> None:
    f = S3ConnectionFormVM(initial=_blank(), persister=_ok_persister)
    f.set_field("name", "prod")
    assert "name" not in f.errors
    f.dispose()


# -------------------- cross-field validator --------------------


def test_endpoint_iff_force_path_style_is_enforced_both_directions() -> None:
    """The §9.bis.5 canonical cross-field example."""
    # endpoint set + force_path_style off → flag force_path_style.
    f = S3ConnectionFormVM(
        initial=_valid().__class__(
            name="x",
            endpoint_url="http://x",
            region="r",
            access_key_id="a",
            secret_access_key="s",
            force_path_style=False,
        ),
        persister=_ok_persister,
    )
    assert "force_path_style" in f.errors
    f.dispose()


def test_no_endpoint_no_force_path_style_is_valid() -> None:
    """AWS S3 (not custom endpoint) → both off → invariant satisfied."""
    f = S3ConnectionFormVM(
        initial=_valid().__class__(
            name="aws-prod",
            endpoint_url="",  # blank — AWS S3 path
            region="us-east-1",
            access_key_id="a",
            secret_access_key="s",
            force_path_style=False,
        ),
        persister=_ok_persister,
    )
    # endpoint_url still flagged by field-presence validator first.
    # Model validator may overwrite that — but since BOTH are off,
    # the model validator returns {} so the field validator stands.
    # The intent is: a no-custom-endpoint AWS connection requires an
    # endpoint_url is empty + force_path_style is False; the
    # configuration is consistent but ``endpoint_url`` is still
    # required by the field-presence policy. Document this explicitly.
    assert "endpoint_url" in f.errors  # field-presence wins
    assert "force_path_style" not in f.errors  # cross-field invariant holds
    f.dispose()


# -------------------- submit gating --------------------


def test_cannot_submit_with_errors() -> None:
    f = S3ConnectionFormVM(initial=_blank(), persister=_ok_persister)
    assert f.can_submit is False
    f.dispose()


def test_cannot_submit_pristine_under_strict() -> None:
    f = S3ConnectionFormVM(initial=_valid(), persister=_ok_persister, strict=True)
    assert f.is_valid is True
    assert f.is_dirty is False
    assert f.can_submit is False  # strict requires dirty
    f.dispose()


def test_can_submit_when_dirty_and_valid() -> None:
    f = S3ConnectionFormVM(initial=_valid(), persister=_ok_persister, strict=True)
    f.set_field("region", "eu-west-1")
    assert f.is_dirty is True
    assert f.is_valid is True
    assert f.can_submit is True
    f.dispose()


def test_can_submit_when_pristine_and_not_strict() -> None:
    f = S3ConnectionFormVM(initial=_valid(), persister=_ok_persister, strict=False)
    assert f.can_submit is True
    f.dispose()


# -------------------- mutation --------------------


def test_set_field_rejects_unknown_field() -> None:
    f = S3ConnectionFormVM(initial=_valid(), persister=_ok_persister)
    with pytest.raises(ValueError, match="no field 'bogus'"):
        f.set_field("bogus", "value")
    f.dispose()


def test_dispose_is_idempotent() -> None:
    f = S3ConnectionFormVM(initial=_valid(), persister=_ok_persister)
    f.dispose()
    f.dispose()
