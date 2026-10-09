"""Unit tests for the shared service source header."""

from __future__ import annotations

import pytest
from textual.app import App, ComposeResult
from textual.content import Content
from textual.widgets import Static, Tooltip

from aws_tui.ui.widgets.context_picker import ContextPicker
from aws_tui.ui.widgets.service_source_header import ServiceSourceHeader
from aws_tui.vm.service_source_vm import ServiceSourceContext
from tests.helpers import focus_and_settle, wait_until

_DEV = ServiceSourceContext("analytics-dev", "dev-sso", "us-east-1")
_PROD = ServiceSourceContext("analytics-prod", "prod-sso", "us-west-2")


@pytest.mark.asyncio
async def test_source_header_rendered_tooltip_preserves_literal_connection_name() -> None:
    source = ServiceSourceContext("[bold]analytics", "dev-sso", "us-east-1")
    header = ServiceSourceHeader(source, selectable=False)
    app = _SourceHost(header)
    app.TOOLTIP_DELAY = 0.01
    async with app.run_test(tooltips=True) as pilot:
        await pilot.hover(".service-source-value")
        tooltip = app.screen.query_one(Tooltip)
        await wait_until(lambda: tooltip.display, what="source tooltip displayed")
        rendered = tooltip.render()
        assert isinstance(rendered, Content)
        assert rendered.plain == source.label


class _SourceHost(App[None]):
    def __init__(self, header: ServiceSourceHeader) -> None:
        super().__init__()
        self.header = header
        self.selections: list[tuple[str, str]] = []

    def compose(self) -> ComposeResult:
        yield self.header

    def on_service_source_header_source_selected(
        self,
        event: ServiceSourceHeader.SourceSelected,
    ) -> None:
        self.selections.append((event.connection_name, event.region))


@pytest.mark.asyncio
async def test_source_header_renders_active_connection_profile_and_region() -> None:
    header = ServiceSourceHeader(_PROD, candidates=(_DEV, _PROD))

    async with _SourceHost(header).run_test() as pilot:
        await pilot.pause()

        picker = header.query_one(ContextPicker)
        assert picker.border_title == "AWS source"
        assert str(
            picker.query_one(".context-picker-value", Static).render()
        ) == _PROD.label.replace(" · ", "·")
        assert str(picker.query_one(".context-picker-indicator", Static).render()) == "▾"


@pytest.mark.asyncio
async def test_source_header_emits_selected_connection_identity() -> None:
    header = ServiceSourceHeader(_DEV, candidates=(_DEV, _PROD))

    async with _SourceHost(header).run_test() as pilot:
        picker = header.query_one(ContextPicker)
        await focus_and_settle(picker)
        await pilot.press("enter", "down", "enter")

        assert pilot.app.selections == [("analytics-prod", "us-west-2")]


@pytest.mark.asyncio
async def test_source_header_compact_mode_preserves_passive_one_row_identity() -> None:
    header = ServiceSourceHeader(_DEV, selectable=False)

    async with _SourceHost(header).run_test():
        await wait_until(
            lambda: header.region.height == 1,
            what="compact source header laid out in one row",
        )

        assert not header.can_focus
        assert not header.query(ContextPicker)
        assert str(header.query_one(".service-source-value", Static).render()) == _DEV.label
        assert header.region.height == 1
