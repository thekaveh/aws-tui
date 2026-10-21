"""Bounded repaint of a reviewed upload plan and its per-object outcomes."""

from __future__ import annotations

from collections.abc import Callable
from typing import ClassVar

from reactivex.abc import DisposableBase
from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Horizontal, Vertical
from textual.events import Click
from textual.screen import ModalScreen
from textual.widgets import DataTable, OptionList, Select, Static, TextArea

from aws_tui.domain.cross_fs import ConflictResolution
from aws_tui.infra.redaction import redact_text
from aws_tui.ui.widgets._worker import DeferredWorkerMixin
from aws_tui.ui.widgets.modal_button import ModalButton
from aws_tui.vm.file_manager.recursive_upload_vm import RecursiveUploadVM


def review_text(vm: RecursiveUploadVM) -> str:
    plan = vm.plan
    lines = [
        f"Source: {vm.source_identity} · {vm.source_label}",
        f"Destination: {vm.destination_identity} · {vm.destination_label}",
        "Each file maps under the selected folder name at this prefix.",
    ]
    if plan is not None:
        lines += [
            f"Review: {len(plan.objects)} objects · {plan.known_bytes} bytes · {plan.conflicts} conflicts · policy {plan.policy.value}",
            f"Empty directories omitted (no folder markers): {len(plan.empty_directories)}",
            *(f"  Empty: {path.as_posix()}" for path in plan.empty_directories),
            f"Excluded entries: {len(plan.excluded)}",
            *(f"  {row.path.as_posix()}: {row.reason}" for row in plan.excluded),
        ]
    lines += [
        "Each object publishes separately; this is not an atomic folder transaction.",
        "Cancel stops queued work and aborts owned multipart uploads; published objects remain.",
        "Retry reviews failed/unattempted objects only; successful or uncertain publications are not replayed.",
    ]
    return redact_text("\n".join(lines))


def _button(label: str, identity: str) -> ModalButton:
    button = ModalButton(label, button_id=identity)
    button.id = f"upload-{identity}"
    return button


