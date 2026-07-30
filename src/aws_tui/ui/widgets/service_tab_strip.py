"""A single-focus-target tab strip for service-local views."""

from __future__ import annotations

from typing import ClassVar

from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.events import Click
from textual.message import Message
from textual.widget import Widget
from textual.widgets import Static


class _ServiceTab(Static):
    """Non-focusable visual child owned by :class:`ServiceTabStrip`."""

    def __init__(self, value: str, label: str) -> None:
        super().__init__(label, id=f"service-tab-{value}", classes="service-tab", markup=False)
        self.value = value


class ServiceTabStrip(Widget, can_focus=True):
    """A service's views represented by one predictable focus stop."""

    DEFAULT_CSS: ClassVar[str] = """
    ServiceTabStrip {
        width: 1fr;
        height: 3;
        min-height: 3;
        layout: horizontal;
        border: solid $accent;
    }
    ServiceTabStrip:focus {
        border: heavy $accent;
    }
    ServiceTabStrip > .service-tab {
        width: 1fr;
        height: 1;
        content-align: center middle;
    }
    ServiceTabStrip > .service-tab.-active {
        text-style: bold;
    }
    ServiceTabStrip:focus > .service-tab.-highlighted {
        background: $accent;
    }
    """

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("left", "cursor_left", "Previous tab", show=False),
        Binding("right", "cursor_right", "Next tab", show=False),
        Binding("enter,space", "select", "Select tab", show=False),
    ]

    class Changed(Message):
        """Posted when keyboard or pointer interaction chooses a tab."""

        def __init__(self, value: str) -> None:
            super().__init__()
            self.value = value

    def __init__(
        self,
        tabs: tuple[tuple[str, str], ...],
        *,
        active: str,
        id: str | None = None,
    ) -> None:
        if not tabs:
            raise ValueError("ServiceTabStrip requires at least one tab")
        values = tuple(value for value, _label in tabs)
        if len(set(values)) != len(values):
            raise ValueError("ServiceTabStrip tab values must be unique")
        if active not in values:
            raise ValueError("ServiceTabStrip active value must exist in tabs")
        super().__init__(id=id)
        self.border_title = "Views"
        self._tabs = tabs
        self._active = active
        self._highlighted = active

    @property
    def active(self) -> str:
        """The currently selected tab value."""

        return self._active

    def compose(self) -> ComposeResult:
        for value, label in self._tabs:
            yield _ServiceTab(value, label)

    def on_mount(self) -> None:
        self._sync_tabs()

    def set_active(self, value: str) -> None:
        """Synchronize to an externally selected tab without emitting a message."""

        self._require_value(value)
        self._active = value
        self._highlighted = value
        self._sync_tabs()

    def action_cursor_left(self) -> None:
        self._move(-1)

    def action_cursor_right(self) -> None:
        self._move(1)

    def action_select(self) -> None:
        self.post_message(self.Changed(self._highlighted))

    def on_click(self, event: Click) -> None:
        if isinstance(event.widget, _ServiceTab):
            self._highlighted = event.widget.value
            self._commit_highlighted()

    def _move(self, delta: int) -> None:
        current = self._values.index(self._highlighted)
        self._highlighted = self._values[(current + delta) % len(self._values)]
        self._commit_highlighted()

    @property
    def _values(self) -> tuple[str, ...]:
        return tuple(value for value, _label in self._tabs)

    def _commit_highlighted(self) -> None:
        self._active = self._highlighted
        self._sync_tabs()
        self.post_message(self.Changed(self._active))

    def _require_value(self, value: str) -> None:
        if value not in self._values:
            raise ValueError(f"Unknown service tab: {value}")

    def _sync_tabs(self) -> None:
        for tab in self.query(_ServiceTab):
            tab.set_class(tab.value == self._active, "-active")
            tab.set_class(tab.value == self._highlighted, "-highlighted")


__all__ = ["ServiceTabStrip"]
