"""Behavior tests for the shared inline context picker."""

from __future__ import annotations

import asyncio
from collections.abc import Callable

import pytest
from textual.app import App, ComposeResult
from textual.color import Color
from textual.containers import Horizontal
from textual.events import Click, MouseDown
from textual.widgets import OptionList, Static

from aws_tui.infra.theme_store import ThemeStore
from aws_tui.ui.widgets.context_picker import ContextOption, ContextPicker
from aws_tui.ui.widgets.overlay_option_list import OverlayOptionList, PickerOpenIntent

_OPTIONS = (
    ContextOption("primary", "primary"),
    ContextOption("analytics", "analytics"),
)


class PickerHost(App[None]):
    CSS = """
    #outside-picker {
        margin-top: 3;
    }
    """

    def __init__(self, picker: ContextPicker) -> None:
        super().__init__()
        self.picker = picker
        self.changes: list[str] = []

    def compose(self) -> ComposeResult:
        yield self.picker
        yield _FocusableStatic(id="after-picker")
        yield _OutsideClickTarget(id="outside-picker")

    def on_context_picker_changed(self, event: ContextPicker.Changed) -> None:
        self.changes.append(event.value)

    def on_mouse_down(self, event: MouseDown) -> None:
        ContextPicker.close_open_for_outside_mouse_down(
            self.screen.query(ContextPicker),
            event.widget,
        )


def _picker(*, selected: str | None = "primary") -> ContextPicker:
    return ContextPicker("Workgroup", _OPTIONS, selected=selected, id="workgroup-picker")


class _FocusableStatic(Static, can_focus=True):
    pass


class _OutsideClickTarget(Static):
    def __init__(self, *, id: str) -> None:
        super().__init__("outside", id=id)
        self.clicks = 0

    def on_click(self, _event: Click) -> None:
        self.clicks += 1


class _FocusCycleHost(App[None]):
    def __init__(self, picker: ContextPicker) -> None:
        super().__init__()
        self.picker = picker

    def compose(self) -> ComposeResult:
        yield self.picker


class _SharedPickerHost(App[None]):
    def __init__(self, first: ContextPicker, newest: ContextPicker) -> None:
        super().__init__()
        self.first = first
        self.newest = newest

    def compose(self) -> ComposeResult:
        yield self.first
        yield self.newest


class _ThemedPickerHost(PickerHost):
    CSS = ThemeStore().load_builtin("carbon")


def _track_picker_focus(
    picker: ContextPicker,
    monkeypatch: pytest.MonkeyPatch,
) -> asyncio.Event:
    focused = asyncio.Event()
    options = picker.query_one(OverlayOptionList)
    focus_options = picker._focus_options

    def track_focus(epoch: int) -> None:
        focus_options(epoch)
        if picker.is_open and picker.app.focused is options:
            focused.set()

    monkeypatch.setattr(picker, "_focus_options", track_focus)
    return focused


async def _wait_for_picker_focus(focused: asyncio.Event) -> None:
    await asyncio.wait_for(focused.wait(), timeout=2)


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
async def test_context_picker_has_no_default_border_without_a_theme() -> None:
    picker = _picker()

    async with PickerHost(picker).run_test() as pilot:
        await pilot.pause()

        assert picker.styles.border_top[0] in {"", "none"}


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
async def test_context_picker_overlay_never_reflows_its_host_or_sibling() -> None:
    picker = _picker()
    async with PickerHost(picker).run_test(size=(60, 16)) as pilot:
        await pilot.pause()
        sibling = pilot.app.query_one("#after-picker", Static)
        before = (picker.region, sibling.region)

        picker.open()
        await pilot.pause()

        options = picker.query_one(OverlayOptionList)
        assert options.display
        assert options.styles.overlay == "screen"
        assert options.region.width == picker.content_region.width
        assert (picker.region, sibling.region) == before

        picker.close()
        await pilot.pause()
        assert (picker.region, sibling.region) == before


