"""Modal controls operating exclusively on a captured loaded pane listing."""

from __future__ import annotations

from typing import ClassVar

from reactivex.abc import DisposableBase
from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Horizontal, Vertical
from textual.events import Click
from textual.screen import ModalScreen
from textual.widgets import Input, OptionList, Static
from textual.widgets.option_list import Option

from aws_tui.ui.widgets.modal_button import ModalButton
from aws_tui.vm.file_manager.pane_vm import PaneSortField, PaneState, PaneVM


class ListingModal(ModalScreen[None]):
    """Capture listing lifetime; dismiss stale forms and release observers."""

    DEFAULT_CSS = """
    ListingModal { align: center middle; }
    ListingModal > Vertical { width: 64; max-width: 95%; height: auto; max-height: 90%; padding: 1 2; border: solid $accent; background: $surface; }
    ListingModal Input { margin: 1 0; }
    ListingModal OptionList { height: 10; }
    ListingModal .modal-footer { height: 3; align: center middle; }
    ListingModal .listing-title { text-style: bold; height: auto; }
    ListingModal .listing-status { height: auto; }
    """
    BINDINGS: ClassVar[list[BindingType]] = [Binding("escape", "cancel", "Close")]

    def __init__(self, pane: PaneVM) -> None:
        super().__init__()
        self.pane = pane
        self.revision = pane.listing_revision
        self._subscription: DisposableBase | None = None
        self._dismiss_requested = False

    def valid(self) -> bool:
        return self.revision == self.pane.listing_revision and self.pane.state in {
            PaneState.IDLE,
            PaneState.EMPTY,
        }

    def on_mount(self) -> None:
        self._subscription = self.pane.on_property_changed.subscribe(self._changed)
        editors = self.query(Input)
        if editors:
            editors.first().focus()
        else:
            self.query_one(OptionList).focus()

    def _changed(self, _: str) -> None:
        if not self.valid():
            self.call_after_refresh(self.action_cancel)

    def on_unmount(self) -> None:
        if self._subscription is not None:
            self._subscription.dispose()
            self._subscription = None

    def action_cancel(self) -> None:
        if not self._dismiss_requested and self in self.app.screen_stack:
            self._dismiss_requested = True
            self.dismiss()

    def action_focus_next(self) -> None:
        self.focus_next()

    def action_focus_prev(self) -> None:
        self.focus_previous()

    def on_click(self, event: Click) -> None:
        if isinstance(event.widget, ModalButton):
            event.stop()
            if event.widget.button_id == "cancel":
                self.action_cancel()
            elif event.widget.button_id == "clear":
                self.clear_filter()
            else:
                self.action_apply()

    def clear_filter(self) -> None:
        pass

    def action_apply(self) -> None:
        self.action_cancel()


class FilterPaneModal(ListingModal):
    def compose(self) -> ComposeResult:
        with Vertical():
            yield Static("Filter loaded entries", classes="listing-title")
            yield Input(self.pane.filter_text, id="listing-input")
            yield Static(
                self.pane.viewmodel.filter_status_text, classes="listing-status", markup=False
            )
            with Horizontal(classes="modal-footer"):
                yield ModalButton("Clear", button_id="clear")
                yield ModalButton("Done", button_id="done", classes="-primary")

    def on_input_changed(self, event: Input.Changed) -> None:
        if self.valid():
            self.pane.set_filter_command.execute(event.value)
            self.query_one(".listing-status", Static).update(self.pane.viewmodel.filter_status_text)

    def clear_filter(self) -> None:
        if self.valid():
            editor = self.query_one(Input)
            editor.value = ""
            editor.focus()
            self.pane.set_filter_command.execute("")


class FindPaneModal(ListingModal):
    def __init__(self, pane: PaneVM) -> None:
        super().__init__(pane)
        self.results = pane.find_entries("")

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Static("Find loaded entry", classes="listing-title")
            yield Input(id="listing-input")
            yield Static(
                "Choosing a hidden result clears the filter and reactivates retained marks.",
                classes="listing-status",
                markup=False,
            )
            yield OptionList(
                *(Option(Text(entry.name), id=str(i)) for i, entry in enumerate(self.results)),
                id="find-results",
            )
            yield Static(
                "" if self.results else "No matches in loaded entries",
                id="find-empty",
                markup=False,
            )
            with Horizontal(classes="modal-footer"):
                yield ModalButton("Cancel", button_id="cancel")
                yield ModalButton("Select", button_id="select", classes="-primary")

    def on_input_changed(self, event: Input.Changed) -> None:
        if not self.valid():
            return
        self.results = self.pane.find_entries(event.value)
        choices = self.query_one(OptionList)
        choices.clear_options()
        choices.add_options(
            Option(Text(entry.name), id=str(i)) for i, entry in enumerate(self.results)
        )
        choices.highlighted = 0 if self.results else None
        self.query_one("#find-empty", Static).update(
            "" if self.results else "No matches in loaded entries"
        )

    def action_move_up(self) -> None:
        self.query_one(OptionList).action_cursor_up()

    def action_move_down(self) -> None:
        self.query_one(OptionList).action_cursor_down()

    def on_option_list_option_selected(self, _: OptionList.OptionSelected) -> None:
        self.action_apply()

    def action_apply(self) -> None:
        index = self.query_one(OptionList).highlighted
        if (
            self.valid()
            and index is not None
            and index < len(self.results)
            and self.pane.select_found_entry(self.results[index], revision=self.revision)
        ):
            self.action_cancel()


class SortPaneModal(ListingModal):
    CHOICES = tuple((field, descending) for field in PaneSortField for descending in (False, True))

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Static("Sort loaded entries", classes="listing-title")
            yield OptionList(
                *(
                    Option(
                        f"{field.value.title()} {'descending' if descending else 'ascending'}",
                        id=str(i),
                    )
                    for i, (field, descending) in enumerate(self.CHOICES)
                )
            )
            with Horizontal(classes="modal-footer"):
                yield ModalButton("Cancel", button_id="cancel")
                yield ModalButton("Apply", button_id="apply", classes="-primary")

    def action_move_up(self) -> None:
        self.query_one(OptionList).action_cursor_up()

    def action_move_down(self) -> None:
        self.query_one(OptionList).action_cursor_down()

    def on_option_list_option_selected(self, _: OptionList.OptionSelected) -> None:
        self.action_apply()

    def action_apply(self) -> None:
        index = self.query_one(OptionList).highlighted
        if self.valid() and index is not None:
            field, descending = self.CHOICES[index]
            self.pane.set_sort(field, descending=descending)
            self.dismiss()
