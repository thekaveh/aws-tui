"""Reviewed local result exports; this dialog never initiates SQL."""

from __future__ import annotations

from functools import partial
from pathlib import Path
from typing import TYPE_CHECKING, ClassVar, cast

from reactivex.abc import DisposableBase
from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.dom import DOMNode
from textual.events import Click
from textual.widgets import Input, Select, Static, Switch

from aws_tui.ui.widgets._worker import DeferredWorkerMixin
from aws_tui.ui.widgets.athena.authoring_dialogs import _PrivateSqlInput, _SqlPrompt
from aws_tui.ui.widgets.modal_button import ModalButton
from aws_tui.vm.athena.export_vm import ExportFormat, ExportReview, ExportScope

if TYPE_CHECKING:
    from aws_tui.vm.athena.page_vm import AthenaPageVM


class AthenaExportModal(DeferredWorkerMixin, _SqlPrompt):
    BINDINGS: ClassVar[list[BindingType]] = [Binding("escape", "cancel", "Cancel / close")]
    DEFAULT_CSS: ClassVar[str] = """
    AthenaExportModal { align: center middle; background: $background 60%; }
    AthenaExportModal > Vertical { width: 76; max-width: 96%; height: 90%; max-height: 34; padding: 1 2; }
    AthenaExportModal VerticalScroll { height: 1fr; min-height: 0; }
    AthenaExportModal Static { height: auto; }
    AthenaExportModal #athena-export-form { height: auto; }
    AthenaExportModal Select, AthenaExportModal Input { height: 3; }
    AthenaExportModal Horizontal { height: 3; align-horizontal: right; }
    AthenaExportModal ModalButton { height: 3; min-width: 10; margin: 0 0 0 1; }
    AthenaExportModal #athena-export-status { color: $text-muted; }
    """

    def __init__(self, page: AthenaPageVM) -> None:
        super().__init__()
        self._page = page
        self._vm = page.exports
        self._review: ExportReview | None = None
        self._working = False
        self._sub: DisposableBase | None = None

    def compose(self) -> ComposeResult:
        with Vertical(classes="modal-frame"):
            yield Static("Export Athena results", classes="modal-title")
            with VerticalScroll():
                with Vertical(id="athena-export-form"):
                    yield Static("Scope", markup=False)
                    yield Select(
                        [
                            ("Loaded view (filtered and sorted)", "loaded"),
                            ("Complete execution", "complete"),
                        ],
                        value="loaded",
                        allow_blank=False,
                        id="athena-export-scope",
                    )
                    yield Static("Format", markup=False)
                    yield Select(
                        [
                            ("CSV (normalized)", "csv"),
                            ("JSON (positional)", "json"),
                            ("Original AWS CSV (complete only)", "original"),
                        ],
                        value="csv",
                        allow_blank=False,
                        id="athena-export-format",
                    )
                    yield Static("Local destination (parent directory must exist)", markup=False)
                    yield _PrivateSqlInput(id="athena-export-path", max_length=4096)
                yield Static(
                    "Review the scope and destination before writing.",
                    id="athena-export-summary",
                    markup=False,
                )
                yield Static(
                    "Replace the exact existing file shown in the review",
                    id="athena-export-replace-label",
                    markup=False,
                )
                yield Switch(False, id="athena-export-replace")
                yield Static("", id="athena-export-status", markup=False)
            with Horizontal(classes="modal-footer"):
                for label, key in (("Close", "cancel"), ("Review", "review"), ("Export", "write")):
                    button = ModalButton(
                        label, button_id=key, classes="-primary" if key == "write" else ""
                    )
                    button.id = f"athena-export-{key}"
                    yield button

    def on_mount(self) -> None:
        self._sub = self._page.on_property_changed.subscribe(self._on_page_changed)
        self._refresh()
        self.query_one("#athena-export-path", Input).focus()

    def _on_page_changed(self, _name: str) -> None:
        self.call_after_refresh(self._refresh)

    def on_unmount(self) -> None:
        self._vm.cancel()
        if self._sub is not None:
            self._sub.dispose()
            self._sub = None

    def _refresh(self) -> None:
        if not self.is_mounted:
            return
        busy = self._working or self._vm.busy
        reviewed = self._review is not None
        self.query_one("#athena-export-form").display = not reviewed
        for key in ("scope", "format", "path"):
            self.query_one(f"#athena-export-{key}").disabled = busy or reviewed
        existing = self._review is not None and self._review.destination.version is not None
        self.query_one("#athena-export-replace-label").display = existing
        replacement = self.query_one("#athena-export-replace", Switch)
        replacement.display = existing
        replacement.disabled = busy
        review_button = self.query_one("#athena-export-review", ModalButton)
        review_button.disabled = busy
        review_button.update("Edit" if reviewed else "Review")
        write = self.query_one("#athena-export-write", ModalButton)
        write.disabled = busy or not reviewed or (existing and not replacement.value)
        self.query_one("#athena-export-status", Static).update(self._vm.status)

    def on_switch_changed(self, event: Switch.Changed) -> None:
        if event.switch.id == "athena-export-replace":
            self._refresh()

    def on_click(self, event: Click) -> None:
        node: DOMNode | None = event.widget
        while node is not None:
            if isinstance(node, ModalButton):
                event.stop()
                if node.disabled:
                    return
                if node.button_id == "cancel":
                    self.action_cancel()
                elif not self._working and not self._invalidated:
                    if node.button_id == "review" and self._review is not None:
                        self._review = None
                        self.query_one("#athena-export-replace", Switch).value = False
                        self.query_one("#athena-export-summary", Static).update(
                            "Review the scope and destination before writing."
                        )
                        self._refresh()
                    elif node.button_id in {"review", "write"}:
                        self._working = True
                        self._refresh()
                        self._run_lifecycle_worker(
                            partial(self._perform, node.button_id),
                            group="athena-export",
                            exclusive=False,
                        )
                return
            node = node.parent

    async def _perform(self, action: str) -> None:
        try:
            if action == "review":
                self.query_one("#athena-export-replace", Switch).value = False
                scope = self.query_one("#athena-export-scope", Select).value
                format = self.query_one("#athena-export-format", Select).value
                path = self.query_one("#athena-export-path", Input).value
                if not path:
                    self.query_one("#athena-export-summary", Static).update(
                        "Enter a local destination path."
                    )
                    return
                self._review = await self._vm.prepare(
                    scope=cast(ExportScope, scope),
                    format=cast(ExportFormat, format),
                    path=Path(path),
                )
                self.query_one("#athena-export-replace", Switch).value = False
                if self._review is not None and not self._invalidated:
                    review = self._review
                    source = review.context
                    count = (
                        str(review.row_count)
                        if review.row_count is not None
                        else "Unknown (complete execution)"
                    )
                    self.query_one("#athena-export-summary", Static).update(
                        f"{'Loaded view (filtered/sorted)' if review.scope == 'loaded' else 'Complete execution (ignores filters)'}\n"
                        f"Source: {source.connection_name} / {source.region}\n"
                        f"Workgroup: {source.workgroup} · Execution: {review.execution_id}\n"
                        f"Catalog / database: {source.catalog} / {source.database}\n"
                        f"Rows: {count} · Format: {review.format}\n"
                        f"Destination: {review.destination.path}\n"
                        f"{'Existing file: explicit replacement required' if review.destination.version is not None else 'New file: competing files will not be replaced'}"
                    )
            elif self._review is not None:
                replacement = self.query_one("#athena-export-replace", Switch)
                overwrite = replacement.value
                replacement.value = False
                receipt = await self._vm.run(self._review, overwrite=overwrite)
                self._review = None
                self.query_one("#athena-export-summary", Static).update(
                    "Review a new export before writing again."
                )
                if receipt is not None:
                    self.app.notify(
                        f"Athena export completed ({receipt.bytes_written} bytes).", title="Athena"
                    )
        finally:
            self._working = False
            if self.is_mounted:
                if self._invalidated:
                    self.dismiss(None)
                else:
                    self._refresh()

    def invalidate(self) -> None:
        self._invalidated = True
        self._vm.cancel()
        if not self._working:
            super().invalidate()

    def action_cancel(self) -> None:
        self._vm.cancel()
        if not self._working:
            self.dismiss(None)
