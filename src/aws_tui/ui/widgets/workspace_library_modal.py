"""Keyboard management of navigation records; persistence belongs to the VM."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from functools import partial
from typing import ClassVar

from reactivex.abc import DisposableBase
from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Grid, Vertical
from textual.events import Click
from textual.screen import ModalScreen
from textual.widgets import Input, OptionList, Static
from textual.widgets.option_list import Option
from vmx import Message, MessageHub

from aws_tui.domain.navigation_workspace import Bookmark, Workspace, WorkspaceError
from aws_tui.ui.widgets._worker import DeferredWorkerMixin
from aws_tui.ui.widgets.confirm_modal import TextualDialogService
from aws_tui.ui.widgets.modal_button import ModalButton
from aws_tui.vm.chrome.confirm_vm import ConfirmationVM, ConfirmRequest
from aws_tui.vm.workspace_library_vm import WorkspaceLibraryVM


@dataclass(frozen=True, slots=True)
class WorkspaceOpenRequest:
    workspace: Workspace
    bookmark: Bookmark | None = None


class WorkspaceLibraryModal(DeferredWorkerMixin, ModalScreen[WorkspaceOpenRequest | None]):
    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("escape", "close", show=False),
        Binding("enter", "commit_focused", show=False),
        Binding("up", "move_up", show=False),
        Binding("down", "move_down", show=False),
    ]
    DEFAULT_CSS: ClassVar[str] = """
    WorkspaceLibraryModal { align: center middle; background: $background 60%; }
    WorkspaceLibraryModal > Vertical { width: 76; max-width: 96%; height: 90%; border: round $accent; background: $surface; padding: 0 1; }
    #workspace-title { height: 1; text-style: bold; color: $accent; }
    #workspace-status { height: 2; }
    #workspace-list { height: 1fr; min-height: 3; }
    #workspace-name { height: 3; }
    #workspace-actions { grid-size: 4 2; height: 6; }
    #workspace-actions ModalButton { min-width: 0; width: 1fr; margin: 0; padding: 0 1; }
    """

    def __init__(
        self,
        vm: WorkspaceLibraryVM,
        *,
        capture: Callable[[str], Workspace],
        capture_bookmark: Callable[[str], Bookmark],
        confirmation: ConfirmationVM,
        hub: MessageHub[Message],
        closed: Callable[[], None],
    ) -> None:
        super().__init__()
        self.vm = vm
        self._capture = capture
        self._capture_bookmark = capture_bookmark
        self._confirmation = confirmation
        self._hub = hub
        self._on_closed = closed
        self._subscription: DisposableBase | None = None
        self._rows: list[WorkspaceOpenRequest] = []
        self._selection: tuple[str, str | None] | None = None
        self._pending = False
        self._loading = True
        self._feedback: str | None = None

    def compose(self) -> ComposeResult:
        with Vertical(classes="modal-frame"):
            yield Static("Workspaces and bookmarks", id="workspace-title", markup=False)
            yield Static("Loading workspace library…", id="workspace-status", markup=False)
            yield OptionList(id="workspace-list")
            name = Input(
                placeholder="Name for Save, Rename or Add bookmark",
                id="workspace-name",
                max_length=80,
            )
            yield name
            with Grid(id="workspace-actions", classes="modal-footer"):
                for identity, label in (
                    ("save", "Save"),
                    ("open", "Open"),
                    ("rename", "Rename"),
                    ("delete", "Delete"),
                    ("bookmark", "Bookmark"),
                    ("refresh", "Refresh"),
                    ("close", "Close"),
                ):
                    button = ModalButton(label, button_id=identity, disabled=identity != "close")
                    button.id = f"workspace-{identity}"
                    yield button

    def on_mount(self) -> None:
        self._subscription = self.vm.on_property_changed.subscribe(self._changed)
        self.query_one(Input).focus()
        self._run_lifecycle_worker(self._load, group="workspace-library-load")

    async def _load(self) -> None:
        try:
            await self.vm.refresh()
        finally:
            self._loading = False
            self._refresh()

    def _changed(self, _property: str) -> None:
        if self.is_mounted and self.is_attached and self.is_running:
            self.call_after_refresh(self._refresh)

    @staticmethod
    def _key(row: WorkspaceOpenRequest) -> tuple[str, str | None]:
        return row.workspace.name, row.bookmark.name if row.bookmark else None

    def _selected(self) -> WorkspaceOpenRequest | None:
        index = self.query_one(OptionList).highlighted
        return self._rows[index] if index is not None and 0 <= index < len(self._rows) else None

    def _refresh(self) -> None:
        if not self.is_mounted or not self.is_attached or not self.is_running:
            return
        listing = self.query_one(OptionList)
        self._rows = []
        options = []
        for record in self.vm.items:
            self._rows.append(WorkspaceOpenRequest(record))
            options.append(Option(Text(f"Workspace: {record.name} · {record.active_service}")))
            for bookmark in record.bookmarks:
                self._rows.append(WorkspaceOpenRequest(record, bookmark))
                options.append(
                    Option(Text(f"  Bookmark: {bookmark.name} · {bookmark.destination.service}"))
                )
        listing.clear_options()
        listing.add_options(options)
        index = next(
            (i for i, row in enumerate(self._rows) if self._key(row) == self._selection), 0
        )
        if self._rows:
            listing.highlighted = index
            self._selection = self._key(self._rows[index])
        else:
            self._selection = None
        busy = self._pending or self._loading or self.vm.busy
        selected = self._selected()
        for identity in ("save", "open", "rename", "delete", "bookmark", "refresh", "close"):
            button = self.query_one(f"#workspace-{identity}", ModalButton)
            disabled = busy or (
                identity in {"open", "rename", "delete", "bookmark"} and selected is None
            )
            if self.vm.read_only and identity in {"save", "rename", "delete", "bookmark"}:
                disabled = True
            button.disabled = disabled if identity != "close" else False
            button.can_focus = not button.disabled
        status = self._feedback or self.vm.error_text
        if status is None:
            status = (
                "Library is read-only in demo mode. " if self.vm.read_only else ""
            ) + "Tab to an action; Enter applies it; Esc closes."
        self.query_one("#workspace-status", Static).update(status)

    def on_option_list_option_highlighted(self, event: OptionList.OptionHighlighted) -> None:
        if event.option_list is self.query_one(OptionList):
            row = self._selected()
            self._selection = self._key(row) if row else None

    def action_move_up(self) -> None:
        self.query_one(OptionList).action_cursor_up()

    def action_move_down(self) -> None:
        self.query_one(OptionList).action_cursor_down()

    def action_commit_focused(self) -> None:
        focused = self.app.focused
        if isinstance(focused, ModalButton):
            focused.press()
        elif isinstance(focused, OptionList):
            self._start("open")
        else:
            self.focus_next()

    def on_click(self, event: Click) -> None:
        if isinstance(event.widget, ModalButton) and event.widget in self.query(
            "#workspace-actions ModalButton"
        ):
            event.stop()
            self._start(event.widget.button_id)

    def _start(self, action: str) -> None:
        if action == "close":
            self.action_close()
            return
        button = self.query_one(f"#workspace-{action}", ModalButton)
        if button.disabled or self._pending or self.app.screen is not self:
            return
        row = self._selected()
        if action == "open":
            if row is not None:
                self.dismiss(row)
            return
        self._pending = True
        self._feedback = None
        name = self.query_one(Input).value
        self._refresh()
        self._run_lifecycle_worker(
            partial(self._apply, action, name, row),
            group="workspace-library-write",
            exit_on_error=False,
        )

    async def _apply(self, action: str, name: str, row: WorkspaceOpenRequest | None) -> None:
        try:
            changed = False
            if action == "refresh":
                await self.vm.refresh()
            elif action == "save":
                changed = await self.vm.save(self._capture(name))
                if changed:
                    self._selection = (name, None)
            elif row is not None:
                record, bookmark = row.workspace, row.bookmark
                if action == "delete":
                    request = ConfirmRequest(
                        title="Delete bookmark?" if bookmark else "Delete workspace?",
                        body_lines=(bookmark.name if bookmark else record.name,),
                        confirm_label="Delete",
                        danger=True,
                    )
                    if not await self._confirmation.ask(
                        request,
                        dialog_service=TextualDialogService(
                            self.app, self._confirmation, hub=self._hub
                        ),
                    ):
                        return
                    if self.app.screen is not self or not self.is_attached:
                        return
                    changed = (
                        await self.vm.delete_bookmark(record, bookmark.name)
                        if bookmark
                        else await self.vm.delete(record)
                    )
                    if changed:
                        self._selection = (record.name, None) if bookmark else None
                elif action == "rename":
                    changed = (
                        await self.vm.rename_bookmark(record, bookmark.name, name)
                        if bookmark
                        else await self.vm.rename(record, name)
                    )
                    if changed:
                        self._selection = (record.name, name) if bookmark else (name, None)
                elif action == "bookmark":
                    saved = self._capture_bookmark(name)
                    changed = await self.vm.add_bookmark(record, saved)
                    if changed:
                        self._selection = (record.name, saved.name)
            if changed:
                self._feedback = "Saved."
        except WorkspaceError as error:
            self._feedback = str(error)
        except Exception:
            self._feedback = "Workspace operation could not be completed"
        finally:
            self._pending = False
            self._refresh()

    def action_close(self) -> None:
        self.vm.close()
        self.dismiss(None)

    def on_unmount(self) -> None:
        if self._subscription is not None:
            self._subscription.dispose()
        self.vm.close()
        self._on_closed()
