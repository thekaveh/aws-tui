"""Tests for the ValidatingFormVM mini-primitive."""

from __future__ import annotations

from dataclasses import dataclass, replace

from aws_tui.vm._composition import ValidatingFormVM


@dataclass
class _Connection:
    name: str = ""
    endpoint_url: str = ""
    force_path_style: bool = False


async def _no_op_persister(_m: _Connection) -> None:
    pass


def _make(
    initial: _Connection | None = None, *, strict: bool = True
) -> ValidatingFormVM[_Connection]:
    return ValidatingFormVM(
        initial=initial if initial is not None else _Connection(),
        persister=_no_op_persister,
        strict=strict,
    )


# -------------------- initial state --------------------


def test_initial_form_has_no_errors() -> None:
    f = _make()
    assert f.errors == {}
    assert f.has_errors is False
    assert f.is_valid is True
    f.dispose()


def test_initial_form_is_not_dirty() -> None:
    f = _make()
    assert f.is_dirty is False
    f.dispose()


def test_initial_approve_command_is_disabled_when_strict() -> None:
    f = _make(strict=True)
    assert f.approve_command.can_execute() is False  # not dirty
    f.dispose()


def test_initial_approve_command_is_enabled_when_not_strict() -> None:
    f = _make(strict=False)
    assert f.approve_command.can_execute() is True
    f.dispose()


# -------------------- field validators --------------------


def test_field_validator_flags_missing_name() -> None:
    f = _make()
    f.add_field_validator("name", lambda m: "name is required" if not m.name else None)
    assert f.errors == {"name": "name is required"}
    assert f.has_errors is True
    f.dispose()


def test_field_validator_clears_when_field_becomes_valid() -> None:
    f = _make()
    f.add_field_validator("name", lambda m: "name is required" if not m.name else None)
    assert "name" in f.errors
    f.set_model(replace(f.model, name="prod"))
    assert f.errors == {}
    f.dispose()


def test_multiple_field_validators_first_non_none_wins() -> None:
    f = _make()
    f.add_field_validator("name", lambda m: "first-error" if not m.name else None)
    f.add_field_validator("name", lambda m: "second-error" if len(m.name) < 5 else None)
    # Initial name is empty — first validator fires.
    assert f.errors["name"] == "first-error"
    # Make name non-empty but short — first passes, second fires.
    f.set_model(replace(f.model, name="abc"))
    assert f.errors["name"] == "second-error"
    f.dispose()


# -------------------- model validators --------------------


def test_model_validator_endpoint_iff_force_path_style() -> None:
    """The §9.bis.5 canonical cross-field example."""
    f = _make()
    f.add_model_validator(
        lambda m: (
            {}
            if bool(m.endpoint_url) == m.force_path_style
            else {
                "endpoint_url": "required when force_path_style is True",
                "force_path_style": "implies endpoint_url",
            }
        )
    )
    # Initial: both False → consistent.
    assert f.errors == {}
    # Turn on force_path_style without endpoint_url → both flagged.
    f.set_model(replace(f.model, force_path_style=True))
    assert "endpoint_url" in f.errors
    assert "force_path_style" in f.errors
    # Set endpoint_url → both clear.
    f.set_model(replace(f.model, endpoint_url="http://minio.local:9000"))
    assert f.errors == {}
    f.dispose()


def test_model_validator_can_overwrite_field_validator() -> None:
    f = _make()
    f.add_field_validator("name", lambda _m: "field-says-bad")
    f.add_model_validator(lambda _m: {"name": "model-overrides"})
    # Model validator runs after field validators; its message wins.
    assert f.errors["name"] == "model-overrides"
    f.dispose()


# -------------------- approve_command predicate --------------------


def test_approve_disabled_while_errors_present() -> None:
    f = _make(strict=False)  # not strict — usually enabled
    f.add_field_validator("name", lambda m: "required" if not m.name else None)
    assert f.approve_command.can_execute() is False  # errors present
    f.set_model(replace(f.model, name="x"))
    assert f.approve_command.can_execute() is True
    f.dispose()


def test_approve_enabled_only_when_dirty_and_valid_under_strict() -> None:
    f = _make(strict=True)
    # Initial: pristine + valid → strict requires dirty → disabled.
    assert f.approve_command.can_execute() is False
    # Make a change → dirty + valid → enabled.
    f.set_model(replace(f.model, name="x"))
    assert f.approve_command.can_execute() is True
    # Add a validator the model fails → dirty + invalid → disabled.
    f.add_field_validator("name", lambda m: "needs 5+ chars" if len(m.name) < 5 else None)
    assert f.approve_command.can_execute() is False
    # Fix it → dirty + valid → enabled.
    f.set_model(replace(f.model, name="prod-zone"))
    assert f.approve_command.can_execute() is True
    f.dispose()


# -------------------- on_errors_changed observable --------------------


def test_on_errors_changed_fires_when_validation_state_flips() -> None:
    f = _make()
    f.add_field_validator("name", lambda m: "required" if not m.name else None)
    payloads: list[dict[str, str]] = []
    sub = f.on_errors_changed.subscribe(on_next=payloads.append)
    try:
        # Going from invalid to valid emits one payload.
        f.set_model(replace(f.model, name="x"))
        assert payloads == [{}]
        # Same valid state → no event.
        f.set_model(replace(f.model, name="y"))
        assert payloads == [{}]
        # Back to invalid → one more event.
        f.set_model(replace(f.model, name=""))
        assert payloads == [{}, {"name": "required"}]
    finally:
        sub.dispose()
        f.dispose()


def test_set_model_does_not_emit_when_errors_unchanged() -> None:
    f = _make()
    f.add_field_validator("name", lambda m: "required" if not m.name else None)
    payloads: list[dict[str, str]] = []
    sub = f.on_errors_changed.subscribe(on_next=payloads.append)
    try:
        # Two successive invalid states with the same field error.
        f.set_model(replace(f.model, name=""))
        f.set_model(replace(f.model, name=""))
        assert payloads == []  # no flip
    finally:
        sub.dispose()
        f.dispose()


# -------------------- dispose --------------------


def test_dispose_is_idempotent() -> None:
    f = _make()
    f.dispose()
    f.dispose()
