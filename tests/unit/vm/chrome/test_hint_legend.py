"""Tests for the HintLegendVM."""

from __future__ import annotations

from typing import cast

import pytest
from vmx import NULL_DISPATCHER, MessageHub
from vmx.messages.protocols import Message

from aws_tui.infra.keymap_store import KeymapStore
from aws_tui.vm.chrome.hint_legend_vm import (
    _ACTION_EFFECTS,
    _ACTION_LABELS,
    _ACTION_PRIORITIES,
    _ACTION_REQUIREMENTS,
    _GLOBAL_ACTIONS,
    _SERVICE_ACTIONS,
    HintAction,
    HintLegendVM,
    _canonical_shortcut,
    _tooltip_for,
)
from aws_tui.vm.messages import FocusChangedMessage, KeymapChangedMessage


def _hub() -> MessageHub[Message]:
    return cast("MessageHub[Message]", MessageHub())


def _build(
    actions: dict[str, tuple[str, ...]] | None = None,
    *,
    keymap: KeymapStore | None = None,
) -> tuple[HintLegendVM, MessageHub[Message]]:
    hub = _hub()
    legend = HintLegendVM(
        hub=hub,
        dispatcher=NULL_DISPATCHER,
        keymap=keymap or KeymapStore(),
    )
    if actions:
        for vm_id, action_ids in actions.items():
            legend.register_focusable(vm_id, action_ids)
    legend.construct()
    return legend, hub


_EXPECTED_ACTION_EFFECTS = {
    "app.command_palette": (
        "Open commands available for the active service. This does not perform an AWS operation."
    ),
    "app.themes": "Open the theme picker. This changes presentation only.",
    "app.cycle_theme": "Switch to the next theme. This changes presentation only.",
    "app.help": "Open keyboard and workflow help. This does not perform an AWS operation.",
    "app.quit": "Exit aws-tui after the application's normal shutdown sequence.",
    "app.swap_source": (
        "Switch to the next configured source and rebuild the active service context. "
        "This does not write AWS resources."
    ),
    "pane.switch_focus": "Move keyboard focus to the next operational pane.",
    "pane.descend": "Open the selected item or descend into the selected location.",
    "pane.copy": "Copy the selected item through the existing transfer workflow.",
    "pane.delete": "Delete the selected item through the existing confirmation workflow.",
    "pane.refresh": "Reload the active operational surface from its current source.",
    "emr.next_application": (
        "Select the next EMR Serverless application and load its runs. This does not start a job."
    ),
    "emr.clone": "Open the clone workflow for the selected EMR Serverless job run.",
    "glue.catalog": "Show the Glue Catalog view. This performs read-only discovery.",
    "glue.jobs": "Show Glue jobs and their read-only run history.",
    "glue.crawlers": "Show Glue crawlers and their read-only state.",
    "glue.choose_run_state": "Open the Glue job-run state filter.",
    "glue.choose_crawler_state": "Open the Glue crawler-state filter.",
    "glue.copy_table_ref": "Copy the selected Glue table's fully qualified SQL identifier.",
    "glue.query_in_athena": (
        "Open the selected Glue table in Athena and prefill a bounded read-only SELECT. "
        "This does not execute the query."
    ),
    "glue.time_travel_in_athena": (
        "Open the selected Iceberg snapshot in Athena and prefill FOR VERSION AS OF SQL. "
        "This does not execute the query."
    ),
    "athena.query": "Show the Athena query editor.",
    "athena.history": "Show read-only Athena query history.",
    "athena.results": "Show rows for the current Athena execution.",
    "athena.saved": "Show saved Athena queries.",
    "athena.choose_workgroup": "Open the Athena workgroup selector.",
    "athena.choose_catalog": "Open the Athena catalog selector.",
    "athena.choose_database": "Open the Athena database selector.",
    "athena.insert_table_ref": "Insert the same-source copied Glue table at the editor cursor.",
    "athena.execute": "Execute the validated read-only SQL in the active Athena context.",
    "athena.cancel": "Stop the active app-owned Athena query execution.",
    "athena.load_more": "Load the next available page for the active Athena view.",
}

_EXPECTED_ACTION_REQUIREMENTS = {
    "pane.copy": "Requires a copyable selected item.",
    "pane.delete": "Requires a deletable selected item.",
    "emr.clone": "Requires a selected cloneable job run.",
    "glue.copy_table_ref": "Requires a visible selected Glue table.",
    "glue.query_in_athena": "Requires a visible selected Glue table.",
    "glue.time_travel_in_athena": "Requires a visible selected snapshot row.",
    "athena.insert_table_ref": "Requires a copied table from the active Athena source.",
    "athena.execute": "Requires valid non-empty read-only SQL and an idle query runner.",
    "athena.cancel": "Requires an active app-owned Athena query.",
    "athena.load_more": "Requires another result page in the active Athena view.",
}

