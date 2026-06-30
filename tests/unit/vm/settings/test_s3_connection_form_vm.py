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
    """The §9.bis.5 canonical cross-field example.

    The validator is symmetric: setting the endpoint without
    ``force_path_style`` flags ``force_path_style``; requiring
    ``force_path_style`` without an endpoint flags ``endpoint_url``
    with the cross-field message (distinct from the bare
    field-presence message)."""
    # Direction A — endpoint set + force_path_style off → flag force_path_style.
    f_a = S3ConnectionFormVM(
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
    assert "force_path_style" in f_a.errors
    f_a.dispose()
    # Direction B — force_path_style on + no endpoint → flag endpoint_url
    # WITH the cross-field message (not the bare field-presence message).
    f_b = S3ConnectionFormVM(
        initial=_valid().__class__(
            name="x",
            endpoint_url="",
            region="r",
            access_key_id="a",
            secret_access_key="s",
            force_path_style=True,
        ),
        persister=_ok_persister,
    )
    assert "endpoint_url" in f_b.errors
    # The cross-field message must be the one that lands — proves the
    # model validator fired (not just the field-presence validator,
    # which would say "endpoint_url is required" instead).
    assert "force_path_style is True" in f_b.errors["endpoint_url"]
    f_b.dispose()


def test_no_endpoint_no_force_path_style_satisfies_cross_field_but_field_presence_still_fails() -> (
    None
):
    """Both flags off → cross-field invariant is consistent (no
    ``force_path_style`` error), but ``endpoint_url`` is still
    flagged because every s3-compatible connection needs a custom
    endpoint URL — the form has no "AWS S3" mode."""
    f = S3ConnectionFormVM(
        initial=_valid().__class__(
            name="prod",
            endpoint_url="",
            region="us-east-1",
            access_key_id="a",
            secret_access_key="s",
            force_path_style=False,
        ),
        persister=_ok_persister,
    )
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
