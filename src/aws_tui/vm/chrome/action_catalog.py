"""Immutable action metadata and pure contextual presentation."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Literal
from unicodedata import category


@dataclass(frozen=True, slots=True)
class ActionSpec:
    id: str
    label: str
    category: str
    keywords: tuple[str, ...] = ()
    service_ids: frozenset[str] = field(default_factory=frozenset)
    key_source: Literal["keymap", "unbound"] = "keymap"


@dataclass(frozen=True, slots=True)
class ActionPresentation:
    id: str
    label: str
    category: str
    keywords: tuple[str, ...] = ()
    service_ids: frozenset[str] = field(default_factory=frozenset)
    effective_keys: tuple[str, ...] = ()
    availability_reason: str | None = None

    @property
    def available(self) -> bool:
        return self.availability_reason is None


ACTION_SPECS: tuple[ActionSpec, ...] = (
    ActionSpec(
        "pane.object_details",
        "S3 object details",
        "File operations",
        service_ids=frozenset(("s3",)),
    ),
    ActionSpec(
        "pane.enter_multiselect",
        "Enter multi-select mode",
        "Selection",
        service_ids=frozenset(("s3",)),
    ),
    ActionSpec(
        "pane.toggle_select", "Toggle cursor selection", "Selection", service_ids=frozenset(("s3",))
    ),
    ActionSpec(
        "pane.select_all", "Select all visible entries", "Selection", service_ids=frozenset(("s3",))
    ),
    ActionSpec(
        "pane.filter", "Filter loaded entries", "Loaded listing", service_ids=frozenset(("s3",))
    ),
    ActionSpec(
        "pane.fuzzy_find", "Find loaded entry", "Loaded listing", service_ids=frozenset(("s3",))
    ),
    ActionSpec(
        "pane.sort",
        "Sort loaded entries",
        "Loaded listing",
        service_ids=frozenset(("s3",)),
        key_source="unbound",
    ),
    ActionSpec(
        "pane.clear_filter",
        "Clear pane filter",
        "Loaded listing",
        service_ids=frozenset(("s3",)),
        key_source="unbound",
    ),
    ActionSpec(
        "pane.clear_selection", "Clear selection", "Selection", service_ids=frozenset(("s3",))
    ),
    ActionSpec(
        "pane.exit_multiselect",
        "Exit multi-select mode",
        "Selection",
        service_ids=frozenset(("s3",)),
    ),
    ActionSpec("app.themes", "Theme picker", "App"),
    ActionSpec("app.transfer_history", "Transfer history and recovery", "App"),
    ActionSpec("app.cycle_theme", "Cycle theme", "App"),
    ActionSpec(
        "app.swap_source",
        "Switch source",
        "Source",
        service_ids=frozenset(("athena", "emr-serverless", "glue", "s3")),
    ),
    ActionSpec(
        "auth.authenticate",
        "Retry active source credentials",
        "Source",
        service_ids=frozenset(("athena", "emr-serverless", "glue", "s3")),
    ),
    ActionSpec(
        "pane.copy_entry_path",
        "Copy cursor entry path",
        "File operations",
        service_ids=frozenset(("s3",)),
    ),
    ActionSpec(
        "pane.copy_path", "Copy pane path", "File operations", service_ids=frozenset(("s3",))
    ),
    ActionSpec(
        "emr.next_application",
        "Next EMR application",
        "EMR Serverless",
        service_ids=frozenset(("emr-serverless",)),
    ),
    ActionSpec("glue.catalog", "Glue catalog", "Glue", service_ids=frozenset(("glue",))),
    ActionSpec("glue.jobs", "Glue jobs", "Glue", service_ids=frozenset(("glue",))),
    ActionSpec("glue.crawlers", "Glue crawlers", "Glue", service_ids=frozenset(("glue",))),
    ActionSpec(
        "glue.choose_run_state", "Choose Glue run state", "Glue", service_ids=frozenset(("glue",))
    ),
    ActionSpec(
        "glue.choose_crawler_state",
        "Choose Glue crawler state",
        "Glue",
        service_ids=frozenset(("glue",)),
    ),
    ActionSpec(
        "glue.copy_table_ref", "Copy Glue table reference", "Glue", service_ids=frozenset(("glue",))
    ),
    ActionSpec(
        "glue.open_s3_location",
        "Open table location in S3",
        "Glue",
        service_ids=frozenset(("glue",)),
        key_source="unbound",
    ),
    ActionSpec(
        "glue.query_in_athena", "Query table in Athena", "Glue", service_ids=frozenset(("glue",))
    ),
    ActionSpec("glue.load_more", "Load more Glue rows", "Glue", service_ids=frozenset(("glue",))),
    ActionSpec(
        "glue.time_travel_in_athena",
        "Query Iceberg snapshot in Athena",
        "Glue",
        service_ids=frozenset(("glue",)),
    ),
    ActionSpec("athena.query", "Athena query", "Athena", service_ids=frozenset(("athena",))),
    ActionSpec("athena.history", "Athena history", "Athena", service_ids=frozenset(("athena",))),
    ActionSpec("athena.results", "Athena results", "Athena", service_ids=frozenset(("athena",))),
    ActionSpec(
        "athena.saved", "Athena saved queries", "Athena", service_ids=frozenset(("athena",))
    ),
    ActionSpec(
        "athena.choose_workgroup",
        "Choose Athena workgroup",
        "Athena",
        service_ids=frozenset(("athena",)),
    ),
    ActionSpec(
        "athena.choose_catalog",
        "Choose Athena catalog",
        "Athena",
        service_ids=frozenset(("athena",)),
    ),
    ActionSpec(
        "athena.choose_database",
        "Choose Athena database",
        "Athena",
        service_ids=frozenset(("athena",)),
    ),
    ActionSpec(
        "athena.insert_table_ref",
        "Insert copied table reference",
        "Athena",
        service_ids=frozenset(("athena",)),
    ),
    ActionSpec(
        "athena.execute", "Execute Athena query", "Athena", service_ids=frozenset(("athena",))
    ),
    ActionSpec(
        "athena.cancel", "Cancel Athena query", "Athena", service_ids=frozenset(("athena",))
    ),
    ActionSpec(
        "athena.load_more", "Load more Athena rows", "Athena", service_ids=frozenset(("athena",))
    ),
    ActionSpec(
        "athena.inspect_cell", "Inspect Athena cell", "Athena", service_ids=frozenset(("athena",))
    ),
    ActionSpec(
        "athena.copy_cell", "Copy Athena cell as JSON", "Athena", service_ids=frozenset(("athena",))
    ),
    ActionSpec(
        "athena.copy_row", "Copy Athena row as JSON", "Athena", service_ids=frozenset(("athena",))
    ),
    ActionSpec(
        "athena.filter_results",
        "Filter loaded Athena results",
        "Athena",
        service_ids=frozenset(("athena",)),
    ),
    ActionSpec(
        "athena.sort_results",
        "Sort loaded Athena results",
        "Athena",
        service_ids=frozenset(("athena",)),
    ),
    ActionSpec(
        "athena.reset_results",
        "Reset loaded Athena results",
        "Athena",
        service_ids=frozenset(("athena",)),
    ),
    ActionSpec(
        "athena.open_result_location",
        "Open Athena result in S3",
        "Athena",
        service_ids=frozenset(("athena",)),
        key_source="unbound",
    ),
    ActionSpec(
        "athena.open_in_glue",
        "Open query table in Glue",
        "Athena",
        service_ids=frozenset(("athena",)),
        key_source="unbound",
    ),
    ActionSpec("service.open.s3", "Go to S3", "Services", keywords=("go", "open", "s3")),
    ActionSpec(
        "service.open.athena", "Go to Athena", "Services", keywords=("go", "open", "athena")
    ),
    ActionSpec("service.open.glue", "Go to Glue", "Services", keywords=("go", "open", "glue")),
    ActionSpec(
        "service.open.emr-serverless",
        "Go to EMR Serverless",
        "Services",
        keywords=("go", "open", "emr", "serverless"),
    ),
    ActionSpec("app.open_settings", "Settings", "Services", keywords=("go", "open", "settings")),
    ActionSpec("app.help", "Help", "App"),
    ActionSpec("app.quit", "Quit", "App"),
    ActionSpec("app.command_palette", "Command palette", "App"),
    ActionSpec("pane.switch_focus", "Next focus target", "Navigation"),
    ActionSpec("pane.switch_focus_back", "Previous focus target", "Navigation"),
    ActionSpec("pane.move_up", "Move up", "Navigation"),
    ActionSpec("pane.move_down", "Move down", "Navigation"),
    ActionSpec("pane.descend", "Open focused item", "Navigation"),
    ActionSpec("pane.ascend", "Ascend to parent", "Navigation", service_ids=frozenset(("s3",))),
    ActionSpec(
        "pane.modal_left",
        "Move left / parent",
        "Navigation",
        service_ids=frozenset(("emr-serverless", "s3")),
    ),
    ActionSpec(
        "pane.modal_right",
        "Next log file",
        "Navigation",
        service_ids=frozenset(("emr-serverless",)),
    ),
    ActionSpec(
        "pane.refresh",
        "Refresh active view",
        "Navigation",
        service_ids=frozenset(("athena", "emr-serverless", "glue", "s3")),
    ),
    ActionSpec(
        "pane.copy", "Copy selected entries", "File operations", service_ids=frozenset(("s3",))
    ),
    ActionSpec(
        "pane.delete", "Delete selected entries", "File operations", service_ids=frozenset(("s3",))
    ),
    ActionSpec("pane.mark_up", "Extend selection up", "Selection", service_ids=frozenset(("s3",))),
    ActionSpec(
        "pane.mark_down", "Extend selection down", "Selection", service_ids=frozenset(("s3",))
    ),
    ActionSpec("pane.quick_look", "Quick Look", "File operations", service_ids=frozenset(("s3",))),
    ActionSpec(
        "emr.clone",
        "Clone selected EMR job run",
        "EMR Serverless",
        service_ids=frozenset(("emr-serverless",)),
    ),
    ActionSpec(
        "emr.cancel",
        "Cancel selected EMR job run",
        "EMR Serverless",
        keywords=("cancel", "job", "run"),
        service_ids=frozenset(("emr-serverless",)),
    ),
    ActionSpec(
        "emr.logs.source",
        "Choose EMR log source",
        "EMR Serverless",
        service_ids=frozenset(("emr-serverless",)),
    ),
    ActionSpec(
        "emr.logs.follow",
        "Start or stop following CloudWatch logs",
        "EMR Serverless",
        service_ids=frozenset(("emr-serverless",)),
    ),
    ActionSpec(
        "emr.logs.filter",
        "Filter EMR logs",
        "EMR Serverless",
        service_ids=frozenset(("emr-serverless",)),
    ),
)


def project_actions(
    specs: Sequence[ActionSpec],
    *,
    registered_ids: frozenset[str],
    bindings: Mapping[str, tuple[str, ...]],
    active_service_id: str | None,
    unavailable_reasons: Mapping[str, str],
) -> tuple[ActionPresentation, ...]:
    rows = []
    for spec in specs:
        keys = bindings[spec.id] if spec.key_source == "keymap" else ()
        reason = (
            "handler_missing"
            if spec.id not in registered_ids
            else "service_inactive"
            if spec.service_ids and active_service_id not in spec.service_ids
            else unavailable_reasons.get(spec.id)
        )
        rows.append(
            ActionPresentation(
                spec.id,
                spec.label,
                spec.category,
                spec.keywords,
                spec.service_ids,
                tuple(keys),
                reason,
            )
        )
    return tuple(rows)


_KEY_LABELS = {
    "backspace": "Backspace",
    "enter": "Enter",
    "escape": "Esc",
    "left": "←",
    "right": "→",
    "tab": "Tab",
    "up": "↑",
    "down": "↓",
    "question_mark": "?",
    "colon": ":",
    "slash": "/",
    "space": "Space",
    "alt": "Alt",
    "ctrl": "Ctrl",
    "meta": "Meta",
    "shift": "Shift",
}
_SERVICE_LABELS = {
    "s3": "S3",
    "athena": "Athena",
    "glue": "Glue",
    "emr-serverless": "EMR Serverless",
}


def format_effective_keys(keys: tuple[str, ...]) -> str:
    return (
        " / ".join(
            "+".join(_KEY_LABELS.get(part.casefold(), part) for part in key.split("+"))
            for key in keys
        )
        or "Unbound"
    )


def scope_label(service_ids: frozenset[str], active_service_id: str | None) -> str:
    if not service_ids:
        return "Global"
    service = active_service_id if active_service_id in service_ids else sorted(service_ids)[0]
    return _SERVICE_LABELS.get(service, service)


def literal_display(value: str) -> str:
    """Expose control characters without interpreting user punctuation."""
    return "".join(ascii(char)[1:-1] if category(char).startswith("C") else char for char in value)
