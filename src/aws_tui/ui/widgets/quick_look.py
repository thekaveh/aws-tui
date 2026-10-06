"""Generation-owned Quick Look loading and literal, cached preview rendering."""

from __future__ import annotations

import asyncio
from contextlib import suppress
from functools import partial

from rich.cells import cell_len
from rich.console import Group
from rich.table import Table
from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Container, ScrollableContainer
from textual.css.query import NoMatches
from textual.screen import ModalScreen
from textual.widgets import Static
from textual.worker import Worker
from vmx import Message, MessageHub

from aws_tui.domain.preview import PreviewFormat, PreviewResult, load_legacy_preview, safe_text
from aws_tui.domain.preview_limits import (
    PREVIEW_MAX_COLUMNS,
    PREVIEW_MAX_RENDER_CHARS,
    PREVIEW_MAX_ROWS,
    RAW_PREVIEW_BYTES,
)
from aws_tui.infra.redaction import redact_text
from aws_tui.ui.widgets._subscriber import HubSubscriberMixin
from aws_tui.ui.widgets._worker import DeferredWorkerMixin
from aws_tui.vm.chrome.quick_look_vm import QuickLookContent, QuickLookVM


class QuickLook(HubSubscriberMixin, DeferredWorkerMixin, ModalScreen[None]):
    """Quick Look modal; toggling never reloads the captured preview."""

    DEFAULT_CSS = """
    QuickLook #quicklook-mode { height: 1; }
    QuickLook #quicklook-body-scroll { height: 1fr; }
    QuickLook #quicklook-body { height: auto; }
    """

    BINDINGS = [  # noqa: RUF012
        ("escape", "close", "Close"),
        ("space", "close", "Close"),
        ("r", "toggle_raw", "Raw / structured"),
        ("left", "move_left", "Scroll left"),
        ("right", "move_right", "Scroll right"),
    ]

    def __init__(self, vm: QuickLookVM, *, hub: MessageHub[Message]) -> None:
        super().__init__()
        self._vm = vm
        self._hub = hub
        self._generation = 0
        self._active = False
        self._result: PreviewResult | None = None
        self._raw_mode = False
        self._preview_workers: list[Worker[None]] = []

    @property
    def vm(self) -> QuickLookVM:
        return self._vm

    def compose(self) -> ComposeResult:
        with Container():
            content = self._vm.content
            title = safe_text(content.title if content else "(no preview)")[0]
            yield Static(Text(title), id="quicklook-title", classes="quicklook-title", markup=False)
            yield Static("Loading", id="quicklook-mode", markup=False)
            with ScrollableContainer(id="quicklook-body-scroll"):
                yield Static(
                    "loading...", id="quicklook-body", classes="quicklook-body", markup=False
                )

    def action_move_up(self) -> None:
        self._scroll_body(-1)

    def action_move_down(self) -> None:
        self._scroll_body(1)

    def _scroll_body(self, delta: int) -> None:
        # The App forwards its priority up/down bindings to this modal.
        with suppress(NoMatches):
            scroll = self.query_one("#quicklook-body-scroll", ScrollableContainer)
            if delta < 0:
                scroll.scroll_up(animate=False)
            else:
                scroll.scroll_down(animate=False)

    def action_move_left(self) -> None:
        self.query_one("#quicklook-body-scroll", ScrollableContainer).scroll_left(animate=False)

    def action_move_right(self) -> None:
        self.query_one("#quicklook-body-scroll", ScrollableContainer).scroll_right(animate=False)

    def action_focus_prev(self) -> None:
        self.action_move_left()

    def action_focus_next(self) -> None:
        self.action_move_right()

    def action_toggle_raw(self) -> None:
        if self._result is None or self._result.format is PreviewFormat.RAW:
            return
        self._raw_mode = not self._raw_mode
        self._render_result()

    def on_mount(self) -> None:
        self._active = True
        self.subscribe_to_vm(
            hub=self._hub,
            vm=self._vm,
            property_names=("content", "is_open"),
            on_property_changed=self._on_property_changed,
        )
        self._schedule_preview()

    def _on_property_changed(self, _property: str) -> None:
        self._schedule_preview()

    def _invalidate_preview(self) -> None:
        # Invalidate first: cancellation may drain asynchronously or be suppressed.
        self._generation += 1
        for worker in self._preview_workers:
            if not worker.is_finished:
                worker.cancel()
        self._preview_workers = [w for w in self._preview_workers if not w.is_finished]

    def _schedule_preview(self) -> None:
        self._invalidate_preview()
        self._result = None
        self._raw_mode = False
        content = self._vm.content
        if not self._active or not self._vm.is_open or content is None:
            return
        self.call_after_refresh(partial(self._start_preview, content, self._generation))

    def _start_preview(self, content: QuickLookContent, generation: int) -> None:
        if not self._can_publish(content, generation):
            return
        self.query_one("#quicklook-title", Static).update(Text(safe_text(content.title)[0]))
        self.query_one("#quicklook-mode", Static).update("Loading")
        self.query_one("#quicklook-body", Static).update("loading...")
        self._preview_workers.append(
            self._run_lifecycle_worker(
                partial(self._load_preview, content, generation),
                group="quick-look-preview",
                exclusive=False,
                exit_on_error=False,
            )
        )

    def _can_publish(self, content: QuickLookContent, generation: int) -> bool:
        return (
            generation == self._generation
            and self._vm.content is content
            and self._vm.is_open
            and self.is_mounted
            and self._active
        )

    async def _load_preview(self, content: QuickLookContent, generation: int) -> None:
        if content.chunks is None:
            return
        try:
            result = (
                await content.load_preview()
                if content.load_preview is not None
                else await load_legacy_preview(
                    content.chunks, name=content.title, mime=content.mime
                )
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            if self._can_publish(content, generation):
                message = safe_text(redact_text(str(exc)))[0]
                self.query_one("#quicklook-mode", Static).update("Unavailable")
                self.query_one("#quicklook-body", Static).update(
                    Text(f"preview unavailable: {message}")
                )
            return
        if not self._can_publish(content, generation):
            return
        self._result = result
        self._render_result()

    def _render_result(self) -> None:
        """Render only the cached, bounded result; all file content is literal."""
        result = self._result
        if result is None:
            return
        body = self.query_one("#quicklook-body", Static)
        scroll = self.query_one("#quicklook-body-scroll", ScrollableContainer)
        raw = self._raw_mode or result.format is PreviewFormat.RAW
        notes = tuple(safe_text(note)[0] for note in result.notes[:PREVIEW_MAX_ROWS])
        table = Table(expand=False, padding=(0, 1))
        table_width = 0
        if not raw:
            headers = [
                safe_text(column.name)[0]
                + (f" : {safe_text(column.type_name)[0]}" if column.type_name is not None else "")
                for column in result.columns[:PREVIEW_MAX_COLUMNS]
            ]
            rows = [
                [safe_text(cell.text)[0] for cell in row[: len(headers)]]
                for row in result.rows[:PREVIEW_MAX_ROWS]
            ]
            for index, header in enumerate(headers):
                width = max(
                    cell_len(header),
                    *(cell_len(row[index]) for row in rows if index < len(row)),
                    1,
                )
                table.add_column(Text(header), width=width, no_wrap=True, overflow="crop")
            table_width = sum(column.width or 0 for column in table.columns) + 3 * len(headers) + 1
            # Padding and borders count too, including sparse rows with wide columns.
            # Each note character may wrap, plus the note's final newline.
            render_chars = (table_width + 1) * (len(rows) + 4) + sum(
                2 * len(note) + 1 for note in notes
            )
            # Zero-width characters add text beyond the terminal-cell rectangle.
            # Wide characters already fit within that conservative estimate.
            render_chars += sum(
                max(0, len(value) - cell_len(value))
                for values in (headers, *rows)
                for value in values
            )
            if render_chars > PREVIEW_MAX_RENDER_CHARS:
                raw = True
                notes += ("Structured output exceeds preview budget",)
            else:
                for row in rows:
                    table.add_row(*(Text(cell) for cell in row))
        mode = "Raw" if raw else f"Structured · {result.format.value.upper()}"
        self.query_one("#quicklook-mode", Static).update(
            Text(mode + " · r: raw / structured · ←/→: scroll")
        )
        if raw:
            text = "".join(
                char if char in "\n\t" else safe_text(char)[0]
                for char in result.raw[:RAW_PREVIEW_BYTES].decode("utf-8", errors="replace")
            )
            if notes:
                text += "\n\n" + "\n".join(notes)
            body.styles.width = "1fr"
            body.update(Text(text[:PREVIEW_MAX_RENDER_CHARS]))
        else:
            body.styles.width = table_width
            body.update(Group(table, *(Text(note) for note in notes)))
        scroll.scroll_to(x=0, y=0, animate=False, force=True)

    async def on_unmount(self) -> None:
        self._active = False
        self.unsubscribe_from_vm()
        self._invalidate_preview()
        # Keep worker handles until their engine finally blocks have drained.
        await asyncio.gather(
            *(worker.wait() for worker in self._preview_workers), return_exceptions=True
        )
        self._preview_workers.clear()

    def action_close(self) -> None:
        self._vm.close_command.execute()
        self.dismiss(None)


__all__ = ["QuickLook"]
