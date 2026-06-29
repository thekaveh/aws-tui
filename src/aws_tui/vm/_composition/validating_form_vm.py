"""ValidatingFormVM — aws-tui-side mini-primitive.

Composes a VMx :class:`FormVM` internally + adds:

- declarative ``field_validator(field, fn)`` and ``model_validator(fn)``
  registration
- a reactive ``errors: dict[str, str]`` map
- auto-gated ``approve_command`` (``can_execute = is_dirty AND not has_errors``)

The composed :class:`FormVM` is held internally and NOT exposed in
the public surface (round-3 directive §9.bis.11). Consumers bind to
the wrapping VM's surface.

Upstream candidate: VMx vNext could ship a declarative validator API
on FormVM natively (see
``docs/superpowers/specs/2026-06-28-vmx-upstream-vnext-asks.md``
Item 4).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any, Generic, TypeVar

import reactivex as rx
from reactivex.subject import Subject
from vmx import RelayCommand
from vmx.forms.form_vm import FormVM

TM = TypeVar("TM")

#: Per-field validator: ``(model) -> error message or None``.
FieldValidator = Callable[[Any], str | None]

#: Cross-field validator: ``(model) -> dict[field_name, error_message]``.
#: Returning an empty dict means "all invariants hold". Validators
#: that need to report multi-field errors return one entry per field
#: that should be flagged (a single invariant may flag multiple
#: fields — e.g. "endpoint_url IFF force_path_style" flags both).
ModelValidator = Callable[[Any], dict[str, str]]


class ValidatingFormVM(Generic[TM]):
    """Form VM with declarative validators + reactive errors map.

    Parameters
    ----------
    initial:
        Initial domain model. Passed straight to the underlying
        :class:`FormVM`.
    persister:
        Async callable ``(model) -> Awaitable[None]``. The persister
        is invoked AFTER validation passes (i.e. ``has_errors`` is
        False). It MAY also raise; raises propagate to the caller.
    strict:
        When True, ``approve_command.can_execute`` additionally
        requires the form to be dirty. Default True (matches the
        FormVM's strict-mode semantics applied to most aws-tui form
        consumers).

    Notes
    -----
    Validators are registered AFTER construction via
    :meth:`add_field_validator` / :meth:`add_model_validator`. A model
    validator can register before any field validators if desired.
    """

    def __init__(
        self,
        initial: TM,
        persister: Callable[[TM], Awaitable[None]],
        *,
        strict: bool = True,
    ) -> None:
        self._inner: FormVM[TM] = FormVM(
            initial,
            persister=persister,
            strict=False,  # We gate via our own predicate below.
        )
        self._strict: bool = strict
        self._field_validators: dict[str, list[FieldValidator]] = {}
        self._model_validators: list[ModelValidator] = []
        self._errors: dict[str, str] = {}
        self._errors_subject: Subject[dict[str, str]] = Subject()
        self._approve_can_execute_trigger: Subject[None] = Subject()
        self._approve_command: RelayCommand = (
            RelayCommand.builder()
            .task(self._inner_approve_fire_and_forget)
            .predicate(self._can_approve)
            .triggers(self._approve_can_execute_trigger)
            .build()
        )
        self._disposed = False

    # ── Public surface ──────────────────────────────────────────────────────

    @property
    def model(self) -> TM:
        return self._inner.model

    @property
    def snapshot(self) -> TM:
        return self._inner.snapshot

    @property
    def is_dirty(self) -> bool:
        return self._inner.is_dirty

    @property
    def errors(self) -> dict[str, str]:
        """Live mapping of field name → error message. Empty when all
        validators pass."""
        return dict(self._errors)

    @property
    def has_errors(self) -> bool:
        return bool(self._errors)

    @property
    def is_valid(self) -> bool:
        return not self._errors

    @property
    def approve_command(self) -> RelayCommand:
        """Persist the model. Disabled when there are errors, or when
        ``strict=True`` and the form is pristine."""
        return self._approve_command

    @property
    def deny_command(self) -> RelayCommand:
        """Revert to the snapshot."""
        return self._inner.deny_command

    @property
    def on_errors_changed(self) -> rx.Observable[dict[str, str]]:
        """Fires whenever the ``errors`` map changes. The payload is
        a snapshot of the new map."""
        return self._errors_subject

    # ── Registration ────────────────────────────────────────────────────────

    def add_field_validator(self, field: str, fn: FieldValidator) -> None:
        """Register a per-field validator. Validators are run in
        registration order; the first non-None message wins for that
        field."""
        self._field_validators.setdefault(field, []).append(fn)
        self._revalidate()

    def add_model_validator(self, fn: ModelValidator) -> None:
        """Register a cross-field validator. May flag multiple
        fields per invocation. Validators are run in registration
        order; later validators may overwrite earlier ones for the
        same field."""
        self._model_validators.append(fn)
        self._revalidate()

    # ── Mutation ────────────────────────────────────────────────────────────

    def set_model(self, model: TM) -> None:
        """Update the model. Re-validates and re-evaluates approve's
        ``can_execute``."""
        self._inner.set_model(model)
        self._revalidate()
        self._approve_can_execute_trigger.on_next(None)

    # ── Lifecycle ───────────────────────────────────────────────────────────

    def dispose(self) -> None:
        if self._disposed:
            return
        self._disposed = True
        self._errors_subject.on_completed()
        self._errors_subject.dispose()
        self._approve_can_execute_trigger.on_completed()
        self._approve_can_execute_trigger.dispose()
        self._approve_command.dispose()
        self._inner.dispose()

    # ── Internal ────────────────────────────────────────────────────────────

    def _revalidate(self) -> None:
        old_errors = dict(self._errors)
        new_errors: dict[str, str] = {}
        # Field validators first; first non-None wins per field.
        for field, fns in self._field_validators.items():
            for field_fn in fns:
                msg = field_fn(self._inner.model)
                if msg is not None:
                    new_errors[field] = msg
                    break
        # Model validators second; may overwrite per-field entries.
        for model_fn in self._model_validators:
            cross_errors = model_fn(self._inner.model)
            for field, msg in cross_errors.items():
                new_errors[field] = msg
        if new_errors != old_errors:
            self._errors = new_errors
            self._errors_subject.on_next(dict(new_errors))
        self._approve_can_execute_trigger.on_next(None)

    def _can_approve(self) -> bool:
        if self._errors:
            return False
        return not (self._strict and not self._inner.is_dirty)

    def _inner_approve_fire_and_forget(self) -> None:
        """Synchronous trampoline matching FormVM's approve task
        contract. We invoke the inner approve_command which schedules
        the async persist via the inner FormVM's task wiring."""
        self._inner.approve_command.execute()


__all__ = ["FieldValidator", "ModelValidator", "ValidatingFormVM"]
