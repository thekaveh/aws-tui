"""Focusable AWS source selector for single-context service pages."""

from __future__ import annotations

from typing import ClassVar

from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.message import Message
from textual.widget import Widget
from textual.widgets import Static

from aws_tui.ui.widgets.context_picker import ContextOption, ContextPicker
from aws_tui.vm.service_source_vm import ServiceSourceContext


class ServiceSourceHeader(Widget, can_focus=True):
    """Project supported AWS connections through the shared context picker."""

    DEFAULT_CSS: ClassVar[str] = """
    ServiceSourceHeader {
        width: 1fr;
        height: auto;
        min-height: 3;
    }
    ServiceSourceHeader > ContextPicker {
        width: 1fr;
        height: auto;
        min-height: 3;
    }
    ServiceSourceHeader:focus > ContextPicker {
        border: heavy $accent;
    }
    ServiceSourceHeader.-compact {
        height: 1;
        min-height: 1;
    }
    ServiceSourceHeader.-compact > .service-source-value {
        width: 1fr;
        height: 1;
        padding: 0 1;
        content-align: left middle;
    }
    """

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("enter,space", "open_picker", "Open source selector", show=False),
    ]

    class SourceSelected(Message):
        """Posted with the stable identity of the committed AWS source."""

        def __init__(
            self,
            header: ServiceSourceHeader,
            connection_name: str,
            region: str,
        ) -> None:
            super().__init__()
            self.header = header
            self.connection_name = connection_name
            self.region = region

    def __init__(
        self,
        source: ServiceSourceContext,
        *,
        candidates: tuple[ServiceSourceContext, ...] = (),
        selectable: bool = True,
        id: str | None = None,
    ) -> None:
        super().__init__(id=id, classes=None if selectable else "-compact")
        self.can_focus = selectable
        self._selectable = selectable
        ordered = dict.fromkeys((*candidates, source))
        self._source = source
        self.tooltip = source.label
        self._candidates = tuple(ordered)
        self._candidate_values = {
            str(index): candidate for index, candidate in enumerate(self._candidates)
        }

    @property
    def picker(self) -> ContextPicker:
        return self.query_one(ContextPicker)

    def _picker_options(self) -> tuple[ContextOption, ...]:
        return tuple(
            ContextOption(candidate.label.replace(" · ", "·"), value)
            for value, candidate in self._candidate_values.items()
        )

    def _active_value(self) -> str | None:
        return next(
            (
                value
                for value, candidate in self._candidate_values.items()
                if candidate.connection_key == self._source.connection_key
            ),
            None,
        )

    def compose(self) -> ComposeResult:
        if not self._selectable:
            yield Static(
                self._source.label,
                id=f"{self.id}-value" if self.id is not None else None,
                classes="service-source-value",
                markup=False,
            )
            return
        yield ContextPicker(
            "AWS source",
            self._picker_options(),
            selected=self._active_value(),
            id=f"{self.id}-picker" if self.id is not None else None,
        )

    def action_open_picker(self) -> None:
        self.open()

    def open(self) -> None:
        if not self._selectable:
            return
        self.picker.open()

    def restore_source(self) -> None:
        """Restore the picker to the source that still owns this page."""

        self.picker.set_options(
            self._picker_options(),
            selected=self._active_value(),
        )

    def on_context_picker_changed(self, event: ContextPicker.Changed) -> None:
        event.stop()
        candidate = self._candidate_values.get(event.value)
        if candidate is None:
            return
        self.post_message(
            self.SourceSelected(
                self,
                candidate.connection_name,
                candidate.region,
            )
        )


__all__ = ["ServiceSourceHeader"]
