"""Read-only explanations of metadata already loaded by the Iceberg panes."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import ClassVar

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import OptionList, Static, TextArea
from textual.widgets.option_list import Option

from aws_tui.domain.iceberg import IcebergReference
from aws_tui.domain.iceberg_interpretation import (
    ObservationSummary,
    SnapshotComparison,
    SnapshotExplanation,
    explain_reference,
)


def _value(value: object | None) -> str:
    return "unknown" if value is None or value == "" else str(value)


def _time(value: datetime | None) -> str:
    return value.isoformat() if value is not None else "unknown"


def _raw(values: tuple[str, ...]) -> str:
    return " | ".join(repr(value) for value in values) if values else "unknown (absent)"


def snapshot_text(snapshot: SnapshotExplanation) -> str:
    lines = [
        "Historical snapshot summary",
        f"Snapshot: {_value(snapshot.snapshot_id)}",
        f"Committed: {_time(snapshot.committed_at)}",
        f"Parent: {_value(snapshot.parent_id)}",
        f"Operation: {_value(snapshot.operation)}",
        "",
    ]
    for metric in snapshot.metrics:
        lines.extend(
            (
                metric.key,
                f"  Raw: {_raw(metric.raw_values)}",
                f"  Explained: {_value(metric.value)} {metric.unit or '(unit unknown)'}",
                f"  {metric.explanation}",
            )
        )
    return "\n".join(lines)


def comparison_text(comparison: SnapshotComparison) -> str:
    lines = [
        "Historical snapshot summary comparison",
        f"A: {comparison.left.snapshot_id}    B: {comparison.right.snapshot_id}",
        "Delta is B - A; unknown values have no delta.",
        f"Committed A: {_time(comparison.left.committed_at)}",
        f"Committed B: {_time(comparison.right.committed_at)}",
        f"Ancestry: {comparison.ancestry.relationship}",
        comparison.ancestry.reason,
        f"Supported path: {' -> '.join(map(str, comparison.ancestry.path)) or 'unresolved'}",
        "",
    ]
    for metric in comparison.metrics:
        delta = f"{metric.delta:+d} {metric.unit}" if metric.delta is not None else "unknown"
        lines.extend(
            (
                metric.key,
                f"  A: {_value(metric.left.value)} | B: {_value(metric.right.value)} | B - A: {delta}",
                f"  Raw A: {_raw(metric.left.raw_values)} | Raw B: {_raw(metric.right.raw_values)}",
                f"  Unit: {metric.unit or 'unknown'}; {metric.left.explanation}",
            )
        )
    return "\n".join(lines)


def observation_text(summary: ObservationSummary, *, pinned_snapshot: int | None) -> str:
    lines = [
        f"CURRENT observations: {summary.collection}",
        f"Historical snapshot A: {_value(pinned_snapshot)}",
        "These are current-table metadata, not files/partitions reconstructed for snapshot A.",
        f"Sample: {summary.sample_size} {summary.collection} | Denominator: {_value(summary.denominator)}",
        f"Coverage: {summary.coverage} | Metadata row cap: {_value(summary.row_limit)}",
        f"Observed: {_time(summary.observed_at)}",
        "Statistics describe fetched rows, independent of local pagination; no extrapolation.",
        "Quantiles use nearest rank over known values; sums are known-value subtotals.",
        "",
    ]
    for name, dist, unit in (
        (
            "file_size_in_bytes"
            if summary.collection == "files"
            else "total_data_file_size_in_bytes",
            summary.size,
            "bytes",
        ),
        ("file_count", summary.file_count_distribution, "files"),
    ):
        if dist is None:
            continue
        lines.extend(
            (
                f"{name} ({unit}); known {dist.known_rows}/{summary.sample_size} rows",
                f"  Sum: {_value(dist.total)} | Min: {_value(dist.minimum)} | p50: {_value(dist.p50)} | p95: {_value(dist.p95)} | Max: {_value(dist.maximum)}",
            )
        )
    lines.append("")
    for counter in summary.counters:
        lines.append(
            f"{counter.source}: {_value(counter.value)} {counter.unit}; known {counter.known_rows}/{counter.sample_size} rows"
        )
    if summary.collection == "files":
        lines.extend(
            (
                "",
                "Content: 0 = data file; 1 = position delete; 2 = equality delete; other = unknown.",
                "Data-file record counts precede applying delete files; delete counts do not imply surviving table rows.",
            )
        )
    return "\n".join(lines)


def reference_text(reference: IcebergReference) -> str:
    lines = [
        f"Reference: {reference.name}",
        f"Type: {reference.ref_type}",
        f"Snapshot: {reference.snapshot_id}",
        "",
    ]
    for row in explain_reference(reference):
        lines.extend(
            (row.field, f"  Raw: {_value(row.raw_value)} {row.unit}", f"  {row.explanation}")
        )
    return "\n".join(lines)


class IcebergInfoModal(ModalScreen[None]):
    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("escape", "close", "Close"),
        Binding("enter", "commit_focused", "Choose", show=False, priority=True),
    ]
    DEFAULT_CSS: ClassVar[str] = """
    IcebergInfoModal { align: center middle; }
    IcebergInfoModal > Vertical { width: 104; max-width: 96%; height: 92%; padding: 1; }
    IcebergInfoModal #iceberg-info-identity { height: auto; max-height: 4; }
    IcebergInfoModal OptionList { height: auto; max-height: 4; min-height: 1; }
    IcebergInfoModal TextArea { height: 1fr; min-height: 3; margin-top: 1; }
    IcebergInfoModal #iceberg-info-help { height: 1; }
    """

    def __init__(self, identity: str, choices: tuple[tuple[str, Callable[[], str]], ...]) -> None:
        super().__init__()
        self._identity = identity
        self._choices = choices
        self._invalidated = False
        self._body = TextArea(
            identity + "\n\n" + choices[0][1](),
            read_only=True,
            soft_wrap=True,
            id="iceberg-info-body",
        )
        self._identity_label = Static(identity, id="iceberg-info-identity", markup=False)
        self._mounted = False

    def compose(self) -> ComposeResult:
        with Vertical(classes="modal-frame"):
            yield self._identity_label
            yield OptionList(
                *(Option(Text(label), id=str(i)) for i, (label, _) in enumerate(self._choices)),
                id="iceberg-info-choices",
            )
            yield self._body
            yield Static(
                "↑↓ choose · Enter opens · Tab scroll/copy · Esc close",
                id="iceberg-info-help",
                markup=False,
            )

    def on_mount(self) -> None:
        self._mounted = True
        if self._invalidated:
            self.dismiss(None)
            return
        self.query_one(OptionList).focus()

    def on_unmount(self) -> None:
        self._mounted = False
        self._choices = ()

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        event.stop()
        if not self._invalidated:
            self._body.load_text(self._identity + "\n\n" + self._choices[event.option_index][1]())

    def action_commit_focused(self) -> None:
        if isinstance(self.focused, OptionList):
            self.focused.action_select()

    def action_move_up(self) -> None:
        if isinstance(self.focused, OptionList):
            self.focused.action_cursor_up()

    def action_move_down(self) -> None:
        if isinstance(self.focused, OptionList):
            self.focused.action_cursor_down()

    def action_close(self) -> None:
        self.dismiss(None)

    def invalidate(self) -> None:
        self._invalidated = True
        # Release captured metadata even if another modal currently covers this one.
        self._choices = ()
        self._identity = ""
        self._body.clear()
        self._identity_label.update("Metadata is no longer available")
        if self._mounted and self.is_attached and self.app.screen is self:
            self.dismiss(None)

    def on_screen_resume(self) -> None:
        if self._invalidated and self.app.screen is self:
            self.dismiss(None)
