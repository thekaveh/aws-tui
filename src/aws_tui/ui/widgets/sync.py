"""Read-only sync comparison, explicit selection and revision-bound admission."""

from __future__ import annotations

from typing import ClassVar, cast

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Horizontal, Vertical
from textual.widgets import DataTable, Input, Select, Static, TextArea

from aws_tui.domain.filesystem import ConflictError
from aws_tui.domain.sync_plan import SyncComparison, SyncRow, SyncSettings, parse_sync_patterns
from aws_tui.infra.redaction import redact_text
from aws_tui.ui.widgets.modal_button import ModalButton
from aws_tui.ui.widgets.recursive_upload import RecursiveUploadModal, _button
from aws_tui.vm.file_manager.sync_vm import SyncVM


def sync_review_text(vm: SyncVM) -> str:
    lines = [
        f"Source: {vm.source_identity} · {vm.source_label}",
        f"Destination: {vm.destination_identity} · {vm.destination_label}",
        "SHA-256 compares full bytes; existing equal-size objects require S3 GET/download and possibly KMS access costs.",
        "Size/time is a heuristic and may miss same-size edits with retained timestamps.",
        "Filters: case-sensitive relative POSIX globs; * spans / and dotfiles. Quote patterns containing spaces. Exclude wins.",
        "Only selected creates/updates publish. No pruning or source deletion. Each object publishes separately.",
        "Cancel retains published objects. Retry freshly compares failed/unattempted selected paths only; started-canceled and uncertain items need inspection and a separate new review.",
    ]
    if vm.review is not None:
        lines += [
            f"Comparison: {vm.settings.comparison.value} · {vm.selected_count} selected · {vm.selected_bytes} bytes · {vm.remaining_count} remaining",
            f"Empty folders omitted: {len(vm.review.execution.empty_directories)}",
            *(f"  Empty: {path.as_posix()}" for path in vm.review.execution.empty_directories),
            *(
                f"  Excluded: {row.path.as_posix()} — {row.reason}"
                for row in vm.review.execution.excluded
            ),
        ]
        if vm.selected_count == 0:
            if vm.remaining_count == 0 and all(row.state == "unchanged" for row in vm.rows):
                lines.append(
                    "Up to date within the reviewed scope. No mutations or confirmation needed."
                )
            else:
                lines.append(
                    "No selected mutations. Inspect blocked/omitted rows and remaining work; no confirmation or write will run."
                )
    return redact_text("\n".join(lines))