@pytest.mark.asyncio
async def test_context_picker_loses_open_state_without_refocusing_on_blur(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    picker = _picker()
    async with PickerHost(picker).run_test() as pilot:
        focus_complete = _track_picker_focus(picker, monkeypatch)
        picker.open()
        await _wait_for_picker_focus(focus_complete)
        pilot.app.query_one("#after-picker", Static).focus()
        await pilot.pause()

        assert not picker.is_open
        assert pilot.app.focused.id == "after-picker"


@pytest.mark.asyncio
async def test_context_picker_closed_before_deferred_focus_cannot_reclaim_focus() -> None:
    picker = _picker()
    async with PickerHost(picker).run_test() as pilot:
        outside = pilot.app.query_one("#after-picker", Static)

        picker.open()
        picker.close(refocus=False)
        outside.focus()
        await pilot.pause()

        assert not picker.is_open
        assert pilot.app.focused is outside


@pytest.mark.asyncio
async def test_context_picker_deferred_focus_yields_to_newer_outside_focus(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    picker = _picker()
    async with PickerHost(picker).run_test() as pilot:
        await pilot.pause()
        outside = pilot.app.query_one("#after-picker", Static)
        picker.focus()
        assert pilot.app.focused is picker
        callbacks: list[Callable[[], None]] = []
        monkeypatch.setattr(picker, "call_after_refresh", callbacks.append)

        picker.open()
        assert len(callbacks) == 1
        outside.focus()
        callbacks[0]()
        await pilot.pause()

        assert not picker.is_open
        assert pilot.app.focused is outside


@pytest.mark.asyncio
async def test_context_picker_close_reopen_invalidates_stale_refocus(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    picker = _picker()
    async with PickerHost(picker).run_test() as pilot:
        callbacks: list[Callable[[], None]] = []
        monkeypatch.setattr(picker, "call_after_refresh", callbacks.append)

        picker.open()
        picker.close()
        picker.open()
        await pilot.pause()

        assert len(callbacks) == 3
        callbacks[2]()
        await pilot.pause()
        assert pilot.app.focused is picker.query_one(OverlayOptionList)

        callbacks[1]()
        callbacks[0]()
        await pilot.pause()
        assert picker.is_open
        assert pilot.app.focused is picker.query_one(OverlayOptionList)


@pytest.mark.asyncio
async def test_shared_open_intent_invalidates_older_picker_focus_callback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    intent = PickerOpenIntent()
    first = ContextPicker(
        "First",
        _OPTIONS,
        selected="primary",
        open_intent=intent,
        id="first-picker",
    )
    newest = ContextPicker(
        "Newest",
        _OPTIONS,
        selected="primary",
        open_intent=intent,
        id="newest-picker",
    )
    async with _SharedPickerHost(first, newest).run_test() as pilot:
        await pilot.pause()
        first_callbacks: list[Callable[[], None]] = []
        newest_callbacks: list[Callable[[], None]] = []
        monkeypatch.setattr(first, "call_after_refresh", first_callbacks.append)
        monkeypatch.setattr(newest, "call_after_refresh", newest_callbacks.append)

        first.open()
        newest.open()
        newest_callbacks[0]()
        first_callbacks[0]()
        await pilot.pause()

        assert newest.is_open
        assert pilot.app.focused is newest.query_one(OverlayOptionList)


@pytest.mark.asyncio
async def test_context_picker_live_refresh_error_propagates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    picker = _picker()
    async with PickerHost(picker).run_test() as pilot:
        await pilot.pause()
        value = picker.query_one(".context-picker-value", Static)

        def fail_update(_value: object) -> None:
            raise RuntimeError("live context trigger defect")

        monkeypatch.setattr(value, "update", fail_update)
        with pytest.raises(RuntimeError, match="live context trigger defect"):
            picker._refresh_trigger()  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_context_picker_refresh_is_safe_after_child_teardown() -> None:
    picker = _picker()
    async with PickerHost(picker).run_test() as pilot:
        await pilot.pause()
        value = picker.query_one(".context-picker-value", Static)
        await value.remove()

        picker._refresh_trigger()  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_context_picker_deduplicates_repeated_stable_values() -> None:
    picker = _picker()
    async with PickerHost(picker).run_test() as pilot:
        await pilot.pause()

        picker.set_options(
            (
                ContextOption("Primary", "primary"),
                ContextOption("Duplicate primary", "primary"),
                ContextOption("Analytics", "analytics"),
            ),
            selected="primary",
        )

        options = picker.query_one(OverlayOptionList).options
        assert [option.id for option in options] == ["primary", "analytics"]
        assert str(options[0].prompt) == "Primary"


@pytest.mark.asyncio
@pytest.mark.parametrize("key", ["tab", "shift+tab"], ids=("forward", "reverse"))
async def test_context_picker_focus_cycle_back_to_owner_closes_overlay(
    key: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    picker = _picker()

    async with _FocusCycleHost(picker).run_test() as pilot:
        picker.focus()
        focus_complete = _track_picker_focus(picker, monkeypatch)
        await pilot.press("enter")
        await _wait_for_picker_focus(focus_complete)

        assert pilot.app.focused is picker.query_one(OverlayOptionList)

        await pilot.press(key)
        await pilot.pause()

        assert pilot.app.focused is picker
        assert not picker.is_open
        assert not picker.query_one(OverlayOptionList).display


@pytest.mark.asyncio
async def test_context_picker_outside_non_focusable_click_closes_without_swallowing_click(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    picker = _picker()

    async with PickerHost(picker).run_test(size=(60, 16)) as pilot:
        await pilot.pause()
        outside = pilot.app.query_one("#outside-picker", _OutsideClickTarget)
        before = (picker.region, outside.region)

        focus_complete = _track_picker_focus(picker, monkeypatch)
        picker.open()
        await _wait_for_picker_focus(focus_complete)

        assert not outside.can_focus
        assert pilot.app.focused is picker.query_one(OverlayOptionList)

        assert await pilot.click(outside)
        await pilot.pause()

        assert outside.clicks == 1
        assert not picker.is_open
        assert not picker.query_one(OverlayOptionList).display
        assert (picker.region, outside.region) == before


@pytest.mark.asyncio
async def test_context_picker_semantic_and_disabled_states_override_active_theme_accent() -> None:
    picker = _picker()

    async with _ThemedPickerHost(picker).run_test() as pilot:
        picker.focus()
        picker.open()
        await pilot.pause()

        assert picker.styles.border_top == ("heavy", Color.parse("#6fb8ff"))

        picker.set_state(loading=True)
        await pilot.pause()

        assert picker.has_focus
        assert picker.has_class("-loading")
        assert picker.styles.border_top == ("solid", Color.parse("#2a2d33"))

        picker.set_state(disabled=True)
        await pilot.pause()

        assert picker.has_class("-disabled")
        assert picker.styles.border_top == ("solid", Color.parse("#2a2d33"))

        picker.set_state(warning=True)
        picker.focus()
        await pilot.pause()

        assert picker.styles.border_top == ("solid", Color.parse("#f0c674"))

        picker.set_state(error=True)
        await pilot.pause()

        assert picker.styles.border_top == ("solid", Color.parse("#ff6b7a"))


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

        indicator = picker.query_one(".context-picker-indicator", Static)
        options = picker.query_one(OverlayOptionList)
        assert options.region.y > indicator.region.y

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
