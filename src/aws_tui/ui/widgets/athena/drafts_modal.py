from __future__ import annotations

from typing import ClassVar, Literal

from reactivex.abc import DisposableBase
from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Grid, Vertical, VerticalScroll
from textual.css.query import NoMatches
from textual.dom import DOMNode
from textual.screen import ModalScreen
from textual.widgets import Button, OptionList, Static
from textual.widgets.option_list import Option
from vmx import Message, MessageHub

from aws_tui.ui.widgets._worker import DeferredWorkerMixin
from aws_tui.ui.widgets.confirm_modal import TextualDialogService
from aws_tui.vm.athena.drafts_vm import AthenaDraftsVM
from aws_tui.vm.athena.page_vm import AthenaPageVM
from aws_tui.vm.chrome.confirm_vm import ConfirmationVM, ConfirmRequest
from aws_tui.vm.chrome.focus_coordinator_vm import FocusCoordinatorVM

DraftModalResult = Literal["restored", "closed"]


async def ask_draft_confirmation(
    host: DOMNode,
    *,
    drafts: AthenaDraftsVM,
    hub: MessageHub[Message],
    request: ConfirmRequest,
) -> bool:
    confirmation = ConfirmationVM(hub=hub, dispatcher=drafts.dispatcher)
    confirmation.construct()
    try:
        return await confirmation.ask(
            request,
            dialog_service=TextualDialogService(host.app, confirmation, hub=hub),
        )
    finally:
        confirmation.dispose()


