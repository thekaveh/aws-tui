"""S3ConnectionFormVM — custom VM composing VMx FormVM (round-3 §9.bis.5).

Wraps a :class:`vmx.FormVM` over :class:`S3CompatForm` and adds
the cross-field invariants the S3-connection edit flow needs:

- All visible fields (``name`` / ``endpoint_url`` / ``region`` /
  ``access_key_id`` / ``secret_access_key``) are required.
- ``endpoint_url`` must be present IFF ``force_path_style`` is True —
  the §9.bis.5 canonical cross-field example.

The inner :class:`vmx.FormVM` is NOT exposed publicly. Consumers (view
widgets, tests) bind to the ``model``, ``errors``, ``can_submit``,
``set_field``, and ``submit_command`` facade surface.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import replace
from typing import Any

import reactivex as rx
from vmx import FormVM, FormVMBuilder, RelayCommand

from aws_tui.vm.settings.s3_compat_form import S3CompatForm

#: Async persister: (form) -> Awaitable[None]. Raises on failure.
S3FormPersister = Callable[[S3CompatForm], Awaitable[None]]
S3FieldValidator = Callable[[S3CompatForm], str | None]
S3ModelValidator = Callable[[S3CompatForm], dict[str, str]]


def _require_non_empty(field_label: str) -> Callable[[S3CompatForm], str | None]:
    def _v(form: S3CompatForm) -> str | None:
        # No default on getattr — an unknown field_label is a wiring
        # mistake that should surface as AttributeError at first use,
        # not silently produce "Xyz is required" for a field the UI
        # never renders.
        value = getattr(form, field_label)
        if not str(value).strip():
            return f"{field_label} is required"
        return None

    return _v


def _first_error(*validators: S3FieldValidator) -> S3FieldValidator:
    def _validate(form: S3CompatForm) -> str | None:
        for validator in validators:
            if (message := validator(form)) is not None:
                return message
        return None

    return _validate


def _endpoint_iff_force_path_style(form: S3CompatForm) -> dict[str, str]:
    has_endpoint = bool(form.endpoint_url.strip())
    if has_endpoint == form.force_path_style:
        return {}
    if form.force_path_style and not has_endpoint:
        return {
            "endpoint_url": "endpoint URL is required when force_path_style is True",
        }
    # has_endpoint AND not force_path_style — the converse mismatch
    return {
        "force_path_style": "force_path_style must be True when an endpoint URL is set",
    }


class S3ConnectionFormVM:
    """Edit-flow VM for one S3 connection.

    Parameters
    ----------
    initial:
        Initial :class:`S3CompatForm`. Used for both the working model
        AND the snapshot the deny/revert path falls back to.
    persister:
        Async callable invoked when the user approves the form.
        Raises propagate to ``submit()`` awaiters.
    strict:
        When True (default), the submit command requires
        ``is_dirty AND not has_errors``. Pass False to allow submitting
        an unchanged-but-valid form (e.g. "save as new" flows).
    """

    _REQUIRED_FIELDS = (
        "name",
        "endpoint_url",
        "region",
        "access_key_id",
        "secret_access_key",
    )

    def __init__(
        self,
        initial: S3CompatForm,
        *,
        persister: S3FormPersister,
        strict: bool = True,
        validators: Mapping[str, S3FieldValidator] | None = None,
    ) -> None:
        extras = dict(validators or {})
        field_validators = {
            field: _first_error(
                _require_non_empty(field),
                *([extras[field]] if field in extras else []),
            )
            for field in self._REQUIRED_FIELDS
        }
        builder: FormVMBuilder[S3CompatForm] = FormVM.builder()
        self._inner: FormVM[S3CompatForm] = (
            builder.initial(initial)
            .persister(persister)
            .strict(strict)
            .model_validator(_endpoint_iff_force_path_style)
            .validator("name", field_validators["name"])
            .validator("endpoint_url", field_validators["endpoint_url"])
            .validator("region", field_validators["region"])
            .validator("access_key_id", field_validators["access_key_id"])
            .validator("secret_access_key", field_validators["secret_access_key"])
            .build()
        )
        self._disposed = False

    # ── Public surface ──────────────────────────────────────────────────────

    @property
    def model(self) -> S3CompatForm:
        return self._inner.model

    @property
    def snapshot(self) -> S3CompatForm:
        return self._inner.snapshot

    @property
    def is_dirty(self) -> bool:
        return self._inner.is_dirty

    @property
    def errors(self) -> dict[str, str]:
        return self._inner.errors

    @property
    def has_errors(self) -> bool:
        return not self._inner.is_valid

    @property
    def is_valid(self) -> bool:
        return self._inner.is_valid

    @property
    def can_submit(self) -> bool:
        return self._inner.approve_command.can_execute()

    @property
    def submit_command(self) -> RelayCommand:
        """Persist the form. Auto-gated: disabled when there are errors,
        and (under strict) when the form is unchanged."""
        return self._inner.approve_command

    @property
    def revert_command(self) -> RelayCommand:
        return self._inner.deny_command

    @property
    def on_errors_changed(self) -> rx.Observable[dict[str, str]]:
        return self._inner.errors_changed

    # ── Mutation ────────────────────────────────────────────────────────────

    def set_field(self, field: str, value: Any) -> None:
        """Update one field on the working model.

        Re-validates and re-evaluates ``can_submit`` synchronously.
        """
        if not hasattr(self._inner.model, field):
            raise ValueError(f"S3CompatForm has no field {field!r}")
        new_model = replace(self._inner.model, **{field: value})
        self._inner.set_model(new_model)

    def set_model(self, model: S3CompatForm) -> None:
        self._inner.set_model(model)

    # ── Lifecycle ───────────────────────────────────────────────────────────

    def dispose(self) -> None:
        if self._disposed:
            return
        self._disposed = True
        self._inner.dispose()


__all__ = [
    "S3ConnectionFormVM",
    "S3FieldValidator",
    "S3FormPersister",
    "S3ModelValidator",
]
