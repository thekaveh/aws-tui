"""Unit tests for the shared service source header."""

from __future__ import annotations

import pytest
from textual.app import App, ComposeResult
from textual.widgets import Static

from aws_tui.ui.widgets.context_picker import ContextPicker
from aws_tui.ui.widgets.service_source_header import ServiceSourceHeader
from aws_tui.vm.service_source_vm import ServiceSourceContext

_DEV = ServiceSourceContext("analytics-dev", "dev-sso", "us-east-1")
_PROD = ServiceSourceContext("analytics-prod", "prod-sso", "us-west-2")


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
        assert str(picker.query_one(".context-picker-trigger", Static).render()) == _PROD.label


@pytest.mark.asyncio
async def test_source_header_emits_selected_connection_identity() -> None:
    header = ServiceSourceHeader(_DEV, candidates=(_DEV, _PROD))

    async with _SourceHost(header).run_test() as pilot:
        picker = header.query_one(ContextPicker)
        picker.focus()
        await pilot.press("enter", "down", "enter")

        assert pilot.app.selections == [("analytics-prod", "us-west-2")]