class RecursiveUploadModal(DeferredWorkerMixin, ModalScreen[None]):
    BINDINGS: ClassVar[list[BindingType]] = [Binding("escape", "close", "Close / hide")]
    DEFAULT_CSS = """
    RecursiveUploadModal { align: center middle; }
    RecursiveUploadModal > Vertical { width: 116; max-width: 98%; height: 96%; border: solid $accent; background: $surface; padding: 0 1; }
    RecursiveUploadModal .upload-title { height: 1; text-style: bold; }
    RecursiveUploadModal TextArea { height: 6; min-height: 4; border: solid $secondary; }
    RecursiveUploadModal Select { height: 3; }
    RecursiveUploadModal DataTable { height: 1fr; min-height: 3; }
    RecursiveUploadModal .upload-status { height: 2; }
    RecursiveUploadModal .modal-footer { height: 3; }
    RecursiveUploadModal .modal-footer ModalButton { width: 1fr; min-width: 10; padding: 0 1; margin: 0; }
    """

    def __init__(
        self,
        vm: RecursiveUploadVM,
        *,
        submit: Callable[[], None],
        on_drained: Callable[[], None] | None = None,
    ):
        super().__init__()
        self.vm, self._submit, self._on_drained = vm, submit, on_drained
        self._subscription: DisposableBase | None = None
        self._dismiss_requested = False
        self._dirty = True
        self._shown_plan: object = object()
        self._rows: dict[str, tuple[str, str, str, str]] = {}

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Static("Upload folder to S3 · Review", classes="upload-title", markup=False)
            yield TextArea(read_only=True, soft_wrap=True)
            yield Select(
                [
                    (label, policy.value)
                    for label, policy in (
                        ("Error on existing key (default)", ConflictResolution.ERROR),
                        ("Skip existing keys", ConflictResolution.SKIP),
                        ("Rename to an unused key", ConflictResolution.RENAME),
                        ("Overwrite reviewed existing objects", ConflictResolution.OVERWRITE),
                    )
                ],
                allow_blank=False,
                value=self.vm.policy.value,
                id="upload-policy",
            )
            yield DataTable(cursor_type="row", zebra_stripes=True)
            yield Static("", classes="upload-status", markup=False)
            with Horizontal(classes="modal-footer"):
                yield _button("Review / retry", "review")
                yield _button("Upload", "upload")
                yield _button("Cancel work", "cancel")
                yield _button("Close / hide", "close")

    def on_mount(self) -> None:
        table = self.query_one(DataTable)
        for label, width in (
            ("Source", 22),
            ("Destination key", 28),
            ("State", 12),
            ("Bytes / error", 28),
        ):
            table.add_column(label, width=width)
        self._subscription = self.vm.on_property_changed.subscribe(lambda _: self._changed())
        self.set_interval(0.1, self._redraw)
        self._redraw()
        table.focus()
        self.call_after_refresh(self._load)

    def _changed(self) -> None:
        self._dirty = True
        if self.vm.state == "closed":
            self.action_close()

    def _load(self) -> None:
        if not self._dismiss_requested:
            self._run_lifecycle_worker(
                self.vm.load, group="recursive-upload-plan", exclusive=False, exit_on_error=False
            )

    def _redraw(self) -> None:
        if not self._dirty or self._dismiss_requested or not self.is_mounted:
            return
        self._dirty = False
        if self.vm.plan is not self._shown_plan:
            self._shown_plan = self.vm.plan
            self.query_one(TextArea).load_text(review_text(self.vm))
        snapshot = self.vm.snapshot
        plan = self.vm.plan
        reviewing = (
            {row.source: row for row in plan.objects}
            if plan is not None and self.vm.state == "ready"
            else {}
        )
        rows = {}
        for row in snapshot.outcomes:
            next_row = reviewing.get(row.item.source)
            destination = next_row.destination if next_row else row.destination
            key = (
                next_row.location[1]
                if next_row
                else row.item.location[1]
                if destination == row.item.destination
                else self.vm.destination_location(destination)[1]
            )
            state = (
                ("conflict" if plan.policy == ConflictResolution.ERROR else plan.policy.value)
                if next_row and next_row.conflict and plan is not None
                else "pending"
                if next_row
                else row.status.value
            )
            warning = row.cleanup_error or (str(row.error) if row.error else "")
            if row.uncertain:
                warning = (
                    "Publication uncertain; inspect destination before any new upload. " + warning
                )
            rows[row.item.source.as_posix()] = (
                "/".join(row.item.source.segments[len(self.vm.source_root.segments) :]),
                key,
                state,
                redact_text(warning)
                if warning
                else f"{row.bytes_done}/{row.item.source_entry.size or 0}",
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
        self.query_one(".upload-status", Static).update(
            self.vm.error_text
            or f"{self.vm.state} · {snapshot.status.value} · {len(snapshot.outcomes)} objects · {snapshot.bytes_done}/{snapshot.bytes_total} bytes\nEnter on a row: full object values"
        )
        self.query_one(Select).disabled = self.vm.busy
        for button in self.query(ModalButton):
            if button.button_id == "upload":
                button.disabled = (
                    self.vm.state != "ready" or self.vm.busy or plan is None or not plan.objects
                )
            elif button.button_id == "review":
                button.disabled = self.vm.busy or (snapshot.finished and not self.vm.can_retry)
            elif button.button_id == "cancel":
                button.disabled = not self.vm.busy or self.vm.state == "planning"
            button.can_focus = not button.disabled

    def on_select_changed(self, event: Select.Changed) -> None:
        if (
            event.select.id == "upload-policy"
            and isinstance(event.value, str)
            and event.value != self.vm.policy.value
        ):
            self.vm.set_policy(ConflictResolution(event.value))
            self._load()

    def on_click(self, event: Click) -> None:
        if not isinstance(event.widget, ModalButton) or event.widget.disabled:
            return
        event.stop()
        identity = event.widget.button_id
        if identity == "upload":
            self._submit()
        elif identity == "review":
            self._load()
        elif identity == "cancel":
            self.vm.cancel()
        elif identity == "close":
            self.action_close()

    def action_commit_focused(self) -> None:
        policy = self.query_one(Select)
        if policy.expanded:
            policy.query_one(OptionList).action_select()
        elif isinstance(self.focused, ModalButton):
            self.focused.press()
        elif isinstance(self.focused, Select):
            self.focused.action_show_overlay()
        elif isinstance(self.focused, OptionList):
            self.focused.action_select()
        elif isinstance(self.focused, DataTable):
            self._redraw()
            identities = tuple(self._rows)
            if 0 <= self.focused.cursor_row < len(identities):
                self._inspect_row(identities[self.focused.cursor_row])

    def _inspect_row(self, identity: str) -> None:
        if identity not in self._rows:
            return
        _source, key, state, detail = self._rows[identity]
        viewer = self.query_one(TextArea)
        viewer.load_text(
            review_text(self.vm)
            + f"\n\nSelected object (at inspection):\nSource: {identity}\nDestination key: {key}\nState: {state}\nBytes / error: {detail}"
        )
        viewer.focus()

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        event.stop()
        if isinstance(event.row_key.value, str):
            self._inspect_row(event.row_key.value)

    def action_move_up(self) -> None:
        policy = self.query_one(Select)
        if policy.expanded:
            policy.query_one(OptionList).action_cursor_up()
        elif isinstance(self.focused, (DataTable, OptionList)):
            self.focused.action_cursor_up()

    def action_move_down(self) -> None:
        policy = self.query_one(Select)
        if policy.expanded:
            policy.query_one(OptionList).action_cursor_down()
        elif isinstance(self.focused, (DataTable, OptionList)):
            self.focused.action_cursor_down()

    def action_focus_next(self) -> None:
        self.focus_next()

    def action_focus_prev(self) -> None:
        self.focus_previous()

    def action_close(self) -> None:
        if not self._dismiss_requested and self.vm.state != "closed" and self.is_mounted:
            policy = self.query_one(Select)
            if policy.expanded:
                policy.expanded = False
                policy.focus()
                return
        self._dismiss_requested = True
        self.vm.close()
        if self.is_mounted and self.app.screen is self:
            self.dismiss()

    def on_screen_resume(self) -> None:
        if self._dismiss_requested:
            self.action_close()

    async def shutdown(self) -> None:
        self._dismiss_requested = True
        await self.vm.shutdown()
        self.workers.cancel_group(self, "recursive-upload-plan")

    async def on_unmount(self) -> None:
        if self._subscription is not None:
            self._subscription.dispose()
        await self.shutdown()
        self.vm.dispose()
        if self._on_drained is not None:
            self._on_drained()
            self._on_drained = None
