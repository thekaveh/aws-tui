"""Keyboard-only-capable manual overview; no operation or mutation controls."""

from __future__ import annotations

from functools import partial
from typing import ClassVar

from reactivex.abc import DisposableBase
from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Grid, Vertical, VerticalScroll
from textual.events import Click
from textual.screen import ModalScreen
from textual.widgets import OptionList, Static
from textual.widgets.option_list import Option

from aws_tui.domain.scoped_overview import OverviewEntry, WatchScope, overview_error
from aws_tui.ui.widgets._worker import DeferredWorkerMixin
from aws_tui.ui.widgets.modal_button import ModalButton
from aws_tui.vm.scoped_overview_vm import OverviewSection, ScopedOverviewVM


class ScopedOverviewModal(DeferredWorkerMixin, ModalScreen[OverviewEntry | None]):
    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("escape", "close", show=False),
        Binding("enter", "commit_focused", show=False),
    ]
    DEFAULT_CSS: ClassVar[str] = """
    ScopedOverviewModal { align: center middle; background: $background 60%; }
    ScopedOverviewModal > Vertical { width: 110; max-width: 96%; height: 90%; border: round $accent; background: $surface; padding: 0 1; }
    #overview-title { height: 1; text-style: bold; color: $accent; }
    #overview-identity { height: 2; }
    #overview-status { height: 2; }
    #overview-list { height: 1fr; min-height: 3; }
    #overview-detail-scroll { height: 4; }
    #overview-details { height: auto; }
    #overview-actions { grid-size: 3 2; height: 6; }
    #overview-actions ModalButton { min-width: 0; width: 1fr; margin: 0; padding: 0 1; }
    """

    def __init__(self, vm: ScopedOverviewVM, *, add_current: WatchScope | None) -> None:
        super().__init__()
        self.vm = vm
        self._add_current = add_current
        self._subscription: DisposableBase | None = None
        self._rows: list[tuple[WatchScope, OverviewEntry | None]] = []
        self._selection: tuple[WatchScope, str | None] | None = None
        self._feedback: str | None = None

    def compose(self) -> ComposeResult:
        with Vertical(classes="modal-frame"):
            yield Static("Scoped overview · read-only", id="overview-title", markup=False)
            source = self.vm.connection
            profile = (
                overview_error(source.profile) or "default AWS chain"
                if source.kind == "aws"
                else "not applicable"
            )
            yield Static(
                f"Configured identity: {overview_error(source.name)} · {overview_error(source.region)}\n"
                f"Profile: {profile}",
                id="overview-identity",
                markup=False,
            )
            yield Static(
                "No watched scopes. Manual refresh only.", id="overview-status", markup=False
            )
            yield OptionList(id="overview-list")
            with VerticalScroll(id="overview-detail-scroll"):
                yield Static("", id="overview-details", markup=False)
            with Grid(id="overview-actions", classes="modal-footer"):
                for key, label in (
                    ("add", "Add current scope"),
                    ("refresh", "Refresh all"),
                    ("retry", "Retry source"),
                    ("open", "Open detail"),
                    ("remove", "Remove scope"),
                    ("close", "Close"),
                ):
                    button = ModalButton(label, button_id=key)
                    button.id = f"overview-{key}"
                    yield button

    def on_mount(self) -> None:
        self._subscription = self.vm.on_property_changed.subscribe(self._changed)
        self.call_after_refresh(self._refresh_view)
        self.query_one(OptionList).focus()

    def _changed(self, _property: str) -> None:
        if self.is_mounted and self.is_attached:
            self.call_after_refresh(self._refresh_view)

    @staticmethod
    def _key(row: tuple[WatchScope, OverviewEntry | None]) -> tuple[WatchScope, str | None]:
        return row[0], row[1].identifier if row[1] is not None else None

    def _selected(self) -> tuple[WatchScope, OverviewEntry | None] | None:
        index = self.query_one(OptionList).highlighted
        return self._rows[index] if index is not None and 0 <= index < len(self._rows) else None

    def _section(self, scope: WatchScope) -> OverviewSection:
        return next(section for section in self.vm.sections if section.scope == scope)

    def _refresh_view(self) -> None:
        if not self.is_mounted or not self.is_attached:
            return
        self._rows = []
        options = []
        for section in self.vm.sections:
            scope = section.scope
            label = (
                "Session transfers · all connections"
                if scope.kind == "transfers"
                else f"{scope.kind} · {scope.resource}"
            )
            self._rows.append((scope, None))
            options.append(Option(Text(f"{label} — {section.state}")))
            for group in ("active", "failed", "other"):
                for entry in section.page.entries:
                    if entry.group == group:
                        self._rows.append((scope, entry))
                        options.append(
                            Option(Text(f"  {group.title()} · {entry.identifier} · {entry.status}"))
                        )
        listing = self.query_one(OptionList)
        listing.clear_options()
        listing.add_options(options)
        if self._rows:
            listing.highlighted = next(
                (
                    index
                    for index, row in enumerate(self._rows)
                    if self._key(row) == self._selection
                ),
                0,
            )
            self._selection = self._key(self._rows[listing.highlighted or 0])
        else:
            self._selection = None
        self.query_one("#overview-status", Static).update(
            "Source changed; close and reopen the overview."
            if self.vm.closed
            else self._feedback
            or ("No watched scopes. " if not self.vm.sections else "")
            + "Manual refresh only · Tab to actions; Enter opens a selected entry."
        )
        self._refresh_details()

    def _refresh_details(self) -> None:
        selected = self._selected()
        section = self._section(selected[0]) if selected else None
        entry = selected[1] if selected else None
        lines = []
        if section is not None:
            last = section.last_success.isoformat() if section.last_success else "never"
            lines.append(f"Last successful refresh: {last}" + (" · STALE" if section.stale else ""))
            lines.extend(value for value in (section.error_text, section.page.cap) if value)
        if entry is not None:
            if entry.last_status is not None:
                lines.append(
                    f"Last crawl status: {entry.last_status} (separate from current {entry.status})"
                )
            lines.extend(
                f"{label}: {value.isoformat() if value else 'unavailable'}"
                for label, value in entry.times
            )
            if not entry.times:
                lines.append("Timing: unavailable in the session projection")
            if entry.error_summary:
                lines.append(entry.error_summary)
        self.query_one("#overview-details", Static).update("\n".join(lines))
        for key, disabled in (
            ("add", self._add_current is None),
            ("refresh", not self.vm.sections or self.vm.busy),
            ("retry", section is None or section.state == "loading"),
            ("open", entry is None or section is None or section.state != "ready" or section.stale),
            ("remove", section is None),
            ("close", False),
        ):
            disabled = disabled or (self.vm.closed and key != "close")
            button = self.query_one(f"#overview-{key}", ModalButton)
            button.disabled, button.can_focus = disabled, not disabled

    def on_option_list_option_highlighted(self, event: OptionList.OptionHighlighted) -> None:
        if event.option_list is self.query_one(OptionList):
            row = self._selected()
            self._selection = self._key(row) if row else None
            self._refresh_details()

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        if event.option_list is self.query_one(OptionList):
            event.stop()
            row = self._selected()
            self._start("open" if row is not None and row[1] is not None else "retry")

    def on_click(self, event: Click) -> None:
        if isinstance(event.widget, ModalButton) and event.widget in self.query(
            "#overview-actions ModalButton"
        ):
            event.stop()
            self._start(event.widget.button_id)

    def action_commit_focused(self) -> None:
        if isinstance(self.app.focused, ModalButton):
            self.app.focused.press()
        elif isinstance(self.app.focused, OptionList):
            row = self._selected()
            self._start("open" if row is not None and row[1] is not None else "retry")

    def action_move_up(self) -> None:
        listing = self.query_one(OptionList)
        if self.app.focused is listing:
            listing.action_cursor_up()

    def action_move_down(self) -> None:
        listing = self.query_one(OptionList)
        if self.app.focused is listing:
            listing.action_cursor_down()

    def _start(self, key: str) -> None:
        if self.app.screen is not self or self.query_one(f"#overview-{key}", ModalButton).disabled:
            return
        selected = self._selected()
        self._feedback = None
        try:
            if key == "close":
                self.action_close()
            elif key == "add" and self._add_current is not None:
                self.vm.add(self._add_current)
                self._selection = (self._add_current, None)
                self._refresh_view()
            elif key == "remove" and selected is not None:
                self.vm.remove(selected[0])
            elif key == "open" and selected is not None:
                self.dismiss(selected[1])
            elif key in {"refresh", "retry"}:
                scope = selected[0] if key == "retry" and selected else None
                self._run_lifecycle_worker(
                    partial(self.vm.refresh, scope), group="overview-refresh", exit_on_error=False
                )
        except ValueError as error:
            self._feedback = str(error)
            self._refresh_view()

    def action_close(self) -> None:
        self.vm.cancel_refresh()
        self.dismiss(None)

    def on_unmount(self) -> None:
        if self._subscription is not None:
            self._subscription.dispose()
        self.vm.cancel_refresh()
