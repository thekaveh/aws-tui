"""Behavior tests for the shared inline context picker."""

from __future__ import annotations

import pytest
from textual.app import App, ComposeResult
from textual.containers import Horizontal
from textual.widgets import OptionList, Static

from aws_tui.ui.widgets.context_picker import ContextOption, ContextPicker

_OPTIONS = (
    ContextOption("primary", "primary"),
    ContextOption("analytics", "analytics"),
)


class PickerHost(App[None]):
    def __init__(self, picker: ContextPicker) -> None:
        super().__init__()
        self.picker = picker
        self.changes: list[str] = []

    def compose(self) -> ComposeResult:
        yield self.picker

    def on_context_picker_changed(self, event: ContextPicker.Changed) -> None:
        self.changes.append(event.value)


def _picker(*, selected: str | None = "primary") -> ContextPicker:
    return ContextPicker("Workgroup", _OPTIONS, selected=selected, id="workgroup-picker")


@pytest.mark.asyncio
async def test_context_picker_renders_its_label_and_selected_value() -> None:
    picker = _picker()

    async with PickerHost(picker).run_test() as pilot:
        await pilot.pause()

        assert picker.value == "primary"
        assert picker.border_title == "Workgroup"
        assert str(picker.query_one(".context-picker-value", Static).render()) == "primary"
        assert str(picker.query_one(".context-picker-indicator", Static).render()) == "▾"


@pytest.mark.asyncio
async def test_context_picker_indicator_tracks_open_state() -> None:
    picker = _picker()

    async with PickerHost(picker).run_test() as pilot:
        await pilot.pause()
        indicator = picker.query_one(".context-picker-indicator", Static)

        assert str(indicator.render()) == "▾"

        picker.open()
        await pilot.pause()

        assert str(indicator.render()) == "▴"


@pytest.mark.asyncio
async def test_context_picker_long_value_keeps_indicator_visible_when_narrow() -> None:
    label = "[bold]production-analytics-workgroup-with-a-long-name[/bold]"
    picker = ContextPicker(
        "Workgroup",
        (ContextOption(label, "production"), ContextOption("secondary", "secondary")),
        selected="production",
        id="long-workgroup-picker",
    )

    async with PickerHost(picker).run_test(size=(18, 10)) as pilot:
        await pilot.pause()
        trigger = picker.query_one(".context-picker-trigger", Horizontal)
        value = picker.query_one(".context-picker-value", Static)
        indicator = picker.query_one(".context-picker-indicator", Static)

        assert picker.outer_size.height == 3
        assert value.size.width < len(label)
        assert value.styles.text_overflow == "ellipsis"
        assert str(value.render()) == label
        assert indicator.size.width == 1
        assert indicator.region.right == trigger.content_region.right
        assert str(indicator.render()) == "▾"

        picker.open()
        await pilot.pause()

        assert picker.is_open
        assert indicator.size.width == 1
        assert indicator.region.right == trigger.content_region.right
        assert str(indicator.render()) == "▴"


@pytest.mark.asyncio
async def test_context_picker_whole_trigger_toggles_from_value_and_indicator() -> None:
    picker = _picker()

    async with PickerHost(picker).run_test() as pilot:
        await pilot.click(".context-picker-value")
        await pilot.pause()
        assert picker.is_open

        await pilot.click(".context-picker-indicator")
        await pilot.pause()
        assert not picker.is_open


@pytest.mark.asyncio
async def test_context_picker_commits_keyboard_selection() -> None:
    picker = _picker()

    async with PickerHost(picker).run_test() as pilot:
        picker.focus()
        await pilot.press("enter", "down", "enter")

        assert picker.value == "analytics"
        assert picker.is_open is False
        assert pilot.app.changes == ["analytics"]


@pytest.mark.asyncio
async def test_context_picker_commits_upward_keyboard_selection() -> None:
    picker = _picker(selected="analytics")

    async with PickerHost(picker).run_test() as pilot:
        picker.focus()
        await pilot.press("enter", "up", "enter")

        assert picker.value == "primary"
        assert picker.is_open is False
        assert pilot.app.changes == ["primary"]


@pytest.mark.asyncio
async def test_context_picker_escape_restores_the_selected_option() -> None:
    picker = _picker()

    async with PickerHost(picker).run_test() as pilot:
        picker.focus()
        await pilot.press("enter", "down", "escape")
        await pilot.press("enter", "enter")

        assert picker.value == "primary"
        assert picker.is_open is False
        assert pilot.app.changes == ["primary"]


@pytest.mark.asyncio
async def test_context_picker_click_commits_an_inline_option() -> None:
    picker = _picker()

    async with PickerHost(picker).run_test() as pilot:
        picker.open()
        await pilot.pause()
        await pilot.click("#workgroup-picker-options", offset=(2, 2))

        assert picker.value == "analytics"
        assert picker.is_open is False
        assert pilot.app.changes == ["analytics"]


@pytest.mark.asyncio
async def test_context_picker_exposes_empty_and_transient_states() -> None:
    picker = _picker(selected=None)

    async with PickerHost(picker).run_test() as pilot:
        await pilot.pause()
        picker.set_options((), selected=None)
        picker.set_state(loading=True, tooltip="Loading workgroups")
        await pilot.pause()

        options = picker.query_one(OptionList)
        value = picker.query_one(".context-picker-value", Static)
        assert picker.has_class("-loading")
        assert picker.tooltip == "Loading workgroups"
        assert str(value.render()) == "Loading..."
        assert options.option_count == 1
        assert options.get_option_at_index(0).disabled is True

        picker.set_state(warning=True)
        assert picker.has_class("-warning")
        assert not picker.has_class("-loading")
        assert str(value.render()) == "(No options)"

        picker.set_state(error=True)
        assert picker.has_class("-error")
        assert not picker.has_class("-warning")
        assert str(value.render()) == "Unable to load options"


@pytest.mark.asyncio
async def test_context_picker_disables_opening_when_disabled() -> None:
    picker = _picker()

    async with PickerHost(picker).run_test() as pilot:
        picker.set_state(disabled=True)
        picker.focus()
        await pilot.press("enter")

        assert picker.disabled is True
        assert picker.is_open is False


@pytest.mark.asyncio
async def test_context_picker_closed_height_stays_stable_when_options_change() -> None:
    picker = _picker()

    async with PickerHost(picker).run_test() as pilot:
        await pilot.pause()
        closed_height = picker.size.height
        picker.set_options((*_OPTIONS, ContextOption("ad-hoc", "ad-hoc")), selected="primary")
        await pilot.pause()

        assert picker.is_open is False
        assert picker.size.height == closed_height
