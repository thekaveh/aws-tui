"""Shared bordered picker for service context values."""

from __future__ import annotations

import contextlib
from collections.abc import Iterable
from dataclasses import dataclass
from typing import ClassVar

from rich.markup import escape as escape_markup
from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Horizontal
from textual.events import MouseDown
from textual.message import Message
from textual.widget import Widget
from textual.widgets import OptionList, Static
from textual.widgets.option_list import Option

from aws_tui.ui.widgets.overlay_option_list import OverlayOptionList


@dataclass(frozen=True, slots=True)
class ContextOption:
    """A display label paired with the stable value used by a service VM."""

    label: str
    value: str


class ContextPicker(Widget, can_focus=True):
    """A titled selector with a screen-overlaid option list."""

    DEFAULT_CSS: ClassVar[str] = """
    ContextPicker {
        width: 1fr;
        height: 3;
        min-height: 3;
        layout: vertical;
    }
    ContextPicker.-disabled {
        opacity: 0.6;
    }
    ContextPicker > .context-picker-trigger {
        width: 1fr;
        height: 1;
        layout: horizontal;
        padding: 0;
    }
    ContextPicker .context-picker-value {
        width: 1fr;
        min-width: 0;
        height: 1;
        content-align: left middle;
        text-overflow: ellipsis;
    }
    ContextPicker .context-picker-indicator {
        width: 1;
        min-width: 1;
        max-width: 1;
        height: 1;
        content-align: right middle;
    }
    ContextPicker > OverlayOptionList {
        width: 1fr;
        height: auto;
        max-height: 12;
        display: none;
        overlay: screen;
        constrain: none inside;
    }
    ContextPicker.-open > OverlayOptionList {
        display: block;
    }
    """

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("enter,space", "toggle_picker", "Open selector", show=False),
        Binding("escape", "close", "Close selector", show=False),
    ]

    class Changed(Message):
        """Posted after the user commits an option."""

        def __init__(self, picker: ContextPicker, value: str) -> None:
            super().__init__()
            self.picker = picker
            self.value = value

        @property
        def control(self) -> ContextPicker:
            return self.picker

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
        with Horizontal(classes="context-picker-trigger"):
            yield Static(classes="context-picker-value", markup=False)
            yield Static(classes="context-picker-indicator", markup=False)
        yield OverlayOptionList(id=self._options_id)

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
        self._refresh_trigger()
        self.call_after_refresh(self._focus_options)

    def close(self, *, restore: bool = True, refocus: bool = True) -> None:
        """Hide the option list and optionally restore its cursor to the value."""

        self.remove_class("-open")
        self._refresh_trigger()
        if restore:
            self._restore_highlight()
        if refocus and not self.disabled:
            self.call_after_refresh(self._refocus)

    def action_toggle_picker(self) -> None:
        if self.is_open:
            self.close()
        else:
            self.open()

    def action_close(self) -> None:
        self.close()

    def focus_on_click(self) -> bool:
        """Keep the overlay focused while its owner trigger is clicked."""

        return not self.is_open

    def on_mouse_down(self, event: MouseDown) -> None:
        if isinstance(event.widget, OptionList):
            return
        self.action_toggle_picker()

    def on_overlay_option_list_dismissed(self, event: OverlayOptionList.Dismissed) -> None:
        self.close(refocus=not event.lost_focus)

    @classmethod
    def close_open_for_outside_mouse_down(
        cls,
        pickers: Iterable[ContextPicker],
        target: Widget | None,
    ) -> None:
        """Close open pickers that don't own a mouse-down target."""

        for picker in pickers:
            if picker.is_open and (target is None or picker not in target.ancestors_with_self):
                picker.close(refocus=False)

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        if event.option.id is not None:
            self._commit(event.option.id)

    def _commit(self, value: str) -> None:
        if not any(option.value == value for option in self._options):
            return
        self._value = value
        self._refresh_trigger()
        self.close(restore=False)
        self.post_message(self.Changed(self, value))

    def _refresh(self) -> None:
        self._refresh_trigger()
        self._refresh_options()

    def _refresh_trigger(self) -> None:
        with contextlib.suppress(Exception):
            self.query_one(".context-picker-value", Static).update(self._trigger_value())
            self.query_one(".context-picker-indicator", Static).update("▴" if self.is_open else "▾")

    def _refresh_options(self) -> None:
        with contextlib.suppress(Exception):
            option_list = self.query_one(OverlayOptionList)
            option_list.clear_options()
            for option in self._build_options():
                option_list.add_option(option)
            self._restore_highlight()

    def _restore_highlight(self) -> None:
        with contextlib.suppress(Exception):
            option_list = self.query_one(OverlayOptionList)
            option_list.highlighted = next(
                (
                    index
                    for index, option in enumerate(self._options)
                    if option.value == self._value
                ),
                None,
            )

    def _focus_options(self) -> None:
        if not self.is_open or not self.is_mounted:
            return
        with contextlib.suppress(Exception):
            self.query_one(OverlayOptionList).focus()

    def _refocus(self) -> None:
        if self.is_mounted:
            self.focus()

    def _trigger_value(self) -> str:
        if self._loading:
            return "Loading..."
        elif self._error:
            return "Unable to load options"
        elif not self._options:
            return "(No options)"
        selected = next(
            (option for option in self._options if option.value == self._value),
            None,
        )
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