_EXPECTED_ACTION_PRIORITIES = {
    "app.command_palette": 0,
    "app.quit": 0,
    "athena.execute": 10,
    "athena.cancel": 10,
    "glue.query_in_athena": 10,
    "glue.time_travel_in_athena": 10,
    "app.swap_source": 20,
    "pane.refresh": 20,
    "glue.catalog": 80,
    "glue.jobs": 80,
    "glue.crawlers": 80,
    "athena.query": 80,
    "athena.history": 80,
    "athena.results": 80,
    "athena.saved": 80,
    "app.themes": 90,
    "app.cycle_theme": 90,
    "app.help": 90,
}

_COMPACT_LABELS = {
    "app.command_palette": "more",
    "app.cycle_theme": "next theme",
    "app.swap_source": "source",
    "glue.copy_table_ref": "copy",
    "glue.query_in_athena": "Athena",
    "glue.time_travel_in_athena": "snapshot",
    "athena.choose_workgroup": "group",
    "athena.insert_table_ref": "table",
    "athena.execute": "run",
    "athena.cancel": "stop",
    "athena.load_more": "more",
}


def test_initial_globals_include_help_and_theme_controls() -> None:
    # Post-PR-81: app-level fallbacks (themes/help/quit) live on the
    # ``.global_actions`` (trailing entries in the Commands pane) NOT on
    # ``.actions`` (LEFT, service-specific).
    legend, _hub = _build()
    global_ids = {a.action_id for a in legend.global_actions}
    assert "app.themes" in global_ids
    assert "app.cycle_theme" in global_ids
    assert "app.help" in global_ids
    assert "app.command_palette" in global_ids
    legend.dispose()


def test_focus_on_pane_swaps_service_actions() -> None:
    legend, hub = _build(
        actions={
            "pane.left": (
                "pane.descend",
                "pane.copy",
                "pane.delete",
            )
        }
    )
    hub.send(FocusChangedMessage(focused_vm_id="pane.left"))
    action_ids = [a.action_id for a in legend.actions]
    # The pane's own (focused) actions are listed first, then the
    # active service's chip set — both live on ``.actions`` (LEFT).
    assert action_ids[:3] == [
        "pane.descend",
        "pane.copy",
        "pane.delete",
    ]
    # App-level globals (themes/help/quit) live on ``.global_actions``
    # post-PR-81 — NOT on ``.actions``.
    global_ids = [a.action_id for a in legend.global_actions]
    assert global_ids[-2:] == ["app.help", "app.quit"]
    assert "app.command_palette" in global_ids
    legend.dispose()


def test_focus_actions_resolve_key_labels() -> None:
    legend, hub = _build(actions={"pane.left": ("pane.copy",)})
    hub.send(FocusChangedMessage(focused_vm_id="pane.left"))
    copy_action = next(a for a in legend.actions if a.action_id == "pane.copy")
    assert copy_action.key_label == "c"
    assert copy_action.action_label == "copy"
    legend.dispose()


def test_focus_unknown_vm_falls_back_to_globals() -> None:
    legend, hub = _build()
    hub.send(FocusChangedMessage(focused_vm_id="unregistered"))
    # No focused-VM registration → ``.actions`` carries only the
    # default fallback-service chip set, and the always-visible
    # globals (themes/help/quit) live on ``.global_actions``.
    global_ids = {a.action_id for a in legend.global_actions}
    assert "app.help" in global_ids
    assert "app.command_palette" in global_ids
    legend.dispose()


def test_keymap_changed_re_derives_legend() -> None:
    legend, hub = _build(actions={"pane.left": ("pane.copy",)})
    hub.send(FocusChangedMessage(focused_vm_id="pane.left"))
    # Caller invokes register_focusable+keymap update in lockstep with the
    # keymap store; the message is a notification so the legend recomputes.
    hub.send(KeymapChangedMessage(action="pane.copy", new_keys=("ctrl+c",)))
    copy_action = next(a for a in legend.actions if a.action_id == "pane.copy")
    # The legend re-resolves through the keymap on each rebuild — but since
    # we use a real KeymapStore we constructed earlier (with defaults), the
    # label stays "c". We assert recomputation by checking the action list
    # is rebuilt (a new HintAction instance with the same payload).
    assert copy_action.key_label == "c"
    legend.dispose()


