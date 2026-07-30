"""Behavior tests for the shared inline context picker."""

from __future__ import annotations

import pytest
from textual.app import App, ComposeResult
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
        assert str(picker.query_one(".context-picker-trigger", Static).render()) == "primary ▾"


@pytest.mark.asyncio
async def test_context_picker_indicator_tracks_open_state() -> None:
    picker = _picker()

    async with PickerHost(picker).run_test() as pilot:
        await pilot.pause()
        trigger = picker.query_one(".context-picker-trigger", Static)

        assert str(trigger.render()).endswith("▾")

        picker.open()
        await pilot.pause()

        assert str(trigger.render()).endswith("▴")


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
        assert picker.has_class("-loading")
        assert picker.tooltip == "Loading workgroups"
        assert options.option_count == 1
        assert options.get_option_at_index(0).disabled is True

        picker.set_state(warning=True)
        assert picker.has_class("-warning")
        assert not picker.has_class("-loading")

        picker.set_state(error=True)
        assert picker.has_class("-error")
        assert not picker.has_class("-warning")


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
