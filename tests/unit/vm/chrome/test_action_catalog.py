"""The catalog owns metadata; projection preserves unavailable diagnostics."""

from dataclasses import FrozenInstanceError

import pytest

from aws_tui.app import AwsTuiApp
from aws_tui.composition import build_app_context
from aws_tui.vm.chrome.action_catalog import (
    ACTION_SPECS,
    ActionSpec,
    format_effective_keys,
    project_actions,
    scope_label,
)


def assert_catalog_coverage(app: AwsTuiApp) -> None:
    by_id = {spec.id: spec for spec in ACTION_SPECS}
    assert len(by_id) == len(ACTION_SPECS)
    assert set(by_id) == set(app._actions.known_actions())
    bindings = app.app_ctx.keymap_store.all()
    for action_id, spec in by_id.items():
        assert spec.label.strip()
        assert spec.category.strip()
        if spec.key_source == "keymap":
            assert action_id in bindings
            expected = app.app_ctx.keymap_store.resolve(action_id)
        else:
            assert action_id not in bindings
            expected = ()
        row = project_actions(
            (spec,),
            registered_ids=frozenset({action_id}),
            bindings=bindings,
            active_service_id=None,
            unavailable_reasons={},
        )[0]
        assert row.effective_keys == expected


def test_complete_actual_app_catalog(tmp_path):
    ctx = build_app_context(config_dir=tmp_path, cache_dir=tmp_path, demo=True)
    try:
        assert_catalog_coverage(AwsTuiApp(ctx))
    finally:
        ctx.root_vm.dispose()
        ctx.log_sink.close()


@pytest.mark.parametrize(
    ("registered", "active", "reasons", "expected"),
    [
        (frozenset(), "athena", {"x": "busy"}, "handler_missing"),
        (frozenset({"x"}), "glue", {"x": "busy"}, "service_inactive"),
        (frozenset({"x"}), "athena", {"x": "busy"}, "busy"),
        (frozenset({"x"}), "athena", {}, None),
    ],
)
def test_reason_precedence_and_retention(registered, active, reasons, expected):
    spec = ActionSpec("x", "Query", "Athena", service_ids=frozenset({"athena"}))
    rows = project_actions(
        (spec,),
        registered_ids=registered,
        bindings={"x": ("ctrl+g",)},
        active_service_id=active,
        unavailable_reasons=reasons,
    )
    assert len(rows) == 1
    assert rows[0].availability_reason == expected
    assert rows[0].available is (expected is None)
    assert rows[0].effective_keys == ("ctrl+g",)
    with pytest.raises(FrozenInstanceError):
        rows[0].label = "changed"


def test_required_binding_and_explicit_unbound():
    with pytest.raises(KeyError):
        project_actions(
            (ActionSpec("x", "Label", "App"),),
            registered_ids=frozenset({"x"}),
            bindings={},
            active_service_id=None,
            unavailable_reasons={},
        )
    row = project_actions(
        (ActionSpec("x", "Label", "App", key_source="unbound"),),
        registered_ids=frozenset({"x"}),
        bindings={"x": ("g",)},
        active_service_id=None,
        unavailable_reasons={},
    )[0]
    assert row.effective_keys == ()


@pytest.mark.parametrize(
    ("keys", "expected"),
    [
        ((), "Unbound"),
        (("CTRL+SHIFT+ENTER", "Alt+g", "G"), "Ctrl+Shift+Enter / Alt+g / G"),
        (("escape", "tab", "left", "question_mark"), "Esc / Tab / ← / ?"),
    ],
)
def test_format_keys(keys, expected):
    assert format_effective_keys(keys) == expected


def test_scope_format():
    assert scope_label(frozenset(), "s3") == "Global"
    assert scope_label(frozenset({"s3", "athena"}), "athena") == "Athena"