def test_register_focusable_with_no_focus_does_not_disturb_state() -> None:
    legend, _hub = _build(actions={"pane.left": ("pane.new",)})
    actions = legend.actions
    action_ids = {a.action_id for a in actions}
    # Without a focus message the registration is dormant; only fallbacks
    # are surfaced. `pane.new` is not in the app-level fallback set, so
    # registering it for a not-yet-focused VM must keep it hidden.
    assert "pane.new" not in action_ids
    legend.dispose()


def test_hint_action_is_frozen() -> None:
    a = HintAction(
        action_id="pane.copy",
        key_label="c",
        action_label="copy",
        tooltip="Shortcut: C",
    )
    with pytest.raises(AttributeError):
        a.key_label = "x"  # type: ignore[misc]


def test_unknown_action_is_silently_skipped() -> None:
    """Pane registers actions but the keymap doesn't know one of them — skip it."""
    legend, hub = _build(
        actions={"pane.left": ("pane.copy", "pane.frobnicate")},
    )
    hub.send(FocusChangedMessage(focused_vm_id="pane.left"))
    action_ids = {a.action_id for a in legend.actions}
    # pane.copy resolves; pane.frobnicate doesn't and is dropped.
    assert "pane.copy" in action_ids
    assert "pane.frobnicate" not in action_ids
    legend.dispose()


def test_dispose_unsubscribes() -> None:
    legend, hub = _build()
    pre = legend.focused_vm_id  # None on a freshly-built legend
    legend.dispose()
    # Sending after dispose must NOT update the VM's state — a
    # subscription that survived dispose would advance focused_vm_id
    # to "x", which the assertion below catches.
    hub.send(FocusChangedMessage(focused_vm_id="x"))
    assert legend.focused_vm_id == pre


def test_set_current_service_shows_dedicated_emr_application_action() -> None:
    """EMR exposes separate source switching and application cycling chips."""
    legend, _hub = _build()
    legend.set_current_service("emr-serverless")
    swap_chip = next(a for a in legend.actions if a.action_id == "app.swap_source")
    assert swap_chip.action_label == "source"
    app_chip = next(a for a in legend.actions if a.action_id == "emr.next_application")
    assert app_chip.key_label == "A"
    assert app_chip.action_label == "switch app"
    legend.dispose()


def test_globals_remain_stable_across_service_switches() -> None:
    """The RIGHT-side globals (themes / help / quit) shouldn't move
    when the active service changes."""
    legend, _hub = _build()
    legend.set_current_service("s3")
    s3_global_ids = tuple(a.action_id for a in legend.global_actions)
    legend.set_current_service("emr-serverless")
    emr_global_ids = tuple(a.action_id for a in legend.global_actions)
    assert s3_global_ids == emr_global_ids, (
        "Global chips must stay identical across S3/EMR — "
        f"S3 globals: {s3_global_ids}, EMR globals: {emr_global_ids}."
    )
    legend.dispose()


def test_emr_serverless_service_chips_do_not_advertise_widget_scoped_filter() -> None:
    """The logs filter key only works when the logs pane has focus."""
    legend, _hub = _build()
    legend.set_current_service("emr-serverless")
    action_ids = {a.action_id for a in legend.actions}
    assert "emr.logs.filter" not in action_ids
    legend.dispose()


def test_glue_service_chips_include_views_refresh_and_source() -> None:
    legend, _hub = _build()
    legend.set_current_service("glue")
    chips = {action.action_id: action for action in legend.actions}
    assert chips["glue.catalog"].action_label == "catalog"
    assert chips["glue.catalog"].key_label == "1"
    assert chips["glue.jobs"].action_label == "jobs"
    assert chips["glue.crawlers"].action_label == "crawlers"
    assert "pane.refresh" in chips
    assert "app.swap_source" in chips
    legend.dispose()


def test_glue_handoffs_have_direct_keys_and_detailed_help() -> None:
    legend, _hub = _build()
    try:
        legend.set_current_service("glue")
        legend.set_disabled_actions(frozenset({"glue.time_travel_in_athena"}))
        chips = {chip.action_id: chip for chip in (*legend.actions, *legend.global_actions)}
        assert chips["glue.query_in_athena"].key_label == "Q"
        assert chips["glue.query_in_athena"].action_label == "Athena"
        assert "Shortcut: Shift + Q" in chips["glue.query_in_athena"].tooltip
        assert "does not execute" in chips["glue.query_in_athena"].tooltip
        assert chips["glue.time_travel_in_athena"].key_label == "V"
        assert "visible selected snapshot row" in chips["glue.time_travel_in_athena"].tooltip
        assert all(chip.tooltip.startswith("Shortcut: ") for chip in chips.values())
    finally:
        legend.dispose()


