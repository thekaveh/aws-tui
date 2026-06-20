"""Smoke test for ServicesMenuFooter."""

from __future__ import annotations

import pytest
from textual.app import App, ComposeResult

from aws_tui.ui.widgets.services_menu_footer import ServicesMenuFooter


def test_services_menu_footer_construction() -> None:
    footer = ServicesMenuFooter()
    assert footer is not None


@pytest.mark.asyncio
async def test_gear_button_press_invokes_open_settings_exactly_once() -> None:
    """Regression: previously both @on(Button.Pressed) AND on_click were
    handled, causing action_open_settings to fire twice per click."""
    invocations: list[int] = []

    class _HostApp(App[None]):
        def compose(self) -> ComposeResult:
            yield ServicesMenuFooter()

        def action_open_settings(self) -> None:
            invocations.append(1)

    app = _HostApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.click("#gear-button")
        await pilot.pause()

    assert sum(invocations) == 1, f"expected exactly 1 invocation, got {sum(invocations)}"
