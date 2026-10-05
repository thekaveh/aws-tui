"""Explicit history inspection and new-copy recovery; disk work belongs to workers."""

from __future__ import annotations

import asyncio
from functools import partial
from typing import TYPE_CHECKING, ClassVar, Literal, cast

from reactivex.abc import DisposableBase
from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Horizontal, Vertical
from textual.events import Click
from textual.screen import ModalScreen
from textual.widgets import OptionList, Static, TextArea
from textual.widgets.option_list import Option
from vmx import Message, MessageHub, PropertyChangedMessage

from aws_tui.domain.cross_fs import ConflictResolution
from aws_tui.domain.transfer_history import TransferConnectionIdentity, TransferHistoryRecord
from aws_tui.ui.widgets.confirm_modal import TextualDialogService
from aws_tui.ui.widgets.modal_button import ModalButton
from aws_tui.vm.chrome.confirm_vm import ConfirmRequest
from aws_tui.vm.file_manager.transfer_history_vm import (
    RecoveryRefused,
    RetryPlan,
    TransferHistoryVM,
)

if TYPE_CHECKING:
    from textual.worker import Worker

    from aws_tui.app import AwsTuiApp

Mode = Literal["history", "recovery"]


def record_details(record: TransferHistoryRecord) -> str:
    """Full literal metadata, including zero/unknown distinctions."""

    def connection(value: TransferConnectionIdentity | None) -> str:
        if value is None:
            return "none"
        return f"{value.kind}: {value.name}\nFingerprint: {value.fingerprint}"

    return "\n".join(
        (
            f"ID: {record.id}",
            f"Operation: {record.operation}",
            f"Outcome: {record.status.replace('_', ' ')}",
            f"Publication: {record.publication.replace('_', ' ')}",
            f"Source: {record.source_uri}",
            f"Source connection: {connection(record.source_connection)}",
            f"Destination: {record.destination_uri if record.destination_uri is not None else 'none'}",
            f"Destination connection: {connection(record.destination_connection)}",
            f"Bytes: {record.bytes_done} / {record.bytes_total if record.bytes_total is not None else 'unknown'}",
            f"Started UTC: {record.started_at.isoformat()}",
            f"Updated UTC: {record.updated_at.isoformat()}",
            f"Finished UTC: {record.finished_at.isoformat() if record.finished_at is not None else 'unknown'}",
            f"Failure reason: {record.failure_reason or 'none'}",
        )
    )


def _history_button(label: str, *, button_id: str, classes: str = "") -> ModalButton:
    button = ModalButton(label, button_id=button_id, classes=classes)
    button.id = f"history-{button_id}"
    return button


class RetryConflictModal(ModalScreen[ConflictResolution | None]):
    """Fresh endpoint review; Enter defaults to refusing overwrites."""

    DEFAULT_CSS = """
    RetryConflictModal { align: center middle; }
    RetryConflictModal > Vertical { width: 90%; max-width: 100; height: 90%; padding: 1 2;
        border: round; }
    RetryConflictModal TextArea { height: 1fr; }
    RetryConflictModal .modal-footer { height: 3; }
    RetryConflictModal .modal-footer > ModalButton.-primary { min-width: 20; }
    RetryConflictModal .modal-footer > ModalButton { min-width: 10; margin: 0; padding: 0 1; width: 1fr; }
    """
    BINDINGS: ClassVar[list[BindingType]] = [Binding("escape", "cancel", "Cancel", priority=True)]

    def __init__(self, plan: RetryPlan) -> None:
        super().__init__()
        self.plan = plan

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Static(
                "Retry copy — fresh conflict decision", classes="modal-title", markup=False
            )
            yield TextArea(
                f"Source: {self.plan.source_uri}\nConnection: {self.plan.source_connection.name}\n"
                f"Fingerprint: {self.plan.source_connection.fingerprint}\n"
                f"Destination: {self.plan.destination_uri}\nConnection: {self.plan.destination_connection.name}\n"
                f"Fingerprint: {self.plan.destination_connection.fingerprint}\n"
                f"Source bytes: {self.plan.source.size if self.plan.source.size is not None else 'unknown'}\n"
                f"Destination currently: {'absent' if self.plan.destination is None else 'present'}\n"
                "Both endpoints will be checked again after your decision.\n"
                "Refuse overwrite is the default. No previous outcome is inferred.",
                read_only=True,
                show_line_numbers=False,
                id="retry-endpoints",
            )
            with Horizontal(classes="modal-footer"):
                for policy, label in (
                    (ConflictResolution.ERROR, "Refuse overwrite"),
                    (ConflictResolution.SKIP, "Skip"),
                    (ConflictResolution.RENAME, "Rename"),
                    (ConflictResolution.OVERWRITE, "Overwrite"),
                ):
                    yield ModalButton(
                        label,
                        button_id=policy.value,
                        classes="-primary"
                        if policy is ConflictResolution.ERROR
                        else "-danger"
                        if policy is ConflictResolution.OVERWRITE
                        else "",
                    )
                yield ModalButton("Cancel", button_id="cancel")

    def on_mount(self) -> None:
        self.query(ModalButton).first().focus()

    def on_click(self, event: Click) -> None:
        if isinstance(event.widget, ModalButton):
            event.stop()
            self.dismiss(
                None
                if event.widget.button_id == "cancel"
                else ConflictResolution(event.widget.button_id)
            )

    def action_cancel(self) -> None:
        self.dismiss(None)


