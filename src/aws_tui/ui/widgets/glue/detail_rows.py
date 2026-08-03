from __future__ import annotations

from contextlib import suppress
from dataclasses import dataclass
from datetime import datetime
from typing import ClassVar

from rich.text import Text
from textual.app import ComposeResult
from textual.containers import VerticalScroll
from textual.css.query import NoMatches
from textual.widget import Widget
from textual.widgets import OptionList, Static
from textual.widgets.option_list import Option

from aws_tui.vm.file_manager.pane_vm import PaneState


@dataclass(frozen=True, slots=True)
class DetailValue:
    key: str
    value: str
    classes: str = ""


def display_value(value: object | None) -> str:
    if value is None or value == "":
        return "—"
    if isinstance(value, bool):
        return "yes" if value else "no"
    return str(value)


def display_time(value: datetime | None) -> str:
    return value.strftime("%Y-%m-%d %H:%M:%S") if value is not None else "—"


def state_placeholder(
    state: PaneState,
    *,
    error_text: str | None,
    empty_text: str,
) -> tuple[str, str] | None:
    if state is PaneState.LOADING:
        return "loading…", ""
    if state is PaneState.UNREACHABLE:
        return error_text or "endpoint unreachable — press r to retry", "-warning"
    if state is PaneState.AUTH_REQUIRED:
        return "authentication required — run aws sso login", "-warning"
    if state is PaneState.FORBIDDEN:
        return error_text or "permission denied — check IAM and Lake Formation", "-warning"
    if state is PaneState.ERROR:
        return error_text or "error — press r to retry", "-error"
    if state is PaneState.EMPTY:
        return empty_text, ""
    return None


class ResourceListPane(Widget):
    DEFAULT_CSS: ClassVar[str] = """
    ResourceListPane {
        height: 1fr;
        layout: vertical;
        border-title-align: left;
    }
    ResourceListPane > OptionList {
        height: 1fr;
        scrollbar-size: 1 1;
        text-wrap: nowrap;
        text-overflow: ellipsis;
    }
    ResourceListPane > .glue-list-footer {
        height: 1;
        padding: 0 1;
        text-align: right;
    }
    """

    def __init__(
        self,
        title: str,
        *,
        id: str,
        empty_text: str,
    ) -> None:
        super().__init__(id=id, classes="glue-pane glue-list-pane")
        self._title = title
        self._empty_text = empty_text
        self._footer_text = ""

    def compose(self) -> ComposeResult:
        yield OptionList(
            id=f"{self.id}-options",
            classes="glue-option-list",
            markup=False,
            compact=True,
        )
        yield Static(self._footer_text, classes="glue-list-footer", markup=False)

    def on_mount(self) -> None:
        self.border_title = self._title
        self._refresh_footer()

    def _refresh_footer(self) -> None:
        with suppress(NoMatches):
            self.query_one(".glue-list-footer", Static).update(self._footer_text)

    @property
    def option_list(self) -> OptionList:
        return self.query_one(OptionList)

    def replace(
        self,
        rows: tuple[tuple[str, str], ...],
        *,
        selected_id: str | None,
        state: PaneState,
        error_text: str | None,
        has_more: bool,
    ) -> None:
        options = self.option_list
        options.clear_options()
        placeholder = state_placeholder(
            state,
            error_text=error_text,
            empty_text=self._empty_text,
        )
        options.remove_class("-warning")
        options.remove_class("-error")
        if placeholder is not None and state is not PaneState.IDLE:
            text, classes = placeholder
            if classes:
                options.add_class(classes)
            options.add_option(
                Option(Text(text, no_wrap=False), id="__placeholder__", disabled=True)
            )
        else:
            for row_id, label in rows:
                options.add_option(
                    Option(
                        Text(label, no_wrap=True, overflow="ellipsis"),
                        id=row_id,
                    )
                )
        if selected_id is not None:
            for index in range(options.option_count):
                if options.get_option_at_index(index).id == selected_id:
                    options.highlighted = index
                    break
        count = len(rows)
        suffix = " · more available" if has_more else ""
        self._footer_text = f"{count} item{'s' if count != 1 else ''}{suffix}"
        self._refresh_footer()


class DetailRows(Widget):
    DEFAULT_CSS: ClassVar[str] = """
    DetailRows {
        height: 1fr;
        layout: vertical;
        border-title-align: left;
    }
    DetailRows > VerticalScroll {
        height: 1fr;
        scrollbar-size: 1 1;
    }
    DetailRows .glue-detail-row {
        height: auto;
        min-height: 1;
        padding: 0 1;
    }
    DetailRows .glue-detail-placeholder {
        height: auto;
        padding: 1 2;
    }
    """

    def __init__(self, title: str, *, id: str) -> None:
        super().__init__(id=id, classes="glue-pane glue-detail-pane")
        self._title = title

    def compose(self) -> ComposeResult:
        yield VerticalScroll(
            id=f"{self.id}-scroll",
            classes="glue-detail-scroll",
        )

    def on_mount(self) -> None:
        self.border_title = self._title

    def replace(
        self,
        rows: tuple[DetailValue, ...],
        *,
        state: PaneState,
        error_text: str | None,
        empty_text: str,
    ) -> None:
        body = self.query_one(VerticalScroll)
        body.remove_children()
        placeholder = state_placeholder(
            state,
            error_text=error_text,
            empty_text=empty_text,
        )
        if placeholder is not None and (state is not PaneState.IDLE or not rows):
            text, classes = placeholder
            body.mount(
                Static(
                    text,
                    classes=f"glue-detail-placeholder {classes}".strip(),
                    markup=False,
                )
            )
            return
        for row in rows:
            line = f"{row.key:<14}  {row.value}"
            body.mount(
                Static(
                    line,
                    classes=f"glue-detail-row {row.classes}".strip(),
                    markup=False,
                )
            )


__all__ = [
    "DetailRows",
    "DetailValue",
    "ResourceListPane",
    "display_time",
    "display_value",
    "state_placeholder",
]
