"""Shared bordered, inline-expanding picker for service context values."""

from __future__ import annotations

import contextlib
from dataclasses import dataclass
from typing import ClassVar

from rich.markup import escape as escape_markup
from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.events import Click
from textual.message import Message
from textual.widget import Widget
from textual.widgets import OptionList, Static
from textual.widgets.option_list import Option


@dataclass(frozen=True, slots=True)
class ContextOption:
    """A display label paired with the stable value used by a service VM."""

    label: str
    value: str


class ContextPicker(Widget, can_focus=True):
    """A titled selector which expands its option list in normal layout flow."""

    DEFAULT_CSS: ClassVar[str] = """
    ContextPicker {
        width: 1fr;
        height: 3;
        min-height: 3;
        layout: vertical;
        border: solid $accent;
    }
    ContextPicker:focus {
        border: heavy $accent;
    }
    ContextPicker.-disabled {
        opacity: 0.6;
    }
    ContextPicker.-warning {
        border: solid $warning;
    }
    ContextPicker.-error {
        border: solid $error;
    }
    ContextPicker > .context-picker-trigger {
        width: 1fr;
        height: 1;
        padding: 0 1;
        content-align: left middle;
    }
    ContextPicker > OptionList {
        width: 1fr;
        height: auto;
        max-height: 12;
        display: none;
    }
    ContextPicker.-open {
        height: auto;
    }
    ContextPicker.-open > OptionList {
        display: block;
    }
    """

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("enter,space", "toggle_picker", "Open selector", show=False),
        Binding("escape", "close", "Close selector", show=False),
    ]

    class Changed(Message):
        """Posted after the user commits an option."""

        def __init__(self, value: str) -> None:
            super().__init__()
            self.value = value

    def __init__(
        self,
        label: str,
        options: tuple[ContextOption, ...],
        *,
        selected: str | None,
        id: str | None = None,
        classes: str | None = None,
    ) -> None:
        super().__init__(id=id, classes=classes)
        self.border_title = label
        self._label = label
        self._options = options
        self._value = selected if any(option.value == selected for option in options) else None
        self._loading = False
        self._warning = False
        self._error = False

    @property
    def value(self) -> str | None:
        """The last committed service-context value."""

        return self._value

    @property
    def is_open(self) -> bool:
        """Whether the inline option list is currently visible."""

        return self.has_class("-open")

    @property
    def _options_id(self) -> str:
        return f"{self.id}-options" if self.id is not None else "context-picker-options"

    def compose(self) -> ComposeResult:
        yield Static(classes="context-picker-trigger", markup=False)
        yield OptionList(id=self._options_id)

    def on_mount(self) -> None:
        self._refresh()

    def set_options(
        self,
        options: tuple[ContextOption, ...],
        *,
        selected: str | None,
    ) -> None:
        """Replace choices and synchronize the committed service value."""

        self._options = options
        self._value = selected if any(option.value == selected for option in options) else None
        self._refresh()

    def set_state(
        self,
        *,
        loading: bool = False,
        disabled: bool = False,
        warning: bool = False,
        error: bool = False,
        tooltip: str | None = None,
    ) -> None:
        """Apply display state supplied by the owning service view."""

        self._loading = loading
        self._warning = warning and not error
        self._error = error
        self.disabled = disabled
        self.tooltip = tooltip
        self.set_class(loading, "-loading")
        self.set_class(disabled, "-disabled")
        self.set_class(self._warning, "-warning")
        self.set_class(error, "-error")
        if disabled or loading:
            self.close()
        self._refresh()

    def open(self) -> None:
        """Show and focus the normal-flow option list when selectable."""

        if self.disabled or self._loading:
            return
        self._refresh_options()
        self.add_class("-open")
        self.call_after_refresh(self._focus_options)

    def close(self, *, restore: bool = True) -> None:
        """Hide the option list and optionally restore its cursor to the value."""

        self.remove_class("-open")
        if restore:
            self._restore_highlight()
        if not self.disabled:
            self.call_after_refresh(self.focus)

    def action_toggle_picker(self) -> None:
        if self.is_open:
            self.close()
        else:
            self.open()

    def action_close(self) -> None:
        self.close()

    def on_click(self, event: Click) -> None:
        if isinstance(event.widget, OptionList):
            return
        self.action_toggle_picker()

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        if event.option.id is not None:
            self._commit(event.option.id)

    def _commit(self, value: str) -> None:
        if not any(option.value == value for option in self._options):
            return
        self._value = value
        self._refresh_trigger()
        self.close(restore=False)
        self.post_message(self.Changed(value))

    def _refresh(self) -> None:
        self._refresh_trigger()
        self._refresh_options()

    def _refresh_trigger(self) -> None:
        with contextlib.suppress(Exception):
            trigger = self.query_one(".context-picker-trigger", Static)
            trigger.update(self._trigger_text())

    def _refresh_options(self) -> None:
        with contextlib.suppress(Exception):
            option_list = self.query_one(OptionList)
            option_list.clear_options()
            for option in self._build_options():
                option_list.add_option(option)
            self._restore_highlight()

    def _restore_highlight(self) -> None:
        with contextlib.suppress(Exception):
            option_list = self.query_one(OptionList)
            option_list.highlighted = next(
                (
                    index
                    for index, option in enumerate(self._options)
                    if option.value == self._value
                ),
                None,
            )

    def _focus_options(self) -> None:
        with contextlib.suppress(Exception):
            self.query_one(OptionList).focus()

    def _trigger_text(self) -> str:
        if self._loading:
            return "Loading..."
        if self._error:
            return "Unable to load options"
        if not self._options:
            return "(No options)"
        selected = next((option for option in self._options if option.value == self._value), None)
        return selected.label if selected is not None else f"(Select {self._label.lower()})"

    def _build_options(self) -> tuple[Option, ...]:
        if self._loading:
            return (Option("Loading...", disabled=True),)
        if self._error:
            return (Option("Unable to load options", disabled=True),)
        if not self._options:
            return (Option("No options available", disabled=True),)
        return tuple(
            Option(escape_markup(option.label), id=option.value) for option in self._options
        )


__all__ = ["ContextOption", "ContextPicker"]