class TransferHistoryModal(ModalScreen[None]):
    """Bounded, keyboard-accessible history with cancellation-safe UI replies."""

    DEFAULT_CSS = """
    TransferHistoryModal { align: center middle; }
    TransferHistoryModal > Vertical { width: 94%; max-width: 112; height: 94%;
        padding: 0 1; border: round; }
    TransferHistoryModal .modal-title { height: 1; text-style: bold; }
    TransferHistoryModal #history-status { height: 2; }
    TransferHistoryModal OptionList { height: 5; border: solid; }
    TransferHistoryModal TextArea { height: 1fr; min-height: 3; border: solid; }
    TransferHistoryModal .modal-footer { height: 3; }
    TransferHistoryModal .modal-footer > ModalButton { min-width: 9; margin: 0; padding: 0 1; width: 1fr; }
    """
    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("escape,q", "close", "Close", priority=True),
        Binding("h", "history", "History", priority=True),
        Binding("r", "recovery", "Recovery", priority=True),
    ]

    def __init__(
        self, vm: TransferHistoryVM, *, hub: MessageHub[Message], mode: Mode = "history"
    ) -> None:
        super().__init__()
        self.vm, self._hub, self.mode = vm, hub, mode
        self._sub: DisposableBase | None = None
        self._worker: Worker[None] | None = None
        self._busy = False
        self._history_closed = False
        self._rows: tuple[TransferHistoryRecord, ...] = ()
        self._feedback: str | None = None

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Static("Transfers — History / Recovery", classes="modal-title", markup=False)
            yield Static("Loading transfer history…", id="history-status", markup=False)
            yield OptionList(id="transfer-history-list")
            yield TextArea(read_only=True, show_line_numbers=False, id="transfer-history-details")
            with Horizontal(classes="modal-footer"):
                yield _history_button("History (h)", button_id="history", classes="-primary")
                yield _history_button("Recovery (r)", button_id="recovery")
                yield _history_button("Copy details", button_id="copy-details")
                yield _history_button("Close (Esc)", button_id="close")
            with Horizontal(classes="modal-footer"):
                yield _history_button("Recheck", button_id="recheck")
                yield _history_button("Retry copy", button_id="retry")
                yield _history_button("Clear history", button_id="clear")
                yield _history_button("Reload", button_id="reload")

    def on_mount(self) -> None:
        self._sub = self._hub.messages.subscribe(on_next=self._on_history_hub_message)
        self.call_after_refresh(self._refresh_records)
        self.query_one(OptionList).focus()

    def on_unmount(self) -> None:
        self._history_closed = True
        if self._sub is not None:
            self._sub.dispose()
        if self._worker is not None and not self._worker.is_finished:
            self._worker.cancel()

    def on_resize(self) -> None:
        if self.is_mounted and not self._history_closed:
            self.call_after_refresh(self._refresh_records)

    def _on_history_hub_message(self, message: object) -> None:
        if (
            isinstance(message, PropertyChangedMessage)
            and message.sender_object in (self.vm, self.vm.runtime)
            and not self._history_closed
        ):
            self.call_after_refresh(self._refresh_records)

    def _refresh_records(self) -> None:
        if self._history_closed or not self.is_mounted:
            return
        choices = self.query_one(OptionList)
        selected = self.selected_record
        self._rows = tuple(record for record in self.vm.records)
        if self.mode == "recovery":
            order = {"never_attempted": 0, "possibly_published": 1, "confirmed_terminal": 2}
            self._rows = tuple(sorted(self._rows, key=lambda record: order[record.publication]))
        choices.clear_options()
        from rich.text import Text

        for record in self._rows:
            # A bounded preview distinguishes transfers; the full literal value
            # remains selectable/copyable in details. OptionList wraps Text even
            # with no_wrap, so constrain the preview to its measured viewport.
            label = Text(
                f"{record.operation} · {record.status.replace('_', ' ')} · {record.publication.replace('_', ' ')} · {record.source_uri}"
            )
            label.truncate(max(1, choices.scrollable_content_region.width - 2), overflow="ellipsis")
            choices.add_option(Option(label, id=record.id))
        choices.highlighted = (
            next(
                (
                    index
                    for index, record in enumerate(self._rows)
                    if selected is not None and record.id == selected.id
                ),
                0,
            )
            if self._rows
            else None
        )
        self._refresh_details()
        state = self.vm.load_state
        status = (
            "Loading transfer history…"
            if state in {"idle", "loading"}
            else (self.vm.error_text or "Transfer history could not be loaded.")
            if state == "error"
            else f"{self.mode.title()}: {len(self._rows)} record(s)"
            if self._rows
            else f"{self.mode.title()}: no records"
        )
        self.query_one("#history-status", Static).update(
            "\n".join(text for text in (self._feedback, self.vm.runtime.error_text) if text)
            or status
        )

    @property
    def selected_record(self) -> TransferHistoryRecord | None:
        if not self.is_mounted:
            return None
        index = self.query_one(OptionList).highlighted
        return self._rows[index] if index is not None and index < len(self._rows) else None

    def _refresh_details(self) -> None:
        record = self.selected_record
        self.query_one(TextArea).load_text(
            record_details(record)
            if record
            else "Select a record to inspect full literal paths and connection fingerprints.\nRecovery never assumes publication succeeded."
        )

    def on_option_list_option_highlighted(self, event: OptionList.OptionHighlighted) -> None:
        event.stop()
        self._refresh_details()

    def action_move_up(self) -> None:
        self.query_one(OptionList).action_cursor_up()

    def action_move_down(self) -> None:
        self.query_one(OptionList).action_cursor_down()

    def action_history(self) -> None:
        self.mode = "history"
        self._feedback = None
        self._refresh_records()

    def action_recovery(self) -> None:
        self.mode = "recovery"
        self._feedback = None
        self._refresh_records()

    def action_close(self) -> None:
        self._history_closed = True
        if self._worker is not None:
            self._worker.cancel()
        self.dismiss()

    def on_click(self, event: Click) -> None:
        if not isinstance(event.widget, ModalButton):
            return
        event.stop()
        action = event.widget.button_id
        if action == "close":
            self.action_close()
        elif action == "history":
            self.action_history()
        elif action == "recovery":
            self.action_recovery()
        elif action == "copy-details":
            app = cast("AwsTuiApp", self.app)
            app.copy_value(self.query_one(TextArea).text, "transfer details")
        elif not self._busy:
            record = self.selected_record
            if action in {"recheck", "retry"} and record is None:
                return
            app = cast("AwsTuiApp", self.app)
            self._busy = True
            self._worker = app._run_lifecycle_worker(
                partial(self._operate, action, record),
                group="transfer-copy" if action == "retry" else "transfer-history-operation",
                exclusive=False,
            )

    async def _decide_conflict(self, plan: RetryPlan) -> ConflictResolution | None:
        if self._history_closed:
            return None
        screen = RetryConflictModal(plan)
        try:
            result = await self.app.push_screen_wait(screen)
            return None if self._history_closed else result
        finally:
            if screen in self.app.screen_stack:
                await screen.dismiss(None)

    async def _operate(self, action: str, record: TransferHistoryRecord | None) -> None:
        app = cast("AwsTuiApp", self.app)
        try:
            self._feedback = None
            if action == "reload":
                await self.vm.load()
            elif action == "clear":
                ctx = app.app_ctx
                request = ConfirmRequest(
                    title="Clear transfer history?",
                    body_lines=(
                        "Remove validated history and interrupted metadata only.",
                        "Source and destination files are preserved. Active transfers continue.",
                    ),
                    confirm_label="Clear history",
                    danger=True,
                )
                if ctx.confirm_vm.is_open:
                    return
                confirmed = await ctx.confirm_vm.ask(
                    request, dialog_service=TextualDialogService(app, ctx.confirm_vm, hub=ctx.hub)
                )
                if confirmed and not self._history_closed:
                    await self.vm.clear()
            elif action == "recheck" and record is not None:
                inspection = await self.vm.recheck(record.id)
                self._feedback = (
                    f"Destination {'absent' if inspection.destination is None else 'present'}. "
                    + (
                        "A new copy may be requested; prior outcome remains unknown."
                        if inspection.retry_eligible
                        else "Retry refused; prior outcome unchanged."
                    )
                )
            elif action == "retry" and record is not None:
                tid = await self.vm.retry(record.id, self._decide_conflict)
                if tid is not None:
                    result = next(
                        (item for item in app.app_ctx.transfers_vm.transfers if item.id == tid),
                        None,
                    )
                    self._feedback = f"New copy: {result.state.value.replace('_', ' ') if result is not None else 'result unavailable'}. See History."
                await self.vm.load()
        except RecoveryRefused as error:
            self._feedback = str(error)
        except asyncio.CancelledError:
            raise
        except Exception:
            self._feedback = "Transfer history operation could not be completed."
        finally:
            if action == "retry":
                app._refresh_transfer_history()
            self._busy = False
            if not self._history_closed and self.is_mounted:
                self._refresh_records()


__all__ = ["RetryConflictModal", "TransferHistoryModal", "record_details"]