class AthenaDraftsModal(DeferredWorkerMixin, ModalScreen[DraftModalResult]):
    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("tab", "focus_next", show=False, priority=True),
        Binding("shift+tab", "focus_previous", show=False, priority=True),
        Binding("up", "move_up", show=False, priority=True),
        Binding("down", "move_down", show=False, priority=True),
        Binding("enter", "commit_focused", show=False, priority=True),
        Binding("escape", "close", show=False, priority=True),
    ]
    DEFAULT_CSS: ClassVar[str] = """
    AthenaDraftsModal { align: center middle; background: $background 60%; }
    AthenaDraftsModal > Vertical { width: 76; max-width: 96%; height: 90%; border: round $accent; background: $surface; padding: 0 1; }
    AthenaDraftsModal .modal-title { height: 1; color: $accent; text-style: bold; }
    #athena-drafts-warning { height: auto; color: $warning; }
    #athena-drafts-list { height: 1fr; min-height: 3; }
    #athena-drafts-detail-scroll { height: 7; scrollbar-size: 1 1; }
    #athena-drafts-detail { height: auto; }
    #athena-drafts-actions { grid-size: 3 2; height: 6; }
    #athena-drafts-actions Button { width: 1fr; min-width: 8; }
    """

    def __init__(
        self,
        page: AthenaPageVM,
        *,
        hub: MessageHub[Message],
        focus_coordinator: FocusCoordinatorVM | None = None,
    ) -> None:
        super().__init__()
        if page.drafts is None:
            raise ValueError("Local drafts are unavailable")
        self._page = page
        self._drafts = page.drafts
        self._hub = hub
        self._focus_coordinator = focus_coordinator
        self._ids: list[str] = []
        self._subscription: DisposableBase | None = None
        self._pending = False
        self._loading = True

    def compose(self) -> ComposeResult:
        with Vertical(classes="modal-frame"):
            yield Static("Local Athena SQL drafts", classes="modal-title", markup=False)
            warning = Static("", id="athena-drafts-warning", markup=False)
            warning.display = False
            yield warning
            yield OptionList(id="athena-drafts-list")
            with VerticalScroll(id="athena-drafts-detail-scroll"):
                yield Static("", id="athena-drafts-detail", markup=False)
            with Grid(id="athena-drafts-actions", classes="modal-footer"):
                yield Button("Restore", id="athena-drafts-restore", flat=True, disabled=True)
                yield Button("Delete", id="athena-drafts-delete", flat=True, disabled=True)
                yield Button("Clear all", id="athena-drafts-clear", flat=True, disabled=True)
                yield Button("Keep current editor", id="athena-drafts-keep", flat=True)
                yield Button("Close", id="athena-drafts-close", flat=True)

    def on_mount(self) -> None:
        self._subscription = self._drafts.on_property_changed.subscribe(self._on_runtime_changed)
        self.call_after_refresh(self._refresh_drafts)
        self._run_lifecycle_worker(self._load, group="athena-drafts-list")
        self.query_one("#athena-drafts-list", OptionList).focus()

    def _on_runtime_changed(self, _name: str) -> None:
        if self.is_mounted and self.is_attached and self.is_running:
            self.call_after_refresh(self._refresh_drafts)

    def on_unmount(self) -> None:
        if self._subscription is not None:
            self._subscription.dispose()

    async def _load(self) -> None:
        try:
            await self._drafts.refresh()
        finally:
            self._loading = False
            if self.is_mounted:
                self._refresh_drafts()

    def _selected_id(self) -> str | None:
        index = self.query_one("#athena-drafts-list", OptionList).highlighted
        return self._ids[index] if index is not None and 0 <= index < len(self._ids) else None

    def _refresh_drafts(self) -> None:
        if not self.is_mounted or not self.is_attached or not self.is_running:
            return
        try:
            selected = self._selected_id()
            listing = self.query_one("#athena-drafts-list", OptionList)
            warning = self.query_one("#athena-drafts-warning", Static)
            self.query_one("#athena-drafts-detail", Static)
            buttons = {
                identity: self.query_one(f"#athena-drafts-{identity}", Button)
                for identity in ("restore", "delete", "clear", "keep")
            }
        except NoMatches:
            # Children can be removed before the screen lifecycle flags change.
            return
        listing.clear_options()
        self._ids = [row.id for row in self._drafts.items]
        for item in self._drafts.items:
            listing.add_option(Option(Text(" · ".join(item.context)), id=item.id))
        if self._ids:
            listing.highlighted = self._ids.index(selected) if selected in self._ids else 0
        row = next((row for row in self._drafts.items if row.id == self._selected_id()), None)
        warning.display = self._drafts.skipped > 0
        warning.update(
            f"{self._drafts.skipped} local draft record(s) could not be read. "
            "Valid drafts remain available. Clear all can remove unreadable records."
            if self._drafts.skipped
            else ""
        )
        self._refresh_drafts_detail()
        busy = self._loading or self._pending or self._drafts.busy or not self._drafts.enabled
        for identity in ("restore", "delete"):
            buttons[identity].disabled = busy or row is None
        buttons["clear"].disabled = busy or not (self._ids or self._drafts.skipped)
        buttons["keep"].disabled = self._pending

    def _refresh_drafts_detail(self) -> None:
        try:
            selected = self._selected_id()
            detail = self.query_one("#athena-drafts-detail", Static)
        except NoMatches:
            return
        row = next((row for row in self._drafts.items if row.id == selected), None)
        details = "No local drafts"
        if row is not None:
            labels = ("Connection", "Region", "Workgroup", "Catalog", "Database")
            details = "\n".join(
                f"{label}: {value}" for label, value in zip(labels, row.context, strict=True)
            )
            details += "\nSaved: " + row.updated_at.isoformat()
        error = self._page.draft_recovery_error or self._drafts.error_text
        if error:
            details += "\n" + error
        detail.update(details)

    def on_option_list_option_highlighted(self, event: OptionList.OptionHighlighted) -> None:
        if event.option_list.id == "athena-drafts-list":
            self._refresh_drafts_detail()

    def action_focus_next(self) -> None:
        self.focus_next()

    def action_focus_previous(self) -> None:
        self.focus_previous()

    def _move(self, delta: int) -> None:
        if isinstance(self.focused, VerticalScroll):
            if delta < 0:
                self.focused.action_scroll_up()
            else:
                self.focused.action_scroll_down()
            return
        listing = self.query_one("#athena-drafts-list", OptionList)
        if self._ids:
            listing.highlighted = min(
                len(self._ids) - 1, max(0, (listing.highlighted or 0) + delta)
            )

    def action_move_up(self) -> None:
        self._move(-1)

    def action_move_down(self) -> None:
        self._move(1)

    def action_commit_focused(self) -> None:
        if isinstance(self.focused, Button) and not self.focused.disabled:
            self.focused.press()
        # Enter in the list only selects/highlights; it never restores SQL.

    def action_close(self) -> None:
        self.dismiss("closed")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        identity = event.button.id
        if identity == "athena-drafts-close":
            self.action_close()
            return
        if (
            self._loading
            or self._pending
            or self._drafts.busy
            or event.button.disabled
            or identity is None
        ):
            return
        self._pending = True
        selected = self._selected_id()
        self._refresh_drafts()
        self._run_lifecycle_worker(
            lambda: self._perform(identity, selected),
            group="athena-drafts-action",
        )

    def _restore_action_focus(self, identity: str) -> None:
        if not self.is_mounted or not self.is_attached or self.screen is not self.app.screen:
            return
        try:
            button = self.query_one(f"#{identity}", Button)
            listing = self.query_one("#athena-drafts-list", OptionList)
        except NoMatches:
            return
        if button.display and not button.disabled:
            button.focus()
        else:
            listing.focus()

    async def _confirm(self, title: str, *, danger: bool = False) -> bool:
        return await ask_draft_confirmation(
            self,
            drafts=self._drafts,
            hub=self._hub,
            request=ConfirmRequest(
                title=title, danger=danger, confirm_label="Confirm" if danger else "Replace"
            ),
        )

    async def _perform(self, identity: str, selected: str | None) -> None:
        try:
            if identity == "athena-drafts-restore" and selected is not None:
                restored = await self._page.restore_draft(
                    selected,
                    lambda: self._confirm("Replace unsaved SQL?"),
                )
                if restored and self.is_mounted:
                    self.dismiss("restored")
            elif identity == "athena-drafts-delete" and selected is not None:
                if await self._confirm("Delete this local draft?", danger=True):
                    await self._drafts.delete(selected)
            elif identity == "athena-drafts-clear":
                if await self._confirm("Delete all local drafts?", danger=True):
                    await self._drafts.clear()
            elif identity == "athena-drafts-keep":
                self._page.keep_current_editor()
        finally:
            self._pending = False
            if self.is_mounted:
                self._refresh_drafts()
                if self.is_attached and self.is_running:
                    self.call_after_refresh(self._restore_action_focus, identity)