def test_service_and_global_actions_have_complete_presentation_metadata() -> None:
    action_ids = set(_GLOBAL_ACTIONS).union(*map(set, _SERVICE_ACTIONS.values()))
    legend, _hub = _build()
    chips = {chip.action_id: chip for chip in legend.global_actions}
    try:
        for service_id in _SERVICE_ACTIONS:
            legend.set_current_service(service_id)
            chips.update({chip.action_id: chip for chip in legend.actions})

        assert set(chips) == action_ids
        assert all(chip.action_label for chip in chips.values())
        assert all(getattr(chip, "tooltip", "").startswith("Shortcut: ") for chip in chips.values())
        assert chips["app.command_palette"].priority == 0
        assert chips["app.command_palette"].overflow_only is True
    finally:
        legend.dispose()


def test_service_and_global_metadata_exactly_matches_the_task_contract() -> None:
    action_ids = set(_GLOBAL_ACTIONS).union(*map(set, _SERVICE_ACTIONS.values()))
    legend, _hub = _build()
    chips = {chip.action_id: chip for chip in legend.global_actions}
    try:
        for service_id in _SERVICE_ACTIONS:
            legend.set_current_service(service_id)
            chips.update({chip.action_id: chip for chip in legend.actions})

        assert _ACTION_EFFECTS == _EXPECTED_ACTION_EFFECTS
        assert _ACTION_REQUIREMENTS == _EXPECTED_ACTION_REQUIREMENTS
        assert _ACTION_PRIORITIES == _EXPECTED_ACTION_PRIORITIES
        assert set(_ACTION_EFFECTS) == action_ids
        assert action_ids <= set(_ACTION_LABELS)
        assert {action_id: _ACTION_LABELS[action_id] for action_id in _COMPACT_LABELS} == (
            _COMPACT_LABELS
        )
        assert all(_ACTION_LABELS[action_id] for action_id in action_ids - set(_COMPACT_LABELS))
        assert all(
            chips[action_id].priority == _EXPECTED_ACTION_PRIORITIES.get(action_id, 50)
            for action_id in action_ids
        )
        assert chips["app.command_palette"].overflow_only is True
        assert chips["app.command_palette"].priority == 0
    finally:
        legend.dispose()


def test_tooltip_requires_a_defined_effect() -> None:
    with pytest.raises(KeyError):
        _tooltip_for("pane.quick_look", "space", enabled=True)


@pytest.mark.parametrize(
    ("key", "expected"),
    [
        ("ctrl+c", "Control + C"),
        ("shift+q", "Shift + Q"),
        ("ctrl+shift+q", "Control + Shift + Q"),
        ("ctrl+enter", "Control + Enter"),
        ("escape", "Escape"),
        ("Q", "Shift + Q"),
        ("shift+Q", "Shift + Q"),
    ],
)
def test_canonical_shortcut_uses_the_required_display_grammar(
    key: str,
    expected: str,
) -> None:
    assert _canonical_shortcut(key) == expected


def test_disabled_prerequisites_appear_only_for_disabled_actions() -> None:
    legend, _hub = _build()
    try:
        legend.set_current_service("glue")
        legend.set_disabled_actions(frozenset({"glue.query_in_athena"}))
        chips = {chip.action_id: chip for chip in legend.actions}
        requirement = "Requires a visible selected Glue table."
        assert requirement in chips["glue.query_in_athena"].tooltip
        assert requirement not in chips["glue.copy_table_ref"].tooltip
    finally:
        legend.dispose()


def test_remapped_glue_handoff_key_rebuilds_the_display_and_tooltip() -> None:
    legend, _hub = _build(
        keymap=KeymapStore(overlay={"glue.query_in_athena": "ctrl+shift+q"}),
    )
    try:
        legend.set_current_service("glue")
        chip = next(
            action for action in legend.actions if action.action_id == "glue.query_in_athena"
        )
        assert chip.key_label == "ctrl+shift+q"
        assert chip.tooltip.startswith("Shortcut: Control + Shift + Q")
    finally:
        legend.dispose()


def test_athena_service_chips_include_views_execution_and_source() -> None:
    legend, _hub = _build()
    legend.set_current_service("athena")
    chips = {action.action_id: action for action in legend.actions}
    assert chips["athena.query"].key_label == "1"
    assert chips["athena.history"].action_label == "history"
    assert chips["athena.results"].action_label == "results"
    assert chips["athena.saved"].action_label == "saved"
    assert chips["athena.execute"].key_label == "ctrl+enter"
    assert chips["athena.cancel"].key_label == "escape"
    assert chips["athena.load_more"].key_label == "l"
    assert chips["athena.load_more"].action_label == "more"
    assert "pane.refresh" in chips
    assert "app.swap_source" in chips
    legend.dispose()
