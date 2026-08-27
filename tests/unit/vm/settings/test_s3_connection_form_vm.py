"""Tests for the S3ConnectionFormVM custom edit-flow VM."""

from __future__ import annotations

import pytest

from aws_tui.vm.settings.s3_compat_form import S3CompatForm
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


# -------------------- addressing-style independence --------------------


def test_virtual_hosted_compatible_endpoint_is_valid() -> None:
    """Custom endpoints may use virtual-hosted addressing, as R2 does."""
    form = S3ConnectionFormVM(
        initial=_valid().__class__(
            name="r2-prod",
            endpoint_url="https://account.r2.cloudflarestorage.com",
            region="auto",
            access_key_id="R2_ACCESS_KEY",
            secret_access_key="R2_SECRET",
            force_path_style=False,
        ),
        persister=_ok_persister,
    )

    assert form.errors == {}
    assert form.is_valid is True
    form.dispose()


@pytest.mark.parametrize("force_path_style", [True, False])
def test_custom_endpoint_is_required_for_every_addressing_style(
    force_path_style: bool,
) -> None:
    form = S3ConnectionFormVM(
        initial=_valid().__class__(
            name="x",
            endpoint_url="",
            region="r",
            access_key_id="a",
            secret_access_key="s",
            force_path_style=force_path_style,
        ),
        persister=_ok_persister,
    )

    assert form.errors["endpoint_url"] == "endpoint_url is required"
    assert "force_path_style" not in form.errors
    form.dispose()


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


# -------------------- extra validators --------------------


def test_extra_field_validator_is_composed_by_vmx_builder_and_emits() -> None:
    f = S3ConnectionFormVM(
        initial=_valid(),
        persister=_ok_persister,
        strict=False,
        validators={
            "name": lambda form: (
                "name must start with team-" if not form.name.startswith("team-") else None
            )
        },
    )
    payloads: list[dict[str, str]] = []
    sub = f.on_errors_changed.subscribe(on_next=payloads.append)
    try:
        assert f.errors == {"name": "name must start with team-"}
        assert payloads == []
        assert f.can_submit is False

        f.set_field("name", "team-minio")

        assert f.errors == {}
        assert payloads == [{}]
        assert f.can_submit is True
    finally:
        sub.dispose()
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
