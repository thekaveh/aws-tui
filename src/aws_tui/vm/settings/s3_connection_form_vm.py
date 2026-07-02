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

from collections.abc import Awaitable, Callable
from dataclasses import replace
from typing import Any

import reactivex as rx
from vmx import FormVM, RelayCommand

from aws_tui.vm.chrome.first_run_vm import S3CompatForm

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
    ) -> None:
        self._field_validators: dict[str, list[S3FieldValidator]] = {}
        self._model_validators: list[S3ModelValidator] = []
        self._inner: FormVM[S3CompatForm] = FormVM(
            initial=initial,
            persister=persister,
            strict=strict,
            validators={field: self._validate_field(field) for field in self._REQUIRED_FIELDS},
            model_validator=self._validate_model,
        )
        # Field-presence validators.
        for field in self._REQUIRED_FIELDS:
            self.add_field_validator(field, _require_non_empty(field))
        # Cross-field invariant — §9.bis.5 canonical example.
        self.add_model_validator(_endpoint_iff_force_path_style)
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

    # ── Registration ────────────────────────────────────────────────────────

    def add_field_validator(self, field: str, fn: S3FieldValidator) -> None:
        """Register an EXTRA per-field validator on top of the
        built-in field-presence + cross-field invariants. Consumers
        (e.g. the View widget) use this to layer their own
        format-specific checks (regex on ``name``, URL parse on
        ``endpoint_url``) without reaching into the composed VMx
        :class:`FormVM` directly."""
        self._field_validators.setdefault(field, []).append(fn)
        self._revalidate()

    def add_model_validator(self, fn: S3ModelValidator) -> None:
        """Register an EXTRA cross-field validator. See
        :meth:`add_field_validator` for the round-3 rationale.
        """
        self._model_validators.append(fn)
        self._revalidate()

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

    # ── Validation aggregation ──────────────────────────────────────────────

    def _validate_field(self, field: str) -> S3FieldValidator:
        def _validator(form: S3CompatForm) -> str | None:
            for validator in self._field_validators.get(field, ()):
                message = validator(form)
                if message is not None:
                    return message
            return None

        return _validator

    def _validate_model(self, form: S3CompatForm) -> dict[str, str]:
        errors: dict[str, str] = {}
        for validator in self._model_validators:
            errors.update(validator(form))
        return errors

    def _revalidate(self) -> None:
        self._inner.set_model(self._inner.model)


__all__ = [
    "S3ConnectionFormVM",
    "S3FieldValidator",
    "S3FormPersister",
    "S3ModelValidator",
]