class SyncModal(RecursiveUploadModal):
    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("escape", "close", "Close / hide"),
        Binding("space", "toggle_operation", "Include / omit"),
    ]
    DEFAULT_CSS = """
    SyncModal TextArea { height: 4; min-height: 3; }
    SyncModal .sync-cost { height: 1; }
    SyncModal .sync-filters { height: 3; }
    SyncModal .sync-filters Input { width: 1fr; border: solid $secondary; }
    """

    @property
    def sync_vm(self) -> SyncVM:
        return cast(SyncVM, self.vm)

    def compose(self) -> ComposeResult:
        self._field_revision = 0
        self._loaded_controls: tuple[object, ...] | None = None
        with Vertical():
            yield Static("Sync folder to S3 · Review", classes="upload-title", markup=False)
            yield Static(
                "Equal-size SHA-256: S3 GET/download/KMS costs. Size/time is heuristic.",
                classes="sync-cost",
                markup=False,
            )
            yield TextArea(sync_review_text(self.sync_vm), read_only=True, soft_wrap=True)
            yield Select(
                [
                    ("Full-byte SHA-256 (GET/download)", SyncComparison.CHECKSUM.value),
                    ("Size/time heuristic (may miss edits)", SyncComparison.SIZE_TIME.value),
                ],
                value=self.sync_vm.settings.comparison.value,
                allow_blank=False,
                id="sync-comparison",
            )
            with Horizontal(classes="sync-filters"):
                include = Input("*", placeholder="Default *", id="sync-include")
                include.border_title = "Include globs"
                yield include
                exclude = Input("", placeholder="No exclusions", id="sync-exclude")
                exclude.border_title = "Exclude globs"
                yield exclude
            yield DataTable(cursor_type="row", zebra_stripes=True)
            yield Static("", classes="upload-status", markup=False)
            with Horizontal(classes="modal-footer"):
                yield _button("Replan / retry", "review")
                yield _button("Sync", "upload")
                yield _button("Cancel work", "cancel")
                yield _button("Close / hide", "close")

    def on_mount(self) -> None:
        # Public reactive watchers run synchronously on native value changes.
        # Input.Changed/Select.Changed messages alone arrive too late for ABA edits.
        for control in (*self.query(Input), *self.query(Select)):
            self.watch(control, "value", self._field_changed, init=False)

    def _controls(self) -> tuple[object, ...]:
        return (
            self._field_revision,
            self.query_one(Select).value,
            self.query_one("#sync-include", Input).value,
            self.query_one("#sync-exclude", Input).value,
        )

    def _field_changed(self) -> None:
        self._field_revision += 1
        self._loaded_controls = None
        self.sync_vm.invalidate_settings()

    def validate_controls(self) -> None:
        """Check actual native values at both confirmation boundaries."""
        if self._loaded_controls is None or self._loaded_controls != self._controls():
            self.sync_vm.invalidate_settings()
            raise ConflictError("Sync settings changed; replan before confirming")

    def _load(self) -> None:
        if self._dismiss_requested or self.vm.busy:
            return
        try:
            settings = SyncSettings(
                comparison=SyncComparison(str(self.query_one(Select).value)),
                include=parse_sync_patterns(self.query_one("#sync-include", Input).value),
                exclude=parse_sync_patterns(self.query_one("#sync-exclude", Input).value),
            )
            self.sync_vm.set_settings(settings)
        except Exception as exc:
            self.sync_vm.invalidate_settings()
            self.vm.error_text = redact_text(str(exc))
            self._changed()
            return
        controls = self._controls()

        async def load() -> None:
            await self.vm.load()
            if controls == self._controls() and self.vm.state == "ready":
                self._loaded_controls = controls
                self._changed()

        self._run_lifecycle_worker(
            load, group="recursive-upload-plan", exclusive=False, exit_on_error=False
        )

    def on_select_changed(self, event: Select.Changed) -> None:
        # The synchronous watcher has already retired admission. Replan explicitly.
        event.stop()

    def action_toggle_operation(self) -> None:
        if (
            not isinstance(self.focused, DataTable)
            or self.vm.busy
            or self.vm.state != "ready"
            or self._shown_plan is not self.vm.plan
        ):
            return
        table = self.focused
        if not table.is_valid_coordinate(table.cursor_coordinate):
            return
        identity = table.coordinate_to_cell_key(table.cursor_coordinate).row_key.value
        row = next(
            (row for row in self.sync_vm.rows if row.comparison.source.as_posix() == identity),
            None,
        )
        if row is not None and row.comparison.label.value in {"create", "update"}:
            self.sync_vm.toggle_operation(row.comparison.source)

    def action_commit_focused(self) -> None:
        if isinstance(self.focused, Input):
            self._load()
        else:
            super().action_commit_focused()

    def _display_location(self, row: SyncRow) -> tuple[str, str] | None:
        if row.item is not None:
            return row.item.location
        try:
            # Excluded local entries have no executable UploadObject. Display the
            # captured provider's pure mapping only if that mapping is available.
            bucket, key = self.sync_vm.destination_location(row.destination)
            return bucket, key
        except Exception:
            return None

    def _redraw(self) -> None:
        if not self._dirty or self._dismiss_requested or not self.is_mounted:
            return
        self._dirty = False
        vm = self.sync_vm
        if self.vm.plan is not self._shown_plan:
            self._shown_plan = self.vm.plan
            self.query_one(TextArea).load_text(sync_review_text(vm))
        rows = {}
        for row in vm.rows:
            comparison, outcome = row.comparison, row.outcome
            warning = (outcome.cleanup_error or str(outcome.error or "")) if outcome else ""
            detail = warning or comparison.reason
            size = comparison.known_bytes
            location = self._display_location(comparison)
            rows[comparison.source.as_posix()] = (
                ("[x] " if row.selected else "[ ] ") + comparison.relative,
                location[1] if location else "Unmapped: " + comparison.destination.as_posix(),
                row.state,
                redact_text(
                    f"{outcome.bytes_done if outcome else 0}/{size if size is not None else '?'} · {detail}"
                ),
            )
        table = self.query_one(DataTable)
        if rows.keys() != self._rows.keys():
            table.clear()
            self._rows = {}
        for identity, values in rows.items():
            if identity not in self._rows:
                table.add_row(*(Text(value) for value in values), key=identity)
            elif values != self._rows[identity]:
                for column, value in zip(table.columns, values, strict=True):
                    table.update_cell(identity, column, Text(value))
        self._rows = rows
        snapshot = vm.snapshot
        self.query_one(".upload-status", Static).update(
            vm.error_text
            or f"{vm.state} · {vm.selected_count} selected · {vm.remaining_count} remaining · attempt {snapshot.bytes_done}/{snapshot.bytes_total} bytes\nSpace: include/omit · Enter: full values · edits require Replan"
        )
        for control in (*self.query(Input), *self.query(Select)):
            control.disabled = vm.busy and vm.state != "planning"
        for control in self.query(ModalButton):
            if control.button_id == "upload":
                control.disabled = vm.busy or vm.state != "ready" or vm.selected_count == 0
            elif control.button_id == "review":
                control.disabled = vm.busy or (snapshot.finished and not vm.can_retry)
            elif control.button_id == "cancel":
                control.disabled = not vm.busy or vm.state == "planning"
            control.can_focus = not control.disabled

    def _inspect_row(self, identity: str) -> None:
        row = next(
            (row for row in self.sync_vm.rows if row.comparison.source.as_posix() == identity), None
        )
        if row is None:
            return
        comparison, outcome = row.comparison, row.outcome
        destination = "mapping unavailable; reviewed path " + comparison.destination.as_posix()
        location = self._display_location(comparison)
        if location is not None:
            bucket, key = location
            destination = f"s3://{bucket}/{key}"
        details = [
            sync_review_text(self.sync_vm),
            "",
            "Selected object (at inspection):",
            f"Source: {comparison.source.as_posix()}",
            f"Destination: {destination}",
            f"State: {row.state} · selected: {row.selected}",
            f"Bytes: {comparison.known_bytes}",
            f"Reason: {comparison.reason}",
            f"Evidence: {comparison.evidence}",
        ]
        if outcome is not None:
            details += [
                f"Attempt: {outcome.status.value} · {outcome.bytes_done} bytes · published: {outcome.published} · uncertain: {outcome.uncertain}",
                f"Error: {outcome.error or ''}",
                f"Cleanup: {outcome.cleanup_error or ''}",
            ]
        viewer = self.query_one(TextArea)
        viewer.load_text(redact_text("\n".join(details)))
        viewer.focus()
