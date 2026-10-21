"""Literal historical metadata, exact-key entry and explicit destination prompts."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import asdict
from typing import ClassVar, Literal

from reactivex.abc import DisposableBase
from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Horizontal, Vertical
from textual.events import Click
from textual.screen import ModalScreen
from textual.widgets import DataTable, Input, Static, TextArea

from aws_tui.ui.widgets._worker import DeferredWorkerMixin
from aws_tui.ui.widgets.modal_button import ModalButton
from aws_tui.vm.file_manager.s3_versions_vm import S3VersionsVM


def _button(label: str, identity: str) -> ModalButton:
    button = ModalButton(label, button_id=identity)
    button.id = f"s3-versions-{identity}"
    return button


def version_text(vm: S3VersionsVM) -> str:
    lines = ["Historical S3 version · read-only inspection", f"Source prefix: {vm.prefix_label}"]
    version = vm.selected
    if version is None:
        if vm.listing is not None:
            return "\n".join(
                [
                    *lines,
                    vm.listing.notice,
                    "No retained versions were found for this exact key.",
                ]
            )
        return "\n".join([*lines, vm.error_text or "Enter an exact key or select a version."])
    lines += [
        f"Exact key: s3://{version.bucket}/{version.key}",
        f"Version ID: {version.version_id}",
        f"Modified: {version.modified.isoformat()}",
        "Size: unknown (delete marker)" if version.size is None else f"Size: {version.size} bytes",
        f"Latest when listed: {'yes' if version.is_latest else 'no'}",
        f"Delete marker: {'yes — no file content' if version.is_delete_marker else 'no'}",
        f"ETag: {version.etag or 'unavailable'}",
    ]
    if vm.listing is not None:
        lines += [vm.listing.notice]
    if version.version_id == "null":
        lines += [
            "Literal null can be replaced without changing its ID; changed observations are refused."
        ]
    lines += [
        "Download retrieves this selected historical content. Recovery creates a different NEW key; the current key is not replaced.",
        "Headers and tags are separately observed, not an atomic metadata snapshot. Checksums are reported, not verified.",
    ]
    if vm.details is not None:
        lines += [
            "",
            "Selected-version metadata:",
            json.dumps(asdict(vm.details), ensure_ascii=False, indent=2, default=str),
        ]
    if vm.detail_error:
        lines += ["", f"Metadata unavailable: {vm.detail_error}"]
    return "\n".join(lines)


class VersionDestinationModal(ModalScreen[str | None]):
    BINDINGS: ClassVar[list[BindingType]] = [Binding("escape", "cancel", "Cancel")]
    DEFAULT_CSS = """
    VersionDestinationModal { align: center middle; }
    VersionDestinationModal > Vertical { width: 82; max-width: 96%; height: auto; max-height: 94%; border: solid $accent; background: $surface; padding: 1 2; }
    VersionDestinationModal Static { height: auto; max-height: 4; }
    VersionDestinationModal .modal-footer { height: 3; align: center middle; }
    """

    def __init__(self, *, mode: Literal["download", "recovery"], scope: str, value: str) -> None:
        super().__init__()
        self.mode, self.scope, self.value = mode, scope, value
        self._done = False
        self._invalidated = False

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Static(
                "Download historical version"
                if self.mode == "download"
                else "Recover historical version to a NEW key",
                markup=False,
            )
            yield Static(self.scope, markup=False)
            yield Static(
                "Enter one local filename:"
                if self.mode == "download"
                else "Enter a different exact key relative to the source prefix:",
                markup=False,
            )
            yield Input(self.value, id="s3-version-destination")
            yield Static(
                "Existing destinations are never overwritten. The next step reviews source and destination.",
                markup=False,
            )
            with Horizontal(classes="modal-footer"):
                yield _button("Review", "review")
                yield _button("Cancel", "cancel")

    def on_mount(self) -> None:
        self.query_one(Input).focus()

    def action_submit(self) -> None:
        self.call_after_refresh(self._submit_settled)

    def _submit_settled(self) -> None:
        if self.is_mounted and self.app.screen is self and not self._done and not self._invalidated:
            value = self.query_one(Input).value
            if value:
                self._done = True
                self.dismiss(value)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        event.stop()
        self.action_submit()

    def on_click(self, event: Click) -> None:
        if isinstance(event.widget, ModalButton):
            event.stop()
            self.action_submit() if event.widget.button_id == "review" else self.action_cancel()

    def action_cancel(self) -> None:
        if not self._done:
            self._done = True
            self.dismiss(None)

    def invalidate(self) -> None:
        self._invalidated = True
        if self.is_mounted and self.app.screen is self:
            self.action_cancel()

    def on_screen_resume(self) -> None:
        if self._invalidated:
            self.action_cancel()

    def action_focus_next(self) -> None:
        self.focus_next()

    def action_focus_prev(self) -> None:
        self.focus_previous()


class S3VersionsModal(DeferredWorkerMixin, ModalScreen[None]):
    BINDINGS: ClassVar[list[BindingType]] = [Binding("escape", "close", "Close")]
    DEFAULT_CSS = """
    S3VersionsModal { align: center middle; }
    S3VersionsModal > Vertical { width: 110; max-width: 98%; height: 94%; border: solid $accent; background: $surface; padding: 0 1; }
    S3VersionsModal .versions-title { height: 1; text-style: bold; }
    S3VersionsModal .versions-key-row { height: 3; }
    S3VersionsModal .versions-key-row Input { width: 1fr; }
    S3VersionsModal .versions-key-row ModalButton { min-width: 8; width: 10; margin: 0; padding: 0 1; }
    S3VersionsModal .versions-status { height: 1; }
    S3VersionsModal DataTable { height: 1fr; min-height: 4; }
    S3VersionsModal TextArea { height: 1fr; min-height: 4; border: solid $secondary; }
    S3VersionsModal .modal-footer { height: 3; }
    S3VersionsModal .modal-footer ModalButton { width: 1fr; min-width: 10; padding: 0 1; margin: 0; }
    """

    def __init__(
        self,
        vm: S3VersionsVM,
        *,
        local_scope: str | None,
        submit: Callable[[Literal["download", "recovery"], str], None],
        on_drained: Callable[[], None] | None = None,
    ) -> None:
        super().__init__()
        self.vm, self.local_scope, self._submit = vm, local_scope, submit
        self._on_drained = on_drained
        self._subscription: DisposableBase | None = None
        self._source_subscription: DisposableBase | None = None
        self._shown: object | None = None
        self._dismiss_requested = False
        self._destination: VersionDestinationModal | None = None

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Static(
                "S3 object versions · historical retrieval / new-key recovery",
                classes="versions-title",
                markup=False,
            )
            with Horizontal(classes="versions-key-row"):
                yield Input(
                    self.vm.key,
                    placeholder="Exact key relative to this source prefix",
                    id="s3-versions-key",
                )
                yield _button("List", "list")
            yield Static("", classes="versions-status", markup=False)
            yield DataTable(cursor_type="row", zebra_stripes=True)
            yield TextArea(read_only=True, soft_wrap=True)
            with Horizontal(classes="modal-footer"):
                yield _button("Inspect old", "inspect")
                yield _button("Download", "download")
                yield _button("Recover NEW", "recover")
                yield _button("Close", "close")

    def on_mount(self) -> None:
        table = self.query_one(DataTable)
        for label, width in [
            ("Version ID", 18),
            ("Modified UTC", 19),
            ("Bytes", 7),
            ("Latest", 6),
            ("Marker", 7),
        ]:
            table.add_column(label, width=width)
        self._subscription = self.vm.on_property_changed.subscribe(lambda _: self._changed())
        self._redraw()
        if self.vm.key:
            table.focus()
            self.call_after_refresh(self._schedule_load)
        else:
            self.query_one(Input).focus()

    def _changed(self) -> None:
        if self.vm.state == "closed":
            self.action_close()
        elif self.is_mounted and not self._dismiss_requested:
            self._redraw()

    def _redraw(self) -> None:
        if self._dismiss_requested:
            return
        table = self.query_one(DataTable)
        listing = self.vm.listing
        if listing is not self._shown:
            self._shown = listing
            table.clear()
            if listing is not None:
                for index, row in enumerate(listing.versions):
                    table.add_row(
                        Text(row.version_id),
                        Text(row.modified.strftime("%Y-%m-%d %H:%M:%S")),
                        Text(str(row.size) if row.size is not None else "unknown"),
                        Text("yes" if row.is_latest else "no"),
                        Text("delete" if row.is_delete_marker else "no"),
                        key=str(index),
                    )
        if listing is not None and self.vm.selected in listing.versions:
            index = listing.versions.index(self.vm.selected)
            if table.cursor_row != index:
                table.move_cursor(row=index)
        self.query_one(TextArea).load_text(version_text(self.vm))
        self.query_one(".versions-status", Static).update(
            self.vm.error_text
            or (
                "Reading selected version…"
                if self.vm.busy
                else f"{self.vm.state} · {self.vm.prefix_label}"
            )
        )
        selected = self.vm.selected
        available = selected is not None and not selected.is_delete_marker and not self.vm.busy
        for button in self.query(ModalButton):
            if button.button_id in {"inspect", "download", "recover"}:
                button.disabled = not available or (
                    button.button_id == "download" and self.local_scope is None
                )
            elif button.button_id == "list":
                button.disabled = not self.vm.key or self.vm.busy
            button.can_focus = not button.disabled

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "s3-versions-key" and event.value == event.input.value:
            self.vm.set_key(event.value)

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        if not self._dismiss_requested:
            self.vm.select(event.cursor_row)

    def _schedule_load(self) -> None:
        if not self._dismiss_requested:
            self._run_lifecycle_worker(self._load, group="s3-versions-list", exit_on_error=False)

    async def _load(self) -> None:
        focused = self.focused
        await self.vm.load()
        if (
            self.is_mounted
            and not self._dismiss_requested
            and self.app.screen is self
            and self.vm.state == "ready"
            and self.focused is focused
            and (
                isinstance(focused, (Input, DataTable))
                or (isinstance(focused, ModalButton) and focused.button_id == "list")
            )
        ):
            self.query_one(DataTable).focus()

    def action_commit_focused(self) -> None:
        if isinstance(self.focused, Input):
            self._queue_key_commit()
        elif isinstance(self.focused, ModalButton):
            self.focused.press()
        elif isinstance(self.focused, DataTable):
            self._inspect()

    def _queue_key_commit(self) -> None:
        # App-priority Enter can precede a pasted Input's queued edit. Admit
        # the visible key after that queue has settled, on this modal only.
        self.call_after_refresh(self._commit_key)

    def _commit_key(self) -> None:
        if self.is_mounted and not self._dismiss_requested and self.app.screen is self:
            self.vm.set_key(self.query_one(Input).value)
            self._schedule_load()

    def _inspect(self) -> None:
        if not self._dismiss_requested:
            self._run_lifecycle_worker(
                self.vm.inspect, group="s3-versions-inspect", exit_on_error=False
            )

    def _request_destination(self, mode: Literal["download", "recovery"]) -> None:
        selected = self.vm.selected
        if (
            self._dismiss_requested
            or self._destination is not None
            or self.vm.busy
            or selected is None
            or selected.is_delete_marker
        ):
            return
        if mode == "download" and self.local_scope is None:
            return
        scope = self.local_scope if mode == "download" else self.vm.prefix_label
        value = (
            (selected.path.name or "historical.bin")
            if mode == "download"
            else self.vm.key + ".recovered"
        )
        screen = VersionDestinationModal(mode=mode, scope=scope or "", value=value)
        self._destination = screen

        def dismissed(value: str | None) -> None:
            self._destination = None
            if value is not None and not self._dismiss_requested:
                self._submit(mode, value)

        self.app.push_screen(screen, dismissed)

    def on_click(self, event: Click) -> None:
        if not isinstance(event.widget, ModalButton) or event.widget.disabled:
            return
        event.stop()
        identity = event.widget.button_id
        if identity == "list":
            self._queue_key_commit()
        elif identity == "inspect":
            self._inspect()
        elif identity in {"download", "recover"}:
            self._request_destination("download" if identity == "download" else "recovery")
        elif identity == "close":
            self.action_close()

    def action_move_up(self) -> None:
        if isinstance(self.focused, DataTable):
            self.focused.action_cursor_up()

    def action_move_down(self) -> None:
        if isinstance(self.focused, DataTable):
            self.focused.action_cursor_down()

    def action_focus_next(self) -> None:
        self.focus_next()

    def action_focus_prev(self) -> None:
        self.focus_previous()

    def action_close(self) -> None:
        self._dismiss_requested = True
        self.vm.close()
        if self._destination is not None:
            self._destination.invalidate()
        if self.is_mounted and self.app.screen is self:
            self.dismiss()

    def on_screen_resume(self) -> None:
        if self._dismiss_requested:
            self.action_close()

    async def shutdown(self) -> None:
        """Drain browser reads before the app closes provider clients."""
        self._dismiss_requested = True
        self.vm.close()
        # The VM already cancelled its provider task. Cancelling the waiting
        # Textual worker first would propagate a second cancellation into the
        # provider's cleanup. Drain that owned cleanup before retiring workers.
        await self.vm.shutdown()
        self.workers.cancel_group(self, "s3-versions-list")
        self.workers.cancel_group(self, "s3-versions-inspect")

    async def on_unmount(self) -> None:
        self._dismiss_requested = True
        if self._subscription is not None:
            self._subscription.dispose()
        if self._source_subscription is not None:
            self._source_subscription.dispose()
        await self.shutdown()
        self.vm.dispose()
        if self._on_drained is not None:
            self._on_drained()
            self._on_drained = None
